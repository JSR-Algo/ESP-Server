import asyncio

import pytest

from core.lesson import sd_pack_fanout, sd_pack_sync
from core.lesson.sd_pack_pending_store import InMemoryLessonSdPendingStore


@pytest.fixture(autouse=True)
def _clear_pending(monkeypatch):
    sd_pack_fanout.set_pending_store_for_tests(
        InMemoryLessonSdPendingStore(random=lambda: 0.0)
    )

    async def callback_ok(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sd_pack_fanout, "_post_sync_results", callback_ok)
    yield
    sd_pack_fanout.clear_pending_for_tests()


def _write_pack(root, cache_key, name="backgroundScene.poster", body=b"asset"):
    pack_dir = root / cache_key
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / name).write_bytes(body)
    return pack_dir


@pytest.mark.asyncio
async def test_fanout_syncs_online_devices(monkeypatch, tmp_path):
    _write_pack(tmp_path, "lesson-a/v1-aaa")
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    calls = []

    class Conn:
        def __init__(self, device_id):
            self.device_id = device_id
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    async def fake_call(conn, mcp_client, pack):
        calls.append((conn.device_id, pack["cacheKey"]))
        return {"ready": True, "failedCount": 0}

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    connections = {"aa:bb": Conn("aa:bb"), "cc:dd": Conn("cc:dd")}
    result = await sd_pack_fanout.fanout_sd_pack_sync(config, connections)

    assert result["packs"] == 1
    assert {c[0] for c in calls} == {"aa:bb", "cc:dd"}
    assert len(result["synced"]) == 2
    assert result["queued"] == []


@pytest.mark.asyncio
async def test_fanout_queues_offline_selected_devices(monkeypatch, tmp_path):
    _write_pack(tmp_path, "lesson-a/v1-aaa")
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    calls = []

    class Conn:
        def __init__(self, device_id):
            self.device_id = device_id
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    async def fake_call(conn, mcp_client, pack):
        calls.append(conn.device_id)
        return {"ready": True, "failedCount": 0}

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    connections = {"online-1": Conn("online-1")}
    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        connections,
        device_ids=["online-1", "offline-2"],
        lesson_id="lesson-a",
    )

    assert calls == ["online-1"]
    assert len(result["synced"]) == 1
    assert result["queued"] == [
        {
            "deviceId": "offline-2",
            "cacheKeys": ["lesson-a/v1-aaa"],
            "reason": "offline",
        }
    ]
    assert "offline-2" in result["pending"]


@pytest.mark.asyncio
async def test_fanout_queues_failed_online_for_reconnect_retry(monkeypatch, tmp_path):
    _write_pack(tmp_path, "lesson-a/v1-aaa")
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }

    class Conn:
        def __init__(self, device_id):
            self.device_id = device_id
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    async def fake_call(conn, mcp_client, pack):
        return {"ready": False, "failedCount": 1, "synced": 0, "failed": 1, "packs": 1}

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        {"dev-bad": Conn("dev-bad")},
        lesson_id="lesson-a",
        queue_offline=True,
    )

    assert len(result["failed"]) == 1
    assert result["queued"] == [
        {
            "deviceId": "dev-bad",
            "cacheKeys": ["lesson-a/v1-aaa"],
            "reason": "retry-after-fail",
        }
    ]
    assert "dev-bad" in result["pending"]


@pytest.mark.asyncio
async def test_drain_pending_on_reconnect(monkeypatch, tmp_path):
    _write_pack(tmp_path, "lesson-a/v1-aaa")
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    await sd_pack_fanout.mark_pending("dev-1", {"lesson-a/v1-aaa"})

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    calls = []

    async def fake_call(conn, mcp_client, pack):
        calls.append(pack["cacheKey"])
        return {"ready": True, "failedCount": 0}

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_fanout.drain_pending_for_connection(Conn())
    assert calls == ["lesson-a/v1-aaa"]
    assert result["synced"] == 1
    assert await sd_pack_fanout.pop_pending("dev-1") is None


@pytest.mark.asyncio
async def test_drain_pending_callback_failure_remains_pending_and_replays(monkeypatch, tmp_path):
    _write_pack(tmp_path, "lesson-a/v1-aaa")
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    await sd_pack_fanout.mark_pending("dev-1", {"lesson-a/v1-aaa"})

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    callback_calls = []

    async def fake_call(conn, mcp_client, pack):
        return {"ready": True, "failedCount": 0}

    async def callback_fail_once(*_args, **kwargs):
        callback_calls.append(kwargs["cache_keys"])
        if len(callback_calls) == 1:
            raise RuntimeError("backend down")

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_sync_results", callback_fail_once)

    with pytest.raises(RuntimeError):
        await sd_pack_fanout.drain_pending_for_connection(Conn())
    assert (await sd_pack_fanout.pending_snapshot())["dev-1"]["cacheKeys"] == [
        "lesson-a/v1-aaa"
    ]

    await sd_pack_fanout.drain_pending_for_connection(Conn())
    assert callback_calls == [["lesson-a/v1-aaa"], ["lesson-a/v1-aaa"]]
    assert await sd_pack_fanout.pop_pending("dev-1") is None


@pytest.mark.asyncio
async def test_drain_pending_resolves_mac_connection_to_backend_uuid(monkeypatch, tmp_path):
    _write_pack(tmp_path, "lesson-a/v1-aaa")
    config = {
        "server": {"api_url": "http://backend.test/v1"},
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        },
    }
    await sd_pack_fanout.mark_pending("backend-uuid-1", {"lesson-a/v1-aaa"})

    class Conn:
        def __init__(self):
            self.device_id = "AA:BB:CC"
            self.config = config
            self.logger = object()
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    resolve_calls = []
    callback_calls = []

    async def resolve(_client, base_url, mac, *, logger=None):
        resolve_calls.append((base_url, mac, logger))
        return "backend-uuid-1", "device-token"

    async def fake_call(conn, mcp_client, pack):
        return {"ready": True, "failedCount": 0}

    async def callback_ok(*_args, **kwargs):
        callback_calls.append((kwargs["backend_device_id"], kwargs["cache_keys"]))

    monkeypatch.setattr("config.device_token_client.resolve_device_identity", resolve)
    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_sync_results", callback_ok)

    await sd_pack_fanout.drain_pending_for_connection(Conn())

    assert resolve_calls == [("http://backend.test/v1", "AA:BB:CC", None)]
    assert callback_calls == [("backend-uuid-1", ["lesson-a/v1-aaa"])]
    assert await sd_pack_fanout.pop_pending("backend-uuid-1") is None


@pytest.mark.asyncio
async def test_fanout_no_packs_returns_empty(tmp_path):
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    result = await sd_pack_fanout.fanout_sd_pack_sync(config, {"a": object()})
    assert result["packs"] == 0
    assert result["skipped"]


def test_http_routes_registered():
    from core.http_server import SimpleHttpServer

    with open(SimpleHttpServer.__init__.__code__.co_filename) as fh:
        src = fh.read()
    assert "/internal/lesson-assets/sd-fanout" in src
