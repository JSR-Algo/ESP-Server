import types

from core.utils import tts


def test_create_instance_waits_for_provider_import_when_module_is_partial(monkeypatch):
    lib_name = "core.providers.tts.edge"

    class FakeProvider:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    initialized_module = types.SimpleNamespace(TTSProvider=FakeProvider)
    monkeypatch.setitem(__import__("sys").modules, lib_name, types.SimpleNamespace())

    calls = []

    def fake_import_module(name):
        calls.append(name)
        return initialized_module

    monkeypatch.setattr(tts.importlib, "import_module", fake_import_module)

    instance = tts.create_instance("edge", {"voice": "test"}, True)

    assert isinstance(instance, FakeProvider)
    assert calls == [lib_name]
