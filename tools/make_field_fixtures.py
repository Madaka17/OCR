"""Build the blank-memo field fixtures for the blank-field detector.

Run:  .venv\\Scripts\\python.exe tools\\make_field_fixtures.py C:/Users/user/Downloads/images.jpg

Writes into tests/fixtures/:
  memo_fields.bgr.gz, memo_fields.json                  blank memo, 11 empty runs
  memo_fields_filled.bgr.gz, memo_fields_filled.json    same page, 3 values typed in
  memo_fields_filled.png                                filled page for the manual UI check
"""
import gzip
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "fixtures"
FONT = "C:/Windows/Fonts/tahoma.ttf"
FONT_SIZE = 11
INK = (20, 20, 20)

sys.stdout.reconfigure(encoding="utf-8")

# Prototype runs on the sample memo, (y, x0, x1), in reading order.
RUNS = [
    (90, 105, 216),
    (108, 36, 112),
    (108, 286, 390),
    (127, 145, 184),
    (193, 127, 164),
    (212, 129, 201),
    (231, 91, 175),
    (231, 339, 418),
    (316, 109, 155),
    (316, 191, 291),
    (566, 247, 369),
]

# Label text expected to sit left of the run, keyed by (y, x0).
LABELS = {
    (90, 105): "กอง",
    (108, 286): "วันที่",
    (316, 109): "ชื่อ",
    (316, 191): "ตำแหน่ง",
    (566, 247): "ลงชื่อ",
}

# Text drawn onto the filled copy: (top-left xy, text).
VALUES = [
    ((112, 303), "สมชาย ใจดี"),
    ((110, 77), "กองช่าง"),
    ((300, 95), "18 พ.ย. 2569"),
]

# Runs that receive a value in the filled copy, keyed by (y, x0), with the
# substring the OCR of that value must contain.
FILLED = {
    (316, 109): "สมชาย",
    (90, 105): "กองช่าง",
    (108, 286): "2569",
}


def write_gz(path, bgr):
    raw = np.ascontiguousarray(bgr, dtype=np.uint8).tobytes()
    path.write_bytes(gzip.compress(raw))


def write_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


def field_list(filled):
    fields = []
    for y, x0, x1 in RUNS:
        key = (y, x0)
        entry = {"y": y, "x0": x0, "x1": x1, "filled": key in filled}
        if key in LABELS:
            entry["label_has"] = LABELS[key]
        if key in filled:
            entry["value_has"] = filled[key]
        fields.append(entry)
    return fields


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: make_field_fixtures.py SRC_JPG")
    src = Path(sys.argv[1])
    bgr = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if bgr is None:
        raise SystemExit(f"cannot read {src}")
    h, w = bgr.shape[:2]
    OUT.mkdir(parents=True, exist_ok=True)

    write_gz(OUT / "memo_fields.bgr.gz", bgr)
    write_json(OUT / "memo_fields.json", {"shape": [h, w], "fields": field_list({})})

    rgb = Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))
    draw = ImageDraw.Draw(rgb)
    font = ImageFont.truetype(FONT, FONT_SIZE)
    for xy, text in VALUES:
        draw.text(xy, text, font=font, fill=INK)

    filled_rgb = np.asarray(rgb)
    write_gz(OUT / "memo_fields_filled.bgr.gz", filled_rgb[:, :, ::-1])
    rgb.save(OUT / "memo_fields_filled.png")
    write_json(
        OUT / "memo_fields_filled.json",
        {"shape": [h, w], "fields": field_list(FILLED)},
    )

    print(f"shape H={h} W={w}")
    for name in ("memo_fields.bgr.gz", "memo_fields.json",
                 "memo_fields_filled.bgr.gz", "memo_fields_filled.json",
                 "memo_fields_filled.png"):
        print(f"wrote {OUT / name}")


if __name__ == "__main__":
    main()
