import asyncio
import hashlib
import inspect
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

import pytest
from aiohttp import web

from core.lesson.sd_pack_materializer import (
    _METRICS,
    MaterializationError,
    _PinnedAddressAsyncNetworkBackend,
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
    def __init__(self, chunks, *, status=200, headers=None, on_chunk=None):
        self._chunks = list(chunks)
        self.status_code = status
        self.headers = dict(headers or {})
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
                maybe_wait = self._on_chunk()
                if inspect.isawaitable(maybe_wait):
                    await maybe_wait
            yield chunk
            await asyncio.sleep(0)


class _Client:
    def __init__(self, mapping, *, on_chunk=None):
        self.mapping = mapping
        self.requests = []
        self.request_headers = []
        self._on_chunk = on_chunk

    def stream(self, method, url, **kwargs):
        assert method == "GET"
        self.requests.append(url)
        self.request_headers.append(dict(kwargs.get("headers") or {}))
        value = self.mapping[url]
        if isinstance(value, _StreamResponse):
            return value
        return _StreamResponse(value, on_chunk=self._on_chunk)


async def _public_resolver(host):
    return ["93.184.216.34"]


async def _private_resolver(host):
    return ["10.0.0.7"]


def _sd_path(key):
    return f"/sdcard/tbot/lesson-assets/{CACHE_KEY}/{quote(key, safe='')}"


class _Connector:
    def __init__(self):
        self.calls = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.calls.append(
            {
                "host": host,
                "port": port,
                "timeout": timeout,
                "local_address": local_address,
                "socket_options": socket_options,
            }
        )
        return object()

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("unix sockets are not used for pinned lesson asset downloads")

    async def sleep(self, seconds):
        return None


class _Logger:
    def __init__(self):
        self.entries = []

    def bind(self, **kwargs):
        self.entries.append(("bind", kwargs))
        return self

    def warning(self, message):
        self.entries.append(("warning", message))

    def error(self, message):
        self.entries.append(("error", message))

    def info(self, message):
        self.entries.append(("info", message))


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
                "sdPath": _sd_path("backgroundScene.poster"),
            },
            {
                "key": "teachingObject.barn",
                "sha256": BARN_SHA,
                "size": len(BARN),
                "mediaType": "image/png",
                "critical": True,
                "url": "https://assets.example/barn.png",
                "localPath": _sd_path("teachingObject.barn"),
            },
        ],
    }
    manifest.update(overrides)
    return manifest


def _mp4_manifest():
    content = b"renderer-v3-replay"
    key = "scene.opening@v3"
    url = "https://assets.example/visuals/scene.opening/v3.mp4?variant=1#opening"
    return content, _manifest(assets=[{
        "key": key, "sha256": _sha(content), "size": len(content), "mediaType": "video/mp4",
        "critical": True, "onlineUrl": url, "url": url, "sdPath": _sd_path(key), "localPath": _sd_path(key),
        "sharedAssetKey": "scene.opening", "sharedAssetVersion": 3,
        "compatibilityMetadata": {
            "codec": "mjpeg", "fps": 10, "durationMs": 1000, "frameCount": 10,
            "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320}, "chromaKey": None,
        },
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"}],
    }])


def _flattened_mp4_manifest():
    content = b"renderer-v4-flattened"
    key = "flattenedCinematic.opening"
    url = "https://assets.example/lessons/derivatives/" + "d" * 64 + "/opening.mp4"
    return content, _manifest(assets=[{
        "key": key, "sha256": _sha(content), "size": len(content), "mediaType": "video/mp4",
        "critical": True, "onlineUrl": url, "url": url, "sdPath": _sd_path(key), "localPath": _sd_path(key),
        "derivativeId": "d" * 64, "phaseId": "opening",
        "compatibilityMetadata": {
            "codec": "mjpeg", "width": 480, "height": 320, "fps": 10,
            "durationMs": 1000, "frameCount": 10, "hasAudio": False,
        },
    }])


