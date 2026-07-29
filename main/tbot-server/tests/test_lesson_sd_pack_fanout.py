import asyncio
import re

import pytest

from core.lesson import sd_pack_fanout, sd_pack_sync
from core.lesson.sd_pack_pending_store import InMemoryLessonSdPendingStore

_BACKEND_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
CHECKSUM_A = "a" * 64
CHECKSUM_B = "b" * 64
CACHE_KEY_A = f"lesson-a/v1-{CHECKSUM_A}"
CACHE_KEY_B = f"lesson-b/v1-{CHECKSUM_B}"

@pytest.fixture(autouse=True)
def _clear_pending(monkeypatch):
    sd_pack_fanout.set_pending_store_for_tests(
        InMemoryLessonSdPendingStore(random=lambda: 0.0)
    )

    async def callback_ok(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", callback_ok)
    yield
    sd_pack_fanout.clear_pending_for_tests()


def _write_pack(root, cache_key, name="backgroundScene.poster", body=b"asset"):
    pack_dir = root / cache_key
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / name).write_bytes(body)
    return pack_dir


def _successful_attestation(pack, *, downloaded_count=1, skipped_count=0):
    return {
        "ready": True,
        "cacheKey": pack["cacheKey"],
        "manifestChecksum": pack["manifestChecksum"],
        "downloadedCount": downloaded_count,
        "skippedCount": skipped_count,
        "failedCount": 0,
        "criticalFailedCount": 0,
    }


@pytest.mark.asyncio
async def test_fanout_syncs_online_devices(monkeypatch, tmp_path):
    _write_pack(tmp_path, CACHE_KEY_A)
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
        return _successful_attestation(pack)

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    connections = {"aa:bb": Conn("aa:bb"), "cc:dd": Conn("cc:dd")}
    result = await sd_pack_fanout.fanout_sd_pack_sync(config, connections)

    assert result["packs"] == 1
    assert {c[0] for c in calls} == {"aa:bb", "cc:dd"}
    assert len(result["synced"]) == 2
    assert result["queued"] == []


@pytest.mark.asyncio
async def test_fanout_queues_offline_selected_devices(monkeypatch, tmp_path):
    _write_pack(tmp_path, CACHE_KEY_A)
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
        return _successful_attestation(pack)

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
            "cacheKeys": [CACHE_KEY_A],
            "reason": "offline",
        }
    ]
    assert "offline-2" in result["pending"]


@pytest.mark.asyncio
async def test_fanout_queues_failed_online_for_reconnect_retry(monkeypatch, tmp_path):
    _write_pack(tmp_path, CACHE_KEY_A)
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
            "cacheKeys": [CACHE_KEY_A],
            "reason": "retry-after-fail",
        }
    ]
    assert "dev-bad" in result["pending"]


