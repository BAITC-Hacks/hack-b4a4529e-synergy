from .cart import number, json_number
from .search import article_query, exact_matches, search_products, product_hit


def review_items(session, items, index, filenames):
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
        exact = exact_matches(index, query)
        is_article = item.get("query_type") == "article" or article_query(query)
        candidates = ([product_hit(p) for p in exact[:5]] if exact else [] if is_article or not query
                      else search_products(query, 5, index=index)["results"])
        candidates = [p for p in candidates if not p.get("analog_of")]
        status = "unresolved" if not candidates else "ambiguous"
        if len(exact) == 1:
            status = "resolved" if quantity is not None else "quantity_required"
        reviewed.append({"filename": filename, "source_reference": reference, "query": query,
                         "quantity": quantity, "candidate_product_ids": [p["id"] for p in candidates],
                         "candidates": candidates, "status": status})
    combined = list(session.attachment_review)
    for row in reviewed:
        key = (row["filename"], row["source_reference"], row["query"])
        combined = [old for old in combined if (old["filename"], old["source_reference"], old["query"]) != key]
        combined.append(row)
    if len(combined) > 500:
        raise ValueError("В одной сессии можно разобрать до 500 строк; разделите спецификацию.")
    session.attachment_review = combined
    session.last_search = [p for row in reviewed for p in row["candidates"]][:10]
    return {"items": reviewed, "total_reviewed": len(combined),
            "note": "Это разбор спецификации. Пользователь выбирает строки и проверяет количество. Корзина не изменена."}
