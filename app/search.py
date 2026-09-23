from __future__ import annotations

import hashlib
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

from .catalog import public_properties, safe_url
from .alternatives import compare, family, normalized, specs
from .cart import format_kzt
from .config import EMBED_MODEL, INDEX_DIR
from .provider import client as provider_client

TOKEN_RE = re.compile(r"[0-9a-zа-яё._-]{2,}", re.IGNORECASE)
MIN_SCORE = 0.30


class CatalogUnavailable(RuntimeError):
    pass


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower().rstrip("._-") for m in TOKEN_RE.finditer(text or "")}


def _article_key(article: str) -> str:
    return (article or "").strip().lower().rstrip("_")


class CatalogIndex:
    def __init__(self, products: list[dict], embeddings: np.ndarray, metadata: dict | None = None):
        if not products or embeddings.ndim != 2 or len(products) != len(embeddings):
            raise ValueError("Invalid products/embeddings shape")
        if len({p["id"] for p in products}) != len(products) or not np.isfinite(embeddings).all():
            raise ValueError("Invalid catalog IDs or vectors")
        self.products = products
        self.embeddings = embeddings.astype(np.float32, copy=True)
        norms = np.linalg.norm(self.embeddings, axis=1, keepdims=True)
        if (norms == 0).any():
            raise ValueError("Zero embedding")
        self.embeddings /= norms
        self.metadata = metadata or {"version": "test", "model": EMBED_MODEL, "count": len(products)}
        self.by_id = {p["id"]: p for p in products}
        self.by_article: dict[str, list[dict]] = {}
        for product in products:
            for article in {product.get("article") or "", str((product.get("properties") or {}).get("ARTIKULPOSTAVSHCHIKA") or "")}:
                key = _article_key(article)
                if key:
                    bucket = self.by_article.setdefault(key, [])
                    if product not in bucket:
                        bucket.append(product)

    @classmethod
    def load(cls, index_dir: Path | None = None):
        root = index_dir or INDEX_DIR
        if (root / "current.json").exists():
            version = json.loads((root / "current.json").read_text())["version"]
            if not re.fullmatch(r"[0-9a-f]{32}", version):
                raise ValueError("Invalid index version")
            root = root / version
        if not (root / "metadata.json").is_file():
            raise FileNotFoundError("Индекс каталога не собран. Запустите: python -m app.index_build")
        metadata = json.loads((root / "metadata.json").read_text())
        if metadata["model"] != EMBED_MODEL:
            raise ValueError("Embedding model changed; rebuild the index")
        for filename, key in (("products.json", "products_sha256"), ("embeddings.npy", "vectors_sha256")):
            if hashlib.sha256((root / filename).read_bytes()).hexdigest() != metadata[key]:
                raise ValueError("Index checksum mismatch")
        products = json.loads((root / "products.json").read_text(encoding="utf-8"))
        embeddings = np.load(root / "embeddings.npy", allow_pickle=False)
        if embeddings.shape != (metadata["count"], metadata["dimensions"]):
            raise ValueError("Index metadata/vector mismatch")
        return cls(products, embeddings, metadata)

    def get(self, product_id: int) -> dict | None:
        return self.by_id.get(product_id)


_index = None
_index_stamp = None
_manual_index = False
_index_lock = threading.Lock()


def get_index() -> CatalogIndex:
    global _index, _index_stamp
    with _index_lock:
        if _manual_index:
            return _index
        pointer = INDEX_DIR / "current.json"
        try:
            stamp = pointer.read_text() if pointer.exists() else None
            if _index is None or stamp != _index_stamp:
                _index = CatalogIndex.load()
                _index_stamp = stamp
        except (FileNotFoundError, ValueError, KeyError, OSError) as exc:
            raise CatalogUnavailable("Catalog index is unavailable") from exc
        return _index


def set_index(index: CatalogIndex | None) -> None:
    global _index, _manual_index, _index_stamp
    with _index_lock:
        _index, _manual_index, _index_stamp = index, index is not None, None


