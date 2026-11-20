import threading
import time

import pytest

from server.llm import LLMError, LLMUnavailable
from server.store import JobError
from server.summary_worker import SummaryWorker

PAGE_TEXT = "ก" * 320  # above the 300-character short-document threshold


def canned(name):
    if name == "chunk_notes":
        return {"summary": "s", "key_points": [], "parties": [], "dates": [], "amounts": []}
    if name == "document_summary":
        return {"doc_type": "D", "title": "T", "summary": "s",
                "key_points": [], "parties": [], "dates": [], "amounts": []}
    raise AssertionError(f"unexpected call {name}")


class FakeLLM:
    """Answers with canned notes, or raises `error` on every call. `on_call` runs first."""

    def __init__(self, error=None, on_call=None):
        self.error = error
        self.on_call = on_call
        self.calls = 0

    def complete(self, prompt, schema, name, max_tokens):
        self.calls += 1
        if self.on_call is not None:
            self.on_call()
        if self.error is not None:
            raise self.error
        return canned(name)


def make_done_job(store, pages_text, finished_at):
    job = store.create("u1", "doc.pdf", 10, now=0)
    job_id = job["id"]
    store.set_state(job_id, "done", pages=len(pages_text), finished_at=finished_at)
    for i, t in enumerate(pages_text, 1):
        line = {"text": t, "confidence": 0.99, "box": []}
        store.write_page(job_id, i, {"number": i, "text": t, "lines": [line]}, None)
    return job_id


@pytest.fixture
def make_worker(store):
    created = []

    def factory(llm=None, poll_seconds=5, now=time.time):
        worker = SummaryWorker(store, llm or FakeLLM(), model="test-model", now=now,
                               poll_seconds=poll_seconds, url="http://llm.test")
        created.append(worker)
        return worker

    yield factory
    for worker in created:
        worker.stop()


def test_scan_backfills_done_jobs_only(store, make_worker):
    done = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    failed = make_done_job(store, [PAGE_TEXT], finished_at=2.0)
    store.set_state(failed, "failed", finished_at=2.0)
    cancelled = make_done_job(store, [PAGE_TEXT], finished_at=3.0)
    store.set_state(cancelled, "cancelled", finished_at=3.0)

    make_worker().scan(now=10.0)

    assert store.read_summary(done)["state"] == "pending"
    assert store.read_summary(failed) is None
    assert store.read_summary(cancelled) is None


def test_step_summarizes_oldest_first(store, make_worker):
    newer = make_done_job(store, [PAGE_TEXT], finished_at=200.0)
    older = make_done_job(store, [PAGE_TEXT], finished_at=100.0)
    worker = make_worker()

    assert worker.step(now=1000.0) is True

    assert store.read_summary(older)["state"] == "done"
    assert store.read_summary(older)["result"]["title"] == "T"
    assert store.read_summary(newer)["state"] == "pending"


def test_short_document_marked_short(store, make_worker):
    job_id = make_done_job(store, ["ก" * 100], finished_at=1.0)

    make_worker().step(now=10.0)

    assert store.read_summary(job_id)["state"] == "short"


def test_unavailable_never_fails(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    llm = FakeLLM(error=LLMUnavailable("down"))
    worker = make_worker(llm=llm)
    now = 100.0

    for _ in range(10):
        worker.step(now)
        record = store.read_summary(job_id)
        assert record["state"] == "pending"
        assert record["attempts"] == 0
        assert record["next_try_at"] - now <= 300
        now = record["next_try_at"] + 1

    llm.error = None
    assert worker.step(now) is True
    assert store.read_summary(job_id)["state"] == "done"


def test_bad_output_fails_after_three_attempts(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    worker = make_worker(llm=FakeLLM(error=LLMError("bad")))
    now = 100.0

    states = []
    for _ in range(3):
        worker.step(now)
        record = store.read_summary(job_id)
        states.append(record["state"])
        now = record["next_try_at"] + 1

    assert states == ["pending", "pending", "failed"]
    assert record["attempts"] == 3
    assert "bad" in record["error"]


def test_recover_running_to_pending(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    store.write_summary(job_id, {"state": "running", "updated_at": 1.0, "attempts": 0, "next_try_at": 0,
                                 "error": None, "progress": [0, 1], "model": "m", "result": None, "waits": 0})

    make_worker().recover()

    assert store.read_summary(job_id)["state"] == "pending"


def test_job_deleted_mid_run(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    llm = FakeLLM(on_call=lambda: store.delete(job_id))

    make_worker(llm=llm).step(now=10.0)

    assert not (store.root / "jobs" / job_id).exists()


def test_regenerate_conflicts_and_resets(store, make_worker):
    worker = make_worker()
    pending = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    worker.scan(now=1.0)
    with pytest.raises(JobError) as exc:
        worker.regenerate(pending, now=2.0)
    assert exc.value.status == 409
    assert exc.value.message == "กำลังสรุปอยู่"

    done = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    store.write_summary(done, {"state": "done", "updated_at": 1.0, "attempts": 2, "next_try_at": 0,
                               "error": None, "progress": None, "model": "m", "result": {"title": "T"}, "waits": 0})
    record = worker.regenerate(done, now=5.0)
    assert record["state"] == "pending"
    assert record["attempts"] == 0
    assert store.read_summary(done)["state"] == "pending"

    running_ocr = store.create("u1", "ocr.pdf", 10, now=0)["id"]
    store.set_state(running_ocr, "running")
    with pytest.raises(JobError) as exc:
        worker.regenerate(running_ocr, now=5.0)
    assert exc.value.status == 409
    assert exc.value.message == "งานยังอ่านไม่เสร็จ"


def test_status_counts_all_states(store, make_worker):
    make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    worker = make_worker()
    worker.scan(now=1.0)

    status = worker.status()

    assert status["enabled"] is True
    assert status["url"] == "http://llm.test"
    assert status["model"] == "test-model"
    assert status["counts"] == {"pending": 1, "running": 0, "done": 0, "failed": 0, "short": 0}


def test_thread_start_stop(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    worker = make_worker()
    worker.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and (store.read_summary(job_id) or {}).get("state") != "done":
        time.sleep(0.02)
    assert store.read_summary(job_id)["state"] == "done"

    worker.stop()
    assert not any(t.name == "summary-worker" and t.is_alive() for t in threading.enumerate())


def test_final_write_failure_repends(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    real_write = store.write_summary

    def fail_on_done(job, data):
        if data["state"] == "done":
            raise OSError("disk full")
        return real_write(job, data)

    store.write_summary = fail_on_done
    assert make_worker().step(now=10.0) is True

    record = store.read_summary(job_id)
    assert record["state"] == "pending"
    assert record["next_try_at"] > 10.0


def test_page_read_error_repends(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)

    def broken_read(job, number):
        raise OSError("unreadable page")

    store.read_page = broken_read
    assert make_worker().step(now=10.0) is True

    assert store.read_summary(job_id)["state"] == "pending"


def test_persistent_final_write_failure_fails_after_three(store, make_worker):
    job_id = make_done_job(store, [PAGE_TEXT], finished_at=1.0)
    real_write = store.write_summary

    def fail_on_done(job, data):
        if data["state"] == "done":
            raise OSError("disk full")
        return real_write(job, data)

    store.write_summary = fail_on_done
    worker = make_worker()
    now = 10.0
    for _ in range(3):
        worker.step(now)
        now = (store.read_summary(job_id)["next_try_at"] or now) + 1

    record = store.read_summary(job_id)
    assert record["state"] == "failed"
    assert record["attempts"] == 3
    assert "disk full" in record["error"]
