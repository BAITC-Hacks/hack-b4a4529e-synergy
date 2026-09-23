"""Read-only, source-backed purchase information; never accepts payment details."""
import json
from datetime import date
from pathlib import Path

from .cart import purchase_rule_issue

POLICY = json.loads(Path(__file__).with_name("policies.json").read_text(encoding="utf-8"))


def get_purchase_terms(topic="all", city=None, buyer_type=None, product=None):
    if topic not in {"all", "payment", "delivery", "minimum"}:
        raise ValueError("неизвестный вопрос об условиях покупки")
    result = {"version": POLICY["version"], "verified_at": POLICY["verified_at"],
              "sources": [POLICY["source"]], "clarifications": []}
    if (date.today() - date.fromisoformat(POLICY["verified_at"])).days > 90:
        result["notice"] = "Условия давно не проверялись; подтвердите актуальность по ссылке или у менеджера."
    if topic in {"all", "payment"}:
        result["payment"] = POLICY["payment"].get(buyer_type, POLICY["payment"])
        if buyer_type == "company":
            result["documents"] = POLICY["payment"]["company_documents"]
    if topic in {"all", "delivery"}:
        if not city:
            result["clarifications"].append("В какой город нужна доставка?")
            result["delivery"] = "Доставка зависит от города, адреса, веса и объёма заказа. " + POLICY["delivery"]["pickup"]
        else:
            key = "almaty" if str(city).strip().casefold() in {"алматы", "алмата", "almaty"} else "other"
            result["delivery"] = POLICY["delivery"][key]
            result["requires_manager_confirmation"] = True
    if topic in {"all", "minimum"}:
        result["minimum"] = POLICY["minimum"]
        if product:
            result["product_rules"] = {k: product.get(k) for k in
                                       ("id", "article", "unit", "unit_known", "min_quantity", "quantity_step", "purchase_rule_note")}
            result["product_rules"]["purchase_rule_note"] = purchase_rule_issue(product) or product.get("purchase_rule_note") or ""
            if product.get("min_quantity") is None:
                result["minimum"] += " Минимальная партия этого товара не подтверждена в источнике."
            if product.get("url"):
                result["sources"].append({"title": "Условия выбранного товара", "url": product["url"]})
        elif topic == "minimum":
            result["clarifications"].append("Укажите артикул товара для проверки минимальной партии.")
    return result
