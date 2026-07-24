import asyncio
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from core.lesson.global_generation_sync import GlobalGenerationSync
from core.lesson.sd_pack_materializer import MaterializationError, materialize_lesson_sd_pack

CHECKSUM = "a" * 64


def _pack(number=1, *, classification="curriculum"):
    lesson_id = f"lesson-{number}"
    cache_key = f"{lesson_id}/v1-{CHECKSUM}"
    return {
        "lessonId": lesson_id,
        "lessonVersion": 1,
        "profile": "espTft",
        "manifestChecksum": CHECKSUM,
        "cacheKey": cache_key,
        "classification": classification,
        "assets": [
            {
                "key": "poster",
                "sha256": CHECKSUM,
                "size": 1,
                "mediaType": "image/png",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.png",
                "sdPath": (f"/sdcard/tbot/lesson-assets/{cache_key}/{quote('poster', safe='')}"),
            }
        ],
    }


def _payload(pack_count=2):
    packs = [_pack(index + 1) for index in range(pack_count)]
    return {
        "generation": 7,
        "publishedAt": "2026-07-24T00:00:00Z",
        "indexChecksum": "b" * 64,
        "curriculumLessonCount": pack_count,
        "packCount": pack_count,
        "index": packs,
    }


def _ready(pack):
    return {
        "cacheKey": pack["cacheKey"],
        "ready": True,
        "criticalReady": True,
        "optionalFailedCount": 0,
    }


class _Store:
    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.accepted_generation = 3
        self.retry_attempt = 0
        self.retries = []
        self.fail_accept = False
        self.fail_retry = False

    async def snapshot(self):
        return {
            "acceptedGeneration": self.accepted_generation,
            "retryAttempt": self.retry_attempt,
        }

    async def mark_materializing(self, generation):
        self.events.append(("materializing", generation))

    async def accept(self, generation, index_checksum, accepted_at):
        self.events.append(("accept", generation, index_checksum, accepted_at))
        if self.fail_accept:
            raise RuntimeError("redis://token@private-host")
        self.accepted_generation = generation

    async def mark_retry(self, error_code, attempt, next_retry_at):
        if self.fail_retry:
            raise RuntimeError("redis secret")
        self.retries.append((error_code, attempt, next_retry_at))
        self.retry_attempt = attempt


@pytest.fixture(autouse=True)
def _clean_concurrency_env(monkeypatch):
    monkeypatch.delenv("LESSON_GENERATION_MATERIALIZE_CONCURRENCY", raising=False)


@pytest.mark.asyncio
async def test_materializes_every_pack_before_accept_then_fanout_without_classification():
    payload = _payload()
    original = deepcopy(payload)
    events = []
    store = _Store(events)

    async def materialize(pack, *, config):
        assert config == {"lesson": {}}
        assert "classification" not in pack
        events.append(("materialize", pack["lessonId"]))
        await asyncio.sleep(0)
        events.append(("complete", pack["lessonId"]))
        return _ready(pack)

    async def fanout(generation, index_checksum, packs):
        events.append(("fanout", generation, index_checksum))
        assert all("classification" not in pack for pack in packs)
        return {"syncedCount": 2, "deviceIds": ["private-device"]}

    result = await GlobalGenerationSync({"lesson": {}}, store, fanout, materialize=materialize).apply(payload)

    assert result == {
        "state": "ready",
        "generation": 7,
        "indexChecksum": "b" * 64,
        "packCount": 2,
        "fanout": {"syncedCount": 2},
    }
    assert payload == original
    assert max(i for i, event in enumerate(events) if event[0] == "complete") < next(
        i for i, event in enumerate(events) if event[0] == "accept"
    )
    assert next(i for i, event in enumerate(events) if event[0] == "accept") < next(
        i for i, event in enumerate(events) if event[0] == "fanout"
    )


@pytest.mark.asyncio
async def test_materializer_cannot_mutate_nested_poller_payload():
    payload = _payload(1)
    original = deepcopy(payload)

    async def materialize(pack, *, config):
        pack["assets"][0]["key"] = "mutated"
        return _ready(pack)

    async def fanout(*args):
        return {}

    await GlobalGenerationSync({}, _Store(), fanout, materialize=materialize).apply(payload)

    assert payload == original


