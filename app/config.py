import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data" / "ekt"
PAGES_DIR = DATA_DIR / "pages"
DETAILS_DIR = DATA_DIR / "details"
INDEX_DIR = ROOT / "data" / "index"
STATIC_DIR = ROOT / "static"

CHAT_MODEL = os.environ.get("CHAT_MODEL", "gpt-5.6-luna")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-3-small")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TOOL_ROUNDS = 5
PROPOSAL_TTL = 10 * 60
SESSION_TTL = 2 * 60 * 60
MAX_SESSIONS = 1000
MAX_FILES = 5
MAX_TOTAL_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_MESSAGE_CHARS = 8000
MAX_HISTORY_MESSAGES = 20
SECURE_COOKIES = os.environ.get("SECURE_COOKIES", "0") == "1"
MODEL_TIMEOUT = 25.0


def openai_api_key() -> str:
    return (os.environ.get("OPENAI_API_KEY") or os.environ.get("API_KEY") or "").strip()
