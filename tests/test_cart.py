from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal

import pytest

from app.cart import Session, cart_view, confirm_pending, propose_cart, propose_add_to_cart, remove_cart_line, user_confirms_add
from tests.helpers import sample_products


def product(**changes):
    return {**sample_products()[0], **changes}


@pytest.mark.parametrize("message", [
    "да", "ок", "я не согласен", "не добавь в корзину", "не добавлять",
    "если я скажу да, добавь", "да, добавь, но сначала поменяй количество",
    "«да, добавь»", "что значит добавить в корзину?", "есть в наличии?",
])
def test_no_ambiguous_confirmation(message):
    assert not user_confirms_add(message, True)


def test_explicit_confirmation_needs_proposal():
    assert user_confirms_add("Да, добавь!", True)
    assert not user_confirms_add("да, добавь", False)


@pytest.mark.parametrize("quantity", [True, 0, -1, 1.5, "NaN", "Infinity", None])
def test_invalid_quantities(quantity):
    session = Session(id="test")
    assert not propose_add_to_cart(session, product(), quantity)["ok"]
    assert session.cart == []


@pytest.mark.parametrize("changes", [{"quantity": None}, {"quantity": 0}, {"quantity": 1}, {"price": None}])
def test_unknown_values_and_overstock(changes):
    session = Session(id="test")
    assert not propose_add_to_cart(session, product(**changes), 2)["ok"]


def test_atomic_batch_decimal_totals_and_replay():
    products = {101: product(price=.1), 103: {**sample_products()[2], "price": .2}}
    session = Session(id="test")
    result = propose_cart(session, [{"product_id": 101, "quantity": 3}, {"product_id": 103, "quantity": 1}], products.get)
    assert result["ok"] and not session.cart
    pid = result["proposal"]["id"]
    assert confirm_pending(session, products.get, pid)["cart"]["total"] == "0.50"
    assert confirm_pending(session, products.get, pid)["status"] == "already_added"
    assert cart_view(session)["count"] == 4


def test_stock_recheck_rejects_entire_batch():
    products = {101: product(), 103: sample_products()[2]}
    session = Session(id="test")
    proposal = propose_cart(session, [{"product_id": 101, "quantity": 2}, {"product_id": 103, "quantity": 2}], products.get)
    products[103] = {**products[103], "quantity": 0}
    assert not confirm_pending(session, products.get, proposal["proposal"]["id"])["ok"]
    assert session.cart == []


def test_replacement_and_foreign_proposal_fail():
    session, other = Session(id="a"), Session(id="b")
    item = product()
    old = propose_add_to_cart(session, item, 1)["proposal"]["id"]
    current = propose_add_to_cart(session, item, 2)["proposal"]["id"]
    assert not confirm_pending(session, lambda _: item, old)["ok"]
    assert not confirm_pending(other, lambda _: item, current)["ok"]
    assert confirm_pending(session, lambda _: item, current)["cart"]["count"] == 2


def test_expired_proposal_and_changed_price():
    session = Session(id="test")
    item = product(price=10)
    pid = propose_add_to_cart(session, item, 1)["proposal"]["id"]
    session.pending = replace(session.pending, expires_at=0)
    assert not confirm_pending(session, lambda _: item, pid)["ok"]
    pid = propose_add_to_cart(session, item, 1)["proposal"]["id"]
    result = confirm_pending(session, lambda _: {**item, "price": 11}, pid)
    assert not result["ok"] and result["proposal"]["id"] != pid and not session.cart


def test_concurrent_confirm_only_once_and_existing_stock():
    session = Session(id="test")
    item = product(quantity=5)
    pid = propose_add_to_cart(session, item, 3)["proposal"]["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: confirm_pending(session, lambda _: item, pid), range(4)))
    assert all(r["ok"] for r in results)
    assert cart_view(session)["count"] == 3
    assert not propose_add_to_cart(session, item, 3)["ok"]


def test_duplicate_lines_and_documented_units():
    session = Session(id="test")
    item = product(quantity=3, unit="м", quantity_step=.5, min_quantity=1)
    assert not propose_add_to_cart(session, item, .25)["ok"]
    result = propose_cart(session, [{"product_id": 101, "quantity": 1.5}, {"product_id": 101, "quantity": 1.5}], lambda _: item)
    assert result["ok"] and result["proposal"]["items"][0]["quantity"] == 3


def test_remove_one_confirmed_line_invalidates_pending():
    products = {item["id"]: item for item in sample_products()}
    session = Session(id="test")
    pid = propose_cart(session, [{"product_id": 101, "quantity": 2},
                                 {"product_id": 103, "quantity": 1}], products.get)["proposal"]["id"]
    confirm_pending(session, products.get, pid)
    line_id = session.cart[0].line_id
    propose_add_to_cart(session, products[101], 1)

    result = remove_cart_line(session, line_id)
    assert result["ok"] and result["cart"]["count"] == 1
    assert session.pending is None
    assert len(session.cart) == 1 and session.cart[0].product_id == 103
    assert not remove_cart_line(session, line_id)["ok"]
