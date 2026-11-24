import importlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server import settings
from server.llm import FailoverLLM, LLMClient, LLMError, LLMUnavailable

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}


class _StubState:
    status = 200
    body = b"{}"
    recorded = []
    paths = []
    auth = []


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _reply(self):
            _StubState.paths.append(self.path)
            _StubState.auth.append(self.headers.get("Authorization"))
            self.send_response(_StubState.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_StubState.body)))
            self.end_headers()
            self.wfile.write(_StubState.body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            _StubState.recorded.append(json.loads(self.rfile.read(length)))
            self._reply()

        def do_GET(self):
            self._reply()

    return Handler


@pytest.fixture
def stub():
    _StubState.status = 200
    _StubState.body = b"{}"
    _StubState.recorded = []
    _StubState.paths = []
    _StubState.auth = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/v1"
    yield base, _StubState
    server.shutdown()
    server.server_close()


def _reply_content(content):
    return json.dumps({"choices": [{"message": {"content": content}}], "usage": {}}).encode()


def test_complete_sends_body_and_parses_content(stub):
    base, state = stub
    state.body = _reply_content('{"a":1}')

    result = LLMClient(base, "m").complete("hi", SCHEMA, "thing", 50)

    assert result == {"a": 1}
    body = state.recorded[0]
    assert body["reasoning_effort"] == "none"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["name"] == "thing"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert body["response_format"]["type"] == "json_schema"
    assert body["model"] == "m"
    assert body["max_tokens"] == 50
    assert body["temperature"] == 0.2
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert state.paths[0] == "/v1/chat/completions"


def test_api_key_sent_as_bearer(stub):
    base, state = stub
    state.body = _reply_content('{"a":1}')

    LLMClient(base, "m", api_key="k123").complete("hi", SCHEMA, "thing", 50)
    LLMClient(base, "m", api_key="k123").ping()

    assert state.auth == ["Bearer k123", "Bearer k123"]


def test_no_auth_header_without_key(stub):
    base, state = stub
    state.body = _reply_content('{"a":1}')

    LLMClient(base, "m").complete("hi", SCHEMA, "thing", 50)
    LLMClient(base, "m").ping()

    assert state.auth == [None, None]


def test_reasoning_effort_omitted_when_none(stub):
    base, state = stub
    state.body = _reply_content('{"a":1}')

    LLMClient(base, "m", reasoning_effort=None).complete("hi", SCHEMA, "thing", 50)

    assert "reasoning_effort" not in state.recorded[0]


def test_http_error_includes_body_not_key(stub):
    base, state = stub
    state.status = 400
    state.body = b"bad param x"

    with pytest.raises(LLMError) as excinfo:
        LLMClient(base, "m", api_key="k123").complete("hi", SCHEMA, "thing", 50)

    message = str(excinfo.value)
    assert "bad param x" in message
    assert "k123" not in message


def test_http_400_raises_llm_error_not_unavailable(stub):
    base, state = stub
    state.status = 400
    state.body = b'{"error":"bad"}'

    with pytest.raises(LLMError) as excinfo:
        LLMClient(base, "m").complete("hi", SCHEMA, "thing", 50)

    assert not isinstance(excinfo.value, LLMUnavailable)


def test_bad_json_content_raises_llm_error(stub):
    base, state = stub
    state.body = _reply_content("not json")

    with pytest.raises(LLMError):
        LLMClient(base, "m").complete("hi", SCHEMA, "thing", 50)


def test_connection_refused_raises_unavailable():
    client = LLMClient("http://127.0.0.1:9/v1", "m", timeout=2)

    with pytest.raises(LLMUnavailable):
        client.complete("hi", SCHEMA, "thing", 50)


def test_ping_true_and_false(stub):
    base, state = stub
    state.status = 200

    assert LLMClient(base, "m").ping() is True
    assert LLMClient("http://127.0.0.1:9/v1", "m", timeout=2).ping() is False


@pytest.fixture
def reset_settings(monkeypatch):
    yield
    monkeypatch.undo()
    importlib.reload(settings)


def test_settings_defaults(monkeypatch, reset_settings):
    monkeypatch.delenv("OCR_LLM_URL", raising=False)
    monkeypatch.delenv("OCR_LLM_MODEL", raising=False)
    importlib.reload(settings)
    assert settings.LLM_URL == "http://localhost:1235/v1"
    assert settings.LLM_MODEL == "unsloth/qwen3.5-4b"

    monkeypatch.setenv("OCR_LLM_URL", "")
    importlib.reload(settings)
    assert settings.LLM_URL == ""


def test_settings_default_not_leaked_to_later_tests():
    if "OCR_LLM_URL" in os.environ:
        pytest.skip("OCR_LLM_URL is set in the real environment")
    assert settings.LLM_URL == "http://localhost:1235/v1"


def test_read_timeout_raises_llm_error_not_unavailable():
    """A call that runs past the timeout counts as an attempt, so it cannot retry forever."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    accepted = []
    threading.Thread(target=lambda: accepted.append(listener.accept()), daemon=True).start()
    try:
        client = LLMClient(f"http://127.0.0.1:{listener.getsockname()[1]}/v1", "m", timeout=0.5)
        with pytest.raises(LLMError) as info:
            client.complete("hi", SCHEMA, "thing", 50)
        assert not isinstance(info.value, LLMUnavailable)
    finally:
        listener.close()


def test_http_error_body_echoing_key_is_redacted(stub):
    base, state = stub
    state.status = 401
    state.body = b"bad key k123"

    with pytest.raises(LLMError) as excinfo:
        LLMClient(base, "m", api_key="k123").complete("hi", SCHEMA, "thing", 50)

    message = str(excinfo.value)
    assert "***" in message
    assert "k123" not in message


def test_list_models_parses_ids_dedups_and_sends_auth(stub):
    base, state = stub
    state.body = json.dumps({"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}).encode()

    assert LLMClient(base, "m", api_key="k123").list_models() == ["a", "b"]
    assert state.paths[0] == "/v1/models"
    assert state.auth == ["Bearer k123"]


def test_list_models_keeps_only_active_chat_models(stub):
    base, state = stub
    state.body = json.dumps({"data": [
        {"id": "deepseek/chat", "status": "active", "supported_endpoints": ["chat.completions", "responses"]},
        {"id": "paxa/paxa-ocr-lite-v1", "status": "active", "supported_endpoints": ["ocr"]},
        {"id": "paxa/paxa-tts", "status": "active", "supported_endpoints": ["audio.speech"]},
        {"id": "qwen/chat", "status": "active", "supported_endpoints": ["chat.completions"]},
        {"id": "bare"},
    ]}).encode()

    assert LLMClient(base, "m").list_models() == ["deepseek/chat", "qwen/chat", "bare"]


def test_list_models_skips_inactive_entries(stub):
    base, state = stub
    state.body = json.dumps({"data": [
        {"id": "old", "status": "retired", "supported_endpoints": ["chat.completions"]},
        {"id": "new", "status": "active", "supported_endpoints": ["chat.completions"]},
    ]}).encode()

    assert LLMClient(base, "m").list_models() == ["new"]


def test_list_models_error_body_redacts_key(stub):
    base, state = stub
    state.status = 500
    state.body = b"oops k123"

    with pytest.raises(LLMError) as excinfo:
        LLMClient(base, "m", api_key="k123").list_models()

    assert "oops" in str(excinfo.value)
    assert "k123" not in str(excinfo.value)


def test_list_models_without_data_raises_llm_error(stub):
    base, state = stub
    state.body = b'{"models": []}'

    with pytest.raises(LLMError):
        LLMClient(base, "m").list_models()


def test_list_models_connection_refused_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        LLMClient("http://127.0.0.1:9/v1", "m", timeout=2).list_models()


def test_with_model_keeps_url_key_and_settings():
    client = LLMClient("http://h/v1/", "m", timeout=7, api_key="k", reasoning_effort="low")

    other = client.with_model("x")

    assert other is not client
    assert other.model == "x"
    assert other.url == client.url == "http://h/v1"
    assert other.timeout == 7
    assert other.api_key == "k"
    assert other.reasoning_effort == "low"
    assert client.model == "m"


class _Fake:
    """A client stand-in. complete answers `reply` or raises `error`; list_models answers
    `models` or raises `models_error`. Every complete call appends its model to `log`."""

    def __init__(self, reply=None, error=None, models=None, models_error=None, model="m", log=None):
        self.reply = reply
        self.error = error
        self.models = models
        self.models_error = models_error
        self.model = model
        self.log = log if log is not None else []
        self.calls = 0

    def complete(self, prompt, schema, name, max_tokens):
        self.calls += 1
        self.log.append(self.model)
        if self.error is not None:
            raise self.error
        return self.reply

    def list_models(self):
        if self.models_error is not None:
            raise self.models_error
        return list(self.models)

    def with_model(self, model):
        return _Fake(reply=self.reply, error=self.error, models=self.models,
                     models_error=self.models_error, model=model, log=self.log)


def test_failover_first_client_success_never_calls_second():
    first = _Fake(reply={"a": 1})
    second = _Fake(reply={"a": 2})

    assert FailoverLLM([first, second]).complete("hi", SCHEMA, "thing", 50) == {"a": 1}
    assert second.calls == 0


def test_failover_llm_error_uses_second_and_logs_without_key(capsys):
    fake_key = "fake-key-one-123"
    first = _Fake(error=LLMError("HTTP 401 from LLM server: invalid key ***"))
    second = _Fake(reply={"a": 2})

    assert FailoverLLM([first, second]).complete("hi", SCHEMA, "thing", 50) == {"a": 2}

    err = capsys.readouterr().err
    assert "[llm] key 1 failed, trying key 2: HTTP 401 from LLM server: invalid key ***" in err
    assert fake_key not in err


def test_failover_logs_clipped_error_text(capsys):
    first = _Fake(error=LLMError("x" * 500))
    second = _Fake(reply={"a": 2})

    FailoverLLM([first, second]).complete("hi", SCHEMA, "thing", 50)

    line = capsys.readouterr().err.splitlines()[0]
    assert line == "[llm] key 1 failed, trying key 2: " + "x" * 200


def test_failover_both_unavailable_raises_last_unavailable():
    first = _Fake(error=LLMUnavailable("down 1"))
    second = _Fake(error=LLMUnavailable("down 2"))

    with pytest.raises(LLMUnavailable, match="down 2"):
        FailoverLLM([first, second]).complete("hi", SCHEMA, "thing", 50)


def test_failover_unavailable_then_llm_error_raises_llm_error():
    first = _Fake(error=LLMUnavailable("down"))
    second = _Fake(error=LLMError("HTTP 400 bad"))

    with pytest.raises(LLMError, match="HTTP 400 bad") as info:
        FailoverLLM([first, second]).complete("hi", SCHEMA, "thing", 50)
    assert not isinstance(info.value, LLMUnavailable)


def test_failover_llm_error_then_unavailable_raises_the_llm_error():
    first = _Fake(error=LLMError("HTTP 400 bad"))
    second = _Fake(error=LLMUnavailable("down"))

    with pytest.raises(LLMError, match="HTTP 400 bad") as info:
        FailoverLLM([first, second]).complete("hi", SCHEMA, "thing", 50)
    assert not isinstance(info.value, LLMUnavailable)


def test_failover_model_is_the_first_clients_model():
    assert FailoverLLM([_Fake(model="a1"), _Fake(model="b1")]).model == "a1"


def test_failover_with_model_applies_to_every_client():
    log = []
    first = _Fake(error=LLMError("bad"), log=log)
    second = _Fake(reply={"a": 2}, log=log)

    other = FailoverLLM([first, second]).with_model("x")

    assert isinstance(other, FailoverLLM)
    assert other.model == "x"
    assert other.complete("hi", SCHEMA, "thing", 50) == {"a": 2}
    assert log == ["x", "x"]


def test_failover_list_models_is_union_without_duplicates():
    first = _Fake(models=["x", "y"])
    second = _Fake(models=["y", "z"])

    assert FailoverLLM([first, second]).list_models() == ["x", "y", "z"]


def test_failover_list_models_skips_a_failing_client():
    first = _Fake(models_error=LLMUnavailable("down"))
    second = _Fake(models=["z"])

    assert FailoverLLM([first, second]).list_models() == ["z"]


def test_failover_list_models_raises_when_every_client_fails():
    first = _Fake(models_error=LLMUnavailable("down 1"))
    second = _Fake(models_error=LLMUnavailable("down 2"))

    with pytest.raises(LLMUnavailable, match="down 2"):
        FailoverLLM([first, second]).list_models()