def product_hit(product: dict, score: float | None = None) -> dict:
    quantity = product.get("quantity")
    hit = {key: product.get(key) for key in ("id", "name", "article", "price", "quantity", "category", "spec_snippet")}
    hit.update({
        "availability": "unknown" if quantity is None else "in_stock" if quantity > 0 else "out_of_stock",
        "price_label": format_kzt(product.get("price")),
        "unit": product.get("unit") or "ед.",
        "image": safe_url(product.get("image")), "url": safe_url(product.get("url")),
        "certificate": safe_url(product.get("certificate")),
        "certificates": [url for value in product.get("certificates", []) if (url := safe_url(value))],
        "properties": public_properties(product.get("properties")),
        "stores": product.get("stores") or [],
        "min_quantity": product.get("min_quantity"), "quantity_step": product.get("quantity_step"),
        "purchase_rule_note": product.get("purchase_rule_note") or "",
        "source_observed_at": product.get("source_observed_at"),
        "unit_known": product.get("unit_known", False),
        "unit_source": product.get("unit_source"),
        "lengths": product.get("lengths") or [],
        "description": product.get("description") or "",
        "supplier_availability": product.get("supplier_availability"),
    })
    if score is not None:
        hit["score"] = round(float(score), 4)
    return hit


def product_detail(product: dict) -> dict:
    return {**product_hit(product), "description": product.get("description") or "",
            "embedded_specifications": product.get("embedded_specifications") or {}}


def exact_matches(index: CatalogIndex, query: str) -> list[dict]:
    found = {}
    key = _article_key(query)
    for product in index.by_article.get(key, []):
        found[product["id"]] = product
    if key.isdigit() and (product := index.get(int(key))):
        found[product["id"]] = product
    explicit_codes = {
        _article_key(match.group(1))
        for match in re.finditer(r"(?:артикул(?:а|у|ом)?|article|id|код(?:а|у|ом)?)\s*[:№#]?\s*([0-9a-zа-яё._-]+)", query, re.I)
    }
    for token in _tokens(query):
        # Short numbers in descriptions are usually watts, amperes or quantities.
        if token.isdigit() and len(token) < 6 and token not in explicit_codes:
            continue
        for product in index.by_article.get(_article_key(token), []):
            found[product["id"]] = product
        if token.isdigit() and token in explicit_codes and (product := index.get(int(token))):
            found[product["id"]] = product
    return list(found.values())


@lru_cache(maxsize=256)
def _embed_query(text: str, model: str) -> np.ndarray:
    client = provider_client(timeout=10, max_retries=1)
    response = client.embeddings.create(model=model, input=[text])
    vector = np.asarray(response.data[0].embedding, dtype=np.float32)
    if not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
        raise ValueError("Invalid query embedding")
    return vector / np.linalg.norm(vector)


def technical_specs(product: dict) -> dict:
    return {label: normalized(label, value) for label, value in specs(product).items()}


def constrain_results(query: str, products: list[dict], *, index) -> list[dict]:
    """Exploratory tool queries must not loosen the customer's stated requirements."""
    if exact_matches(index, query):
        return products
    requested = {"name": query}
    wanted, wanted_family = technical_specs(requested), family(requested)
    if not wanted and not wanted_family:
        return products
    matches = []
    for product in products:
        source = index.get(product["id"]) or product
        actual, actual_family = technical_specs(source), family(source)
        if wanted_family and actual_family and wanted_family != actual_family:
            continue
        if any(key in actual and actual[key] != value for key, value in wanted.items()):
            continue
        missing = sorted(wanted.keys() - actual.keys())
        if wanted_family and not actual_family:
            missing.append("тип товара")
        hit = {**product}
        hit.pop("unverified_specs", None)
        if missing:
            hit["unverified_specs"] = missing
        matches.append(hit)
    return matches