@pytest.mark.asyncio
async def test_fanout_returns_one_contract_device_row_per_requested_device(
    monkeypatch, tmp_path
):
    _write_pack(tmp_path, CACHE_KEY_A)
    _write_pack(tmp_path, CACHE_KEY_B)
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

    async def fake_sync(conn, *_args, **_kwargs):
        if conn.device_id == "dev-ok":
            return {
                "packs": 2,
                "synced": 2,
                "failed": 0,
                "resultsByCacheKey": {
                    CACHE_KEY_A: {
                        "ready": True,
                        "downloadedCount": 2,
                        "skippedCount": 0,
                        "failedCount": 1,
                        "criticalFailedCount": 0,
                        "errorCode": " --OPTIONAL.THUMBNAIL FAILED!!! ",
                    },
                    CACHE_KEY_B: {
                        "ready": True,
                        "downloadedCount": 1,
                        "skippedCount": 1,
                        "failedCount": 0,
                        "criticalFailedCount": 0,
                    },
                },
            }
        return {
            "packs": 2,
            "synced": 1,
            "failed": 1,
            "resultsByCacheKey": {
                CACHE_KEY_A: {
                    "ready": False,
                    "downloadedCount": 0,
                    "skippedCount": 0,
                    "failedCount": 2,
                    "criticalFailedCount": 1,
                    "errorCode": " --CRITICAL/ASSET FAILED!!! ",
                },
                CACHE_KEY_B: {
                    "ready": True,
                    "downloadedCount": 1,
                    "skippedCount": 0,
                    "failedCount": 0,
                    "criticalFailedCount": 0,
                },
            },
        }

    monkeypatch.setattr(sd_pack_fanout, "sync_cached_lesson_assets_to_sd", fake_sync)

    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        {"dev-ok": Conn("dev-ok"), "dev-bad": Conn("dev-bad")},
        device_ids=["dev-ok", "dev-offline", "dev-bad", "dev-ok"],
        lesson_id="lesson",
    )

    assert [device["deviceId"] for device in result["devices"]] == [
        "dev-ok",
        "dev-offline",
        "dev-bad",
    ]
    assert len({device["deviceId"] for device in result["devices"]}) == 3
    by_device = {device["deviceId"]: device for device in result["devices"]}
    assert by_device["dev-ok"] == {
        "deviceId": "dev-ok",
        "state": "COMPLETE",
        "downloadedCount": 3,
        "skippedCount": 1,
        "reusedCount": 0,
        "failedCount": 1,
        "criticalFailedCount": 0,
        "errorCode": "optional_thumbnail_failed_",
        "retryable": False,
    }
    assert by_device["dev-offline"] == {
        "deviceId": "dev-offline",
        "state": "PENDING_OFFLINE",
        "downloadedCount": 0,
        "skippedCount": 0,
        "reusedCount": 0,
        "failedCount": 0,
        "criticalFailedCount": 0,
        "retryable": True,
    }
    assert by_device["dev-bad"] == {
        "deviceId": "dev-bad",
        "state": "RETRY_WAIT",
        "downloadedCount": 1,
        "skippedCount": 0,
        "reusedCount": 0,
        "failedCount": 2,
        "criticalFailedCount": 1,
        "errorCode": "critical_asset_failed_",
        "retryable": True,
    }
    for device in result["devices"]:
        if "errorCode" in device:
            assert _BACKEND_ERROR_CODE_RE.fullmatch(device["errorCode"])


@pytest.mark.asyncio
async def test_fanout_preserves_and_aggregates_reused_counts(monkeypatch, tmp_path):
    _write_pack(tmp_path, CACHE_KEY_A)
    _write_pack(tmp_path, CACHE_KEY_B)
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = config

    async def fake_sync(_conn, *_args, **_kwargs):
        return {
            "packs": 2,
            "synced": 2,
            "failed": 0,
            "resultsByCacheKey": {
                CACHE_KEY_A: {
                    "ready": True,
                    "downloadedCount": 0,
                    "skippedCount": 0,
                    "reusedCount": 2,
                    "failedCount": 0,
                    "criticalFailedCount": 0,
                },
                CACHE_KEY_B: {
                    "ready": True,
                    "downloadedCount": 1,
                    "skippedCount": 0,
                    "failedCount": 0,
                    "criticalFailedCount": 0,
                },
            },
        }

    callbacks = []

    async def capture_callback(*_args, **kwargs):
        callbacks.append(kwargs["result"])

    monkeypatch.setattr(sd_pack_fanout, "sync_cached_lesson_assets_to_sd", fake_sync)
    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", capture_callback)

    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        {"dev-1": Conn()},
        device_ids=["dev-1"],
        lesson_id="lesson",
    )

    callbacks_by_key = {item["cacheKey"]: item for item in callbacks}
    assert callbacks_by_key[CACHE_KEY_A]["reusedCount"] == 2
    assert callbacks_by_key[CACHE_KEY_B]["reusedCount"] == 0
    assert result["devices"][0]["reusedCount"] == 2


