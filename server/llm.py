import json
import urllib.error
import urllib.request


class LLMError(Exception):
    """A bad HTTP status, invalid JSON in the content, or a missing choices list."""


class LLMUnavailable(LLMError):
    """The server could not be reached: refused, reset, DNS failure, or timeout."""


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
        request = urllib.request.Request(
            f"{self.url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        raw = self._send(request)
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

    def _send(self, request: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            raise LLMError(f"HTTP {exc.code} from LLM server") from exc
        except urllib.error.URLError as exc:
            raise LLMUnavailable(f"LLM server unreachable: {exc.reason}") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise LLMUnavailable(f"LLM server unavailable: {exc}") from exc
