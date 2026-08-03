import asyncio
import base64
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import quote

import pytest

from core.lesson import sd_pack_sync
from core.lesson.shared_asset_store import SharedAssetStore
from core.providers.tools.device_mcp import mcp_handler

_BACKEND_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")

def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def _rich_sd_path(cache_key: str, key: str) -> str:
    return f"/sdcard/tbot/lesson-assets/{cache_key}/{quote(key, safe='')}"


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

def test_cached_asset_packs_preserve_ready_rich_pack_metadata(tmp_path):
    checksum = "0123456789abcdef" * 4
    cache_key = f"lesson-a/v3-{checksum}"
    pack_root = tmp_path / "sd" / "tbot" / "lesson-assets"
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=pack_root)
    poster = b"poster"
    barn = b"barn"
    poster_sha = hashlib.sha256(poster).hexdigest()
    barn_sha = hashlib.sha256(barn).hexdigest()
    store.put_bytes(poster, poster_sha)
    store.put_bytes(barn, barn_sha)
    manifest = {
        "assignmentVersion": 9,
        "lessonId": "lesson-a",
        "lessonVersion": 3,
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "ready": True,
        "assets": [
            {
                "key": "teachingObject.barn",
                "sha256": barn_sha,
                "size": len(barn),
                "mediaType": "image/png",
                "critical": False,
                "onlineUrl": "https://assets.example/barn.png?sig=keep-me",
                "sdPath": _rich_sd_path(cache_key, "teachingObject.barn"),
            },
            {
                "key": "backgroundScene.poster",
                "sha256": poster_sha,
                "size": len(poster),
                "mediaType": "image/jpeg",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.jpg?sig=secret&expires=1",
                "sdPath": _rich_sd_path(cache_key, "backgroundScene.poster"),
            },
        ],
    }
    store.commit_pack(
        cache_key,
        {"backgroundScene.poster": poster_sha, "teachingObject.barn": barn_sha},
        manifest=manifest,
    )

    packs = list(
        sd_pack_sync.cached_asset_packs(
            {
                "lesson": {
                    "asset_cache_root": str(pack_root),
                    "asset_pack_mount_root": str(pack_root),
                    "asset_public_base_url": "https://computed.example",
                }
            }
        )
    )

    assert len(packs) == 1
    pack = packs[0]
    assert pack["manifestChecksum"] == checksum
    assert pack["localRoot"] == f"/sdcard/tbot/lesson-assets/{cache_key}"
    assert [asset["key"] for asset in pack["assets"]] == [
        "backgroundScene.poster",
        "teachingObject.barn",
    ]
    by_key = {asset["key"]: asset for asset in pack["assets"]}
    poster_asset = by_key["backgroundScene.poster"]
    assert poster_asset["sha256"] == poster_sha
    assert len(poster_asset["sha256"]) == 64
    assert poster_asset["sha256"] == poster_asset["sha256"].lower()
    assert poster_asset["size"] == len(poster)
    assert type(poster_asset["size"]) is int
    assert poster_asset["mediaType"] == "image/jpeg"
    assert poster_asset["critical"] is True
    assert poster_asset["onlineUrl"] == "https://assets.example/poster.jpg?sig=secret&expires=1"
    assert poster_asset["url"] == poster_asset["onlineUrl"]
    assert poster_asset["sdPath"] == _rich_sd_path(cache_key, "backgroundScene.poster")
    assert poster_asset["localPath"] == poster_asset["sdPath"]
    assert by_key["teachingObject.barn"]["critical"] is False


