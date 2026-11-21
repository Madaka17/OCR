import http.client
import json
import urllib.parse
import urllib.request

CONNECT_TIMEOUT_SECONDS = 10


class LLMError(Exception):
    """A bad HTTP status, a request timeout, invalid JSON in the content, or a missing choices list."""


class LLMUnavailable(LLMError):
    """The server could not be reached: refused, reset, DNS failure, or no connection
    within CONNECT_TIMEOUT_SECONDS.

    A timeout is a plain LLMError: the server is up but this request is too slow,
    so it must count as an attempt instead of waiting and retrying forever.
    """


class LLMClient:
    def __init__(self, url: str, model: str, timeout: float = 600):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, prompt: str, schema: dict, name: str, max_tokens: int) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "reasoning_effort": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": name, "strict": True, "schema": schema},
            },
        }
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

    def ping(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.url}/models", timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _send(self, path: str, data: bytes) -> bytes:
        """POST data. Connect failures mean the server is down (LLMUnavailable); a
        server that accepts the request but answers too slowly is an LLMError."""
        parts = urllib.parse.urlsplit(self.url + path)
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = cls(parts.hostname, parts.port, timeout=min(CONNECT_TIMEOUT_SECONDS, self.timeout))
        try:
            try:
                conn.connect()
            except OSError as exc:
                raise LLMUnavailable(f"LLM server unreachable: {exc}") from exc
            conn.sock.settimeout(self.timeout)
            try:
                conn.request("POST", parts.path, body=data, headers={"Content-Type": "application/json"})
                resp = conn.getresponse()
                raw = resp.read()
            except TimeoutError as exc:
                raise LLMError(f"LLM request timed out after {self.timeout:g}s") from exc
            except (OSError, http.client.HTTPException) as exc:
                raise LLMUnavailable(f"LLM server unavailable: {exc!r}") from exc
            if resp.status >= 400:
                raise LLMError(f"HTTP {resp.status} from LLM server")
            return raw
        finally:
            conn.close()
