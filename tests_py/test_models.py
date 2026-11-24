import pytest

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


@pytest.mark.parametrize("model_id, expected", [
    ("a/b-mxfp4", "b"),
    ("x-awq", "x"),
    ("x-gptq", "x"),
    ("x-mlx", "x"),
    ("x-exl2", "x"),
    ("foo-fp3", "foo-fp3"),
    ("a-gguf-awq", "a-gguf"),
])
def test_short_name_strips_one_known_suffix(model_id, expected):
    assert short_name(model_id) == expected


def test_default_is_first_even_when_api_lists_it_later():
    llm = FakeModelsLLM(ids=["x", "y", "d-model"])
    catalog = ModelCatalog(llm, "d-model")

    assert [m["id"] for m in catalog.list()] == ["d-model", "x", "y"]


def test_default_is_added_when_api_omits_it():
    llm = FakeModelsLLM(ids=["x"])
    catalog = ModelCatalog(llm, "unsloth/qwen3.5-4b")

    assert catalog.list() == [
        {"id": "unsloth/qwen3.5-4b", "name": "qwen3.5-4b", "key": "1"},
        {"id": "x", "name": "x", "key": "1"},
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

    assert catalog.list() == [{"id": "unsloth/qwen3.5-4b", "name": "qwen3.5-4b", "key": "1"}]


def test_failed_refresh_is_retried_after_a_minute():
    clock = {"t": 1000.0}
    llm = FakeModelsLLM(error=LLMUnavailable("down"))
    catalog = ModelCatalog(llm, "d", ttl=600, now=lambda: clock["t"])
    catalog.list()
    assert llm.calls == 1

    clock["t"] = 1030.0
    catalog.list()
    assert llm.calls == 1

    clock["t"] = 1061.0
    llm.error = None
    llm.ids = ["x"]
    assert [m["id"] for m in catalog.list()] == ["d", "x"]
    assert llm.calls == 2


def test_unexpected_error_falls_back_like_api_error():
    class Broken:
        def list_models(self):
            raise RuntimeError("bug")

    assert ModelCatalog(Broken(), "d").list() == [{"id": "d", "name": "d", "key": "1"}]


def test_allowed_only_for_listed_ids():
    catalog = ModelCatalog(FakeModelsLLM(ids=["x"]), "d")

    assert catalog.allowed("x") is True
    assert catalog.allowed("d") is True
    assert catalog.allowed("y") is False


def test_default_property():
    assert ModelCatalog(FakeModelsLLM(), "d").default == "d"


def test_one_key_names_have_no_suffix():
    catalog = ModelCatalog(FakeModelsLLM(ids=["x"]), "d")

    assert catalog.list() == [{"id": "d", "name": "d", "key": "1"}, {"id": "x", "name": "x", "key": "1"}]


def test_two_keys_order_and_names():
    catalog = ModelCatalog(FakeModelsLLM(ids=["x"]), "d",
                           others={"2": FakeModelsLLM(ids=["y", "d"])})

    assert catalog.list() == [
        {"id": "d", "name": "d · key 1", "key": "1"},
        {"id": "x", "name": "x · key 1", "key": "1"},
        {"id": "d", "name": "d · key 2", "key": "2"},
        {"id": "y", "name": "y · key 2", "key": "2"},
    ]


def test_allowed_is_per_key():
    catalog = ModelCatalog(FakeModelsLLM(ids=["x"]), "d", others={"2": FakeModelsLLM(ids=["y"])})

    assert catalog.allowed("y", "2") is True
    assert catalog.allowed("d", "2") is True
    assert catalog.allowed("x", "2") is False
    assert catalog.allowed("y") is False
    assert catalog.allowed("x") is True


def test_unknown_key_is_not_allowed():
    catalog = ModelCatalog(FakeModelsLLM(ids=["x"]), "d", others={"2": FakeModelsLLM(ids=["y"])})

    assert catalog.allowed("d", "3") is False


def test_key_two_down_offers_only_its_default():
    catalog = ModelCatalog(FakeModelsLLM(ids=["x"]), "d",
                           others={"2": FakeModelsLLM(error=LLMUnavailable("down"))})

    assert [(m["id"], m["key"]) for m in catalog.list()] == [("d", "1"), ("x", "1"), ("d", "2")]
    assert catalog.allowed("d", "2") is True
    assert catalog.allowed("y", "2") is False


def test_each_key_has_its_own_cache():
    clock = {"t": 1000.0}
    k1 = FakeModelsLLM(ids=["x"])
    k2 = FakeModelsLLM(ids=["y"])
    catalog = ModelCatalog(k1, "d", ttl=600, now=lambda: clock["t"], others={"2": k2})

    catalog.list()
    catalog.list()
    assert (k1.calls, k2.calls) == (1, 1)