def test_cached_asset_packs_preserve_renderer_v4_flattened_identity(tmp_path):
    checksum = "4" * 64
    cache_key = f"lesson-a/v4-{checksum}"
    pack_root = tmp_path / "sd" / "tbot" / "lesson-assets"
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=pack_root)
    content = b"flattened"
    digest = hashlib.sha256(content).hexdigest()
    key = "flattenedCinematic.opening"
    store.put_bytes(content, digest)
    manifest = {
        "lessonId": "lesson-a", "lessonVersion": 4, "profile": "espTft",
        "manifestChecksum": checksum, "cacheKey": cache_key,
        "assets": [{
            "key": key, "sha256": digest, "size": len(content), "mediaType": "video/mp4",
            "critical": True,
            "onlineUrl": "https://assets.example/lessons/derivatives/" + "d" * 64 + "/opening.mp4",
            "sdPath": _rich_sd_path(cache_key, key),
            "derivativeId": "d" * 64, "phaseId": "opening",
            "compatibilityMetadata": {
                "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
                "durationMs": 9000, "frameCount": 90, "hasAudio": False,
            },
        }],
    }
    store.commit_pack(cache_key, {key: digest}, manifest=manifest)

    packs = list(sd_pack_sync.cached_asset_packs({"lesson": {"asset_pack_mount_root": str(pack_root)}}))

    assert len(packs) == 1
    asset = packs[0]["assets"][0]
    assert asset["derivativeId"] == "d" * 64
    assert asset["phaseId"] == "opening"
    assert asset["compatibilityMetadata"]["frameCount"] == 90


def test_cached_asset_packs_preserve_renderer_v4_v2_cue_identity(tmp_path):
    checksum = "5" * 64
    cache_key = f"lesson-a/v4-{checksum}"
    pack_root = tmp_path / "sd" / "tbot" / "lesson-assets"
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=pack_root)
    content = b"flattened-cue"
    digest = hashlib.sha256(content).hexdigest()
    key = "flattenedCinematic.barn-listen"
    store.put_bytes(content, digest)
    manifest = {
        "lessonId": "lesson-a", "lessonVersion": 4, "profile": "espTft",
        "manifestChecksum": checksum, "cacheKey": cache_key,
        "assets": [{
            "key": key, "sha256": digest, "size": len(content), "mediaType": "video/mp4",
            "critical": True,
            "onlineUrl": "https://assets.example/lessons/derivatives/" + "d" * 64 + "/barn-listen.mp4",
            "sdPath": _rich_sd_path(cache_key, key), "derivativeId": "d" * 64,
            "cueId": "barn-listen", "effect": "listen", "stepKey": "barn", "playbackMode": "loop",
            "compatibilityMetadata": {
                "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
                "durationMs": 1300, "frameCount": 13, "hasAudio": False,
            },
        }],
    }
    store.commit_pack(cache_key, {key: digest}, manifest=manifest)

    packs = list(sd_pack_sync.cached_asset_packs({"lesson": {"asset_pack_mount_root": str(pack_root)}}))

    asset = packs[0]["assets"][0]
    assert {field: asset[field] for field in ("cueId", "effect", "stepKey", "playbackMode")} == {
        "cueId": "barn-listen", "effect": "listen", "stepKey": "barn", "playbackMode": "loop",
    }

def test_cached_asset_packs_use_pack_mount_root_when_cache_root_is_absent(tmp_path):
    checksum = "0123456789abcdef" * 4
    cache_key = f"lesson-a/v3-{checksum}"
    pack_root = tmp_path / "sd" / "tbot" / "lesson-assets"
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=pack_root)
    poster = b"poster"
    poster_sha = hashlib.sha256(poster).hexdigest()
    store.put_bytes(poster, poster_sha)
    manifest = {
        "lessonId": "lesson-a",
        "lessonVersion": 3,
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "assets": [
            {
                "key": "backgroundScene.poster",
                "sha256": poster_sha,
                "size": len(poster),
                "mediaType": "image/jpeg",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.jpg",
                "sdPath": _rich_sd_path(cache_key, "backgroundScene.poster"),
            }
        ],
    }
    store.commit_pack(cache_key, {"backgroundScene.poster": poster_sha}, manifest=manifest)

    packs = list(
        sd_pack_sync.cached_asset_packs(
            {"lesson": {"asset_pack_mount_root": str(pack_root)}}
        )
    )

    assert [pack["cacheKey"] for pack in packs] == [cache_key]
    assert packs[0]["assets"][0]["sha256"] == poster_sha

