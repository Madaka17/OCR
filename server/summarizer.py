"""Map-reduce summary of an OCR'd job with a local LLM.

Pages are grouped into chunks, each chunk yields notes (map), the notes are
merged in levels until one document summary remains (reduce). The llm object
is duck-typed: anything with complete(prompt, schema, name, max_tokens).
"""

import json
from concurrent.futures import ThreadPoolExecutor

from server.factcheck import check_items

MIN_CONF = 0.85
CHUNK_CHARS = 9000
REDUCE_CHARS = 12000
PARALLEL = 2
SHORT_CHARS = 300

LANGUAGE_LINE = "เขียนสรุปด้วยภาษาหลักของเอกสาร (ถ้าเอกสารเป็นภาษาอังกฤษ ให้เขียนภาษาอังกฤษ)"

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"}, "page": {"type": "integer"}}, "required": ["text", "page"]}},
        "parties": {"type": "array", "items": {"type": "string"}},
        "dates": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "value": {"type": "string"}, "page": {"type": "integer"}},
            "required": ["label", "value", "page"]}},
        "amounts": {"type": "array", "items": {"type": "object", "properties": {
            "label": {"type": "string"}, "value": {"type": "string"}, "page": {"type": "integer"}},
            "required": ["label", "value", "page"]}},
    },
    "required": ["summary", "key_points", "parties", "dates", "amounts"],
}

REDUCE_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string"},
        "title": {"type": "string"},
        **MAP_SCHEMA["properties"],
    },
    "required": ["doc_type", "title"] + MAP_SCHEMA["required"],
}

MAP_PROMPT = """ข้อความต่อไปนี้ได้จาก OCR ของเอกสาร หน้า {first}-{last} อาจมีคำสะกดผิดจาก OCR
แต่ละหน้าขึ้นต้นด้วย [หน้า N]
""" + LANGUAGE_LINE + """

ให้สรุปเฉพาะสิ่งที่อยู่ในข้อความนี้ ห้ามเดา ห้ามเติมข้อมูลที่ไม่มี
- summary: สรุป 2-3 ประโยค
- key_points: ประเด็นสำคัญไม่เกิน 6 ข้อ พร้อมเลขหน้าที่พบ
- parties: ชื่อบุคคล บริษัท หน่วยงานที่สำคัญ
- dates: วันที่สำคัญ (label บอกว่าเป็นวันอะไร) พร้อมเลขหน้า
- amounts: จำนวนเงินหรือตัวเลขสำคัญ คัดลอกตัวเลขตามต้นฉบับ พร้อมเลขหน้า

{text}"""

REDUCE_PROMPT = """ต่อไปนี้คือบันทึกสรุปของเอกสารเดียวกัน (JSON) บันทึกอาจเป็นสรุปทีละช่วงหน้า หรือสรุปที่รวมมาแล้ว
เอกสารมีทั้งหมด {pages} หน้า
ชื่อไฟล์: {filename}
""" + LANGUAGE_LINE + """

รวมเป็นสรุปของทั้งเอกสาร ใช้เฉพาะข้อมูลในบันทึก ห้ามเดา
- doc_type: ประเภทเอกสาร
- title: ชื่อเรื่องสั้นๆ
- summary: สรุปทั้งเอกสาร 3-5 ประโยค
- key_points: ประเด็นสำคัญที่สุดไม่เกิน 8 ข้อ พร้อมเลขหน้า
- parties, dates, amounts: รวมรายการซ้ำให้เหลืออันเดียว เก็บเฉพาะที่สำคัญ ไม่เกิน 10 รายการต่อหมวด

{notes}"""


class ShortText(Exception):
    """The job has too little text to be worth summarizing."""


