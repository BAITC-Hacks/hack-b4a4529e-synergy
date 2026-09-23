"""Lossless API snapshots. Raw response bodies are never normalized in place."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

API_BASE = "https://ekt.kz/api/products"
PAGE_SIZE = 500


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def checksum(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def atomic_bytes(path: Path, body: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path: Path, value):
    atomic_bytes(path, json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"))


def run_path(root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid catalog run ID")
    return root / "raw" / run_id


def detail_url(item: dict, base: str = API_BASE) -> str:
    product_id = item["id"]
    url = item.get("url_api_detail") or f"{base}/detail?id={product_id}"
    parsed, origin = urlparse(url), urlparse(base)
    if (parsed.scheme, parsed.netloc, parsed.path) != (origin.scheme, origin.netloc, origin.path + "/detail"):
        raise ValueError(f"Untrusted detail endpoint for product {product_id}")
    if parsed.username or parsed.password or parsed.fragment or parse_qs(parsed.query) != {"id": [str(product_id)]}:
        raise ValueError(f"Invalid detail parameters for product {product_id}")
    return url


def validate_page(data, page: int, per_page: int = PAGE_SIZE) -> list[dict]:
    if not isinstance(data, dict) or data.get("page") != page or data.get("per_page") != per_page:
        raise ValueError(f"Invalid pagination on page {page}")
    items = data.get("items")
    if not isinstance(items, list) or len(items) > per_page or data.get("count") != len(items):
        raise ValueError(f"Invalid item count on page {page}")
    ids = []
    for item in items:
        if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
            raise ValueError(f"Invalid product ID on page {page}")
        detail_url(item)
        ids.append(item["id"])
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate product IDs on page {page}")
    return items


def validate_detail(data, product_id: int):
    if (not isinstance(data, dict) or type(data.get("id")) is not int or data["id"] != product_id
            or not isinstance(data.get("name"), str) or not data["name"].strip()):
        raise ValueError(f"Invalid detail for product {product_id}")
    # Preserve arbitrary fields, but reject malformed shapes required by ingestion.
    for key, kind in (("properties", dict), ("stores", list), ("offers", list)):
        if data.get(key) is not None and not isinstance(data[key], kind):
            raise ValueError(f"Invalid {key} for product {product_id}")
    if any(not isinstance(item, dict) for item in data.get("stores") or []):
        raise ValueError(f"Invalid warehouse for product {product_id}")
    return data


def read_response(folder: Path, entry: dict):
    if entry.get("status") != "complete" or entry.get("http_status") != 200:
        raise ValueError("Response is not complete")
    path = (folder / entry["path"]).resolve()
    if not path.is_relative_to(folder.resolve()):
        raise ValueError("Invalid response path")
    body = path.read_bytes()
    if checksum(body) != entry["sha256"]:
        raise ValueError(f"Response checksum mismatch: {entry['path']}")
    return json.loads(body)


def listing_records(folder: Path, manifest: dict) -> dict[int, dict]:
    last = manifest.get("last_page")
    if not isinstance(last, int) or last < 1:
        raise ValueError("Listing is incomplete")
    records = {}
    for number in range(1, last + 1):
        entry = manifest["pages"].get(str(number))
        if not entry:
            raise ValueError(f"Missing listing page {number}")
        items = validate_page(read_response(folder, entry), number, manifest["page_size"])
        if (len(items) < manifest["page_size"]) != (number == last):
            raise ValueError("Invalid listing termination")
        for item in items:
            if item["id"] in records:
                raise ValueError("Repeated listing content; catalog changed or pagination looped")
            records[item["id"]] = {"item": item, "page": number}
    return records


def archived_records(root: Path, run_id: str | None = None):
    if run_id is None:
        run_id = json.loads((root / "current.json").read_text())["run_id"]
    folder = run_path(root, run_id)
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("status") != "complete" or manifest.get("run_id") != run_id:
        raise ValueError("Catalog archive is incomplete or unsupported")
    records = listing_records(folder, manifest)
    if not records:
        raise ValueError("Catalog archive is empty")
    for product_id, listing in records.items():
        entry = manifest["details"].get(str(product_id))
        if not entry:
            raise ValueError(f"Missing detail {product_id}")
        raw = validate_detail(read_response(folder, entry), product_id)
        yield raw, {"run_id": run_id, "detail_path": f"raw/{run_id}/{entry['path']}",
                    "listing_path": f"raw/{run_id}/{manifest['pages'][str(listing['page'])]['path']}",
                    "sha256": entry["sha256"], "observed_at": entry["fetched_at"]}