def _analogs_for(index: CatalogIndex, source: dict, limit: int) -> list[dict]:
    ranked = []
    for product in index.products:
        if product["id"] == source["id"] or (product.get("quantity") or 0) <= 0:
            continue
        comparison = compare(source, product)
        if not comparison:
            continue
        overlap = _tokens(source["name"]) & _tokens(product["name"])
        hit = product_hit(product)
        hit.update(analog_of=source["id"], analog_reason=comparison["reason"],
                   compatibility=comparison["compatibility"], comparison=comparison)
        rank = len(comparison["matches"]) + (100 if comparison["compatibility"] == "supported_alternative" else 0)
        ranked.append((rank, len(overlap), product["id"], hit))
    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [x[3] for x in ranked[:limit]]


def article_query(query: str) -> bool:
    return bool(re.search(r"\b(?:артикул(?:а|у|ом)?|article|код)\s*[:№#]?\s*[0-9a-zа-яё._-]+", query, re.I)
                or re.fullmatch(r"\d{6,}_?|(?=.*\d)[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+){2,}_?", query.strip()))


def search_products(query: str, limit: int = 5, *, index=None, embed_query=None) -> dict:
    catalog = index or get_index()
    limit = max(1, min(int(limit), 10))
    query = (query or "").strip()
    if not query:
        return {"results": [], "error": "пустой запрос"}
    exact = exact_matches(catalog, query)
    if exact:
        hits = [{**product_hit(product, 1.0), "exact_match": True} for product in exact[:limit]]
    elif article_query(query):
        hits = []
    else:
        vector = embed_query(query) if embed_query else _embed_query(query, catalog.metadata["model"])
        if vector.shape != (catalog.embeddings.shape[1],):
            raise ValueError("Query embedding dimensions differ from index")
        scores = catalog.embeddings @ vector
        wanted = technical_specs({"name": query})
        wanted_family = family({"name": query})
        order = np.argsort(-scores)
        verified, uncertain = [], []
        for i in order:
            if scores[i] < MIN_SCORE:
                break
            product = catalog.products[int(i)]
            product_family = family(product)
            if wanted_family and product_family and wanted_family != product_family:
                continue
            actual = technical_specs(product)
            if any(key in actual and actual[key] != value for key, value in wanted.items()):
                continue
            missing = sorted(wanted.keys() - actual.keys())
            hit = product_hit(product, float(scores[i]))
            if missing:
                hit["unverified_specs"] = missing
                uncertain.append(hit)
            else:
                verified.append(hit)
            if len(verified) >= limit:
                break
        hits = (verified or uncertain)[:limit]
    if hits and hits[0]["availability"] == "out_of_stock":
        analogs = _analogs_for(catalog, catalog.get(hits[0]["id"]), 3)
        if len(exact) <= 1:
            seen = {hits[0]["id"]}
            combined = [hits[0]]
            for hit in [*analogs, *hits[1:]]:
                if hit["id"] not in seen:
                    combined.append(hit)
                    seen.add(hit["id"])
            hits = combined[:max(limit, len(analogs) + 1)]
    semantic_ambiguous = not exact and len(hits) > 1 and any(
        technical_specs(catalog.get(hit["id"])) != technical_specs(catalog.get(hits[0]["id"]))
        for hit in hits[1:]
    )
    ambiguous = len(exact) > 1 or semantic_ambiguous
    return {"query": query, "results": hits, "ambiguous": ambiguous,
            "needs_clarification": not hits or ambiguous or bool(hits[0].get("unverified_specs")),
            "snapshot": catalog.metadata,
            "note": "Остатки и цены из снимка; добавление не резервирует товар."}


def get_product(product_id: int, *, index=None) -> dict:
    catalog = index or get_index()
    product = catalog.get(product_id)
    if not product:
        return {"error": "товар не найден"}
    detail = product_detail(product)
    detail["exact_match"] = True
    if product.get("quantity") == 0:
        detail["analogs"] = _analogs_for(catalog, product, 3)
    detail["snapshot"] = catalog.metadata
    return detail