@pytest.mark.asyncio
async def test_renderer_v4_one_file_pack_materializes_reuses_and_never_activates_corrupt(tmp_path):
    content, manifest = _flattened_mp4_manifest()
    url = manifest["assets"][0]["onlineUrl"]
    client = _Client({url: [content]})

    first = await materialize_lesson_sd_pack(
        manifest, config=_config(tmp_path), client=client, resolver=_public_resolver,
    )
    second = await materialize_lesson_sd_pack(
        manifest, config=_config(tmp_path), client=client, resolver=_public_resolver,
    )

    assert first["downloadedCount"] == 1
    assert second["skippedCount"] == 1
    assert client.requests == [url]
    pack_path = tmp_path / "sd" / "tbot" / "lesson-assets" / CACHE_KEY / "pack.json"
    stored = json.loads(pack_path.read_text(encoding="utf-8"))
    assert stored["assets"][0]["derivativeId"] == "d" * 64
    assert stored["assets"][0]["phaseId"] == "opening"
    assert stored["assets"][0]["compatibilityMetadata"]["width"] == 480

    corrupt_root = tmp_path / "corrupt"
    corrupt_client = _Client({url: [content + b"corrupt"]})
    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            manifest, config=_config(corrupt_root), client=corrupt_client, resolver=_public_resolver,
        )
    assert exc_info.value.code in {"DECLARED_SIZE_MISMATCH", "CHECKSUM_MISMATCH"}
    assert not (corrupt_root / "sd" / "tbot" / "lesson-assets" / CACHE_KEY / "READY").exists()


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
        resolver=_public_resolver,
    )

    assert result == {
        "cacheKey": CACHE_KEY,
        "ready": True,
        "criticalReady": True,
        "optionalFailedCount": 0,
        "assetCount": 2,
        "downloadedCount": 2,
        "skippedCount": 0,
    }
    assert type(result) is dict
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
async def test_renderer_v3_public_mp4_preserves_exact_bytes_and_metadata_without_credentials(tmp_path):
    content = b"\x00\x00\x00\x18ftypmp42renderer-v3-mjpeg"
    key = "scene.opening@v3"
    url = "https://assets.example/visuals/scene.opening/v3.mp4"
    metadata = {
        "codec": "mjpeg", "fps": 10, "durationMs": 1000, "frameCount": 10,
        "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
        "chromaKey": None,
    }
    manifest = _manifest(assets=[{
        "key": key, "sha256": _sha(content), "size": len(content), "mediaType": "video/mp4",
        "critical": True, "onlineUrl": url, "url": url, "sdPath": _sd_path(key),
        "localPath": _sd_path(key), "sharedAssetKey": "scene.opening", "sharedAssetVersion": 3,
        "compatibilityMetadata": metadata,
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"}],
    }])
    client = _Client({url: [content[:8], content[8:]]})

    result = await materialize_lesson_sd_pack(
        manifest, config=_config(tmp_path), client=client, resolver=_public_resolver,
    )

    assert result["ready"] is True
    assert client.requests == [url]
    assert client.request_headers == [{}]
    store = SharedAssetStore(tmp_path / "sd" / "tbot", pack_root=tmp_path / "sd" / "tbot" / "lesson-assets")
    pack = store.pack_root / CACHE_KEY
    assert (pack / quote(key, safe="")).read_bytes() == content
    stored = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    assert stored["assets"][0]["compatibilityMetadata"] == metadata
    assert stored["assets"][0]["visualRefs"] == manifest["assets"][0]["visualRefs"]


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "?variant=robot&expires=2000000000#opening"])
async def test_renderer_v3_mp4_preserves_exact_public_url_with_optional_query_and_fragment(tmp_path, suffix):
    content = b"renderer-v3-public-url"
    key = "scene.opening@v3"
    url = "https://assets.example/visuals/scene.opening/v3.mp4" + suffix
    asset = {
        "key": key, "sha256": _sha(content), "size": len(content), "mediaType": "video/mp4",
        "critical": True, "onlineUrl": url, "url": url, "sdPath": _sd_path(key),
        "localPath": _sd_path(key), "sharedAssetKey": "scene.opening", "sharedAssetVersion": 3,
        "compatibilityMetadata": {
            "codec": "mjpeg", "fps": 10, "durationMs": 1000, "frameCount": 10,
            "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            "chromaKey": None,
        },
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"}],
    }

    await materialize_lesson_sd_pack(
        _manifest(assets=[asset]), config=_config(tmp_path), client=_Client({url: [content]}), resolver=_public_resolver,
    )

    pack = tmp_path / "sd" / "tbot" / "lesson-assets" / CACHE_KEY
    stored = json.loads((pack / "pack.json").read_text(encoding="utf-8"))
    assert stored["assets"][0]["onlineUrl"] == url


