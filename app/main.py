from __future__ import annotations

import secrets
import logging
import time
import threading
from copy import deepcopy
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APIError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .cart import Session, _line, cancel_pending, confirm_pending, propose_cart, remove_cart_line, change_quantity, selection_draft
from .config import (MAX_FILES, MAX_MESSAGE_CHARS, MAX_TOTAL_UPLOAD_BYTES, MAX_UPLOAD_BYTES,
                     SECURE_COOKIES, SESSION_TTL, STATIC_DIR)
from .search import CatalogUnavailable, get_index, product_hit, _analogs_for
from .attachments import AttachmentError, validate_attachment
from .sessions import get_or_create, state_payload, new_chat
from .specifications import review_items
from .discovery import browse, comparison

app = FastAPI(title="EKT consultant")
logger = logging.getLogger("ekt.requests")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())


@app.middleware("http")
async def request_timing(request: Request, call_next):
    started = time.perf_counter()
    request_id = secrets.token_hex(8)
    response = await call_next(request)
    route = request.scope.get("route")
    logger.info("request id=%s method=%s route=%s status=%s seconds=%.3f", request_id,
                request.method, getattr(route, "path", "unmatched"), response.status_code,
                time.perf_counter() - started)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/api/ready")
def readiness():
    try:
        index = get_index()
        return {"ready": True, "catalog_version": index.metadata["version"], "product_count": len(index.products)}
    except CatalogUnavailable:
        return JSONResponse({"ready": False, "catalog_version": None, "product_count": 0}, status_code=503)


def _session(request):
    return get_or_create(request.cookies.get(_cookie_name(request)))


def _cookie_name(request):
    # Browser cookies are shared across localhost ports. Keep demo instances isolated.
    if request.url.hostname in {"localhost", "127.0.0.1", "::1"} and request.url.port:
        return f"sid_{request.url.port}"
    return "sid"


def _response(request, payload, session, status=200):
    response = JSONResponse(payload, status_code=status)
    response.set_cookie(_cookie_name(request), session.id, httponly=True, samesite="lax",
                        secure=SECURE_COOKIES, max_age=SESSION_TTL)
    response.headers["Cache-Control"] = "no-store"
    return response


def _authorized(request, session):
    if request.headers.get("x-chat-id") and request.headers["x-chat-id"] != session.chat_id:
        return False
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        return False
    if request.headers.get("sec-fetch-site") == "cross-site":
        return False
    token = request.headers.get("x-csrf-token", "")
    return bool(token and secrets.compare_digest(token, session.csrf_token))


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/cart")
def cart_page():
    return FileResponse(STATIC_DIR / "cart.html")


@app.get("/api/state")
def api_state(request: Request):
    session = _session(request)
    return _response(request, state_payload(session), session)


def _chat(session, message, attachments, proposal_id, request_id=None, chat_id=None):
    if not session.lock.acquire(blocking=False):
        return {"error": "Дождитесь предыдущего ответа."}, 409
    token = request_id or secrets.token_hex(16)
    try:
        if session.active_request or token in session.cancelled_requests or (chat_id and chat_id != session.chat_id):
            return {"error": "Чат изменился или ответ уже готовится. Обновите страницу."}, 409
        session.active_request = token
        session.request_status = {"id": token, "stage": "validating", "message": message[:8000]}
        session.cancel_event = threading.Event()
        working = Session(session.id)
        for field in fields(Session):
            if field.name not in {"lock", "cancel_event"}:
                setattr(working, field.name, deepcopy(getattr(session, field.name)))
        working.active_request = None
        working.cancel_event = session.cancel_event
        revision = session.revision
    finally:
        session.lock.release()
    try:
        from .agent import run_turn
        attachments = [validate_attachment(f["filename"], f["mime"], f["data"]) for f in attachments]
        with session.lock:
            session.request_status["stage"] = "reading" if attachments else "searching"
        result = run_turn(working, message, attachments, proposal_id)
        with session.lock:
            if session.active_request != token or session.revision != revision or session.chat_id != working.chat_id:
                return {"error": "Ответ остановлен или выбор изменился."}, 409
            for field in fields(Session):
                if field.name not in {"lock", "cancel_event", "active_request", "cancelled_requests", "finished_requests", "last_seen", "request_status"}:
                    setattr(session, field.name, getattr(working, field.name))
            session.active_request = None
            session.request_status = {"id": token, "stage": "complete"}
            return {**result, **state_payload(session)}, 200
    except AttachmentError as exc:
        session.request_status = {"id": token, "stage": "failed", "error": str(exc), "message": message}
        return {"error": str(exc)}, 422
    except (CatalogUnavailable, FileNotFoundError):
        session.request_status = {"id": token, "stage": "failed", "error": "Каталог временно недоступен.", "message": message}
        logger.warning("catalog unavailable during chat")
        return {"error": "Каталог временно недоступен. Попробуйте позже."}, 503
    except (APIError, RuntimeError, ValueError) as exc:
        session.request_status = {"id": token, "stage": "failed", "error": "Не удалось получить ответ. Повторите запрос.", "message": message}
        # Do not expose upstream request contents, credentials or stack traces.
        logger.warning("chat failure type=%s", type(exc).__name__)
        return {"error": "Не удалось получить ответ. Попробуйте ещё раз."}, 502
    finally:
        with session.lock:
            session.finished_requests = (session.finished_requests + [token])[-100:]
            if session.active_request == token:
                session.active_request = None


