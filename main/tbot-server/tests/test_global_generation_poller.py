from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
import pytest

import core.lesson.global_generation_poller as poller_module
from core.lesson.flattened_cinematic_contract import TRGB_MEDIA_TYPE, trgb_container_bytes
from core.lesson.global_generation_poller import (
    GlobalGenerationPoller,
    canonical_json,
)
from core.lesson.global_generation_sync import GlobalGenerationSync

CMS_URL = "https://cms.example/v1/public/lesson-assets/latest"
NOW = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)
HASH_A = "a" * 64
HASH_B = "b" * 64


def _asset(
    key: str = "robot teach.png",
    *,
    size: int = 0,
    cache_key: str = f"lesson-a/v3-{HASH_B}",
) -> dict:
    encoded = quote(key, safe="-_.!~*'()")
    url = f"https://cdn.example/assets/{encoded}"
    path = f"/sdcard/tbot/lesson-assets/{cache_key}/{encoded}"
    return {
        "key": key,
        "sdPath": path,
        "localPath": path,
        "onlineUrl": url,
        "url": url,
        "sha256": HASH_A,
        "size": size,
        "mediaType": "image/png",
        "critical": True,
    }


def _renderer_v3_mp4_asset(*, cache_key: str = f"lesson-a/v3-{HASH_B}") -> dict:
    asset = _asset("scene.opening@v3", size=900000, cache_key=cache_key)
    asset.update({
        "onlineUrl": "https://cdn.example/visuals/scene.opening/v3.mp4",
        "url": "https://cdn.example/visuals/scene.opening/v3.mp4",
        "mediaType": "video/mp4",
        "sharedAssetKey": "scene.opening",
        "sharedAssetVersion": 3,
        "compatibilityMetadata": {
            "codec": "mjpeg", "fps": 10, "durationMs": 9000, "frameCount": 90,
            "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            "chromaKey": None,
        },
        "visualRefs": [
            {"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"},
            {"stepKey": "s1", "phase": "greet", "slot": "backgroundScene.greet"},
        ],
    })
    return asset


def _renderer_v4_mp4_asset(*, cache_key: str = f"lesson-a/v4-{HASH_B}") -> dict:
    asset = _asset("flattenedCinematic.opening", size=900000, cache_key=cache_key)
    asset.update({
        "onlineUrl": "https://cdn.example/lessons/derivatives/" + "d" * 64 + "/opening.mp4",
        "url": "https://cdn.example/lessons/derivatives/" + "d" * 64 + "/opening.mp4",
        "mediaType": "video/mp4",
        "derivativeId": "d" * 64,
        "phaseId": "opening",
        "compatibilityMetadata": {
            "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
            "durationMs": 9000, "frameCount": 90, "hasAudio": False,
        },
    })
    return asset


def _renderer_v4_v2_mp4_asset(
    *,
    cache_key: str = f"lesson-a/v4-{HASH_B}",
    cue_id: str = "barn-listen",
    effect: str = "listen",
) -> dict:
    playback_mode = "loop" if effect in {"greet", "listen", "thinking"} else "once"
    duration_ms = {"listen": 1300, "teach": 2600}[effect]
    asset = _asset(f"flattenedCinematic.{cue_id}", size=duration_ms * 100, cache_key=cache_key)
    asset.update({
        "onlineUrl": f"https://cdn.example/lessons/derivatives/{'d' * 64}/{cue_id}.mp4",
        "url": f"https://cdn.example/lessons/derivatives/{'d' * 64}/{cue_id}.mp4",
        "mediaType": "video/mp4", "derivativeId": "d" * 64,
        "cueId": cue_id, "effect": effect, "stepKey": "barn", "playbackMode": playback_mode,
        "compatibilityMetadata": {
            "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
            "durationMs": duration_ms, "frameCount": duration_ms // 100, "hasAudio": False,
        },
    })
    return asset


def _renderer_v4_v2_trgb_asset(
    *,
    cache_key: str = f"lesson-a/v4-{HASH_B}",
    cue_id: str = "barn-opening",
    effect: str = "opening",
) -> dict:
    playback_mode = "loop" if effect in {"greet", "listen", "thinking"} else "once"
    duration_ms = {"opening": 9500, "listen": 1300, "teach": 2600}[effect]
    frame_count = duration_ms // 100
    derivative_path = f"lessons/derivatives/{'d' * 64}/{cue_id}.trgb"
    asset = _asset(
        f"flattenedCinematic.{cue_id}",
        size=trgb_container_bytes(frame_count),
        cache_key=cache_key,
    )
    asset.update({
        "onlineUrl": f"https://admin.tjbot.vn/lesson-derivatives/{derivative_path}",
        "url": f"https://admin.tjbot.vn/lesson-derivatives/{derivative_path}",
        "mediaType": TRGB_MEDIA_TYPE,
        "derivativeId": "d" * 64,
        "cueId": cue_id,
        "effect": effect,
        "stepKey": "barn",
        "playbackMode": playback_mode,
        "compatibilityMetadata": {
            "codec": "rgb565le", "containerVersion": 1,
            "width": 480, "height": 320, "storedWidth": 320, "storedHeight": 480,
            "orientation": "panelNativeClockwise", "fps": 10,
            "durationMs": duration_ms, "frameCount": frame_count, "frameBytes": 307200,
            "hasAudio": False,
        },
    })
    return asset


def _pack(*, lesson_id: str = "lesson-a", classification: str = "curriculum") -> dict:
    checksum = HASH_B
    cache_key = f"{lesson_id}/v3-{checksum}"
    return {
        "lessonId": lesson_id,
        "lessonVersion": 3,
        "profile": "espTft",
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "classification": classification,
        "assets": [_asset(cache_key=cache_key)],
    }


