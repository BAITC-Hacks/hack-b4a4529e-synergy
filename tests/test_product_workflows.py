import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace as Obj

import pytest

from app.agent import run_turn
from app.cart import Session, propose_cart, confirm_pending
from app.main import _chat
from app.search import set_index
from app.specifications import review_items
from app.tabular import extract_rows, process_tables
from tests.helpers import tiny_index, sample_products
from tests.test_api import client_and_session


def document_rows(session, index, quantities=(2, 3), unit="шт."):
    return review_items(session, [{"filename": "spec.csv", "source_reference": f"row {i}",
        "query": "200300285_", "quantity": qty, "source_unit": unit} for i, qty in enumerate(quantities)],
        index, {"spec.csv"})["items"]


def contributions(rows):
    return [{"product_id": 101, "quantity": row["quantity"], "source_id": row["row_id"]} for row in rows]


def test_duplicate_product_rows_sum_and_retry_does_not_double():
    index = tiny_index(); session = Session("rows")
    rows = document_rows(session, index)
    first = propose_cart(session, contributions(rows), index.get)
    second = propose_cart(session, contributions(rows), index.get)
    assert first["proposal"]["items"][0]["quantity"] == second["proposal"]["items"][0]["quantity"] == 5
    assert len(session.selection[0]["sources"]) == 2
    confirmed = confirm_pending(session, index.get, second["proposal"]["id"])
    assert confirmed["cart"]["count"] == 5
    assert all(row["completion"] == "added" for row in session.attachment_review)
    assert not propose_cart(session, contributions(rows), index.get)["ok"]
    assert confirm_pending(session, index.get, second["proposal"]["id"])["status"] == "already_added"
    assert session.cart[0].quantity == 5


def test_same_source_twice_is_rejected_and_manual_quantity_is_preserved():
    index = tiny_index(); session = Session("rows")
    rows = document_rows(session, index, (2,))
    assert not propose_cart(session, contributions(rows) * 2, index.get)["ok"]
    result = propose_cart(session, [{"product_id": 101, "quantity": 7}, *contributions(rows)], index.get)
    assert result["proposal"]["items"][0]["quantity"] == 9


@pytest.mark.parametrize("unit", ["м", ""])
def test_document_units_must_be_confirmed_and_match(unit):
    index = tiny_index(); session = Session("units")
    rows = document_rows(session, index, (2,), unit)
    result = propose_cart(session, contributions(rows), index.get)
    assert not result["ok"] and result["item_errors"][0]["source_id"] == rows[0]["row_id"]
    assert not session.cart and session.pending is None


def test_correction_keeps_row_identity_and_reopen_is_explicit():
    index = tiny_index(); set_index(index)
    client, session = client_and_session()
    row = document_rows(session, index, (2,), "м")[0]
    response = client.post(f"/api/review/{row['row_id']}", json={"action": "unit", "source_unit": "шт"})
    assert response.status_code == 200
    corrected = response.json()["attachment_review"][0]
    assert corrected["row_id"] == row["row_id"] and corrected["completion"] == "ready"
    proposal = client.post("/api/cart/propose-items", json={"items": contributions([corrected])}).json()["proposal"]
    client.post("/api/cart/confirm", json={"proposal_id": proposal["id"]})
    assert client.post(f"/api/review/{row['row_id']}", json={"action": "search", "query": "kab001"}).status_code == 409
    assert client.post(f"/api/review/{row['row_id']}", json={"action": "reopen"}).status_code == 200
    assert session.attachment_review[0]["completion"] == "unresolved"


def test_unresolved_article_can_be_repaired_without_reupload():
    index = tiny_index(); set_index(index)
    client, session = client_and_session()
    row = review_items(session, [{"filename":"x.csv", "source_reference":"row 2", "query":"999999999_",
        "quantity":2, "source_unit":"шт"}], index, {"x.csv"})["items"][0]
    result = client.post(f"/api/review/{row['row_id']}", json={"action":"search", "query":"200300285_"}).json()
    assert result["attachment_review"][0]["candidate_product_ids"] == [101]
    assert result["attachment_review"][0]["row_id"] == row["row_id"]


