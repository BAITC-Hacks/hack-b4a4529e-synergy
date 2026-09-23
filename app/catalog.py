from __future__ import annotations

import csv
import io
import json
import math
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin

from .config import DATA_DIR, DETAILS_DIR, PAGES_DIR

SKIP_PROP_KEYS = {
    "BRAND_PRIORITY",
    "NOVINKA",
    "SPETSPREDLOZHENIE",
    "RECOMMEND",
    "IMYAKARTINKI",
    "CML2_TRAITS",
    "CML2_TAXES",
    "CML2_ARTICLE",
}

PROP_LABELS = {
    "TORGOVAYA_MARKA": "Торговая марка",
    "NOMINALNYY_TOK": "Номинальный ток",
    "NOMINALNOE_NAPRYAZHENIE": "Номинальное напряжение",
    "NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST": "Отключающая способность",
    "KOLICHESTVO_POLYUSOV": "Количество полюсов",
    "TIP_USTANOVKI": "Тип установки",
    "KRATNOST_MIN": "Кратность / мин. партия",
    "CML2_BAR_CODE": "Штрихкод",
    "ARTIKULPOSTAVSHCHIKA": "Артикул поставщика",
    "OBYEM": "Тип",
}

CSV_FIELDS = {
    "id": ("id", "product_id", "ид"),
    "name": ("name", "title", "наименование", "название"),
    "article": ("article", "артикул", "sku"),
    "price": ("price", "цена"),
    "quantity": ("quantity", "qty", "stock", "остаток", "наличие"),
    "url": ("url", "ссылка"),
    "image": ("image", "img", "фото"),
    "description": ("description", "описание"),
    "category": ("category", "категория"),
    "certificate": ("certificate", "сертификат"),
    "stores": ("stores", "склады"),
    "properties": ("properties", "свойства"),
    "unit": ("unit", "measure", "единица"),
    "min_quantity": ("min_quantity", "minimum_order_quantity"),
    "quantity_step": ("quantity_step", "purchase_multiple"),
}


def category_from_url(url: str) -> str:
    if not url:
        return ""
    parts = urlparse(url).path.strip("/").split("/")
    if "catalog" not in parts:
        return ""
    i = parts.index("catalog")
    segs = [s for s in parts[i + 1 : -1] if s]
    return " / ".join(segs)


def _skip_prop(key: str) -> bool:
    if key in SKIP_PROP_KEYS:
        return True
    return key.startswith("CML2_") and key not in PROP_LABELS


