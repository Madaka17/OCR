import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SITE_DIR = BASE_DIR / "site"
MODEL_DIR = BASE_DIR / "OpenThai2.0-OCR-Text"
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