def test_exclusion_needs_a_reason_and_actions_are_owned():
    index = tiny_index(); set_index(index)
    client, session = client_and_session(); other, _ = client_and_session()
    row = document_rows(session, index, (2,))[0]
    path = f"/api/review/{row['row_id']}"
    assert other.post(path, json={"action":"reopen"}).status_code == 404
    assert client.post(path, json={"action":"exclude"}).status_code == 422
    assert client.post(path, json={"action":"exclude","reason":"Не требуется"}).status_code == 200
    assert not propose_cart(session, contributions([row]), index.get)["ok"]


def test_large_csv_is_reconciled_without_model_calls_and_new_file_appends(monkeypatch):
    import app.agent as agent
    monkeypatch.setattr(agent, "_client", lambda: pytest.fail("structured tables must not call model"))
    index = tiny_index(); set_index(index); session = Session("tables")
    data = ("article,quantity,unit\n" + "200300285_,1,шт\n" * 600).encode()
    run_turn(session, "Разбери", [{"filename":"a.csv","mime":"text/csv","data":data}])
    assert len(session.attachment_review) == 600
    assert session.document_summary[0]["source_rows"] == session.document_summary[0]["reviewed_rows"] == 600
    run_turn(session, "Ещё файл", [{"filename":"b.csv","mime":"text/csv","data":b"article,quantity,unit\nkab001,2,m\n"}])
    assert len(session.attachment_review) == 601
    run_turn(session, "Повтор", [{"filename":"a.csv","mime":"text/csv","data":data}])
    assert len(session.attachment_review) == 601


def test_unrecognized_columns_and_bad_quantity_remain_reviewable():
    file = {"filename":"a.csv", "data": "article,quantity,unit\n200300285_,wrong,шт\nunknown,,м\n".encode()}
    rows = extract_rows(file)
    assert len(rows) == 2 and rows[0]["quantity"] is None and "wrong" in rows[0]["source_text"]
    unknown = extract_rows({"filename":"b.csv","data":b"unrecognized,header\nhello,world\n"})
    assert len(unknown) == 2 and all(row["query"] == "" for row in unknown)


def test_refreshed_page_can_observe_running_then_complete_request(monkeypatch):
    import app.agent as agent
    index = tiny_index(); set_index(index); client, session = client_and_session()
    entered, release = Event(), Event()
    def slow(working, *args):
        entered.set(); assert release.wait(4)
        agent.remember(working, "test", "Готово")
        return {"text":"Готово"}
    monkeypatch.setattr(agent,"run_turn",slow)
    with ThreadPoolExecutor() as executor:
        result = executor.submit(_chat,session,"test",[],"","req",session.chat_id)
        assert entered.wait(2)
        state = client.get("/api/state").json()
        assert state["busy"] and state["active_request"] == "req" and state["request_status"]["stage"] == "searching"
        release.set(); assert result.result()[1] == 200
    state = client.get("/api/state").json()
    assert not state["busy"] and state["request_status"]["stage"] == "complete"


def test_late_failure_does_not_replace_new_chat_status(monkeypatch):
    import app.agent as agent
    set_index(tiny_index()); client, session = client_and_session()
    entered, release = Event(), Event()
    def slow(*args):
        entered.set(); assert release.wait(4); raise RuntimeError("cancelled")
    monkeypatch.setattr(agent,"run_turn",slow)
    with ThreadPoolExecutor() as executor:
        result = executor.submit(_chat,session,"test",[],"","old",session.chat_id)
        assert entered.wait(2)
        client.post("/api/chat/new")
        release.set(); result.result()
    assert session.request_status == {}


def test_clarification_and_compound_answer_are_preserved(monkeypatch):
    import app.agent as agent
    set_index(tiny_index())
    calls = [Obj(type="function_call",name="get_product",arguments='{"product_id":101}',call_id="a"),
             Obj(type="function_call",name="get_purchase_terms",arguments=json.dumps({"topic":"delivery","city":"Алматы","buyer_type":None,"product_id":None}),call_id="b")]
    responses = iter([Obj(output=calls), Obj(output=[],output_text="Уточните, сколько полюсов требуется?")])
    monkeypatch.setattr(agent,"_client",lambda:Obj(responses=Obj(create=lambda **kw:next(responses))))
    result = run_turn(Session("compound"),"Расскажи о 200300285_ и доставке в Алматы")
    assert "сколько полюсов" in result["text"] and "Источник условий" in result["text"] and result["products"]


