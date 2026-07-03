import asyncio
import struct

import pytest

from core.utils import context_provider, gc_manager, p3


class _BoundLogger:
    def __init__(self):
        self.records = []

    def bind(self, **kwargs):
        self.records.append(("bind", kwargs))
        return self

    def debug(self, message):
        self.records.append(("debug", message))

    def info(self, message):
        self.records.append(("info", message))

    def warning(self, message):
        self.records.append(("warning", message))

    def error(self, message):
        self.records.append(("error", message))


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _p3_packet(payload: bytes) -> bytes:
    return struct.pack(">BBH", 1, 0, len(payload)) + payload


def test_context_provider_uses_default_logger_and_returns_empty_without_providers(monkeypatch):
    logger = _BoundLogger()
    monkeypatch.setattr(context_provider, "setup_logging", lambda: logger)

    provider = context_provider.ContextDataProvider({})

    assert provider.fetch_all("device-1") == ""
    assert provider.logger is logger


def test_context_provider_formats_successes_and_logs_failure_edges(monkeypatch):
    logger = _BoundLogger()
    calls = []
    responses = {
        "https://dict.test": _Response(200, {"code": 0, "data": {"battery": "full"}}),
        "https://list.test": _Response(200, {"code": 0, "data": ["lesson", "music"]}),
        "https://scalar.test": _Response(200, {"code": 0, "data": "ready"}),
        "https://code.test": _Response(200, {"code": 7, "msg": "bad"}),
        "https://nondict.test": _Response(200, ["bad"]),
        "https://status.test": _Response(503, {}),
    }

    def fake_get(url, headers, timeout):
        calls.append((url, dict(headers), timeout))
        if url == "https://boom.test":
            raise RuntimeError("network down")
        return responses[url]

    monkeypatch.setattr(context_provider.httpx, "get", fake_get)
    provider = context_provider.ContextDataProvider(
        {
            "context_providers": [
                {"headers": {"ignored": "missing-url"}},
                {"url": "https://dict.test", "headers": {"auth": "token"}},
                {"url": "https://list.test", "headers": "bad"},
                {"url": "https://scalar.test"},
                {"url": "https://code.test"},
                {"url": "https://nondict.test"},
                {"url": "https://status.test"},
                {"url": "https://boom.test"},
            ]
        },
        logger=logger,
    )

    assert provider.fetch_all("robot-7") == "\n".join(
        ["- **battery:** full", "- lesson", "- music", "- ready"]
    )
    assert calls[0] == ("https://dict.test", {"auth": "token", "device-id": "robot-7"}, 3)
    assert calls[1] == ("https://list.test", {"device-id": "robot-7"}, 3)
    assert any(level == "warning" and "ReturnErrorcode" in msg for level, msg in logger.records)
    assert any(level == "warning" and "notJSONDictionary" in msg for level, msg in logger.records)
    assert any(level == "warning" and "Request failed: 503" in msg for level, msg in logger.records)
    assert any(level == "error" and "network down" in msg for level, msg in logger.records)
    assert any(level == "debug" and "Dynamic context data" in msg for level, msg in logger.records)


def test_p3_decodes_packets_from_bytes_and_files_and_rejects_truncated_payloads(tmp_path):
    payload = _p3_packet(b"one") + _p3_packet(b"two")
    path = tmp_path / "audio.p3"
    path.write_bytes(payload)

    assert p3.decode_opus_from_bytes(payload) == ([b"one", b"two"], 0.12)
    assert p3.decode_opus_from_file(path) == ([b"one", b"two"], 0.12)

    truncated = struct.pack(">BBH", 1, 0, 4) + b"no"
    with pytest.raises(ValueError, match="mismatch"):
        p3.decode_opus_from_bytes(truncated)

    bad_path = tmp_path / "bad.p3"
    bad_path.write_bytes(truncated)
    with pytest.raises(ValueError, match="mismatch"):
        p3.decode_opus_from_file(bad_path)


@pytest.mark.asyncio
async def test_gc_manager_start_stop_cancel_and_singleton_paths(monkeypatch):
    logger = _BoundLogger()
    monkeypatch.setattr(gc_manager, "logger", logger)
    monkeypatch.setattr(gc_manager, "_gc_manager_instance", None)

    manager = gc_manager.GlobalGCManager(interval_seconds=60)
    await manager.stop()
    await manager.start()
    await manager.start()
    await manager.stop()

    task_manager = gc_manager.GlobalGCManager(interval_seconds=60)
    task = asyncio.create_task(task_manager._gc_loop())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    first = gc_manager.get_gc_manager(interval_seconds=1)
    second = gc_manager.get_gc_manager(interval_seconds=99)
    assert first is second
    assert any(level == "warning" and "already running" in msg for level, msg in logger.records)
    assert any(level == "info" and "canceled" in msg for level, msg in logger.records)


@pytest.mark.asyncio
async def test_gc_manager_loop_timeout_success_stop_and_error_paths(monkeypatch):
    logger = _BoundLogger()
    monkeypatch.setattr(gc_manager, "logger", logger)
    monkeypatch.setattr(gc_manager.gc, "get_objects", lambda: [object(), object()])
    monkeypatch.setattr(gc_manager.gc, "collect", lambda: 3)

    manager = gc_manager.GlobalGCManager(interval_seconds=0.001)
    await manager._run_gc()
    assert any(level == "debug" and "objects collected: 3" in msg for level, msg in logger.records)

    stop_manager = gc_manager.GlobalGCManager(interval_seconds=1)
    stop_manager._stop_event.set()
    await stop_manager._gc_loop()

    pending_stop_manager = gc_manager.GlobalGCManager(interval_seconds=60)
    pending_stop_task = asyncio.create_task(pending_stop_manager._gc_loop())
    await asyncio.sleep(0)
    pending_stop_manager._stop_event.set()
    await pending_stop_task

    error_manager = gc_manager.GlobalGCManager(interval_seconds=0.001)

    async def raise_gc():
        raise RuntimeError("gc failed")

    monkeypatch.setattr(error_manager, "_run_gc", raise_gc)
    await error_manager._gc_loop()
    assert any(level == "error" and "gc failed" in msg for level, msg in logger.records)

    monkeypatch.setattr(gc_manager.asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError("no loop")))
    await manager._run_gc()
    assert any(level == "error" and "no loop" in msg for level, msg in logger.records)
