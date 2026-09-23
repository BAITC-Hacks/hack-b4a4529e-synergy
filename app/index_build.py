from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from openai import OpenAI

from .catalog import embed_text, load_products
from .config import EMBED_MODEL, INDEX_DIR, openai_api_key


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def embed_texts(client, texts: list[str], cache_path: Path, model: str = EMBED_MODEL) -> np.ndarray:
    vectors = []
    with sqlite3.connect(cache_path) as cache:
        cache.execute("CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB NOT NULL)")
        for start in range(0, len(texts), 64):
            batch = texts[start:start + 64]
            keys = [digest((model + "\n" + text).encode()) for text in batch]
            saved = {key: np.frombuffer(row[0], dtype=np.float32).copy()
                     for key in keys if (row := cache.execute("SELECT vector FROM vectors WHERE key=?", (key,)).fetchone())}
            missing = list(dict.fromkeys(key for key in keys if key not in saved))
            if missing:
                text_by_key = dict(zip(keys, batch))
                response = client.embeddings.create(model=model, input=[text_by_key[key] for key in missing])
                items = sorted(response.data, key=lambda item: item.index)
                if [item.index for item in items] != list(range(len(missing))):
                    raise ValueError("Embedding response does not match requested products")
                for key, item in zip(missing, items):
                    vector = np.asarray(item.embedding, dtype=np.float32)
                    if vector.ndim != 1 or not len(vector) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
                        raise ValueError("Invalid embedding")
                    vector /= np.linalg.norm(vector)
                    saved[key] = vector
                    cache.execute("INSERT OR REPLACE INTO vectors VALUES (?,?)", (key, vector.tobytes()))
                cache.commit()
            vectors.extend(saved[key] for key in keys)
            print(f"Embedded {min(start + 64, len(texts))}/{len(texts)} (new {len(missing)})", flush=True)
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim != 2 or not np.isfinite(array).all():
        raise ValueError("Invalid or inconsistent embedding dimensions")
    return array


def build_index(data_dir: Path | None = None, index_dir: Path | None = None, *, client=None, limit: int | None = None) -> Path:
    # load_products freezes complete CSV records; later appended rows belong to a future build.
    products = load_products(data_dir)
    if limit is not None:
        products = products[:limit]
    if not products:
        raise ValueError("No products found. Download a catalog snapshot first.")
    target = index_dir or INDEX_DIR
    target.mkdir(parents=True, exist_ok=True)
    client = client or OpenAI(api_key=openai_api_key(), timeout=30, max_retries=2)
    vectors = embed_texts(client, [embed_text(p)[:12000] for p in products], target / "embedding-cache.sqlite3")
    version = uuid.uuid4().hex
    folder = target / version
    folder.mkdir()
    product_data = json.dumps(products, ensure_ascii=False, allow_nan=False).encode()
    (folder / "products.json").write_bytes(product_data)
    np.save(folder / "embeddings.npy", vectors)
    metadata = {
        "version": version, "model": EMBED_MODEL, "dimensions": vectors.shape[1],
        "count": len(products), "indexed_at": datetime.now(timezone.utc).isoformat(),
        "source": "downloaded catalog snapshot", "stock_observed_at": None,
        "products_sha256": digest(product_data),
        "vectors_sha256": digest((folder / "embeddings.npy").read_bytes()),
        "known_stock": sum(p.get("quantity") is not None for p in products),
        "certificates": sum(bool(p.get("certificate")) for p in products),
        "known_prices": sum(p.get("price") is not None for p in products),
        "known_units": sum(bool(p.get("unit_known")) for p in products),
        "minimum_quantities": sum(p.get("min_quantity") is not None for p in products),
        "purchase_multiples": sum(p.get("quantity_step") is not None for p in products),
        "unresolved_certificates": sum(bool(p.get("certificate_references")) and not p.get("certificates") for p in products),
    }
    (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    # Validate exactly what the server will load before publishing a single pointer.
    from .search import CatalogIndex
    CatalogIndex.load(folder)
    pointer = target / f".current-{version}.tmp"
    pointer.write_text(json.dumps({"version": version}), encoding="utf-8")
    os.replace(pointer, target / "current.json")
    print(f"Published {len(products)} products, version {version}", flush=True)
    return folder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, help="Build a representative subset; omit for all downloaded records")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    build_index(limit=args.limit)


if __name__ == "__main__":
    main()