def _payload(*, generation: int = 8, index: list[dict] | None = None) -> dict:
    packs = deepcopy(index if index is not None else [_pack()])
    return {
        "data": {
            "generation": generation,
            "publishedAt": "2026-07-24T00:00:00.000Z",
            "indexChecksum": hashlib.sha256(canonical_json(packs)).hexdigest(),
            "curriculumLessonCount": sum(
                pack["classification"] == "curriculum" for pack in packs
            ),
            "packCount": len(packs),
            "index": packs,
        }
    }


class FakeStore:
    def __init__(self, *, etag: str | None = None) -> None:
        self.restored_etag = etag
        self.desired: list[tuple[int, str, str]] = []
        self.polled: list[str] = []

    async def snapshot(self) -> dict:
        return {"etag": self.restored_etag, "acceptedGeneration": 7}

    async def set_desired(self, generation: int, index_checksum: str, etag: str) -> None:
        self.desired.append((generation, index_checksum, etag))

    async def mark_polled(self, polled_at: str) -> None:
        self.polled.append(polled_at)


class DurableFakeStore(FakeStore):
    def __init__(self) -> None:
        super().__init__()
        self.state: dict = {}
        self.snapshot_error: Exception | None = None

    async def snapshot(self) -> dict:
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return deepcopy(self.state)

    def mark_accepted(self, data: dict) -> None:
        self.state = {
            "acceptedGeneration": data["generation"],
            "acceptedIndexChecksum": data["indexChecksum"],
            "materializationState": "ready",
        }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
        trust_env=False,
    )


def _response(payload: dict, *, etag: str | None = None, status: int = 200) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if etag is not None:
        headers["etag"] = etag
    return httpx.Response(status, headers=headers, content=json.dumps(payload).encode())


def _config() -> dict:
    return {
        "lesson": {
            "generation_cms_url": CMS_URL,
            "asset_allowed_origins": (
                "https://cdn.example,https://admin.tjbot.vn,https://res.cloudinary.com"
            ),
        }
    }


@pytest.mark.asyncio
async def test_valid_200_stores_desired_before_callback_and_then_uses_etag() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return _response(payload, etag=etag)
        return httpx.Response(304)

    store = DurableFakeStore()
    events: list[tuple[str, object]] = []

    async def callback(data: dict) -> None:
        events.append(("callback", deepcopy(data)))
        assert store.desired == [(8, checksum, etag)]
        store.mark_accepted(data)

    poller = GlobalGenerationPoller(
        _config(), store, callback, http=_client(handler), clock=lambda: NOW
    )
    assert (await poller.run_once())["state"] == "accepted"
    assert (await poller.run_once())["state"] == "not_modified"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert "if-none-match" not in requests[0].headers
    assert requests[1].headers["accept-encoding"] == "identity"
    assert requests[1].headers["if-none-match"] == etag
    assert len(events) == 1


