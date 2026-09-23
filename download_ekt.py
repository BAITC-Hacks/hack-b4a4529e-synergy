#!/usr/bin/env python3
"""Archive complete EKT API responses; resume from checksummed raw files."""

import argparse
import asyncio
import io
import csv
import fcntl
import json
import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiohttp

from app.catalog_archive import (API_BASE, PAGE_SIZE, archived_records, atomic_bytes, atomic_json,
                                 checksum, detail_url, listing_records, read_response, run_path,
                                 utc_now, validate_detail, validate_page)

ROOT = Path(__file__).resolve().parent
BASE = API_BASE
OUT = ROOT / "data" / "ekt"
CONCURRENCY = int(os.environ.get("EKT_CONCURRENCY", "16"))
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30, sock_connect=10, sock_read=25)
LIST_TIMEOUT = aiohttp.ClientTimeout(total=60, sock_connect=10, sock_read=55)
COLUMNS = [
    "id",
    "article",
    "name",
    "price",
    "quantity",
    "description",
    "image",
    "url",
    "stores",
    "properties",
    "offers",
]


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def flat(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " | ".join(part for part in (flat(item) for item in value) if part)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = flat(item)
            parts.append(f"{key}={text}" if text else str(key))
        return " | ".join(parts)
    return str(value)


def stores_cell(stores) -> str:
    parts = []
    for store in stores or []:
        name = store.get("name") or store.get("id")
        parts.append(f"{name}={store.get('quantity')}")
    return " | ".join(parts)


def row_from_detail(data: dict) -> dict:
    return {
        "id": data.get("id", ""),
        "article": data.get("article") or "",
        "name": data.get("name") or "",
        "price": "" if data.get("price") is None else data.get("price"),
        "quantity": "" if data.get("quantity") is None else data.get("quantity"),
        "description": data.get("description") or "",
        "image": data.get("image") or "",
        "url": data.get("url") or "",
        "stores": stores_cell(data.get("stores")),
        "properties": flat(data.get("properties")),
        "offers": flat(data.get("offers")),
    }


def save_detail(data: dict, root: Path | None = None) -> None:
    """Publish a lossless detail without modifying the append-only CSV schema."""
    product_id = data.get("id")
    if type(product_id) is not int or product_id <= 0 or not data.get("name"):
        raise ValueError("Invalid product detail")
    directory = (root or OUT) / "details"
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{product_id}-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(temporary, directory / f"{product_id}.json")



async def fetch_response(session, url, params, timeout=REQUEST_TIMEOUT):
    """Return the body bytes without JSON reserialization or credential logging."""
    for attempt in range(6):
        try:
            async with session.get(url, params=params, timeout=timeout, allow_redirects=False) as response:
                body = await response.read()
                status = response.status
            if status == 200:
                return body, status
            if status not in {429, 500, 502, 503, 504}:
                return body, status
        except (aiohttp.ClientError, asyncio.TimeoutError):
            status = "transport failure"
        if attempt < 5:
            await asyncio.sleep(min(.5 * 2 ** attempt, 8))
    if isinstance(status, int):
        return body, status
    raise ValueError(f"API request failed after retries ({status})")


def open_run(root, new=False):
    pointer = root / "download.json"
    if pointer.exists() and not new:
        run_id = json.loads(pointer.read_text())["run_id"]
        folder = run_path(root, run_id)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("run_id") != run_id:
            raise ValueError("Unsupported download manifest; start with --new")
        return folder, manifest
    run_id = uuid.uuid4().hex
    folder = run_path(root, run_id)
    manifest = {"schema_version": 1, "run_id": run_id, "api_base": BASE,
                "page_size": PAGE_SIZE, "started_at": utc_now(), "status": "downloading",
                "last_page": None, "pages": {}, "details": {}}
    atomic_json(folder / "manifest.json", manifest)
    atomic_json(pointer, {"run_id": run_id})
    return folder, manifest


async def obtain(session, folder, entries, key, path, url, params, validate, timeout=REQUEST_TIMEOUT):
    entry = entries.get(key)
    if entry:
        try:
            data = read_response(folder, entry)
            validate(data)
            return data
        except (OSError, ValueError, KeyError, TypeError):
            pass  # Only incomplete runs are repaired; completed archives remain immutable.
    request_params = {key: values[0] if len(values) == 1 else values
                      for key, values in parse_qs(urlparse(url).query).items()}
    entry = {"path": path, "url": url, "params": {**request_params, **params}, "status": "pending"}
    entries[key] = entry
    try:
        body, status = await fetch_response(session, url, params, timeout)
        # Persist even malformed HTTP-200 bodies; invalid entries are never complete.
        atomic_bytes(folder / path, body)
        entry.update(http_status=status, fetched_at=utc_now(), sha256=checksum(body), bytes=len(body))
        if status != 200:
            raise ValueError(f"API HTTP {status}")
        data = json.loads(body)
        validate(data)
        entry["status"] = "complete"
        return data
    except (ValueError, OSError, TypeError) as exc:
        entry.update(status="failed", error=type(exc).__name__)
        raise


async def collect_archive(session, folder, manifest):
    seen = set()
    number = 1
    manifest["last_page"] = None
    while True:
        try:
            data = await obtain(session, folder, manifest["pages"], str(number), f"pages/{number:05d}.json",
                                BASE, {"page": number, "per_page": PAGE_SIZE},
                                lambda d: validate_page(d, number), LIST_TIMEOUT)
            items = data["items"]
            ids = {item["id"] for item in items}
            if seen & ids:
                raise ValueError("Repeated listing content; use --new after the API listing stabilizes")
            seen.update(ids)
            print(f"Listing page={number} products={len(seen)}", flush=True)
            if len(items) < PAGE_SIZE:
                manifest["last_page"] = number
                break
            number += 1
        finally:
            atomic_json(folder / "manifest.json", manifest)
    if not seen:
        raise ValueError("Empty API catalog; active catalog preserved")
    return listing_records(folder, manifest)


async def download_archive_details(session, folder, manifest, records, concurrency):
    queue = asyncio.Queue()
    for product_id in sorted(records):
        queue.put_nowait(product_id)
    failures, checked = [], 0

    async def worker():
        nonlocal checked
        while True:
            try:
                product_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            item = records[product_id]["item"]
            try:
                await obtain(session, folder, manifest["details"], str(product_id),
                             f"details/{product_id}.json", detail_url(item), {},
                             lambda d: validate_detail(d, product_id))
            except (ValueError, OSError, TypeError):
                failures.append(product_id)
            checked += 1
            if checked % 64 == 0:
                atomic_json(folder / "manifest.json", manifest)
            if checked % 256 == 0 or checked == len(records):
                print(f"Details checked={checked}/{len(records)} failed={len(failures)}", flush=True)

    try:
        await asyncio.gather(*(worker() for _ in range(concurrency)))
    finally:
        atomic_json(folder / "manifest.json", manifest)
    return failures


def export_and_report(root, folder, manifest):
    from collections import Counter
    from app.catalog import normalize_product
    normalized, originals = [], []
    field_counts, property_counts, field_types = Counter(), Counter(), {}
    observed_fields = ("unit", "measure", "quantity", "price", "properties.CML2_BASE_UNIT",
                       "properties.METRAZHNYY_TOVAR", "properties.DLINA_SHNURA", "properties.DLINA_RULONA",
                       "properties.DLINA_KABELYA", "properties.DLINA", "properties.KRATNOST_MIN",
                       "properties.KRATNOST_MAKS", "properties.FILES_CERTIFICATES",
                       "properties.KOL_VO_U_POSTAVSHCHIKA", "properties.KOL_VO_CHASOV_DOSTAVKI_OT_POSTAVSHCHIKA")
    source_presence = {key: Counter() for key in observed_fields}
    for raw, source in archived_records(root, manifest["run_id"]):
        product = normalize_product(raw)
        if product is None:
            raise ValueError(f"Cannot normalize archived product {raw.get('id')}")
        product.update(source_response=source, source_observed_at=source["observed_at"])
        normalized.append(product)
        originals.append(raw)
        for field in observed_fields:
            parent, _, key = field.rpartition(".")
            container = (raw.get(parent) or {}) if parent else raw
            state = "absent" if key not in container else "null" if container[key] is None else "empty" if container[key] in ("", [], {}) else "present"
            source_presence[field][state] += 1
        field_counts.update(raw.keys())
        property_counts.update((raw.get("properties") or {}).keys())
        for key, value in raw.items():
            field_types.setdefault(key, Counter())[type(value).__name__] += 1
    normalized.sort(key=lambda p: p["id"])
    atomic_json(folder / "products.json", normalized)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, delimiter=";")
    writer.writeheader()
    for raw in originals:
        # CSV is a projection only. JSON cells retain nested data rather than flatten it.
        writer.writerow({key: json.dumps(raw[key], ensure_ascii=False) if isinstance(raw.get(key), (dict, list))
                         else raw.get(key) for key in COLUMNS})
    atomic_bytes(folder / "products.csv", stream.getvalue().encode("utf-8-sig"))
    coverage = {"selling_unit": sum(p["unit_known"] for p in normalized),
                "explicit_lengths": sum(bool(p["lengths"]) for p in normalized),
                "normalized_lengths": sum(any(v["metres"] is not None for v in p["lengths"]) for p in normalized),
                "embedded_specifications": sum(bool(p["embedded_specifications"]) for p in normalized),
                "price": sum(p["price"] is not None for p in normalized),
                "stock": sum(p["quantity"] is not None for p in normalized),
                "purchase_increment": sum(p["quantity_step"] is not None for p in normalized),
                "minimum_quantity": sum(p["min_quantity"] is not None for p in normalized),
                "certificate_links": sum(bool(p["certificates"]) for p in normalized),
                "certificate_references": sum(bool(p["certificate_references"]) for p in normalized),
                "supplier_availability": sum(bool(p["supplier_availability"]) for p in normalized)}
    report = {"run_id": manifest["run_id"], "verified_at": utc_now(), "status": "complete",
              "listing_pages": manifest["last_page"], "discovered_products": len(normalized),
              "valid_details": len(normalized), "missing_details": [], "failed_details": [],
              "raw_checksums_verified": True, "raw_top_level_field_counts": field_counts,
              "raw_top_level_value_types": field_types, "raw_property_counts": property_counts,
              "source_field_presence": source_presence,
              "derived_field_coverage": coverage,
              "missing_derived_fields": {key: len(normalized) - count for key, count in coverage.items()},
              "note": "Raw responses preserve all returned fields. Missing derived values may be absent, ambiguous, or unrecognized in the source; see field provenance and original response."}
    previous = root / "current.json"
    if previous.exists():
        old_folder = run_path(root, json.loads(previous.read_text())["run_id"])
        old_manifest = json.loads((old_folder / "manifest.json").read_text())
        old = set(listing_records(old_folder, old_manifest))
    elif (root / "products.csv").exists():
        with (root / "products.csv").open(encoding="utf-8-sig", newline="") as handle:
            old = {int(row["id"]) for row in csv.DictReader(handle, delimiter=";")}
    else:
        old = set()
    report["previous_ids_not_listed"] = sorted(old - {p["id"] for p in normalized})
    atomic_json(folder / "report.json", report)
    return report


