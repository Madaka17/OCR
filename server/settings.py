import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    """Read KEY=VALUE lines into os.environ. Real environment variables win.

    The file is OCR_ENV_FILE when that is set (empty string disables loading),
    otherwise BASE_DIR/.env. A missing file is fine; an unreadable one warns once.
    """
    raw = os.environ.get("OCR_ENV_FILE")
    if raw == "":
        return
    path = Path(raw) if raw is not None else BASE_DIR / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    except OSError as exc:
        print(f"warning: cannot read env file {path}: {exc}", file=sys.stderr)
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


_load_env_file()
SITE_DIR = BASE_DIR / "site"
MODEL_DIR = BASE_DIR / "OpenThai2.1-OCR-Text"
DATA_DIR = Path(os.environ.get("OCR_DATA", BASE_DIR / "data"))

MAX_JOB_BYTES = 500 * 1024 * 1024
MAX_JOB_PAGES = 1000
CHUNK_BYTES = 64 * 1024 * 1024
RETENTION_SECONDS = 7 * 86400
STALE_UPLOAD_SECONDS = 86400

WORKERS = int(os.environ.get("OCR_WORKERS", "2"))
THREADS = int(os.environ.get("OCR_THREADS", "3"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "7860"))
LLM_URL = os.environ.get("OCR_LLM_URL", "http://localhost:1235/v1").rstrip("/")
LLM_MODEL = os.environ.get("OCR_LLM_MODEL", "unsloth/qwen3.5-4b")
LLM_API_KEY = os.environ.get("OCR_LLM_API_KEY", "")
LLM_REASONING_EFFORT = os.environ.get("OCR_LLM_REASONING_EFFORT", "none")