@pytest.mark.asyncio
async def test_validated_renderer_v3_shared_mp4_metadata_passes_through_unchanged() -> None:
    pack = _pack()
    pack["assets"] = [_renderer_v3_mp4_asset(cache_key=pack["cacheKey"])]
    payload = _payload(index=[pack])
    received = []

    async def callback(data: dict) -> None:
        received.append(data)

    poller = GlobalGenerationPoller(
        _config(), FakeStore(), callback,
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {"state": "accepted"}
    assert received[0]["index"][0]["assets"][0] == pack["assets"][0]


@pytest.mark.asyncio
async def test_validated_renderer_v4_flattened_asset_passes_through_unchanged() -> None:
    pack = _pack()
    pack["lessonVersion"] = 4
    pack["cacheKey"] = f"lesson-a/v4-{HASH_B}"
    pack["assets"] = [_renderer_v4_mp4_asset(cache_key=pack["cacheKey"])]
    payload = _payload(index=[pack])
    received = []
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: received.append(data),
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {"state": "accepted"}
    assert received[0]["index"][0]["assets"][0] == pack["assets"][0]


@pytest.mark.asyncio
async def test_validated_renderer_v4_v2_cue_passes_through_unchanged() -> None:
    pack = _pack()
    pack["lessonVersion"] = 4
    pack["cacheKey"] = f"lesson-a/v4-{HASH_B}"
    pack["assets"] = [_renderer_v4_v2_mp4_asset(cache_key=pack["cacheKey"])]
    payload = _payload(index=[pack])
    received = []
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: received.append(data),
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {"state": "accepted"}
    assert received[0]["index"][0]["assets"][0] == pack["assets"][0]


@pytest.mark.asyncio
async def test_validated_renderer_v4_v2_trgb_cue_passes_through_unchanged() -> None:
    pack = _pack()
    pack["lessonVersion"] = 4
    pack["cacheKey"] = f"lesson-a/v4-{HASH_B}"
    pack["assets"] = [_renderer_v4_v2_trgb_asset(cache_key=pack["cacheKey"])]
    payload = _payload(index=[pack])
    received = []
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: received.append(data),
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {"state": "accepted"}
    assert received[0]["index"][0]["assets"][0] == pack["assets"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [
    lambda asset: asset.update(extra=True),
    lambda asset: asset.update(path="lessons/derivatives/" + "d" * 64 + "/barn-opening.trgb"),
    lambda asset: asset.update(
        url=asset["url"].replace("barn-opening.trgb", "other.trgb"),
        onlineUrl=asset["onlineUrl"].replace("barn-opening.trgb", "other.trgb"),
    ),
    lambda asset: asset.update(
        url="https://admin.tjbot.vn/lesson-derivatives/barn-opening.trgb",
        onlineUrl="https://admin.tjbot.vn/lesson-derivatives/barn-opening.trgb",
    ),
    lambda asset: asset.update(
        url=asset["url"].replace("https://admin.tjbot.vn/lesson-derivatives", "https://res.cloudinary.com"),
        onlineUrl=asset["onlineUrl"].replace(
            "https://admin.tjbot.vn/lesson-derivatives", "https://res.cloudinary.com"
        ),
    ),
    lambda asset: asset.update(
        url=asset["url"].replace("/lesson-derivatives/lessons/", "/lessons/"),
        onlineUrl=asset["onlineUrl"].replace("/lesson-derivatives/lessons/", "/lessons/"),
    ),
    lambda asset: asset.update(
        url=asset["url"].replace(
            "lesson-derivatives/lessons/derivatives/",
            "lessons/derivatives/duplicate/lessons/derivatives/",
        ),
        onlineUrl=asset["onlineUrl"].replace(
            "lesson-derivatives/lessons/derivatives/",
            "lessons/derivatives/duplicate/lessons/derivatives/",
        ),
    ),
    lambda asset: asset["compatibilityMetadata"].update(codec="mjpeg"),
    lambda asset: asset["compatibilityMetadata"].update(frameBytes=153600),
    lambda asset: asset.update(size=asset["size"] - 1),
])
async def test_renderer_v4_v2_trgb_cue_rejects_non_exact_contract(mutate) -> None:
    pack = _pack()
    pack["lessonVersion"] = 4
    pack["cacheKey"] = f"lesson-a/v4-{HASH_B}"
    asset = _renderer_v4_v2_trgb_asset(cache_key=pack["cacheKey"])
    mutate(asset)
    pack["assets"] = [asset]
    payload = _payload(index=[pack])
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda _data: None,
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {
        "state": "rejected", "errorCode": "cms_invalid_renderer_v4_mp4",
    }


@pytest.mark.asyncio
async def test_accepts_sorted_non_cue_assets_followed_by_authored_renderer_v4_cues() -> None:
    pack = _pack()
    pack["lessonVersion"] = 4
    pack["cacheKey"] = f"lesson-a/v4-{HASH_B}"
    pack["assets"] = [
        _asset("alpha.png", cache_key=pack["cacheKey"]),
        _asset("zeta.png", cache_key=pack["cacheKey"]),
        _renderer_v4_v2_mp4_asset(
            cache_key=pack["cacheKey"], cue_id="barn-teach", effect="teach"
        ),
        _renderer_v4_v2_mp4_asset(cache_key=pack["cacheKey"]),
    ]
    payload = _payload(index=[pack])
    received = []
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: received.append(data),
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {"state": "accepted"}
    assert received[0]["index"][0]["assets"] == pack["assets"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("unsorted_non_cue", "cms_assets_not_sorted"),
        ("non_cue_after_cue", "cms_assets_not_sorted"),
        ("duplicate_cue", "cms_duplicate_asset_key"),
        ("malformed_cue_identity", "cms_invalid_renderer_v4_mp4"),
    ],
)
async def test_renderer_v4_cue_ordering_retains_strict_asset_rejections(
    case: str,
    code: str,
) -> None:
    pack = _pack()
    pack["lessonVersion"] = 4
    pack["cacheKey"] = f"lesson-a/v4-{HASH_B}"
    cue = _renderer_v4_v2_mp4_asset(cache_key=pack["cacheKey"])
    if case == "unsorted_non_cue":
        pack["assets"] = [
            _asset("zeta.png", cache_key=pack["cacheKey"]),
            _asset("alpha.png", cache_key=pack["cacheKey"]),
            cue,
        ]
    elif case == "non_cue_after_cue":
        pack["assets"] = [cue, _asset("zeta.png", cache_key=pack["cacheKey"])]
    elif case == "duplicate_cue":
        pack["assets"] = [cue, deepcopy(cue)]
    else:
        cue["cueId"] = "other-cue"
        pack["assets"] = [cue]
    payload = _payload(index=[pack])
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda _data: None,
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {"state": "rejected", "errorCode": code}


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [
    lambda asset: asset.update(extra=True),
    lambda asset: asset.pop("derivativeId"),
    lambda asset: asset.update(key="flattenedCinematic.greet"),
    lambda asset: asset.update(phaseId="dance"),
    lambda asset: asset["compatibilityMetadata"].update(width=320),
    lambda asset: asset["compatibilityMetadata"].update(frameCount=89),
])
async def test_renderer_v4_flattened_asset_rejects_non_exact_contract(mutate) -> None:
    pack = _pack()
    pack["lessonVersion"] = 4
    pack["cacheKey"] = f"lesson-a/v4-{HASH_B}"
    asset = _renderer_v4_mp4_asset(cache_key=pack["cacheKey"])
    mutate(asset)
    pack["assets"] = [asset]
    payload = _payload(index=[pack])
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda _data: None,
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    result = await poller.run_once()
    assert result["state"] == "rejected"
    assert result["errorCode"] == "cms_invalid_renderer_v4_mp4"


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "?variant=robot&expires=2000000000#opening"])
async def test_renderer_v3_mp4_public_url_query_and_fragment_are_preserved_exactly(suffix) -> None:
    pack = _pack()
    asset = _renderer_v3_mp4_asset(cache_key=pack["cacheKey"])
    asset["onlineUrl"] += suffix
    asset["url"] += suffix
    pack["assets"] = [asset]
    payload = _payload(index=[pack])
    received = []
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: received.append(data),
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    assert await poller.run_once() == {"state": "accepted"}
    assert received[0]["index"][0]["assets"][0]["onlineUrl"] == asset["onlineUrl"]
    assert received[0]["index"][0]["assets"][0]["url"] == asset["url"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    lambda asset: asset.pop("sharedAssetKey"),
    lambda asset: asset["compatibilityMetadata"].update(codec="h264"),
    lambda asset: asset.update(visualRefs=[]),
])
async def test_rejects_unvalidated_renderer_v3_video(mutation) -> None:
    pack = _pack()
    asset = _renderer_v3_mp4_asset(cache_key=pack["cacheKey"])
    mutation(asset)
    pack["assets"] = [asset]
    payload = _payload(index=[pack])
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda _data: None,
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    result = await poller.run_once()

    assert result["state"] == "rejected"


