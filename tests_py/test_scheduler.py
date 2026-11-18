import json
import threading
import time

import numpy as np
import pytest
from PIL import Image

from server.pages import count_pages
from server.scheduler import Scheduler
from server.stats import EventLog


class FakeEngine:
    def __init__(self, fail_shapes=()):
        self.calls = []
        self.fail_shapes = set(fail_shapes)
        self._lock = threading.Lock()

    def read(self, bgr):
        h, w = bgr.shape[:2]
        with self._lock:
            self.calls.append((h, w))
        if (h, w) in self.fail_shapes:
            raise RuntimeError("engine exploded")
        return {"text": f"{h}x{w}", "lines": []}


def sized_pdf(path, n_pages):
    """Page k (1-based) is (100k, 50+25k) px at 200 dpi.

    These heights round-trip exactly through pdfium's 200 dpi render; other heights
    (e.g. 60, 70) come back one pixel taller because of float rounding in the scale.
    """
    images = [Image.new("RGB", (100 * k, 50 + 25 * k), "white") for k in range(1, n_pages + 1)]
    images[0].save(path, save_all=True, append_images=images[1:], resolution=200)
    return path


def expected_text(n):
    return f"{50 + 25 * n}x{100 * n}"


def expected_shape(n):
    return (50 + 25 * n, 100 * n)


def submit(store, sched, path, now):
    data = path.read_bytes()
    job = store.create("u1", path.name, len(data), now=now)
    store.put_chunk(job["id"], 0, data, now=now)
    source = store.assemble(job["id"])
    sched.enqueue(job["id"], count_pages(source))
    return job["id"]


def wait_for_state(store, job_id, state, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.get(job_id)["state"] == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"job never reached {state}: {store.get(job_id)['state']}")


def drain_claims(sched):
    out = []
    while (item := sched.claim()) is not None:
        out.append(item)
    return out


def complete(store, job_id, number):
    store.write_page(job_id, number, {"number": number, "text": "", "lines": []}, None)


@pytest.fixture
def make_scheduler(store, tmp_path):
    created = []

    def factory(engine=None, workers=1):
        sched = Scheduler(store, EventLog(tmp_path / "logs"), lambda: engine or FakeEngine(), workers=workers)
        created.append(sched)
        return sched

    yield factory
    for sched in created:
        sched.stop()


