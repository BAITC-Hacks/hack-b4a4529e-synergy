"""Read-only browsing and comparison using catalog facts."""
import numpy as np
from .search import exact_matches, article_query, _embed_query, product_hit, constrain_results, MIN_SCORE
from .alternatives import specs


def browse(index, query, offset=0, limit=10, sort="relevance", in_stock=False):
    exact = exact_matches(index, query)
    models = index.by_model_code.get(query.strip().casefold(), [])
    if exact or models:
        products = exact or models
    elif article_query(query):
        products = []
    else:
        vector = _embed_query(query, index.metadata["model"])
        scores = index.embeddings @ vector
        products = [index.products[int(i)] for i in np.argsort(-scores) if scores[i] >= MIN_SCORE]
    hits = constrain_results(query, [product_hit(p) for p in products], index=index)
    if in_stock:
        hits = [p for p in hits if (p.get("quantity") or 0) > 0]
    if sort == "price":
        hits.sort(key=lambda p: (p.get("price") is None, float(p.get("price") or 0), p["id"]))
    return {"results": hits[offset:offset + limit], "total": len(hits), "offset": offset,
            "has_more": offset + limit < len(hits), "query": query, "snapshot": index.metadata}


def comparison(index, ids):
    products = [index.get(pid) for pid in ids]
    if any(p is None for p in products):
        raise ValueError("Товар не найден.")
    attributes = sorted(set().union(*(specs(p).keys() for p in products)))
    return {"products": [product_hit(p) for p in products],
            "attributes": [{"name": key, "values": [specs(p).get(key) or "Неизвестно" for p in products]} for key in attributes],
            "note": "Сравнение известных характеристик не подтверждает взаимозаменяемость."}