@pytest.mark.asyncio
async def test_rejects_legacy_or_arbitrary_video_without_renderer_v3_shared_contract() -> None:
    pack = _pack()
    pack["assets"][0].update(mediaType="video/mp4")
    payload = _payload(index=[pack])
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda _data: None,
        http=_client(lambda _request: _response(payload, etag=f'"lesson-assets-g8-{payload["data"]["indexChecksum"]}"')),
        clock=lambda: NOW,
    )

    result = await poller.run_once()

    assert result["state"] == "rejected"


@pytest.mark.asyncio
async def test_exact_weak_etag_is_accepted_as_canonical_generation_identity() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return _response(payload, etag=f"W/{etag}")
        return httpx.Response(304)

    store = DurableFakeStore()

    async def callback(data: dict) -> None:
        store.mark_accepted(data)

    poller = GlobalGenerationPoller(
        _config(), store, callback, http=_client(handler), clock=lambda: NOW
    )

    assert await poller.run_once() == {"state": "accepted"}
    assert store.desired == [(8, checksum, etag)]
    assert await poller.run_once() == {"state": "not_modified"}
    assert requests[1].headers["if-none-match"] == etag


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "etag_template",
    [
        'w/"lesson-assets-g8-{checksum}"',
        'W/W/"lesson-assets-g8-{checksum}"',
        'W/ "lesson-assets-g8-{checksum}"',
        ' W/"lesson-assets-g8-{checksum}"',
        'W/"lesson-assets-g7-{checksum}"',
        'W/"lesson-assets-g8-{checksum}-extra"',
        'W/"lesson-assets-g8-{checksum}", "other"',
    ],
)
async def test_weak_etag_must_be_one_exact_legal_prefix_of_expected_identity(
    etag_template: str,
) -> None:
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    client = _client(
        lambda request: _response(payload, etag=etag_template.format(checksum=checksum))
    )
    store = FakeStore()

    result = await GlobalGenerationPoller(
        _config(), store, lambda data: None, http=client, clock=lambda: NOW
    ).run_once()

    assert result == {"state": "rejected", "errorCode": "cms_etag_mismatch"}
    assert store.desired == []


@pytest.mark.asyncio
async def test_exact_weak_etag_does_not_bypass_body_index_checksum_verification() -> None:
    payload = _payload()
    payload["data"]["indexChecksum"] = "0" * 64
    etag = f'W/"lesson-assets-g8-{"0" * 64}"'
    store = FakeStore()

    result = await GlobalGenerationPoller(
        _config(),
        store,
        lambda data: None,
        http=_client(lambda request: _response(payload, etag=etag)),
        clock=lambda: NOW,
    ).run_once()

    assert result == {
        "state": "rejected",
        "errorCode": "cms_index_checksum_mismatch",
    }
    assert store.desired == []


@pytest.mark.asyncio
async def test_retry_wait_callback_does_not_cache_etag_and_retries_fresh_200() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304)
        return _response(payload, etag=etag)

    class IntegrationStore(FakeStore):
        def __init__(self) -> None:
            super().__init__()
            self.retry_attempt = 0
            self.accepted_generation = 7
            self.accepted_checksum = HASH_A
            self.materialization_state = "ready"

        async def snapshot(self) -> dict:
            return {
                "etag": self.restored_etag,
                "acceptedGeneration": self.accepted_generation,
                "acceptedIndexChecksum": self.accepted_checksum,
                "materializationState": self.materialization_state,
                "retryAttempt": self.retry_attempt,
            }

        async def mark_materializing(self, generation: int) -> None:
            return None

        async def accept(
            self, generation: int, index_checksum: str, accepted_at: str
        ) -> None:
            self.accepted_generation = generation
            self.accepted_checksum = index_checksum
            self.materialization_state = "ready"
            self.retry_attempt = 0

        async def mark_retry(
            self, error_code: str, attempt: int, next_retry_at: str
        ) -> None:
            self.retry_attempt = attempt

    attempts = 0

    async def materialize(pack, *, config):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient private materialization detail")
        return {
            "cacheKey": pack["cacheKey"],
            "ready": True,
            "criticalReady": True,
            "optionalFailedCount": 0,
        }

    async def fanout(generation, index_checksum, packs):
        return {"syncedCount": 1}

    store = IntegrationStore()
    sync = GlobalGenerationSync(_config(), store, fanout, materialize=materialize)
    poller = GlobalGenerationPoller(
        _config(), store, sync.apply, http=_client(handler), clock=lambda: NOW
    )

    assert await poller.run_once() == {
        "state": "rejected",
        "errorCode": "generation_callback_retry",
    }
    assert "if-none-match" not in requests[0].headers
    assert await poller.run_once() == {"state": "accepted"}
    assert "if-none-match" not in requests[1].headers
    assert await poller.run_once() == {"state": "not_modified"}
    assert requests[2].headers["if-none-match"] == etag
    assert attempts == 2


