from fastapi.testclient import TestClient

from app.cart import propose_add_to_cart
from app.main import app
from app.search import set_index
from app.sessions import get_or_create
from tests.helpers import sample_products, tiny_index


def test_pages_ok():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/cart").status_code == 200


def test_confirm_without_proposal_fails():
    client = TestClient(app)
    set_index(tiny_index())
    response = client.post("/api/cart/confirm")
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_confirm_endpoint_adds_line():
    client = TestClient(app)
    index = tiny_index()
    set_index(index)
    state = client.get("/api/state")
    sid = state.cookies["sid"]
    session = get_or_create(sid)
    propose_add_to_cart(session, sample_products()[0], 2)
    response = client.post("/api/cart/confirm")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["cart"]["count"] == 2
    assert body["cart"]["url"] == "/cart"
    page = client.get("/cart")
    assert page.status_code == 200


def test_chat_without_index_returns_russian_error():
    client = TestClient(app)
    response = client.post("/api/chat", data={"message": "тест"})
    assert response.status_code == 503
    assert "индекс" in response.json()["error"].lower() or "ключ" in response.json()["error"].lower()
