from __future__ import annotations

import threading
import time
import uuid

from .cart import Session, cart_view
from .config import MAX_SESSIONS, SESSION_TTL

_lock = threading.Lock()
_sessions: dict[str, Session] = {}


def reset() -> None:
    with _lock:
        _sessions.clear()


def get_or_create(session_id: str | None) -> Session:
    now = time.time()
    with _lock:
        for sid in [key for key, value in _sessions.items() if now - value.last_seen >= SESSION_TTL]:
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
                "proposal": session.pending.as_dict() if session.pending else None,
                "history": list(session.history), "products": list(session.last_search)}
