import http.client
import json
import threading
import time
import urllib.parse
from types import SimpleNamespace

import pytest
from PIL import Image

from server import settings
from server import app as app_mod
from server import scheduler as sched_mod
from server.app import make_server, start_cleanup
from server.auth import Auth
from server.llm import LLMUnavailable
from server.models import ModelCatalog
from server.scheduler import Scheduler
from server.stats import EventLog
from server.store import JobStore
from server.summary_worker import SummaryWorker


class FakeEngine:
    def read(self, bgr):
        h, w = bgr.shape[:2]
        return {
            "text": f"{h}x{w}",
            "lines": [{"text": f"{h}x{w}", "confidence": 0.5, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]}],
        }


def sized_pdf(path, n_pages):
    """Page k (1-based) is (100k, 50+25k) px at 200 dpi (same geometry as test_scheduler)."""
    images = [Image.new("RGB", (100 * k, 50 + 25 * k), "white") for k in range(1, n_pages + 1)]
    images[0].save(path, save_all=True, append_images=images[1:], resolution=200)
    return path


def expected_text(n):
    return f"{50 + 25 * n}x{100 * n}"


class Response:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    def header(self, name):
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None

    def json(self):
        return json.loads(self.body.decode("utf-8"))


class Client:
    def __init__(self, port):
        self.port = port
        self.cookie = None

    def request(self, method, path, body=None, headers=None, use_cookie=True):
        hdrs = dict(headers or {})
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if use_cookie and self.cookie:
            hdrs["Cookie"] = f"ocr_session={self.cookie}"
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path, body=body, headers=hdrs)
            resp = conn.getresponse()
            return Response(resp.status, resp.getheaders(), resp.read())
        finally:
            conn.close()

    def login(self, password, headers=None):
        r = self.request("POST", "/api/login", {"password": password}, headers=headers)
        if r.status == 200:
            self.cookie = r.header("Set-Cookie").split(";", 1)[0].split("=", 1)[1]
        return r

    def fresh(self):
        return Client(self.port)


@pytest.fixture
def env(tmp_path):
    yield from serve_env(tmp_path, llm=False)


@pytest.fixture
def env_llm(tmp_path):
    yield from serve_env(tmp_path, llm=True)


class FakeModelsLLM:
    def list_models(self):
        return ["model-a", "model-b"]


@pytest.fixture
def env_models(tmp_path):
    yield from serve_env(tmp_path, llm=False, models=ModelCatalog(FakeModelsLLM(), "model-a"))


class DownModelsLLM:
    def list_models(self):
        raise LLMUnavailable("down")


@pytest.fixture
def env_models_down(tmp_path):
    yield from serve_env(tmp_path, llm=False, models=ModelCatalog(DownModelsLLM(), "model-a"))