@pytest.mark.asyncio
async def test_cold_start_does_not_send_restored_etag_and_rejects_304() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(304)

    store = FakeStore(etag='"lesson-assets-g7-restored"')
    poller = GlobalGenerationPoller(
        _config(), store, lambda data: None, http=_client(handler), clock=lambda: NOW
    )
    result = await poller.run_once()
    assert result == {"state": "rejected", "errorCode": "cms_cold_304"}
    assert "if-none-match" not in requests[0].headers
    assert store.desired == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "durable_state",
    [
        {},
        {
            "acceptedGeneration": 9,
            "acceptedIndexChecksum": HASH_A,
            "materializationState": "ready",
        },
        {
            "acceptedGeneration": 8,
            "acceptedIndexChecksum": HASH_A,
            "materializationState": "ready",
        },
        {
            "acceptedGeneration": 8,
            "acceptedIndexChecksum": None,
            "materializationState": "ready",
        },
        {
            "acceptedGeneration": 8,
            "acceptedIndexChecksum": HASH_A,
            "materializationState": "materializing",
        },
    ],
)
async def test_cached_304_reapplies_generation_when_durable_acceptance_loses_parity(
    durable_state: dict,
) -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return _response(payload, etag=etag)
        return httpx.Response(304)

    store = DurableFakeStore()
    callback_calls: list[dict] = []

    async def callback(data: dict) -> None:
        callback_calls.append(deepcopy(data))
        store.mark_accepted(data)

    poller = GlobalGenerationPoller(
        _config(), store, callback, http=_client(handler), clock=lambda: NOW
    )
    assert await poller.run_once() == {"state": "accepted"}
    store.state = durable_state

    assert await poller.run_once() == {"state": "accepted"}
    assert requests[1].headers["if-none-match"] == etag
    assert store.desired == [(8, checksum, etag), (8, checksum, etag)]
    assert len(callback_calls) == 2
    assert store.state == {
        "acceptedGeneration": 8,
        "acceptedIndexChecksum": checksum,
        "materializationState": "ready",
    }


@pytest.mark.asyncio
async def test_cached_304_with_healthy_durable_acceptance_remains_not_modified() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return _response(payload, etag=etag)
        return httpx.Response(304)

    store = DurableFakeStore()
    callback_calls: list[dict] = []

    async def callback(data: dict) -> None:
        callback_calls.append(deepcopy(data))
        store.mark_accepted(data)

    poller = GlobalGenerationPoller(
        _config(), store, callback, http=_client(handler), clock=lambda: NOW
    )
    assert await poller.run_once() == {"state": "accepted"}

    assert await poller.run_once() == {"state": "not_modified"}
    assert requests[1].headers["if-none-match"] == etag
    assert store.desired == [(8, checksum, etag)]
    assert len(callback_calls) == 1


@pytest.mark.asyncio
async def test_cached_304_rejects_safely_when_durable_snapshot_fails() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return _response(payload, etag=etag)
        return httpx.Response(304)

    store = DurableFakeStore()
    callback_calls: list[dict] = []

    async def callback(data: dict) -> None:
        callback_calls.append(deepcopy(data))
        store.mark_accepted(data)

    poller = GlobalGenerationPoller(
        _config(), store, callback, http=_client(handler), clock=lambda: NOW
    )
    assert await poller.run_once() == {"state": "accepted"}
    store.snapshot_error = RuntimeError("redis://secret-host/private-data")

    assert await poller.run_once() == {
        "state": "rejected",
        "errorCode": "generation_store_failed",
    }
    assert requests[1].headers["if-none-match"] == etag
    assert store.desired == [(8, checksum, etag)]
    assert len(callback_calls) == 1


@pytest.mark.asyncio
async def test_valid_redirects_are_manual_and_limited_to_two_same_origin_hops() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(302, headers={"location": "/hop-one"})
        if len(requests) == 2:
            return httpx.Response(307, headers={"location": "https://cms.example/hop-two"})
        return _response(payload, etag=f'"lesson-assets-g8-{checksum}"')

    store = FakeStore()
    poller = GlobalGenerationPoller(
        _config(), store, lambda data: None, http=_client(handler), clock=lambda: NOW
    )
    assert (await poller.run_once())["state"] == "accepted"
    assert [str(request.url) for request in requests] == [
        CMS_URL,
        "https://cms.example/hop-one",
        "https://cms.example/hop-two",
    ]
    assert all(request.headers["accept-encoding"] == "identity" for request in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("location", "code"),
    [
        ("https://evil.example/index", "cms_redirect_rejected"),
        ("http://cms.example/index", "cms_redirect_rejected"),
        ("https://cms.example:444/index", "cms_redirect_rejected"),
        ("", "cms_redirect_rejected"),
    ],
)
async def test_redirect_must_preserve_exact_cms_origin(location: str, code: str) -> None:
    client = _client(lambda request: httpx.Response(302, headers={"location": location}))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": code}


@pytest.mark.asyncio
async def test_third_redirect_is_rejected() -> None:
    client = _client(lambda request: httpx.Response(302, headers={"location": "/again"}))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_too_many_redirects"


