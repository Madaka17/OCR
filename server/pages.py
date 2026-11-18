import math
import threading
from pathlib import Path

import cv2
import numpy as np
import pypdfium2 as pdfium

from server.store import JobError

# pdfium is not thread-safe: every pypdfium2 call goes through this lock.
PDFIUM_LOCK = threading.Lock()

RENDER_DPI = 200
MAX_LONG_SIDE = 4000
MAX_AREA = 16_000_000
THUMB_LONG_SIDE = 1600
UNREADABLE = "ไฟล์นี้ไม่ใช่รูปภาพหรือ PDF ที่อ่านได้"


def _is_pdf(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) == b"%PDF"


def count_pages(path: Path) -> int:
    try:
        if _is_pdf(path):
            with PDFIUM_LOCK:
                doc = pdfium.PdfDocument(str(path))
                try:
                    count = len(doc)
                finally:
                    doc.close()
            if count < 1:
                raise JobError(400, UNREADABLE)
            return count
        if cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR) is None:
            raise JobError(400, UNREADABLE)
        return 1
    except JobError:
        raise
    except Exception as e:
        raise JobError(400, UNREADABLE) from e


def fit_scale(w: int, h: int) -> float:
    scale = 1.0
    long_side = max(w, h)
    if long_side > MAX_LONG_SIDE:
        scale = min(scale, MAX_LONG_SIDE / long_side)
    if w * h > MAX_AREA:
        scale = min(scale, math.sqrt(MAX_AREA / (w * h)))
    return scale


def _shrink(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    scale = fit_scale(w, h)
    if scale >= 1.0:
        return bgr
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)


def render_page(path: Path, index: int) -> np.ndarray:
    if _is_pdf(path):
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(str(path))
            try:
                page = doc[index]
                try:
                    bitmap = page.render(scale=RENDER_DPI / 72)
                    rgb = np.asarray(bitmap.to_pil().convert("RGB"))
                finally:
                    page.close()
            finally:
                doc.close()
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    else:
        bgr = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    return _shrink(np.ascontiguousarray(bgr))


def thumbnail_jpeg(bgr: np.ndarray) -> bytes:
    h, w = bgr.shape[:2]
    scale = min(1.0, THUMB_LONG_SIDE / max(w, h))
    if scale < 1.0:
        bgr = cv2.resize(bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("jpeg encode failed")
    return buf.tobytes()
