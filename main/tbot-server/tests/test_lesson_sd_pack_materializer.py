import asyncio
import hashlib
import json
import os
from urllib.parse import quote
from unittest.mock import AsyncMock, patch

import pytest

from core.lesson.sd_pack_materializer import (
    MaterializationError,
    materialize_lesson_sd_pack,
)
from core.lesson.shared_asset_store import SharedAssetStore


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


POSTER = b"poster-bytes"
BARN = b"barn-bytes"
POSTER_SHA = _sha(POSTER)
BARN_SHA = _sha(BARN)
MANIFEST_SHA = "a" * 64
CACHE_KEY = f"pip-farm-3m/v7-{MANIFEST_SHA}"


class _StreamResponse:
    def __init__(self, chunks, *, status=200, on_chunk=None):
        self._chunks = list(chunks)
        self.status_code = status
        self._on_chunk = on_chunk

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size=65536):
        for chunk in self._chunks:
            if self._on_chunk is not None:
                self._on_chunk()
            yield chunk
            await asyncio.sleep(0)


class _Client:
    def __init__(self, mapping, *, on_chunk=None):
        self.mapping = mapping
        self.requests = []
        self._on_chunk = on_chunk

    def stream(self, method, url):
        assert method == "GET"
        self.requests.append(url)
        return _StreamResponse(self.mapping[url], on_chunk=self._on_chunk)


def _manifest(**overrides):
    manifest = {
        "lessonId": "pip-farm-3m",
        "lessonVersion": 7,
        "profile": "espTft",
        "manifestChecksum": MANIFEST_SHA,
        "cacheKey": CACHE_KEY,
        "assets": [
            {
                "key": "backgroundScene.poster",
                "sha256": POSTER_SHA,
                "size": len(POSTER),
                "mediaType": "image/jpeg",
                "critical": True,
                "onlineUrl": "https://assets.example/poster.jpg?sig=secret",
                "sdPath": f"sd://tbot/lesson-assets/{CACHE_KEY}/{quote('backgroundScene.poster', safe='')}",
            },
            {
                "key": "teachingObject.barn",
                "sha256": BARN_SHA,
                "size": len(BARN),
                "mediaType": "image/png",
                "critical": True,
                "url": "https://assets.example/barn.png",
                "localPath": f"sd://tbot/lesson-assets/{CACHE_KEY}/{quote('teachingObject.barn', safe='')}",
            },
        ],
    }
    manifest.update(overrides)
    return manifest


def _config(tmp_path, **lesson):
    cfg = {
        "lesson": {
            "asset_pack_mount_root": str(tmp_path / "sd" / "tbot" / "lesson-assets"),
        }
    }
    cfg["lesson"].update(lesson)
    return cfg


@pytest.fixture(autouse=True)
def _limits_and_allowlist(monkeypatch):
    monkeypatch.setenv("LESSON_ASSET_ALLOWED_ORIGINS", "https://assets.example")
    monkeypatch.setenv("LESSON_SD_MAX_FILE_BYTES", "64")
    monkeypatch.setenv("LESSON_SD_MAX_PACK_BYTES", "128")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("NODE_ENV", raising=False)
    monkeypatch.delenv("TBOT_ENV", raising=False)


@pytest.mark.asyncio
async def test_valid_bounded_stream_materializes_atomic_ready_pack(tmp_path):
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER[:3], POSTER[3:]],
            "https://assets.example/barn.png": [BARN],
        }
    )

    result = await materialize_lesson_sd_pack(
        _manifest(),
        config=_config(tmp_path),
        client=client,
    )

    assert result == {
        "cacheKey": CACHE_KEY,
        "ready": True,
        "assetCount": 2,
        "downloadedCount": 2,
        "skippedCount": 0,
    }
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=tmp_path / "sd" / "tbot" / "lesson-assets")
    assert store.is_pack_ready(CACHE_KEY)
    pack = tmp_path / "sd" / "tbot" / "lesson-assets" / CACHE_KEY
    manifest = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    assert manifest["manifestChecksum"] == MANIFEST_SHA
    assert {asset["key"] for asset in manifest["assets"]} == {
        "backgroundScene.poster",
        "teachingObject.barn",
    }
    assert _sha((pack / "backgroundScene.poster").read_bytes()) == POSTER_SHA
    assert "sig=secret" in manifest["assets"][0]["onlineUrl"]