@pytest.mark.asyncio
async def test_renderer_v3_mp4_truncation_cleans_staging_and_allows_retry(tmp_path):
    content = b"renderer-v3-complete-mp4"
    key = "scene.opening@v3"
    url = "https://assets.example/visuals/scene.opening/v3.mp4"
    asset = {
        "key": key, "sha256": _sha(content), "size": len(content), "mediaType": "video/mp4",
        "critical": True, "onlineUrl": url, "url": url, "sdPath": _sd_path(key),
        "localPath": _sd_path(key), "sharedAssetKey": "scene.opening", "sharedAssetVersion": 3,
        "compatibilityMetadata": {
            "codec": "mjpeg", "fps": 10, "durationMs": 1000, "frameCount": 10,
            "hasAudio": False, "rect": {"x": 0, "y": 0, "width": 480, "height": 320},
            "chromaKey": None,
        },
        "visualRefs": [{"stepKey": "s1", "phase": "opening", "slot": "backgroundScene.opening"}],
    }
    manifest = _manifest(assets=[asset])

    with pytest.raises(MaterializationError) as truncated:
        await materialize_lesson_sd_pack(
            manifest, config=_config(tmp_path), client=_Client({url: [content[:-2]]}), resolver=_public_resolver,
        )

    assert truncated.value.code == "DECLARED_SIZE_MISMATCH"
    root = tmp_path / "sd" / "tbot" / "lesson-assets"
    encoded = quote(key, safe="")
    assert not (root / CACHE_KEY / encoded).exists()
    assert not list(root.rglob("*.part"))
    assert not list(root.rglob(".materialize-*"))

    result = await materialize_lesson_sd_pack(
        manifest, config=_config(tmp_path), client=_Client({url: [content]}), resolver=_public_resolver,
    )
    assert result["ready"] is True
    assert (root / CACHE_KEY / encoded).read_bytes() == content


@pytest.mark.asyncio
async def test_ready_replay_without_redownload(tmp_path):
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )
    await materialize_lesson_sd_pack(
        _manifest(),
        config=_config(tmp_path),
        client=client,
        resolver=_public_resolver,
    )
    replay = await materialize_lesson_sd_pack(
        _manifest(),
        config=_config(tmp_path),
        client=client,
        resolver=_public_resolver,
    )

    assert replay == {
        "cacheKey": CACHE_KEY,
        "ready": True,
        "criticalReady": True,
        "optionalFailedCount": 0,
        "assetCount": 2,
        "downloadedCount": 0,
        "skippedCount": 2,
    }
    assert type(replay) is dict
    assert client.requests == [
        "https://assets.example/poster.jpg?sig=secret",
        "https://assets.example/barn.png",
    ]


