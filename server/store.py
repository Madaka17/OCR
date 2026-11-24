import json
import math
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from server import settings


_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
TERMINAL_STATES = ("done", "failed", "cancelled")
ORPHAN_SECONDS = 3600  # job dirs without meta.json are removed once this old


def _valid_id(job_id: str) -> bool:
    return isinstance(job_id, str) and _JOB_ID_RE.fullmatch(job_id) is not None


class JobError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# On Windows, os.replace fails with PermissionError while another thread has the
# target open (a status poll reading summary.json or meta.json). The reader holds it
# for well under a millisecond, so a few short retries ride it out. Elsewhere a
# PermissionError is a real permission problem, so it is raised at once.
SHARING_RETRIES = 10 if os.name == "nt" else 1
SHARING_RETRY_SECONDS = 0.01


def _retry_sharing(fn, *args):
    for attempt in range(SHARING_RETRIES):
        try:
            return fn(*args)
        except PermissionError:
            if attempt == SHARING_RETRIES - 1:
                raise
            time.sleep(SHARING_RETRY_SECONDS * (attempt + 1))


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _retry_sharing(os.replace, tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _chunk_count(size: int) -> int:
    return math.ceil(size / settings.CHUNK_BYTES)


class JobStore:
    """On-disk job store: one directory per job under <root>/jobs/<id>/."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.jobs_dir = self.root / "jobs"
        self._lock = threading.Lock()
        self._reported: set[str] = set()  # unreadable paths already logged
        self._checked: set[str] = set()  # page files already verified as valid JSON

    def _job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def source_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "source.bin"

    def _report_unreadable(self, path: Path, error: BaseException) -> None:
        if str(path) in self._reported:
            return
        self._reported.add(str(path))
        print(f"[store] unreadable {path}: {error!r}", file=sys.stderr, flush=True)

    def _read_json_file(self, path: Path) -> dict | None:
        """Parsed JSON object, or None when the file is missing or unreadable."""
        try:
            return json.loads(_retry_sharing(path.read_text, "utf-8"))
        except FileNotFoundError:
            return None
        except (ValueError, OSError) as e:
            self._report_unreadable(path, e)
            return None

    def _read_meta(self, job_id: str) -> dict | None:
        if not _valid_id(job_id):
            return None
        return self._read_json_file(self._job_dir(job_id) / "meta.json")

    def _save_meta(self, meta: dict) -> None:
        data = json.dumps(meta, ensure_ascii=False).encode("utf-8")
        _atomic_write(self._job_dir(meta["id"]) / "meta.json", data)

    def create(self, owner: str, filename: str, size: int, now: float, model: str | None = None) -> dict:
        if size <= 0:
            raise JobError(400, "ไฟล์ว่างเปล่า")
        if size > settings.MAX_JOB_BYTES:
            raise JobError(400, "ไฟล์ใหญ่เกิน 500 MB")
        job_id = secrets.token_urlsafe(16)
        meta = {
            "id": job_id,
            "owner": owner,
            "filename": filename,
            "size": size,
            "state": "uploading",
            "pages": 0,
            "created_at": now,
            "last_chunk_at": now,
            "started_at": None,
            "finished_at": None,
            "failed_pages": [],
            "error": None,
        }
        if model is not None:
            meta["model"] = model
        with self._lock:
            (self._job_dir(job_id) / "upload").mkdir(parents=True)
            self._save_meta(meta)
        return meta

    def put_chunk(self, job_id: str, index: int, data: bytes, now: float) -> None:
        with self._lock:
            meta = self._read_meta(job_id)
            if meta is None:
                raise JobError(404, "ไม่พบงาน")
            if meta["state"] != "uploading":
                raise JobError(409, "งานนี้รับไฟล์เพิ่มไม่ได้แล้ว")
            if not 0 <= index < _chunk_count(meta["size"]):
                raise JobError(400, "ลำดับชิ้นไฟล์ไม่ถูกต้อง")
            if len(data) > settings.CHUNK_BYTES:
                raise JobError(400, "ชิ้นไฟล์ใหญ่เกินกำหนด")
            _atomic_write(self._job_dir(job_id) / "upload" / f"{index}.part", data)
            meta["last_chunk_at"] = now
            self._save_meta(meta)

    def assemble(self, job_id: str) -> Path:
        with self._lock:
            meta = self._read_meta(job_id)
            if meta is None:
                raise JobError(404, "ไม่พบงาน")
            job_dir = self._job_dir(job_id)
            upload = job_dir / "upload"
            source = self.source_path(job_id)
            if not upload.exists() and source.is_file():
                return source  # an earlier finish assembled it and died before enqueueing
            parts = [upload / f"{i}.part" for i in range(_chunk_count(meta["size"]))]
            if not all(p.exists() for p in parts):
                raise JobError(400, "อัปโหลดไม่ครบ")
            total = sum(p.stat().st_size for p in parts)
            if total != meta["size"]:
                raise JobError(400, "อัปโหลดไม่ครบ")
            fd, tmp = tempfile.mkstemp(dir=job_dir, prefix="source.bin", suffix=".tmp")
            with os.fdopen(fd, "wb") as out:
                for p in parts:
                    out.write(p.read_bytes())
            os.replace(tmp, source)
            shutil.rmtree(upload)
            return source

    def set_state(self, job_id: str, state: str, **fields) -> None:
        with self._lock:
            meta = self._read_meta(job_id)
            if meta is None:
                return
            meta["state"] = state
            meta.update(fields)
            self._save_meta(meta)
            if state in TERMINAL_STATES:
                # Results and thumbnails stay; the (large) uploaded source is no longer needed.
                try:
                    self.source_path(job_id).unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    print(f"[store] could not remove source of {job_id}: {e!r}", file=sys.stderr, flush=True)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._read_meta(job_id)

    def list(self, owner: str | None) -> list[dict]:
        with self._lock:
            metas = [m for m in self._all_metas() if owner is None or m["owner"] == owner]
        return sorted(metas, key=lambda m: m["created_at"], reverse=True)

    def _all_metas(self) -> list[dict]:
        if not self.jobs_dir.exists():
            return []
        metas = []
        for job_dir in self.jobs_dir.iterdir():
            meta = self._read_meta(job_dir.name)
            if meta is not None:
                metas.append(meta)
        return metas

    def write_page(self, job_id: str, number: int, result: dict, jpeg: bytes | None) -> None:
        with self._lock:
            job_dir = self._job_dir(job_id)
            if not _valid_id(job_id) or not job_dir.exists():
                return
            pages = job_dir / "pages"
            pages.mkdir(exist_ok=True)
            json_path = pages / f"{number}.json"
            _atomic_write(json_path, json.dumps(result, ensure_ascii=False).encode("utf-8"))
            self._checked.discard(str(json_path))
            if jpeg is not None:
                _atomic_write(pages / f"{number}.jpg", jpeg)

    def read_page(self, job_id: str, number: int) -> dict | None:
        if not _valid_id(job_id):
            return None
        return self._read_json_file(self._job_dir(job_id) / "pages" / f"{number}.json")

    def read_summary(self, job_id: str) -> dict | None:
        if not _valid_id(job_id):
            return None
        return self._read_json_file(self._job_dir(job_id) / "summary.json")

    def has_summary(self, job_id: str) -> bool:
        """True when summary.json exists, even if it cannot be read right now."""
        return _valid_id(job_id) and (self._job_dir(job_id) / "summary.json").exists()

    def write_summary(self, job_id: str, data: dict) -> bool:
        """Atomically write summary.json. Writes nothing when the job's meta.json is gone."""
        with self._lock:
            if not _valid_id(job_id) or self._read_meta(job_id) is None:
                return False
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            _atomic_write(self._job_dir(job_id) / "summary.json", payload)
            return True

    def _page_ok(self, path: Path) -> bool:
        """A page file counts only if it parses. A file that does not parse is deleted so
        the page is produced again; a file that merely cannot be read is left alone."""
        key = str(path)
        if key in self._checked:
            return True
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except ValueError as e:
            self._report_unreadable(path, e)
            try:
                path.unlink()
            except OSError:
                pass
            return False
        except OSError as e:
            self._report_unreadable(path, e)
            return False
        self._checked.add(key)
        return True

    def done_pages(self, job_id: str) -> set[int]:
        if not _valid_id(job_id):
            return set()
        pages = self._job_dir(job_id) / "pages"
        if not pages.exists():
            return set()
        return {int(p.stem) for p in pages.glob("*.json") if p.stem.isdigit() and self._page_ok(p)}

    def text(self, job_id: str) -> str:
        texts = []
        for number in sorted(self.done_pages(job_id)):
            result = self.read_page(job_id, number)
            if result is None:  # job deleted or page removed while reading
                continue
            texts.append("" if "error" in result else result.get("text", ""))
        return "\n\n".join(texts)

    def _forget(self, job_id: str) -> None:
        prefix = str(self._job_dir(job_id)) + os.sep
        self._checked = {key for key in self._checked if not key.startswith(prefix)}

    def delete(self, job_id: str) -> None:
        if not _valid_id(job_id):
            return
        with self._lock:
            self._forget(job_id)
            shutil.rmtree(self._job_dir(job_id), ignore_errors=True)

    def cleanup(self, now: float) -> list[str]:
        removed = []
        with self._lock:
            for meta in self._all_metas():
                expired = now - meta["created_at"] >= settings.RETENTION_SECONDS
                stale = (
                    meta["state"] == "uploading"
                    and now - meta["last_chunk_at"] >= settings.STALE_UPLOAD_SECONDS
                )
                if expired or stale:
                    self._forget(meta["id"])
                    shutil.rmtree(self._job_dir(meta["id"]), ignore_errors=True)
                    removed.append(meta["id"])
            removed += self._remove_orphans(now)
        return removed

    def _remove_orphans(self, now: float) -> list[str]:
        """Job directories with no meta.json (interrupted create) that are over an hour old."""
        removed = []
        if not self.jobs_dir.exists():
            return removed
        for job_dir in self.jobs_dir.iterdir():
            if not job_dir.is_dir() or (job_dir / "meta.json").exists():
                continue
            try:
                age = now - job_dir.stat().st_mtime
            except OSError:
                continue
            if age >= ORPHAN_SECONDS:
                self._forget(job_dir.name)
                shutil.rmtree(job_dir, ignore_errors=True)
                removed.append(job_dir.name)
        return removed
