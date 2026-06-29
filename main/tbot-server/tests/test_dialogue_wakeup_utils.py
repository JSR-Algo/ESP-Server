import importlib.util
import sys
import types
from pathlib import Path

import pytest

import core.utils as core_utils
from core.utils import wakeup_word


def _load_real_dialogue(monkeypatch):
    module_path = Path(__file__).resolve().parents[1] / "core" / "utils" / "dialogue.py"
    spec = importlib.util.spec_from_file_location("core.utils.dialogue", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    monkeypatch.setitem(sys.modules, "core.utils.dialogue", module)
    monkeypatch.setattr(core_utils, "dialogue", module, raising=False)
    spec.loader.exec_module(module)
    return module


def _new_wakeup_config(tmp_path):
    config = wakeup_word.WakeupWordsConfig.__new__(wakeup_word.WakeupWordsConfig)
    config.config_file = str(tmp_path / "data" / ".wakeup_words.yaml")
    config.assets_dir = str(tmp_path / "assets")
    config._config_cache = None
    config._last_load_time = 0
    config._cache_ttl = 1
    config._lock_timeout = 0.01
    return config


def test_dialogue_builds_system_context_tools_and_speaker_metadata(monkeypatch):
    dialogue_module = _load_real_dialogue(monkeypatch)
    dialogue = dialogue_module.Dialogue()
    assert dialogue.current_time

    default_message = dialogue_module.Message("user", "hello")
    assert default_message.uniq_id
    assert default_message.content == "hello"

    dialogue.update_system_message("Static<context>Now {{current_time}} <memory>old</memory>")
    dialogue.update_system_message("Static<context>Now {{current_time}} <memory>old</memory>")
    dialogue.put(dialogue_module.Message("assistant", tool_calls=[{"id": "fewshot-call"}], is_temporary=True))
    dialogue.put(dialogue_module.Message("assistant", tool_calls=[types.SimpleNamespace(id="real-call")]))
    dialogue.put(dialogue_module.Message("tool", "finished", tool_call_id="real-call"))
    dialogue.put(dialogue_module.Message("tool", "generated-id"))
    dialogue.put(dialogue_module.Message("user", "question"))

    messages = dialogue.get_llm_dialogue_with_memory(
        "fresh memory",
        {"speakers": ["1, Alice, child", object(), "2,Bob"]},
    )

    assert messages[0] == {"role": "system", "content": "Static"}
    dynamic_context = next(
        msg["content"] for msg in messages if msg["role"] == "system" and "fresh memory" in msg["content"]
    )
    assert "Alice:child" in dynamic_context
    assert "Bob:" in dynamic_context
    assert messages[1] == {"role": "assistant", "tool_calls": [{"id": "fewshot-call"}]}
    assert any(msg.get("tool_call_id") == "fewshot-call" for msg in messages)
    assert any(msg == {"role": "user", "content": "question"} for msg in messages)
    assert any(msg.get("content") == "generated-id" and msg.get("tool_call_id") for msg in messages)

    no_context = dialogue_module.Dialogue()
    no_context.update_system_message("Only static")
    no_context.put(dialogue_module.Message("user", "hi"))
    assert no_context.get_llm_dialogue() == [
        {"role": "system", "content": "Only static"},
        {"role": "user", "content": "hi"},
    ]

    no_speaker_config = dialogue_module.Dialogue()
    no_speaker_config.update_system_message("Static<context>dynamic")
    assert no_speaker_config.get_llm_dialogue_with_memory(None, None) == [
        {"role": "system", "content": "Static"},
        {"role": "system", "content": "<context>dynamic"},
    ]

    no_system = dialogue_module.Dialogue()
    no_system.put(dialogue_module.Message("user", "bare"))
    assert no_system.get_llm_dialogue_with_memory(None, None) == [{"role": "user", "content": "bare"}]


def test_wakeup_file_lock_success_timeout_and_config_roundtrip(tmp_path, monkeypatch):
    with monkeypatch.context() as scoped:
        scoped.setattr(wakeup_word.WakeupWordsConfig, "_ensure_directories", lambda self: None)
        default_config = wakeup_word.WakeupWordsConfig()
    assert default_config.config_file == "data/.wakeup_words.yaml"
    assert default_config.assets_dir == "config/assets/wakeup_words"

    locked = []
    unlocked = []
    monkeypatch.setattr(wakeup_word.portalocker, "lock", lambda file, flags: locked.append((file, flags)))
    monkeypatch.setattr(wakeup_word.portalocker, "unlock", lambda file: unlocked.append(file))
    file_obj = object()
    with wakeup_word.FileLock(file_obj) as locked_file:
        assert locked_file is file_obj
    assert locked and unlocked == [file_obj]

    attempts = []

    def always_locked(file, flags):
        attempts.append((file, flags))
        raise wakeup_word.portalocker.LockException("busy")

    times = iter([0, 0.2])
    with monkeypatch.context() as scoped:
        scoped.setattr(wakeup_word.portalocker, "lock", always_locked)
        scoped.setattr(wakeup_word.time, "time", lambda: next(times))
        scoped.setattr(wakeup_word.time, "sleep", lambda seconds: None)
        times = iter([0, 0.05, 0.2])
        with pytest.raises(TimeoutError, match="timed out"):
            with wakeup_word.FileLock(file_obj, timeout=0.1):
                pass
    assert attempts

    config = _new_wakeup_config(tmp_path)
    config._ensure_directories()
    config._save_config({"voice": {"text": "hello"}})
    assert config._load_config() == {"voice": {"text": "hello"}}
    config._config_cache = {"cached": True}
    config._last_load_time = wakeup_word.time.time()
    assert config._load_config() == {"cached": True}


def test_wakeup_response_update_generate_and_error_paths(tmp_path, monkeypatch, capsys):
    config = _new_wakeup_config(tmp_path)
    config._ensure_directories()
    assert config.get_wakeup_response("missing") is None

    small_file = tmp_path / "small.wav"
    small_file.write_bytes(b"tiny")
    config.update_wakeup_response("hi", str(small_file), "hello🙂")
    assert config.get_wakeup_response("hi") is None

    large_file = tmp_path / "large.wav"
    large_file.write_bytes(b"x" * (15 * 1024))
    config.update_wakeup_response("hi", str(large_file), "hello🙂")
    response = config.get_wakeup_response("hi")
    assert response["file_path"] == str(large_file)
    assert response["text"] == "hello"

    generated = config.generate_file_path("hi")
    assert generated.startswith(config.assets_dir)
    Path(generated).write_bytes(b"old")
    assert config.generate_file_path("hi") == generated
    assert not Path(generated).exists()

    Path(generated).write_bytes(b"old")
    monkeypatch.setattr(wakeup_word.os, "remove", lambda path: (_ for _ in ()).throw(RuntimeError("remove denied")))
    with pytest.raises(RuntimeError, match="remove denied"):
        config.generate_file_path("hi")
    assert "Failed to delete existing audio file" in capsys.readouterr().out

    monkeypatch.setattr(config, "_load_config", lambda: (_ for _ in ()).throw(RuntimeError("load denied")))
    with pytest.raises(RuntimeError, match="load denied"):
        config.update_wakeup_response("hi", str(large_file), "hello")
    assert "Update wake word reply config failed" in capsys.readouterr().out


def test_wakeup_load_and_save_report_io_and_unknown_errors(tmp_path, monkeypatch, capsys):
    config = _new_wakeup_config(tmp_path)

    def raise_io(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", raise_io)
    assert config._load_config() == {}
    assert "Load config file failed" in capsys.readouterr().out
    with pytest.raises(OSError, match="disk full"):
        config._save_config({})
    assert "Save config file failed" in capsys.readouterr().out

    def raise_unknown(*args, **kwargs):
        raise RuntimeError("unknown")

    monkeypatch.setattr("builtins.open", raise_unknown)
    assert config._load_config() == {}
    assert "Unknown error loading config file" in capsys.readouterr().out
    with pytest.raises(RuntimeError, match="unknown"):
        config._save_config({})
    assert "Unknown error saving config file" in capsys.readouterr().out

    monkeypatch.setattr(wakeup_word.hashlib, "md5", lambda data: (_ for _ in ()).throw(RuntimeError("hash denied")))
    with pytest.raises(RuntimeError, match="hash denied"):
        config.generate_file_path("hi")
    assert "Generate audio file path failed" in capsys.readouterr().out