@pytest.mark.asyncio
async def test_checksum_or_etag_mismatch_never_replaces_prior_state() -> None:
    payload = _payload()
    payload["data"]["indexChecksum"] = "0" * 64
    store = FakeStore()
    callback_calls: list[dict] = []
    client = _client(
        lambda request: _response(payload, etag=f'"lesson-assets-g8-{"0" * 64}"')
    )
    result = await GlobalGenerationPoller(
        _config(), store, callback_calls.append, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_index_checksum_mismatch"
    assert store.desired == []
    assert callback_calls == []

    valid = _payload()
    wrong_etag_client = _client(lambda request: _response(valid, etag='"wrong"'))
    result = await GlobalGenerationPoller(
        _config(), store, callback_calls.append, http=wrong_etag_client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_etag_mismatch"
    assert store.desired == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda body: body.update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"].update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"]["index"][0].update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(extra=True), "cms_unknown_field"),
        (lambda body: body["data"].update(generation=0), "cms_invalid_generation"),
        (lambda body: body["data"].update(generation=1.0), "cms_invalid_generation"),
        (lambda body: body["data"].update(indexChecksum=HASH_A.upper()), "cms_invalid_checksum"),
        (lambda body: body["data"]["index"][0].update(profile="web"), "cms_invalid_profile"),
        (lambda body: body["data"]["index"][0].update(classification="private"), "cms_invalid_classification"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(size=-1), "cms_invalid_asset_size"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(size=1.0), "cms_invalid_asset_size"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(url="https://cdn.example/other"), "cms_asset_alias_mismatch"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(onlineUrl="http://cdn.example/x", url="http://cdn.example/x"), "cms_asset_url_rejected"),
        (lambda body: body["data"]["index"][0]["assets"][0].update(onlineUrl="https://other.example/x", url="https://other.example/x"), "cms_asset_origin_rejected"),
    ],
)
async def test_strict_schema_rejections_have_sanitized_codes(mutate, code: str) -> None:
    payload = _payload()
    mutate(payload)
    # Recompute the checksum when testing a semantic field rather than checksum integrity.
    if code not in {"cms_invalid_checksum", "cms_index_checksum_mismatch"}:
        payload["data"]["indexChecksum"] = hashlib.sha256(
            canonical_json(payload["data"]["index"])
        ).hexdigest()
    checksum = payload["data"]["indexChecksum"]
    client = _client(
        lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"')
    )
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": code}


@pytest.mark.asyncio
async def test_requires_unique_js_utf16_sorted_packs_and_assets() -> None:
    first = _pack(lesson_id="a-astral")
    second = _pack(lesson_id="a-astral")
    duplicate = _payload(index=[first, second])
    checksum = duplicate["data"]["indexChecksum"]
    client = _client(lambda request: _response(duplicate, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_duplicate_lesson_id"

    unsorted = _payload(index=[_pack(lesson_id="z-last"), _pack(lesson_id="a-first")])
    checksum = unsorted["data"]["indexChecksum"]
    client = _client(lambda request: _response(unsorted, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_index_not_sorted"

    asset_unsorted_pack = _pack()
    cache_key = asset_unsorted_pack["cacheKey"]
    asset_unsorted_pack["assets"] = [
        _asset("\ue000", cache_key=cache_key),
        _asset("\U00010000", cache_key=cache_key),
    ]
    asset_unsorted = _payload(index=[asset_unsorted_pack])
    checksum = asset_unsorted["data"]["indexChecksum"]
    client = _client(
        lambda request: _response(asset_unsorted, etag=f'"lesson-assets-g8-{checksum}"')
    )
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_assets_not_sorted"


@pytest.mark.asyncio
async def test_sd_path_uses_rfc3986_strict_encoding_and_the_200_byte_boundary() -> None:
    # T5.1: this used to assert JavaScript encodeURIComponent semantics
    # (safe="-_.!~*'()"), which disagreed with the materializer, the sync path
    # and SharedAssetStore._pack_asset_name — all of which use
    # quote(key, safe=""). The store writes the file under the strict name, so
    # the strict name is the contract.
    payload = _payload()
    asset = payload["data"]["index"][0]["assets"][0]
    asset["key"] = "bang!~'()"
    encoded = "bang%21~%27%28%29"
    cache_key = payload["data"]["index"][0]["cacheKey"]
    path = f"/sdcard/tbot/lesson-assets/{cache_key}/{encoded}"
    asset["sdPath"] = asset["localPath"] = path
    payload["data"]["indexChecksum"] = hashlib.sha256(
        canonical_json(payload["data"]["index"])
    ).hexdigest()
    checksum = payload["data"]["indexChecksum"]
    store = FakeStore()
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    assert (
        await GlobalGenerationPoller(
            _config(), store, lambda data: None, http=client, clock=lambda: NOW
        ).run_once()
    )["state"] == "accepted"

    too_long = _payload()
    too_long["data"]["index"][0]["assets"][0]["key"] = "x" * 201
    too_long["data"]["indexChecksum"] = hashlib.sha256(
        canonical_json(too_long["data"]["index"])
    ).hexdigest()
    checksum = too_long["data"]["indexChecksum"]
    client = _client(lambda request: _response(too_long, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["errorCode"] == "cms_asset_key_too_long"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [123, [], None, True])
async def test_malformed_asset_sha256_is_a_stable_non_retryable_poll_rejection(value) -> None:
    payload = _payload()
    payload["data"]["index"][0]["assets"][0]["sha256"] = value
    payload["data"]["indexChecksum"] = hashlib.sha256(
        canonical_json(payload["data"]["index"])
    ).hexdigest()
    checksum = payload["data"]["indexChecksum"]
    callback_calls: list[dict] = []
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))

    result = await GlobalGenerationPoller(
        _config(), FakeStore(), callback_calls.append, http=client, clock=lambda: NOW
    ).run_once()

    assert result == {"state": "rejected", "errorCode": "cms_invalid_asset_checksum"}
    assert callback_calls == []


@pytest.mark.asyncio
async def test_cache_key_over_the_canonical_cap_fails_with_stable_sanitized_code() -> None:
    # T5.1: the canonical cap is CACHE_KEY_MAX_BYTES = 205, exactly saturated by
    # a 128-byte slug + a 10-digit version. This test used to assert a local
    # 200-byte cap, so it rejected a maximal-but-legal key the materializer
    # accepted. 205 is now accepted; the 11-digit version that pushes it to 206
    # is the rejection boundary.
    lesson_id = "a" * 128
    version = 1_234_567_890
    cache_key = f"{lesson_id}/v{version}-{HASH_B}"
    assert len(cache_key.encode("ascii")) == 205
    pack = _pack(lesson_id=lesson_id)
    pack["lessonVersion"] = version
    pack["cacheKey"] = cache_key
    pack["assets"] = [_asset(cache_key=cache_key)]
    payload = _payload(index=[pack])
    checksum = payload["data"]["indexChecksum"]
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["state"] == "accepted"

    over_version = 12_345_678_901
    over_key = f"{lesson_id}/v{over_version}-{HASH_B}"
    assert len(over_key.encode("ascii")) == 206
    over_pack = _pack(lesson_id=lesson_id)
    over_pack["lessonVersion"] = over_version
    over_pack["cacheKey"] = over_key
    over_pack["assets"] = [_asset(cache_key=over_key)]
    over_payload = _payload(index=[over_pack])
    over_checksum = over_payload["data"]["indexChecksum"]
    over_client = _client(
        lambda request: _response(over_payload, etag=f'"lesson-assets-g8-{over_checksum}"')
    )
    over_result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=over_client, clock=lambda: NOW
    ).run_once()
    assert over_result == {"state": "rejected", "errorCode": "cms_cache_key_too_long"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "published_at",
    ["2026-07-24 00:00:00+00:00", "2026-07-24T00:00+00:00"],
)
async def test_published_at_rejects_non_rfc3339_datetime_forms(published_at: str) -> None:
    payload = _payload()
    payload["data"]["publishedAt"] = published_at
    checksum = payload["data"]["indexChecksum"]
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": "cms_invalid_published_at"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "published_at",
    [
        "2026-07-24T00:00:00Z",
        "2026-07-24T00:00:00.123456Z",
        "2026-07-24T00:00:00+00:00",
        "2026-07-24T00:00:00.1+00:00",
    ],
)
async def test_published_at_accepts_strict_utc_rfc3339(published_at: str) -> None:
    payload = _payload()
    payload["data"]["publishedAt"] = published_at
    checksum = payload["data"]["indexChecksum"]
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
    ).run_once()
    assert result["state"] == "accepted"