def serve_env(tmp_path, llm, models=None):
    data = tmp_path / "data"
    site = tmp_path / "site"
    (site / "js").mkdir(parents=True)
    (site / "index.html").write_text("<!doctype html><title>home</title>", encoding="utf-8")
    (site / "login.html").write_text("<!doctype html><title>login</title>", encoding="utf-8")
    (site / "js" / "app.js").write_text("console.log(1);", encoding="utf-8")
    (site / "css").mkdir()
    (site / "css" / "theme.css").write_text(":root { --bg: #fff; }", encoding="utf-8")
    (site / "js" / "theme.js").write_text("self.Theme = {};", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("outside the site dir", encoding="utf-8")

    store = JobStore(data)
    events = EventLog(data)
    auth = Auth(data / "config.json")
    auth.set_passwords("user-pw", "admin-pw")
    sched = Scheduler(store, events, engine_factory=FakeEngine, workers=2)
    sched.start()
    wait_until_alive(sched)
    # The worker is built but never started: tests drive summary records directly.
    summaries = SummaryWorker(store, None, "test-model", url="http://llm.test/v1") if llm else None
    httpd = make_server("127.0.0.1", 0, store, sched, auth, events, site_dir=site, summaries=summaries,
                        models=models)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield SimpleNamespace(
        port=httpd.server_address[1],
        client=Client(httpd.server_address[1]),
        store=store,
        scheduler=sched,
        auth=auth,
        tmp=tmp_path,
    )
    httpd.shutdown()
    httpd.server_close()
    thread.join()
    sched.stop()


def pdf_bytes(tmp_path, n_pages=3):
    return sized_pdf(tmp_path / "scan.pdf", n_pages).read_bytes()


def create_job(client, size):
    r = client.request("POST", "/api/jobs", {"filename": "scan.pdf", "size": size})
    assert r.status == 201, r.body
    return r.json()["job"]["id"], r.json()["chunk_size"]


def upload_all(client, job_id, data, chunk_size):
    for i in range(0, len(data), chunk_size):
        r = client.request("PUT", f"/api/jobs/{job_id}/chunks/{i // chunk_size}", data[i:i + chunk_size])
        assert r.status == 200, r.body


def wait_until_alive(sched, timeout=10):
    deadline = time.monotonic() + timeout
    while not sched.alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sched.alive()


def wait_state(client, job_id, state, timeout=30):
    deadline = time.monotonic() + timeout
    job = None
    while time.monotonic() < deadline:
        job = client.request("GET", f"/api/jobs/{job_id}").json()
        if job["state"] == state:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job never reached {state}: {job}")


def logged_in(env, password="user-pw"):
    client = env.client.fresh()
    assert client.login(password).status == 200
    return client


# ----- health, headers, static ------------------------------------------------

def test_health_no_auth_and_503_when_worker_dead(env):
    anon = env.client.fresh()

    r = anon.request("GET", "/health")
    assert r.status == 200
    assert r.json() == {"ok": True}

    env.scheduler.stop()

    r = anon.request("GET", "/health")
    assert r.status == 503
    assert r.json() == {"ok": False}


def test_coop_coep_on_every_response(env):
    user = logged_in(env)
    responses = [
        user.request("GET", "/js/app.js"),
        user.request("GET", "/api/me"),
        user.request("GET", "/no-such-page"),
        env.client.fresh().request("GET", "/health"),
        env.client.fresh().request("GET", "/", headers={"Accept": "text/html"}),
    ]
    for r in responses:
        assert r.header("Cross-Origin-Opener-Policy") == "same-origin", r.status
        assert r.header("Cross-Origin-Embedder-Policy") == "require-corp", r.status


def test_html_redirects_to_login_without_session(env):
    r = env.client.fresh().request("GET", "/", headers={"Accept": "text/html"})

    assert r.status == 302
    assert r.header("Location") == "/login.html"


def test_api_401_without_session(env):
    anon = env.client.fresh()

    r = anon.request("GET", "/api/me")
    assert r.status == 401
    assert "error" in r.json()

    r = anon.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10})
    assert r.status == 401
    assert "error" in r.json()


def test_login_page_is_public_and_static_serves_with_session(env):
    anon = env.client.fresh()
    r = anon.request("GET", "/login.html")
    assert r.status == 200
    assert b"login" in r.body

    user = logged_in(env)
    r = user.request("GET", "/js/app.js")
    assert r.status == 200
    assert r.header("Content-Type").startswith("application/javascript")
    assert r.body == b"console.log(1);"

    r = user.request("GET", "/")
    assert r.status == 200
    assert b"home" in r.body


def test_theme_assets_public(env):
    anon = env.client.fresh()
    r = anon.request("GET", "/css/theme.css")
    assert r.status == 200
    assert r.header("Content-Type").startswith("text/css")
    r = anon.request("GET", "/js/theme.js")
    assert r.status == 200
    assert r.header("Content-Type").startswith("application/javascript")


def test_static_path_traversal_404(env):
    user = logged_in(env)

    assert user.request("GET", "/../requirements.txt").status == 404
    assert user.request("GET", "/%2e%2e/requirements.txt").status == 404
    assert user.request("GET", "/..%2frequirements.txt").status == 404


def test_unknown_api_route_is_json_404(env):
    user = logged_in(env)

    r = user.request("GET", "/api/nothing-here")

    assert r.status == 404
    assert "error" in r.json()


# ----- login / logout / me -----------------------------------------------------

def test_wrong_password_401(env):
    r = env.client.fresh().login("wrong-pw")

    assert r.status == 401
    assert r.json() == {"error": "รหัสผ่านไม่ถูกต้อง"}


