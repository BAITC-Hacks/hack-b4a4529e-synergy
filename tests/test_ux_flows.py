from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app.agent import remember, run_turn
from app.cart import Session, propose_add_to_cart
from app.main import _chat
from app.search import set_index
from tests.helpers import tiny_index, sample_products
from tests.test_api import client_and_session


def test_new_chat_preserves_cart_and_invalidates_old_proposal():
    set_index(tiny_index())
    client, session = client_and_session()
    product = sample_products()[0]
    pid = propose_add_to_cart(session, product, 2)["proposal"]["id"]
    client.post("/api/cart/confirm", json={"proposal_id": pid})
    stale = propose_add_to_cart(session, product, 1)["proposal"]["id"]
    run_turn(session, product["article"])
    old_chat = session.chat_id
    data = client.post("/api/chat/new").json()
    assert data["cart"]["count"] == 2
    assert data["chat_id"] != old_chat and not data["messages"] and not data["history"]
    assert not data["selection"] and not data["products"] and data["proposal"] is None
    assert client.post("/api/cart/confirm", json={"proposal_id": stale}).status_code == 409
    assert client.post("/api/chat", data={"message": product["article"], "chat_id": old_chat}).status_code == 409


def test_display_history_keeps_cards_while_model_context_is_bounded():
    set_index(tiny_index())
    session = Session("history")
    run_turn(session, sample_products()[0]["article"])
    first = session.messages[1]["products"][0]["id"]
    for index in range(20):
        remember(session, f"question {index}", "answer")
    assert len(session.history) == 20 and len(session.messages) == 42
    assert session.messages[1]["products"][0]["id"] == first
    assert session.messages[-1]["products"] == []


def test_selection_collects_products_without_adding_and_refreshes():
    set_index(tiny_index())
    client, session = client_and_session()
    items = [{"product_id": 101, "quantity": 2}, {"product_id": 103, "quantity": 1}]
    data = client.put("/api/selection", json={"items": items}).json()
    assert len(data["selection"]) == 2 and not session.cart and session.pending is None
    assert len(client.get("/api/state").json()["selection"]) == 2
    proposal = client.post("/api/cart/propose-items", json={"items": items}).json()["proposal"]
    client.put("/api/selection", json={"items": items[:1]})
    assert client.post("/api/cart/confirm", json={"proposal_id": proposal["id"]}).status_code == 409
    assert not session.cart


def test_cart_increase_requires_confirmation_decrease_is_saved():
    set_index(tiny_index())
    client, session = client_and_session()
    pid = propose_add_to_cart(session, sample_products()[0], 2)["proposal"]["id"]
    client.post("/api/cart/confirm", json={"proposal_id": pid})
    line = session.cart[0].line_id
    proposed = client.post("/api/cart/quantity", json={"line_id": line, "quantity": 3}).json()
    assert proposed["cart"]["count"] == 2 and proposed["proposal"]["items"][0]["quantity"] == 1
    client.post("/api/cart/confirm", json={"proposal_id": proposed["proposal"]["id"]})
    assert client.get("/api/state").json()["cart"]["count"] == 3
    decreased = client.post("/api/cart/quantity", json={"line_id": line, "quantity": 1}).json()
    assert decreased["cart"]["count"] == 1 and decreased["proposal"] is None
    assert client.post("/api/cart/quantity", json={"line_id": line, "quantity": 99999}).status_code == 409


def test_alternatives_available_for_in_stock_product_without_cart_change():
    products = sample_products()
    products[1]["quantity"] = 5
    set_index(tiny_index(products))
    client, session = client_and_session()
    data = client.post("/api/products/101/alternatives").json()
    assert data["products"][0]["id"] == 101
    assert any(p.get("analog_of") == 101 for p in data["products"])
    assert data["messages"][-1]["products"] and not session.cart


def test_stop_discards_late_model_proposal_and_allows_new_request(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    client, session = client_and_session()
    entered, release = Event(), Event()
    def slow(working, *_args):
        entered.set()
        assert release.wait(4)
        propose_add_to_cart(working, sample_products()[0], 1)
        remember(working, "old request", "late response")
        return {"text": "late response"}
    monkeypatch.setattr(agent, "run_turn", slow)
    with ThreadPoolExecutor() as executor:
        future = executor.submit(_chat, session, "old request", [], "", "slow", session.chat_id)
        assert entered.wait(2)
        stopped = client.post("/api/chat/stop", json={"request_id": "slow"})
        assert stopped.json()["stopped"] and not session.active_request
        client.post("/api/chat/new")
        remember(session, "new chat", "fresh response")
        release.set()
        assert future.result()[1] == 409
    assert not session.pending and not session.cart
    assert session.messages[-1]["content"] == "fresh response"


def test_stop_before_upload_is_parsed_blocks_late_start():
    client, session = client_and_session()
    client.post("/api/chat/stop", json={"request_id": "not-started"})
    assert _chat(session, "test", [], "", "not-started", session.chat_id)[1] == 409


def test_new_actions_require_session_authorization():
    client, _ = client_and_session()
    for path, body in [("/api/chat/new", {}), ("/api/chat/stop", {"request_id": "x"}),
                       ("/api/cart/quantity", {"line_id": "x", "quantity": 2}), ("/api/products/101/alternatives", {})]:
        assert client.post(path, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_model_can_request_alternatives_for_available_product():
    from app.agent import execute_tool
    products = sample_products(); products[1]["quantity"] = 5
    result = execute_tool(Session("alternatives"), "get_alternatives", {"product_id": 101}, index=tiny_index(products))
    assert result["analogs"] and result["quantity"] > 0


def test_cancel_completed_answer_does_not_report_it_stopped():
    set_index(tiny_index())
    client, session = client_and_session()
    response = client.post("/api/chat", data={"message": sample_products()[0]["article"], "request_id": "complete"})
    assert response.status_code == 200
    assert client.post("/api/chat/stop", json={"request_id": "complete"}).json()["stopped"] is False
    assert session.messages


def test_cart_quantity_revalidates_purchase_rules_and_other_sessions():
    product = sample_products()[0]; product.update(min_quantity=2, quantity_step=2)
    index = tiny_index([product]); set_index(index)
    client, session = client_and_session()
    pid = propose_add_to_cart(session, product, 4)["proposal"]["id"]
    client.post("/api/cart/confirm", json={"proposal_id": pid})
    line = session.cart[0].line_id
    assert client.post("/api/cart/quantity", json={"line_id": line, "quantity": 1}).status_code == 409
    other, _ = client_and_session()
    assert other.post("/api/cart/quantity", json={"line_id": line, "quantity": 2}).status_code == 409
    data = client.post("/api/cart/quantity", json={"line_id": line, "quantity": 6}).json()
    product["quantity_step"] = 4
    rejected = client.post("/api/cart/confirm", json={"proposal_id": data["proposal"]["id"]})
    assert rejected.status_code == 409 and session.cart[0].quantity == 4