def test_cached_asset_packs_yield_ready_rich_pack_without_public_base_url(tmp_path):
    checksum = "fedcba9876543210" * 4
    cache_key = f"lesson-a/v3-{checksum}"
    pack_root = tmp_path / "sd" / "tbot" / "lesson-assets"
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=pack_root)
    poster = b"poster"
    poster_sha = hashlib.sha256(poster).hexdigest()
    store.put_bytes(poster, poster_sha)
    manifest = {
        "assignmentVersion": 9,
        "lessonId": "lesson-a",
        "lessonVersion": 3,
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "ready": True,
        "assets": [
            {
                "key": "backgroundScene.poster",
                "sha256": poster_sha,
                "size": len(poster),
                "mediaType": "image/jpeg",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.jpg?sig=secret&expires=1",
                "sdPath": _rich_sd_path(cache_key, "backgroundScene.poster"),
            }
        ],
    }
    store.commit_pack(cache_key, {"backgroundScene.poster": poster_sha}, manifest=manifest)

    packs = list(
        sd_pack_sync.cached_asset_packs(
            {
                "lesson": {
                    "asset_cache_root": str(pack_root),
                    "asset_pack_mount_root": str(pack_root),
                }
            }
        )
    )

    assert len(packs) == 1
    asset = packs[0]["assets"][0]
    assert asset["onlineUrl"] == "https://assets.example/poster.jpg?sig=secret&expires=1"
    assert asset["url"] == asset["onlineUrl"]
    assert asset["sdPath"] == _rich_sd_path(cache_key, "backgroundScene.poster")
    assert asset["localPath"] == asset["sdPath"]
    assert asset["sha256"] == poster_sha
    assert len(asset["sha256"]) == 64
    assert asset["sha256"] == asset["sha256"].lower()
    assert asset["size"] == len(poster)
    assert type(asset["size"]) is int
    assert asset["mediaType"] == "image/jpeg"
    assert asset["critical"] is True

def test_cached_asset_packs_fail_closed_for_malformed_rich_pack(tmp_path):
    checksum = "0123456789abcdef" * 4
    cache_key = f"lesson-a/v3-{checksum}"
    pack_root = tmp_path / "sd" / "tbot" / "lesson-assets"
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=pack_root)
    poster = b"poster"
    poster_sha = hashlib.sha256(poster).hexdigest()
    store.put_bytes(poster, poster_sha)
    manifest = {
        "lessonId": "lesson-a",
        "lessonVersion": 3,
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "assets": [
            {
                "key": "backgroundScene.poster",
                "sha256": poster_sha,
                "size": len(poster),
                "mediaType": "image/jpeg",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.jpg?sig=secret",
                "sdPath": _rich_sd_path(cache_key, "backgroundScene.poster"),
            }
        ],
    }
    store.commit_pack(cache_key, {"backgroundScene.poster": poster_sha}, manifest=manifest)
    pack_json = pack_root / cache_key / "pack.json"
    broken = json.loads(pack_json.read_text(encoding="utf-8"))
    broken["assets"][0].pop("onlineUrl")
    pack_json.write_text(json.dumps(broken, sort_keys=True), encoding="utf-8")
    ready = {"packSha256": hashlib.sha256(pack_json.read_bytes()).hexdigest(), "assetCount": 1}
    (pack_root / cache_key / "READY").write_text(json.dumps(ready), encoding="utf-8")

    packs = list(
        sd_pack_sync.cached_asset_packs(
            {
                "lesson": {
                    "asset_cache_root": str(pack_root),
                    "asset_pack_mount_root": str(pack_root),
                    "asset_public_base_url": "https://computed.example",
                }
            }
        )
    )

    assert packs == []

def test_cached_asset_packs_normalize_historical_aliases_without_canonical_short_checksum(tmp_path):
    cache_root = tmp_path / "lesson_assets"
    pack_dir = cache_root / "lesson-a" / "v3-abc123"
    pack_dir.mkdir(parents=True)
    (pack_dir / "poster").write_bytes(b"poster")

    packs = list(
        sd_pack_sync.cached_asset_packs(
            {
                "lesson": {
                    "asset_cache_root": str(cache_root),
                    "asset_public_base_url": "https://esp.example",
                }
            }
        )
    )

    assert len(packs) == 1
    pack = packs[0]
    assert pack.get("manifestChecksum") in (None, "")
    assert pack.get("historicalManifestChecksum") == "abc123"
    asset = pack["assets"][0]
    assert asset["url"].startswith("https://esp.example/")
    assert asset["onlineUrl"] == asset["url"]
    assert asset["localPath"] == "sd://tbot/lesson-assets/lesson-a/v3-abc123/poster"
    assert asset["sdPath"] == asset["localPath"]
    assert asset["mediaType"] == "application/octet-stream"
    assert asset["critical"] is True


