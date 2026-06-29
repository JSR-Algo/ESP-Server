import importlib.util
import sys
import types
from pathlib import Path

import pytest

from core.api.base_handler import BaseHandler


class _FakeProvider:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _exercise_factory(monkeypatch, module, class_name, lib_name, provider_attr):
    calls = []
    provider_module = types.SimpleNamespace(**{provider_attr: _FakeProvider})

    monkeypatch.delitem(sys.modules, lib_name, raising=False)
    monkeypatch.setattr(module.os.path, "exists", lambda _path: True)

    def fake_import_module(name):
        calls.append(name)
        return provider_module

    monkeypatch.setattr(module.importlib, "import_module", fake_import_module)

    first = module.create_instance(class_name, "arg", enabled=True)
    second = module.create_instance(class_name, "cached")

    assert isinstance(first, _FakeProvider)
    assert first.args == ("arg",)
    assert first.kwargs == {"enabled": True}
    assert second.args == ("cached",)
    assert calls == [lib_name]

    monkeypatch.setattr(module.os.path, "exists", lambda _path: False)
    monkeypatch.delitem(sys.modules, lib_name, raising=False)
    with pytest.raises(ValueError):
        module.create_instance(class_name)


def _load_real_loadplugins_module():
    path = Path(__file__).resolve().parents[1] / "plugins_func" / "loadplugins.py"
    spec = importlib.util.spec_from_file_location("tests.real_loadplugins", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_factories_import_cache_and_reject_unknown_types(monkeypatch):
    from core.utils import intent, llm, memory, vad, vllm
    from core.utils import asr

    cases = [
        (asr, "demo_asr", "core.providers.asr.demo_asr", "ASRProvider"),
        (intent, "demo_intent", "core.providers.intent.demo_intent.demo_intent", "IntentProvider"),
        (llm, "demo_llm", "core.providers.llm.demo_llm.demo_llm", "LLMProvider"),
        (memory, "demo_memory", "core.providers.memory.demo_memory.demo_memory", "MemoryProvider"),
        (vllm, "demo_vllm", "core.providers.vllm.demo_vllm", "VLLMProvider"),
        (vad, "demo_vad", "core.providers.vad.demo_vad", "VADProvider"),
    ]

    for module, class_name, lib_name, provider_attr in cases:
        _exercise_factory(monkeypatch, module, class_name, lib_name, provider_attr)


def test_auto_import_modules_loads_each_discovered_module(monkeypatch):
    loadplugins = _load_real_loadplugins_module()
    package = types.SimpleNamespace(__path__=["/fake/plugins"])
    imported = []

    def fake_import_module(name):
        imported.append(name)
        if name == "plugins_func.functions":
            return package
        return types.SimpleNamespace()

    monkeypatch.setattr(loadplugins.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(
        loadplugins.pkgutil,
        "iter_modules",
        lambda path: [(None, "alpha", False), (None, "beta", False)],
    )

    loadplugins.auto_import_modules("plugins_func.functions")

    assert imported == [
        "plugins_func.functions",
        "plugins_func.functions.alpha",
        "plugins_func.functions.beta",
    ]


@pytest.mark.asyncio
async def test_base_handler_options_response_sets_cors_and_allowed_methods():
    handler = BaseHandler({"server": {}})

    response = await handler.handle_options(None)

    assert response.body == b""
    assert response.content_type == "text/plain"
    assert response.headers["Access-Control-Allow-Headers"] == (
        "client-id, content-type, device-id, authorization"
    )
    assert response.headers["Access-Control-Allow-Credentials"] == "true"
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Access-Control-Allow-Methods"] == "GET, POST, OPTIONS"
