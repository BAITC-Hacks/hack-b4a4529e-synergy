from __future__ import annotations

import base64
import json
import mimetypes

from openai import OpenAI

from .cart import add_to_cart, propose_add_to_cart, user_confirms_add
from .config import CHAT_MODEL, MAX_TOOL_ROUNDS, openai_api_key
from .search import get_index, get_product, search_products

SYSTEM = """Вы консультант интернет-магазина Электрокомплект (ekt.kz). Отвечайте по-русски.

Правила:
- Данные о товарах, цене, наличии, характеристиках и сертификатах берите только из инструментов. Не выдумывайте цифры.
- Для поиска по названию, артикулу, фото или спецификации вызывайте search_products. Для полной карточки — get_product.
- Если availability = unknown, скажите, что в снимке каталога нет остатка, и не предлагайте добавить в корзину.
- Если товара нет (quantity 0), предложите аналоги из ответа search_products и кратко объясните analog_reason.
- В корзину: сначала уточните количество, если клиент его не назвал. Затем propose_add_to_cart. Не вызывайте add_to_cart, пока клиент явно не подтвердит (кнопка «Добавить в корзину» или фраза вроде «да, добавь»).
- После успешного добавления дайте ссылку /cart.
- Не принимайте и не храните платёжные данные. Вопросы об оплате и доставке: в этом прототипе условий покупки нет — скажите об этом прямо.
"""

TOOLS = [
    {
        "type": "function",
        "name": "search_products",
        "description": "Семантический поиск по снимку каталога ekt.kz. Возвращает цену, остаток, характеристики и аналоги при нулевом остатке.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Запрос клиента: название, артикул, id или описание с фото",
                },
                "limit": {
                    "type": "integer",
                    "description": "Сколько позиций вернуть, от 1 до 10",
                },
            },
            "required": ["query", "limit"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_product",
        "description": "Полная карточка товара из снимка по id: описание, свойства, склады, сертификат.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "id товара из поиска"},
            },
            "required": ["product_id"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "propose_add_to_cart",
        "description": "Показать клиенту, что будет добавлено. Корзину не меняет. Вызывать только когда известны id и количество.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "quantity": {"type": "integer", "description": "Сколько штук добавить"},
            },
            "required": ["product_id", "quantity"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "add_to_cart",
        "description": "Добавить в корзину только после явного подтверждения и после propose_add_to_cart с теми же id и количеством.",
        "parameters": {
            "type": "object",
            "properties": {
                "product_id": {"type": "integer"},
                "quantity": {"type": "integer"},
            },
            "required": ["product_id", "quantity"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]

IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}


def _client() -> OpenAI:
    key = openai_api_key()
    if not key:
        raise RuntimeError("Нет OPENAI_API_KEY")
    return OpenAI(api_key=key, timeout=120.0)


def _dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _data_url(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def attachments_to_parts(files: list[dict]) -> list[dict]:
    parts = []
    for item in files:
        name = item["filename"]
        mime = item.get("mime") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        data = item["data"]
        suffix = name.lower()[name.lower().rfind(".") :] if "." in name else ""
        if mime in IMAGE_MIMES or suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            if mime not in IMAGE_MIMES:
                mime = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                    ".webp": "image/webp",
                    ".gif": "image/gif",
                }.get(suffix, "image/jpeg")
            parts.append({"type": "input_image", "image_url": _data_url(mime, data)})
        elif suffix in DOC_EXTS or mime in {
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "text/csv",
        }:
            parts.append(
                {
                    "type": "input_file",
                    "filename": name,
                    "file_data": _data_url(mime, data),
                }
            )
        else:
            parts.append(
                {
                    "type": "input_text",
                    "text": f"[файл {name} не поддерживается: нужен jpeg/png/webp/gif, pdf, word, excel или csv]",
                }
            )
    return parts


def build_user_input(message: str, files: list[dict] | None) -> dict | str:
    text = (message or "").strip() or "Смотрите вложение."
    parts = attachments_to_parts(files or [])
    if not parts:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [{"type": "input_text", "text": text}, *parts],
    }


def _output_text(response) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text.strip()
    chunks = []
    for item in response.output:
        if getattr(item, "type", None) != "message":
            continue
        for part in item.content:
            part_type = getattr(part, "type", None)
            if part_type in ("output_text", "text"):
                chunks.append(part.text)
    return "\n".join(chunks).strip()


def _function_calls(response) -> list:
    return [item for item in response.output if getattr(item, "type", None) == "function_call"]


def _create_response(client: OpenAI, kwargs: dict):
    try:
        return client.responses.create(**kwargs)
    except Exception as exc:
        if "reasoning" in kwargs and "reasoning" in str(exc).lower():
            fallback = dict(kwargs)
            fallback.pop("reasoning", None)
            return client.responses.create(**fallback)
        raise


def execute_tool(session, name: str, arguments: dict, *, confirmed: bool) -> str:
    try:
        index = get_index()
        if name == "search_products":
            result = search_products(
                arguments.get("query") or "",
                arguments.get("limit") or 5,
                index=index,
            )
            session.last_search = result.get("results") or []
            return _dumps(result)
        if name == "get_product":
            return _dumps(get_product(int(arguments["product_id"]), index=index))
        if name == "propose_add_to_cart":
            product = index.get(int(arguments["product_id"]))
            result = propose_add_to_cart(session, product, arguments.get("quantity"))
            return _dumps(result)
        if name == "add_to_cart":
            product = index.get(int(arguments["product_id"]))
            result = add_to_cart(
                session,
                product,
                arguments.get("quantity"),
                confirmed=confirmed,
            )
            return _dumps(result)
        return _dumps({"error": f"неизвестный инструмент {name}"})
    except Exception as exc:
        return _dumps({"error": str(exc)})


def run_turn(session, message: str, files: list[dict] | None = None) -> dict:
    client = _client()
    confirmed = user_confirms_add(message, session.pending is not None)
    user_item = build_user_input(message, files)
    input_items: list = [user_item]
    previous = session.last_response_id
    text = ""

    for _ in range(MAX_TOOL_ROUNDS):
        kwargs = {
            "model": CHAT_MODEL,
            "instructions": SYSTEM,
            "tools": TOOLS,
            "input": input_items,
            "reasoning": {"effort": "low"},
        }
        if previous:
            kwargs["previous_response_id"] = previous
        response = _create_response(client, kwargs)
        previous = response.id
        session.last_response_id = response.id
        calls = _function_calls(response)
        if not calls:
            text = _output_text(response)
            break
        input_items = []
        for call in calls:
            try:
                args = json.loads(call.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            output = execute_tool(session, call.name, args, confirmed=confirmed)
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": output,
                }
            )
    else:
        text = "Слишком много шагов поиска. Сформулируйте запрос короче."

    from .sessions import state_payload

    payload = state_payload(session)
    payload["text"] = text or "Готово."
    payload["products"] = session.last_search
    return payload
