"""Background thread that summarizes finished OCR jobs and stores summary.json per job."""

import sys
import threading
import time

from server import summarizer
from server.llm import LLMUnavailable
from server.store import JobError

POLL_SECONDS = 5
UNAVAILABLE_BASE_SECONDS = 15
UNAVAILABLE_CAP_SECONDS = 300
ATTEMPT_LIMIT = 3
RETRY_STEP_SECONDS = 30
ERROR_CHARS = 300
STOP_TIMEOUT_SECONDS = 5
ACTIVE = ("pending", "running")
STATES = ("pending", "running", "done", "failed", "short")
DEFAULTS = {"attempts": 0, "waits": 0, "next_try_at": 0, "error": None, "progress": None, "result": None}


def _clip(error: BaseException) -> str:
    return (str(error) or type(error).__name__)[:ERROR_CHARS]


class SummaryWorker:
    """Summarizes one OCR-done job at a time. Writes go through store.write_summary."""

    def __init__(self, store, llm, model: str, now=time.time, poll_seconds: float = POLL_SECONDS, url: str = "",
                 llms: dict | None = None):
        self.store = store
        self.llm = llm
        self.llms = dict(llms or {})  # API key name -> client; a job picks one by its llm_key
        self.model = model
        self.url = url
        self._now = now
        self.poll_seconds = poll_seconds
        self._mu = threading.Lock()  # serializes state checks against state writes
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_ok_at: float | None = None
        self._last_error: str | None = None

    # ----- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.recover()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="summary-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        # Bounded: an in-flight LLM call can take minutes. The thread is a daemon, so
        # giving up on it here never keeps shutdown waiting on the model.
        self._stop.set()
        if self._thread is not None:
            self._thread.join(STOP_TIMEOUT_SECONDS)
            if self._thread.is_alive():
                self._report("still busy at shutdown, abandoning", RuntimeError("LLM call in flight"))
            else:
                self._thread = None

    def _report(self, what: str, error: BaseException) -> None:
        print(f"[summary-worker] {what}: {error!r}", file=sys.stderr, flush=True)

    def _safely_recover(self) -> None:
        try:
            self.recover()
        except Exception as e:
            self._report("recover failed", e)

    def _run(self) -> None:
        # Nothing may escape this loop: a dead worker thread silently stops all summaries.
        while not self._stop.is_set():
            try:
                worked = self.step(self._now())
            except Exception as e:
                self._report("step failed, will retry", e)
                worked = False
                self._safely_recover()
            if not worked:
                self._stop.wait(self.poll_seconds)

    # ----- record helpers --------------------------------------------------

    def _job_model(self, meta: dict) -> str:
        """The model this job is summarized with: its own choice, else the server default."""
        return meta.get("model") or self.model

    def _client_for(self, meta: dict):
        """The client for the job's API key; the default client when that key is not configured."""
        client = self.llms.get(meta.get("llm_key", "1"))
        return client if client is not None else self.llm

    def _llm_for(self, model: str, base):
        return base if model == self.model else base.with_model(model)

    def _put(self, job_id: str, now: float, prev: dict, model: str, **changes) -> dict | None:
        """Write a full record built from prev. Returns it, or None when the job is gone.

        The record names the API key only when it is not the default key "1".
        """
        record = {**DEFAULTS, **prev, **changes, "model": model, "updated_at": now}
        meta = self.store.get(job_id) or {}
        if meta.get("llm_key", "1") != "1":
            record["llm_key"] = meta["llm_key"]
        else:
            record.pop("llm_key", None)
        return record if self.store.write_summary(job_id, record) else None

    # ----- queue -----------------------------------------------------------

    def recover(self) -> None:
        with self._mu:
            now = self._now()
            for meta in self.store.list(None):
                rec = self.store.read_summary(meta["id"])
                if rec and rec.get("state") == "running":
                    self._put(meta["id"], now, rec, self._job_model(meta), state="pending")

    def scan(self, now: float) -> None:
        with self._mu:
            for meta in self.store.list(None):
                # has_summary, not read_summary: an unreadable record must not be reset.
                if meta["state"] == "done" and not self.store.has_summary(meta["id"]):
                    self._put(meta["id"], now, {}, self._job_model(meta),
                              state="pending", attempts=0, next_try_at=0)

    def next_job(self, now: float) -> str | None:
        candidates = []
        for meta in self.store.list(None):
            if meta["state"] != "done":
                continue
            rec = self.store.read_summary(meta["id"])
            if rec and rec.get("state") == "pending" and rec.get("next_try_at", 0) <= now:
                candidates.append((meta.get("finished_at") or 0, meta["id"]))
        return min(candidates)[1] if candidates else None

    def step(self, now: float) -> bool:
        self.scan(now)
        job_id = self.next_job(now)
        if job_id is None:
            return False
        self.run_one(job_id, now)
        return True

    # ----- one job ---------------------------------------------------------

    def run_one(self, job_id: str, now: float) -> None:
        meta = self.store.get(job_id)
        if meta is None:
            return
        model = self._job_model(meta)
        # A run can take many minutes. Times written during or after it (updated_at,
        # next_try_at, last_ok_at) count from `now` plus the time spent so far.
        started = time.monotonic()

        def clock() -> float:
            return now + (time.monotonic() - started)

        with self._mu:
            prev = self.store.read_summary(job_id) or {}
            # progress stays None until the first chunk: the chunk count is not known yet.
            if self._put(job_id, now, prev, model, state="running", progress=None) is None:
                return
        try:
            self._summarize_and_store(job_id, meta, prev, clock)
        except Exception as e:
            # Any failure after the running write must not leave the record stuck at running.
            self._report(f"summary of {job_id} interrupted, will retry", e)
            self._repend(job_id, clock(), prev, e, model)

    def _repend(self, job_id: str, now: float, prev: dict, error: BaseException, model: str) -> None:
        """Count the failure like any other error: retry with growing delay, fail at the limit."""
        attempts = prev.get("attempts", 0) + 1
        self._last_error = str(error)
        try:
            with self._mu:
                if attempts < ATTEMPT_LIMIT:
                    self._put(job_id, now, prev, model, state="pending", attempts=attempts,
                              next_try_at=now + RETRY_STEP_SECONDS * attempts,
                              error=_clip(error), progress=None)
                else:
                    self._put(job_id, now, prev, model, state="failed", attempts=attempts,
                              error=_clip(error), progress=None)
        except OSError as e:
            self._report("could not re-pend job, recover() will", e)

    def _summarize_and_store(self, job_id: str, meta: dict, prev: dict, clock) -> None:
        model = self._job_model(meta)
        llm = self._llm_for(model, self._client_for(meta))
        pages = []
        for number in range(1, meta["pages"] + 1):
            page = self.store.read_page(job_id, number)
            if page is not None:
                pages.append(page)

        def progress(done: int, total: int) -> None:
            self._put(job_id, clock(), prev, model, state="running", progress=[done, total])

        try:
            result = summarizer.summarize(llm, pages, meta["filename"], on_progress=progress,
                                          page_count=meta["pages"])
        except summarizer.ShortText:
            now = clock()
            self._put(job_id, now, prev, model, state="short", result=None, progress=None)
        except LLMUnavailable as e:
            now = clock()
            waits = prev.get("waits", 0) + 1
            delay = min(UNAVAILABLE_CAP_SECONDS, UNAVAILABLE_BASE_SECONDS * 2 ** (waits - 1))
            self._last_error = str(e)
            self._put(job_id, now, prev, model, state="pending", waits=waits,
                      next_try_at=now + delay, error=_clip(e), progress=None)
        except Exception as e:
            now = clock()
            attempts = prev.get("attempts", 0) + 1
            self._last_error = str(e)
            if attempts < ATTEMPT_LIMIT:
                self._put(job_id, now, prev, model, state="pending", attempts=attempts, waits=0,
                          next_try_at=now + RETRY_STEP_SECONDS * attempts, error=_clip(e), progress=None)
            else:
                self._put(job_id, now, prev, model, state="failed", attempts=attempts, waits=0,
                          error=_clip(e), progress=None)
        else:
            now = clock()
            self._put(job_id, now, prev, model, state="done", result=result, waits=0, error=None, progress=None)
            self._last_ok_at = now
            self._last_error = None

    # ----- API support -----------------------------------------------------

    def regenerate(self, job_id: str, now: float) -> dict:
        with self._mu:
            meta = self.store.get(job_id)
            if meta is None:
                raise JobError(404, "ไม่พบงาน")
            rec = self.store.read_summary(job_id)
            if rec and rec.get("state") in ACTIVE:
                raise JobError(409, "กำลังสรุปอยู่")
            if meta["state"] != "done":
                raise JobError(409, "งานยังอ่านไม่เสร็จ")
            record = self._put(job_id, now, {}, self._job_model(meta), state="pending", attempts=0, waits=0,
                               next_try_at=0, error=None, result=None, progress=None)
        if record is None:
            raise JobError(404, "ไม่พบงาน")
        return record

    def status(self) -> dict:
        counts = {state: 0 for state in STATES}
        for meta in self.store.list(None):
            rec = self.store.read_summary(meta["id"])
            if rec and rec.get("state") in counts:
                counts[rec["state"]] += 1
        return {
            "enabled": True,
            "url": self.url,
            "model": self.model,
            "last_ok_at": self._last_ok_at,
            "last_error": self._last_error,
            "counts": counts,
        }
