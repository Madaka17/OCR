import getpass
import json

import pytest

from server import __main__ as cli
from server import settings
from server.auth import Auth


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    return tmp_path


def test_passwords_with_flags_writes_config_and_says_restart(data_dir, capsys):
    code = cli.main(["passwords", "--user", "u-pw", "--admin", "a-pw"])

    assert code == 0
    auth = Auth(data_dir / "config.json")
    assert auth.login("u-pw", None)[1]["role"] == "user"
    assert auth.login("a-pw", None)[1]["role"] == "admin"
    assert "เริ่มเซิร์ฟเวอร์ใหม่" in capsys.readouterr().out


def test_passwords_prompts_when_flags_missing(data_dir, monkeypatch):
    answers = iter(["u-pw", "u-pw", "a-pw", "a-pw"])  # user, user again, admin, admin again
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))

    code = cli.main(["passwords"])

    assert code == 0
    auth = Auth(data_dir / "config.json")
    assert auth.login("a-pw", None)[1]["role"] == "admin"


def test_passwords_prompt_mismatch_changes_nothing(data_dir, monkeypatch):
    Auth(data_dir / "config.json").set_passwords("keep-u", "keep-a")
    answers = iter(["u-pw", "u-pw", "a-pw", "typo"])  # admin confirmation differs
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))

    code = cli.main(["passwords"])

    assert code == 2
    assert Auth(data_dir / "config.json").login("keep-a", None)[1]["role"] == "admin"


def test_config_file_is_valid_json_after_cli_write(data_dir):
    cli.main(["passwords", "--user", "u", "--admin", "a"])

    assert set(json.loads((data_dir / "config.json").read_text(encoding="utf-8"))) == {"user", "admin", "secret"}