@app.post("/api/chat")
async def api_chat(request: Request):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу: сессия не подтверждена."}, session, 403)
    attachments = []
    async with request.form(max_files=MAX_FILES, max_fields=10, max_part_size=MAX_UPLOAD_BYTES) as form:
        message = str(form.get("message") or "").strip()
        proposal_id = str(form.get("proposal_id") or "")[:100]
        request_id = str(form.get("request_id") or "")[:100] or None
        chat_id = str(form.get("chat_id") or "")[:100] or None
        if len(message) > MAX_MESSAGE_CHARS:
            return _response(request, {"error": "Сообщение слишком длинное."}, session, 400)
        total = 0
        for upload in form.getlist("files"):
            if not hasattr(upload, "read"):
                return _response(request, {"error": "Некорректное вложение."}, session, 400)
            name = Path((upload.filename or "file").replace("\\", "/")).name
            if len(name) > 180 or any(f["filename"] == name for f in attachments):
                return _response(request, {"error": "Используйте разные имена файлов длиной до 180 символов."}, session, 400)
            if Path(name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}:
                return _response(request, {"error": "Этот формат файла не поддерживается."}, session, 400)
            data = await upload.read(MAX_UPLOAD_BYTES + 1)
            total += len(data)
            if not data or len(data) > MAX_UPLOAD_BYTES or total > MAX_TOTAL_UPLOAD_BYTES:
                return _response(request, {"error": "Файл пустой или превышен лимит: 10 МБ на файл, 20 МБ суммарно."}, session, 400)
            attachments.append({"filename": name, "mime": upload.content_type, "data": data})
    if not message and not attachments:
        return _response(request, {"error": "Напишите сообщение."}, session, 400)
    payload, status = await run_in_threadpool(_chat, session, message, attachments, proposal_id, request_id, chat_id)
    return _response(request, payload, session, status)


class ProposalAction(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=100)