@pytest.mark.asyncio
async def test_ready_replay_without_redownload(tmp_path):
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )
    await materialize_lesson_sd_pack(_manifest(), config=_config(tmp_path), client=client)
    replay = await materialize_lesson_sd_pack(_manifest(), config=_config(tmp_path), client=client)

    assert replay == {
        "cacheKey": CACHE_KEY,
        "ready": True,
        "assetCount": 2,
        "downloadedCount": 0,
        "skippedCount": 2,
    }
    assert client.requests == [
        "https://assets.example/poster.jpg?sig=secret",
        "https://assets.example/barn.png",
    ]


@pytest.mark.asyncio
async def test_checksum_mismatch_is_terminal_and_sanitized(tmp_path):
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [b"x" * len(POSTER)],
            "https://assets.example/barn.png": [BARN],
        }
    )

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(_manifest(), config=_config(tmp_path), client=client)

    exc = exc_info.value
    assert exc.code == "CHECKSUM_MISMATCH"
    assert exc.status == 400
    assert exc.retryable is False
    assert "sig=secret" not in str(exc)
    assert not SharedAssetStore(
        tmp_path / "sd" / "tbot",
        pack_root=tmp_path / "sd" / "tbot" / "lesson-assets",
    ).is_pack_ready(CACHE_KEY)


@pytest.mark.asyncio
async def test_declared_size_mismatch_rejected(tmp_path):
    bad = _manifest()
    bad["assets"][0]["size"] = len(POSTER) + 1
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(bad, config=_config(tmp_path), client=client)

    assert exc_info.value.code == "DECLARED_SIZE_MISMATCH"


@pytest.mark.asyncio
async def test_per_file_and_total_pack_byte_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("LESSON_SD_MAX_FILE_BYTES", str(len(POSTER) - 1))
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )
    with pytest.raises(MaterializationError) as per_file:
        await materialize_lesson_sd_pack(_manifest(), config=_config(tmp_path), client=client)
    assert per_file.value.code == "FILE_TOO_LARGE"

    monkeypatch.setenv("LESSON_SD_MAX_FILE_BYTES", "64")
    monkeypatch.setenv("LESSON_SD_MAX_PACK_BYTES", str(len(POSTER) + len(BARN) - 1))
    with pytest.raises(MaterializationError) as total:
        await materialize_lesson_sd_pack(_manifest(), config=_config(tmp_path), client=client)
    assert total.value.code == "PACK_TOO_LARGE"


@pytest.mark.asyncio
async def test_non_https_url_in_production_and_disallowed_origin_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    non_https = _manifest()
    non_https["assets"][0]["onlineUrl"] = "http://assets.example/poster.jpg"
    with pytest.raises(MaterializationError) as scheme:
        await materialize_lesson_sd_pack(non_https, config=_config(tmp_path), client=_Client({}))
    assert scheme.value.code == "NON_HTTPS_URL"

    monkeypatch.delenv("APP_ENV", raising=False)
    blocked = _manifest()
    blocked["assets"][0]["onlineUrl"] = "https://evil.example/poster.jpg"
    with pytest.raises(MaterializationError) as origin:
        await materialize_lesson_sd_pack(blocked, config=_config(tmp_path), client=_Client({}))
    assert origin.value.code == "DISALLOWED_ORIGIN"


@pytest.mark.asyncio
async def test_strict_schema_alias_conflicts_and_unsafe_paths_rejected(tmp_path):
    extra = _manifest(extra="nope")
    with pytest.raises(MaterializationError) as unknown:
        await materialize_lesson_sd_pack(extra, config=_config(tmp_path), client=_Client({}))
    assert unknown.value.code == "UNKNOWN_FIELD"

    conflict = _manifest()
    conflict["assets"][0]["url"] = "https://assets.example/other.jpg"
    with pytest.raises(MaterializationError) as alias:
        await materialize_lesson_sd_pack(conflict, config=_config(tmp_path), client=_Client({}))
    assert alias.value.code == "ALIAS_CONFLICT"

    traversal = _manifest(cacheKey="../x/v1-" + MANIFEST_SHA)
    with pytest.raises(MaterializationError) as cache_key:
        await materialize_lesson_sd_pack(traversal, config=_config(tmp_path), client=_Client({}))
    assert cache_key.value.code == "INVALID_CACHE_KEY"