@pytest.mark.asyncio
async def test_one_failure_does_not_cancel_other_packs_or_replace_prior_acceptance():
    payload = _payload(3)
    store = _Store()
    completed = []

    async def materialize(pack, *, config):
        if pack["lessonId"] == "lesson-1":
            await asyncio.sleep(0)
            raise RuntimeError("https://secret.example/token")
        await asyncio.sleep(0.01)
        completed.append(pack["lessonId"])
        return _ready(pack)

    fanout_calls = []

    async def fanout(*args):
        fanout_calls.append(args)

    result = await GlobalGenerationSync({}, store, fanout, materialize=materialize).apply(payload)

    assert result == {
        "state": "retry_wait",
        "errorCode": "generation_materialization_failed",
        "generation": 7,
    }
    assert completed == ["lesson-2", "lesson-3"]
    assert store.accepted_generation == 3
    assert fanout_calls == []
    assert store.retries[0][0] == "generation_materialization_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_value", "env_value", "expected"),
    [(None, None, 3), (2, None, 2), (2, "4", 4)],
)
async def test_materialization_concurrency_is_bounded(monkeypatch, config_value, env_value, expected):
    if env_value is not None:
        monkeypatch.setenv("LESSON_GENERATION_MATERIALIZE_CONCURRENCY", env_value)
    lesson = {}
    if config_value is not None:
        lesson["generation_materialize_concurrency"] = config_value
    active = 0
    maximum = 0
    release = asyncio.Event()

    async def materialize(pack, *, config):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if maximum == min(expected, 6):
            release.set()
        await release.wait()
        await asyncio.sleep(0)
        active -= 1
        return _ready(pack)

    async def fanout(*args):
        return {}

    result = await GlobalGenerationSync({"lesson": lesson}, _Store(), fanout, materialize=materialize).apply(
        _payload(6)
    )

    assert result["state"] == "ready"
    assert maximum == min(expected, 6)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, 9, "bad", True])
async def test_invalid_concurrency_fails_before_side_effects(monkeypatch, value):
    monkeypatch.setenv("LESSON_GENERATION_MATERIALIZE_CONCURRENCY", str(value))
    calls = []
    store = _Store(calls)

    async def materialize(pack, *, config):
        calls.append(("materialize",))
        return _ready(pack)

    async def fanout(*args):
        calls.append(("fanout",))

    result = await GlobalGenerationSync({}, store, fanout, materialize=materialize).apply(_payload(1))

    assert result["errorCode"] == "invalid_materialize_concurrency"
    assert calls == []


@pytest.mark.asyncio
async def test_invalid_materializer_result_prevents_acceptance_and_fanout():
    store = _Store()

    async def materialize(pack, *, config):
        return {"ready": True, "criticalReady": False, "optionalFailedCount": 1}

    fanout = pytest.fail
    result = await GlobalGenerationSync({}, store, fanout, materialize=materialize).apply(_payload(1))

    assert result["errorCode"] == "generation_materialization_invalid_result"
    assert store.accepted_generation == 3


@pytest.mark.asyncio
async def test_materialization_error_uses_only_normalized_stable_code():
    store = _Store()

    async def materialize(pack, *, config):
        raise MaterializationError(
            "CHECKSUM-MISMATCH!!!WITH_URL_HTTPS://SECRET.EXAMPLE/PRIVATE",
            400,
            False,
            "token and url",
        )

    result = await GlobalGenerationSync({}, store, pytest.fail, materialize=materialize).apply(_payload(1))

    assert result["errorCode"] == "generation_materialization_failed"
    assert len(result["errorCode"]) <= 64
    assert store.retries[0][0] == result["errorCode"]


@pytest.mark.asyncio
async def test_safe_materialization_error_code_is_normalized_to_snake_case():
    store = _Store()

    async def materialize(pack, *, config):
        raise MaterializationError("CHECKSUM-MISMATCH", 400, False, "private details")

    result = await GlobalGenerationSync({}, store, pytest.fail, materialize=materialize).apply(_payload(1))

    assert result["errorCode"] == "checksum_mismatch"


@pytest.mark.asyncio
async def test_accept_failure_does_not_fanout_or_replace_prior_generation():
    store = _Store()
    store.fail_accept = True
    fanout_calls = []

    async def fanout(*args):
        fanout_calls.append(args)

    result = await GlobalGenerationSync(
        {}, store, fanout, materialize=lambda pack, *, config: _async(_ready(pack))
    ).apply(_payload(1))

    assert result["errorCode"] == "generation_accept_failed"
    assert store.accepted_generation == 3
    assert fanout_calls == []


@pytest.mark.asyncio
async def test_fanout_failure_keeps_acceptance_and_same_generation_retries_fanout():
    store = _Store()
    materialize_calls = []
    fanout_calls = 0

    async def materialize(pack, *, config):
        materialize_calls.append(pack["cacheKey"])
        return _ready(pack)

    async def fanout(*args):
        nonlocal fanout_calls
        fanout_calls += 1
        if fanout_calls == 1:
            raise RuntimeError("device-id/private-token")
        return {"syncedCount": 1}

    sync = GlobalGenerationSync({}, store, fanout, materialize=materialize)
    first = await sync.apply(_payload(1))
    second = await sync.apply(_payload(1))

    assert first["errorCode"] == "generation_fanout_failed"
    assert store.accepted_generation == 7
    assert second["state"] == "ready"
    assert fanout_calls == 2
    assert len(materialize_calls) == 2