@pytest.mark.parametrize(
    "origin",
    ["https://bad host", "https://bad\thost", "https://-bad.example", "https://bad_.example"],
)
def test_configured_allowed_origin_rejects_malformed_hostname(origin: str) -> None:
    config = _config()
    config["lesson"]["asset_allowed_origins"] = origin
    with pytest.raises(ValueError, match="HTTPS origins"):
        GlobalGenerationPoller(config, FakeStore(), lambda data: None)


@pytest.mark.asyncio
async def test_malformed_matching_asset_origin_never_reaches_callback() -> None:
    config = _config()
    config["lesson"]["asset_allowed_origins"] = "https://bad host"
    callback_calls: list[dict] = []
    with pytest.raises(ValueError, match="HTTPS origins"):
        GlobalGenerationPoller(config, FakeStore(), callback_calls.append)
    assert callback_calls == []


@pytest.mark.asyncio
async def test_asset_url_with_parser_stripped_hostname_control_is_rejected() -> None:
    payload = _payload()
    asset = payload["data"]["index"][0]["assets"][0]
    asset["onlineUrl"] = asset["url"] = "https://cdn.example\t/assets/file.png"
    payload["data"]["indexChecksum"] = hashlib.sha256(
        canonical_json(payload["data"]["index"])
    ).hexdigest()
    checksum = payload["data"]["indexChecksum"]
    callback_calls: list[dict] = []
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), FakeStore(), callback_calls.append, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": "cms_asset_url_rejected"}
    assert callback_calls == []


@pytest.mark.parametrize(
    "origin",
    ["https://cdn.example", "https://127.0.0.1:8443", "https://[2001:db8::1]:9443"],
)
def test_configured_allowed_origin_accepts_valid_host_kinds(origin: str) -> None:
    config = _config()
    config["lesson"]["asset_allowed_origins"] = origin
    poller = GlobalGenerationPoller(config, FakeStore(), lambda data: None, http=object())
    assert poller.allowed_origins


@pytest.mark.asyncio
async def test_rejects_nan_oversized_body_and_non_200_without_leaking_details() -> None:
    cases = [
        (httpx.Response(200, content=b'{"data":{"generation":NaN}}'), "cms_invalid_json"),
        (httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1)), "cms_response_too_large"),
        (httpx.Response(503, content=b"secret backend body"), "cms_http_status"),
    ]
    for response, code in cases:
        client = _client(lambda request, response=response: response)
        result = await GlobalGenerationPoller(
            _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW
        ).run_once()
        assert result == {"state": "rejected", "errorCode": code}


@pytest.mark.asyncio
async def test_start_polls_immediately_and_uses_exact_bounded_backoff() -> None:
    attempts = 0
    sleeps: list[float] = []
    first_request = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        first_request.set()
        return httpx.Response(503)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 8:
            raise asyncio.CancelledError

    poller = GlobalGenerationPoller(
        _config(),
        FakeStore(),
        lambda data: None,
        http=_client(handler),
        clock=lambda: NOW,
        sleep=sleep,
    )
    assert poller.start() is None
    await asyncio.wait_for(first_request.wait(), timeout=1)
    while len(sleeps) < 8:
        await asyncio.sleep(0)
    await poller.stop()
    assert attempts == 8
    assert sleeps == [5, 10, 20, 40, 80, 160, 300, 300]


