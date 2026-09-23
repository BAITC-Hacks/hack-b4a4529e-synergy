import json
from types import SimpleNamespace as Obj

from app.agent import TOOLS, execute_tool, run_turn
from app.cart import Session, propose_add_to_cart
from app.search import set_index
from tests.helpers import tiny_index, sample_products


def test_model_has_no_mutation_tool():
    session = Session(id="test")
    index = tiny_index()
    assert "add_to_cart" not in [t["name"] for t in TOOLS]
    result = execute_tool(session, "add_to_cart", {"product_id": 101, "quantity": 1}, index=index)
    assert "error" in result and not session.cart


def test_realistic_search_then_proposal_waits_for_user(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    responses = iter([
        Obj(output=[Obj(type="function_call", name="search_products", arguments=json.dumps({"query": "200300285_", "limit": 3}), call_id="1")]),
        Obj(output=[Obj(type="function_call", name="propose_cart", arguments=json.dumps({"items": [{"product_id": 101, "quantity": 2}]}), call_id="2")]),
    ])
    class FakeClient:
        responses = Obj(create=lambda **kwargs: next(responses))
    monkeypatch.setattr(agent, "_client", lambda: FakeClient())
    session = Session(id="test")
    result = run_turn(session, "Добавь две штуки 200300285_")
    assert result["proposal"]["items"][0]["quantity"] == 2
    assert result["cart"]["count"] == 0
    assert "Добавить" in result["text"]


def test_negative_reply_cannot_mutate_cart(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    client = Obj(responses=Obj(create=lambda **kwargs: Obj(output=[], output_text="Не добавляю.")))
    monkeypatch.setattr(agent, "_client", lambda: client)
    session = Session(id="test")
    proposal = propose_add_to_cart(session, sample_products()[0], 2)
    result = run_turn(session, "я не согласен", proposal_id=proposal["proposal"]["id"])
    assert result["cart"]["count"] == 0


def test_only_text_history_is_kept(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    captured = []
    def create(**kwargs):
        captured.append(kwargs)
        return Obj(output=[], output_text="Уточните товар.")
    monkeypatch.setattr(agent, "_client", lambda: Obj(responses=Obj(create=create)))
    session = Session(id="test")
    run_turn(session, "фото", [{"filename": "photo.jpg", "mime": "image/jpeg", "data": b"test-image"}])
    assert captured[0]["store"] is False
    assert "data:image" not in json.dumps(session.history)
    run_turn(session, "первый товар")
    assert any(item.get("content") == "Уточните товар." for item in captured[1]["input"] if isinstance(item, dict))


def test_search_answer_uses_visible_catalog_cards(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    responses = iter([
        Obj(output=[Obj(type="function_call", name="search_products",
                        arguments=json.dumps({"query": "200300285_", "limit": 3}), call_id="1")]),
        Obj(output=[], output_text="Другой товар стоит 5 525 ₸ и есть в наличии."),
    ])
    monkeypatch.setattr(agent, "_client", lambda: Obj(responses=Obj(create=lambda **kwargs: next(responses))))
    result = run_turn(Session(id="test"), "200300285_")
    assert result["products"][0]["article"] == "200300285_"
    assert "5 525" not in result["text"]
    assert "карточках" in result["text"]


def test_multiple_detail_calls_keep_both_cards(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    responses = iter([
        Obj(output=[Obj(type="function_call", name="get_product", arguments='{"product_id": 101}', call_id="1"),
                    Obj(type="function_call", name="get_product", arguments='{"product_id": 103}', call_id="2")]),
        Obj(output=[], output_text="Проверьте оба варианта."),
    ])
    monkeypatch.setattr(agent, "_client", lambda: Obj(responses=Obj(create=lambda **kwargs: next(responses))))
    result = run_turn(Session(id="test"), "Покажи оба товара")
    assert {product["id"] for product in result["products"]} == {101, 103}


def test_empty_search_replaces_previous_cards(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    responses = iter([
        Obj(output=[Obj(type="function_call", name="search_products",
                        arguments='{"query": "unknown", "limit": 3}', call_id="1")]),
        Obj(output=[], output_text="Проверьте запрос."),
    ])
    monkeypatch.setattr(agent, "_client", lambda: Obj(responses=Obj(create=lambda **kwargs: next(responses))))
    monkeypatch.setattr(agent, "search_products", lambda *args, **kwargs: {"results": [], "needs_clarification": True})
    session = Session(id="test", last_search=[{"id": 101, "name": "old"}])
    result = run_turn(session, "Найди неизвестный товар")
    assert result["products"] == []
    assert "ничего не найдено" in result["text"]
