import errno
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from config import logger as logger_module


def test_access_log_token_leak_detector_flags_ws_query_secrets():
    captured_access_log = "\n".join(
        [
            '203.0.113.10 - - "GET /tbot/v1/?device-id=robot-1&client-id=esp32 HTTP/1.1" 101 -',
            '203.0.113.11 - - "GET /tbot/v1/?device-id=robot-1&authorization=Bearer%20abc.def.ghi HTTP/1.1" 101 -',
            '203.0.113.12 - - "GET /tbot/v1/?ws_url=wss%3A%2F%2Fesp.example.com%2Ftbot%2Fv1%2F%3Ftoken%3Dsecret-token HTTP/1.1" 200 -',
            '203.0.113.13 - - "GET /tbot/v1/?websocket_token=secret-token HTTP/1.1" 101 -',
        ]
    )

    leaks = logger_module.find_token_leaks_in_access_log(captured_access_log)

    assert len(leaks) == 3
    assert leaks[0]["line"] == 2
    assert leaks[0]["kind"] == "authorization_query"
    assert leaks[1]["line"] == 3
    assert leaks[1]["kind"] == "token_bearing_ws_url"
    assert leaks[2]["line"] == 4
    assert leaks[2]["kind"] == "token_query"


def test_access_log_token_leak_detector_allows_header_only_and_scrubbed_queries():
    captured_access_log = "\n".join(
        [
            '203.0.113.20 - - "GET /tbot/v1/?device-id=robot-1&client-id=esp32 HTTP/1.1" 101 -',
            '203.0.113.21 - - "GET /tbot/v1/?authorization=[REDACTED] HTTP/1.1" 101 -',
            '203.0.113.22 - - "GET /tbot/v1/?authorization=Bearer%20[REDACTED] HTTP/1.1" 101 -',
            '203.0.113.23 - - "GET /tbot/v1/?ws_url=wss%3A%2F%2Fesp.example.com%2Ftbot%2Fv1%2F HTTP/1.1" 200 -',
            '203.0.113.24 - - "GET /tbot/v1/?token=[redacted] HTTP/1.1" 101 -',
        ]
    )

    assert logger_module.find_token_leaks_in_access_log(captured_access_log) == []


def test_module_abbreviations_build_expected_module_string():
    selected = {
        "VAD": "silero_vad",
        "ASR": "",
        "LLM": "openai",
        "TTS": "edge_tts",
        "Memory": "mem",
        "Intent": "function_call",
        "VLLM": "vision",
    }

    assert logger_module.get_module_abbreviation("missing", selected) == "00"
    assert logger_module.get_module_abbreviation("VAD", selected) == "va"
    assert logger_module.get_module_abbreviation("LLM", selected) == "op"
    assert logger_module.build_module_string(selected) == "va00opttmecavi"


def test_formatter_sets_defaults_and_connection_logger_binds(monkeypatch):
    record = {"name": "module.name", "message": "hello", "extra": {}}
    assert logger_module.formatter(record) == "hello"
    assert record["extra"]["tag"] == "module.name"
    assert record["selected_module"] == "00000000000000"

    captured = {}

    class _Logger:
        def bind(self, **kwargs):
            captured.update(kwargs)
            return "bound"

    monkeypatch.setattr(logger_module, "logger", _Logger())
    assert logger_module.create_connection_logger("module-code") == "bound"
    assert captured == {"selected_module": "module-code"}


def test_safe_file_sink_retention_rotation_and_disk_full(tmp_path, monkeypatch):
    log_path = tmp_path / "server.log"
    old = tmp_path / "server.log.old"
    old.write_text("old", encoding="utf-8")
    stale = datetime.now() - timedelta(days=40)
    fresh = datetime.now()
    old_ts = stale.timestamp()
    fresh_path = tmp_path / "other.log"
    fresh_path.write_text("fresh", encoding="utf-8")

    monkeypatch.setattr(logger_module.os.path, "getmtime", lambda path: old_ts if Path(path) == old else fresh.timestamp())
    sink = logger_module._SafeFileSink(str(log_path), stderr=SimpleNamespace(write=lambda *_: None, flush=lambda: None), max_bytes=1, retention_days=30)
    log_path.write_text("existing", encoding="utf-8")

    sink("new message")
    assert not old.exists()
    assert log_path.exists()

    sink._close_file()
    no_retention = logger_module._SafeFileSink(str(log_path), retention_days=None)
    no_retention._cleanup_old_logs()

    class _DiskFullSink(logger_module._SafeFileSink):
        def _rotate_if_needed(self, message_len):
            raise OSError(errno.ENOSPC, "full")

    disk_full = _DiskFullSink(str(tmp_path / "full.log"), stderr=SimpleNamespace(write=lambda *_: None, flush=lambda: None))
    disk_full("message")
    disk_full("again")
    assert disk_full.disabled

def test_safe_file_sink_defensive_paths(tmp_path, monkeypatch):
    log_path = tmp_path / "server.log"
    old = tmp_path / "server.log.old"
    old.write_text("old", encoding="utf-8")

    sink = logger_module._SafeFileSink(str(log_path), max_bytes=0)
    sink._rotate_if_needed(100)

    missing_sink = logger_module._SafeFileSink(str(log_path), max_bytes=1)
    missing_sink._rotate_if_needed(100)

    monkeypatch.setattr(logger_module.os.path, "getmtime", lambda _path: (_ for _ in ()).throw(OSError("gone")))
    cleanup_sink = logger_module._SafeFileSink(str(log_path), retention_days=30)
    cleanup_sink._cleanup_old_logs()
    assert old.exists()

    class _PermissionSink(logger_module._SafeFileSink):
        def _rotate_if_needed(self, message_len):
            raise OSError(errno.EACCES, "denied")

    denied = _PermissionSink(str(tmp_path / "denied.log"))
    try:
        denied("message")
    except OSError as exc:
        assert exc.errno == errno.EACCES
    else:
        raise AssertionError("non-ENOSPC OSError must propagate")
