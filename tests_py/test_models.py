from server.llm import LLMError, LLMUnavailable
from server.models import ModelCatalog, short_name


class FakeModelsLLM:
    """Answers list_models with `ids`, or raises `error` when set. Counts the calls."""

    def __init__(self, ids=None, error=None):
        self.ids = ids or []
        self.error = error
        self.calls = 0

    def list_models(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.ids)


def test_short_name_strips_provider_and_quantization():
    assert short_name("unsloth/qwen3.8-27b-nvfp4") == "qwen3.8-27b"
    assert short_name("unsloth/qwen3.5-4b") == "qwen3.5-4b"
    assert short_name("gpt-4o") == "gpt-4o"
    assert short_name("org/sub/Llama-3-8B-GGUF") == "Llama-3-8B"
    assert short_name("m-q4_k_m") == "m"


def test_default_is_first_even_when_api_lists_it_later():
    llm = FakeModelsLLM(ids=["x", "y", "d-model"])
    catalog = ModelCatalog(llm, "d-model")

    assert [m["id"] for m in catalog.list()] == ["d-model", "x", "y"]


def test_default_is_added_when_api_omits_it():
    llm = FakeModelsLLM(ids=["x"])
    catalog = ModelCatalog(llm, "unsloth/qwen3.5-4b")

    assert catalog.list() == [
        {"id": "unsloth/qwen3.5-4b", "name": "qwen3.5-4b"},
        {"id": "x", "name": "x"},
    ]


def test_list_is_cached_for_the_ttl():
    clock = {"t": 1000.0}
    llm = FakeModelsLLM(ids=["x"])
    catalog = ModelCatalog(llm, "d", ttl=600, now=lambda: clock["t"])

    catalog.list()
    clock["t"] = 1100.0
    catalog.list()
    assert llm.calls == 1

    clock["t"] = 1601.0
    catalog.list()
    assert llm.calls == 2


def test_api_error_after_good_fetch_returns_last_good_list():
    clock = {"t": 1000.0}
    llm = FakeModelsLLM(ids=["x"])
    catalog = ModelCatalog(llm, "d", ttl=600, now=lambda: clock["t"])
    catalog.list()

    llm.error = LLMError("down")
    clock["t"] = 2000.0
    assert [m["id"] for m in catalog.list()] == ["d", "x"]


def test_api_error_without_good_fetch_returns_only_default():
    llm = FakeModelsLLM(error=LLMUnavailable("down"))
    catalog = ModelCatalog(llm, "unsloth/qwen3.5-4b")

    assert catalog.list() == [{"id": "unsloth/qwen3.5-4b", "name": "qwen3.5-4b"}]


def test_allowed_only_for_listed_ids():
    catalog = ModelCatalog(FakeModelsLLM(ids=["x"]), "d")

    assert catalog.allowed("x") is True
    assert catalog.allowed("d") is True
    assert catalog.allowed("y") is False


def test_default_property():
    assert ModelCatalog(FakeModelsLLM(), "d").default == "d"