@pytest.mark.asyncio
async def test_retry_attempt_delay_and_retry_persistence_failure_are_safe():
    store = _Store()
    store.retry_attempt = 2
    store.fail_retry = True

    async def materialize(pack, *, config):
        raise RuntimeError("redis://token@host and uuid 123")

    result = await GlobalGenerationSync(
        {},
        store,
        pytest.fail,
        materialize=materialize,
        clock=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc),
    ).apply(_payload(1))

    assert result == {
        "state": "retry_wait",
        "errorCode": "generation_materialization_failed",
        "generation": 7,
    }


@pytest.mark.asyncio
async def test_retry_uses_snapshot_attempt_and_bounded_utc_delay():
    store = _Store()
    store.retry_attempt = 2

    async def materialize(pack, *, config):
        raise RuntimeError("private")

    await GlobalGenerationSync(
        {},
        store,
        pytest.fail,
        materialize=materialize,
        clock=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc),
    ).apply(_payload(1))

    assert store.retries == [("generation_materialization_failed", 3, "2026-07-24T00:00:20Z")]


@pytest.mark.asyncio
async def test_cancelled_materialization_is_reraised_without_retry_state():
    store = _Store()

    async def materialize(pack, *, config):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await GlobalGenerationSync({}, store, pytest.fail, materialize=materialize).apply(_payload(1))

    assert store.retries == []


@pytest.mark.asyncio
async def test_instance_lock_serializes_complete_generation_apply_operations():
    store = _Store()
    active = 0
    maximum = 0

    async def materialize(pack, *, config):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _ready(pack)

    async def fanout(*args):
        return {}

    sync = GlobalGenerationSync({}, store, fanout, materialize=materialize)
    await asyncio.gather(sync.apply(_payload(1)), sync.apply(_payload(1)))

    assert maximum == 1


class _EmptyResponse:
    status_code = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self, chunk_size=65536):
        if False:
            yield b""


class _EmptyClient:
    def __init__(self):
        self.requests = 0

    def stream(self, method, url):
        self.requests += 1
        return _EmptyResponse()


@pytest.mark.asyncio
async def test_zero_byte_asset_materializes_with_complete_readiness_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("LESSON_ASSET_ALLOWED_ORIGINS", "https://assets.example")
    monkeypatch.setenv("LESSON_SD_MAX_FILE_BYTES", "64")
    monkeypatch.setenv("LESSON_SD_MAX_PACK_BYTES", "128")
    digest = hashlib.sha256(b"").hexdigest()
    cache_key = f"empty/v1-{CHECKSUM}"
    manifest = {
        "lessonId": "empty",
        "lessonVersion": 1,
        "profile": "espTft",
        "manifestChecksum": CHECKSUM,
        "cacheKey": cache_key,
        "assets": [
            {
                "key": "empty.bin",
                "sha256": digest,
                "size": 0,
                "mediaType": "application/octet-stream",
                "critical": False,
                "onlineUrl": "https://assets.example/empty.bin",
                "sdPath": f"/sdcard/tbot/lesson-assets/{cache_key}/empty.bin",
            }
        ],
    }
    root = tmp_path / "sd" / "tbot" / "lesson-assets"
    result = await materialize_lesson_sd_pack(
        manifest,
        config={"lesson": {"asset_pack_mount_root": str(root)}},
        client=_EmptyClient(),
        resolver=lambda host: _async(["93.184.216.34"]),
    )

    assert result["ready"] is True
    assert result["criticalReady"] is True
    assert result["optionalFailedCount"] == 0
    assert (Path(root) / cache_key / "empty.bin").read_bytes() == b""


def test_materializer_result_readiness_fields_preserve_legacy_dict_equality():
    from core.lesson.sd_pack_materializer import _result

    result = _result("lesson/v1-" + CHECKSUM, 1, 1, 0)

    legacy = {
        "cacheKey": "lesson/v1-" + CHECKSUM,
        "ready": True,
        "assetCount": 1,
        "downloadedCount": 1,
        "skippedCount": 0,
    }
    assert result == legacy
    assert legacy == result
    assert (result != legacy) is False
    assert (legacy != result) is False
    assert result["criticalReady"] is True
    assert result["optionalFailedCount"] == 0


async def _async(value):
    return value