@pytest.mark.asyncio
async def test_successful_loop_poll_returns_to_30_second_interval() -> None:
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)
        raise asyncio.CancelledError

    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=client, clock=lambda: NOW, sleep=sleep
    )
    poller.start()
    while not sleeps:
        await asyncio.sleep(0)
    await poller.stop()
    assert sleeps == [30]


@pytest.mark.asyncio
async def test_default_client_disables_redirects_and_environment(monkeypatch) -> None:
    captured: dict = {}

    class Client:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        async def aclose(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    poller = GlobalGenerationPoller(_config(), FakeStore(), lambda data: None)
    assert captured["follow_redirects"] is False
    assert captured["trust_env"] is False
    assert captured["timeout"].connect == 3
    assert captured["timeout"].read == 15
    await poller.stop()
    assert captured["closed"] is True


def test_poller_source_remains_python_310_compatible() -> None:
    source = inspect.getsource(poller_module)
    assert "datetime.UTC" not in source
    assert "from datetime import UTC" not in source
    assert "asyncio.timeout" not in source
    assert "asyncio.wait_for" in source
    assert "except asyncio.TimeoutError" in source


@pytest.mark.asyncio
async def test_concurrent_cold_polls_serialize_and_second_uses_new_etag() -> None:
    requests: list[httpx.Request] = []
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    etag = f'"lesson-assets-g8-{checksum}"'

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            await asyncio.sleep(0)
            return _response(payload, etag=etag)
        return httpx.Response(304)

    store = DurableFakeStore()
    callback_calls: list[dict] = []

    async def callback(data: dict) -> None:
        callback_calls.append(data)
        store.mark_accepted(data)
        await asyncio.sleep(0)

    poller = GlobalGenerationPoller(
        _config(), store, callback, http=_client(handler), clock=lambda: NOW
    )
    first, second = await asyncio.gather(poller.run_once(), poller.run_once())
    assert {first["state"], second["state"]} == {"accepted", "not_modified"}
    assert store.desired == [(8, checksum, etag)]
    assert len(callback_calls) == 1
    assert "if-none-match" not in requests[0].headers
    assert requests[1].headers["if-none-match"] == etag


@pytest.mark.asyncio
async def test_trigger_retry_returns_immediately_and_coalesces_duplicate_requests() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=object(), clock=lambda: NOW
    )

    async def delayed_run_once():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return {"state": "accepted"}

    poller.run_once = delayed_run_once

    first = await asyncio.wait_for(poller.trigger_retry(), timeout=0.05)
    second = await asyncio.wait_for(poller.trigger_retry(), timeout=0.05)
    await asyncio.wait_for(entered.wait(), timeout=1)

    assert first == {"state": "accepted"}
    assert second == {"state": "not_modified"}
    assert calls == 1

    release.set()
    await poller.stop()


@pytest.mark.asyncio
async def test_stop_cancels_and_awaits_an_active_triggered_retry() -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=object(), clock=lambda: NOW
    )

    async def delayed_run_once():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    poller.run_once = delayed_run_once
    assert await poller.trigger_retry() == {"state": "accepted"}
    await asyncio.wait_for(entered.wait(), timeout=1)

    await asyncio.wait_for(poller.stop(), timeout=1)

    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_triggered_retry_consumes_background_exceptions_without_leaking_details() -> None:
    messages = []

    class Logger:
        def warning(self, message):
            messages.append(message)

    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=object(), clock=lambda: NOW
    )
    poller.log = Logger()

    async def crashing_run_once():
        raise RuntimeError("https://private.example/?token=secret")

    poller.run_once = crashing_run_once
    assert await poller.trigger_retry() == {"state": "accepted"}
    await asyncio.sleep(0)
    await poller.stop()

    assert messages == ["lesson generation poll state=generation_retry_failed"]


@pytest.mark.asyncio
async def test_stop_cancellation_releases_poll_lock_without_deadlock() -> None:
    entered = asyncio.Event()
    blocker = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await blocker.wait()
        return httpx.Response(503)

    poller = GlobalGenerationPoller(
        _config(), FakeStore(), lambda data: None, http=_client(handler), clock=lambda: NOW
    )
    poller.start()
    await asyncio.wait_for(entered.wait(), timeout=1)
    waiting_poll = asyncio.create_task(poller.run_once())
    await asyncio.wait_for(poller.stop(), timeout=1)
    result = await asyncio.wait_for(waiting_poll, timeout=1)
    assert result == {"state": "rejected", "errorCode": "cms_http_status"}


@pytest.mark.asyncio
async def test_store_failure_has_distinct_sanitized_code_and_preserves_accepted() -> None:
    class FailingStore(FakeStore):
        async def set_desired(self, generation: int, index_checksum: str, etag: str) -> None:
            raise RuntimeError("redis://secret-host/private-data")

    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    callback_calls: list[dict] = []
    store = FailingStore()
    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), store, callback_calls.append, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": "generation_store_failed"}
    assert (await store.snapshot())["acceptedGeneration"] == 7
    assert callback_calls == []


@pytest.mark.asyncio
async def test_callback_failure_has_distinct_sanitized_code_and_preserves_accepted() -> None:
    payload = _payload()
    checksum = payload["data"]["indexChecksum"]
    store = FakeStore()

    async def callback(data: dict) -> None:
        raise RuntimeError("https://secret.example/path?token=private")

    client = _client(lambda request: _response(payload, etag=f'"lesson-assets-g8-{checksum}"'))
    result = await GlobalGenerationPoller(
        _config(), store, callback, http=client, clock=lambda: NOW
    ).run_once()
    assert result == {"state": "rejected", "errorCode": "generation_callback_failed"}
    assert (await store.snapshot())["acceptedGeneration"] == 7
    assert store.desired == [(8, checksum, f'"lesson-assets-g8-{checksum}"')]
