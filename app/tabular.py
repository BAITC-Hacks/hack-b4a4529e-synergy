"""Read every nonempty spreadsheet row locally, preserving provenance and units."""
import csv
import hashlib
import io
from pathlib import Path

import openpyxl
import xlrd

from .cart import number
from .specifications import review_items

HEADERS = {
    "article": {"article", "артикул", "код", "sku", "код товара"},
    "description": {"name", "description", "наименование", "название", "товар", "описание", "наименование товара"},
    "quantity": {"quantity", "qty", "количество", "кол-во", "кол во", "кол."},
    "unit": {"unit", "ед", "ед.", "ед. изм.", "единица", "единица измерения", "единица изм.", "ед.изм."},
}


def sheets(file):
    suffix = Path(file["filename"]).suffix.lower()
    if suffix == ".csv":
        text = file["data"].decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        yield "CSV", list(csv.reader(io.StringIO(text), dialect))
    elif suffix == ".xlsx":
        book = openpyxl.load_workbook(io.BytesIO(file["data"]), read_only=True, data_only=True)
        try:
            for sheet in book:
                sheet.reset_dimensions()
                yield sheet.title, list(sheet.values)
        finally:
            book.close()
    elif suffix == ".xls":
        book = xlrd.open_workbook(file_contents=file["data"], on_demand=True)
        try:
            for sheet in book.sheets():
                yield sheet.name, [sheet.row_values(i) for i in range(sheet.nrows)]
        finally:
            book.release_resources()


def extract_rows(file):
    rows = []
    document_id = hashlib.sha256(file["data"]).hexdigest()
    for sheet, values in sheets(file):
        columns = {}
        for line, cells in enumerate(values, 1):
            cells = [str(int(v)) if isinstance(v, float) and v.is_integer() else str(v or "").strip() for v in cells]
            if not any(cells):
                continue
            found = {key: i for i, cell in enumerate(cells) for key, aliases in HEADERS.items() if cell.casefold() in aliases}
            if not columns and ("article" in found or "description" in found) and "quantity" in found:
                columns = found
                continue
            def cell(key):
                idx = columns.get(key)
                return cells[idx] if idx is not None and idx < len(cells) else ""
            query = cell("article") or cell("description")
            quantity = None
            try:
                value = number(cell("quantity"))
                if value > 0:
                    quantity = str(value)
            except ValueError:
                pass
            rows.append({"document_id": document_id, "filename": file["filename"],
                         "source_reference": f"{sheet} · строка {line}", "query": query,
                         "query_type": "article" if cell("article") else "description", "quantity": quantity,
                         "source_unit": cell("unit"), "source_text": " | ".join(cells)})
    return rows


def process_tables(session, files, index):
    for file in files:
        rows = extract_rows(file)
        if len(rows) + len(session.attachment_review) > 20000:
            raise ValueError("В одном подборе поддерживается до 20 000 строк.")
        for start in range(0, len(rows), 100):
            if session.cancel_event.is_set():
                raise RuntimeError("response cancelled")
            review_items(session, rows[start:start + 100], index, {file["filename"]}, local_only=True)
        document_id = hashlib.sha256(file["data"]).hexdigest()
        actual = sum(row.get("document_id") == document_id for row in session.attachment_review)
        if actual != len(rows):
            raise ValueError("Не удалось сверить строки документа. Повторите загрузку.")
        summary = {"document_id": document_id, "filename": file["filename"], "source_rows": len(rows),
                   "reviewed_rows": actual, "reconciled": True}
        session.document_summary = [x for x in session.document_summary if x["document_id"] != document_id] + [summary]
