import json
import threading

import pytest

from server import summarizer
from server.summarizer import (
    CHUNK_CHARS,
    REDUCE_CHARS,
    ShortText,
    make_chunks,
    page_texts,
    sanitize,
    summarize,
)


def page(n, text, conf=0.95):
    return {"number": n, "text": text, "lines": [{"text": text, "confidence": conf}]}


def _notes_in(prompt):
    """Decode the JSON notes array that the reduce prompt puts at its end."""
    return json.JSONDecoder().raw_decode(prompt, prompt.index("[{"))[0]


class FakeLLM:
    def __init__(self, note_chars=10):
        self.note_chars = note_chars
        self.calls = []
        self._lock = threading.Lock()

    def complete(self, prompt, schema, name, max_tokens):
        with self._lock:
            self.calls.append((name, prompt, max_tokens))
        if name == "chunk_notes":
            return {
                "summary": "m" * self.note_chars,
                "key_points": [],
                "parties": [],
                "dates": [],
                "amounts": [{"label": "x", "value": "1,000", "page": 1}],
            }
        if name == "document_summary":
            notes = _notes_in(prompt)
            amounts = [a for n in notes for a in n.get("amounts", [])]
            return {
                "doc_type": "D",
                "title": "T",
                "summary": "s" * self.note_chars,
                "key_points": [],
                "parties": [],
                "dates": [],
                "amounts": amounts,
            }
        raise AssertionError(f"unexpected call {name}")

    def prompts(self, name):
        return [p for n, p, _ in self.calls if n == name]


def test_short_text_raises():
    llm = FakeLLM()
    pages = [page(1, "a" * 100), page(2, "b" * 100)]
    with pytest.raises(ShortText):
        summarize(llm, pages, "f.pdf")
    assert llm.calls == []


def test_low_confidence_lines_dropped():
    pages = [{
        "number": 1,
        "text": "keep\ndrop",
        "lines": [{"text": "keep", "confidence": 0.9}, {"text": "drop", "confidence": 0.5}],
    }]
    assert page_texts(pages) == [(1, "keep")]


def test_chunks_group_pages_under_limit():
    texts = [(1, "x" * 4000), (2, "x" * 4000), (3, "x" * 4000)]
    chunks = make_chunks(texts, chunk_chars=9000)
    assert [[n for n, _ in c] for c in chunks] == [[1, 2], [3]]


def test_oversized_page_is_its_own_chunk():
    texts = [(1, "x" * 100), (2, "x" * 9500), (3, "x" * 100)]
    chunks = make_chunks(texts, chunk_chars=9000)
    assert [[n for n, _ in c] for c in chunks] == [[1], [2], [3]]


def test_progress_reported_per_chunk():
    llm = FakeLLM()
    pages = [page(n, "x" * 10000) for n in (1, 2, 3)]
    calls = []
    summarize(llm, pages, "f.pdf", on_progress=lambda d, t: calls.append((d, t)))
    assert len(calls) == 3
    assert calls[-1] == (3, 3)


def test_single_chunk_still_reduced_for_title():
    llm = FakeLLM()
    result = summarize(llm, [page(1, "x" * 500)], "f.pdf")
    assert len(llm.prompts("document_summary")) == 1
    assert result["title"] == "T"


def test_reduce_in_levels():
    llm = FakeLLM(note_chars=1500)
    pages = [page(n, "x" * 9000) for n in range(1, 41)]
    result = summarize(llm, pages, "f.pdf")
    reduce_prompts = llm.prompts("document_summary")
    assert len(reduce_prompts) > 1
    assert all(len(p) < REDUCE_CHARS + 3000 for p in reduce_prompts)
    assert isinstance(result, dict)


def test_sanitize_bad_pages_and_missing_keys():
    out = sanitize(
        {"dates": [{"label": "d", "value": "v", "page": 999}],
         "key_points": [{"text": "k", "page": "3"}]},
        114,
    )
    assert out["dates"][0]["page"] is None
    assert out["key_points"][0]["page"] is None
    assert out["parties"] == []
    assert out["title"] == ""
    assert set(out) == {"doc_type", "title", "summary", "key_points", "parties", "dates", "amounts"}


def test_amounts_fact_checked():
    llm = FakeLLM()
    result = summarize(llm, [page(1, "ยอดรวม 1,000 บาท " + "x" * 300)], "f.pdf")
    assert result["amounts"][0]["check"] == "ok"


def test_prompt_asks_for_document_language():
    llm = FakeLLM(note_chars=1500)
    pages = [page(n, "x" * 9000) for n in range(1, 6)]
    summarize(llm, pages, "f.pdf")
    assert llm.calls
    for _, prompt, _ in llm.calls:
        assert "ภาษาหลักของเอกสาร" in prompt


def test_max_tokens_per_stage():
    llm = FakeLLM()
    summarize(llm, [page(1, "x" * 500)], "f.pdf")
    by_name = {name: mt for name, _, mt in llm.calls}
    assert by_name["chunk_notes"] == 2000
    assert by_name["document_summary"] == 3000
