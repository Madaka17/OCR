import pytest

from server import settings
from server.store import JobError

DAY = 86400


def test_create_rejects_over_500mb(store):
    with pytest.raises(JobError) as exc:
        store.create("u1", "big.pdf", 500 * 1024 * 1024 + 1, now=0)
    assert exc.value.status == 400


def test_create_returns_uploading_job(store):
    job = store.create("u1", "doc.pdf", 10, now=100.0)
    assert job["state"] == "uploading"
    assert job["owner"] == "u1"
    assert job["filename"] == "doc.pdf"
    assert job["size"] == 10
    assert store.get(job["id"])["id"] == job["id"]


def test_chunks_assemble_in_index_order(store, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_BYTES", 4)
    job = store.create("u1", "doc.pdf", 10, now=0)
    store.put_chunk(job["id"], 2, b"ij", now=1)
    store.put_chunk(job["id"], 0, b"abcd", now=2)
    store.put_chunk(job["id"], 1, b"efgh", now=3)

    path = store.assemble(job["id"])

    assert path.read_bytes() == b"abcdefghij"
    assert not (tmp_path / "jobs" / job["id"] / "upload").exists()


def test_retry_chunk_overwrites(store, monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_BYTES", 4)
    job = store.create("u1", "doc.pdf", 4, now=0)
    store.put_chunk(job["id"], 0, b"AAAA", now=1)
    store.put_chunk(job["id"], 0, b"BBBB", now=2)

    path = store.assemble(job["id"])

    assert path.read_bytes() == b"BBBB"


def test_missing_chunk_rejected(store, monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_BYTES", 4)
    job = store.create("u1", "doc.pdf", 10, now=0)
    store.put_chunk(job["id"], 0, b"abcd", now=1)
    store.put_chunk(job["id"], 1, b"efgh", now=2)

    with pytest.raises(JobError) as exc:
        store.assemble(job["id"])
    assert exc.value.status == 400
    assert exc.value.message == "อัปโหลดไม่ครบ"


def test_chunk_after_finish_is_409(store, monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_BYTES", 4)
    job = store.create("u1", "doc.pdf", 4, now=0)
    store.put_chunk(job["id"], 0, b"abcd", now=1)
    store.assemble(job["id"])
    store.set_state(job["id"], "queued")

    with pytest.raises(JobError) as exc:
        store.put_chunk(job["id"], 0, b"zzzz", now=2)
    assert exc.value.status == 409


def test_list_filters_owner_newest_first(store):
    a = store.create("alice", "a.pdf", 1, now=1)
    b = store.create("bob", "b.pdf", 1, now=2)
    c = store.create("alice", "c.pdf", 1, now=3)

    assert [j["id"] for j in store.list("alice")] == [c["id"], a["id"]]
    assert [j["id"] for j in store.list("bob")] == [b["id"]]
    assert [j["id"] for j in store.list(None)] == [c["id"], b["id"], a["id"]]


def test_text_joins_pages_in_number_order(store):
    job = store.create("u1", "doc.pdf", 1, now=0)
    store.write_page(job["id"], 2, {"number": 2, "text": "p2"}, None)
    store.write_page(job["id"], 1, {"number": 1, "text": "p1"}, None)

    assert store.done_pages(job["id"]) == {1, 2}
    assert store.text(job["id"]) == "p1\n\np2"


def test_failed_page_contributes_empty_text(store):
    job = store.create("u1", "doc.pdf", 1, now=0)
    store.write_page(job["id"], 1, {"number": 1, "text": "p1"}, None)
    store.write_page(job["id"], 2, {"error": "render failed"}, None)
    store.write_page(job["id"], 3, {"number": 3, "text": "p3"}, None)

    assert store.text(job["id"]) == "p1\n\n\n\np3"


def test_write_page_after_delete_is_noop(store, tmp_path):
    job = store.create("u1", "doc.pdf", 1, now=0)
    store.delete(job["id"])

    store.write_page(job["id"], 1, {"number": 1, "text": "late"}, b"jpg")

    assert store.get(job["id"]) is None
    assert not (tmp_path / "jobs" / job["id"]).exists()


def test_cleanup_removes_jobs_7_days_after_creation(store):
    t0 = 1_000_000.0
    job = store.create("u1", "doc.pdf", 1, now=t0)
    store.set_state(job["id"], "done")

    assert store.cleanup(t0 + 7 * DAY - 1) == []
    assert store.get(job["id"]) is not None

    assert store.cleanup(t0 + 7 * DAY + 1) == [job["id"]]
    assert store.get(job["id"]) is None


def test_cleanup_removes_stale_upload_after_24h(store):
    t0 = 1_000_000.0
    job = store.create("u1", "doc.pdf", 1, now=t0)

    assert store.cleanup(t0 + DAY - 1) == []
    assert store.cleanup(t0 + DAY + 1) == [job["id"]]


def test_delete_dotdot_cannot_remove_data_root(store, tmp_path):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("x", encoding="utf-8")
    job = store.create("u1", "doc.pdf", 1, now=0)

    store.delete("..")

    assert sentinel.exists()
    assert store.get(job["id"]) is not None


def test_invalid_job_id_cannot_read_outside_jobs_dir(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "meta.json").write_text(
        '{"id": "outside", "owner": "u1", "state": "uploading", "size": 1,'
        ' "created_at": 0, "last_chunk_at": 0}',
        encoding="utf-8",
    )

    assert store.get("../outside") is None
    assert store.read_page("../outside", 1) is None
    assert store.done_pages("../outside") == set()


def test_invalid_job_id_behaves_as_missing(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "meta.json").write_text(
        '{"id": "outside", "owner": "u1", "state": "uploading", "size": 1,'
        ' "created_at": 0, "last_chunk_at": 0}',
        encoding="utf-8",
    )

    with pytest.raises(JobError) as exc:
        store.put_chunk("../outside", 0, b"a", now=1)
    assert exc.value.status == 404
    assert not (outside / "upload").exists()

    store.set_state("../outside", "done")
    store.write_page("../outside", 1, {"number": 1, "text": "x"}, None)
    assert not (outside / "pages").exists()
    assert (outside / "meta.json").read_text(encoding="utf-8").count('"done"') == 0


# ----- corrupt files (item 4) ------------------------------------------------

def test_truncated_meta_is_missing_and_other_jobs_still_listed(store, tmp_path, capsys):
    good = store.create("u1", "good.pdf", 1, now=1)
    bad = store.create("u1", "bad.pdf", 1, now=2)
    (tmp_path / "jobs" / bad["id"] / "meta.json").write_text('{"id": "bad", "own', encoding="utf-8")

    assert store.get(bad["id"]) is None
    assert [j["id"] for j in store.list(None)] == [good["id"]]
    assert store.cleanup(1.0) == []
    assert str(tmp_path / "jobs" / bad["id"] / "meta.json") in capsys.readouterr().err


def test_unreadable_meta_logged_once(store, tmp_path, capsys):
    bad = store.create("u1", "bad.pdf", 1, now=2)
    (tmp_path / "jobs" / bad["id"] / "meta.json").write_text("{", encoding="utf-8")

    store.get(bad["id"])
    store.get(bad["id"])
    store.list(None)

    assert capsys.readouterr().err.count("meta.json") == 1


def test_truncated_page_is_missing_and_removed_from_done(store, tmp_path):
    job = store.create("u1", "doc.pdf", 1, now=0)
    store.write_page(job["id"], 1, {"number": 1, "text": "p1"}, None)
    store.write_page(job["id"], 2, {"number": 2, "text": "p2"}, None)
    bad_path = tmp_path / "jobs" / job["id"] / "pages" / "2.json"
    bad_path.write_text('{"number": 2, "te', encoding="utf-8")

    assert store.read_page(job["id"], 2) is None
    assert store.done_pages(job["id"]) == {1}
    assert not bad_path.exists()


def test_text_skips_page_when_job_deleted_mid_read(store, monkeypatch):
    job = store.create("u1", "doc.pdf", 1, now=0)
    store.write_page(job["id"], 1, {"number": 1, "text": "p1"}, None)
    real_read_page = store.read_page

    def delete_then_read(job_id, number):
        store.delete(job_id)
        return real_read_page(job_id, number)

    monkeypatch.setattr(store, "read_page", delete_then_read)

    assert store.text(job["id"]) == ""


def test_atomic_write_fsyncs_before_replace(store, monkeypatch, tmp_path):
    from server import store as store_mod

    calls = []
    real_fsync = store_mod.os.fsync
    real_replace = store_mod.os.replace
    monkeypatch.setattr(store_mod.os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd)))
    monkeypatch.setattr(store_mod.os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b)))

    store_mod._atomic_write(tmp_path / "x.json", b"{}")

    assert calls == ["fsync", "replace"]