@pytest.mark.asyncio
async def test_sync_cached_lesson_assets_to_sd_calls_mcp_for_each_cached_pack(monkeypatch, tmp_path):
    cache_root = tmp_path / "lesson_assets"
    first_checksum = "a" * 64
    second_checksum = "b" * 64
    first_key = f"lesson-a/v1-{first_checksum}"
    second_key = f"lesson-b/v2-{second_checksum}"
    first = cache_root / first_key
    second = cache_root / second_key
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
        return json.dumps(
            {
                "ready": True,
                "cacheKey": pack["cacheKey"],
                "manifestChecksum": pack["manifestChecksum"],
                "downloadedCount": len(pack["assets"]),
                "skippedCount": 0,
                "failedCount": 0,
            }
        )

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["packs"] == 2
    assert result["synced"] == 2
    assert result["failed"] == 0
    assert result["resultsByCacheKey"] == {
        first_key: {
            "cacheKey": first_key,
            "downloadedCount": 1,
            "skippedCount": 0,
            "reusedCount": 0,
            "failedCount": 0,
            "criticalFailedCount": 0,
            "ready": True,
        },
        second_key: {
            "cacheKey": second_key,
            "downloadedCount": 1,
            "skippedCount": 0,
            "reusedCount": 0,
            "failedCount": 0,
            "criticalFailedCount": 0,
            "ready": True,
        },
    }
    assert calls == [
        ("self.lesson_assets.sync_to_sd", first_key),
        ("self.lesson_assets.sync_to_sd", second_key),
    ]


@pytest.mark.asyncio
async def test_sync_cached_lesson_assets_to_sd_preserves_per_cache_firmware_counts(
    monkeypatch, tmp_path
):
    cache_root = tmp_path / "lesson_assets"
    first_checksum = "c" * 64
    second_checksum = "d" * 64
    first_key = f"lesson-a/v1-{first_checksum}"
    second_key = f"lesson-b/v2-{second_checksum}"
    first = cache_root / first_key
    second = cache_root / second_key
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "backgroundScene.poster").write_bytes(b"a")
    (first / "teachingObject.barn").write_bytes(b"b")
    (first / "intro.mp3").write_bytes(b"c")
    (second / "teachingObject.barn").write_bytes(b"b")

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
        if pack["cacheKey"] == first_key:
            return {
                "ready": True,
                "cacheKey": first_key,
                "manifestChecksum": first_checksum,
                "downloadedCount": 2,
                "skippedCount": 1,
                "failedCount": 0,
                "criticalFailedCount": 0,
            }
        return {
            "ready": False,
            "cacheKey": second_key,
            "manifestChecksum": second_checksum,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 2,
            "criticalFailedCount": 1,
            "errorCode": " SD failed/token=secret ",
        }

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["synced"] == 1
    assert result["failed"] == 1
    assert result["resultsByCacheKey"] == {
        first_key: {
            "cacheKey": first_key,
            "downloadedCount": 2,
            "skippedCount": 1,
            "reusedCount": 0,
            "failedCount": 0,
            "criticalFailedCount": 0,
            "ready": True,
        },
        second_key: {
            "cacheKey": second_key,
            "downloadedCount": 1,
            "skippedCount": 0,
            "reusedCount": 0,
            "failedCount": 2,
            "criticalFailedCount": 1,
            "ready": False,
            "errorCode": "sd_failed_token_secret",
        },
    }

