from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP

from .config import PROPOSAL_TTL
from .units import unit_key

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
    source_inputs: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        total = sum((item.line_total for item in self.items), Decimal(0))
        return {"id": self.id, "items": [item.as_dict() for item in self.items],
                "expires_at": self.expires_at, "total_label": format_kzt(total),
                "source_inputs": list(self.source_inputs)}


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
    messages: list[dict] = field(default_factory=list)
    chat_id: str = field(default_factory=lambda: secrets.token_hex(12))
    active_request: str | None = None
    cancelled_requests: list[str] = field(default_factory=list)
    finished_requests: list[str] = field(default_factory=list)
    cancel_event: object = field(default_factory=threading.Event, repr=False)
    selection: list[dict] = field(default_factory=list)
    selection_inputs: list[dict] = field(default_factory=list)
    request_status: dict = field(default_factory=dict)
    document_summary: list[dict] = field(default_factory=list)
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


def purchase_rule_issue(product: dict) -> str:
    """Missing commercial terms must never become implicit units or increments."""
    missing = []
    unit = str(product.get("unit") or "").strip()
    if product.get("unit_known") is not True or not unit or unit == "ед." or "\ufffd" in unit:
        missing.append("единица продажи")
    for key, label in (("min_quantity", "минимальная партия"), ("quantity_step", "кратность покупки / шаг отреза")):
        try:
            valid = number(product.get(key)) > 0
        except ValueError:
            valid = False
        if not valid:
            missing.append(label)
    if missing:
        return "Добавление недоступно: требуют подтверждения поставщика — " + ", ".join(missing) + "."
    return ""