@pytest.mark.asyncio
async def test_ready_replay_accepts_rotated_online_url_without_redownload(tmp_path):
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )
    await materialize_lesson_sd_pack(
        _manifest(),
        config=_config(tmp_path),
        client=client,
        resolver=_public_resolver,
    )
    rotated = deepcopy(_manifest())
    rotated["assets"][0]["onlineUrl"] = "https://assets.example/poster.jpg?sig=rotated"

    replay = await materialize_lesson_sd_pack(
        rotated,
        config=_config(tmp_path),
        client=client,
        resolver=_public_resolver,
    )

    assert replay["downloadedCount"] == 0
    assert replay["skippedCount"] == 2
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
        await materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        )

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
        await materialize_lesson_sd_pack(
            bad,
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        )

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
        await materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        )
    assert per_file.value.code == "FILE_TOO_LARGE"

    monkeypatch.setenv("LESSON_SD_MAX_FILE_BYTES", "64")
    monkeypatch.setenv("LESSON_SD_MAX_PACK_BYTES", str(len(POSTER) + len(BARN) - 1))
    with pytest.raises(MaterializationError) as total:
        await materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        )
    assert total.value.code == "PACK_TOO_LARGE"


@pytest.mark.asyncio
async def test_non_https_url_in_production_and_disallowed_origin_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    non_https = _manifest()
    non_https["assets"][0]["onlineUrl"] = "http://assets.example/poster.jpg"
    with pytest.raises(MaterializationError) as scheme:
        await materialize_lesson_sd_pack(
            non_https,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert scheme.value.code == "NON_HTTPS_URL"

    monkeypatch.delenv("APP_ENV", raising=False)
    blocked = _manifest()
    blocked["assets"][0]["onlineUrl"] = "https://evil.example/poster.jpg"
    with pytest.raises(MaterializationError) as origin:
        await materialize_lesson_sd_pack(
            blocked,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert origin.value.code == "DISALLOWED_ORIGIN"


@pytest.mark.asyncio
async def test_strict_schema_alias_conflicts_and_unsafe_paths_rejected(tmp_path):
    extra = _manifest(extra="nope")
    with pytest.raises(MaterializationError) as unknown:
        await materialize_lesson_sd_pack(
            extra,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert unknown.value.code == "UNKNOWN_FIELD"

    conflict = _manifest()
    conflict["assets"][0]["url"] = "https://assets.example/other.jpg"
    with pytest.raises(MaterializationError) as alias:
        await materialize_lesson_sd_pack(
            conflict,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert alias.value.code == "ALIAS_CONFLICT"

    traversal = _manifest(cacheKey="../x/v1-" + MANIFEST_SHA)
    with pytest.raises(MaterializationError) as cache_key:
        await materialize_lesson_sd_pack(
            traversal,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert cache_key.value.code == "INVALID_CACHE_KEY"


@pytest.mark.asyncio
async def test_fat_case_insensitive_encoded_basename_collision_rejected(tmp_path):
    bad = _manifest()
    bad["assets"][1]["key"] = "backgroundscene.poster"
    bad["assets"][1]["sdPath"] = _sd_path("backgroundscene.poster")
    bad["assets"][1].pop("localPath")

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            bad,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )

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
        await materialize_lesson_sd_pack(
            _manifest(),
            config=config,
            client=client,
            resolver=_public_resolver,
        )

    assert exc_info.value.code == "STORAGE_ERROR"
    assert seen_before_ready == [False]
    root = tmp_path / "sd" / "tbot" / "lesson-assets"
    assert not SharedAssetStore(
        tmp_path / "sd" / "tbot",
        pack_root=root,
    ).is_pack_ready(CACHE_KEY)
    assert not list(root.rglob("*.staging"))
    assert not list(root.rglob("*.part"))


@pytest.mark.asyncio
async def test_literal_and_resolved_private_addresses_are_rejected(tmp_path, monkeypatch):
    private_literal = _manifest()
    private_literal["assets"][0]["onlineUrl"] = "https://127.0.0.1/poster.jpg"
    monkeypatch.setenv(
        "LESSON_ASSET_ALLOWED_ORIGINS",
        "https://assets.example,https://127.0.0.1",
    )

    with pytest.raises(MaterializationError) as literal:
        await materialize_lesson_sd_pack(
            private_literal,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert literal.value.code == "PRIVATE_ADDRESS"

    with pytest.raises(MaterializationError) as resolved:
        await materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_private_resolver,
        )
    assert resolved.value.code == "PRIVATE_ADDRESS"


@pytest.mark.asyncio
async def test_public_dns_resolution_is_required_before_streaming(tmp_path):
    seen = []

    async def resolver(host):
        seen.append(host)
        return ["93.184.216.34"]

    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )

    await materialize_lesson_sd_pack(
        _manifest(),
        config=_config(tmp_path),
        client=client,
        resolver=resolver,
    )

    assert seen == ["assets.example"]
    assert client.requests == [
        "https://assets.example/poster.jpg?sig=secret",
        "https://assets.example/barn.png",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://assets.example/poster.jpg#secret", "URL_FRAGMENT"),
        ("https://assets.example/assets/../poster.jpg", "UNSAFE_URL_PATH"),
        ("https://assets.example/assets/%2e%2e/poster.jpg", "UNSAFE_URL_PATH"),
        ("https://assets.example/assets/%252e%252e/poster.jpg", "UNSAFE_URL_PATH"),
        ("https://assets.example/assets/%5cposter.jpg", "UNSAFE_URL_PATH"),
        ("https://assets.example/assets%2fposter.jpg", "UNSAFE_URL_PATH"),
    ],
)
async def test_unsafe_url_fragments_and_paths_are_rejected(tmp_path, url, code):
    bad = _manifest()
    bad["assets"][0]["onlineUrl"] = url

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            bad,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )

    assert exc_info.value.code == code


@pytest.mark.asyncio
async def test_redirect_responses_are_rejected_without_following(tmp_path):
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": _StreamResponse(
                [],
                status=302,
                headers={"Location": "https://127.0.0.1/private"},
            ),
            "https://assets.example/barn.png": [BARN],
        }
    )

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        )

    assert exc_info.value.code == "REDIRECT_NOT_ALLOWED"
    assert client.requests == ["https://assets.example/poster.jpg?sig=secret"]


