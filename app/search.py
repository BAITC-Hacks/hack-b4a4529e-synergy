from __future__ import annotations

import hashlib
import json
import re
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
from openai import OpenAI

from .catalog import public_properties, safe_url
from .config import EMBED_MODEL, INDEX_DIR, openai_api_key

TOKEN_RE = re.compile(r"[0-9a-zа-яё._-]{2,}", re.IGNORECASE)
MIN_SCORE = 0.30


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
        stamp = pointer.read_text() if pointer.exists() else None
        if _index is None or stamp != _index_stamp:
            _index = CatalogIndex.load()
            _index_stamp = stamp
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
        "unit": product.get("unit") or "ед.",
        "image": safe_url(product.get("image")), "url": safe_url(product.get("url")),
        "certificate": safe_url(product.get("certificate")),
        "properties": public_properties(product.get("properties")),
        "stores": product.get("stores") or [],
        "min_quantity": product.get("min_quantity"), "quantity_step": product.get("quantity_step"),
    })
    if score is not None:
        hit["score"] = round(float(score), 4)
    return hit


def product_detail(product: dict) -> dict:
    return {**product_hit(product), "description": product.get("description") or ""}


def exact_matches(index: CatalogIndex, query: str) -> list[dict]:
    found = {}
    key = _article_key(query)
    for product in index.by_article.get(key, []):
        found[product["id"]] = product
    for token in _tokens(query):
        for product in index.by_article.get(_article_key(token), []):
            found[product["id"]] = product
        if token.isdigit() and (product := index.get(int(token))):
            found[product["id"]] = product
    return list(found.values())


@lru_cache(maxsize=256)
def _embed_query(text: str, model: str) -> np.ndarray:
    client = OpenAI(api_key=openai_api_key(), timeout=10, max_retries=1)
    response = client.embeddings.create(model=model, input=[text])
    vector = np.asarray(response.data[0].embedding, dtype=np.float32)
    if not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
        raise ValueError("Invalid query embedding")
    return vector / np.linalg.norm(vector)


SPEC_KEYS = {
    "ток": ("NOMINALNYY_TOK",),
    "напряжение": ("NOMINALNOE_NAPRYAZHENIE", "NAPRYAZHENIE"),
    "полюса": ("KOLICHESTVO_POLYUSOV",),
    "отключающая способность": ("NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST",),
    "характеристика": ("KHARAKTERISTIKA_SRABATYVANIYA",),
    "монтаж": ("TIP_USTANOVKI", "SPOSOB_MONTAZHA"),
    "сечение": ("SECHENIE", "SECHENIE_ZHILY"),
    "число жил": ("KOLICHESTVO_ZHIL",),
    "материал жилы": ("MATERIAL_ZHILY",),
    "цоколь": ("TIP_TSOKOLYA",),
    "мощность": ("MOSHCHNOST_W", "MOSHCHNOST"),
    "температура света": ("TSVETOVAYA_TEMPERATURA",),
    "защита": ("STEPEN_ZASHCHITY_IP", "STEPEN_ZASHCHITY"),
    "тип лампы": ("TIP_LAMPY",),
}


def _spec_value(value) -> str:
    return re.sub(r"\s+", "", str(value).lower().replace(",", ".").translate(str.maketrans("авкх", "abkx")))


def technical_specs(product: dict) -> dict:
    props = product.get("properties") or {}
    result = {label: _spec_value(props[key]) for label, keys in SPEC_KEYS.items()
              for key in keys if props.get(key) not in (None, "")}
    name = product.get("name") or ""
    patterns = {
        "ток": r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*[аa](?![a-zа-я])",
        "полюса": r"(?<!\w)([1-4])\s*[pрф](?![a-zа-я])",
        "отключающая способность": r"(\d+(?:[.,]\d+)?)\s*[кk][аa]",
        "защита": r"\bIP\s*(\d{2})",
        "температура света": r"(\d{4})\s*[kк]\b",
    }
    for label, pattern in patterns.items():
        if label not in result and (match := re.search(pattern, name, re.I)):
            value = match.group(1)
            if label == "ток":
                value += "A"
            result[label] = _spec_value(value)
    if match := re.search(r"\b(\d+)\s*[xх×]\s*(\d+(?:[.,]\d+)?)", name, re.I):
        result.setdefault("число жил", match.group(1))
        result.setdefault("сечение", match.group(2).replace(",", "."))
    return result


def _analogs_for(index: CatalogIndex, source: dict, limit: int) -> list[dict]:
    original = technical_specs(source)
    ranked = []
    for product in index.products:
        if product["id"] == source["id"] or (product.get("quantity") or 0) <= 0:
            continue
        if not source.get("category") or source["category"] != product.get("category"):
            continue
        specs = technical_specs(product)
        common = original.keys() & specs.keys()
        if not common or any(original[key] != specs[key] for key in common):
            continue
        overlap = _tokens(source["name"]) & _tokens(product["name"])
        missing = sorted(original.keys() - specs.keys())
        reason = "Та же категория; совпадают " + ", ".join(f"{key}: {original[key]}" for key in sorted(common))
        reason += ". Перед заменой проверьте полный набор характеристик."
        if missing:
            reason += " Не указаны: " + ", ".join(missing) + "."
        hit = product_hit(product)
        hit.update(analog_of=source["id"], analog_reason=reason, compatibility="candidate_requires_review")
        ranked.append((len(common), len(overlap), product["id"], hit))
    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    return [x[3] for x in ranked[:limit]]


def search_products(query: str, limit: int = 5, *, index=None, embed_query=None) -> dict:
    catalog = index or get_index()
    limit = max(1, min(int(limit), 10))
    query = (query or "").strip()
    if not query:
        return {"results": [], "error": "пустой запрос"}
    exact = exact_matches(catalog, query)
    if exact:
        hits = [product_hit(product, 1.0) for product in exact[:limit]]
    else:
        vector = embed_query(query) if embed_query else _embed_query(query, catalog.metadata["model"])
        if vector.shape != (catalog.embeddings.shape[1],):
            raise ValueError("Query embedding dimensions differ from index")
        scores = catalog.embeddings @ vector
        order = np.argsort(-scores)[:limit]
        hits = [product_hit(catalog.products[int(i)], float(scores[i])) for i in order if scores[i] >= MIN_SCORE]
    if hits and hits[0]["availability"] == "out_of_stock":
        analogs = _analogs_for(catalog, catalog.get(hits[0]["id"]), 3)
        # Unfiltered semantic neighbors are not presented as substitutes.
        hits = [hits[0], *analogs] if len(exact) <= 1 else hits
    return {"query": query, "results": hits, "ambiguous": len(exact) > 1,
            "needs_clarification": not hits or len(exact) > 1,
            "snapshot": catalog.metadata,
            "note": "Остатки и цены из снимка; добавление не резервирует товар."}


def get_product(product_id: int, *, index=None) -> dict:
    catalog = index or get_index()
    product = catalog.get(product_id)
    if not product:
        return {"error": "товар не найден"}
    detail = product_detail(product)
    if product.get("quantity") == 0:
        detail["analogs"] = _analogs_for(catalog, product, 3)
    detail["snapshot"] = catalog.metadata
    return detail