@pytest.mark.asyncio
async def test_fanout_callback_retained_key_returns_retry_wait_device_row(
    monkeypatch, tmp_path
):
    _write_pack(tmp_path, CACHE_KEY_A)
    _write_pack(tmp_path, CACHE_KEY_B)
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    async def fake_call(_conn, _mcp_client, pack):
        return _successful_attestation(pack)

    async def post_one(*_args, **kwargs):
        if kwargs["result"]["cacheKey"] == CACHE_KEY_B:
            raise RuntimeError("backend down")

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", post_one)

    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        {"dev-1": Conn()},
        device_ids=["dev-1"],
        lesson_id="lesson",
    )

    assert result["devices"] == [
        {
            "deviceId": "dev-1",
            "state": "RETRY_WAIT",
            "downloadedCount": 2,
            "skippedCount": 0,
            "reusedCount": 0,
            "failedCount": 0,
            "criticalFailedCount": 0,
            "errorCode": "callback_error",
            "retryable": True,
        }
    ]
    assert _BACKEND_ERROR_CODE_RE.fullmatch(result["devices"][0]["errorCode"])
    assert result["queued"] == [
        {
            "deviceId": "dev-1",
            "cacheKeys": [CACHE_KEY_B],
            "reason": "retry-after-fail",
        }
    ]

@pytest.mark.asyncio
async def test_fanout_device_row_error_code_is_backend_safe_and_capped(
    monkeypatch, tmp_path
):
    _write_pack(tmp_path, CACHE_KEY_A)
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }

    class Conn:
        device_id = "dev-1"

    async def fake_sync(_conn, *_args, **_kwargs):
        return {
            "packs": 1,
            "synced": 0,
            "failed": 1,
            "resultsByCacheKey": {
                CACHE_KEY_A: {
                    "ready": False,
                    "failedCount": 1,
                    "criticalFailedCount": 1,
                    "errorCode": "___..." + ("A" * 80),
                }
            },
        }

    monkeypatch.setattr(sd_pack_fanout, "sync_cached_lesson_assets_to_sd", fake_sync)

    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        {"dev-1": Conn()},
        device_ids=["dev-1"],
        lesson_id="lesson-a",
    )

    assert result["devices"][0]["errorCode"] == "a" * 64
    assert _BACKEND_ERROR_CODE_RE.fullmatch(result["devices"][0]["errorCode"])


@pytest.mark.asyncio
async def test_drain_pending_on_reconnect(monkeypatch, tmp_path):
    _write_pack(tmp_path, CACHE_KEY_A)
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    await sd_pack_fanout.mark_pending("dev-1", {CACHE_KEY_A})

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
        return _successful_attestation(pack)

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_fanout.drain_pending_for_connection(Conn())
    assert calls == [CACHE_KEY_A]
    assert result["synced"] == 1
    assert await sd_pack_fanout.pop_pending("dev-1") is None


@pytest.mark.asyncio
async def test_drain_pending_callback_failure_remains_pending_and_replays(monkeypatch, tmp_path):
    _write_pack(tmp_path, CACHE_KEY_A)
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    await sd_pack_fanout.mark_pending("dev-1", {CACHE_KEY_A})

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
        return _successful_attestation(pack)

    async def callback_fail_once(*_args, **kwargs):
        callback_calls.append([kwargs["result"]["cacheKey"]])
        if len(callback_calls) == 1:
            raise RuntimeError("backend down")

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", callback_fail_once)

    first = await sd_pack_fanout.drain_pending_for_connection(Conn())
    assert first["callbackErrors"][0]["type"] == "RuntimeError"
    assert (await sd_pack_fanout.pending_snapshot())["dev-1"]["cacheKeys"] == [
        CACHE_KEY_A
    ]

    await sd_pack_fanout.drain_pending_for_connection(Conn())
    assert callback_calls == [[CACHE_KEY_A], [CACHE_KEY_A]]
    assert await sd_pack_fanout.pop_pending("dev-1") is None


