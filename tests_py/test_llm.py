import importlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server import settings
from server.llm import LLMClient, LLMError, LLMUnavailable

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}


class _StubState:
    status = 200
    body = b"{}"
    recorded = []
    paths = []


def _make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _reply(self):
            _StubState.paths.append(self.path)
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
def reset_settings():
    yield
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