@pytest.mark.asyncio
async def test_wrong_sd_path_prefix_or_scheme_is_rejected(tmp_path):
    wrong_scheme = _manifest()
    wrong_scheme["assets"][0]["sdPath"] = f"sd://tbot/lesson-assets/{CACHE_KEY}/backgroundScene.poster"
    with pytest.raises(MaterializationError) as scheme:
        await materialize_lesson_sd_pack(
            wrong_scheme,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert scheme.value.code == "INVALID_SD_PATH"

    wrong_prefix = _manifest()
    wrong_prefix["assets"][0]["sdPath"] = f"/tmp/tbot/lesson-assets/{CACHE_KEY}/backgroundScene.poster"
    with pytest.raises(MaterializationError) as prefix:
        await materialize_lesson_sd_pack(
            wrong_prefix,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert prefix.value.code == "INVALID_SD_PATH"

    mismatch = _manifest()
    mismatch["assets"][0]["localPath"] = _sd_path("backgroundScene.poster.other")
    with pytest.raises(MaterializationError) as alias:
        await materialize_lesson_sd_pack(
            mismatch,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert alias.value.code == "ALIAS_CONFLICT"


@pytest.mark.asyncio
async def test_url_credentials_are_rejected_without_leaking_secret(tmp_path):
    bad = _manifest()
    bad["assets"][0]["onlineUrl"] = "https://user:private-token@assets.example/poster.jpg"

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            bad,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )

    assert exc_info.value.code == "INVALID_URL"
    assert "private-token" not in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "code", "secret"),
    [
        (lambda manifest: manifest.update({"unexpected": "private-token"}), "UNKNOWN_FIELD", "private-token"),
        (
            lambda manifest: manifest["assets"][0].update(
                {"sdPath": "sd://private-token/tbot/lesson-assets/bad"}
            ),
            "INVALID_SD_PATH",
            "private-token",
        ),
        (
            lambda manifest: manifest["assets"][0].update(
                {"onlineUrl": "https://evil.example/poster.jpg?sig=private-token"}
            ),
            "DISALLOWED_ORIGIN",
            "private-token",
        ),
    ],
)
async def test_early_rejections_are_counted_once_and_logged_safely(
    tmp_path,
    mutate,
    code,
    secret,
):
    before = dict(_METRICS)
    logger = _Logger()
    manifest = _manifest()
    mutate(manifest)

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            manifest,
            config=_config(tmp_path),
            client=_Client({}),
            logger=logger,
            resolver=_public_resolver,
        )

    assert exc_info.value.code == code
    assert _METRICS["rejected"] == before["rejected"] + 1
    assert _METRICS["checksum_failures"] == before["checksum_failures"]
    binds = [entry for kind, entry in logger.entries if kind == "bind"]
    assert binds[-1]["cacheKey"] == CACHE_KEY
    assert binds[-1]["result"] == "rejected"
    assert binds[-1]["errorCode"] == code
    joined = json.dumps(logger.entries, sort_keys=True)
    assert secret not in joined
    assert "sig=" not in joined
    assert "https://evil.example" not in joined


