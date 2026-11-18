"""Generate golden fixtures for the browser port.

Run:  .venv\\Scripts\\python.exe -I tools\\make_golden.py

Renders synthetic Thai/English pages with Pillow (Tahoma), runs the Python
LineOCR on each, and writes:
  tests/fixtures/<name>.bgr.gz   gzipped raw BGR bytes, h*w*3
  tests/fixtures/<name>.json     detect / split_counts / page / read
  tests/fixtures/text_cases.json normalisation cases
"""
import gzip
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MODEL_PARENT = ROOT / "OpenThai2.0-OCR-Text"
OUT = ROOT / "tests" / "fixtures"
FONT = r"C:\Windows\Fonts\tahoma.ttf"

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(MODEL_PARENT))
from runtime.line_engine import LineOCR, split_one, _fragments, fix_scripts, invalid_share, _enum  # noqa: E402
from runtime.thai_norm import canonical  # noqa: E402

THAI_DIGITS = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")

PARAGRAPH = [
    "ระบบอ่านข้อความภาษาไทยจากภาพถ่ายเอกสารราชการ",
    "ในปี ๒๕๖๖ หน่วยงานได้ออกประกาศฉบับที่ ๑๒๓ เพื่อใช้จริง",
    "The engine reads each line and keeps word positions.",
    "การตัดคำและการเรียงลำดับบรรทัดทำงานร่วมกันอย่างถูกต้อง",
    "ผู้ใช้สามารถวางภาพหรือไฟล์ PDF เพื่อเริ่มการอ่านได้ทันที",
    "ราคารวมภาษีมูลค่าเพิ่ม ๗ เปอร์เซ็นต์ เป็นจำนวน ๕,๒๐๐ บาท",
    "Model version 2.0 runs in the browser with ONNX Runtime Web.",
    "ตัวอักษรที่ซ้อนกันด้วยวรรณยุกต์และสระบนล่างต้องอ่านได้ครบถ้วน",
    "โรงเรียนเปิดภาคเรียนที่สองในวันที่ ๑๕ มกราคม ทุกคนต้องมาให้ตรงเวลา",
    "Thank you for reading this sample page of Thai prose.",
]

COLUMN_LEFT = [
    "ประวัติของหน่วยงานเริ่มต้นเมื่อหลายสิบปีก่อน",
    "การดำเนินงานครั้งนี้มีผู้เข้าร่วมกว่าสามร้อยคน",
    "ผลการประเมินแสดงว่าโครงการบรรลุเป้าหมาย",
    "งบประมาณถูกใช้ไปตามแผนที่กำหนดไว้ทุกไตรมาส",
    "คณะทำงานได้จัดประชุมทุกเดือนเพื่อติดตามความคืบหน้า",
    "ประชาชนในพื้นที่ให้ความร่วมมืออย่างดีเยี่ยม",
    "ระบบใหม่ช่วยลดเวลาในการประมวลผลเอกสาร",
    "เจ้าหน้าที่ทุกคนได้รับการอบรมก่อนเริ่มใช้งาน",
    "การตรวจสอบภายในพบข้อผิดพลาดเพียงเล็กน้อย",
    "แผนงานปีหน้าจะเน้นการพัฒนาบุคลากร",
    "ข้อเสนอแนะจากผู้ใช้ถูกนำไปปรับปรุงระบบ",
    "รายงานฉบับนี้สรุปผลการดำเนินงานทั้งปี",
    "ความปลอดภัยของข้อมูลเป็นเรื่องสำคัญอันดับแรก",
    "ขอขอบคุณทุกฝ่ายที่มีส่วนร่วมในความสำเร็จ",
]

COLUMN_RIGHT = [
    "ในไตรมาสที่สามมีการเปิดตัวบริการออนไลน์",
    "ผู้บริหารกล่าวว่าเราจะก้าวต่อไปอย่างมั่นคง",
    "สถิติการใช้งานเพิ่มขึ้นร้อยละสิบห้าในปีนี้",
    "อุปกรณ์ใหม่ถูกติดตั้งในสำนักงานทุกสาขา",
    "การฝึกอบรมจัดขึ้นสามครั้งตลอดทั้งปี",
    "ทีมสนับสนุนพร้อมให้คำปรึกษาตลอดเวลาทำการ",
    "โครงการต่อไปจะขยายไปยังภูมิภาคตะวันออก",
    "คู่มือการใช้งานฉบับปรับปรุงได้เผยแพร่แล้ว",
    "ความพึงพอใจของลูกค้าอยู่ในระดับสูง",
    "การจัดซื้อจัดจ้างเป็นไปตามระเบียบอย่างโปร่งใส",
    "ศูนย์ข้อมูลได้รับการยกระดับความเร็วสูงขึ้น",
    "ทุกคนมีส่วนร่วมในการวางแผนงานปีหน้า",
    "ขอบคุณที่ติดตามความก้าวหน้าของเรามาโดยตลอด",
    "เอกสารทั้งหมดจัดเก็บไว้ในระบบอิเล็กทรอนิกส์",
]