async def download_catalog(session, root=OUT, *, new=False, concurrency=CONCURRENCY):
    if concurrency < 1:
        raise ValueError("Concurrency must be positive")
    folder, manifest = open_run(root, new)
    if manifest["status"] == "complete":
        # Verify before reporting an immutable run as usable. --new refreshes the source.
        list(archived_records(root, manifest["run_id"]))
        if not (folder / "report.json").exists() or not (folder / "products.json").exists() or not (folder / "products.csv").exists():
            export_and_report(root, folder, manifest)
        active = root / "current.json"
        if not active.exists() or json.loads(active.read_text()).get("run_id") != manifest["run_id"]:
            atomic_json(active, {"run_id": manifest["run_id"]})
        print(f"Already complete: {folder}. Use --new for a fresh snapshot.", flush=True)
        return folder
    records = await collect_archive(session, folder, manifest)
    failures = await download_archive_details(session, folder, manifest, records, concurrency)
    if failures:
        atomic_json(folder / "report.json", {"run_id": manifest["run_id"], "status": "incomplete",
                    "discovered_products": len(records), "failed_details": failures,
                    "missing_details": [pid for pid in records if manifest["details"].get(str(pid), {}).get("status") != "complete"]})
        raise ValueError(f"{len(failures)} details failed; rerun to resume. Active catalog preserved.")
    manifest.update(status="complete", completed_at=utc_now())
    atomic_json(folder / "manifest.json", manifest)
    try:
        report = export_and_report(root, folder, manifest)
    except Exception:
        manifest["status"] = "downloading"
        atomic_json(folder / "manifest.json", manifest)
        raise
    atomic_json(root / "current.json", {"run_id": manifest["run_id"]})
    print(f"Published raw snapshot: {report['valid_details']} products; report={folder / 'report.json'}", flush=True)
    return folder


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new", action="store_true", help="Start a fresh snapshot; otherwise resume the current download")
    parser.add_argument("--concurrency", type=int, default=CONCURRENCY)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    user, password = os.environ.get("EKT_API_USER"), os.environ.get("EKT_API_PASSWORD")
    if not user or not password:
        raise SystemExit("Set EKT_API_USER and EKT_API_PASSWORD in .env")
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / ".download.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another raw catalog download is running")
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=args.concurrency),
                                         headers={"Accept": "application/json",
                                                  "Authorization": aiohttp.encode_basic_auth(user, password)}) as session:
            await download_catalog(session, new=args.new, concurrency=args.concurrency)


if __name__ == "__main__":
    asyncio.run(main())