def prop_label(key: str) -> str:
    return PROP_LABELS.get(key, key.replace("_", " ").strip())


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _as_number(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    text = str(value).replace(" ", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    if number.is_integer():
        return int(number)
    return number


def _stringify_prop(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return ", ".join(parts) if parts else None
    text = str(value).strip()
    return text or None


def public_properties(properties: dict | None) -> dict[str, str]:
    out = {}
    for key, value in (properties or {}).items():
        if _skip_prop(str(key)):
            continue
        text = _stringify_prop(value)
        if text:
            out[prop_label(str(key))] = text
    return out


def safe_url(value) -> str | None:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return None
    value = value.strip()
    if value.startswith("/upload/"):
        value = urljoin("https://ekt.kz", value)
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def extract_certificate(record: dict) -> str | None:
    keys = ("certificate", "certificate_url", "sertifikat", "сертификат")
    for key in keys:
        value = record.get(key)
        if safe_url(value):
            return safe_url(value)
    for item in record.get("certificates") or []:
        value = item.get("url") if isinstance(item, dict) else item
        if safe_url(value):
            return safe_url(value)
    for key, value in (record.get("properties") or {}).items():
        blob = f"{key} {value}".lower()
        if "sertifikat" in blob or "certificate" in blob or "сертификат" in blob:
            if safe_url(value):
                return safe_url(value)
    return None


def spec_snippet(record: dict, limit: int = 280) -> str:
    description = (record.get("description") or "").replace("\r\n", "\n").strip()
    if description:
        compact = re.sub(r"\s+", " ", description)
        return compact[:limit]
    props = public_properties(record.get("properties"))
    if not props:
        return ""
    text = "; ".join(f"{k}: {v}" for k, v in list(props.items())[:6])
    return text[:limit]


def embed_text(product: dict) -> str:
    parts = [
        product.get("name") or "",
        f"артикул {product['article']}" if product.get("article") else "",
        product.get("category") or "",
        product.get("description") or "",
    ]
    for label, value in public_properties(product.get("properties")).items():
        if label in {"KOLICHESTVOVREZERVE", "Кратность / мин. партия"}:
            continue
        parts.append(f"{label}: {value}")
    return "\n".join(p for p in parts if p)


def _empty_product() -> dict:
    return {
        "id": None,
        "name": "",
        "article": "",
        "price": None,
        "quantity": None,
        "image": None,
        "url": None,
        "category": "",
        "description": "",
        "properties": {},
        "stores": [],
        "certificate": None,
        "spec_snippet": "",
    }


def parse_stores(value) -> list:
    if isinstance(value, list):
        return [{"name": item.get("name") or str(item.get("id", "")),
                 "quantity": _as_number(item.get("quantity"))} for item in value if isinstance(item, dict)]
    if not isinstance(value, str) or not value.strip():
        return []
    stores = []
    for part in value.split(" | "):
        if "=" not in part:
            continue
        name, _, qty = part.rpartition("=")
        stores.append({"name": name.strip(), "quantity": _as_number(qty)})
    return stores


def parse_properties(value) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    props: dict[str, str] = {}
    current = None
    for part in value.split(" | "):
        key, sep, rest = part.partition("=")
        looks_like_key = sep and key and (" " not in key.strip()) and len(key) < 80
        if looks_like_key:
            current = key.strip()
            props[current] = rest.strip()
        elif current:
            props[current] = f"{props[current]} | {part.strip()}"
    return props


def normalize_product(raw: dict) -> dict | None:
    product_id = _as_int(raw.get("id"))
    name = (raw.get("name") or "").strip()
    if product_id is None or not name:
        return None
    properties = parse_properties(raw.get("properties"))
    stores = parse_stores(raw.get("stores"))
    product = _empty_product()
    product.update(
        {
            "id": product_id,
            "name": name,
            "article": str(raw.get("article") or "").strip(),
            "price": _as_number(raw.get("price")),
            "quantity": _as_number(raw.get("quantity")),
            "image": safe_url(raw.get("image")),
            "url": safe_url(raw.get("url")),
            "category": (raw.get("category") or category_from_url(raw.get("url") or "")).strip(),
            "description": (raw.get("description") or "").strip(),
            "properties": properties,
            "stores": stores,
            "certificate": extract_certificate({**raw, "properties": properties}),
            "unit": str(raw.get("unit") or "ед."),
            "min_quantity": _as_number(raw.get("min_quantity")),
            "quantity_step": _as_number(raw.get("quantity_step")),
        }
    )
    product["spec_snippet"] = spec_snippet(product)
    return product


def _merge(base: dict, incoming: dict) -> dict:
    merged = dict(base)
    for key, value in incoming.items():
        if key in ("properties", "stores"):
            if value:
                merged[key] = value
            continue
        if value in (None, "", [], {}):
            continue
        merged[key] = value
    merged["spec_snippet"] = spec_snippet(merged)
    if not merged.get("certificate"):
        merged["certificate"] = extract_certificate(merged)
    if not merged.get("category"):
        merged["category"] = category_from_url(merged.get("url") or "")
    return merged


def _load_page_file(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("items") or data.get("products") or []
        if not items and data.get("id"):
            items = [data]
    else:
        items = []
    products = []
    for item in items:
        if isinstance(item, dict):
            product = normalize_product(item)
            if product:
                products.append(product)
    return products


def _load_json_products(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "items" in data:
        items = data["items"]
    elif isinstance(data, dict) and data.get("id"):
        items = [data]
    else:
        items = []
    products = []
    for item in items:
        if isinstance(item, dict):
            product = normalize_product(item)
            if product:
                products.append(product)
    return products


def _csv_value(row: dict, field: str):
    names = {name.lower(): name for name in row if isinstance(name, str)}
    for alias in CSV_FIELDS[field]:
        key = names.get(alias.lower())
        if key is not None:
            return row[key]
    return None


def _load_csv(path: Path) -> list[dict]:
    products = []
    # The downloader appends complete, flattened CSV rows. Freeze a byte prefix;
    # exclude any last row which was still being written when its size was read.
    with path.open("rb") as source:
        size = source.seek(0, 2)
        source.seek(0)
        data = source.read(size)
    if data and not data.endswith(b"\n"):
        data = data[:data.rfind(b"\n") + 1]
    with io.StringIO(data.decode("utf-8-sig"), newline="") as handle:
        header = handle.readline()
        if not header:
            return []
        header_l = header.lower()
        if "id" not in header_l and "артикул" not in header_l and "article" not in header_l:
            return []
        handle.seek(0)
        delimiter = ";" if header.count(";") > header.count(",") else ","
        reader = csv.DictReader(handle, delimiter=delimiter, strict=True)
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Incomplete catalog CSV record; retry after the download flushes")
            raw = {field: _csv_value(row, field) for field in CSV_FIELDS}
            product = normalize_product(raw)
            if product:
                products.append(product)
    return products


def load_products(data_dir: Path | None = None) -> list[dict]:
    root = data_dir or DATA_DIR
    pages_dir = root / "pages" if data_dir else PAGES_DIR
    details_dir = root / "details" if data_dir else DETAILS_DIR
    by_id: dict[int, dict] = {}

    if pages_dir.is_dir():
        for path in sorted(pages_dir.glob("*.json")):
            for product in _load_page_file(path):
                by_id.setdefault(product["id"], product)

    combined = root / "products.json"
    if combined.is_file():
        for product in _load_json_products(combined):
            if product["id"] in by_id:
                by_id[product["id"]] = _merge(by_id[product["id"]], product)
            else:
                by_id[product["id"]] = product

    for path in sorted(root.glob("*.csv")):
        for product in _load_csv(path):
            if product["id"] in by_id:
                by_id[product["id"]] = _merge(by_id[product["id"]], product)
            else:
                by_id[product["id"]] = product

    if details_dir.is_dir():
        for path in details_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            if raw.get("id") is None:
                raw["id"] = path.stem
            detail = normalize_product(raw)
            if not detail:
                continue
            if detail["id"] in by_id:
                by_id[detail["id"]] = _merge(by_id[detail["id"]], detail)
            else:
                by_id[detail["id"]] = detail

    return [by_id[key] for key in sorted(by_id)]