TABLE_ITEMS = [
    "ค่าน้ำประปา", "ค่าไฟฟ้า", "ค่าอินเทอร์เน็ต", "ค่าเช่าอาคาร",
    "ค่าอาหารกลางวัน", "ค่าเดินทาง", "ค่าวัสดุสำนักงาน", "ค่าบำรุงรักษา",
]
TABLE_AMOUNTS = [1250.0, 3480.5, 899.0, 12000.0, 455.75, 2310.0, 780.25, 6150.0]

NUMBERED_LEFT = [
    "ยื่นคำขอต่อหน่วยงานภายในวันที่กำหนด",
    "แนบสำเนาบัตรประจำตัวประชาชน",
    "ชำระค่าธรรมเนียมตามอัตราที่ประกาศ",
    "รับใบอนุญาตภายในสิบห้าวันทำการ",
    "ตรวจสอบเอกสารก่อนลงนามทุกฉบับ",
    "จัดเก็บสำเนาไว้เป็นหลักฐาน",
]
NUMBERED_RIGHT = [
    "กรอกแบบฟอร์มให้ครบถ้วนและชัดเจน",
    "ติดรูปถ่ายขนาดหนึ่งนิ้วจำนวนสองรูป",
    "ส่งเอกสารทางไปรษณีย์ลงทะเบียน",
    "ติดต่อเจ้าหน้าที่หากมีข้อสงสัย",
    "ตรวจสอบสถานะคำขอผ่านระบบออนไลน์",
    "เก็บใบเสร็จไว้สำหรับการตรวจสอบ",
]

MARKS_LINES = [
    "ตุ๊กตาตัวนี้มีราคา ๒๕๐ บาท .",
    "กิ๊ฟชอปปิ้ง ปั๊มน้ำมัน ป่ำ ไม่ใช่คำจริง",
    "มิ๊กซ์ ผู้ใหญ่ สุ๊ก ตุ๊ก หนึ่งคันรถ , สองคน",
    "ราคา ๙๙๙ บาท ! ไม่รวมภาษี ? ใช่ ;",
    "เป็นสิ่งของที่ทำจากวัสดุ ฯลฯ : ดังนี้ .",
    "ปั๋ว กุ๊กไก่ จิ๋ว ซื้อของที่ร้าน ๒ ชิ้น ,",
]


def font(size):
    return ImageFont.truetype(FONT, size)


def text_w(f, s):
    return f.getlength(s)


def draw_lines(lines, size, pad=40, lh=None, bg=(255, 255, 255), fg=(0, 0, 0)):
    f = font(size)
    lh = lh or int(size * 1.7)
    w = int(max(text_w(f, s) for s in lines)) + 2 * pad
    h = lh * len(lines) + 2 * pad
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    for i, s in enumerate(lines):
        d.text((pad, pad + i * lh), s, font=f, fill=fg)
    return im


def to_bgr(im):
    return np.ascontiguousarray(np.array(im.convert("RGB"))[:, :, ::-1])


def fx_paragraph():
    return to_bgr(draw_lines(PARAGRAPH, 34))


def fx_two_columns():
    size, pad, gutter = 24, 40, 60
    f = font(size)
    lh = int(size * 1.7)
    colw = int(max(text_w(f, s) for s in COLUMN_LEFT + COLUMN_RIGHT)) + 4
    title_f = font(36)
    title = "รายงานประจำปี ๒๕๖๖"
    title_h = int(36 * 1.8)
    w = pad + colw + gutter + colw + pad
    h = pad + title_h + lh * len(COLUMN_LEFT) + pad
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((pad, pad), title, font=title_f, fill=(0, 0, 0))
    y0 = pad + title_h
    for i, (a, b) in enumerate(zip(COLUMN_LEFT, COLUMN_RIGHT)):
        y = y0 + i * lh
        d.text((pad, y), a, font=f, fill=(0, 0, 0))
        d.text((pad + colw + gutter, y), b, font=f, fill=(0, 0, 0))
    return to_bgr(im)