def test_normalize_firmware_sync_result_error_code_matches_backend_regex():
    cases = [
        (" SD failed/token=secret ", "sd_failed_token_secret"),
        ("___...OPTIONAL--THUMBNAIL FAILED!!!", "optional_thumbnail_failed_"),
        ("9Already_OK", "9already_ok"),
        ("A" * 80, "a" * 64),
        ("___---...", ""),
    ]

    for raw, expected in cases:
        result = sd_pack_sync.normalize_firmware_sync_result(
            "lesson-a/v1-a",
            {"ready": False, "criticalFailedCount": 1, "errorCode": raw},
        )
        if expected:
            assert result["errorCode"] == expected
            assert _BACKEND_ERROR_CODE_RE.fullmatch(result["errorCode"])
        else:
            assert "errorCode" not in result

@pytest.mark.asyncio
async def test_sync_cached_lesson_assets_to_sd_rejects_optional_asset_failure(
    monkeypatch, tmp_path
):
    cache_root = tmp_path / "lesson_assets"
    checksum = "e" * 64
    cache_key = f"lesson-a/v1-{checksum}"
    pack_dir = cache_root / cache_key
    pack_dir.mkdir(parents=True)
    (pack_dir / "optional-art.png").write_bytes(b"a")

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

    async def fake_call(_conn, _client, _pack):
        return {
            "ready": True,
            "cacheKey": cache_key,
            "manifestChecksum": checksum,
            "downloadedCount": 0,
            "skippedCount": 0,
            "failedCount": 1,
            "criticalFailedCount": 0,
            "errorCode": "OPTIONAL_THUMBNAIL_FAILED",
        }

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["synced"] == 0
    assert result["failed"] == 1
    assert result["resultsByCacheKey"][cache_key] == {
        "cacheKey": cache_key,
        "downloadedCount": 0,
        "skippedCount": 0,
        "reusedCount": 0,
        "failedCount": 1,
        "criticalFailedCount": 0,
        "ready": False,
        "errorCode": "optional_thumbnail_failed",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "firmware_result",
    [
        {
            "ready": True,
            "manifestChecksum": "a" * 64,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 0,
        },
        {
            "ready": True,
            "cacheKey": "lesson-b/v1-" + "a" * 64,
            "manifestChecksum": "a" * 64,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 0,
        },
        {
            "ready": True,
            "cacheKey": "lesson-a/v1-" + "a" * 64,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 0,
        },
        {
            "ready": True,
            "cacheKey": "lesson-a/v1-" + "a" * 64,
            "manifestChecksum": "b" * 64,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 0,
        },
        {
            "cacheKey": "lesson-a/v1-" + "a" * 64,
            "manifestChecksum": "a" * 64,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 0,
        },
        {
            "ready": True,
            "cacheKey": "lesson-a/v1-" + "a" * 64,
            "manifestChecksum": "a" * 64,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 1,
        },
        {
            "ready": True,
            "cacheKey": "lesson-a/v1-" + "a" * 64,
            "manifestChecksum": "a" * 64,
            "downloadedCount": 0,
            "skippedCount": 0,
            "failedCount": 0,
        },
        {
            "ready": True,
            "cacheKey": "lesson-a/v1-" + "a" * 64,
            "manifestChecksum": "a" * 64,
            "downloadedCount": 1,
            "skippedCount": 0,
            "reusedCount": -1,
            "failedCount": 0,
        },
    ],
    ids=[
        "missing-cache-key",
        "wrong-cache-key",
        "missing-checksum",
        "wrong-checksum",
        "missing-ready",
        "failed-assets",
        "count-mismatch",
        "negative-reused-count",
    ],
)
async def test_background_sync_rejects_inexact_firmware_attestation(
    monkeypatch, firmware_result
):
    checksum = "a" * 64
    cache_key = f"lesson-a/v1-{checksum}"
    pack = {
        "cacheKey": cache_key,
        "manifestChecksum": checksum,
        "assets": [{"key": "poster"}],
    }

    class Client:
        async def is_ready(self):
            return True

    class Conn:
        config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        mcp_client = Client()

    monkeypatch.setattr(sd_pack_sync, "cached_asset_packs", lambda _config: [pack])

    async def fake_call(_conn, _client, _pack):
        return firmware_result

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["synced"] == 0
    assert result["failed"] == 1
    assert result["resultsByCacheKey"][cache_key]["ready"] is False