def purchase_options(session: Session, product: dict) -> dict:
    """Values for the quantity control; _line remains the final authority."""
    unit = product.get("unit") or "ед."
    existing = sum((line.quantity for line in session.cart if line.product_id == product["id"]), Decimal(0))
    if product.get("quantity") is None or product.get("price") is None:
        return {"can_add": False, "reason": "Цена или остаток неизвестны."}
    stock = number(product["quantity"])
    remaining = max(Decimal(0), stock - existing)
    if stock <= 0:
        return {"can_add": False, "reason": "Нет в наличии по снимку каталога."}
    if issue := purchase_rule_issue(product):
        return {"can_add": False, "reason": issue}
    increment = number(product["quantity_step"])
    minimum = number(product["min_quantity"])
    needed = max(Decimal(0), minimum - existing)
    suggested = max(increment, (needed / increment).to_integral_value(rounding=ROUND_CEILING) * increment)
    can_add = remaining > 0 and suggested <= remaining
    if can_add:
        reason = ""
    elif remaining <= 0:
        reason = "Весь доступный остаток уже в корзине."
    else:
        reason = "Остатка недостаточно для минимального количества или кратности."
    return {"can_add": can_add, "reason": reason, "existing": str(existing), "remaining": str(remaining),
            "suggested_quantity": str(suggested), "step": str(increment),
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
    if issue := purchase_rule_issue(product):
        raise ValueError(issue)
    qty = number(quantity)
    if qty <= 0 or qty > Decimal("1000000000") or qty.normalize().as_tuple().exponent < -6:
        raise ValueError("укажите положительное количество (не более 6 знаков после запятой)")
    unit = product.get("unit") or "ед."
    step = number(product["quantity_step"])
    minimum = number(product["min_quantity"])
    if qty % step:
        raise ValueError(f"количество должно быть кратно {step}")
    existing = sum((x.quantity for x in session.cart if x.product_id == product["id"]), Decimal(0))
    if qty + existing < minimum:
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


def selection_draft(session, items, lookup):
    """Keep source contributions separate until server-side decimal aggregation."""
    if not isinstance(items, list) or len(items) > 5000:
        raise ValueError("Слишком много выбранных строк.")
    inputs, seen_sources, quantities, sources = [], set(), {}, {}
    review = {row.get("row_id"): row for row in session.attachment_review}
    errors = []
    for item in items:
        pid = item.get("product_id")
        sid = item.get("source_id") or None
        product = lookup(pid) if type(pid) is int else None
        try:
            if not product:
                raise ValueError("Товар не найден в каталоге.")
            qty = number(item.get("quantity"))
            if qty <= 0 or qty > Decimal("1000000000") or qty.normalize().as_tuple().exponent < -6:
                raise ValueError("Укажите положительное количество, не более 6 знаков после запятой.")
            row = review.get(sid) if sid else None
            if sid:
                if sid in seen_sources:
                    raise ValueError("Строка спецификации передана повторно.")
                seen_sources.add(sid)
                if not row or row.get("completion") in {"added", "excluded"}:
                    raise ValueError("Строка уже добавлена, исключена или недоступна.")
                if pid not in row["candidate_product_ids"]:
                    raise ValueError("Выберите товар из результатов этой строки.")
            source_unit = (row.get("source_unit") if row else item.get("source_unit")) or ""
            entry = {"product_id": pid, "quantity": str(qty), "source_id": sid, "source_unit": source_unit}
            inputs.append(entry)
            quantities[pid] = quantities.get(pid, Decimal(0)) + qty
            if row:
                sources.setdefault(pid, []).append({"row_id": sid, "filename": row["filename"],
                    "source_reference": row["source_reference"], "quantity": str(qty), "source_unit": source_unit})
            if source_unit and unit_key(source_unit) != unit_key(product.get("unit")):
                errors.append({"product_id": pid, "source_id": sid,
                    "error": f"В документе: {source_unit}; единица продажи: {product.get('unit') or 'неизвестна'}. Уточните единицу без автоматического пересчёта."})
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append({"product_id": pid, "source_id": sid, "error": str(exc)})
    lines = []
    for pid, qty in quantities.items():
        product = lookup(pid)
        try:
            line = _line(session, product, qty).as_dict()
        except ValueError as exc:
            errors.append({"product_id": pid, "source_id": None, "error": str(exc)})
            price = product.get("price")
            line = {"product_id": pid, "name": product["name"], "article": product.get("article", ""),
                    "quantity": json_number(qty), "unit": product.get("unit") or "ед.",
                    "unit_price": str(price) if price is not None else None,
                    "total_label": format_kzt(qty * number(price)) if price is not None else "Неизвестно"}
        line["sources"] = sources.get(pid, [])
        line["validation_errors"] = [e["error"] for e in errors if e["product_id"] == pid]
        lines.append(line)
    return inputs, lines, errors


def propose_cart(session: Session, items: list[dict], lookup, catalog_version: str = "") -> dict:
    with session.lock:
        # A failed replacement must not leave an older selection confirmable.
        session.pending = None
        try:
            if not isinstance(items, list) or not 1 <= len(items) <= 50:
                raise ValueError("предложение должно содержать от 1 до 50 позиций")
            inputs, draft, errors = selection_draft(session, items, lookup)
            session.selection_inputs = inputs
            if errors:
                return _err("Исправьте отмеченные позиции. " + errors[0]["error"], item_errors=errors)
            session.selection = draft
            lines = tuple(_line(session, lookup(line["product_id"]), line["quantity"]) for line in draft)
        except (ValueError, TypeError, AttributeError) as exc:
            return _err(str(exc))
        session.pending = Proposal(secrets.token_urlsafe(24), session.id, lines,
                                   time.time() + PROPOSAL_TTL, session.revision, catalog_version, tuple(inputs))
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
            if pending.source_inputs:
                _, _, errors = selection_draft(session, list(pending.source_inputs), lookup)
                if errors:
                    session.pending = None
                    return _err("Исправьте отмеченные позиции.", item_errors=errors)
            current = tuple(_line(session, lookup(line.product_id), line.quantity) for line in pending.items)
        except ValueError as exc:
            session.pending = None
            return _err(str(exc))
        changed = pending.revision != session.revision or any(
            (a.unit_price, a.unit, a.minimum, a.step) != (b.unit_price, b.unit, b.minimum, b.step)
            for a, b in zip(pending.items, current)
        )
        if changed:
            result = propose_cart(session, list(pending.source_inputs) or [
                {"product_id": x.product_id, "quantity": x.quantity} for x in current], lookup, catalog_version)
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
        contributed = {item.get("source_id"): item for item in pending.source_inputs if item.get("source_id")}
        for row in session.attachment_review:
            if row.get("row_id") in contributed:
                row.update(completion="added", added=contributed[row["row_id"]], proposal_id=pending.id)
        session.revision += 1
        session.completed = (session.completed + [pending.id])[-100:]
        session.pending = None
        session.selection = []
        session.selection_inputs = []
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


def change_quantity(session: Session, line_id: str, quantity, lookup, catalog_version="") -> dict:
    """Decreases are explicit edits; increases use the existing confirmation contract."""
    with session.lock:
        line = next((item for item in session.cart if item.line_id == line_id), None)
        if not line:
            return _err("товар уже удалён из корзины")
        try:
            desired = number(quantity)
            if desired > line.quantity:
                return propose_cart(session, [{"product_id": line.product_id, "quantity": desired - line.quantity}], lookup, catalog_version)
            check = Session("quantity-check", cart=[item for item in session.cart if item.line_id != line_id])
            checked = _line(check, lookup(line.product_id), desired)
            if checked.unit != line.unit:
                return _err("единица товара изменилась; удалите позицию и выберите её заново")
        except ValueError as exc:
            return _err(str(exc))
        session.cart = [replace(item, quantity=desired) if item.line_id == line_id else item for item in session.cart]
        session.pending = None
        session.revision += 1
        return {"ok": True, "status": "updated", "cart": cart_view(session)}