def fx_table():
    size = 34
    f = font(size)
    lh = int(size * 2.0)
    pad = 40
    col2_w = int(max(text_w(f, f"{a:,.2f}") for a in TABLE_AMOUNTS)) + 10
    col3_w = int(max(text_w(f, f"{a:,.2f}".translate(THAI_DIGITS)) for a in TABLE_AMOUNTS)) + 10
    label_w = int(max(text_w(f, s) for s in TABLE_ITEMS)) + 40
    w = pad + label_w + col2_w + 40 + col3_w + pad
    h = pad + lh * len(TABLE_ITEMS) + pad
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    right2 = pad + label_w + col2_w
    right3 = w - pad
    for i, (item, amt) in enumerate(zip(TABLE_ITEMS, TABLE_AMOUNTS)):
        y = pad + i * lh
        d.text((pad, y), item, font=f, fill=(0, 0, 0))
        a_txt = f"{amt:,.2f}"
        d.text((right2 - text_w(f, a_txt), y), a_txt, font=f, fill=(0, 0, 0))
        t_txt = a_txt.translate(THAI_DIGITS)
        d.text((right3 - text_w(f, t_txt), y), t_txt, font=f, fill=(0, 0, 0))
    return to_bgr(im)


def fx_numbered_across():
    size, pad, gutter = 32, 40, 80
    f = font(size)
    lh = int(size * 1.9)
    colw = int(max(text_w(f, s) for s in NUMBERED_LEFT + NUMBERED_RIGHT) + text_w(f, "๑๑. ")) + 4
    w = pad + colw + gutter + colw + pad
    h = pad + lh * len(NUMBERED_LEFT) + pad
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for i, (a, b) in enumerate(zip(NUMBERED_LEFT, NUMBERED_RIGHT)):
        y = pad + i * lh
        # Interleaved numbering read across the rows: left odd, right even.
        d.text((pad, y), f"{i * 2 + 1}. {a}".translate(THAI_DIGITS), font=f, fill=(0, 0, 0))
        d.text((pad + colw + gutter, y), f"{i * 2 + 2}. {b}".translate(THAI_DIGITS), font=f, fill=(0, 0, 0))
    return to_bgr(im)


def fx_sideways():
    return np.ascontiguousarray(np.rot90(fx_paragraph(), k=-1))