def test_login_sets_httponly_cookie(env):
    r = env.client.fresh().request(
        "POST", "/api/login", {"password": "user-pw"}
    )

    assert r.status == 200
    cookie = r.header("Set-Cookie")
    assert cookie.startswith("ocr_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Path=/" in cookie
    assert "Max-Age=2592000" in cookie
    assert "Secure" not in cookie


def test_secure_flag_behind_https_proxy(env):
    r = env.client.fresh().request(
        "POST", "/api/login", {"password": "user-pw"}, headers={"X-Forwarded-Proto": "https"}
    )

    assert "Secure" in r.header("Set-Cookie")


def test_secure_flag_behind_cloudflare(env):
    r = env.client.fresh().request(
        "POST", "/api/login", {"password": "user-pw"}, headers={"CF-Connecting-IP": "203.0.113.5"}
    )

    assert "Secure" in r.header("Set-Cookie")


def test_me_reports_role(env):
    assert logged_in(env, "user-pw").request("GET", "/api/me").json() == {"role": "user"}
    assert logged_in(env, "admin-pw").request("GET", "/api/me").json() == {"role": "admin"}


def test_logout_clears_session(env):
    user = logged_in(env)
    assert user.request("GET", "/api/me").status == 200

    r = user.request("POST", "/api/logout")

    assert r.status == 200
    assert "Max-Age=0" in r.header("Set-Cookie")
    user.cookie = None
    assert user.request("GET", "/api/me").status == 401


# ----- job API -------------------------------------------------------------------

def test_full_job_flow(env, monkeypatch):
    data = pdf_bytes(env.tmp)
    monkeypatch.setattr(settings, "CHUNK_BYTES", max(1, len(data) // 3))
    user = logged_in(env)

    job_id, chunk_size = create_job(user, len(data))
    assert chunk_size == settings.CHUNK_BYTES
    upload_all(user, job_id, data, chunk_size)

    r = user.request("POST", f"/api/jobs/{job_id}/finish")
    assert r.status == 200, r.body
    job = wait_state(user, job_id, "done")
    assert job["done_pages"] == 3
    assert job["filename"] == "scan.pdf"
    assert "owner" not in job

    result = user.request("GET", f"/api/jobs/{job_id}/result").json()
    assert result["filename"] == "scan.pdf"
    assert [p["number"] for p in result["pages"]] == [1, 2, 3]
    for n, page in enumerate(result["pages"], start=1):
        assert page["text"] == expected_text(n)
        assert page["image"] == f"/api/jobs/{job_id}/pages/{n}.jpg"
        assert not page["image"].startswith("data:")

    img = user.request("GET", f"/api/jobs/{job_id}/pages/1.jpg")
    assert img.status == 200
    assert img.header("Content-Type") == "image/jpeg"
    assert img.header("Cache-Control") == "private, max-age=604800"
    assert img.body[:2] == b"\xff\xd8"

    text = user.request("GET", f"/api/jobs/{job_id}/text")
    assert text.status == 200
    assert text.header("Content-Type") == "text/plain; charset=utf-8"
    assert text.body.decode("utf-8") == "\n\n".join(expected_text(n) for n in (1, 2, 3))
    assert "filename*=UTF-8''" in text.header("Content-Disposition")
    assert text.header("Content-Disposition").endswith(urllib.parse.quote("scan.pdf.txt", safe=""))


def test_list_shows_only_own_jobs(env):
    alice = logged_in(env)
    bob = logged_in(env)
    job_id, _ = create_job(alice, 10)

    assert [j["id"] for j in alice.request("GET", "/api/jobs").json()] == [job_id]
    assert bob.request("GET", "/api/jobs").json() == []
    assert bob.request("GET", f"/api/jobs/{job_id}").status == 200  # any user with the id may view


def test_list_and_get_include_computed_fields(env):
    user = logged_in(env)
    job_id, _ = create_job(user, 10)

    job = user.request("GET", f"/api/jobs/{job_id}").json()

    assert job["done_pages"] == 0
    assert job["position"] == 0
    assert job["eta_seconds"] is None
    listed = user.request("GET", "/api/jobs").json()[0]
    assert listed["done_pages"] == 0 and listed["position"] == 0 and listed["eta_seconds"] is None


def test_delete_other_users_job_403(env):
    alice = logged_in(env)
    bob = logged_in(env)
    job_id, _ = create_job(alice, 10)

    assert bob.request("DELETE", f"/api/jobs/{job_id}").status == 403
    assert bob.request("GET", f"/api/jobs/{job_id}").status == 200

    assert alice.request("DELETE", f"/api/jobs/{job_id}").status == 200
    assert alice.request("GET", f"/api/jobs/{job_id}").status == 404


def test_admin_can_delete_any_job(env):
    alice = logged_in(env)
    admin = logged_in(env, "admin-pw")
    job_id, _ = create_job(alice, 10)

    assert admin.request("DELETE", f"/api/jobs/{job_id}").status == 200
    assert alice.request("GET", f"/api/jobs/{job_id}").status == 404


def test_missing_job_is_404(env):
    user = logged_in(env)

    assert user.request("GET", "/api/jobs/does-not-exist").status == 404
    assert user.request("GET", "/api/jobs/does-not-exist/result").status == 404
    assert user.request("GET", "/api/jobs/does-not-exist/text").status == 404
    assert user.request("POST", "/api/jobs/does-not-exist/finish").status == 404
    assert user.request("PUT", "/api/jobs/does-not-exist/chunks/0", b"x").status == 404


def test_finish_with_missing_chunk_400_and_stays_uploading(env, monkeypatch):
    data = pdf_bytes(env.tmp)
    monkeypatch.setattr(settings, "CHUNK_BYTES", max(1, len(data) // 3))
    user = logged_in(env)
    job_id, chunk_size = create_job(user, len(data))
    first = user.request("PUT", f"/api/jobs/{job_id}/chunks/0", data[:chunk_size])
    assert first.status == 200

    r = user.request("POST", f"/api/jobs/{job_id}/finish")

    assert r.status == 400
    assert "error" in r.json()
    assert user.request("GET", f"/api/jobs/{job_id}").json()["state"] == "uploading"

    upload_all(user, job_id, data, chunk_size)
    assert user.request("POST", f"/api/jobs/{job_id}/finish").status == 200


def test_retried_chunk_overwrites(env, monkeypatch):
    data = pdf_bytes(env.tmp)
    monkeypatch.setattr(settings, "CHUNK_BYTES", max(1, len(data) // 3))
    user = logged_in(env)
    job_id, chunk_size = create_job(user, len(data))
    assert user.request("PUT", f"/api/jobs/{job_id}/chunks/0", b"\x00" * chunk_size).status == 200

    upload_all(user, job_id, data, chunk_size)

    assert user.request("POST", f"/api/jobs/{job_id}/finish").status == 200
    wait_state(user, job_id, "done")
    assert user.request("GET", f"/api/jobs/{job_id}/result").json()["pages"][0]["text"] == expected_text(1)


def test_over_1000_pages_rejected(env, monkeypatch):
    monkeypatch.setattr(settings, "MAX_JOB_PAGES", 2)
    data = pdf_bytes(env.tmp, n_pages=3)
    user = logged_in(env)
    job_id, chunk_size = create_job(user, len(data))
    upload_all(user, job_id, data, chunk_size)

    r = user.request("POST", f"/api/jobs/{job_id}/finish")

    assert r.status == 400
    assert r.json() == {"error": "PDF มีมากเกิน 1000 หน้า"}
    assert user.request("GET", f"/api/jobs/{job_id}").status == 404


def test_unreadable_upload_rejected_and_deleted(env):
    data = b"this is not a pdf or an image" * 4
    user = logged_in(env)
    job_id, chunk_size = create_job(user, len(data))
    upload_all(user, job_id, data, chunk_size)

    r = user.request("POST", f"/api/jobs/{job_id}/finish")

    assert r.status == 400
    assert r.json() == {"error": "ไฟล์นี้ไม่ใช่รูปภาพหรือ PDF ที่อ่านได้"}
    assert user.request("GET", f"/api/jobs/{job_id}").status == 404


def test_chunk_over_limit_413(env, monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_BYTES", 10)
    user = logged_in(env)
    job_id, _ = create_job(user, 100)

    r = user.request("PUT", f"/api/jobs/{job_id}/chunks/0", b"x" * 50)

    assert r.status == 413
    assert "error" in r.json()
    assert user.request("GET", f"/api/jobs/{job_id}").json()["state"] == "uploading"


def test_only_owner_may_upload_or_finish(env):
    alice = logged_in(env)
    bob = logged_in(env)
    job_id, _ = create_job(alice, 10)

    assert bob.request("PUT", f"/api/jobs/{job_id}/chunks/0", b"0123456789").status == 403
    assert bob.request("POST", f"/api/jobs/{job_id}/finish").status == 403


def test_bad_create_body_400(env):
    user = logged_in(env)

    assert user.request("POST", "/api/jobs", {"filename": "a.pdf"}).status == 400
    assert user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 0}).status == 400


def test_cleanup_removes_expired_jobs(env):
    job = env.store.create("u1", "old.pdf", 10, now=0)
    stop = threading.Event()
    thread = start_cleanup(env.store, interval=0.05, stop=stop)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and env.store.get(job["id"]) is not None:
            time.sleep(0.02)
        assert env.store.get(job["id"]) is None
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()


# ----- login limiter (item 1) -------------------------------------------------

LOGIN_BLOCKED = "ลองผิดหลายครั้งเกินไป กรุณารอ 15 นาที"


@pytest.fixture
def no_login_delay(monkeypatch):
    monkeypatch.setattr(app_mod, "FAILED_LOGIN_DELAY", 0)


def login_from(client, password, ip=None):
    headers = {"CF-Connecting-IP": ip} if ip else None
    return client.request("POST", "/api/login", {"password": password}, headers=headers)


def test_sixth_attempt_from_same_ip_is_429_even_with_right_password(env, no_login_delay, monkeypatch):
    calls = []
    real_login = env.auth.login

    def counting_login(pw, cookie):
        calls.append(pw)
        return real_login(pw, cookie)

    monkeypatch.setattr(env.auth, "login", counting_login)

    for _ in range(5):
        assert login_from(env.client.fresh(), "wrong", ip="198.51.100.7").status == 401
    r = login_from(env.client.fresh(), "user-pw", ip="198.51.100.7")

    assert r.status == 429
    assert r.json() == {"error": LOGIN_BLOCKED}
    assert len(calls) == 5  # the blocked attempt never reached the password check


def test_other_ip_is_not_blocked(env, no_login_delay):
    for _ in range(5):
        login_from(env.client.fresh(), "wrong", ip="198.51.100.8")

    assert login_from(env.client.fresh(), "user-pw", ip="198.51.100.9").status == 200


def test_successful_login_resets_the_ip(env, no_login_delay):
    for _ in range(4):
        assert login_from(env.client.fresh(), "wrong", ip="198.51.100.10").status == 401
    assert login_from(env.client.fresh(), "user-pw", ip="198.51.100.10").status == 200
    for _ in range(4):
        assert login_from(env.client.fresh(), "wrong", ip="198.51.100.10").status == 401

    assert login_from(env.client.fresh(), "user-pw", ip="198.51.100.10").status == 200


def test_global_failure_cap_blocks_everyone(env, no_login_delay):
    for i in range(31):
        assert login_from(env.client.fresh(), "wrong", ip=f"203.0.113.{i}").status == 401

    r = login_from(env.client.fresh(), "user-pw", ip="192.0.2.200")

    assert r.status == 429
    assert r.json() == {"error": LOGIN_BLOCKED}


# ----- socket timeout (item 2) ------------------------------------------------

def test_stalled_client_is_closed_by_server(env, monkeypatch):
    import socket

    monkeypatch.setattr(app_mod.Handler, "timeout", 0.5)
    sock = socket.create_connection(("127.0.0.1", env.port))
    try:
        sock.settimeout(5)
        started = time.monotonic()

        assert sock.recv(1024) == b""  # server closed the idle connection
        assert time.monotonic() - started < 4
    finally:
        sock.close()


# ----- no drain before auth (item 3) ------------------------------------------

def raw_exchange(port, request_head: bytes, timeout=4):
    """Send only the request head, then read until the server closes. A server that waits for
    the announced body instead of closing makes recv() time out, which fails the caller."""
    import socket

    sock = socket.create_connection(("127.0.0.1", port))
    try:
        sock.settimeout(timeout)
        sock.sendall(request_head)
        data = b""
        started = time.monotonic()
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data, time.monotonic() - started
    finally:
        sock.close()


def status_code(raw: bytes) -> int:
    return int(raw.split(None, 2)[1])


def test_unauthenticated_huge_put_is_answered_and_closed_without_draining(env):
    head = b"PUT /api/jobs/abc/chunks/0 HTTP/1.1\r\nHost: x\r\nContent-Length: 50000000\r\n\r\n"

    data, elapsed = raw_exchange(env.port, head)

    assert status_code(data) == 401
    assert elapsed < 4


def test_unauthenticated_huge_login_body_is_closed_without_draining(env):
    head = b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 50000000\r\n\r\n"

    data, elapsed = raw_exchange(env.port, head)

    assert status_code(data) == 400
    assert elapsed < 4


def test_handler_timeout_default_is_120_seconds():
    assert app_mod.Handler.timeout == 120


# ----- security headers (item 8) ----------------------------------------------

def test_security_headers_on_static_and_api(env):
    user = logged_in(env)

    for r in (user.request("GET", "/js/app.js"), user.request("GET", "/api/me")):
        assert r.header("X-Content-Type-Options") == "nosniff"
        assert r.header("X-Frame-Options") == "DENY"
        assert r.header("Referrer-Policy") == "same-origin"


# ----- admin page gate normalisation (item 9) ----------------------------------

def test_admin_page_gate_ignores_case_suffix_and_encoding(env):
    (env.tmp / "site" / "admin.html").write_text("<!doctype html><title>admin</title>", encoding="utf-8")
    user = logged_in(env)

    for path in ("/Admin.html", "/admin.html.", "/ADMIN", "/ADMIN.HTML", "/admin.html%20", "/admin%2ehtml"):
        r = user.request("GET", path)
        assert r.status == 302, path
        assert r.header("Location") == "/login.html?admin=1", path
        assert b"admin</title>" not in r.body, path


def test_static_names_with_trailing_dot_or_space_are_404(env):
    user = logged_in(env)

    assert user.request("GET", "/index.html.").status == 404
    assert user.request("GET", "/js/app.js%20").status == 404
    assert user.request("GET", "/js/app.js::$DATA").status == 404


# ----- finish is idempotent (item 11) -----------------------------------------

def test_finish_twice_returns_the_job(env, monkeypatch):
    data = pdf_bytes(env.tmp)
    monkeypatch.setattr(settings, "CHUNK_BYTES", max(1, len(data) // 3))
    user = logged_in(env)
    job_id, chunk_size = create_job(user, len(data))
    upload_all(user, job_id, data, chunk_size)
    env.scheduler.stop()  # keep the job queued so the second finish sees a stable state

    assert user.request("POST", f"/api/jobs/{job_id}/finish").status == 200
    second = user.request("POST", f"/api/jobs/{job_id}/finish")

    assert second.status == 200, second.body
    assert second.json()["id"] == job_id


def test_finish_after_crash_between_assemble_and_enqueue(env, monkeypatch):
    data = pdf_bytes(env.tmp)
    monkeypatch.setattr(settings, "CHUNK_BYTES", max(1, len(data) // 3))
    user = logged_in(env)
    job_id, chunk_size = create_job(user, len(data))
    upload_all(user, job_id, data, chunk_size)
    env.store.assemble(job_id)  # the first finish died right after this

    r = user.request("POST", f"/api/jobs/{job_id}/finish")

    assert r.status == 200, r.body
    job = wait_state(user, job_id, "done")
    assert job["done_pages"] == 3


# ----- ownership flag (item 13) -----------------------------------------------

def test_mine_flag_true_only_for_owner_and_no_owner_id(env):
    alice = logged_in(env)
    bob = logged_in(env)
    job_id, _ = create_job(alice, 10)

    own = alice.request("GET", f"/api/jobs/{job_id}").json()
    other = bob.request("GET", f"/api/jobs/{job_id}").json()

    assert own["mine"] is True
    assert other["mine"] is False
    assert "owner" not in other
    assert alice.request("GET", "/api/jobs").json()[0]["mine"] is True


# ----- disk space (item 6) -----------------------------------------------------

def test_create_refused_when_disk_is_low(env, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(app_mod.shutil, "disk_usage", lambda path: SimpleNamespace(free=1024**3))
    user = logged_in(env)

    r = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 100 * 1024 * 1024})

    assert r.status == 507
    assert r.json() == {"error": "พื้นที่ดิสก์บนเซิร์ฟเวอร์ไม่พอ"}
    assert user.request("GET", "/api/jobs").json() == []


def test_create_allowed_with_room_to_spare(env, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(app_mod.shutil, "disk_usage", lambda path: SimpleNamespace(free=10 * 1024**3))

    assert logged_in(env).request("POST", "/api/jobs", {"filename": "a.pdf", "size": 1000}).status == 201


# ----- health with no engine (item 5) -----------------------------------------

def test_health_503_when_no_engine_ever_loads(env, monkeypatch):
    monkeypatch.setattr(sched_mod, "BACKOFF_SECONDS", 0.01)

    def broken():
        raise RuntimeError("model missing")

    events = EventLog(env.tmp / "data")
    sched = Scheduler(env.store, events, engine_factory=broken, workers=1)
    sched.start()
    httpd = make_server("127.0.0.1", 0, env.store, sched, env.auth, events, site_dir=env.tmp / "site")
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        time.sleep(0.1)
        r = Client(httpd.server_address[1]).request("GET", "/health")

        assert r.status == 503
        assert r.json() == {"ok": False}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()
        sched.stop()


# ----- AI summary API ---------------------------------------------------------

SUMMARY_OFF_MSG = "ไม่ได้เปิดใช้การสรุปด้วย AI"


def done_job(client, env):
    data = pdf_bytes(env.tmp)
    job_id, chunk_size = create_job(client, len(data))
    upload_all(client, job_id, data, chunk_size)
    r = client.request("POST", f"/api/jobs/{job_id}/finish")
    assert r.status == 200, r.body
    wait_state(client, job_id, "done")
    return job_id


def write_summary(env, job_id, state, **fields):
    record = {"state": state, "updated_at": 1.0, "attempts": 0, "waits": 0, "next_try_at": 0,
              "error": None, "progress": None, "model": "test-model", "result": None}
    record.update(fields)
    assert env.store.write_summary(job_id, record)


def test_summary_state_in_job_list_when_enabled(env_llm):
    user = logged_in(env_llm)
    job_id = done_job(user, env_llm)
    write_summary(env_llm, job_id, "done", result={"title": "t"})

    jobs = user.request("GET", "/api/jobs").json()
    assert [j["summary_state"] for j in jobs if j["id"] == job_id] == ["done"]


def test_summary_state_null_when_enabled_but_no_record(env_llm):
    user = logged_in(env_llm)
    job_id = done_job(user, env_llm)

    assert user.request("GET", f"/api/jobs/{job_id}").json()["summary_state"] is None
    assert user.request("GET", f"/api/jobs/{job_id}/summary").json() == {"enabled": True, "state": None}


def test_no_summary_state_when_disabled(env):
    user = logged_in(env)
    job_id = done_job(user, env)

    assert "summary_state" not in user.request("GET", f"/api/jobs/{job_id}").json()
    r = user.request("GET", f"/api/jobs/{job_id}/summary")
    assert r.status == 200
    assert r.json() == {"enabled": False}


def test_get_summary_visible_to_other_session(env_llm):
    owner = logged_in(env_llm)
    job_id = done_job(owner, env_llm)
    write_summary(env_llm, job_id, "done", result={"title": "t"})

    r = logged_in(env_llm).request("GET", f"/api/jobs/{job_id}/summary")
    assert r.status == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["state"] == "done"
    assert body["result"] == {"title": "t"}


def test_post_summary_conflict_while_pending(env_llm):
    user = logged_in(env_llm)
    job_id = done_job(user, env_llm)
    write_summary(env_llm, job_id, "pending")

    assert user.request("POST", f"/api/jobs/{job_id}/summary").status == 409


def test_post_summary_by_non_owner_allowed(env_llm):
    owner = logged_in(env_llm)
    job_id = done_job(owner, env_llm)
    write_summary(env_llm, job_id, "done", result={"title": "t"})

    r = logged_in(env_llm).request("POST", f"/api/jobs/{job_id}/summary")
    assert r.status == 202
    assert r.json()["state"] == "pending"


def test_post_summary_disabled_503(env):
    user = logged_in(env)
    job_id = done_job(user, env)

    r = user.request("POST", f"/api/jobs/{job_id}/summary")
    assert r.status == 503
    assert r.json() == {"error": SUMMARY_OFF_MSG}


def test_get_summary_unknown_job_404(env_llm):
    user = logged_in(env_llm)
    assert user.request("GET", "/api/jobs/does-not-exist/summary").status == 404


def test_post_summary_unknown_job_404(env_llm):
    user = logged_in(env_llm)
    assert user.request("POST", "/api/jobs/does-not-exist/summary").status == 404


def test_summary_routes_need_login(env_llm):
    assert env_llm.client.fresh().request("GET", "/api/jobs/x/summary").status == 401
    assert env_llm.client.fresh().request("POST", "/api/jobs/x/summary").status == 401


# ----- model choice (per job) -------------------------------------------------

MODEL_INVALID_MSG = "โมเดลนี้ใช้ไม่ได้"


def test_models_requires_login(env_models):
    assert env_models.client.fresh().request("GET", "/api/models").status == 401


def test_models_lists_catalog_with_default(env_models):
    user = logged_in(env_models)

    r = user.request("GET", "/api/models")

    assert r.status == 200
    assert r.json() == {
        "models": [{"id": "model-a", "name": "model-a"}, {"id": "model-b", "name": "model-b"}],
        "default": "model-a",
    }


def test_models_empty_when_no_catalog(env):
    r = logged_in(env).request("GET", "/api/models")

    assert r.status == 200
    assert r.json() == {"models": [], "default": None}


def test_create_job_with_allowed_model_stores_it(env_models):
    user = logged_in(env_models)

    r = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": "model-b"})
    job_id = r.json()["job"]["id"]

    assert r.status == 201
    assert r.json()["job"]["model"] == "model-b"
    assert user.request("GET", f"/api/jobs/{job_id}").json()["model"] == "model-b"


def test_create_job_with_disallowed_model_is_400_and_creates_nothing(env_models):
    user = logged_in(env_models)

    for bad in ["no-such-model", 123, "", ["x"]]:
        r = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": bad})
        assert r.status == 400, bad
        assert r.json() == {"error": MODEL_INVALID_MSG}
    assert env_models.store.list(None) == []


def test_create_job_with_any_model_is_400_without_catalog(env):
    r = logged_in(env).request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": "model-a"})

    assert r.status == 400
    assert r.json() == {"error": MODEL_INVALID_MSG}
    assert env.store.list(None) == []


def test_create_job_without_model_has_no_model_key(env_models):
    user = logged_in(env_models)

    job_id = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10}).json()["job"]["id"]
    null_id = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": None}).json()["job"]["id"]

    assert "model" not in user.request("GET", f"/api/jobs/{job_id}").json()
    assert "model" not in user.request("GET", f"/api/jobs/{null_id}").json()


def test_job_creation_with_api_down_allows_only_the_default(env_models_down):
    user = logged_in(env_models_down)

    ok = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": "model-a"})
    other = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": "model-b"})

    assert ok.status == 201
    assert other.status == 400
    assert other.json() == {"error": MODEL_INVALID_MSG}


class KeyedModelsLLM:
    """Lists `ids` for the key it stands for."""

    def __init__(self, ids):
        self.ids = ids

    def list_models(self):
        return list(self.ids)


@pytest.fixture
def env_two_keys(tmp_path):
    catalog = ModelCatalog(KeyedModelsLLM(["model-a", "model-b"]), "model-a",
                           others={"2": KeyedModelsLLM(["model-c"])})
    yield from serve_env(tmp_path, llm=False, models=catalog)


def test_models_endpoint_has_no_key_field_and_lists_key_two_ids(env_two_keys):
    r = logged_in(env_two_keys).request("GET", "/api/models")

    assert r.json()["models"] == [
        {"id": "model-a", "name": "model-a"},
        {"id": "model-b", "name": "model-b"},
        {"id": "model-c", "name": "model-c"},
    ]


def test_create_job_with_model_only_key_two_lists_is_201_without_llm_key(env_two_keys):
    user = logged_in(env_two_keys)

    r = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": "model-c"})

    assert r.status == 201
    job_id = r.json()["job"]["id"]
    assert env_two_keys.store.get(job_id)["model"] == "model-c"
    assert "llm_key" not in env_two_keys.store.get(job_id)


def test_create_job_ignores_a_stale_key_field(env_two_keys):
    user = logged_in(env_two_keys)

    r = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, "model": "model-c", "key": "2"})

    assert r.status == 201
    job_id = r.json()["job"]["id"]
    assert env_two_keys.store.get(job_id)["model"] == "model-c"
    assert "llm_key" not in env_two_keys.store.get(job_id)


@pytest.mark.parametrize("body", [
    {"model": "no-such-model"},
    {"model": "no-such-model", "key": "2"},
    {"model": ""},
])
def test_create_job_with_unlisted_model_is_400(env_two_keys, body):
    user = logged_in(env_two_keys)

    r = user.request("POST", "/api/jobs", {"filename": "a.pdf", "size": 10, **body})

    assert r.status == 400
    assert r.json() == {"error": MODEL_INVALID_MSG}
    assert env_two_keys.store.list(None) == []
