"""Validate ephemeral uploads before forwarding them to the model."""
import csv
import io
from pathlib import Path
import zipfile

import olefile
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from defusedxml import ElementTree
import openpyxl
import xlrd

MIMES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
    ".pdf": "application/pdf", ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".csv": "text/csv",
}


class AttachmentError(ValueError):
    pass


def validate_attachment(filename, mime, data):
    suffix = Path(filename).suffix.lower()
    expected = MIMES.get(suffix)
    if not expected:
        raise AttachmentError("Этот формат файла не поддерживается.")
    allowed = {expected, "application/octet-stream", ""}
    if suffix == ".csv":
        allowed |= {"application/csv", "application/vnd.ms-excel", "text/plain"}
    if (mime or "").split(";")[0].lower() not in allowed:
        raise AttachmentError("Тип файла не соответствует расширению. Экспортируйте файл заново.")
    warnings = []
    if not data:
        raise AttachmentError("Файл пустой.")
    try:
        stream = io.BytesIO(data)
        if expected.startswith("image/"):
            with Image.open(stream) as image:
                if Image.MIME.get(image.format) != expected:
                    raise AttachmentError("Изображение не соответствует расширению файла.")
                if image.width * image.height > 25_000_000:
                    raise AttachmentError("Уменьшите изображение до 25 мегапикселей.")
                image.verify()
        elif suffix == ".pdf":
            if not data.startswith(b"%PDF-"):
                raise AttachmentError("Файл не является PDF.")
            pdf = PdfReader(stream)
            if pdf.is_encrypted:
                raise AttachmentError("PDF защищён паролем. Пришлите незашифрованную копию.")
            if not 1 <= len(pdf.pages) <= 100:
                raise AttachmentError("Пришлите PDF от 1 до 100 страниц; большой документ разделите.")
        elif suffix in {".docx", ".xlsx"}:
            with zipfile.ZipFile(stream) as archive:
                entries = archive.infolist()
                if any(i.flag_bits & 1 for i in entries):
                    raise AttachmentError("Пришлите файл без защиты паролем.")
                if sum(i.file_size for i in entries) > 50 * 1024 * 1024:
                    raise AttachmentError("Документ слишком большой после распаковки. Разделите его.")
                required = "word/document.xml" if suffix == ".docx" else "xl/workbook.xml"
                ElementTree.fromstring(archive.read(required))
                if any("vbaProject" in i.filename for i in entries):
                    raise AttachmentError("Экспортируйте документ без макросов.")
                if any(i.filename.startswith(("word/media/", "xl/media/")) for i in entries):
                    warnings.append("Встроенные изображения Office не читаются: для них приложите PDF или отдельное фото.")
            if suffix == ".xlsx":
                book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
                try:
                    for sheet in book:
                        sheet.reset_dimensions()
                        for count, _row in enumerate(sheet.iter_rows(), 1):
                            if count > 1000:
                                raise AttachmentError("В листе Excel больше 1000 строк. Разделите таблицу и отправьте части.")
                finally:
                    book.close()
        elif suffix in {".doc", ".xls"}:
            with olefile.OleFileIO(stream) as compound:
                if compound.exists("EncryptedPackage") or compound.exists("EncryptionInfo"):
                    raise AttachmentError("Пришлите документ без защиты паролем.")
                if suffix == ".doc":
                    if not compound.exists("WordDocument"):
                        raise AttachmentError("Файл не является документом Word.")
                    header = compound.openstream("WordDocument").read(12)
                    if len(header) < 12 or int.from_bytes(header[10:12], "little") & 0x8100:
                        raise AttachmentError("Документ Word повреждён или защищён паролем.")
                    warnings.append("Встроенные изображения Word требуют PDF или отдельного фото.")
            if suffix == ".xls":
                book = xlrd.open_workbook(file_contents=data, on_demand=True)
                try:
                    if any(sheet.nrows > 1000 for sheet in book.sheets()):
                        raise AttachmentError("В листе Excel больше 1000 строк. Разделите таблицу.")
                finally:
                    book.release_resources()
        else:
            text = data.decode("utf-8-sig")
            if "\x00" in text:
                raise AttachmentError("CSV должен содержать текст UTF-8.")
            try:
                dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            for count, _row in enumerate(csv.reader(io.StringIO(text), dialect=dialect, strict=True), 1):
                if count > 1000:
                    raise AttachmentError("В CSV больше 1000 строк. Разделите таблицу.")
    except AttachmentError:
        raise
    except Exception as exc:
        # Upstream parsers can include document text in exception messages.
        raise AttachmentError("Файл повреждён, зашифрован или не соответствует формату. Экспортируйте его заново.") from exc
    return {"filename": filename, "mime": expected, "data": data, "warnings": warnings}