@pytest.mark.asyncio
async def test_drain_pending_clears_only_ready_callback_success_and_retains_failed_key(
    monkeypatch, tmp_path
):
    _write_pack(tmp_path, CACHE_KEY_A)
    for index in range(3):
        _write_pack(tmp_path, CACHE_KEY_A, name=f"optional-{index}.asset")
    _write_pack(tmp_path, CACHE_KEY_B)
    _write_pack(tmp_path, CACHE_KEY_B, name="second.asset")
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    await sd_pack_fanout.mark_pending("dev-1", {CACHE_KEY_A, CACHE_KEY_B})

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    async def fake_call(_conn, _mcp_client, pack):
        if pack["cacheKey"] == CACHE_KEY_A:
            return _successful_attestation(
                pack, downloaded_count=3, skipped_count=1
            )
        return {
            "ready": False,
            "downloadedCount": 0,
            "skippedCount": 0,
            "failedCount": 2,
            "criticalFailedCount": 1,
            "errorCode": "firmware failed",
        }

    callbacks = []

    async def post_one(*_args, **kwargs):
        callbacks.append(kwargs["result"])

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", post_one)

    await sd_pack_fanout.drain_pending_for_connection(Conn())

    assert [item["cacheKey"] for item in callbacks] == [CACHE_KEY_A, CACHE_KEY_B]
    assert callbacks[0]["downloadedCount"] == 3
    assert callbacks[0]["criticalFailedCount"] == 0
    assert callbacks[1]["failedCount"] == 2
    assert callbacks[1]["criticalFailedCount"] == 1
    assert callbacks[1]["errorCode"] == "firmware_failed"
    assert _BACKEND_ERROR_CODE_RE.fullmatch(callbacks[1]["errorCode"])
    assert (await sd_pack_fanout.pending_snapshot())["dev-1"]["cacheKeys"] == [
        CACHE_KEY_B
    ]

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_lesson", "mcp_client", "expected_reason"),
    [
        ({}, object(), "sd_pack_disabled"),
        ({"asset_delivery_mode": "sd_pack"}, None, "no_mcp_client"),
        (
            {"asset_delivery_mode": "sd_pack"},
            type("M", (), {"is_ready": lambda self: asyncio.sleep(0, result=False)})(),
            "mcp_not_ready",
        ),
    ],
)
async def test_drain_pending_skipped_sync_retains_without_callback_and_one_attempt(
    monkeypatch,
    config_lesson,
    mcp_client,
    expected_reason,
):
    store = InMemoryLessonSdPendingStore(random=lambda: 0.0)
    sd_pack_fanout.set_pending_store_for_tests(store)
    await store.mark("dev-1", {CACHE_KEY_A})
    callback_calls = []

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = {"lesson": config_lesson}
            self.mcp_client = mcp_client

    async def post_one(*_args, **kwargs):
        callback_calls.append(kwargs["result"])

    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", post_one)

    result = await sd_pack_fanout.drain_pending_for_connection(Conn())

    assert result["skipped"] == expected_reason
    assert result["retainedCacheKeys"] == [CACHE_KEY_A]
    assert result["errorCode"] == expected_reason
    assert _BACKEND_ERROR_CODE_RE.fullmatch(result["errorCode"])
    assert callback_calls == []
    pending = await store.load("dev-1")
    assert pending["cacheKeys"] == [CACHE_KEY_A]
    assert pending["attemptCount"] == 2


@pytest.mark.asyncio
async def test_drain_pending_partial_callback_failure_retains_only_failed_callback_key(
    monkeypatch, tmp_path
):
    _write_pack(tmp_path, CACHE_KEY_A)
    _write_pack(tmp_path, CACHE_KEY_B)
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }
    await sd_pack_fanout.mark_pending("dev-1", {CACHE_KEY_A, CACHE_KEY_B})

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    async def fake_call(_conn, _mcp_client, pack):
        return _successful_attestation(pack)

    async def post_one(*_args, **kwargs):
        if kwargs["result"]["cacheKey"] == CACHE_KEY_B:
            raise RuntimeError("backend down")

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", post_one)

    result = await sd_pack_fanout.drain_pending_for_connection(Conn())
    assert result["callbackErrors"][0]["type"] == "RuntimeError"

    pending = (await sd_pack_fanout.pending_snapshot())["dev-1"]
    assert pending["cacheKeys"] == [CACHE_KEY_B]
    assert pending["attemptCount"] == 2