def fx_skewed():
    base = fx_paragraph()
    h, w = base.shape[:2]
    pad = 60
    canvas = np.full((h + 2 * pad, w + 2 * pad, 3), 255, np.uint8)
    canvas[pad:pad + h, pad:pad + w] = base
    H, W = canvas.shape[:2]
    m = cv2.getRotationMatrix2D((W / 2, H / 2), 5, 1.0)
    return cv2.warpAffine(canvas, m, (W, H), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def fx_upside_down():
    return np.ascontiguousarray(fx_paragraph()[::-1, ::-1])


def fx_single_line():
    # Ink at 250/255: the detector finds no box, so read() takes the
    # whole-image fallback. Darker ink is detected and skips that branch.
    im = Image.new("RGB", (400, 60), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.text((12, 8), "ประเทศไทย", font=font(38), fill=(250, 250, 250))
    return to_bgr(im)


def fx_color_banner():
    w, h = 900, 360
    grad = np.zeros((h, w, 3), np.uint8)  # RGB
    t = np.linspace(0.0, 1.0, w)
    start, end = np.array([10, 20, 90]), np.array([190, 20, 20])
    row = (start[None, :] * (1 - t[:, None]) + end[None, :] * t[:, None]).astype(np.uint8)
    grad[:, :, :] = row[None, :, :]
    im = Image.fromarray(grad)
    d = ImageDraw.Draw(im)
    f = font(40)
    lines = [
        ("ประกาศรับสมัครงานประจำปี", (255, 255, 255)),
        ("วันที่เปิดรับสมัคร ๑๕ มกราคม", (255, 230, 0)),
        ("สถานที่ยื่นใบสมัครที่สำนักงาน", (255, 255, 255)),
        ("โทร ๐๒ ๒๒๒ ๓๓๓๓ ต่อ ๑๒๓", (255, 230, 0)),
    ]
    for i, (s, col) in enumerate(lines):
        d.text((40, 30 + i * 80), s, font=f, fill=col)
    return to_bgr(im)


def fx_blank():
    return np.full((600, 800, 3), 255, np.uint8)


def fx_marks():
    # Wide line gaps with detached glyphs in the gap above a line: the
    # detector gives them their own (tall) boxes. Tighter spacing merges
    # them into the neighbouring line, and _fragments has not been seen to
    # fire on any rendering tried (see the report).
    size, pad, lhf = 36, 40, 2.2
    f = font(size)
    lh = int(size * lhf)
    w = int(max(text_w(f, s) for s in MARKS_LINES)) + 2 * pad
    h = lh * len(MARKS_LINES) + 2 * pad
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    tops = []
    for i, s in enumerate(MARKS_LINES):
        y = pad + i * lh
        tops.append(y)
        d.text((pad, y), s, font=f, fill=(0, 0, 0))
    fs = font(int(size * 0.9))
    for li, dx, glyph in ((1, 40, "ๆ"), (2, 300, "ฯ"), (3, 200, "่")):
        d.text((pad + dx, tops[li] - lh * 0.5), glyph, font=fs, fill=(0, 0, 0))
    return to_bgr(im)


def fx_margin_note():
    # Horizontal prose plus two short words turned 90 degrees as a margin
    # note on the right. Only two vertical boxes, so _sideways votes 0 and
    # the page stays upright, while the turned words give tall boxes.
    size, pad, lhf = 34, 40, 1.7
    f = font(size)
    lh = int(size * lhf)
    body_w = int(max(text_w(f, s) for s in PARAGRAPH))
    note_f = font(30)
    tiles = []
    for s in ("หมายเหตุ", "ด่วน"):
        tile = Image.new("RGB", (int(text_w(note_f, s)) + 10, 44), (255, 255, 255))
        ImageDraw.Draw(tile).text((4, 4), s, font=note_f, fill=(0, 0, 0))
        tiles.append(tile.rotate(-90, expand=True))  # clockwise
    note_x = pad + body_w + 80
    W = note_x + max(t.width for t in tiles) + pad
    H = lh * len(PARAGRAPH) + 2 * pad
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    for i, s in enumerate(PARAGRAPH):
        d.text((pad, pad + i * lh), s, font=f, fill=(0, 0, 0))
    im.paste(tiles[0], (note_x, pad + lh * 1))
    im.paste(tiles[1], (note_x, pad + lh * 5))
    return to_bgr(im)


FIXTURES = {
    "paragraph": fx_paragraph,
    "two_columns": fx_two_columns,
    "table": fx_table,
    "numbered_across": fx_numbered_across,
    "sideways": fx_sideways,
    "skewed": fx_skewed,
    "upside_down": fx_upside_down,
    "single_line": fx_single_line,
    "color_banner": fx_color_banner,
    "blank": fx_blank,
    "marks": fx_marks,
    "margin_note": fx_margin_note,
}

TEXT_CASES = [
    "\uF700\uF701\uF702",
    "ก\uF710\uF705",
    "\uF719\uF71A\uF70F",
    "\uF712\uF713 ดี",
    "\u200bไทย\u200c",
    "ไทย\u00ad\u00adแลนด์",
    "\ufeffสวัสดี",
    "ค่า\u00a0น้ำ",
    "นํ้า",
    "น้ํา",
    "เเ",
    "ำำ",
    "น้้ำ",
    "ก่ั",
    "กํ่า",
    "ปิ่ั",
    "W.ศ.",
    "wฤษภาคม",
    "๗o.๘๒",
    "(๑๒) ข้อ",
    "3. item",
    "บรรทัด๑\r\nบรรทัด๒",
    "a\tb\tc",
    "",
    "ูี้ซ์ ริ ร้ร้ ไ้",
    "  ช่องว่าง   หลายช่อง  ",
    "123 ๑๒๓",
    "กรุงเทพฯ",
    "hello world",
    "\r\n\r\nบรรทัดแรก\n\nบรรทัดสุดท้าย\r\n",
    "ที่ดิน\u200dที่ดี",
    "ญ\uF70Fุ",
    "ฐานข้อมูล",
]


def box_list(boxes):
    return [np.asarray(b, dtype=float).tolist() for b in boxes]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ocr = LineOCR(str(MODEL_PARENT), threads=2)
    split_gap = ocr.cfg["det"]["split_gap"]

    for name, make in FIXTURES.items():
        img = make()
        h, w = img.shape[:2]
        assert max(h, w) < 1400, (name, h, w)
        (OUT / f"{name}.bgr.gz").write_bytes(gzip.compress(img.tobytes(), mtime=0))

        detect = ocr.detect(img)
        split_counts = [len(split_one(img, b, split_gap)) for b in detect]
        p = ocr._page(img, ocr.detect(img))
        n = len(p["rec"])
        texts = [p["rec"][i][0] for i in range(n)]
        confs = [float(p["rec"][i][1]) for i in range(n)]
        fragments = sorted(_fragments(p["boxes"], p["rec"]))
        page = {
            "boxes": box_list(p["boxes"]),
            "texts": texts,
            "confs": confs,
            "rows": [[int(i) for i in r] for r in p["rows"]],
            "angle": float(p["angle"]),
            "fragments": [int(i) for i in fragments],
        }

        if name == "margin_note":
            # Guard: this fixture exists to cover the tall-box branch of
            # _read_boxes, so it must produce a tall box and stay upright.
            tall = [b for b in p["boxes"]
                    if np.linalg.norm(b[0] - b[3]) >= 1.5 * np.linalg.norm(b[0] - b[1])]
            if not tall:
                raise AssertionError("margin_note: no box with height >= 1.5 * width in page.boxes")
        r = ocr.read(img)
        if name == "margin_note" and r["rotation"] != 0:
            raise AssertionError(f"margin_note: expected rotation 0, got {r['rotation']}")
        lines = []
        for ln in r["lines"]:
            ln = {k: v for k, v in ln.items() if k != "words"}
            lines.append(ln)
        read = {
            "text": r["text"],
            "lines": lines,
            "dropped": r["dropped"],
            "rotation": int(r["rotation"]),
            "text_angle": float(r["text_angle"]),
        }

        expected = {
            "shape": [h, w],
            "detect": box_list(detect),
            "split_counts": split_counts,
            "page": page,
            "read": read,
        }
        (OUT / f"{name}.json").write_text(json.dumps(expected, ensure_ascii=False, indent=1), encoding="utf-8")
        first = read["text"][:60].replace("\n", " | ")
        print(f"{name}: shape={h}x{w} rotation={read['rotation']} lines={len(lines)} "
              f"rec={n} dropped={len(read['dropped'])} text60={first!r}")

    cases = []
    for s in TEXT_CASES:
        cases.append({
            "in": s,
            "canonical": canonical(s),
            "fix_scripts": fix_scripts(canonical(s)),
            "invalid_share": invalid_share(s),
            "enum": _enum(s),
        })
    (OUT / "text_cases.json").write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"text_cases: {len(cases)}")

    frag = fragment_cases()
    (OUT / "fragment_cases.json").write_text(json.dumps(frag, ensure_ascii=False, indent=1), encoding="utf-8")
    print("fragment_cases: " + ", ".join(f"{c['name']}={c['expected']}" for c in frag))


def _box(x0, x1, y0, y1):
    """Axis-aligned box as 4 points, same order as the detector: TL, TR, BR, BL."""
    return [[float(x0), float(y0)], [float(x1), float(y0)], [float(x1), float(y1)], [float(x0), float(y1)]]


def fragment_cases():
    """_fragments on hand-built boxes. Line height 40 px (median of the three
    full-height lines); a fragment is <= 3 chars, under 14 px tall, within
    20 px vertically of a full-height line, and x-overlapping it."""
    line1 = _box(0, 200, 100, 140)
    line2 = _box(0, 200, 160, 200)
    near = _box(50, 60, 74, 84)  # 10 px tall, 16 px above line1's top edge region
    far = _box(50, 60, 40, 50)  # 50 px above line1
    no_x = _box(300, 310, 74, 84)  # clear of both lines horizontally
    tall_tiny = _box(50, 60, 60, 90)  # 30 px tall, not under 35 % of 40
    cases = [
        ("tiny_above_fragment", [near, line1, line2], ["ั", "ไทยไทยไทยไทย", "ไทยไทยไทยไทย"]),
        ("tiny_four_chars_not", [near, line1, line2], ["ัทยท", "ไทยไทยไทยไทย", "ไทยไทยไทยไทย"]),
        ("tiny_far_vertical_not", [far, line1, line2], ["ั", "ไทยไทยไทยไทย", "ไทยไทยไทยไทย"]),
        ("tiny_no_x_overlap_not", [no_x, line1, line2], ["ั", "ไทยไทยไทยไทย", "ไทยไทยไทยไทย"]),
        ("tiny_too_tall_not", [tall_tiny, line1, line2], ["ั", "ไทยไทยไทยไทย", "ไทยไทยไทยไทย"]),
        ("fewer_than_three_boxes", [near, line1], ["ั", "ไทยไทยไทยไทย"]),
    ]
    out = []
    for name, boxes, texts in cases:
        confs = [0.9] * len(boxes)
        rec = {i: (t, c) for i, (t, c) in enumerate(zip(texts, confs))}
        arrs = [np.asarray(b, dtype=np.float32) for b in boxes]
        expected = sorted(int(i) for i in _fragments(arrs, rec))
        out.append({"name": name, "boxes": box_list(arrs), "texts": texts, "confs": confs, "expected": expected})
    if not any(c["expected"] for c in out):
        raise AssertionError("fragment_cases: no case has a non-empty expected value")
    return out


if __name__ == "__main__":
    main()
