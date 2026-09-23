from __future__ import annotations

import threading
import time
import uuid
from copy import deepcopy

from .cart import Session, cart_view, purchase_options
from .config import MAX_SESSIONS, SESSION_TTL

_lock = threading.Lock()
_sessions: dict[str, Session] = {}


def reset() -> None:
    with _lock:
        _sessions.clear()


def get_or_create(session_id: str | None) -> Session:
    now = time.time()
    with _lock:
        for sid in [key for key, value in _sessions.items() if not value.active_request and now - value.last_seen >= SESSION_TTL]:
            del _sessions[sid]
        session = _sessions.get(session_id or "")
        if session is None:
            if len(_sessions) >= MAX_SESSIONS:
                raise RuntimeError("Сервис занят. Попробуйте позже.")
            session = Session(id=uuid.uuid4().hex)
            _sessions[session.id] = session
        session.last_seen = now
        return session


def state_payload(session: Session) -> dict:
    with session.lock:
        if session.pending and time.time() >= session.pending.expires_at:
            session.pending = None
        return {"csrf_token": session.csrf_token, "cart": cart_view(session),
                "chat_id": session.chat_id, "messages": [{**deepcopy(message), "products": [
                    {**product, "purchase_options": purchase_options(session, product)} for product in message.get("products", [])
                ]} for message in session.messages],
                "selection": deepcopy(session.selection), "selection_inputs": deepcopy(session.selection_inputs),
                "request_status": dict(session.request_status),
                "document_summary": deepcopy(session.document_summary),
                "busy": bool(session.active_request), "active_request": session.active_request,
                "proposal": session.pending.as_dict() if session.pending else None,
                "history": list(session.history),
                "products": [{**product, "purchase_options": purchase_options(session, product)}
                             for product in session.last_search],
                "sources": list(session.sources), "snapshot": dict(session.snapshot),
                "attachment_review": [{**deepcopy(row), "candidates": [
                    {**product, "purchase_options": purchase_options(session, product)} for product in row["candidates"]
                ]} for row in session.attachment_review],
                "attachment_issues": list(session.attachment_issues)}


def new_chat(session: Session) -> dict:
    with session.lock:
        session.cancel_event.set()
        session.active_request = None
        session.chat_id = uuid.uuid4().hex
        session.pending = None
        for name in ("history", "messages", "selection", "selection_inputs", "last_search", "product_context", "sources", "attachment_review", "attachment_issues", "document_summary"):
            setattr(session, name, [])
        session.snapshot = {}
        session.request_status = {}
        return state_payload(session)
