import asyncio
import base64
import hashlib
import json
from pathlib import Path

import pytest

from core.lesson import sd_pack_sync
from core.providers.tools.device_mcp import mcp_handler


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cached_asset_packs_are_built_from_verified_lesson_cache(tmp_path):
    cache_root = tmp_path / "lesson_assets"
    pack_dir = cache_root / "lesson-a" / "v3-abc123"
    pack_dir.mkdir(parents=True)
    (pack_dir / "backgroundScene.poster").write_bytes(b"large-original")
    (pack_dir / "backgroundScene.poster.render.jpg").write_bytes(b"render-safe")
    (pack_dir / "teachingObject.barn").write_bytes(b"barn")

    config = {
        "lesson": {
            "asset_cache_root": str(cache_root),
            "asset_pack_local_root": "sd://tbot/lesson-assets",
            "asset_public_base_url": "https://esp.example",
        }
    }

    packs = list(sd_pack_sync.cached_asset_packs(config))

    assert len(packs) == 1
    pack = packs[0]
    assert pack["cacheKey"] == "lesson-a/v3-abc123"
    assert pack["lessonId"] == "lesson-a"
    assert pack["lessonVersion"] == 3
    assert pack["ready"] is True
    by_key = {asset["key"]: asset for asset in pack["assets"]}
    assert set(by_key) == {"backgroundScene.poster", "teachingObject.barn"}
    token = base64.urlsafe_b64encode(b"lesson-a/v3-abc123").decode("ascii").rstrip("=")
    assert by_key["backgroundScene.poster"]["url"] == (
        f"https://esp.example/tbot/lesson-assets/{token}/backgroundScene.poster"
    )
    assert by_key["backgroundScene.poster"]["sha256"] == _sha(
        pack_dir / "backgroundScene.poster.render.jpg"
    )
    assert by_key["backgroundScene.poster"]["localPath"] == (
        "sd://tbot/lesson-assets/lesson-a/v3-abc123/backgroundScene.poster"
    )
    assert by_key["teachingObject.barn"]["sha256"] == _sha(pack_dir / "teachingObject.barn")


@pytest.mark.asyncio
async def test_sync_cached_lesson_assets_to_sd_calls_mcp_for_each_cached_pack(monkeypatch, tmp_path):
    cache_root = tmp_path / "lesson_assets"
    first = cache_root / "lesson-a" / "v1-a"
    second = cache_root / "lesson-b" / "v2-b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "backgroundScene.poster").write_bytes(b"a")
    (second / "teachingObject.barn").write_bytes(b"b")
    calls = []

    class Client:
        async def is_ready(self):
            return True

    class Conn:
        config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_cache_root": str(cache_root),
                "asset_public_base_url": "https://esp.example",
            }
        }
        mcp_client = Client()

    async def fake_call(conn, client, pack):
        calls.append((sd_pack_sync.SD_PACK_SYNC_TOOL, pack["cacheKey"]))
        return json.dumps({"ready": True, "failedCount": 0})

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["packs"] == 2
    assert result["synced"] == 2
    assert result["failed"] == 0
    assert calls == [
        ("self.lesson_assets.sync_to_sd", "lesson-a/v1-a"),
        ("self.lesson_assets.sync_to_sd", "lesson-b/v2-b"),
    ]


@pytest.mark.asyncio
async def test_sync_cached_lesson_assets_to_sd_skips_when_sd_pack_disabled(tmp_path):
    class Conn:
        config = {"lesson": {"asset_delivery_mode": "http_pull"}}
        mcp_client = None

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["skipped"] == "sd_pack_disabled"


@pytest.mark.asyncio
async def test_background_sync_pauses_while_voice_is_busy(monkeypatch, tmp_path):
    cache_root = tmp_path / "lesson_assets"
    pack_dir = cache_root / "lesson-a" / "v1-a"
    pack_dir.mkdir(parents=True)
    (pack_dir / "poster").write_bytes(b"a")
    busy = [True, False]
    sleeps = []
    calls = []

    class Client:
        async def is_ready(self):
            return True

    class Conn:
        config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_cache_root": str(cache_root),
                "asset_public_base_url": "https://esp.example",
            }
        }
        mcp_client = Client()

    async def fake_call(_conn, _client, pack):
        calls.append(pack["cacheKey"])
        return {"ready": True}

    async def fake_sleep(_delay):
        sleeps.append("paused")

    def busy_check():
        return busy.pop(0) if busy else False

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(
        Conn(), busy_check=busy_check, sleep=fake_sleep
    )

    assert result["synced"] == 1
    assert sleeps == ["paused"]
    assert calls == ["lesson-a/v1-a"]


def test_cached_asset_packs_ignore_configured_sd_pack_without_valid_ready(tmp_path):
    cache_root = tmp_path / "cache"
    cached = cache_root / "lesson-a" / "v1-a"
    cached.mkdir(parents=True)
    (cached / "poster").write_bytes(b"a")
    sd_root = tmp_path / "sd" / "lesson-assets"
    (sd_root / "lesson-a" / "v1-a").mkdir(parents=True)
    config = {
        "lesson": {
            "asset_cache_root": str(cache_root),
            "asset_pack_mount_root": str(sd_root),
            "asset_public_base_url": "https://esp.example",
        }
    }

    assert list(sd_pack_sync.cached_asset_packs(config)) == []


@pytest.mark.asyncio
async def test_mcp_ready_schedules_cached_lesson_sd_sync():
    client = mcp_handler.MCPClient()
    scheduled = []

    class Conn:
        func_handler = None

        def schedule_cached_lesson_sd_sync(self):
            scheduled.append("scheduled")

    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "self.lesson_assets.sync_to_sd",
                    "description": "sync",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ]
        },
    }

    await mcp_handler.handle_mcp_message(Conn(), client, payload)

    assert await client.is_ready()
    assert scheduled == ["scheduled"]

@pytest.mark.asyncio
async def test_mcp_ready_still_schedules_when_raw_sd_sync_tool_is_not_advertised():
    client = mcp_handler.MCPClient()
    scheduled = []

    class Conn:
        func_handler = None

        def schedule_cached_lesson_sd_sync(self):
            scheduled.append("scheduled")

    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "self.get_device_status",
                    "description": "status",
                    "inputSchema": {"type": "object", "properties": {}, "required": []},
                }
            ]
        },
    }

    await mcp_handler.handle_mcp_message(Conn(), client, payload)

    assert await client.is_ready()
    assert scheduled == ["scheduled"]

@pytest.mark.asyncio
async def test_sync_cached_lesson_assets_to_sd_stops_after_unknown_raw_tool(monkeypatch, tmp_path):
    cache_root = tmp_path / "lesson_assets"
    first = cache_root / "lesson-a" / "v1-a"
    second = cache_root / "lesson-b" / "v2-b"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "backgroundScene.poster").write_bytes(b"a")
    (second / "teachingObject.barn").write_bytes(b"b")
    calls = []

    class Client:
        async def is_ready(self):
            return True

    class Conn:
        config = {
            "lesson": {
                "asset_delivery_mode": "sd_pack",
                "asset_cache_root": str(cache_root),
                "asset_public_base_url": "https://esp.example",
            }
        }
        mcp_client = Client()

    async def fake_call(conn, client, pack):
        calls.append(pack["cacheKey"])
        raise Exception("MCP error: Unknown tool: self.lesson_assets.sync_to_sd")

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["packs"] == 2
    assert result["synced"] == 0
    assert result["failed"] == 1
    assert result["unsupported"] is True
    assert calls == ["lesson-a/v1-a"]