def test_round_robin_interleaves(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    a = submit(store, sched, sized_pdf(tmp_path / "a.pdf", 5), now=1.0)
    submit(store, sched, sized_pdf(tmp_path / "b.pdf", 2), now=2.0)

    order = []
    while (item := sched.claim()) is not None:
        job_id, number = item
        order.append(("A" if job_id == a else "B") + str(number))
        complete(store, job_id, number)

    assert order == ["A1", "B1", "A2", "B2", "A3", "A4", "A5"]


def test_recover_resumes_without_rereading(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    job_id = submit(store, sched, sized_pdf(tmp_path / "six.pdf", 6), now=1.0)
    for n in (1, 2, 3):
        complete(store, job_id, n)
    store.set_state(job_id, "running")

    sched.recover()

    assert store.get(job_id)["state"] == "queued"
    assert [number for _, number in drain_claims(sched)] == [4, 5, 6]


def fail_page(store, job_id, number):
    store.write_page(job_id, number, {"number": number, "error": "boom", "text": "", "lines": []}, None)


def test_recover_finalizes_fully_written_job_done(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)
    complete(store, job_id, 1)
    complete(store, job_id, 2)
    store.set_state(job_id, "running")  # power lost before the terminal set_state

    sched.recover()

    meta = store.get(job_id)
    assert meta["state"] == "done"
    assert meta["finished_at"] is not None


def test_recover_finalizes_all_error_job_failed(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)
    fail_page(store, job_id, 1)
    fail_page(store, job_id, 2)
    store.set_state(job_id, "running")

    sched.recover()

    meta = store.get(job_id)
    assert meta["state"] == "failed"
    assert meta["finished_at"] is not None


def test_claim_finalizes_job_with_all_pages_written(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)
    complete(store, job_id, 1)
    complete(store, job_id, 2)
    store.set_state(job_id, "running")

    assert sched.claim() is None
    assert store.get(job_id)["state"] == "done"


def test_two_workers_multi_page_pdf(store, tmp_path, make_scheduler):
    engine = FakeEngine()
    sched = make_scheduler(engine=engine, workers=2)
    job_id = submit(store, sched, sized_pdf(tmp_path / "twelve.pdf", 12), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done")

    for n in range(1, 13):
        assert store.read_page(job_id, n)["text"] == expected_text(n)
        assert (store.jobs_dir / job_id / "pages" / f"{n}.jpg").exists()
    assert store.get(job_id)["state"] == "done"


def test_page_error_continues(store, tmp_path, make_scheduler):
    engine = FakeEngine(fail_shapes={expected_shape(2)})
    sched = make_scheduler(engine=engine, workers=1)
    job_id = submit(store, sched, sized_pdf(tmp_path / "three.pdf", 3), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done")

    assert store.read_page(job_id, 1)["text"] == expected_text(1)
    assert "error" in store.read_page(job_id, 2)
    assert store.read_page(job_id, 3)["text"] == expected_text(3)
    meta = store.get(job_id)
    assert meta["state"] == "done"
    assert len(meta["failed_pages"]) == 1
    error_lines = (tmp_path / "logs" / "errors.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(error_lines) == 1


def test_cancel_stops_claims(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    job_id = submit(store, sched, sized_pdf(tmp_path / "three.pdf", 3), now=1.0)

    sched.cancel(job_id)

    assert store.get(job_id)["state"] == "cancelled"
    assert sched.claim() is None


def test_concurrent_last_pages_single_terminal_transition(store, tmp_path, make_scheduler, monkeypatch):
    barrier = threading.Barrier(2, timeout=10)

    class BarrierEngine(FakeEngine):
        def read(self, bgr):
            barrier.wait()  # both pages are in flight at once
            return super().read(bgr)

    sched = make_scheduler(engine=BarrierEngine(fail_shapes={expected_shape(2)}), workers=2)
    transitions = []
    real_set_state = store.set_state

    def spy(job_id, state, **fields):
        transitions.append(state)
        real_set_state(job_id, state, **fields)

    monkeypatch.setattr(store, "set_state", spy)
    job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done")

    terminal = [s for s in transitions if s in ("done", "failed", "cancelled")]
    assert terminal == ["done"]
    assert store.get(job_id)["failed_pages"] == [2]


def test_cancel_mid_page_drops_result(store, tmp_path, make_scheduler):
    entered = threading.Event()
    release = threading.Event()

    class GateEngine(FakeEngine):
        def read(self, bgr):
            entered.set()
            release.wait(10)
            return super().read(bgr)

    sched = make_scheduler(engine=GateEngine(), workers=1)
    job_id = submit(store, sched, sized_pdf(tmp_path / "one.pdf", 2), now=1.0)

    sched.start()
    assert entered.wait(10)
    sched.cancel(job_id)
    release.set()
    sched.stop()

    assert store.read_page(job_id, 1) is None
    assert store.get(job_id)["state"] == "cancelled"


def test_delete_mid_page_drops_result(store, tmp_path, make_scheduler):
    entered = threading.Event()
    release = threading.Event()

    class GateEngine(FakeEngine):
        def read(self, bgr):
            entered.set()
            release.wait(10)
            return super().read(bgr)

    sched = make_scheduler(engine=GateEngine(), workers=1)
    job_id = submit(store, sched, sized_pdf(tmp_path / "one.pdf", 2), now=1.0)

    sched.start()
    assert entered.wait(10)
    store.delete(job_id)
    release.set()
    sched.stop()

    assert store.get(job_id) is None
    assert store.read_page(job_id, 1) is None


def test_worker_survives_event_log_failure(store, tmp_path, make_scheduler, monkeypatch):
    sched = make_scheduler(workers=1)
    failures = []

    def broken_page_done(*args, **kwargs):
        failures.append(args)
        raise OSError("disk full")

    monkeypatch.setattr(sched.events, "page_done", broken_page_done)
    job_id = submit(store, sched, sized_pdf(tmp_path / "three.pdf", 3), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done", timeout=10)

    assert sched.alive()
    assert len(failures) == 3
    for n in (1, 2, 3):
        assert store.read_page(job_id, n)["text"] == expected_text(n)


def test_worker_survives_write_failure_and_retries_page(store, tmp_path, make_scheduler, monkeypatch):
    sched = make_scheduler(workers=1)
    real_write = store.write_page
    failed = []

    def flaky_write(job_id, number, result, jpeg):
        if not failed:
            failed.append(number)
            raise OSError("disk full")
        return real_write(job_id, number, result, jpeg)

    monkeypatch.setattr(store, "write_page", flaky_write)
    job_id = submit(store, sched, sized_pdf(tmp_path / "three.pdf", 3), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done", timeout=10)

    assert sched.alive()
    assert failed == [1]
    for n in (1, 2, 3):
        assert store.read_page(job_id, n)["text"] == expected_text(n)


def test_worker_survives_engine_factory_failure(store, tmp_path):
    engine = FakeEngine()
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("model missing")
        return engine

    sched = Scheduler(store, EventLog(tmp_path / "logs"), factory, workers=1)
    try:
        job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)
        sched.start()
        wait_for_state(store, job_id, "done", timeout=10)
        assert sched.alive()
        assert len(attempts) >= 2
        assert store.read_page(job_id, 2)["text"] == expected_text(2)
    finally:
        sched.stop()


def test_failed_claim_does_not_strand_page(store, tmp_path, make_scheduler, monkeypatch):
    sched = make_scheduler()
    job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)
    real_set_state = store.set_state
    hit = []

    def flaky(job_id_arg, state, **fields):
        if state == "running" and not hit:
            hit.append(1)
            raise OSError("disk full")
        return real_set_state(job_id_arg, state, **fields)

    monkeypatch.setattr(store, "set_state", flaky)

    with pytest.raises(OSError):
        sched.claim()
    assert sched.claim() == (job_id, 1)


def test_worker_survives_claim_failure(store, tmp_path, make_scheduler, monkeypatch):
    sched = make_scheduler(workers=1)
    real_set_state = store.set_state
    hit = []

    def flaky(job_id_arg, state, **fields):
        if state == "running" and not hit:
            hit.append(1)
            raise OSError("disk full")
        return real_set_state(job_id_arg, state, **fields)

    monkeypatch.setattr(store, "set_state", flaky)
    job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done", timeout=10)

    assert sched.alive()
    assert store.read_page(job_id, 2)["text"] == expected_text(2)


def test_queue_position_counts_earlier_active_jobs(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    a = submit(store, sched, sized_pdf(tmp_path / "a.pdf", 2), now=1.0)
    b = submit(store, sched, sized_pdf(tmp_path / "b.pdf", 2), now=2.0)
    c = submit(store, sched, sized_pdf(tmp_path / "c.pdf", 2), now=3.0)

    assert [sched.queue_position(j) for j in (a, b, c)] == [0, 1, 2]

    sched.cancel(a)

    assert sched.queue_position(b) == 0
    assert sched.queue_position(c) == 1


def test_eta_none_until_first_page_then_estimates(store, tmp_path, make_scheduler):
    entered = threading.Event()
    release = threading.Event()

    class GateSecondPage(FakeEngine):
        def read(self, bgr):
            if bgr.shape[:2] == expected_shape(2):
                entered.set()
                release.wait(10)
            return super().read(bgr)

    sched = make_scheduler(engine=GateSecondPage(), workers=1)
    job_id = submit(store, sched, sized_pdf(tmp_path / "three.pdf", 3), now=1.0)
    assert sched.eta_seconds(job_id) is None

    sched.start()
    assert entered.wait(10)  # page 1 finished, page 2 in flight
    try:
        page_seconds = [
            json.loads(line)["seconds"]
            for line in (tmp_path / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line)["job"] == job_id
        ]
        assert len(page_seconds) == 1
        # 2 pages remain (page 2 in flight, page 3 queued), 1 active job, 1 worker.
        assert sched.eta_seconds(job_id) == pytest.approx(2 * page_seconds[0])
    finally:
        release.set()
        sched.stop()


def test_alive_reflects_thread_state(make_scheduler):
    sched = make_scheduler(workers=2)
    assert not sched.alive()
    sched.start()
    wait_until_alive(sched)
    sched.stop()
    assert not sched.alive()


def wait_until_alive(sched, timeout=10):
    deadline = time.monotonic() + timeout
    while not sched.alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sched.alive()


def test_float_box_written_as_ints(store, tmp_path, make_scheduler):
    class BoxEngine(FakeEngine):
        def read(self, bgr):
            return {
                "text": "t",
                "lines": [
                    {
                        "text": "t",
                        "confidence": 0.5,
                        "box": np.array([[1.4, 2.6], [3.2, 4.7], [5.0, 6.0], [7.9, 8.1]]),
                    }
                ],
            }

    sched = make_scheduler(engine=BoxEngine(), workers=1)
    job_id = submit(store, sched, sized_pdf(tmp_path / "one.pdf", 1), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done")

    box = store.read_page(job_id, 1)["lines"][0]["box"]
    assert box == [[1, 3], [3, 5], [5, 6], [8, 8]]
    assert all(isinstance(v, int) for point in box for v in point)


# ----- corrupt files (item 4) -------------------------------------------------

def test_truncated_page_job_completes_after_restart(store, tmp_path, make_scheduler):
    job_id = submit(store, make_scheduler(workers=1), sized_pdf(tmp_path / "three.pdf", 3), now=1.0)
    complete(store, job_id, 1)
    complete(store, job_id, 2)
    complete(store, job_id, 3)
    (store.jobs_dir / job_id / "pages" / "3.json").write_text('{"number": 3, "te', encoding="utf-8")
    store.set_state(job_id, "running")

    sched = make_scheduler(engine=FakeEngine(), workers=2)  # restart
    sched.start()
    wait_for_state(store, job_id, "done")

    assert store.read_page(job_id, 3)["text"] == expected_text(3)


def test_truncated_meta_does_not_stall_other_jobs(store, tmp_path, make_scheduler):
    sched = make_scheduler()
    broken = submit(store, sched, sized_pdf(tmp_path / "a.pdf", 2), now=1.0)
    healthy = submit(store, sched, sized_pdf(tmp_path / "b.pdf", 2), now=2.0)
    (store.jobs_dir / broken / "meta.json").write_text('{"id": "x', encoding="utf-8")

    assert sched.claim() == (healthy, 1)
    assert store.cleanup(3.0) == []
    assert [j["id"] for j in store.list(None)] == [healthy]


# ----- engine readiness and heartbeat (item 5) --------------------------------

def test_engine_backoff_doubles_from_half_second_and_caps_at_60(monkeypatch):
    from server import scheduler as sched_mod

    assert sched_mod.engine_backoff_seconds(1) == 0.5
    assert sched_mod.engine_backoff_seconds(2) == 1.0
    assert sched_mod.engine_backoff_seconds(3) == 2.0
    assert sched_mod.engine_backoff_seconds(8) == 60.0
    assert sched_mod.engine_backoff_seconds(40) == 60.0


def test_not_alive_until_engine_loads(store, tmp_path, make_scheduler):
    release = threading.Event()

    def slow_factory():
        release.wait(10)
        return FakeEngine()

    sched = Scheduler(store, EventLog(tmp_path / "logs"), slow_factory, workers=2)
    try:
        sched.start()
        time.sleep(0.1)
        assert not sched.alive()
        release.set()
        deadline = time.monotonic() + 10
        while not sched.alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sched.alive()
    finally:
        release.set()
        sched.stop()


def test_never_alive_when_engine_always_fails(store, tmp_path, monkeypatch):
    from server import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "BACKOFF_SECONDS", 0.01)

    def broken():
        raise RuntimeError("model missing")

    sched = Scheduler(store, EventLog(tmp_path / "logs"), broken, workers=2)
    try:
        sched.start()
        time.sleep(0.3)
        assert sched.alive() is False
    finally:
        sched.stop()


def test_stuck_page_makes_worker_unhealthy(store, tmp_path, make_scheduler, monkeypatch):
    from server import scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "PAGE_STUCK_SECONDS", 0.05)
    entered = threading.Event()
    release = threading.Event()

    class StuckEngine(FakeEngine):
        def read(self, bgr):
            entered.set()
            release.wait(10)
            return super().read(bgr)

    sched = make_scheduler(engine=StuckEngine(), workers=1)
    submit(store, sched, sized_pdf(tmp_path / "one.pdf", 1), now=1.0)
    sched.start()
    try:
        assert entered.wait(10)
        time.sleep(0.2)
        assert sched.alive() is False
    finally:
        release.set()
        sched.stop()


# ----- disk full (item 6) -------------------------------------------------------

def test_storage_failures_three_times_fail_the_job(store, tmp_path, make_scheduler, monkeypatch):
    sched = make_scheduler(workers=1)
    attempts = []

    def always_full(job_id, number, result, jpeg):
        attempts.append(number)
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "write_page", always_full)
    job_id = submit(store, sched, sized_pdf(tmp_path / "two.pdf", 2), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "failed", timeout=20)
    time.sleep(0.6)  # give a 4th attempt the chance to happen

    meta = store.get(job_id)
    assert meta["error"] == "ดิสก์เต็มหรือเขียนไฟล์ไม่ได้"
    assert attempts == [1, 1, 1]
    assert sched.alive()


def test_storage_failure_counter_resets_on_success(store, tmp_path, make_scheduler, monkeypatch):
    sched = make_scheduler(workers=1)
    real_write = store.write_page
    calls = []

    def flaky(job_id, number, result, jpeg):
        calls.append(number)
        if calls.count(number) <= 2:
            raise OSError("disk hiccup")
        return real_write(job_id, number, result, jpeg)

    monkeypatch.setattr(store, "write_page", flaky)
    job_id = submit(store, sched, sized_pdf(tmp_path / "one.pdf", 1), now=1.0)

    sched.start()
    wait_for_state(store, job_id, "done", timeout=20)

    assert store.read_page(job_id, 1)["text"] == expected_text(1)
