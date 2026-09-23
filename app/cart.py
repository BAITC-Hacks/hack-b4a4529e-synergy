from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from .config import PROPOSAL_TTL

CONFIRM = re.compile(r"(?:да[,!]?\s+добавь(?:те)?(?:\s+в корзину)?|добав(?:ь|ьте|ить) в корзину|подтверждаю добавление)[.!]*", re.I)
CANCEL = re.compile(r"(?:нет|не добавляй|не добавлять|отмена|отмени(?: добавление)?)[.!]*", re.I)
CENT = Decimal("0.01")


def number(value) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError("нужно число")
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("нужно число") from exc
    if not result.is_finite() or abs(result) > Decimal("1000000000000"):
        raise ValueError("недопустимое число")
    return result


def json_number(value: Decimal):
    return int(value) if value == value.to_integral_value() else float(value)


def money_value(value: Decimal) -> str:
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")


def format_kzt(value) -> str:
    if value is None:
        return "—"
    amount = number(value).quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{amount:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
    return f"{text} ₸"


@dataclass(frozen=True)
class CartLine:
    product_id: int
    name: str
    article: str
    quantity: Decimal
    unit_price: Decimal
    stock: Decimal
    unit: str = "ед."
    minimum: Decimal | None = None
    step: Decimal | None = None
    line_id: str = field(default_factory=lambda: secrets.token_urlsafe(12))

    @property
    def line_total(self) -> Decimal:
        return (self.unit_price * self.quantity).quantize(CENT, rounding=ROUND_HALF_UP)

    def as_dict(self) -> dict:
        return {
            "line_id": self.line_id, "product_id": self.product_id,
            "name": self.name, "article": self.article,
            "quantity": json_number(self.quantity), "unit": self.unit,
            "unit_price": money_value(self.unit_price), "line_total": money_value(self.line_total),
            "stock": json_number(self.stock), "price_label": format_kzt(self.unit_price),
            "total_label": format_kzt(self.line_total),
        }


@dataclass(frozen=True)
class Proposal:
    id: str
    owner: str
    items: tuple[CartLine, ...]
    expires_at: float
    revision: int
    catalog_version: str

    def as_dict(self) -> dict:
        total = sum((item.line_total for item in self.items), Decimal(0))
        return {"id": self.id, "items": [item.as_dict() for item in self.items],
                "expires_at": self.expires_at, "total_label": format_kzt(total)}


@dataclass
class Session:
    id: str
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    cart: list[CartLine] = field(default_factory=list)
    pending: Proposal | None = None
    revision: int = 0
    completed: list[str] = field(default_factory=list)
    last_search: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    product_context: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    attachment_review: list[dict] = field(default_factory=list)
    attachment_issues: list[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)
    lock: object = field(default_factory=threading.RLock, repr=False)


def cart_view(session: Session) -> dict:
    total = sum((line.line_total for line in session.cart), Decimal(0))
    count = sum((line.quantity for line in session.cart), Decimal(0))
    return {"items": [line.as_dict() for line in session.cart], "total": money_value(total),
            "count": json_number(count), "total_label": format_kzt(total), "url": "/cart"}


def purchase_options(session: Session, product: dict) -> dict:
    """Values for the quantity control; _line remains the final authority."""
    unit = product.get("unit") or "ед."
    existing = sum((line.quantity for line in session.cart if line.product_id == product["id"]), Decimal(0))
    if product.get("quantity") is None or product.get("price") is None:
        return {"can_add": False, "reason": "Цена или остаток неизвестны."}
    stock = number(product["quantity"])
    remaining = max(Decimal(0), stock - existing)
    step = number(product["quantity_step"]) if product.get("quantity_step") is not None else None
    minimum = number(product["min_quantity"]) if product.get("min_quantity") is not None else None
    fractional = unit.lower() in {"м", "м.", "метр", "кг", "kg", "m"}
    increment = step or (None if fractional else Decimal(1))
    needed = max(Decimal(0), (minimum or Decimal(0)) - existing)
    if increment:
        suggested = max(increment, (needed / increment).to_integral_value(rounding=ROUND_CEILING) * increment)
    else:
        suggested = max(needed, min(Decimal(1), remaining))
    can_add = (remaining > 0 and suggested > 0 and suggested <= remaining
               and (increment is None or increment > 0))
    if can_add:
        reason = ""
    elif stock <= 0:
        reason = "Нет в наличии по снимку каталога."
    elif remaining <= 0:
        reason = "Весь доступный остаток уже в корзине."
    else:
        reason = "Остатка недостаточно для минимального количества или кратности."
    return {"can_add": can_add, "reason": reason, "existing": str(existing), "remaining": str(remaining),
            "suggested_quantity": str(suggested), "step": str(increment) if increment else "any",
            "unit": unit}


def user_confirms_add(text: str, has_pending: bool) -> bool:
    return bool(has_pending and CONFIRM.fullmatch((text or "").strip()))


def user_cancels(text: str) -> bool:
    return bool(CANCEL.fullmatch((text or "").strip()))


def _err(message: str, **extra) -> dict:
    return {"ok": False, "error": message, **extra}


def _line(session: Session, product: dict | None, quantity) -> CartLine:
    if not product:
        raise ValueError("товар не найден в каталоге")
    qty = number(quantity)
    if qty <= 0 or qty > Decimal("1000000000") or qty.normalize().as_tuple().exponent < -6:
        raise ValueError("укажите положительное количество (не более 6 знаков после запятой)")
    unit = product.get("unit") or "ед."
    step = number(product["quantity_step"]) if product.get("quantity_step") is not None else None
    minimum = number(product["min_quantity"]) if product.get("min_quantity") is not None else None
    if step is not None and (step <= 0 or qty % step):
        raise ValueError(f"количество должно быть кратно {step}")
    if step is None and unit.lower() not in {"м", "м.", "метр", "кг", "kg", "m"} and qty % 1:
        raise ValueError("для этой единицы товара укажите целое количество")
    existing = sum((x.quantity for x in session.cart if x.product_id == product["id"]), Decimal(0))
    if minimum is not None and (minimum <= 0 or qty + existing < minimum):
        raise ValueError(f"минимальное количество: {minimum} {unit}")
    if product.get("quantity") is None:
        raise ValueError("остаток неизвестен; добавить нельзя")
    stock = number(product["quantity"])
    if stock <= 0 or existing + qty > stock:
        raise ValueError(f"доступно для добавления: {max(Decimal(0), stock - existing)} {unit}")
    if product.get("price") is None:
        raise ValueError("цена неизвестна; уточните её до добавления")
    price = number(product["price"])
    if price < 0:
        raise ValueError("некорректная цена")
    price = price.quantize(CENT, rounding=ROUND_HALF_UP)
    return CartLine(product["id"], product["name"], product.get("article") or "",
                    qty, price, stock, unit, minimum, step)


def propose_cart(session: Session, items: list[dict], lookup, catalog_version: str = "") -> dict:
    with session.lock:
        # A failed replacement must not leave an older selection confirmable.
        session.pending = None
        try:
            if not isinstance(items, list) or not 1 <= len(items) <= 50:
                raise ValueError("предложение должно содержать от 1 до 50 позиций")
            quantities: dict[int, Decimal] = {}
            for item in items:
                product_id = item.get("product_id")
                if type(product_id) is not int:
                    raise ValueError("некорректный идентификатор товара")
                qty = number(item.get("quantity"))
                if qty <= 0:
                    raise ValueError("количество должно быть положительным")
                quantities[product_id] = quantities.get(product_id, Decimal(0)) + qty
            lines = tuple(_line(session, lookup(pid), qty) for pid, qty in quantities.items())
        except (ValueError, TypeError, AttributeError) as exc:
            return _err(str(exc))
        session.pending = Proposal(secrets.token_urlsafe(24), session.id, lines,
                                   time.time() + PROPOSAL_TTL, session.revision, catalog_version)
        return {"ok": True, "status": "awaiting_confirmation", "proposal": session.pending.as_dict()}


def propose_add_to_cart(session: Session, product: dict | None, quantity) -> dict:
    return propose_cart(session, [{"product_id": product["id"] if product else 0, "quantity": quantity}],
                        lambda _pid: product)


def confirm_pending(session: Session, lookup, proposal_id: str, catalog_version: str = "") -> dict:
    with session.lock:
        if proposal_id in session.completed:
            return {"ok": True, "status": "already_added", "cart": cart_view(session), "cart_url": "/cart"}
        pending = session.pending
        if not pending or pending.id != proposal_id or pending.owner != session.id:
            return _err("предложение устарело или не принадлежит этой сессии")
        if time.time() >= pending.expires_at:
            session.pending = None
            return _err("предложение истекло; запросите новое")
        try:
            current = tuple(_line(session, lookup(line.product_id), line.quantity) for line in pending.items)
        except ValueError as exc:
            session.pending = None
            return _err(str(exc))
        changed = pending.revision != session.revision or any(
            (a.unit_price, a.unit, a.minimum, a.step) != (b.unit_price, b.unit, b.minimum, b.step)
            for a, b in zip(pending.items, current)
        )
        if changed:
            result = propose_cart(session, [{"product_id": x.product_id, "quantity": x.quantity}
                                           for x in current], lookup, catalog_version)
            return {**result, "ok": False, "error": "условия изменились; подтвердите новое предложение"}
        updated = list(session.cart)
        for addition in current:
            # Preserve prices already accepted for earlier additions.
            for i, line in enumerate(updated):
                if (line.product_id, line.unit_price, line.unit) == (addition.product_id, addition.unit_price, addition.unit):
                    updated[i] = replace(line, quantity=line.quantity + addition.quantity)
                    break
            else:
                updated.append(addition)
        session.cart = updated
        session.revision += 1
        session.completed = (session.completed + [pending.id])[-100:]
        session.pending = None
        return {"ok": True, "status": "added", "cart": cart_view(session), "cart_url": "/cart"}


def cancel_pending(session: Session, proposal_id: str) -> dict:
    with session.lock:
        if not session.pending or session.pending.id != proposal_id:
            return _err("предложение уже неактуально")
        session.pending = None
        return {"ok": True, "status": "cancelled"}


def remove_cart_line(session: Session, line_id: str) -> dict:
    with session.lock:
        for index, line in enumerate(session.cart):
            if line.line_id == line_id:
                session.cart.pop(index)
                session.pending = None
                session.revision += 1
                return {"ok": True, "status": "removed", "cart": cart_view(session)}
        return _err("товар уже удалён из корзины")
