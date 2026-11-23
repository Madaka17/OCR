import importlib
import os

import pytest

from server import settings

LLM_KEYS = ["OCR_LLM_URL", "OCR_LLM_MODEL", "OCR_LLM_API_KEY", "OCR_LLM_REASONING_EFFORT"]


@pytest.fixture
def env_reset(monkeypatch):
    """Restore the environment and reload settings with defaults after each test,
    so no value read from a test's .env file leaks into later tests."""
    saved = {k: os.environ.get(k) for k in LLM_KEYS + ["OCR_ENV_FILE"]}
    yield monkeypatch
    monkeypatch.undo()
    for k in LLM_KEYS:
        os.environ.pop(k, None)
    os.environ["OCR_ENV_FILE"] = ""
    importlib.reload(settings)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _clean_llm_env(monkeypatch):
    for k in LLM_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_env_file_is_parsed(tmp_path, env_reset):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment line\n"
        "\n"
        'OCR_LLM_URL="http://example.test/v1/"\n'
        "  OCR_LLM_MODEL = 'my-model'  \n"
        "OCR_LLM_API_KEY=abc=def\n"
        "this line has no equals sign\n"
        "OCR_LLM_REASONING_EFFORT=\n",
        encoding="utf-8",
    )
    env_reset.setenv("OCR_ENV_FILE", str(env_file))
    _clean_llm_env(env_reset)

    importlib.reload(settings)

    assert settings.LLM_URL == "http://example.test/v1"
    assert settings.LLM_MODEL == "my-model"
    assert settings.LLM_API_KEY == "abc=def"
    assert settings.LLM_REASONING_EFFORT == ""


def test_real_env_var_overrides_file(tmp_path, env_reset):
    env_file = tmp_path / ".env"
    env_file.write_text("OCR_LLM_MODEL=from-file\n", encoding="utf-8")
    env_reset.setenv("OCR_ENV_FILE", str(env_file))
    _clean_llm_env(env_reset)
    env_reset.setenv("OCR_LLM_MODEL", "from-env")

    importlib.reload(settings)

    assert settings.LLM_MODEL == "from-env"


def test_empty_env_file_path_disables_loading(tmp_path, env_reset):
    env_file = tmp_path / ".env"
    env_file.write_text("OCR_LLM_API_KEY=should-not-load\n", encoding="utf-8")
    env_reset.setenv("OCR_ENV_FILE", "")
    _clean_llm_env(env_reset)

    importlib.reload(settings)

    assert settings.LLM_API_KEY == ""
    assert "OCR_LLM_API_KEY" not in os.environ


def test_missing_file_is_fine_and_defaults_apply(tmp_path, env_reset, capsys):
    env_reset.setenv("OCR_ENV_FILE", str(tmp_path / "does-not-exist.env"))
    _clean_llm_env(env_reset)

    importlib.reload(settings)

    assert settings.LLM_API_KEY == ""
    assert settings.LLM_REASONING_EFFORT == "none"
    assert capsys.readouterr().err == ""


def test_unreadable_file_warns_once_and_continues(tmp_path, env_reset, capsys):
    env_reset.setenv("OCR_ENV_FILE", str(tmp_path))  # a directory cannot be read as a file
    _clean_llm_env(env_reset)

    importlib.reload(settings)

    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert settings.LLM_API_KEY == ""