@pytest.mark.asyncio
async def test_background_sync_accepts_exact_firmware_attestation(monkeypatch):
    checksum = "a" * 64
    cache_key = f"lesson-a/v1-{checksum}"
    pack = {
        "cacheKey": cache_key,
        "manifestChecksum": checksum,
        "assets": [{"key": "poster"}, {"key": "barn"}],
    }

    class Client:
        async def is_ready(self):
            return True

    class Conn:
        config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        mcp_client = Client()

    monkeypatch.setattr(sd_pack_sync, "cached_asset_packs", lambda _config: [pack])

    async def fake_call(_conn, _client, _pack):
        return {
            "ready": True,
            "cacheKey": cache_key,
            "manifestChecksum": checksum,
            "downloadedCount": 1,
            "skippedCount": 1,
            "failedCount": 0,
        }

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["synced"] == 1
    assert result["failed"] == 0
    assert result["resultsByCacheKey"][cache_key]["ready"] is True


@pytest.mark.asyncio
async def test_background_sync_accepts_reused_firmware_attestation(monkeypatch):
    checksum = "a" * 64
    cache_key = f"lesson-a/v1-{checksum}"
    pack = {
        "cacheKey": cache_key,
        "manifestChecksum": checksum,
        "assets": [{"key": "poster"}, {"key": "barn"}],
    }

    class Client:
        async def is_ready(self):
            return True

    class Conn:
        config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        mcp_client = Client()

    monkeypatch.setattr(sd_pack_sync, "cached_asset_packs", lambda _config: [pack])

    async def fake_call(_conn, _client, _pack):
        return {
            "ready": True,
            "cacheKey": cache_key,
            "manifestChecksum": checksum,
            "downloadedCount": 1,
            "skippedCount": 0,
            "reusedCount": 1,
            "failedCount": 0,
        }

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)

    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())

    assert result["synced"] == 1
    assert result["failed"] == 0
    assert result["resultsByCacheKey"][cache_key]["reusedCount"] == 1
    assert result["resultsByCacheKey"][cache_key]["ready"] is True


@pytest.mark.asyncio
async def test_raw_mcp_dispatch_uses_physical_copy_and_preserves_render_pack(monkeypatch):
    checksum = "a" * 64
    cache_key = f"lesson-a/v1-{checksum}"
    pack = {
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "localRoot": f"sd://tbot/lesson-assets/{cache_key}",
        "assets": [
            {
                "key": "folder/poster one.png",
                "path": "assets/poster one.png",
                "url": "https://assets.example/poster.png",
                "onlineUrl": "https://assets.example/poster.png",
                "sha256": "b" * 64,
                "size": 100,
                "mediaType": "image/png",
                "critical": True,
                "sdPath": f"sd://tbot/lesson-assets/{cache_key}/folder%2Fposter%20one.png",
                "localPath": f"sd://tbot/lesson-assets/{cache_key}/folder%2Fposter%20one.png",
            }
        ],
    }
    original = json.loads(json.dumps(pack))
    calls = []

    class Conn:
        config = {
            "lesson": {
                "asset_pack_mount_root": "/opt/tbot-esp32-server/data/lesson-packs"
            }
        }

    async def capture(_conn, _client, tool, arguments, timeout):
        calls.append((tool, arguments, timeout))
        return {"ready": True}

    monkeypatch.setattr(
        "core.api.device_mcp_admin_handler._call_raw_mcp_tool", capture
    )
    await sd_pack_sync.call_sd_pack_sync_tool(Conn(), object(), pack)

    assert pack == original
    sent = calls[0][1]["assetPack"]
    assert sent["localRoot"] == f"/sdcard/tbot/lesson-assets/{cache_key}"
    assert sent["assets"][0]["sdPath"].endswith(
        "/folder%2Fposter%20one.png"
    )
    assert "localPath" not in sent["assets"][0]
    assert "url" not in sent["assets"][0]


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
    checksum = "f" * 64
    cache_key = f"lesson-a/v1-{checksum}"
    pack_dir = cache_root / cache_key
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
        return {
            "ready": True,
            "cacheKey": cache_key,
            "manifestChecksum": checksum,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 0,
        }

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
    assert calls == [cache_key]


