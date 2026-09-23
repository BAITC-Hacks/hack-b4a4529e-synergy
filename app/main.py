from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .cart import cancel_pending, confirm_pending
from .config import MAX_UPLOAD_BYTES, STATIC_DIR, openai_api_key
from .search import get_index
from .sessions import get_or_create, state_payload

app = FastAPI(title="EKT consultant")


def _session(request: Request):
    return get_or_create(request.cookies.get("sid"))


def _with_cookie(payload: dict, session, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    response.set_cookie("sid", session.id, httponly=True, samesite="lax")
    return response


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/cart")
def cart_page():
    return FileResponse(STATIC_DIR / "cart.html")


@app.get("/api/state")
def api_state(request: Request):
    session = _session(request)
    return _with_cookie(state_payload(session), session)


@app.post("/api/chat")
async def api_chat(request: Request):
    if not openai_api_key():
        return JSONResponse({"error": "Нет ключа OpenAI в .env"}, status_code=503)
    session = _session(request)
    form = await request.form()
    message = str(form.get("message") or "")
    attachments = []
    uploads = form.getlist("files")
    for upload in uploads:
        if not hasattr(upload, "read"):
            continue
        data = await upload.read()
        if not data:
            continue
        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"error": f"файл {upload.filename} больше 10 МБ"},
                status_code=400,
            )
        attachments.append(
            {
                "filename": upload.filename or "file",
                "mime": upload.content_type,
                "data": data,
            }
        )
    if not (message or "").strip() and not attachments:
        return JSONResponse({"error": "Напишите вопрос или прикрепите файл"}, status_code=400)
    try:
        get_index()
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    from .agent import run_turn

    payload = run_turn(session, message, attachments)
    return _with_cookie(payload, session)


@app.post("/api/cart/confirm")
def api_confirm(request: Request):
    session = _session(request)
    try:
        index = get_index()
    except FileNotFoundError:
        index = None

    def lookup(product_id: int):
        if index is None:
            return None
        return index.get(product_id)

    result = confirm_pending(session, lookup)
    payload = state_payload(session)
    payload.update(result)
    status = 200 if result.get("ok") else 400
    return _with_cookie(payload, session, status_code=status)


@app.post("/api/cart/cancel")
def api_cancel(request: Request):
    session = _session(request)
    cancel_pending(session)
    return _with_cookie(state_payload(session), session)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
