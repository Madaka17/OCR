from test_app import create_job, env, env_llm, logged_in, pdf_bytes  # noqa: F401 - fixtures

from server.stats import EventLog

ADMIN_PAGE = b"<!doctype html><title>admin</title>"


def write_admin_page(env):
    (env.tmp / "site" / "admin.html").write_text(ADMIN_PAGE.decode("utf-8"), encoding="utf-8")


def test_admin_overview_403_for_user_200_for_admin(env):
    assert env.client.fresh().request("GET", "/api/admin/overview").status == 401

    user = logged_in(env)
    r = user.request("GET", "/api/admin/overview")
    assert r.status == 403
    assert "error" in r.json()

    admin = logged_in(env, "admin-pw")
    r = admin.request("GET", "/api/admin/overview")
    assert r.status == 200
    body = r.json()
    assert set(body) == {"jobs", "stats", "errors", "resources", "workers", "llm"}
    assert set(body["workers"]) == {"count", "alive"}
    assert body["workers"]["count"] == 2
    assert body["workers"]["alive"] is True
    assert body["stats"]["pages_per_hour"] == [0] * 24
    assert set(body["resources"]) == {"cpu_percent", "ram_used_gb", "ram_total_gb", "disk_free_gb", "data_dir_gb"}


def test_admin_html_redirect_for_user(env):
    write_admin_page(env)

    for client in (env.client.fresh(), logged_in(env)):
        r = client.request("GET", "/admin")
        assert r.status == 302
        assert r.header("Location") == "/login.html?admin=1"
        assert client.request("GET", "/admin.html").status == 302

    r = logged_in(env, "admin-pw").request("GET", "/admin")
    assert r.status == 200
    assert r.body == ADMIN_PAGE
    assert r.header("Content-Type").startswith("text/html")


def test_admin_sees_all_owners_jobs(env):
    alice = logged_in(env)
    bob = logged_in(env)
    alice_job, _ = create_job(alice, 10)
    bob_job, _ = create_job(bob, 10)

    jobs = logged_in(env, "admin-pw").request("GET", "/api/admin/overview").json()["jobs"]

    assert {j["id"] for j in jobs} == {alice_job, bob_job}
    for job in jobs:
        assert "owner" not in job
        assert {"done_pages", "position", "eta_seconds"} <= set(job)


def test_admin_delete_job_admin_only(env):
    alice = logged_in(env)
    job_id, _ = create_job(alice, 10)

    assert alice.request("DELETE", f"/api/admin/jobs/{job_id}").status == 403
    assert alice.request("GET", f"/api/jobs/{job_id}").status == 200

    admin = logged_in(env, "admin-pw")
    assert admin.request("DELETE", f"/api/admin/jobs/{job_id}").status == 200
    assert alice.request("GET", f"/api/jobs/{job_id}").status == 404


def test_admin_errors_resolve_filename_or_fall_back_to_job_id(env):
    user = logged_in(env)
    job_id, _ = create_job(user, 10)
    log = EventLog(env.tmp / "data")
    log.error(job_id, 2, "ocr failed", now=1.0)
    log.error("gone-job-id", 7, "old failure", now=2.0)

    errors = logged_in(env, "admin-pw").request("GET", "/api/admin/overview").json()["errors"]

    by_message = {e["message"]: e for e in errors}
    assert by_message["ocr failed"]["file"] == "scan.pdf"
    assert by_message["ocr failed"]["page"] == 2
    assert by_message["old failure"]["file"] == "gone-job-id"
    assert errors[0]["message"] == "old failure"  # newest first


def test_overview_includes_llm_status(env_llm):
    admin = logged_in(env_llm, "admin-pw")
    body = admin.request("GET", "/api/admin/overview").json()
    assert body["llm"]["enabled"] is True
    assert body["llm"]["model"] == "test-model"


def test_overview_llm_disabled(env):
    admin = logged_in(env, "admin-pw")
    assert admin.request("GET", "/api/admin/overview").json()["llm"] == {"enabled": False}
