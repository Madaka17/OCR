"""HTTP API, login, and static file serving for the OCR job server.

Built on http.server like web_app.py. Every response carries the COOP/COEP headers
so the browser OCR path can use SharedArrayBuffer.
"""
import json
import re
import shutil
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from server import settings
from server.limiter import LoginLimiter
from server.pages import count_pages
from server.stats import _existing_ancestor, recent_errors, resources, summary
from server.store import JobError

SESSION_COOKIE = "ocr_session"
SESSION_MAX_AGE = 30 * 86400
FAILED_LOGIN_DELAY = 1.0
MAX_JSON_BODY = 64 * 1024
MAX_DRAIN_BYTES = 200 * 1024 * 1024
DRAIN_CHUNK_BYTES = 64 * 1024
DISK_HEADROOM_BYTES = 1024**3  # free space kept back on top of twice the upload size
PUBLIC_STATIC = ("/login.html",)
ADMIN_PAGE_PATHS = ("/admin", "/admin.html")

MSG_NOT_FOUND = "ไม่พบหน้านี้"
MSG_LOGIN_REQUIRED = "กรุณาเข้าสู่ระบบ"
MSG_BAD_REQUEST = "คำขอไม่ถูกต้อง"
MSG_NO_BODY = "ไม่มีข้อมูลในคำขอ"
MSG_WRONG_PASSWORD = "รหัสผ่านไม่ถูกต้อง"
MSG_TOO_MANY_LOGINS = "ลองผิดหลายครั้งเกินไป กรุณารอ 15 นาที"
MSG_FORBIDDEN = "ไม่มีสิทธิ์เข้าถึงงานนี้"
MSG_JOB_NOT_FOUND = "ไม่พบงาน"
MSG_NOT_UPLOADING = "งานนี้ไม่อยู่ในสถานะอัปโหลด"
MSG_CHUNK_TOO_BIG = "ชิ้นไฟล์ใหญ่เกินกำหนด"
MSG_TOO_MANY_PAGES = "PDF มีมากเกิน 1000 หน้า"
MSG_DISK_FULL = "พื้นที่ดิสก์บนเซิร์ฟเวอร์ไม่พอ"
MSG_SUMMARY_OFF = "ไม่ได้เปิดใช้การสรุปด้วย AI"
MSG_SERVER_ERROR = "เกิดข้อผิดพลาดภายในเซิร์ฟเวอร์"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css; charset=utf-8",
    ".wasm": "application/wasm",
    ".onnx": "application/octet-stream",
    ".bin": "application/octet-stream",
    ".json": "application/json",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}

_JOB = r"/api/jobs/([^/]+)"


def _norm(path: str) -> str:
    """Lower-case, percent-decoded path without trailing dots or spaces: how Windows reads a name."""
    return urllib.parse.unquote(path).lower().rstrip(". ")


def _unsafe_name(path: str) -> bool:
    """Windows drops trailing dots and spaces and treats ':' as a stream separator, so such
    spellings can reach a real file under another name. Refuse them."""
    name = urllib.parse.unquote(path)
    return any(part.endswith((".", " ")) or ":" in part or "\\" in part for part in name.split("/"))