def page_texts(pages: list[dict], min_conf: float = MIN_CONF) -> list[tuple[int, str]]:
    out = []
    for p in pages:
        if p.get("error"):
            text = ""
        else:
            text = "\n".join(
                line["text"] for line in p.get("lines") or []
                if line.get("confidence", 1) >= min_conf
            )
        out.append((p["number"], text))
    return sorted(out, key=lambda x: x[0])


def make_chunks(texts: list[tuple[int, str]], chunk_chars: int = CHUNK_CHARS):
    chunks, cur, size = [], [], 0
    for num, text in texts:
        if cur and size + len(text) > chunk_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append((num, text))
        size += len(text)
    if cur:
        chunks.append(cur)
    return chunks


def sanitize(raw: dict, page_count: int) -> dict:
    def page_of(value):
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= page_count:
            return value
        return None

    def items(name, fields, limit):
        out = []
        for item in raw.get(name) or []:
            if not isinstance(item, dict):
                continue
            entry = {f: str(item.get(f, "")) for f in fields if f != "page"}
            entry["page"] = page_of(item.get("page"))
            out.append(entry)
        return out[:limit]

    def strings(name, limit):
        return [str(s) for s in (raw.get(name) or [])][:limit]

    return {
        "doc_type": str(raw.get("doc_type", "")),
        "title": str(raw.get("title", "")),
        "summary": str(raw.get("summary", "")),
        "key_points": items("key_points", ("text", "page"), 8),
        "parties": strings("parties", 10),
        "dates": items("dates", ("label", "value", "page"), 10),
        "amounts": items("amounts", ("label", "value", "page"), 10),
    }


def _batches(notes: list[dict]) -> list[list[dict]]:
    out, cur, size = [], [], 0
    for note in notes:
        s = len(json.dumps(note, ensure_ascii=False))
        if cur and size + s > REDUCE_CHARS:
            out.append(cur)
            cur, size = [], 0
        cur.append(note)
        size += s
    if cur:
        out.append(cur)
    return out


def _span(batch: list[dict]) -> str:
    return f"{batch[0]['pages'].split('-')[0]}-{batch[-1]['pages'].split('-')[-1]}"


def summarize(llm, pages: list[dict], filename: str, on_progress=None) -> dict:
    texts = page_texts(pages)
    if sum(len(t) for _, t in texts) < SHORT_CHARS:
        raise ShortText()

    chunks = make_chunks(texts)

    def run_map(chunk):
        body = "\n\n".join(f"[หน้า {n}]\n{t}" for n, t in chunk)
        prompt = MAP_PROMPT.format(first=chunk[0][0], last=chunk[-1][0], text=body)
        return llm.complete(prompt, MAP_SCHEMA, "chunk_notes", 2000)

    def run_reduce(batch):
        prompt = REDUCE_PROMPT.format(
            pages=len(pages), filename=filename,
            notes=json.dumps(batch, ensure_ascii=False),
        )
        out = llm.complete(prompt, REDUCE_SCHEMA, "document_summary", 3000)
        out["pages"] = _span(batch)
        return out

    with ThreadPoolExecutor(PARALLEL) as pool:
        notes = []
        for i, m in enumerate(pool.map(run_map, chunks), 1):
            notes.append(m)
            if on_progress:
                on_progress(i, len(chunks))
        notes = [
            {"pages": f"{c[0][0]}-{c[-1][0]}", **m} for c, m in zip(chunks, notes)
        ]

        reduced = False
        while len(notes) > 1:
            batches = _batches(notes)
            if len(batches) >= len(notes):
                # No progress: force-pair so the level always shrinks.
                batches = [notes[i:i + 2] for i in range(0, len(notes), 2)]
            notes = list(pool.map(run_reduce, batches))
            reduced = True

        final = notes[0] if reduced else run_reduce([notes[0]])

    result = sanitize(final, len(pages))
    page_text = {p["number"]: p.get("text", "") for p in pages}
    result["dates"] = check_items(result["dates"], page_text)
    result["amounts"] = check_items(result["amounts"], page_text)
    return result
