"""The AI models a job may be summarized with: the server's list, cached for a while."""

import re
import threading
import time

from server.llm import LLMError

# Quantization and packaging suffixes that people do not need to see in a name.
_SUFFIX_RE = re.compile(r"-(?:gguf|nvfp4|(?:q|int|fp|bf)\d\w*)$", re.IGNORECASE)


def short_name(model_id: str) -> str:
    """A readable name: the last path part, without quantization or GGUF suffixes."""
    name = model_id.rsplit("/", 1)[-1]
    while True:
        stripped = _SUFFIX_RE.sub("", name)
        if stripped == name:
            return name
        name = stripped


class ModelCatalog:
    """The model ids the LLM server offers, default first. Refreshed every `ttl` seconds.

    A failed refresh keeps the last good list. Before any good list exists, only the
    default is offered.
    """

    def __init__(self, llm, default: str, ttl: float = 600, now=time.time):
        self.llm = llm
        self._default = default
        self.ttl = ttl
        self._now = now
        self._mu = threading.Lock()
        self._ids: list[str] | None = None  # the last good list, default first
        self._fetched_at: float | None = None

    @property
    def default(self) -> str:
        return self._default

    def _with_default_first(self, ids: list[str]) -> list[str]:
        rest = [i for i in ids if i != self._default]
        return [self._default, *rest]

    def _current(self) -> list[str]:
        with self._mu:
            now = self._now()
            if self._fetched_at is not None and now - self._fetched_at < self.ttl:
                return self._ids if self._ids is not None else [self._default]
            self._fetched_at = now
            try:
                self._ids = self._with_default_first(self.llm.list_models())
            except LLMError:
                pass  # keep the last good list, or fall back to the default alone
            return self._ids if self._ids is not None else [self._default]

    def list(self) -> list[dict]:
        return [{"id": i, "name": short_name(i)} for i in self._current()]

    def allowed(self, model_id) -> bool:
        return model_id in self._current()