@pytest.mark.asyncio
async def test_private_dns_rejection_is_counted_once_and_does_not_log_private_ip(tmp_path):
    before = dict(_METRICS)
    logger = _Logger()

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=_Client({}),
            logger=logger,
            resolver=_private_resolver,
        )

    assert exc_info.value.code == "PRIVATE_ADDRESS"
    assert _METRICS["rejected"] == before["rejected"] + 1
    assert _METRICS["checksum_failures"] == before["checksum_failures"]
    binds = [entry for kind, entry in logger.entries if kind == "bind"]
    assert binds[-1]["cacheKey"] == CACHE_KEY
    assert binds[-1]["result"] == "rejected"
    assert binds[-1]["errorCode"] == "PRIVATE_ADDRESS"
    joined = json.dumps(logger.entries, sort_keys=True)
    assert "10.0.0.7" not in joined


@pytest.mark.asyncio
async def test_pinned_backend_connects_to_attested_ip_not_hostname():
    connector = _Connector()
    backend = _PinnedAddressAsyncNetworkBackend(
        {"assets.example": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]},
        delegate=connector,
    )

    await backend.connect_tcp("assets.example", 443, timeout=3.0)
    await backend.connect_tcp("assets.example", 443, timeout=3.0)

    assert [call["host"] for call in connector.calls] == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]


@pytest.mark.asyncio
async def test_encoded_asset_basename_length_boundary(tmp_path):
    key = "a" * 200
    manifest = _manifest()
    manifest["assets"] = [
        {
            "key": key,
            "sha256": POSTER_SHA,
            "size": len(POSTER),
            "mediaType": "image/jpeg",
            "critical": True,
            "onlineUrl": "https://assets.example/poster.jpg?sig=secret",
            "sdPath": _sd_path(key),
        }
    ]
    result = await materialize_lesson_sd_pack(
        manifest,
        config=_config(tmp_path),
        client=_Client({"https://assets.example/poster.jpg?sig=secret": [POSTER]}),
        resolver=_public_resolver,
    )
    assert result["ready"] is True

    one_over = deepcopy(manifest)
    one_over["assets"][0]["key"] = "a" * 201
    one_over["assets"][0]["sdPath"] = _sd_path("a" * 201)
    with pytest.raises(MaterializationError) as ascii_over:
        await materialize_lesson_sd_pack(
            one_over,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert ascii_over.value.code == "INVALID_ASSET_KEY"

    expanded = deepcopy(manifest)
    expanded["assets"][0]["key"] = "é" * 34
    expanded["assets"][0]["sdPath"] = _sd_path("é" * 34)
    with pytest.raises(MaterializationError) as unicode_over:
        await materialize_lesson_sd_pack(
            expanded,
            config=_config(tmp_path),
            client=_Client({}),
            resolver=_public_resolver,
        )
    assert unicode_over.value.code == "INVALID_ASSET_KEY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest: manifest["assets"][0].update({"size": len(POSTER) + 1}),
        lambda manifest: manifest["assets"][0].update({"sha256": "b" * 64}),
        lambda manifest: manifest["assets"][0].update({"mediaType": "image/webp"}),
        lambda manifest: manifest["assets"][0].update({"critical": False}),
        lambda manifest: manifest["assets"][0].update({"key": "backgroundScene.changed"}),
    ],
)
async def test_replay_rejects_rich_manifest_metadata_mismatch_without_download(tmp_path, mutate):
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )
    await materialize_lesson_sd_pack(
        _manifest(),
        config=_config(tmp_path),
        client=client,
        resolver=_public_resolver,
    )
    before_requests = list(client.requests)
    changed = deepcopy(_manifest())
    mutate(changed)
    if changed["assets"][0]["key"] == "backgroundScene.changed":
        changed["assets"][0]["sdPath"] = _sd_path("backgroundScene.changed")

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            changed,
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        )

    assert exc_info.value.code == "PACK_REPLAY_MISMATCH"
    assert client.requests == before_requests


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [
    lambda asset: asset.update(onlineUrl=asset["onlineUrl"].replace("variant=1", "variant=2"), url=asset["url"].replace("variant=1", "variant=2")),
    lambda asset: asset["compatibilityMetadata"].update(durationMs=2000, frameCount=20),
    lambda asset: asset.update(visualRefs=[{"stepKey": "s1", "phase": "greet", "slot": "backgroundScene.greet"}]),
])
async def test_renderer_v3_replay_rejects_changed_rich_identity(tmp_path, mutate):
    content, manifest = _mp4_manifest()
    url = manifest["assets"][0]["onlineUrl"]
    client = _Client({url: [content]})
    await materialize_lesson_sd_pack(manifest, config=_config(tmp_path), client=client, resolver=_public_resolver)
    changed = deepcopy(manifest)
    mutate(changed["assets"][0])

    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(changed, config=_config(tmp_path), client=client, resolver=_public_resolver)

    assert exc_info.value.code == "PACK_REPLAY_MISMATCH"