@pytest.mark.asyncio
async def test_immediate_fanout_partial_callback_failure_does_not_readd_cleared_key(
    monkeypatch, tmp_path
):
    _write_pack(tmp_path, CACHE_KEY_A)
    _write_pack(tmp_path, CACHE_KEY_B)
    config = {
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        }
    }

    class Conn:
        def __init__(self):
            self.device_id = "dev-1"
            self.config = config
            self.mcp_client = type(
                "M",
                (),
                {"is_ready": lambda self: asyncio.sleep(0, result=True)},
            )()

    async def fake_call(_conn, _mcp_client, pack):
        return _successful_attestation(pack)

    async def post_one(*_args, **kwargs):
        if kwargs["result"]["cacheKey"] == CACHE_KEY_B:
            raise RuntimeError("backend down")

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", post_one)

    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        {"dev-1": Conn()},
        lesson_id="lesson",
    )

    assert result["queued"] == [
        {
            "deviceId": "dev-1",
            "cacheKeys": [CACHE_KEY_B],
            "reason": "retry-after-fail",
        }
    ]
    assert (await sd_pack_fanout.pending_snapshot())["dev-1"]["cacheKeys"] == [
        CACHE_KEY_B
    ]
    assert (await sd_pack_fanout.pending_snapshot())["dev-1"]["attemptCount"] == 1


@pytest.mark.asyncio
async def test_drain_pending_exception_remarks_with_future_backoff(monkeypatch):
    clock = type("Clock", (), {"epoch": 1_700_000_000, "__call__": lambda self: self.epoch})()
    store = InMemoryLessonSdPendingStore(clock=clock, random=lambda: 0.0)
    sd_pack_fanout.set_pending_store_for_tests(store)
    await store.mark("dev-1", {"lesson-a/v1"})
    clock.epoch += 2

    class Conn:
        device_id = "dev-1"
        config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        mcp_client = object()

    async def fail_sync(*_args, **_kwargs):
        raise RuntimeError("transport down")

    monkeypatch.setattr(sd_pack_fanout, "sync_cached_lesson_assets_to_sd", fail_sync)

    with pytest.raises(RuntimeError):
        await sd_pack_fanout.drain_pending_for_connection(Conn())

    pending = await store.load("dev-1")
    assert pending["attemptCount"] == 2
    assert pending["nextAttemptAt"] == "2023-11-14T22:13:24Z"


@pytest.mark.asyncio
async def test_fanout_identity_resolution_failure_is_offline_not_raw_online(
    monkeypatch, tmp_path
):
    _write_pack(tmp_path, CACHE_KEY_A)
    config = {
        "server": {"api_url": "http://backend.test/v1"},
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        },
    }

    class Conn:
        def __init__(self):
            self.device_id = "AA:BB:CC"
            self.config = config

    async def resolver(*_args, **_kwargs):
        return None, None

    async def unexpected_sync(*_args, **_kwargs):
        raise AssertionError("raw MAC connection must not be treated as online")

    monkeypatch.setattr("core.lesson.sd_pack_retry_worker.resolve_device_identity", resolver)
    monkeypatch.setattr(sd_pack_fanout, "sync_cached_lesson_assets_to_sd", unexpected_sync)

    result = await sd_pack_fanout.fanout_sd_pack_sync(
        config,
        {"AA:BB:CC": Conn()},
        device_ids=["backend-uuid-1"],
        lesson_id="lesson-a",
    )

    assert result["synced"] == []
    assert result["queued"] == [
        {
            "deviceId": "backend-uuid-1",
            "cacheKeys": [CACHE_KEY_A],
            "reason": "offline",
        }
    ]


@pytest.mark.asyncio
async def test_drain_pending_resolves_mac_connection_to_backend_uuid(monkeypatch, tmp_path):
    _write_pack(tmp_path, CACHE_KEY_A)
    config = {
        "server": {"api_url": "http://backend.test/v1"},
        "lesson": {
            "asset_delivery_mode": "sd_pack",
            "asset_cache_root": str(tmp_path),
            "asset_public_base_url": "https://esp.example",
        },
    }
    await sd_pack_fanout.mark_pending("backend-uuid-1", {CACHE_KEY_A})

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
        return _successful_attestation(pack)

    async def callback_ok(*_args, **kwargs):
        result = kwargs["result"]
        callback_calls.append((result["deviceId"], [result["cacheKey"]]))

    monkeypatch.setattr("config.device_token_client.resolve_device_identity", resolve)
    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    monkeypatch.setattr(sd_pack_fanout, "_post_one_sync_result", callback_ok)

    await sd_pack_fanout.drain_pending_for_connection(Conn())

    assert resolve_calls == [("http://backend.test/v1", "AA:BB:CC", None)]
    assert callback_calls == [("backend-uuid-1", [CACHE_KEY_A])]
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
