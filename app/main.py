from __future__ import annotations

import secrets
import logging
import time
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APIError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .cart import cancel_pending, confirm_pending, propose_cart, remove_cart_line
from .config import (MAX_FILES, MAX_MESSAGE_CHARS, MAX_TOTAL_UPLOAD_BYTES, MAX_UPLOAD_BYTES,
                     SECURE_COOKIES, SESSION_TTL, STATIC_DIR)
from .search import CatalogUnavailable, get_index
from .attachments import AttachmentError, validate_attachment
from .sessions import get_or_create, state_payload

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


def _chat(session, message, attachments, proposal_id):
    if not session.lock.acquire(blocking=False):
        return {"error": "Дождитесь предыдущего ответа."}, 409
    try:
        from .agent import run_turn
        attachments = [validate_attachment(f["filename"], f["mime"], f["data"]) for f in attachments]
        return run_turn(session, message, attachments, proposal_id), 200
    except AttachmentError as exc:
        return {"error": str(exc)}, 422
    except (CatalogUnavailable, FileNotFoundError):
        logger.warning("catalog unavailable during chat")
        return {"error": "Каталог временно недоступен. Попробуйте позже."}, 503
    except (APIError, RuntimeError, ValueError) as exc:
        # Do not expose upstream request contents, credentials or stack traces.
        logger.warning("chat failure type=%s", type(exc).__name__)
        return {"error": "Не удалось получить ответ. Попробуйте ещё раз."}, 502
    finally:
        session.lock.release()


@app.post("/api/chat")
async def api_chat(request: Request):
    session = _session(request)
    if not _authorized(request, session):
        return _response(request, {"error": "Обновите страницу: сессия не подтверждена."}, session, 403)
    attachments = []
    async with request.form(max_files=MAX_FILES, max_fields=10, max_part_size=MAX_UPLOAD_BYTES) as form:
        message = str(form.get("message") or "").strip()
        proposal_id = str(form.get("proposal_id") or "")[:100]
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
    payload, status = await run_in_threadpool(_chat, session, message, attachments, proposal_id)
    return _response(request, payload, session, status)


class ProposalAction(BaseModel):
    proposal_id: str = Field(min_length=1, max_length=100)


class ProductAction(BaseModel):
    product_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0)


class RemoveAction(BaseModel):
    line_id: str = Field(min_length=1, max_length=100)


class SelectionAction(BaseModel):
    items: list[ProductAction] = Field(min_length=1, max_length=50)


def _propose_product(session, product_id, quantity):
    return _propose_items(session, [{"product_id": product_id, "quantity": quantity}])


def _propose_items(session, items):
    if not session.lock.acquire(blocking=False):
        return {"error": "Дождитесь предыдущего ответа."}, 409
    try:
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
