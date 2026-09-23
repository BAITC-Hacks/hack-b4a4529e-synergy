#!/usr/bin/env python3
"""Download the ekt.kz catalog into CSV, resuming by catalog page."""

import asyncio
import csv
import fcntl
import json
import os
import time
import uuid
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent
BASE = "https://ekt.kz/api/products"
OUT = ROOT / "data" / "ekt"
CSV_PATH = OUT / "products.csv"
LIST_PATH = OUT / "list_checkpoint.txt"
CONCURRENCY = int(os.environ.get("EKT_CONCURRENCY", "64"))
LIST_PAGE_SIZE = 500
LIST_CONCURRENCY = min(CONCURRENCY, 4)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=25, sock_connect=10, sock_read=20)
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


def item_ids(page: dict) -> list[int]:
    return [item["id"] for item in page.get("items") or []]


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


def spreadsheet_row(row: dict) -> dict:
    return {
        key: " ".join(value.split()) if isinstance(value, str) else value
        for key, value in row.items()
    }


def ensure_spreadsheet_csv() -> None:
    if not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0:
        return
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as source:
        header = source.readline().rstrip("\r\n")
        if header == ";".join(COLUMNS):
            return
        if header != ",".join(COLUMNS):
            raise ValueError(f"Unexpected CSV header in {CSV_PATH}")
        source.seek(0)
        temp_path = CSV_PATH.with_suffix(".csv.tmp")
        with temp_path.open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=COLUMNS, delimiter=";")
            writer.writeheader()
            writer.writerows(spreadsheet_row(row) for row in csv.DictReader(source))
        os.replace(temp_path, CSV_PATH)


def load_saved_ids() -> set[int]:
    if not CSV_PATH.exists():
        return set()
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as handle:
        return {int(row["id"]) for row in csv.DictReader(handle, delimiter=";")}


def load_list_pages() -> dict[int, list[int]]:
    pages: dict[int, list[int]] = {}
    if not LIST_PATH.exists():
        return pages
    for line in LIST_PATH.read_text().splitlines():
        if "\t" not in line:
            continue
        page_s, ids_s = line.split("\t", 1)
        if not page_s.startswith(f"{LIST_PAGE_SIZE}:"):
            continue
        try:
            number = int(page_s.split(":", 1)[1])
        except ValueError:
            continue
        ids = [int(part) for part in ids_s.split(",") if part]
        if ids:
            pages[number] = ids
    return pages


def catalog_end(pages: dict[int, list[int]], signature: list[int], per_page: int) -> int | None:
    for number in sorted(pages):
        ids = pages[number]
        if number != 1 and ids == signature:
            continue
        if len(ids) < per_page:
            return number
    return None


async def get_json(
    session: aiohttp.ClientSession,
    url: str,
    sem: asyncio.Semaphore,
    timeout: aiohttp.ClientTimeout = REQUEST_TIMEOUT,
) -> dict:
    delay = 0.25
    last_error: Exception | str | None = None
    for _ in range(6):
        try:
            async with sem:
                async with session.get(url, timeout=timeout) as resp:
                    body = await resp.read()
            if resp.status in (429, 500, 502, 503, 504):
                last_error = f"HTTP {resp.status} {url}"
            elif resp.status != 200:
                raise RuntimeError(f"HTTP {resp.status} {url} {body[:180]!r}")
            else:
                return json.loads(body)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        await asyncio.sleep(delay)
        delay = min(delay * 2, 4)
    raise RuntimeError(f"{url}: {last_error}")