@pytest.mark.asyncio
async def test_background_sync_does_not_cancel_dispatched_transfer_when_voice_turn_starts(
    monkeypatch, tmp_path
):
    cache_root = tmp_path / "lesson_assets"
    checksum = "f" * 64
    cache_key = f"lesson-a/v1-{checksum}"
    pack_dir = cache_root / cache_key
    pack_dir.mkdir(parents=True)
    (pack_dir / "poster").write_bytes(b"a")
    transfer_started = asyncio.Event()
    release_transfer = asyncio.Event()
    attempts = []
    busy = {"value": False}

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
        attempts.append(pack["cacheKey"])
        transfer_started.set()
        await release_transfer.wait()
        return {
            "ready": True,
            "cacheKey": cache_key,
            "manifestChecksum": checksum,
            "downloadedCount": 1,
            "skippedCount": 0,
            "failedCount": 0,
        }

    async def flip_busy():
        await transfer_started.wait()
        busy["value"] = True
        await asyncio.sleep(0)
        busy["value"] = False
        release_transfer.set()

    monkeypatch.setattr(sd_pack_sync, "call_sd_pack_sync_tool", fake_call)
    flipper = asyncio.create_task(flip_busy())
    result = await sd_pack_sync.sync_cached_lesson_assets_to_sd(
        Conn(), busy_check=lambda: busy["value"], poll_interval=0
    )
    await flipper

    assert result["synced"] == 1
    assert attempts == [cache_key]


@pytest.mark.asyncio
async def test_sd_sync_coordinator_prioritizes_foreground_after_active_transfer():
    conn = type("Conn", (), {})()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order = []

    async def operation(name, *, block=False):
        order.append(name)
        if block:
            first_started.set()
            await release_first.wait()
        return name

    first = asyncio.create_task(
        sd_pack_sync.request_sd_pack_sync(
            conn, "background-a", lambda: operation("background-a", block=True)
        )
    )
    await first_started.wait()
    second = asyncio.create_task(
        sd_pack_sync.request_sd_pack_sync(
            conn, "background-b", lambda: operation("background-b")
        )
    )
    foreground = asyncio.create_task(
        sd_pack_sync.request_sd_pack_sync(
            conn,
            "foreground",
            lambda: operation("foreground"),
            foreground=True,
        )
    )
    await asyncio.sleep(0)
    release_first.set()

    assert await asyncio.gather(first, second, foreground) == [
        "background-a",
        "background-b",
        "foreground",
    ]
    assert order == ["background-a", "foreground", "background-b"]


@pytest.mark.asyncio
async def test_sd_sync_coordinator_joins_same_cache_key():
    conn = type("Conn", (), {})()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"ready": True}

    first = asyncio.create_task(
        sd_pack_sync.request_sd_pack_sync(conn, "same-key", operation)
    )
    await started.wait()
    second = asyncio.create_task(
        sd_pack_sync.request_sd_pack_sync(conn, "same-key", operation)
    )
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(first, second) == [
        {"ready": True},
        {"ready": True},
    ]
    assert calls == 1


@pytest.mark.asyncio
async def test_cancelling_waiter_does_not_cancel_dispatched_sd_sync():
    conn = type("Conn", (), {})()
    started = asyncio.Event()
    finished = asyncio.Event()
    release = asyncio.Event()

    async def operation():
        started.set()
        await release.wait()
        finished.set()
        return "done"

    waiter = asyncio.create_task(
        sd_pack_sync.request_sd_pack_sync(conn, "cache-key", operation)
    )
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1)


def test_sd_pack_sync_timeout_scales_for_large_pack():
    pack = {
        "assets": [
            {"size": 13_703_853 // 28} for _ in range(27)
        ]
        + [{"size": 13_703_853 - (13_703_853 // 28) * 27}]
    }

    assert sd_pack_sync.sd_pack_sync_timeout_sec({}, pack) >= 1_000


def test_sd_pack_sync_timeout_accepts_configured_floor():
    config = {"lesson": {"sd_sync_timeout_floor_sec": 1_500}}

    assert sd_pack_sync.sd_pack_sync_timeout_sec(config, {"assets": []}) == 1_500


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
