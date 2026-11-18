import json

import pytest

from server.stats import recent_errors, resources, summary

NOW = 1_800_000_000.0
HOUR = 3600
DAY = 86400


def write_jsonl(path, records, raw_tail=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
        f.write(raw_tail)


def ev(ts, ok=True, seconds=2.0, job="j1", page=1):
    return {"ts": ts, "job": job, "page": page, "seconds": seconds, "ok": ok}


def err(ts, message, job="j1", page=1):
    return {"ts": ts, "job": job, "page": page, "message": message}


def test_summary_buckets(tmp_path):
    write_jsonl(tmp_path / "events.jsonl", [
        ev(NOW - 30 * 60, ok=True, seconds=3.0),
        ev(NOW - 30 * 60, ok=True, seconds=5.0),
        ev(NOW - 90 * 60, ok=True, seconds=4.0),
        ev(NOW - 90 * 60, ok=False, seconds=1.0),
        ev(NOW - 25 * HOUR, ok=True, seconds=100.0),
    ])

    s = summary(tmp_path, NOW)

    assert len(s["pages_per_hour"]) == 24
    assert s["pages_per_hour"][-1] == 2
    assert s["pages_per_hour"][-2] == 2
    assert sum(s["pages_per_hour"]) == 4  # the 25-hour-old event is outside the 24-hour window
    # 7-day window: the 25-hour-old ok page counts; the failed page is excluded from the average.
    assert s["pages_7d"] == 5
    assert s["failed_pages_7d"] == 1
    assert s["avg_seconds_per_page"] == pytest.approx((3.0 + 5.0 + 4.0 + 100.0) / 4)


def test_summary_empty_root(tmp_path):
    s = summary(tmp_path, NOW)

    assert s["pages_per_hour"] == [0] * 24
    assert s["jobs_per_day"] == [0] * 7
    assert s["avg_seconds_per_page"] is None
    assert s["pages_7d"] == 0
    assert s["failed_pages_7d"] == 0


def test_jobs_per_day_by_created_at_oldest_first(tmp_path):
    jobs = [
        {"created_at": NOW - DAY + 10},   # today's bucket (last)
        {"created_at": NOW - DAY - 10},   # yesterday
        {"created_at": NOW - 6 * DAY},    # oldest day in window (first)
        {"created_at": NOW - 8 * DAY},    # outside the window
    ]

    s = summary(tmp_path, NOW, jobs=jobs)

    assert s["jobs_per_day"] == [1, 0, 0, 0, 0, 1, 1]


def test_truncated_last_line_ignored(tmp_path):
    write_jsonl(tmp_path / "events.jsonl", [ev(NOW - 60)], raw_tail='{"ts": 1700000')
    write_jsonl(tmp_path / "errors.jsonl", [err(NOW - 60, "boom")], raw_tail='{"ts": 17')

    assert summary(tmp_path, NOW)["pages_7d"] == 1
    assert [e["message"] for e in recent_errors(tmp_path)] == ["boom"]


def test_recent_errors_newest_first_max_50(tmp_path):
    write_jsonl(tmp_path / "errors.jsonl", [err(NOW + i, f"m{i}") for i in range(60)])

    errors = recent_errors(tmp_path)

    assert len(errors) == 50
    assert errors[0]["message"] == "m59"
    assert errors[-1]["message"] == "m10"


def test_recent_errors_missing_file(tmp_path):
    assert recent_errors(tmp_path) == []


def test_resources_keys_numeric(tmp_path):
    (tmp_path / "blob.bin").write_bytes(b"x" * 2048)

    r = resources(tmp_path)

    assert set(r) == {"cpu_percent", "ram_used_gb", "ram_total_gb", "disk_free_gb", "data_dir_gb"}
    for key, value in r.items():
        assert isinstance(value, (int, float)) and not isinstance(value, bool), key
    assert r["ram_total_gb"] > 0
    assert r["disk_free_gb"] > 0
    assert r["data_dir_gb"] >= 0


def test_resources_missing_data_dir_does_not_crash(tmp_path):
    r = resources(tmp_path / "not-created-yet")

    assert r["disk_free_gb"] > 0
    assert r["data_dir_gb"] == 0


# ----- log pruning (item 10) ------------------------------------------------

def test_prune_drops_lines_older_than_eight_days(tmp_path):
    from server.stats import EventLog

    now = NOW
    log = EventLog(tmp_path)
    write_jsonl(tmp_path / "events.jsonl", [ev(now - 9 * DAY), ev(now - 1 * DAY, page=2)])
    write_jsonl(tmp_path / "errors.jsonl", [err(now - 8 * DAY - 60, "old"), err(now - DAY, "new")])

    log.prune(now)

    kept_events = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    kept_errors = [json.loads(x) for x in (tmp_path / "errors.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["page"] for e in kept_events] == [2]
    assert [e["message"] for e in kept_errors] == ["new"]


def test_prune_leaves_no_temp_files_and_keeps_appends_working(tmp_path):
    from server.stats import EventLog

    log = EventLog(tmp_path)
    write_jsonl(tmp_path / "events.jsonl", [ev(NOW - 9 * DAY)])

    log.prune(NOW)
    log.page_done("j1", 1, 2.0, True, NOW)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["events.jsonl"]
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["job"] == "j1"


def test_prune_without_logs_is_a_noop(tmp_path):
    from server.stats import EventLog

    EventLog(tmp_path).prune(NOW)

    assert list(tmp_path.iterdir()) == []


def test_start_cleanup_prunes_logs(tmp_path):
    import time as time_mod
    from server.stats import EventLog
    from server.store import JobStore
    from server.app import start_cleanup

    write_jsonl(tmp_path / "events.jsonl", [ev(time_mod.time() - 9 * DAY)])
    start_cleanup(JobStore(tmp_path), events=EventLog(tmp_path), interval=0.05)

    deadline = time_mod.monotonic() + 10
    while time_mod.monotonic() < deadline and (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip():
        time_mod.sleep(0.02)

    assert (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip() == ""
