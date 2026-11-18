import sys
import threading
import time

from server.pages import render_page, thumbnail_jpeg

ACTIVE = ("queued", "running")
BACKOFF_SECONDS = 0.5
ENGINE_BACKOFF_CAP_SECONDS = 60.0
PAGE_STUCK_SECONDS = 600  # a worker inside one page longer than this makes /health fail
STORAGE_FAILURE_LIMIT = 3
STORAGE_FAILED_MESSAGE = "ดิสก์เต็มหรือเขียนไฟล์ไม่ได้"


def engine_backoff_seconds(failures: int) -> float:
    """Delay after the given number (>= 1) of consecutive engine load failures."""
    return min(ENGINE_BACKOFF_CAP_SECONDS, BACKOFF_SECONDS * 2 ** (failures - 1))


class Scheduler:
    """Round-robin page scheduler over all active jobs, run by daemon worker threads.

    Metadata mutations and page writes happen under self._lock; OCR and rendering do not.
    """

    def __init__(self, store, events, engine_factory, workers: int):
        self.store = store
        self.events = events
        self.engine_factory = engine_factory
        self.workers = workers
        self._lock = threading.Lock()
        self._claimed: set[tuple[str, int]] = set()
        self._cursor = 0
        self._seconds: dict[str, list[float]] = {}  # job_id -> [total seconds, page count]
        self._storage_failures: dict[tuple[str, int], int] = {}
        self._ready = [False] * workers  # True once that worker's engine has loaded
        self._busy_since: dict[int, float] = {}  # worker index -> monotonic start of its page
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ----- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.recover()
        self._ready = [False] * self.workers
        for i in range(self.workers):
            thread = threading.Thread(target=self._run, args=(i,), name=f"ocr-worker-{i}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join()
        self._threads = []
        self._ready = [False] * self.workers

    def alive(self) -> bool:
        """True when every worker thread runs, every engine has loaded, and no page is stuck."""
        if not self._threads or not all(t.is_alive() for t in self._threads):
            return False
        if not all(self._ready):
            return False
        now = time.monotonic()
        return all(now - since <= PAGE_STUCK_SECONDS for since in list(self._busy_since.values()))

    def recover(self) -> None:
        with self._lock:
            for meta in self.store.list(None):
                if meta["state"] == "running":
                    self.store.set_state(meta["id"], "queued")
            for meta in self._active():
                self._finish_if_complete(meta["id"], time.time())

    # ----- queue -----------------------------------------------------------

    def enqueue(self, job_id: str, pages: int) -> None:
        self.store.set_state(job_id, "queued", pages=pages)

    def cancel(self, job_id: str) -> None:
        with self._lock:
            meta = self.store.get(job_id)
            if meta is None or meta["state"] not in ACTIVE:
                return
            self.store.set_state(job_id, "cancelled", finished_at=time.time())
            self._seconds.pop(job_id, None)

    def _active(self) -> list[dict]:
        metas = [m for m in self.store.list(None) if m["state"] in ACTIVE]
        return sorted(metas, key=lambda m: m["created_at"])

    def claim(self) -> tuple[str, int] | None:
        with self._lock:
            active = self._active()
            n = len(active)
            for step in range(n):
                idx = (self._cursor + step) % n
                meta = active[idx]
                job_id = meta["id"]
                done = self.store.done_pages(job_id)
                pending = [
                    k for k in range(1, meta["pages"] + 1)
                    if k not in done and (job_id, k) not in self._claimed
                ]
                if not pending:
                    self._finish_if_complete(job_id, time.time())
                    continue
                number = pending[0]
                if meta["state"] == "queued":
                    self.store.set_state(job_id, "running", started_at=time.time())
                self._claimed.add((job_id, number))
                self._cursor = (idx + 1) % n
                return job_id, number
            return None

    def queue_position(self, job_id: str) -> int:
        with self._lock:
            me = self.store.get(job_id)
            if me is None or me["state"] not in ACTIVE:
                return 0
            return sum(1 for m in self._active() if m["created_at"] < me["created_at"])

    def eta_seconds(self, job_id: str) -> float | None:
        with self._lock:
            meta = self.store.get(job_id)
            if meta is None or meta["state"] not in ACTIVE:
                return None
            stats = self._seconds.get(job_id)
            if not stats or stats[1] == 0:
                return None
            remaining = meta["pages"] - len(self.store.done_pages(job_id))
            mean = stats[0] / stats[1]
            return remaining * mean * len(self._active()) / self.workers

    # ----- workers ---------------------------------------------------------

    def _report(self, what: str, error: BaseException) -> None:
        print(f"[ocr-worker] {what}: {error!r}", file=sys.stderr, flush=True)

    def _safely(self, what: str, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:
            self._report(what, e)

    def _run(self, index: int) -> None:
        # Nothing may escape this loop: a dead worker thread makes /health report 503 forever.
        engine = None
        load_failures = 0
        while not self._stop.is_set():
            try:
                if engine is None:
                    try:
                        engine = self.engine_factory()
                    except Exception as e:
                        load_failures += 1
                        delay = engine_backoff_seconds(load_failures)
                        self._report(f"engine load failed, retrying in {delay:g}s", e)
                        self._stop.wait(delay)
                        continue
                    load_failures = 0
                    self._ready[index] = True
                item = self.claim()
            except Exception as e:
                self._report("worker claim failed, retrying", e)
                self._stop.wait(BACKOFF_SECONDS)
                continue
            if item is None:
                self._stop.wait(BACKOFF_SECONDS / 2)
                continue
            self._busy_since[index] = time.monotonic()
            try:
                self._process(engine, *item)
                self._storage_failures.pop(item, None)
            except OSError as e:
                self._storage_failed(item, e)
                self._stop.wait(BACKOFF_SECONDS)
            except Exception as e:
                self._report(f"page {item[0]}#{item[1]} failed, will retry", e)
                self._stop.wait(BACKOFF_SECONDS)
            finally:
                self._busy_since.pop(index, None)
                with self._lock:
                    self._claimed.discard(item)

    def _storage_failed(self, item: tuple[str, int], error: OSError) -> None:
        """Count consecutive storage failures for one page; after the limit the job is failed."""
        job_id, number = item
        self._report(f"storage failure on page {number}", error)
        count = self._storage_failures.get(item, 0) + 1
        self._storage_failures[item] = count
        if count < STORAGE_FAILURE_LIMIT:
            return
        try:
            with self._lock:
                meta = self.store.get(job_id)
                if meta is not None and meta["state"] in ACTIVE:
                    self.store.set_state(job_id, "failed", error=STORAGE_FAILED_MESSAGE, finished_at=time.time())
                self._seconds.pop(job_id, None)
        except OSError as e:
            self._report("could not mark job failed, will try again", e)
            return  # keep the counter: the next failure tries again
        self._storage_failures.pop(item, None)

    def _process(self, engine, job_id: str, number: int) -> None:
        source = self.store.source_path(job_id)
        started = time.monotonic()
        result = jpeg = None
        error = None
        try:
            bgr = render_page(source, number - 1)
            out = engine.read(bgr)
            h, w = bgr.shape[:2]
            result = {
                "number": number,
                "width": w,
                "height": h,
                "seconds": 0.0,
                "text": out["text"],
                "lines": [
                    {
                        "text": line["text"],
                        "confidence": float(line["confidence"]),
                        "box": [[int(round(x)), int(round(y))] for x, y in line["box"]],
                    }
                    for line in out["lines"]
                ],
            }
            jpeg = thumbnail_jpeg(bgr)
        except Exception as e:
            error = str(e)
        seconds = time.monotonic() - started
        now = time.time()

        with self._lock:
            meta = self.store.get(job_id)
            if meta is None or meta["state"] == "cancelled":
                return  # deleted or cancelled mid-page: drop the result
            if error is None:
                result["seconds"] = seconds
                self.store.write_page(job_id, number, result, jpeg)
            else:
                self.store.write_page(
                    job_id, number, {"number": number, "error": error, "text": "", "lines": []}, None
                )
                self.store.set_state(
                    job_id, meta["state"], failed_pages=meta["failed_pages"] + [number]
                )
            stats = self._seconds.setdefault(job_id, [0.0, 0])
            stats[0] += seconds
            stats[1] += 1
            self._finish_if_complete(job_id, now)

        # Logging failures must never kill the worker or skip the accounting above.
        self._safely("event log page_done failed", self.events.page_done, job_id, number, seconds, error is None, now)
        if error is not None:
            self._safely("event log error failed", self.events.error, job_id, number, error, now)

    def _finish_if_complete(self, job_id: str, now: float) -> None:
        """Finalise an active job once every page has a result on disk.

        The outcome is read from the page files, not from meta, so a crash between the
        last write_page and the terminal set_state is repaired by recover() or claim().
        """
        meta = self.store.get(job_id)
        if meta is None or meta["state"] not in ACTIVE or meta["pages"] < 1:
            return
        if not set(range(1, meta["pages"] + 1)) <= self.store.done_pages(job_id):
            return
        results = [self.store.read_page(job_id, n) for n in range(1, meta["pages"] + 1)]
        if any(r is None for r in results):
            return  # deleted underneath us
        state = "failed" if all("error" in r for r in results) else "done"
        self.store.set_state(job_id, state, finished_at=now)
        self._seconds.pop(job_id, None)
