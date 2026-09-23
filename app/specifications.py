import hashlib
import re
from .cart import number, json_number, purchase_options
from .search import article_query, exact_matches, search_products, product_hit, constrain_results
from .units import unit_key


def review_items(session, items, index, filenames, *, local_only=False, match_cache=None):
    """Resolve extracted rows; the model never supplies authoritative product facts."""
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError("Передайте от 1 до 100 строк спецификации за один вызов.")
    reviewed = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Некорректная строка спецификации.")
        filename = str(item.get("filename") or "")[:180]
        if filename not in filenames and len(filenames) == 1:
            filename = next(iter(filenames))
        if filename not in filenames:
            raise ValueError("Строка должна ссылаться на приложенный файл.")
        query = str(item.get("query") or "").strip()[:500]
        reference = str(item.get("source_reference") or "не указано")[:180]
        quantity = item.get("quantity")
        if quantity is not None:
            quantity = number(quantity)
            if quantity <= 0:
                raise ValueError("Количество должно быть положительным.")
            quantity = json_number(quantity)
        document_id = str(item.get("document_id") or filename)
        row_id = hashlib.sha256((document_id + "\0" + reference).encode()).hexdigest()[:24]
        old = next((row for row in session.attachment_review if row.get("row_id") == row_id), None)
        if old and old.get("completion") in {"added", "excluded"}:
            reviewed.append(old)
            continue
        exact = exact_matches(index, query)
        is_article = item.get("query_type") == "article" or article_query(query)
        if exact:
            candidates = [product_hit(p) for p in exact[:5]]
        elif is_article or not query:
            candidates = []
        elif local_only and match_cache is not None and query in match_cache:
            candidates = [dict(p) for p in match_cache[query]]
        elif local_only:
            tokens = set(re.findall(r"[\w]+", query.casefold()))
            ranked = sorted(((len(tokens & set(re.findall(r"[\w]+", p["name"].casefold()))), p["id"], p)
                             for p in index.products), key=lambda value: (-value[0], value[1]))
            candidates = constrain_results(query, [product_hit(p) for score, _, p in ranked[:20]
                                                   if score >= max(1, len(tokens) * .6)], index=index)[:5]
        else:
            candidates = search_products(query, 5, index=index)["results"]
        candidates = [p for p in candidates if not p.get("analog_of")]
        if local_only and match_cache is not None:
            match_cache[query] = [dict(p) for p in candidates]
        status = "unresolved" if not candidates else "ambiguous"
        if len(exact) == 1:
            status = "resolved" if quantity is not None else "quantity_required"
        source_unit = str(item.get("source_unit") or "").strip()[:40]
        for candidate in candidates:
            candidate["purchase_options"] = purchase_options(session, index.get(candidate["id"]))
        ready = bool(source_unit) and len(exact) == 1 and quantity is not None and candidates[0]["purchase_options"]["can_add"]
        if ready and source_unit:
            ready = unit_key(source_unit) == unit_key(candidates[0]["unit"])
        reviewed.append({"row_id": row_id, "document_id": document_id,
                         "completion": "ready" if ready else "unresolved", "source_unit": source_unit,
                         "source_text": str(item.get("source_text") or query)[:2000],
                         "filename": filename, "source_reference": reference, "query": query,
                         "quantity": quantity, "candidate_product_ids": [p["id"] for p in candidates],
                         "candidates": candidates, "status": status})
    by_id = {row["row_id"]: row for row in session.attachment_review}
    by_id.update({row["row_id"]: row for row in reviewed})
    combined = list(by_id.values())
    if len(combined) > 20000:
        raise ValueError("В одном подборе поддерживается до 20 000 строк. Сохраните список и начните новый подбор.")
    session.attachment_review = combined
    session.last_search = [p for row in reviewed for p in row["candidates"]][:10]
    return {"items": reviewed, "total_reviewed": len(combined),
            "note": "Это разбор спецификации. Пользователь выбирает строки и проверяет количество. Корзина не изменена."}
