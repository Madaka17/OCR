import json
from types import SimpleNamespace

from server import auth as auth_mod
from server.auth import Auth, check_password, hash_password


def make_auth(tmp_path):
    auth = Auth(tmp_path / "config.json")
    auth.set_passwords("user-pw", "admin-pw")
    return auth


def test_hash_round_trip():
    stored = hash_password("รหัสผ่าน")

    scheme, salt_hex, digest_hex = stored.split("$")
    assert scheme == "scrypt"
    assert len(bytes.fromhex(salt_hex)) == 16
    assert len(bytes.fromhex(digest_hex)) == 64
    assert check_password("รหัสผ่าน", stored)
    assert not check_password("รหัสอื่น", stored)
    assert hash_password("รหัสผ่าน") != stored  # random salt


def test_malformed_stored_hash_rejected():
    assert not check_password("x", "garbage")
    assert not check_password("x", "md5$00$00")
    assert not check_password("x", None)


def test_login_assigns_role_by_password(tmp_path):
    auth = make_auth(tmp_path)

    _, admin = auth.login("admin-pw", None)
    _, user = auth.login("user-pw", None)

    assert admin["role"] == "admin"
    assert user["role"] == "user"
    assert auth.login("wrong", None) is None


def test_passwords_persist_across_instances(tmp_path):
    make_auth(tmp_path)

    reloaded = Auth(tmp_path / "config.json")

    assert reloaded.ready()
    assert reloaded.login("user-pw", None) is not None


def test_tampered_cookie_rejected(tmp_path):
    auth = make_auth(tmp_path)
    cookie, _ = auth.login("user-pw", None)
    payload_hex, _, sig = cookie.rpartition(".")
    data = json.loads(bytes.fromhex(payload_hex))
    data["role"] = "admin"
    forged_payload = json.dumps(data).encode("utf-8").hex()

    assert auth.session(f"{forged_payload}.{sig}") is None
    assert auth.session(cookie) is not None


def test_expired_cookie_rejected(tmp_path, monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(auth_mod, "time", SimpleNamespace(time=lambda: clock["now"]))
    auth = make_auth(tmp_path)
    cookie, _ = auth.login("user-pw", None)
    assert auth.session(cookie) is not None

    clock["now"] += 30 * 86400 + 1

    assert auth.session(cookie) is None


def test_relogin_keeps_owner(tmp_path):
    auth = make_auth(tmp_path)
    first_cookie, first = auth.login("user-pw", None)

    second_cookie, second = auth.login("user-pw", first_cookie)
    _, admin = auth.login("admin-pw", first_cookie)

    assert second["owner"] == first["owner"]
    assert admin["owner"] == first["owner"]
    assert auth.session(second_cookie)["owner"] == first["owner"]


def test_new_login_without_cookie_gets_new_owner(tmp_path):
    auth = make_auth(tmp_path)
    _, a = auth.login("user-pw", None)
    _, b = auth.login("user-pw", None)

    assert a["owner"] != b["owner"]


def test_garbage_cookie_rejected(tmp_path):
    auth = make_auth(tmp_path)

    assert auth.session(None) is None
    assert auth.session("") is None
    assert auth.session("no-dot-here") is None
    assert auth.session("zz.zz") is None
    assert auth.session("é.é") is None


# ----- password change invalidates sessions; atomic config (item 7) ----------

def test_cookie_issued_before_password_change_is_rejected(tmp_path):
    auth = make_auth(tmp_path)
    cookie, _ = auth.login("user-pw", None)
    assert auth.session(cookie) is not None

    auth.set_passwords("new-user-pw", "new-admin-pw")

    assert auth.session(cookie) is None


def test_config_is_valid_json_after_write_and_no_temp_files(tmp_path):
    auth = make_auth(tmp_path)
    auth.set_passwords("another", "pair")

    data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))

    assert set(data) == {"user", "admin", "secret"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json"]


def test_config_write_fsyncs_before_replace(tmp_path, monkeypatch):
    import os

    calls = []
    real_fsync = os.fsync
    real_replace = os.replace
    monkeypatch.setattr(auth_mod.os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd)))
    monkeypatch.setattr(auth_mod.os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b)))

    make_auth(tmp_path)

    assert calls[-2:] == ["fsync", "replace"]
