"""Grounded summaries: facts come exclusively from tool results."""
import re


def catalog_answer(message, products):
    lines = ["Данные каталога показаны в карточках ниже."]
    for product in products[:3]:
        if product.get("analog_reason"):
            lines.append(f"{product['name']}: {product['analog_reason']}")
    if re.search(r"сертифик|декларац", message, re.I):
        for product in products[:3]:
            available = bool(product.get("certificates") or product.get("certificate"))
            lines.append(f"{product['article']}: " + ("ссылки на сертификаты доступны в карточке." if available else "в каталоге нет ссылки на сертификат; это не означает отсутствие сертификации."))
    if re.search(r"склад|алмат|астан|тараз|шымкент|актау|атырау|караганд|уст[ь-]|талды", message, re.I):
        for product in products[:3]:
            stores = product.get("stores") or []
            lines.append(f"{product['article']}: " + ("; ".join(f"{s['name']}: {s.get('quantity') if s.get('quantity') is not None else 'неизвестно'}" for s in stores) if stores else "данных по складам нет."))
    if re.search(r"характерист|отлич|сравн", message, re.I):
        for product in products[:3]:
            props = product.get("properties") or {}
            lines.append(f"{product['article']}: " + ("; ".join(f"{key}: {value}" for key, value in list(props.items())[:12]) if props else "характеристики не указаны."))
    if any(p.get("quantity") == 0 for p in products) and not any(p.get("analog_of") for p in products):
        lines.append("Подходящий доступный аналог по известным характеристикам не найден. Уточните требования к замене.")
    return "\n".join(lines)


def terms_answer(terms):
    lines = []
    payment = terms.get("payment")
    if isinstance(payment, list):
        lines.append("Оплата: " + "; ".join(payment) + ".")
    elif isinstance(payment, dict):
        lines.extend(["Для физических лиц: " + "; ".join(payment["individual"]) + ".",
                      "Для компаний: " + "; ".join(payment["company"]) + "."])
    for key in ("documents", "delivery", "minimum", "notice"):
        if terms.get(key):
            lines.append(terms[key])
    rules = terms.get("product_rules")
    if rules:
        for key, label in (("min_quantity", "Минимальная партия"), ("quantity_step", "Кратность")):
            if rules.get(key) is not None:
                lines.append(f"{label}: {rules[key]} {rules.get('unit') or 'ед.'}.")
        if rules.get("purchase_rule_note"):
            lines.append(rules["purchase_rule_note"])
    lines.extend(terms.get("clarifications", []))
    lines.append("Источник условий приведён ниже.")
    return "\n".join(lines)
