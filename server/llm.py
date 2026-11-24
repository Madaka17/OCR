import http.client
import json
import sys
import urllib.parse
import urllib.request

CONNECT_TIMEOUT_SECONDS = 10
MODELS_TIMEOUT_SECONDS = 15


class LLMError(Exception):
    """A bad HTTP status, a request timeout, invalid JSON in the content, or a missing choices list."""


class LLMUnavailable(LLMError):
    """The server could not be reached: refused, reset, DNS failure, or no connection
    within CONNECT_TIMEOUT_SECONDS.

    A timeout is a plain LLMError: the server is up but this request is too slow,
    so it must count as an attempt instead of waiting and retrying forever.
    """


def _is_chat_model(item: dict) -> bool:
    """False for entries that are not usable for chat: an inactive model, or one whose
    supported_endpoints list lacks chat.completions. An entry without those fields stays."""
    if "status" in item and item["status"] != "active":
        return False
    endpoints = item.get("supported_endpoints")
    if isinstance(endpoints, list) and "chat.completions" not in endpoints:
        return False
    return True


class LLMClient:
    def __init__(
        self,
        url: str,
        model: str,
        timeout: float = 600,
        api_key: str = "",
        reasoning_effort: str | None = "none",
    ):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.api_key = api_key
        self.reasoning_effort = reasoning_effort

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def complete(self, prompt: str, schema: dict, name: str, max_tokens: int) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        raw = self._send("/chat/completions", json.dumps(body).encode("utf-8"))
        try:
            payload = json.loads(raw)
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed completion response: {exc}") from exc
        try:
            return json.loads(content)
        except (ValueError, TypeError) as exc:
            raise LLMError(f"content is not valid JSON: {exc}") from exc

    def with_model(self, model: str) -> "LLMClient":
        return LLMClient(self.url, model, timeout=self.timeout, api_key=self.api_key,
                         reasoning_effort=self.reasoning_effort)

    def list_models(self) -> list[str]:
        """The model ids the server offers, in API order, without duplicates."""
        raw = self._send("/models", None, timeout=min(MODELS_TIMEOUT_SECONDS, self.timeout))
        try:
            items = json.loads(raw)["data"]
            if not isinstance(items, list):
                raise TypeError("data is not a list")
            ids = [item["id"] for item in items if isinstance(item["id"], str) and _is_chat_model(item)]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed models response: {exc}") from exc
        return list(dict.fromkeys(ids))

    def ping(self) -> bool:
        try:
            request = urllib.request.Request(f"{self.url}/models", headers=self._auth_headers())
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _send(self, path: str, data: bytes | None, timeout: float | None = None) -> bytes:
        """POST data, or GET when data is None. Connect failures mean the server is down
        (LLMUnavailable); a server that accepts the request but answers too slowly is an
        LLMError."""
        timeout = self.timeout if timeout is None else timeout
        method = "GET" if data is None else "POST"
        parts = urllib.parse.urlsplit(self.url + path)
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname, parts.port, timeout=min(CONNECT_TIMEOUT_SECONDS, timeout))
        try:
            try:
                conn.connect()
            except OSError as exc:
                raise LLMUnavailable(f"LLM server unreachable: {exc}") from exc
            conn.sock.settimeout(timeout)
            try:
                headers = {"Content-Type": "application/json", **self._auth_headers()}
                conn.request(method, parts.path, body=data, headers=headers)
                resp = conn.getresponse()
                raw = resp.read()
            except TimeoutError as exc:
                raise LLMError(f"LLM request timed out after {timeout:g}s") from exc
            except (OSError, http.client.HTTPException) as exc:
                raise LLMUnavailable(f"LLM server unavailable: {exc!r}") from exc
            if resp.status >= 400:
                text = raw.decode("utf-8", errors="replace")
                if self.api_key:
                    text = text.replace(self.api_key, "***")
                detail = text[:300]
                raise LLMError(f"HTTP {resp.status} from LLM server: {detail}")
            return raw
        finally:
            conn.close()


class FailoverLLM:
    """Tries each client in order and returns the first answer. Only LLMError is a failure to move past."""

    def __init__(self, clients: list):
        self.clients = list(clients)

    @property
    def model(self) -> str:
        return self.clients[0].model

    def complete(self, prompt: str, schema: dict, name: str, max_tokens: int) -> dict:
        errors = []
        for index, client in enumerate(self.clients, 1):
            try:
                return client.complete(prompt, schema, name, max_tokens)
            except LLMError as exc:
                errors.append(exc)
                if index < len(self.clients):
                    print(f"[llm] key {index} failed, trying key {index + 1}: {str(exc)[:200]}",
                          file=sys.stderr, flush=True)
        if all(isinstance(exc, LLMUnavailable) for exc in errors):
            raise errors[-1]
        raise next(exc for exc in reversed(errors) if not isinstance(exc, LLMUnavailable))

    def with_model(self, model: str) -> "FailoverLLM":
        return FailoverLLM([client.with_model(model) for client in self.clients])

    def list_models(self) -> list[str]:
        """The union of every client's model ids, in order, without duplicates."""
        ids: list[str] = []
        errors: list[Exception] = []
        for client in self.clients:
            try:
                ids.extend(client.list_models())
            except Exception as exc:
                errors.append(exc)
        if errors and len(errors) == len(self.clients):
            raise errors[-1]
        return list(dict.fromkeys(ids))