class Writer:
    def __init__(self, saved_ids: set[int]) -> None:
        self.saved_ids = saved_ids
        self.saved = len(saved_ids)
        self.failed = 0
        new_file = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
        self.csv_handle = CSV_PATH.open("a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.csv_handle, fieldnames=COLUMNS, delimiter=";")
        if new_file:
            self.csv_handle.write("\ufeff")
            self.writer.writeheader()
            self.csv_handle.flush()

    def close(self) -> None:
        self.csv_handle.close()

    def commit_page(self, number: int, rows: list[dict]) -> None:
        new_rows = []
        seen = set(self.saved_ids)
        for row in rows:
            product_id = int(row["id"])
            if product_id not in seen:
                new_rows.append(row)
                seen.add(product_id)
        self.writer.writerows(spreadsheet_row(row) for row in new_rows)
        self.csv_handle.flush()
        os.fsync(self.csv_handle.fileno())
        self.saved_ids.update(int(row["id"]) for row in new_rows)
        self.saved += len(new_rows)
        print(f"page={number} saved={self.saved} new={len(new_rows)}", flush=True)


async def collect_pages(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    pages: dict[int, list[int]],
    list_handle,
    queue: asyncio.Queue,
    scheduled: set[int],
    saved_ids: set[int],
) -> None:
    lock = asyncio.Lock()

    def remember(number: int, ids: list[int]) -> None:
        if number in pages:
            return
        list_handle.write(f"{LIST_PAGE_SIZE}:{number}\t{','.join(str(item_id) for item_id in ids)}\n")
        list_handle.flush()
        os.fsync(list_handle.fileno())
        pages[number] = ids
        schedule(number, ids)

    def schedule(number: int, ids: list[int]) -> None:
        if number in scheduled:
            return
        scheduled.add(number)
        if not all(item_id in saved_ids for item_id in ids):
            queue.put_nowait((number, ids))

    async def fetch_list(number: int) -> list[int]:
        data = await get_json(
            session, f"{BASE}?page={number}&per_page={LIST_PAGE_SIZE}", sem, LIST_TIMEOUT
        )
        if data.get("per_page") != LIST_PAGE_SIZE:
            raise RuntimeError(f"Unexpected per_page on list page {number}: {data.get('per_page')}")
        return item_ids(data)

    if 1 not in pages:
        remember(1, await fetch_list(1))
    signature = pages[1]
    per_page = LIST_PAGE_SIZE
    end = catalog_end(pages, signature, per_page)

    async def fetch_page(number: int) -> str:
        if number in pages:
            ids = pages[number]
            if number != 1 and ids == signature:
                return "end"
            if len(ids) < per_page:
                return "end"
            return "ok"
        ids = await fetch_list(number)
        async with lock:
            if number != 1 and (not ids or ids == signature):
                return "end"
            remember(number, ids)
            if len(ids) < per_page:
                return "end"
        return "ok"

    if end is not None:
        missing = [number for number in range(1, end + 1) if number not in pages]
        if missing:
            await asyncio.gather(*(fetch_page(number) for number in missing))
        for number in range(1, end + 1):
            ids = pages.get(number) or []
            if ids:
                schedule(number, ids)
        print(f"list pages={end} queued_pages={queue.qsize()}", flush=True)
        return

    # Cached pages may have been listed before their details were saved.
    for number in sorted(pages):
        if pages[number] == signature and number != 1:
            continue
        schedule(number, pages[number])

    # Retry holes left by interrupted concurrent page requests.
    next_number = 2
    stop = False

    def fill(page_queue: asyncio.Queue) -> None:
        nonlocal next_number
        while not stop and page_queue.qsize() < LIST_CONCURRENCY * 2:
            page_queue.put_nowait(next_number)
            next_number += 1

    page_queue: asyncio.Queue = asyncio.Queue()
    fill(page_queue)

    async def worker() -> None:
        nonlocal stop
        while True:
            try:
                number = page_queue.get_nowait()
            except asyncio.QueueEmpty:
                if stop:
                    return
                await asyncio.sleep(0.02)
                continue
            status = await fetch_page(number)
            async with lock:
                if status == "end":
                    stop = True
                elif not stop:
                    fill(page_queue)
                if len(pages) % 100 == 0:
                    print(f"list pages kept={len(pages)} latest={number}", flush=True)

    await asyncio.gather(*(worker() for _ in range(LIST_CONCURRENCY)))
    end = catalog_end(pages, signature, per_page)
    print(f"list pages={end or len(pages)} queued_pages={queue.qsize()}", flush=True)


async def download_details(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    queue: asyncio.Queue,
    writer: Writer,
) -> None:
    lock = asyncio.Lock()

    async def fetch_row(product_id: int) -> dict:
        detail = await get_json(session, f"{BASE}/detail?id={product_id}", sem)
        save_detail(detail)
        return row_from_detail(detail)

    async def worker() -> None:
        while True:
            page = await queue.get()
            if page is None:
                return
            number, ids = page
            missing = list(dict.fromkeys(product_id for product_id in ids if product_id not in writer.saved_ids))
            rows = await asyncio.gather(
                *(fetch_row(product_id) for product_id in missing),
                return_exceptions=True,
            )
            errors = [(product_id, row) for product_id, row in zip(missing, rows) if isinstance(row, Exception)]
            if errors:
                writer.failed += len(errors)
                for product_id, exc in errors:
                    print(f"detail failed page={number} id={product_id} {exc}", flush=True)
            successful_rows = [row for row in rows if not isinstance(row, Exception)]
            if successful_rows:
                async with lock:
                    writer.commit_page(number, successful_rows)

    workers = [asyncio.create_task(worker()) for _ in range(CONCURRENCY)]
    await asyncio.gather(*workers)


async def main() -> None:
    load_dotenv(ROOT / ".env")
    user = os.environ.get("EKT_API_USER")
    password = os.environ.get("EKT_API_PASSWORD")
    if not user or not password:
        raise SystemExit("Set EKT_API_USER and EKT_API_PASSWORD in .env")

    OUT.mkdir(parents=True, exist_ok=True)
    with LIST_PATH.open("a", encoding="utf-8") as list_handle:
        try:
            fcntl.flock(list_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another download_ekt.py process is already running")

        ensure_spreadsheet_csv()
        saved_ids = load_saved_ids()
        pages = load_list_pages()
        print(f"resume saved={len(saved_ids)} list_pages={len(pages)} concurrency={CONCURRENCY}", flush=True)

        started = time.perf_counter()
        sem = asyncio.Semaphore(CONCURRENCY)
        queue: asyncio.Queue = asyncio.Queue()
        scheduled: set[int] = set()
        connector = aiohttp.TCPConnector(limit=CONCURRENCY, limit_per_host=CONCURRENCY, ttl_dns_cache=300)
        writer = Writer(saved_ids)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "Accept": "application/json",
                    "Authorization": aiohttp.encode_basic_auth(user, password),
                },
            ) as session:
                await collect_pages(session, sem, pages, list_handle, queue, scheduled, saved_ids)
                for _ in range(CONCURRENCY):
                    queue.put_nowait(None)
                await download_details(session, sem, queue, writer)
        finally:
            writer.close()

        elapsed = time.perf_counter() - started
        print(
            f"csv={CSV_PATH} saved={writer.saved} failed={writer.failed} seconds={elapsed:.1f}",
            flush=True,
        )
        if writer.failed:
            raise SystemExit(f"{writer.failed} details failed; re-run to retry them")


if __name__ == "__main__":
    asyncio.run(main())
