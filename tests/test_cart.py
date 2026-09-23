from app.cart import Session, add_to_cart, propose_add_to_cart, user_confirms_add
from tests.helpers import sample_products


def product(quantity=23, price=64920, product_id=101):
    item = dict(sample_products()[0])
    item["id"] = product_id
    item["quantity"] = quantity
    item["price"] = price
    return item


def test_no_add_without_proposal():
    session = Session(id="t")
    result = add_to_cart(session, product(), 1, confirmed=True)
    assert result["ok"] is False
    assert session.cart == []


def test_reject_unknown_stock():
    session = Session(id="t")
    result = propose_add_to_cart(session, product(quantity=None), 1)
    assert result["ok"] is False
    assert session.pending is None


def test_reject_over_stock():
    session = Session(id="t")
    result = propose_add_to_cart(session, product(quantity=2), 5)
    assert result["ok"] is False
    assert session.pending is None


def test_confirm_adds_and_cart_link():
    session = Session(id="t")
    item = product(quantity=10, price=100)
    proposed = propose_add_to_cart(session, item, 2)
    assert proposed["ok"] is True
    result = add_to_cart(session, item, 2, confirmed=True)
    assert result["ok"] is True
    assert result["cart_url"] == "/cart"
    assert result["cart"]["items"][0]["quantity"] == 2
    assert result["cart"]["total"] == 200
    assert result["cart"]["url"] == "/cart"


def test_add_without_confirmation_is_rejected():
    session = Session(id="t")
    item = product()
    propose_add_to_cart(session, item, 1)
    result = add_to_cart(session, item, 1, confirmed=False)
    assert result["ok"] is False
    assert session.cart == []
    assert session.pending is not None


def test_remaining_stock_after_first_add():
    session = Session(id="t")
    item = product(quantity=5, price=10)
    propose_add_to_cart(session, item, 3)
    assert add_to_cart(session, item, 3, confirmed=True)["ok"] is True
    blocked = propose_add_to_cart(session, item, 3)
    assert blocked["ok"] is False
    allowed = propose_add_to_cart(session, item, 2)
    assert allowed["ok"] is True


def test_yes_confirms_only_with_pending():
    assert user_confirms_add("да, добавь", True) is True
    assert user_confirms_add("да", True) is True
    assert user_confirms_add("да, добавь", False) is False
    assert user_confirms_add("есть в наличии?", True) is False
