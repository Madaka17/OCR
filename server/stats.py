import json
import os
import tempfile
import threading
from pathlib import Path

import psutil

HOUR = 3600
DAY = 86400
GB = 1024**3
ERRORS_LIMIT = 50
LOG_MAX_AGE_SECONDS = 8 * DAY


class EventLog:
    """Append-only JSON-lines logs: events.jsonl (page timings) and errors.jsonl (page failures)."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.Lock()

    def _append(self, name: str, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with open(self.root / name, "a", encoding="utf-8") as f:
                f.write(line)

    def page_done(self, job_id: str, number: int, seconds: float, ok: bool, now: float) -> None:
        self._append(
            "events.jsonl",
            {"ts": now, "job": job_id, "page": number, "seconds": seconds, "ok": ok},
        )

    def error(self, job_id: str, number: int, message: str, now: float) -> None:
        self._append(
            "errors.jsonl",
            {"ts": now, "job": job_id, "page": number, "message": message},
        )

    def prune(self, now: float, max_age: float = LOG_MAX_AGE_SECONDS) -> None:
        """Rewrite both logs without lines older than max_age (atomic replace, under the append lock)."""
        cutoff = now - max_age
        with self._lock:
            for name in ("events.jsonl", "errors.jsonl"):
                path = self.root / name
                if not path.is_file():
                    continue
                kept = []
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and float(record.get("ts", 0)) >= cutoff:
                            kept.append(line if line.endswith("\n") else line + "\n")
                fd, tmp = tempfile.mkstemp(dir=self.root, prefix=name, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.writelines(kept)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(tmp, path)


def _read_jsonl(path: Path) -> list[dict]:
    """Every well-formed object in the file; a truncated or garbled line is skipped."""
    if not path.is_file():
        return []
    records = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def summary(root: Path, now: float, jobs=()) -> dict:
    """Dashboard numbers from events.jsonl plus the job metas (for jobs_per_day by created_at)."""
    events = _read_jsonl(Path(root) / "events.jsonl")
    pages_per_hour = [0] * 24
    jobs_per_day = [0] * 7
    week_ok_seconds = []
    pages_7d = failed_7d = 0

    for ev in events:
        age = now - float(ev.get("ts", 0))
        if age < 0:
            continue
        if age < 24 * HOUR:
            pages_per_hour[23 - int(age // HOUR)] += 1
        if age < 7 * DAY:
            pages_7d += 1
            if ev.get("ok"):
                week_ok_seconds.append(float(ev.get("seconds", 0.0)))
            else:
                failed_7d += 1

    for job in jobs:
        age = now - float(job["created_at"])
        if 0 <= age < 7 * DAY:
            jobs_per_day[6 - int(age // DAY)] += 1

    return {
        "pages_per_hour": pages_per_hour,
        "jobs_per_day": jobs_per_day,
        "avg_seconds_per_page": (sum(week_ok_seconds) / len(week_ok_seconds)) if week_ok_seconds else None,
        "pages_7d": pages_7d,
        "failed_pages_7d": failed_7d,
    }


def recent_errors(root: Path, limit: int = ERRORS_LIMIT) -> list[dict]:
    """Newest first. errors.jsonl is append-only, so reverse file order is reverse time order."""
    return _read_jsonl(Path(root) / "errors.jsonl")[::-1][:limit]


def _existing_ancestor(path: Path) -> Path:
    path = Path(path).resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _dir_bytes(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return total
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return total


def resources(data_dir: Path) -> dict:
    vm = psutil.virtual_memory()
    return {
        "cpu_percent": float(psutil.cpu_percent(interval=None)),
        "ram_used_gb": round(vm.used / GB, 2),
        "ram_total_gb": round(vm.total / GB, 2),
        "disk_free_gb": round(psutil.disk_usage(str(_existing_ancestor(data_dir))).free / GB, 2),
        "data_dir_gb": round(_dir_bytes(Path(data_dir)) / GB, 2),
    }