# ----- disk growth and idempotent assemble (items 10, 11) --------------------

def _job_with_source(store, monkeypatch):
    monkeypatch.setattr(settings, "CHUNK_BYTES", 4)
    job = store.create("u1", "doc.pdf", 4, now=0)
    store.put_chunk(job["id"], 0, b"abcd", now=1)
    store.assemble(job["id"])
    return job["id"]


@pytest.mark.parametrize("state", ["done", "failed", "cancelled"])
def test_terminal_state_removes_source_but_keeps_results(store, tmp_path, monkeypatch, state):
    job_id = _job_with_source(store, monkeypatch)
    store.write_page(job_id, 1, {"number": 1, "text": "p1"}, b"jpg")

    store.set_state(job_id, state)

    assert not (tmp_path / "jobs" / job_id / "source.bin").exists()
    assert store.read_page(job_id, 1) == {"number": 1, "text": "p1"}
    assert (tmp_path / "jobs" / job_id / "pages" / "1.jpg").exists()


def test_queued_state_keeps_source(store, tmp_path, monkeypatch):
    job_id = _job_with_source(store, monkeypatch)

    store.set_state(job_id, "queued")

    assert (tmp_path / "jobs" / job_id / "source.bin").read_bytes() == b"abcd"


def test_cleanup_removes_dir_without_meta_only_after_an_hour(store, tmp_path):
    import os

    orphan = tmp_path / "jobs" / "orphan-job"
    (orphan / "upload").mkdir(parents=True)
    fresh = tmp_path / "jobs" / "fresh-job"
    (fresh / "upload").mkdir(parents=True)
    os.utime(orphan, (1_000_000, 1_000_000))
    now = 1_000_000 + 3600 + 1

    removed = store.cleanup(now)

    assert removed == ["orphan-job"]
    assert not orphan.exists()
    assert fresh.exists()


def test_cleanup_keeps_old_dir_that_has_meta(store, tmp_path):
    import os

    job = store.create("u1", "doc.pdf", 1, now=1_000_000)
    job_dir = tmp_path / "jobs" / job["id"]
    os.utime(job_dir, (1_000_000, 1_000_000))

    assert store.cleanup(1_000_000 + 3600 + 1) == []
    assert job_dir.exists()


def test_assemble_twice_returns_existing_source(store, tmp_path, monkeypatch):
    job_id = _job_with_source(store, monkeypatch)

    again = store.assemble(job_id)

    assert again.read_bytes() == b"abcd"