class ProductAction(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    source_id: str | None = Field(default=None, max_length=100)
    source_unit: str = Field(default="", max_length=40)


class RemoveAction(BaseModel):
    line_id: str = Field(min_length=1, max_length=100)


class QuantityAction(RemoveAction):
    quantity: Decimal = Field(gt=0)


class StopAction(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)


@app.post("/api/chat/new")
def api_new_chat(request: Request):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу."}, session, 403)
    return _response(request, new_chat(session), session)


@app.post("/api/chat/stop")
def api_stop_chat(request: Request, action: StopAction):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу."}, session, 403)
    with session.lock:
        stopped = session.active_request == action.request_id
        if not stopped and not session.active_request and action.request_id not in session.finished_requests:
            # Also reject this request if it is still parsing its upload.
            stopped = True
        session.cancelled_requests = (session.cancelled_requests + [action.request_id])[-100:]
        if session.active_request == action.request_id:
            session.cancel_event.set()
            session.active_request = None
            session.request_status = {"id": action.request_id, "stage": "stopped"}
        return _response(request, {**state_payload(session), "stopped": stopped}, session)


class DraftSelection(BaseModel):
    items: list[ProductAction] = Field(max_length=50)


@app.put("/api/selection")
def api_selection(request: Request, action: DraftSelection):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу."}, session, 403)
    with session.lock:
        if session.active_request:
            return _response(request, {"error": "Сначала остановите ответ."}, session, 409)
        try:
            index = get_index()
            inputs, lines, errors = selection_draft(session, [item.model_dump() for item in action.items], index.get)
            session.selection_inputs = inputs
            if not errors:
                session.selection = lines
            session.pending = None
            return _response(request, {**state_payload(session), "item_errors": errors}, session, 409 if errors else 200)
        except ValueError as exc:
            return _response(request, {"error": str(exc)}, session, 409)
        except CatalogUnavailable:
            return _response(request, {"error": "Каталог временно недоступен."}, session, 503)


@app.post("/api/products/{product_id}/alternatives")
def api_alternatives(request: Request, product_id: int):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу."}, session, 403)
    with session.lock:
        if session.active_request:
            return _response(request, {"error": "Сначала остановите ответ."}, session, 409)
        try:
            index = get_index()
            source = index.get(product_id)
            if not source:
                return _response(request, {"error": "Товар не найден."}, session, 404)
            alternatives = _analogs_for(index, source, 3)
            products = [product_hit(source), *alternatives]
            text = ("Нашёл варианты для сравнения. Проверьте совпадения и отличия перед заменой." if alternatives
                    else "Подходящих аналогов по известным характеристикам не найдено. Уточните, какие параметры можно изменить.")
            from .agent import remember
            session.last_search = products
            session.snapshot = index.metadata
            session.product_context = list({p["id"]: p for p in [*session.product_context, *products]}.values())[-20:]
            remember(session, "Показать аналоги: " + source["name"], text, products=products)
            return _response(request, {**state_payload(session), "text": text}, session)
        except CatalogUnavailable:
            return _response(request, {"error": "Каталог временно недоступен."}, session, 503)


@app.post("/api/cart/quantity")
def api_quantity(request: Request, action: QuantityAction):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу."}, session, 403)
    with session.lock:
        if session.active_request:
            return _response(request, {"error": "Сначала остановите ответ в чате."}, session, 409)
        try:
            index = get_index()
            result = change_quantity(session, action.line_id, action.quantity, index.get, index.metadata["version"])
            return _response(request, {**state_payload(session), **result}, session, 200 if result.get("ok") else 409)
        except CatalogUnavailable:
            return _response(request, {"error": "Каталог временно недоступен."}, session, 503)


class SelectionAction(BaseModel):
    items: list[ProductAction] = Field(min_length=1, max_length=50)


def _propose_product(session, product_id, quantity):
    return _propose_items(session, [{"product_id": product_id, "quantity": quantity}])


def _propose_items(session, items):
    if not session.lock.acquire(blocking=False):
        return {"error": "Дождитесь предыдущего ответа."}, 409
    try:
        if session.active_request:
            return {"error": "Сначала остановите ответ."}, 409
        index = get_index()
        result = propose_cart(session, items, index.get, index.metadata["version"])
        return {**state_payload(session), **result}, 200 if result.get("ok") else 409
    except (CatalogUnavailable, FileNotFoundError):
        logger.exception("catalog unavailable during proposal")
        return {"error": "Каталог временно недоступен. Попробуйте позже."}, 503
    finally:
        session.lock.release()


@app.post("/api/cart/propose")
async def api_propose(request: Request, action: ProductAction):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу: сессия не подтверждена."}, session, 403)
    payload, status = await run_in_threadpool(_propose_product, session, action.product_id, action.quantity)
    return _response(request, payload, session, status)


@app.post("/api/cart/propose-items")
async def api_propose_items(request: Request, action: SelectionAction):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу: сессия не подтверждена."}, session, 403)
    payload, status = await run_in_threadpool(_propose_items, session, [item.model_dump() for item in action.items])
    return _response(request, payload, session, status)


def _change_cart(session, proposal_id, confirm):
    if not session.lock.acquire(blocking=False):
        return {"error": "Дождитесь предыдущего ответа."}, 409
    try:
        if session.active_request:
            return {"error": "Сначала остановите ответ."}, 409
        if confirm:
            index = get_index()
            result = confirm_pending(session, index.get, proposal_id, index.metadata["version"])
        else:
            result = cancel_pending(session, proposal_id)
        from .agent import remember
        if result.get("ok") and result.get("status") != "already_added":
            remember(session, "Подтвердить добавление" if confirm else "Не добавлять",
                     "Добавлено в корзину." if confirm else "Не добавляю.")
        return {**state_payload(session), **result}, 200 if result.get("ok") else 409
    except (CatalogUnavailable, FileNotFoundError):
        logger.exception("catalog unavailable during confirmation")
        return {"error": "Индекс каталога недоступен."}, 503
    finally:
        session.lock.release()


async def _action(request, action, confirm):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу: сессия не подтверждена."}, session, 403)
    payload, status = await run_in_threadpool(_change_cart, session, action.proposal_id, confirm)
    return _response(request, payload, session, status)


@app.post("/api/cart/confirm")
async def api_confirm(request: Request, action: ProposalAction):
    return await _action(request, action, True)


@app.post("/api/cart/cancel")
async def api_cancel(request: Request, action: ProposalAction):
    return await _action(request, action, False)


@app.post("/api/cart/remove")
def api_remove(request: Request, action: RemoveAction):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу: сессия не подтверждена."}, session, 403)
    result = remove_cart_line(session, action.line_id)
    return _response(request, result, session, 200 if result["ok"] else 409)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ReviewAction(BaseModel):
    action: str = Field(pattern="^(search|alternatives|exclude|reopen|unit)$")
    query: str = Field(default="", max_length=500)
    source_unit: str = Field(default="", max_length=40)
    reason: str = Field(default="", max_length=500)


@app.post("/api/review/{row_id}")
def api_review(request: Request, row_id: str, action: ReviewAction):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу."}, session, 403)
    with session.lock:
        if session.active_request:
            return _response(request, {"error": "Дождитесь завершения разбора или остановите его."}, session, 409)
        row = next((r for r in session.attachment_review if r.get("row_id") == row_id), None)
        if not row:
            return _response(request, {"error": "Строка не найдена."}, session, 404)
        if row.get("completion") == "added" and action.action != "reopen":
            return _response(request, {"error": "Строка уже добавлена. Сначала выберите «Добавить повторно»."}, session, 409)
        try:
            index = get_index()
            if action.action == "exclude":
                if not action.reason.strip():
                    return _response(request, {"error": "Укажите причину исключения."}, session, 422)
                row.update(completion="excluded", exclude_reason=action.reason.strip())
            elif action.action == "reopen":
                row.update(completion="unresolved")
                row.pop("added", None)
                row.pop("exclude_reason", None)
            elif action.action == "alternatives":
                source = index.get(row["candidate_product_ids"][0]) if row["candidate_product_ids"] else None
                if not source:
                    return _response(request, {"error": "Сначала найдите исходный товар по артикулу."}, session, 409)
                candidates = _analogs_for(index, source, 5)
                row.update(candidates=candidates, candidate_product_ids=[p["id"] for p in candidates], status="ambiguous", completion="unresolved")
            else:
                updated = {**row, "query": action.query.strip() or row["query"],
                           "source_unit": action.source_unit if action.action == "unit" else row.get("source_unit", "")}
                review_items(session, [updated], index, {row["filename"]})
            session.selection_inputs = [x for x in session.selection_inputs if x.get("source_id") != row_id]
            _, session.selection, _ = selection_draft(session, session.selection_inputs, index.get)
            session.pending = None
            session.revision += 1
            return _response(request, state_payload(session), session)
        except (ValueError, CatalogUnavailable) as exc:
            return _response(request, {"error": str(exc)}, session, 409)


@app.get("/api/search")
def api_search(request: Request, q: str = Query(min_length=1, max_length=500),
               offset: int = Query(default=0, ge=0, le=20000), sort: str = Query(default="relevance", pattern="^(relevance|price)$"),
               in_stock: bool = False):
    session = _session(request)
    try:
        from .cart import purchase_options
        result = browse(get_index(), q, offset=offset, sort=sort, in_stock=in_stock)
        for hit in result["results"]:
            hit["purchase_options"] = purchase_options(session, hit)
        return _response(request, result, session)
    except (ValueError, CatalogUnavailable, APIError):
        return _response(request, {"error": "Поиск временно недоступен. Повторите попытку."}, session, 503)


@app.get("/api/compare")
def api_compare(request: Request, ids: str = Query(max_length=100)):
    session = _session(request)
    try:
        products = list(dict.fromkeys(int(value) for value in ids.split(",")))
        if not 2 <= len(products) <= 3:
            raise ValueError("Выберите два или три товара.")
        return _response(request, comparison(get_index(), products), session)
    except (ValueError, CatalogUnavailable) as exc:
        return _response(request, {"error": str(exc)}, session, 400)