@pytest.mark.asyncio
async def test_historical_digest_manifest_is_not_replayed_as_rich_pack(tmp_path):
    store = SharedAssetStore(
        tmp_path / "sd" / "tbot",
        pack_root=tmp_path / "sd" / "tbot" / "lesson-assets",
    )
    store.put_bytes(POSTER, POSTER_SHA)
    store.put_bytes(BARN, BARN_SHA)
    store.commit_pack(
        CACHE_KEY,
        {
            "backgroundScene.poster": POSTER_SHA,
            "teachingObject.barn": BARN_SHA,
        },
    )
    assert store.is_pack_ready(CACHE_KEY)
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        }
    )

    result = await materialize_lesson_sd_pack(
        _manifest(),
        config=_config(tmp_path),
        client=client,
        resolver=_public_resolver,
    )

    assert result["downloadedCount"] == 2
    assert client.requests == [
        "https://assets.example/poster.jpg?sig=secret",
        "https://assets.example/barn.png",
    ]
    rich = json.loads(
        (tmp_path / "sd" / "tbot" / "lesson-assets" / CACHE_KEY / "pack.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(rich["assets"], list)


@pytest.mark.asyncio
async def test_concurrent_identical_materializations_yield_one_accept_and_one_locked_replay(tmp_path):
    entered = 0
    release = asyncio.Event()

    async def barrier():
        nonlocal entered
        entered += 1
        if entered == 2:
            release.set()
        await release.wait()

    before = dict(_METRICS)
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/barn.png": [BARN],
        },
        on_chunk=barrier,
    )

    results = await asyncio.gather(
        materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        ),
        materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        ),
    )

    assert sorted(result["downloadedCount"] for result in results) == [0, 2]
    assert sorted(result["skippedCount"] for result in results) == [0, 2]
    assert _METRICS["accepted"] == before["accepted"] + 1
    assert _METRICS["replayed"] == before["replayed"] + 1