class Handler(BaseHTTPRequestHandler):
    server_version = "ThaiOCR/2.0"
    timeout = 120  # seconds a client may stay silent before its connection is closed

    def log_message(self, format, *args):
        print(format % args, file=sys.stderr)

    def end_headers(self):
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        super().end_headers()

    # ----- plumbing --------------------------------------------------------

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        try:
            self._respond(method)
        except OSError:
            # Includes TimeoutError: a stalled or vanished client just loses its connection.
            self.close_connection = True

    def _respond(self, method):
        self._pending = self._content_length()
        self._drain_allowed = False
        try:
            self._dispatch(method, urllib.parse.urlsplit(self.path).path)
        except JobError as e:
            self._json(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 - never leak a traceback to the client
            print(f"[http] {method} {self.path} failed: {e!r}", file=sys.stderr, flush=True)
            self._json(500, {"error": MSG_SERVER_ERROR})
        finally:
            self._finish_body()

    def _content_length(self) -> int:
        try:
            return max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            return 0

    def _read_body(self) -> bytes:
        """Read the whole request body (once)."""
        n = self._pending
        self._pending = 0
        return self.rfile.read(n) if n > 0 else b""

    def _finish_body(self) -> None:
        """Deal with a request body the handler did not read.

        Only the authenticated 413 chunk path drains it, so the browser can read the 413.
        Everywhere else the body is never read: the connection is closed instead, which keeps
        an anonymous client from making the server read up to 200 MB.
        """
        if self._pending <= 0:
            return
        if self._drain_allowed:
            self._drain()
        else:
            self.close_connection = True
        self._pending = 0

    def _drain(self) -> None:
        remaining = min(self._pending, MAX_DRAIN_BYTES)
        self._pending = 0
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(DRAIN_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    def _send(self, status, body: bytes, content_type: str, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status, payload, headers=()):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, data, "application/json; charset=utf-8", headers)

    def _redirect(self, location: str):
        self._send(302, b"", "text/plain; charset=utf-8", [("Location", location)])

    def _read_json(self) -> dict:
        if self._pending <= 0 or self._pending > MAX_JSON_BODY:
            raise JobError(400, MSG_BAD_REQUEST)
        try:
            data = json.loads(self._read_body().decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise JobError(400, MSG_BAD_REQUEST)
        if not isinstance(data, dict):
            raise JobError(400, MSG_BAD_REQUEST)
        return data

    def _cookie_value(self) -> str | None:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return value
        return None

    def _session(self) -> dict | None:
        return self.server.auth.session(self._cookie_value())

    def _secure(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").lower() == "https" or "CF-Connecting-IP" in self.headers

    def _client_key(self) -> str:
        """Who a login failure is counted against: the Cloudflare client IP, else the socket peer."""
        return self.headers.get("CF-Connecting-IP", "").strip() or self.client_address[0]

    def _cookie_header(self, value: str, max_age: int) -> tuple[str, str]:
        parts = [f"{SESSION_COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self._secure():
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    def _public(self, meta: dict, session: dict) -> dict:
        store, scheduler = self.server.store, self.server.scheduler
        job_id = meta["id"]
        out = {k: v for k, v in meta.items() if k != "owner"}
        out["mine"] = meta["owner"] == session["owner"]
        out["done_pages"] = len(store.done_pages(job_id))
        out["position"] = scheduler.queue_position(job_id)
        out["eta_seconds"] = scheduler.eta_seconds(job_id)
        if self.server.summaries is not None:
            out["summary_state"] = (store.read_summary(job_id) or {}).get("state")
        return out

    def _job_or_404(self, job_id: str) -> dict:
        meta = self.server.store.get(job_id)
        if meta is None:
            raise JobError(404, MSG_JOB_NOT_FOUND)
        return meta

    def _owned_job(self, session: dict, job_id: str) -> dict:
        meta = self._job_or_404(job_id)
        if meta["owner"] != session["owner"]:
            raise JobError(403, MSG_FORBIDDEN)
        return meta

    # ----- dispatch --------------------------------------------------------

    def _dispatch(self, method, path):
        if method == "GET" and path == "/health":
            return self._health()
        if method == "GET" and path in PUBLIC_STATIC:
            return self._static(path, public=True)
        if method == "POST" and path == "/api/login":
            return self._login()
        if method == "POST" and path == "/api/logout":
            return self._logout()
        if method == "GET" and _norm(path) in ADMIN_PAGE_PATHS:
            return self._admin_page()
        if path.startswith("/api/"):
            return self._api(method, path)
        if method == "GET":
            return self._static(path)
        return self._not_found()

    def _not_found(self):
        self._json(404, {"error": MSG_NOT_FOUND})

    def _unauthorized(self):
        self._json(401, {"error": MSG_LOGIN_REQUIRED})

    def _health(self):
        if self.server.scheduler.alive():
            return self._json(200, {"ok": True})
        return self._json(503, {"ok": False})

    def _login(self):
        body = self._read_json()
        password = body.get("password")
        if not isinstance(password, str):
            raise JobError(400, MSG_BAD_REQUEST)
        key = self._client_key()
        limiter = self.server.limiter
        if not limiter.allowed(key):
            return self._json(429, {"error": MSG_TOO_MANY_LOGINS})
        result = self.server.auth.login(password, self._cookie_value())
        if result is None:
            limiter.record_failure(key)
            time.sleep(FAILED_LOGIN_DELAY)
            return self._json(401, {"error": MSG_WRONG_PASSWORD})
        limiter.record_success(key)
        value, session = result
        return self._json(200, {"role": session["role"]},
                          headers=[self._cookie_header(value, SESSION_MAX_AGE)])

    def _logout(self):
        return self._json(200, {"ok": True}, headers=[self._cookie_header("", 0)])

    def _admin_page(self):
        session = self._session()
        if session is None or session["role"] != "admin":
            return self._redirect("/login.html?admin=1")
        return self._static("/admin.html")

    def _static(self, path, public=False):
        if not public and self._session() is None:
            if self._wants_html(path):
                return self._redirect("/login.html")
            return self._unauthorized()
        if _unsafe_name(path):
            return self._not_found()
        if path == "/":
            path = "/index.html"
        root = Path(self.server.site_dir).resolve()
        try:
            target = (root / urllib.parse.unquote(path).lstrip("/")).resolve()
            if not target.is_relative_to(root) or not target.is_file():
                return self._not_found()
            data = target.read_bytes()
        except (ValueError, OSError):
            return self._not_found()
        content_type = CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        return self._send(200, data, content_type)

    def _wants_html(self, path: str) -> bool:
        return "text/html" in self.headers.get("Accept", "") or path == "/" or path.endswith(".html")

    def _api(self, method, path):
        for route_method, pattern, handler in self._routes():
            if route_method != method:
                continue
            match = pattern.fullmatch(path)
            if match is None:
                continue
            session = self._session()
            if session is None:
                return self._unauthorized()
            return handler(session, *match.groups())
        return self._not_found()

    def _routes(self):
        return [
            ("GET", re.compile(r"/api/me"), self._me),
            ("GET", re.compile(r"/api/jobs"), self._list_jobs),
            ("POST", re.compile(r"/api/jobs"), self._create_job),
            ("GET", re.compile(_JOB), self._get_job),
            ("DELETE", re.compile(_JOB), self._delete_job),
            ("PUT", re.compile(_JOB + r"/chunks/(\d+)"), self._put_chunk),
            ("POST", re.compile(_JOB + r"/finish"), self._finish_job),
            ("GET", re.compile(_JOB + r"/result"), self._result),
            ("GET", re.compile(_JOB + r"/pages/(\d+)\.jpg"), self._page_jpg),
            ("GET", re.compile(_JOB + r"/text"), self._text),
            ("GET", re.compile(_JOB + r"/summary"), self._get_summary),
            ("POST", re.compile(_JOB + r"/summary"), self._post_summary),
            ("GET", re.compile(r"/api/admin/overview"), self._admin_overview),
            ("DELETE", re.compile(r"/api/admin/jobs/([^/]+)"), self._admin_delete_job),
        ]

    # ----- API handlers ----------------------------------------------------

    def _me(self, session):
        return self._json(200, {"role": session["role"]})

    def _list_jobs(self, session):
        metas = self.server.store.list(session["owner"])
        return self._json(200, [self._public(m, session) for m in metas])

    def _create_job(self, session):
        body = self._read_json()
        filename = body.get("filename")
        size = body.get("size")
        if not isinstance(filename, str) or not isinstance(size, int) or isinstance(size, bool):
            raise JobError(400, MSG_BAD_REQUEST)
        free = shutil.disk_usage(_existing_ancestor(self.server.store.root)).free
        if free < size * 2 + DISK_HEADROOM_BYTES:
            raise JobError(507, MSG_DISK_FULL)
        meta = self.server.store.create(session["owner"], filename, size, now=time.time())
        return self._json(201, {"job": self._public(meta, session), "chunk_size": settings.CHUNK_BYTES})

    def _get_job(self, session, job_id):
        return self._json(200, self._public(self._job_or_404(job_id), session))

    def _delete_job(self, session, job_id):
        meta = self._job_or_404(job_id)
        if meta["owner"] != session["owner"] and session["role"] != "admin":
            raise JobError(403, MSG_FORBIDDEN)
        self.server.scheduler.cancel(job_id)
        self.server.store.delete(job_id)
        return self._json(200, {"ok": True})

    def _put_chunk(self, session, job_id, index):
        self._owned_job(session, job_id)
        if self._pending <= 0:
            raise JobError(400, MSG_NO_BODY)
        if self._pending > settings.CHUNK_BYTES:
            self._drain_allowed = True  # the browser reads this 413 before the rest is drained
            raise JobError(413, MSG_CHUNK_TOO_BIG)
        data = self._read_body()
        self.server.store.put_chunk(job_id, int(index), data, now=time.time())
        return self._json(200, {"ok": True})

    def _finish_job(self, session, job_id):
        meta = self._owned_job(session, job_id)
        if meta["state"] == "cancelled":
            raise JobError(409, MSG_NOT_UPLOADING)
        if meta["state"] != "uploading":
            # A repeated finish (a retried request) changes nothing and returns the job.
            return self._json(200, self._public(meta, session))
        store, scheduler = self.server.store, self.server.scheduler
        source = store.assemble(job_id)
        try:
            pages = count_pages(source)
        except JobError:
            store.delete(job_id)
            raise
        if pages > settings.MAX_JOB_PAGES:
            scheduler.cancel(job_id)
            store.delete(job_id)
            raise JobError(400, MSG_TOO_MANY_PAGES)
        scheduler.enqueue(job_id, pages)
        return self._json(200, self._public(store.get(job_id), session))

    def _result(self, session, job_id):
        meta = self._job_or_404(job_id)
        store = self.server.store
        pages = []
        for number in sorted(store.done_pages(job_id)):
            page = store.read_page(job_id, number)
            if page is None:
                continue
            if "error" not in page:
                page["image"] = f"/api/jobs/{job_id}/pages/{number}.jpg"
            pages.append(page)
        return self._json(200, {"filename": meta["filename"], "text": store.text(job_id), "pages": pages})

    def _get_summary(self, session, job_id):
        self._job_or_404(job_id)
        if self.server.summaries is None:
            return self._json(200, {"enabled": False})
        record = self.server.store.read_summary(job_id) or {"state": None}
        return self._json(200, {"enabled": True, **record})

    def _post_summary(self, session, job_id):
        if self.server.summaries is None:
            raise JobError(503, MSG_SUMMARY_OFF)
        return self._json(202, self.server.summaries.regenerate(job_id, time.time()))

    def _page_jpg(self, session, job_id, number):
        self._job_or_404(job_id)
        path = self.server.store.jobs_dir / job_id / "pages" / f"{int(number)}.jpg"
        if not path.is_file():
            raise JobError(404, MSG_NOT_FOUND)
        return self._send(200, path.read_bytes(), "image/jpeg",
                          headers=[("Cache-Control", "private, max-age=604800")])

    def _require_admin(self, session):
        if session["role"] != "admin":
            raise JobError(403, MSG_FORBIDDEN)

    def _admin_overview(self, session):
        self._require_admin(session)
        store, events = self.server.store, self.server.events
        scheduler = self.server.scheduler
        now = time.time()
        metas = store.list(None)
        names = {m["id"]: m["filename"] for m in metas}
        errors = [
            {**rec, "file": names.get(rec.get("job"), rec.get("job"))}
            for rec in recent_errors(events.root)
        ]
        summaries = self.server.summaries
        return self._json(200, {
            "jobs": [self._public(m, session) for m in metas],
            "stats": summary(events.root, now, jobs=metas),
            "errors": errors,
            "resources": resources(store.root),
            "workers": {"count": scheduler.workers, "alive": scheduler.alive()},
            "llm": summaries.status() if summaries is not None else {"enabled": False},
        })

    def _admin_delete_job(self, session, job_id):
        self._require_admin(session)
        return self._delete_job(session, job_id)

    def _text(self, session, job_id):
        meta = self._job_or_404(job_id)
        body = self.server.store.text(job_id).encode("utf-8")
        name = urllib.parse.quote(meta["filename"] + ".txt", safe="")
        return self._send(200, body, "text/plain; charset=utf-8",
                          headers=[("Content-Disposition", f"attachment; filename*=UTF-8''{name}")])


def make_server(host, port, store, scheduler, auth, events, site_dir=None, summaries=None) -> ThreadingHTTPServer:
    """Create (but do not start) the HTTP server. site_dir defaults to settings.SITE_DIR at call time."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.store = store
    httpd.scheduler = scheduler
    httpd.auth = auth
    httpd.events = events
    httpd.limiter = LoginLimiter()
    httpd.site_dir = Path(site_dir if site_dir is not None else settings.SITE_DIR)
    httpd.summaries = summaries
    return httpd


def start_cleanup(store, events=None, interval=3600):
    """Every `interval` seconds on a daemon thread: remove expired jobs and prune old log lines."""

    def loop():
        while True:
            now = time.time()
            try:
                store.cleanup(now)
            except Exception as e:  # noqa: BLE001 - cleanup must keep running
                print(f"[cleanup] failed: {e!r}", file=sys.stderr, flush=True)
            if events is not None:
                try:
                    events.prune(now)
                except Exception as e:  # noqa: BLE001 - pruning must keep running
                    print(f"[cleanup] log prune failed: {e!r}", file=sys.stderr, flush=True)
            time.sleep(interval)

    thread = threading.Thread(target=loop, name="cleanup", daemon=True)
    thread.start()
    return thread
