import gzip
import json
import sys
import time

import cv2
import numpy as np
import pytest
from PIL import Image

from server import settings
from server.pages import count_pages, render_page, fit_scale, thumbnail_jpeg
from server.store import JobError


def make_pdf(path, page_sizes, resolution=72):
    """One PDF page per (w, h) pixel size; page size in pt = px * 72 / resolution."""
    images = [Image.new("RGB", size, "white") for size in page_sizes]
    images[0].save(path, save_all=True, append_images=images[1:], resolution=resolution)
    return path


def test_count_and_render_caps(tmp_path):
    # 300 px at resolution 7.2 -> a 3000 x 3000 pt page; 200 dpi render gives 8333 px.
    path = make_pdf(tmp_path / "big.pdf", [(300, 300)], resolution=7.2)

    assert count_pages(path) == 1
    bgr = render_page(path, 0)

    h, w = bgr.shape[:2]
    assert bgr.dtype == np.uint8
    assert bgr.shape[2] == 3
    assert max(w, h) <= 4000
    assert w * h <= 16_000_000


def test_fit_scale_leaves_small_pages_alone():
    assert fit_scale(1000, 800) == 1.0


def test_count_rejects_unreadable_file(tmp_path):
    path = tmp_path / "junk.bin"
    path.write_bytes(b"not a pdf or image at all")

    with pytest.raises(JobError) as exc:
        count_pages(path)
    assert exc.value.status == 400
    assert exc.value.message == "ไฟล์นี้ไม่ใช่รูปภาพหรือ PDF ที่อ่านได้"


def test_thumbnail_long_side_capped_and_jpeg(tmp_path):
    bgr = np.full((3000, 2000, 3), 255, dtype=np.uint8)

    data = thumbnail_jpeg(bgr)

    assert data[:2] == b"\xff\xd8"
    decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) == 1600


@pytest.mark.slow
def test_real_engine_parity(tmp_path):
    sys.path.insert(0, str(settings.MODEL_DIR))
    from runtime.line_engine import LineOCR

    fixture_dir = settings.BASE_DIR / "tests" / "fixtures"
    meta = json.loads((fixture_dir / "paragraph.json").read_text(encoding="utf-8"))
    h, w = meta["shape"]
    raw = gzip.decompress((fixture_dir / "paragraph.bgr.gz").read_bytes())
    bgr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)

    png = tmp_path / "paragraph.png"
    assert cv2.imwrite(str(png), bgr)
    expected = LineOCR(str(settings.MODEL_DIR), threads=settings.THREADS).read(bgr)["text"]

    from server.scheduler import Scheduler
    from server.stats import EventLog
    from server.store import JobStore

    store = JobStore(tmp_path / "store")
    events = EventLog(tmp_path / "logs")
    sched = Scheduler(store, events, lambda: LineOCR(str(settings.MODEL_DIR), threads=settings.THREADS), workers=1)
    job = store.create("u1", "paragraph.png", png.stat().st_size, now=0)
    store.put_chunk(job["id"], 0, png.read_bytes(), now=0)
    store.assemble(job["id"])
    sched.enqueue(job["id"], 1)
    sched.start()
    try:
        deadline = time.monotonic() + 300
        while store.get(job["id"])["state"] != "done" and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        sched.stop()

    assert store.read_page(job["id"], 1)["text"] == expected
