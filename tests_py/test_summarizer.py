import copy
import json
import threading
import time

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
    def __init__(self, note_chars=10, chunk_note=None):
        self.note_chars = note_chars
        self.chunk_note = chunk_note
        self.calls = []
        self._lock = threading.Lock()

    def complete(self, prompt, schema, name, max_tokens):
        with self._lock:
            self.calls.append((name, prompt, max_tokens))
        if name == "chunk_notes" and self.chunk_note is not None:
            return copy.deepcopy(self.chunk_note)
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


def test_notes_compacted_before_reduce():
    big = {
        "summary": "m" * 20000,
        "key_points": [],
        "parties": [],
        "dates": [],
        "amounts": [{"label": "x", "value": "1,000", "page": 1} for _ in range(50)],
    }
    llm = FakeLLM(chunk_note=big)
    summarize(llm, [page(1, "x" * 500)], "f.pdf")
    prompts = llm.prompts("document_summary")
    assert prompts
    assert all(len(p) < REDUCE_CHARS + 3000 for p in prompts)
    for p in prompts:
        for note in _notes_in(p):
            assert len(note["summary"]) <= 1500
            assert len(note["amounts"]) <= 10


def _worst_note():
    item = {"label": "L" * 200, "value": "V" * 200, "page": 1}
    return {
        "summary": "s" * 1500,
        "key_points": [{"text": "k" * 200, "page": 1} for _ in range(8)],
        "parties": ["p" * 200 for _ in range(10)],
        "dates": [dict(item) for _ in range(10)],
        "amounts": [dict(item) for _ in range(10)],
    }


def test_large_notes_terminate():
    llm = FakeLLM(chunk_note=_worst_note())
    pages = [page(n, "x" * 9000) for n in range(1, 11)]
    result = summarize(llm, pages, "f.pdf")
    assert isinstance(result, dict)
    prompts = llm.prompts("document_summary")
    assert len(prompts) < 20
    assert all(len(p) < REDUCE_CHARS + 3000 for p in prompts)


def test_compact_note_fits_half_budget():
    out = summarizer._compact(_worst_note(), 10)
    assert len(json.dumps(out, ensure_ascii=False)) <= REDUCE_CHARS // 2
    assert len(out["key_points"]) >= 1


def test_max_tokens_per_stage():
    llm = FakeLLM()
    summarize(llm, [page(1, "x" * 500)], "f.pdf")
    by_name = {name: mt for name, _, mt in llm.calls}
    assert by_name["chunk_notes"] == 2000
    assert by_name["document_summary"] == 3000


class FailingLLM(FakeLLM):
    """Fails the first chunk call; counts every chunk call made."""

    def complete(self, prompt, schema, name, max_tokens):
        if name == "chunk_notes":
            with self._lock:
                first = not self.prompts("chunk_notes")
                self.calls.append((name, prompt, max_tokens))
            if first:
                raise RuntimeError("boom")
            return super().complete(prompt, schema, name, max_tokens)
        return super().complete(prompt, schema, name, max_tokens)


def test_map_error_stops_new_calls():
    llm = FailingLLM()
    pages = [page(n, "x" * 9000) for n in range(1, 21)]
    with pytest.raises(RuntimeError, match="boom"):
        summarize(llm, pages, "f.pdf")
    # Only calls already in flight when the first one failed may still run.
    assert len(llm.prompts("chunk_notes")) <= 2 * summarizer.PARALLEL


def test_map_results_keep_chunk_order():
    llm = FakeLLM()
    pages = [page(n, "x" * 9000) for n in range(1, 8)]
    summarize(llm, pages, "f.pdf")
    reduce_prompt = llm.prompts("document_summary")[0]
    spans = [n["pages"] for n in _notes_in(reduce_prompt)]
    assert spans == sorted(spans, key=lambda s: int(s.split("-")[0]))


def test_in_flight_llm_call_never_blocks_exit(tmp_path):
    """Ctrl+C must not wait for the model: the process exits with a call still open."""
    import subprocess
    import sys
    import textwrap

    from tests_py.conftest import PROJECT_ROOT

    script = tmp_path / "hang.py"
    script.write_text(textwrap.dedent(f"""
        import sys, threading
        sys.path.insert(0, {str(PROJECT_ROOT)!r})
        from server import summarizer

        started = threading.Event()

        class HangingLLM:
            def complete(self, prompt, schema, name, max_tokens):
                started.set()
                threading.Event().wait()  # never answers

        pages = [{{"number": 1, "text": "x" * 500, "lines": [{{"text": "x" * 500, "confidence": 0.9}}]}}]
        threading.Thread(target=summarizer.summarize, args=(HangingLLM(), pages, "f.pdf"), daemon=True).start()
        started.wait(10)
        print("exiting", flush=True)
    """), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert "exiting" in proc.stdout


class SlowLLM(FakeLLM):
    def complete(self, prompt, schema, name, max_tokens):
        time.sleep(0.05)
        return super().complete(prompt, schema, name, max_tokens)


def test_progress_error_stops_new_calls():
    llm = SlowLLM()
    pages = [page(n, "x" * 9000) for n in range(1, 21)]

    def explode(done, total):
        raise OSError("disk full")

    with pytest.raises(OSError):
        summarize(llm, pages, "f.pdf", on_progress=explode)
    time.sleep(0.2)  # let in-flight calls settle
    assert len(llm.prompts("chunk_notes")) <= 2 * summarizer.PARALLEL
