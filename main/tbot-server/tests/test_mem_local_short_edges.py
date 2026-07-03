import json
import importlib
import sys
import types

import pytest
import yaml


class _Logger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def debug(self, message):
        self.records.append(("debug", message))

    def info(self, message):
        self.records.append(("info", message))

    def error(self, message):
        self.records.append(("error", message))


class _LLM:
    model_name = "memory-model"
    api_key = "set"

    def __init__(self, response):
        self.response = response
        self.calls = []

    def response_no_stream(self, prompt, message, **kwargs):
        self.calls.append((prompt, message, kwargs))
        return self.response


def _message(role, content):
    return types.SimpleNamespace(role=role, content=content)


def _import_memory(monkeypatch, tmp_path):
    module_name = "core.providers.memory.mem_local_short.mem_local_short"
    for dependency_name, required_attr in (
        ("config.manage_api_client", "generate_and_save_chat_summary"),
        ("core.utils.util", "check_model_key"),
    ):
        dependency = sys.modules.get(dependency_name)
        if dependency is not None and not hasattr(dependency, required_attr):
            sys.modules.pop(dependency_name, None)
    sys.modules.pop(module_name, None)
    import core.providers.memory.base as base_module

    logger = _Logger()
    monkeypatch.setattr(base_module, "logger", logger)

    config_loader = importlib.import_module("config.config_loader")
    api_client = importlib.import_module("config.manage_api_client")

    summaries = []
    monkeypatch.setattr(config_loader, "get_project_dir", lambda: str(tmp_path) + "/")

    async def fake_generate_and_save_chat_summary(summary_id):
        summaries.append(summary_id)

    monkeypatch.setattr(api_client, "generate_and_save_chat_summary", fake_generate_and_save_chat_summary)

    module = __import__(module_name, fromlist=["MemoryProvider"])
    monkeypatch.setattr(module, "logger", logger)
    return module, logger, summaries


def test_extract_json_data_accepts_fenced_plain_and_invalid_json(monkeypatch, tmp_path, capsys):
    module, _, _ = _import_memory(monkeypatch, tmp_path)
    assert module.extract_json_data('```json\n{"ok": true}\n```') == '\n{"ok": true}\n'
    assert module.extract_json_data('{"ok": true}') == '{"ok": true}'
    assert module.extract_json_data("not json") == ""
    assert "Error:" in capsys.readouterr().out


def test_memory_load_save_init_and_query_paths(monkeypatch, tmp_path):
    module, _, _ = _import_memory(monkeypatch, tmp_path)
    memory_file = tmp_path / "data" / ".memory.yaml"
    memory_file.parent.mkdir()
    memory_file.write_text(yaml.safe_dump({None: "boot memory", "role-1": "stored memory"}), encoding="utf-8")

    provider = module.MemoryProvider({}, summary_memory=None)
    assert provider.short_memory == "boot memory"

    provider.init_memory("role-1", _LLM('{"fresh": true}'), summary_memory=None, save_to_file=True)
    assert provider.short_memory == "stored memory"
    assert provider.role_id == "role-1"
    assert provider.llm.model_name == "memory-model"

    provider.short_memory = "new memory"
    provider.save_memory_to_file()
    assert yaml.safe_load(memory_file.read_text(encoding="utf-8"))["role-1"] == "new memory"
    assert awaitable_result(provider.query_memory("anything")) == "new memory"

    provider.init_memory("role-2", _LLM("{}"), summary_memory="api memory", save_to_file=False)
    assert provider.short_memory == "api memory"


def awaitable_result(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.mark.asyncio
async def test_save_memory_llm_file_mode_formats_messages_and_persists(monkeypatch, tmp_path):
    module, logger, _ = _import_memory(monkeypatch, tmp_path)
    (tmp_path / "data").mkdir()
    llm = _LLM('```json\n{"memory": ["likes robots"]}\n```')
    provider = module.MemoryProvider({}, summary_memory="old memory")
    provider.init_memory("role-1", llm, summary_memory="old memory", save_to_file=True)

    result = await provider.save_memory(
        [
            _message("user", '{"content": "hello", "emotion": "happy"}'),
            _message("assistant", "hi"),
            _message("tool", "ignored"),
            _message("user", "{bad json"),
        ],
        session_id="session-1",
    )

    assert json.loads(result) == {"memory": ["likes robots"]}
    assert "User: hello" in llm.calls[0][1]
    assert "Assistant: hi" in llm.calls[0][1]
    assert "Historical memory:\nold memory" in llm.calls[0][1]
    assert "Current time:" in llm.calls[0][1]
    persisted = yaml.safe_load((tmp_path / "data" / ".memory.yaml").read_text(encoding="utf-8"))
    assert json.loads(persisted["role-1"]) == {"memory": ["likes robots"]}
    assert any(level == "info" and "Save memory successful" in message for level, message in logger.records)


@pytest.mark.asyncio
async def test_save_memory_short_no_llm_bad_key_bad_json_and_backend_summary(monkeypatch, tmp_path):
    module, logger, summaries = _import_memory(monkeypatch, tmp_path)
    provider = module.MemoryProvider({}, summary_memory="")
    provider.llm = _LLM("{}")
    assert await provider.save_memory([_message("user", "only one")]) is None

    provider.llm = None
    assert await provider.save_memory([_message("user", "a"), _message("assistant", "b")]) is None
    assert any(level == "error" and "LLM is not set" in message for level, message in logger.records)

    bad_llm = _LLM("not json")
    bad_llm.api_key = "Your API key"
    provider.init_memory("role-1", bad_llm, summary_memory="", save_to_file=True)
    result = await provider.save_memory([_message("user", "{bad}"), _message("assistant", "b")])
    assert result == ""
    assert "User: {bad}" in bad_llm.calls[0][1]
    assert any(level == "error" and "API key" in message for level, message in logger.records)
    assert any(level == "error" and "Error in saving memory" in message for level, message in logger.records)

    provider.init_memory("role-2", _LLM("{}"), summary_memory="remote", save_to_file=False)
    assert await provider.save_memory([_message("user", "a"), _message("assistant", "b")], session_id="session-2") == "remote"
    assert summaries == ["session-2"]
    assert await provider.save_memory([_message("user", "a"), _message("assistant", "b")]) == "remote"
    assert summaries == ["session-2", "role-2"]