@pytest.mark.asyncio
async def test_fat_case_insensitive_encoded_basename_collision_rejected(tmp_path):
    bad = _manifest()
    bad["assets"][1]["key"] = "backgroundscene.poster"
    bad["assets"][1]["sdPath"] = f"sd://tbot/lesson-assets/{CACHE_KEY}/backgroundscene.poster"
    bad["assets"][1].pop("localPath")

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(bad, config=_config(tmp_path), client=_Client({}))

    assert exc_info.value.code == "BASENAME_COLLISION"


@pytest.mark.asyncio
async def test_interrupted_staging_cleanup_and_no_visibility_before_ready(tmp_path):
    seen_before_ready = []

    def observe(stage, path):
        if stage == "after_replace" and path.name == "pack.json":
            public_ready = tmp_path / "sd" / "tbot" / "lesson-assets" / CACHE_KEY / "READY"
            seen_before_ready.append(public_ready.exists())
        if stage == "before_replace" and path.name == "READY":
            raise RuntimeError("power loss")

    config = _config(tmp_path)
    config["lesson"]["shared_asset_store_failure_hook"] = observe
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(_manifest(), config=config, client=client)

    assert exc_info.value.code == "STORAGE_ERROR"
    assert seen_before_ready == [False]
    root = tmp_path / "sd" / "tbot" / "lesson-assets"
    assert not SharedAssetStore(
        tmp_path / "sd" / "tbot",
        pack_root=root,
    ).is_pack_ready(CACHE_KEY)
    assert not list(root.rglob("*.staging"))
    assert not list(root.rglob("*.part"))


def test_handler_route_is_registered():
    source = open("core/http_server.py", encoding="utf-8").read()
    assert "lesson_sd_materialize_handler" in source
    assert '"/internal/lesson-assets/materialize"' in source


class _Request:
    def __init__(self, *, secret="secret", body=None, json_error=None):
        self.headers = {}
        if secret is not None:
            self.headers["X-Mint-Secret"] = secret
        self._body = _manifest() if body is None else body
        self._json_error = json_error

    async def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._body


def _json_response(response):
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_materialize_handler_auth_and_status_mapping(monkeypatch):
    from core.api.lesson_sd_materialize_handler import LessonSdMaterializeHandler

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    handler = LessonSdMaterializeHandler({}, {})
    accepted = {
        "cacheKey": CACHE_KEY,
        "ready": True,
        "assetCount": 2,
        "downloadedCount": 2,
        "skippedCount": 0,
    }
    replayed = {**accepted, "downloadedCount": 0, "skippedCount": 2}

    with patch(
        "core.api.lesson_sd_materialize_handler.materialize_lesson_sd_pack",
        new=AsyncMock(return_value=accepted),
    ) as service:
        created = await handler.handle_post(_Request())
    with patch(
        "core.api.lesson_sd_materialize_handler.materialize_lesson_sd_pack",
        new=AsyncMock(return_value=replayed),
    ):
        replay = await handler.handle_post(_Request())
    with patch(
        "core.api.lesson_sd_materialize_handler.materialize_lesson_sd_pack",
        new=AsyncMock(return_value=accepted),
    ) as unauthorized_service:
        unauthorized = await handler.handle_post(_Request(secret="wrong"))

    assert created.status == 201
    assert _json_response(created) == {"data": accepted}
    assert replay.status == 200
    assert _json_response(replay) == {"data": replayed}
    assert unauthorized.status == 401
    assert service.await_args.kwargs["config"] == {}
    unauthorized_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_materialize_handler_returns_sanitized_materialization_errors(monkeypatch):
    from core.api.lesson_sd_materialize_handler import LessonSdMaterializeHandler

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    handler = LessonSdMaterializeHandler({}, {})
    error = MaterializationError(
        "DISALLOWED_ORIGIN",
        400,
        False,
        "Asset URL origin is not allowed",
        {"assetKey": "backgroundScene.poster"},
    )

    with patch(
        "core.api.lesson_sd_materialize_handler.materialize_lesson_sd_pack",
        new=AsyncMock(side_effect=error),
    ):
        response = await handler.handle_post(_Request())

    assert response.status == 400
    assert _json_response(response) == {
        "error": "DISALLOWED_ORIGIN",
        "message": "Asset URL origin is not allowed",
        "retryable": False,
        "details": {"assetKey": "backgroundScene.poster"},
    }
