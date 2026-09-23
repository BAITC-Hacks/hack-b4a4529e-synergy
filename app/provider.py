"""Single place for creating the external model client."""

from openai import OpenAI

from .config import openai_api_key


def client(*, timeout: float, max_retries: int) -> OpenAI:
    key = openai_api_key()
    if not key:
        raise RuntimeError("Нет OPENAI_API_KEY")
    return OpenAI(api_key=key, timeout=timeout, max_retries=max_retries)