@pytest.mark.asyncio
async def test_concurrent_conflicting_materializations_keep_first_ready_pack(tmp_path):
    entered = 0
    release = asyncio.Event()
    changed = deepcopy(_manifest())
    changed["assets"][0]["sha256"] = _sha(b"changed")
    changed["assets"][0]["size"] = len(b"changed")
    changed["assets"][0]["onlineUrl"] = "https://assets.example/changed.jpg"

    async def barrier():
        nonlocal entered
        entered += 1
        if entered == 2:
            release.set()
        await release.wait()

    before = dict(_METRICS)
    client = _Client(
        {
            "https://assets.example/poster.jpg?sig=secret": [POSTER],
            "https://assets.example/changed.jpg": [b"changed"],
            "https://assets.example/barn.png": [BARN],
        },
        on_chunk=barrier,
    )

    results = await asyncio.gather(
        materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        ),
        materialize_lesson_sd_pack(
            changed,
            config=_config(tmp_path),
            client=client,
            resolver=_public_resolver,
        ),
        return_exceptions=True,
    )

    accepted = [result for result in results if isinstance(result, dict)]
    rejected = [result for result in results if isinstance(result, MaterializationError)]
    assert len(accepted) == 1
    assert accepted[0]["downloadedCount"] == 2
    assert len(rejected) == 1
    assert rejected[0].code == "PACK_REPLAY_MISMATCH"
    assert _METRICS["accepted"] == before["accepted"] + 1
    assert _METRICS["rejected"] == before["rejected"] + 1
    pack = tmp_path / "sd" / "tbot" / "lesson-assets" / CACHE_KEY / "pack.json"
    stored = json.loads(pack.read_text(encoding="utf-8"))
    assert stored["assets"][0]["onlineUrl"] in {
        "https://assets.example/poster.jpg?sig=secret",
        "https://assets.example/changed.jpg",
    }


@pytest.mark.asyncio
async def test_historical_digest_manifest_incompatible_rejects_without_upgrade(tmp_path):
    store = SharedAssetStore(
        tmp_path / "sd" / "tbot",
        pack_root=tmp_path / "sd" / "tbot" / "lesson-assets",
    )
    wrong = _sha(b"wrong")
    store.put_bytes(b"wrong", wrong)
    store.put_bytes(BARN, BARN_SHA)
    store.commit_pack(
        CACHE_KEY,
        {
            "backgroundScene.poster": wrong,
            "teachingObject.barn": BARN_SHA,
        },
    )
    with pytest.raises(MaterializationError) as exc_info:
        await materialize_lesson_sd_pack(
            _manifest(),
            config=_config(tmp_path),
            client=_Client(
                {
                    "https://assets.example/poster.jpg?sig=secret": [POSTER],
                    "https://assets.example/barn.png": [BARN],
                }
            ),
            resolver=_public_resolver,
        )

    assert exc_info.value.code == "PACK_REPLAY_MISMATCH"
    manifest = json.loads(
        (tmp_path / "sd" / "tbot" / "lesson-assets" / CACHE_KEY / "pack.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(manifest["assets"], dict)


def test_handler_route_is_registered():
    source = Path("core/http_server.py").read_text(encoding="utf-8")
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


@pytest.mark.asyncio
async def test_materialize_handler_preserves_request_entity_too_large(monkeypatch):
    from core.api.lesson_sd_materialize_handler import LessonSdMaterializeHandler

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    handler = LessonSdMaterializeHandler({}, {})

    response = await handler.handle_post(
        _Request(json_error=web.HTTPRequestEntityTooLarge(max_size=10, actual_size=11))
    )

    assert response.status == 413
    assert _json_response(response)["error"] == "REQUEST_ENTITY_TOO_LARGE"


@pytest.mark.asyncio
async def test_materialize_handler_malformed_json_returns_400(monkeypatch):
    from core.api.lesson_sd_materialize_handler import LessonSdMaterializeHandler

    monkeypatch.setenv("TBOT_DEVICE_MINT_SECRET", "secret")
    handler = LessonSdMaterializeHandler({}, {})

    response = await handler.handle_post(_Request(json_error=ValueError("private-token")))

    assert response.status == 400
    assert _json_response(response)["error"] == "INVALID_REQUEST"
    assert "private-token" not in response.text
