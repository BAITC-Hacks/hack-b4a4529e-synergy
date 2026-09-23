import pytest

from app.answers import catalog_answer, terms_answer
from app.cart import Session, confirm_pending, propose_add_to_cart, purchase_options, change_quantity
from app.catalog import normalize_product
from app.policies import get_purchase_terms
from app.search import product_hit, set_index
from tests.helpers import sample_products, tiny_index
from tests.test_api import client_and_session


@pytest.mark.parametrize("changes", [
    {"unit_known": False}, {"unit_known": None}, {"unit": ""}, {"unit": "ед."},
    {"min_quantity": None}, {"min_quantity": 0}, {"min_quantity": -1},
    {"quantity_step": None}, {"quantity_step": 0}, {"quantity_step": -1},
    {"quantity_step": "NaN"}, {"quantity_step": True},
])
def test_unconfirmed_terms_block_controls_and_server_additions(changes):
    product = {**sample_products()[0], **changes}
    session = Session("terms")
    options = purchase_options(session, product)
    assert not options["can_add"] and "подтверждения поставщика" in options["reason"]
    assert "step" not in options and "suggested_quantity" not in options
    assert not propose_add_to_cart(session, product, 2)["ok"]
    assert not session.cart and session.pending is None
    hit = product_hit(product)
    assert hit["purchase_rules_confirmed"] is False
    answer = catalog_answer("найди товар", [hit])
    assert "подтверждения поставщика" in answer and "укажите количество" not in answer.lower()


def test_metre_flag_and_ambiguous_numbers_do_not_authorize_cut_lengths():
    product = normalize_product({"id": 1, "name": "Кабель (305м)", "price": 250, "quantity": 500,
        "properties": {"METRAZHNYY_TOVAR": "Да", "DLINA_RULONA": "305 метров",
                       "KRATNOST_MIN": "1", "KRATNOST_MAKS": "305"}})
    assert product["unit_known"] and product["unit"] == "м"
    assert product["min_quantity"] is None and product["quantity_step"] is None
    for amount in (.1, 1, 305):
        assert not propose_add_to_cart(Session("cut"), product, amount)["ok"]
    assert not purchase_options(Session("cut"), product)["can_add"]


def test_confirmed_fractional_increment_is_enforced_through_confirmation():
    product = normalize_product({"id": 1, "name": "Кабель", "price": "2.50", "quantity": 10,
                                 "unit": "м", "min_quantity": "1.25", "quantity_step": "0.25"})
    session = Session("confirmed")
    assert purchase_options(session, product)["suggested_quantity"] == "1.25"
    assert not propose_add_to_cart(session, product, 1)["ok"]
    assert not propose_add_to_cart(session, product, 1.3)["ok"]
    proposal = propose_add_to_cart(session, product, 1.5)["proposal"]
    result = confirm_pending(session, lambda _: product, proposal["id"])
    assert result["ok"] and result["cart"]["total"] == "3.75"
    assert result["cart"]["items"][0]["unit"] == "м"


def test_confirmation_and_cart_edits_recheck_lost_terms():
    product = sample_products()[0]
    session = Session("changed")
    proposal = propose_add_to_cart(session, product, 2)["proposal"]
    unknown = {**product, "unit_known": False}
    assert not confirm_pending(session, lambda _: unknown, proposal["id"])["ok"]
    assert not session.cart and session.pending is None
    proposal = propose_add_to_cart(session, product, 2)["proposal"]
    confirm_pending(session, lambda _: product, proposal["id"])
    line = session.cart[0]
    for quantity in (1, 3):
        assert not change_quantity(session, line.line_id, quantity, lambda _: unknown)["ok"]
        assert session.cart[0].quantity == 2


def test_unknown_unit_is_exposed_in_cards_and_purchase_terms():
    product = normalize_product({"id": 1, "name": "Товар", "price": 10, "quantity": 5,
                                 "min_quantity": 1, "quantity_step": 1})
    hit = product_hit(product)
    assert not hit["unit_known"] and hit["unit_label"] == "единица продажи не подтверждена"
    answer = terms_answer(get_purchase_terms("minimum", product=product))
    assert "единица продажи не подтверждена" in answer
    assert "подтверждения поставщика" in answer


def test_http_selection_and_batch_cannot_bypass_purchase_gate():
    products = sample_products()
    products[0]["unit_known"] = False
    set_index(tiny_index(products))
    client, session = client_and_session()
    items = [{"product_id": 101, "quantity": 1}, {"product_id": 103, "quantity": 1}]
    response = client.put("/api/selection", json={"items": items})
    assert response.status_code == 409
    response = client.post("/api/cart/propose-items", json={"items": items})
    assert response.status_code == 409
    assert not session.cart and session.pending is None and not session.selection
