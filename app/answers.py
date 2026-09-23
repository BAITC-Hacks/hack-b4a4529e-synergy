"""Grounded summaries: facts come exclusively from tool results."""
import re


def clarification_answer(message, products, model_text=""):
    # Preserve plain clarification questions, never generated prices, URLs or compatibility claims.
    for sentence in re.findall(r"(?:^|[.!?]\s*|\n)([^.!?\n]+\?)", model_text):
        sentence = sentence.strip()
        if (re.match(r"^(?:Уточните|Сколько|Какой|Какая|Какие|Какое|Нужен|Нужна|Нужны|Что важнее)\b", sentence)
                and not re.search(r"\d|https?://|цен|остат|совместим|подход|безопас", sentence, re.I)):
            return sentence
    from .alternatives import family, specs
    wanted = specs({"name": message})
    kind = family({"name": message}) or (family(products[0]) if products else None)
    questions = {
        "breaker": [("ток", "Какой номинальный ток требуется?"), ("полюса", "Сколько полюсов требуется?"),
                    ("характеристика", "Какая характеристика срабатывания требуется?")],
        "cable": [("число жил", "Сколько жил требуется?"), ("сечение", "Какое сечение жил требуется?"),
                  ("напряжение", "На какое напряжение нужен кабель?")],
        "lamp": [("цоколь", "Какой цоколь требуется?"), ("мощность", "Какая мощность нужна?")],
    }
    for key, question in questions.get(kind, []):
        if key not in wanted:
            return question
    return "Что важнее при выборе: цена, наличие в вашем городе или конкретная марка?"

from .cart import purchase_rule_issue


def catalog_answer(message, products):
    exact = [p for p in products if p.get("exact_match") and not p.get("analog_of")]
    lines = ["Нашёл точное совпадение. Укажите количество и добавьте товар в выбор." if len(exact) == 1
             else "Нашёл несколько вариантов. Выберите подходящие товары." if len(products) > 1
             else "Нашёл товар. Проверьте характеристики и укажите количество."]
    if products and all(purchase_rule_issue(p) for p in products):
        lines = ["Нашёл товары по запросу. До добавления нужно подтвердить единицы продажи, минимальные партии и кратность у поставщика."]
        if len(products) == 1:
            lines = ["Нашёл точное совпадение." if exact else "Нашёл товар.", purchase_rule_issue(products[0])]
    if re.search(r"поставщик|поступлен", message, re.I):
        for product in products[:3]:
            supplier = product.get("supplier_availability")
            if supplier:
                quantity = supplier.get("quantity")
                lead_time = supplier.get("lead_time_raw")
                lines.append(f"{product['article']}: у поставщика — {quantity if quantity is not None else 'остаток неизвестен'}; "
                             + (f"срок поступления по данным поставщика: {lead_time}." if lead_time else "срок поступления не указан."))
                lines.append(supplier["note"])
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
                if rules.get("unit_known"):
                    lines.append(f"{label}: {rules[key]} {rules['unit']}.")
                else:
                    lines.append(f"{label}: {rules[key]}; единицу продажи уточните у поставщика.")
        if rules.get("purchase_rule_note"):
            lines.append(rules["purchase_rule_note"])
    lines.extend(terms.get("clarifications", []))
    lines.append("Источник условий приведён ниже.")
    return "\n".join(lines)