def test_catalog_pagination_and_comparison_use_current_facts(monkeypatch):
    import app.discovery as discovery
    products = [{**sample_products()[0],"id":i,"article":str(100000+i),"price":100-i} for i in range(1,24)]
    index = tiny_index(products); set_index(index)
    monkeypatch.setattr(discovery,"_embed_query",lambda *args:index.embeddings[0]*0+0.5)
    monkeypatch.setattr(discovery,"MIN_SCORE",0.4)
    client, _ = client_and_session()
    first = client.get("/api/search",params={"q":"товар","sort":"price"}).json()
    second = client.get("/api/search",params={"q":"товар","sort":"price","offset":10}).json()
    assert first["total"] == 23 and first["has_more"]
    assert not set(p["id"] for p in first["results"]) & set(p["id"] for p in second["results"])
    assert [p["price"] for p in first["results"]] == sorted(p["price"] for p in first["results"])
    compared = client.get("/api/compare?ids=1,2").json()
    assert len(compared["products"]) == 2 and compared["attributes"]


def test_reupload_preserves_exclusions_and_order_and_zero_source_text():
    index = tiny_index(); session = Session("repeat")
    file = {"filename":"a.csv","data":"article,quantity,unit\n200300285_,2,шт\nkab001,0,м\n".encode()}
    process_tables(session, [file], index)
    original_ids = [row["row_id"] for row in session.attachment_review]
    session.attachment_review[0].update(completion="excluded", exclude_reason="Не требуется")
    process_tables(session, [file], index)
    assert [row["row_id"] for row in session.attachment_review] == original_ids
    assert session.attachment_review[0]["completion"] == "excluded"
    assert session.attachment_review[0]["exclude_reason"] == "Не требуется"
    assert "0" in session.attachment_review[1]["source_text"]


def test_review_purchase_options_refresh_after_cart_confirmation():
    index = tiny_index(); set_index(index); client, session = client_and_session()
    rows = document_rows(session, index, (2, 3))
    proposal = propose_cart(session, contributions(rows[:1]), index.get)["proposal"]
    confirm_pending(session, index.get, proposal["id"])
    remaining = client.get("/api/state").json()["attachment_review"][1]["candidates"][0]["purchase_options"]["remaining"]
    assert remaining == "21"


def test_xlsx_multi_sheet_reconciliation_preserves_source_units():
    import io
    import openpyxl
    book = openpyxl.Workbook()
    for sheet in [book.active, book.create_sheet("Другой лист")]:
        sheet.append(["Артикул", "Количество", "Ед. изм."])
        sheet.append(["200300285_", 2, "шт"])
    output = io.BytesIO(); book.save(output); book.close()
    session = Session("xlsx")
    process_tables(session, [{"filename":"rows.xlsx","data":output.getvalue()}], tiny_index())
    assert len(session.attachment_review) == 2
    assert len({row["row_id"] for row in session.attachment_review}) == 2
    assert all(row["source_unit"] == "шт" for row in session.attachment_review)
    assert session.document_summary[0]["reviewed_rows"] == 2


def test_successive_fifty_row_batches_only_add_remaining_contributions():
    products=sample_products(); products[0]["quantity"]=1000
    index=tiny_index(products); session=Session("batches")
    rows=document_rows(session,index,(1,)*55)
    first=propose_cart(session,contributions(rows[:50]),index.get)["proposal"]
    confirm_pending(session,index.get,first["id"])
    unfinished=[row for row in session.attachment_review if row["completion"]!="added"]
    assert len(unfinished)==5
    second=propose_cart(session,contributions(unfinished),index.get)["proposal"]
    confirm_pending(session,index.get,second["id"])
    assert session.cart[0].quantity==55
    assert all(row["completion"]=="added" for row in session.attachment_review)
