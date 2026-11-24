"""The AI models a job may be summarized with: the server's list, cached for a while."""

import re
import threading
import time

# Quantization and packaging suffixes that people do not need to see in a name.
_SUFFIX_RE = re.compile(
    r"-(?:nvfp4|mxfp4|fp4|fp8|fp16|bf16|int4|int8|awq|gptq|gguf|mlx|exl2|q[0-9][0-9a-z_]*)$",
    re.IGNORECASE,
)
RETRY_SECONDS = 60  # a failed refresh is retried this soon, not after the full ttl


def short_name(model_id: str) -> str:
    """A readable name: the last path part, without one trailing quantization or GGUF suffix."""
    return _SUFFIX_RE.sub("", model_id.rsplit("/", 1)[-1])


class _KeyModels:
    """One API key's model list. It has its own cache, ttl, retry and fallback."""

    def __init__(self, llm, default: str, ttl: float, now):
        self.llm = llm
        self._default = default
        self.ttl = ttl
        self._now = now
        self._mu = threading.Lock()
        self._ids: list[str] | None = None  # the last good list, default first
        self._next_refresh: float | None = None

    def _with_default_first(self, ids: list[str]) -> list[str]:
        rest = [i for i in ids if i != self._default]
        return [self._default, *rest]

    def ids(self) -> list[str]:
        with self._mu:
            now = self._now()
            if self._next_refresh is None or now >= self._next_refresh:
                try:
                    self._ids = self._with_default_first(self.llm.list_models())
                    self._next_refresh = now + self.ttl
                except Exception:  # noqa: BLE001 - the catalog must keep serving whatever happens
                    self._next_refresh = now + RETRY_SECONDS
            return self._ids if self._ids is not None else [self._default]


class ModelCatalog:
    """The model ids the API keys offer together, each id once, default first. Refreshed every `ttl` seconds.

    Key "1" is `llm`. `others` maps more key names to their clients. A failed refresh
    keeps that key's last good list. Before any good list exists, only the default is
    offered for that key.
    """

    def __init__(self, llm, default: str, ttl: float = 600, now=time.time, others: dict | None = None):
        self._default = default
        self._sources: dict[str, _KeyModels] = {"1": _KeyModels(llm, default, ttl, now)}
        for name, client in (others or {}).items():
            self._sources[name] = _KeyModels(client, default, ttl, now)

    @property
    def default(self) -> str:
        return self._default

    def _union(self) -> list[str]:
        ids: dict[str, None] = {}
        for source in self._sources.values():
            ids.update(dict.fromkeys(source.ids()))
        return list(ids)

    def list(self) -> list[dict]:
        return [{"id": model_id, "name": short_name(model_id)} for model_id in self._union()]

    def allowed(self, model_id) -> bool:
        return model_id in self._union()
