from fastapi.testclient import TestClient

from app.cart import propose_add_to_cart
from app.main import app
from app.search import set_index
from app.sessions import get_or_create
from tests.helpers import sample_products, tiny_index


def client_and_session():
    client = TestClient(app)
    state = client.get("/api/state").json()
    client.headers["X-CSRF-Token"] = state["csrf_token"]
    session = get_or_create(client.cookies["sid"])
    return client, session


def test_pages_and_server_generated_session():
    client = TestClient(app)
    client.cookies.set("sid", "chosen-by-client")
    assert client.get("/").status_code == 200
    assert client.get("/cart").status_code == 200
    state = client.get("/api/state")
    assert state.status_code == 200
    assert state.cookies["sid"] != "chosen-by-client"


def test_confirmation_requires_csrf_and_owned_proposal():
    client, session = client_and_session()
    set_index(tiny_index())
    pid = propose_add_to_cart(session, sample_products()[0], 2)["proposal"]["id"]
    second, _ = client_and_session()
    assert second.post("/api/cart/confirm", json={"proposal_id": pid}).status_code == 409
    assert client.post("/api/cart/confirm", json={"proposal_id": pid}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/cart/confirm", json={"proposal_id": pid}, headers={"Origin": "https://other.test"}).status_code == 403
    result = client.post("/api/cart/confirm", json={"proposal_id": pid})
    assert result.status_code == 200
    assert result.json()["cart"]["count"] == 2
    assert result.json()["cart_url"] == "/cart"
    assert client.get("/api/state").json()["cart"]["count"] == 2
    assert client.post("/api/cart/confirm", json={"proposal_id": pid}).json()["status"] == "already_added"


def test_text_confirmation_bypasses_model(monkeypatch):
    import app.agent as agent
    client, session = client_and_session()
    set_index(tiny_index())
    pid = propose_add_to_cart(session, sample_products()[0], 2)["proposal"]["id"]
    def forbidden():
        raise AssertionError("Confirmation must not call the model")
    monkeypatch.setattr(agent, "_client", forbidden)
    result = client.post("/api/chat", data={"message": "да, добавь", "proposal_id": pid})
    assert result.status_code == 200
    assert result.json()["cart"]["count"] == 2
    assert result.json()["cart_url"] == "/cart"


def test_cancel_and_stale_buttons():
    client, session = client_and_session()
    set_index(tiny_index())
    old = propose_add_to_cart(session, sample_products()[0], 1)["proposal"]["id"]
    new = propose_add_to_cart(session, sample_products()[0], 2)["proposal"]["id"]
    assert client.post("/api/cart/cancel", json={"proposal_id": old}).status_code == 409
    assert session.pending.id == new
    assert client.post("/api/cart/cancel", json={"proposal_id": new}).status_code == 200
    assert session.pending is None and not session.cart


def test_product_card_prepares_proposal_without_adding():
    client, session = client_and_session()
    set_index(tiny_index())
    product_id = sample_products()[0]["id"]
    denied = client.post("/api/cart/propose", json={"product_id": product_id, "quantity": 1},
                         headers={"X-CSRF-Token": "wrong"})
    assert denied.status_code == 403
    prepared = client.post("/api/cart/propose", json={"product_id": product_id, "quantity": "12"})
    assert prepared.status_code == 200
    assert prepared.json()["cart"]["count"] == 0
    assert prepared.json()["proposal"]["items"][0]["quantity"] == 12
    assert not session.cart
    proposal_id = prepared.json()["proposal"]["id"]
    confirmed = client.post("/api/cart/confirm", json={"proposal_id": proposal_id})
    assert confirmed.status_code == 200
    assert confirmed.json()["cart"]["count"] == 12


def test_remove_cart_line_requires_session_token():
    client, session = client_and_session()
    set_index(tiny_index())
    pid = propose_add_to_cart(session, sample_products()[0], 2)["proposal"]["id"]
    client.post("/api/cart/confirm", json={"proposal_id": pid})
    line_id = session.cart[0].line_id

    denied = client.post("/api/cart/remove", json={"line_id": line_id},
                         headers={"X-CSRF-Token": "wrong"})
    assert denied.status_code == 403 and len(session.cart) == 1
    result = client.post("/api/cart/remove", json={"line_id": line_id})
    assert result.status_code == 200 and result.json()["cart"]["items"] == []
    assert client.get("/api/state").json()["cart"]["count"] == 0


def test_chat_missing_index_has_safe_error(monkeypatch):
    import app.agent as agent
    client, _ = client_and_session()
    def unavailable():
        from app.search import CatalogUnavailable
        raise CatalogUnavailable()
    monkeypatch.setattr(agent, "get_index", unavailable)
    result = client.post("/api/chat", data={"message": "лампа"})
    assert result.status_code == 503
    assert "Каталог временно недоступен" in result.json()["error"]


def test_local_demo_ports_do_not_share_session_cookie():
    from fastapi.testclient import TestClient
    from app.main import app

    first = TestClient(app, base_url="http://localhost:8765")
    second = TestClient(app, base_url="http://localhost:8766")
    first.get("/api/state")
    second.cookies.update(first.cookies)
    second.get("/api/state")
    assert first.cookies.get("sid_8765")
    assert second.cookies.get("sid_8766")
    assert first.cookies.get("sid_8765") != second.cookies.get("sid_8766")


def test_empty_and_unsupported_upload():
    client, _ = client_and_session()
    assert client.post("/api/chat", data={"message": ""}).status_code == 400
    result = client.post("/api/chat", files={"files": ("program.exe", b"test")})
    assert result.status_code == 400
