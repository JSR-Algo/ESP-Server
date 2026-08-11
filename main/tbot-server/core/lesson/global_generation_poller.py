"""Poll and validate the public CMS lesson asset generation index."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import ipaddress
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

import httpx

from config.logger import setup_logging
from core.lesson.flattened_cinematic_contract import (
    TRGB_MEDIA_TYPE,
    FlattenedCinematicContractError,
    validate_flattened_cinematic_cache_asset,
)
from core.lesson.cache_key_contract import (
    CACHE_KEY_MAX_BYTES,
    MAX_ENCODED_BASENAME_BYTES as CONTRACT_MAX_ENCODED_BASENAME_BYTES,
    AssetBasenameRefused,
    CacheEvictionRefused,
    compose_cache_key,
    encode_asset_basename,
)
from core.lesson.layered_cinematic_contract import (
    LayeredCinematicContractError,
    validate_layered_cinematic_generation_asset,
)
from core.lesson.sd_pack_mcp_payload import (
    FirmwareSyncPackError,
    validate_renderer_v3_shared_mp4,
    validate_renderer_v4_flattened_mp4,
)

POLL_INTERVAL_SECONDS = 30.0
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 2
MAX_SAFE_INTEGER = (1 << 53) - 1
MAX_ASSETS = 64
# T5.1: both limits now come from THE contract module. They used to be local
# copies, and MAX_CACHE_KEY_BYTES was 200 while the canonical cap is 205 — a
# maximal-but-legal key was rejected here as cms_cache_key_too_long while the
# materializer accepted it.
MAX_ENCODED_BASENAME_BYTES = CONTRACT_MAX_ENCODED_BASENAME_BYTES
MAX_CACHE_KEY_BYTES = CACHE_KEY_MAX_BYTES

_ENVELOPE_FIELDS = frozenset({"data"})
_DATA_FIELDS = frozenset(
    {
        "generation",
        "publishedAt",
        "indexChecksum",
        "curriculumLessonCount",
        "packCount",
        "index",
    }
)
_PACK_FIELDS = frozenset(
    {
        "lessonId",
        "lessonVersion",
        "profile",
        "manifestChecksum",
        "cacheKey",
        "classification",
        "assets",
    }
)
_ASSET_FIELDS = frozenset(
    {
        "key",
        "sdPath",
        "localPath",
        "onlineUrl",
        "url",
        "sha256",
        "size",
        "mediaType",
        "critical",
    }
)
_SHARED_IDENTITY_FIELDS = frozenset({"sharedAssetKey", "sharedAssetVersion"})
_RENDERER_V3_MP4_FIELDS = frozenset({"compatibilityMetadata", "visualRefs"})
_RENDERER_V4_V1_MP4_FIELDS = frozenset({"compatibilityMetadata", "derivativeId", "phaseId"})
_RENDERER_V4_V2_MP4_FIELDS = frozenset(
    {"compatibilityMetadata", "derivativeId", "cueId", "effect", "stepKey", "playbackMode"}
)
_RENDERER_V4_V2_TRGB_FIELDS = _RENDERER_V4_V2_MP4_FIELDS
_RENDERER_V4_MP4_FIELDS = _RENDERER_V4_V1_MP4_FIELDS | _RENDERER_V4_V2_MP4_FIELDS
_ALL_ASSET_FIELDS = (
    _ASSET_FIELDS | _SHARED_IDENTITY_FIELDS | _RENDERER_V3_MP4_FIELDS | _RENDERER_V4_MP4_FIELDS
)
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_LESSON_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RFC3339_UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|\+00:00)$"
)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_FAT_FORBIDDEN_RE = re.compile(r"[<>:\"/\\|?*\x00-\x1f]")
_FAT_DEVICE_RE = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)")
_RESERVED_BASENAMES = frozenset(
    {".", "..", "current.json", "pvg.json", "activation.json", "lesson-pack-activation.json"}
)
_RESERVED_SUFFIXES = (".tmp", ".download", ".part", ".backup")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_TRGB_PROXY_ORIGIN = ("https", "admin.tjbot.vn", 443)
_TRGB_PROXY_PATH_PREFIX = "/lesson-derivatives/lessons/derivatives/"
_RENDERER_DERIVATIVE_PATH_PREFIX = "lessons/derivatives/"


class _PollRejected(ValueError):  # noqa: N818 - internal control-flow rejection
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_json(index: Any) -> bytes:
    return json.dumps(
        index,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


class GlobalGenerationPoller:
    def __init__(
        self,
        config: Mapping[str, Any],
        store: Any,
        on_generation: Callable[[dict[str, Any]], Awaitable[Any] | Any],
        *,
        http: Any = None,
        clock: Callable[[], Any] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.store = store
        self.on_generation = on_generation
        self.clock = clock
        self.sleep = sleep
        self.cms_url = _cms_url(config)
        self.cms_origin = _origin(self.cms_url, allowed_schemes={"http", "https"})
        self.allowed_origins = _allowed_origins(config)
        self.poll_interval = _poll_interval(config)
        self.log = setup_logging()
        self._etag: str | None = None
        self._payload: dict[str, Any] | None = None
        self._task: asyncio.Task[None] | None = None
        self._retry_task: asyncio.Task[None] | None = None
        self._run_lock = asyncio.Lock()
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=3.0),
            follow_redirects=False,
            trust_env=False,
        )

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        retry_task, self._retry_task = self._retry_task, None
        if task is not None and not task.done():
            task.cancel()
        if retry_task is not None and not retry_task.done():
            retry_task.cancel()
        if task is not None:
            with suppress(asyncio.CancelledError):
                await task
        if retry_task is not None:
            with suppress(asyncio.CancelledError):
                await retry_task
        if self._owns_http:
            await self.http.aclose()

    async def trigger_retry(self) -> dict[str, str]:
        task = self._retry_task
        if task is not None and not task.done():
            return {"state": "not_modified"}
        self._retry_task = asyncio.create_task(self._run_triggered_retry())
        return {"state": "accepted"}

    async def _run_triggered_retry(self) -> None:
        current = asyncio.current_task()
        try:
            await self.run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log("warning", "generation_retry_failed")
        finally:
            if self._retry_task is current:
                self._retry_task = None

    async def run_once(self) -> dict[str, str]:
        async with self._run_lock:
            return await self._run_once_locked()

    async def _run_once_locked(self) -> dict[str, str]:
        try:
            response = await self._request()
            if response["status"] == 304:
                if self._payload is None:
                    raise _PollRejected("cms_cold_304")
                if await self._durable_acceptance_matches(self._payload):
                    return {"state": "not_modified"}
                return await self._apply_generation(self._payload, self._etag)
            payload = _parse_json(response["body"])
            data = _validate_payload(payload, self.allowed_origins)
            expected_etag = (
                f'"lesson-assets-g{data["generation"]}-{data["indexChecksum"]}"'
            )
            if response["etag"] not in {expected_etag, f"W/{expected_etag}"}:
                raise _PollRejected("cms_etag_mismatch")
            return await self._apply_generation(data, expected_etag)
        except asyncio.CancelledError:
            raise
        except _PollRejected as exc:
            self._log("warning", exc.code)
            return {"state": "rejected", "errorCode": exc.code}
        except Exception:
            self._log("warning", "cms_request_failed")
            return {"state": "rejected", "errorCode": "cms_request_failed"}
        finally:
            try:
                await self.store.mark_polled(_utc_timestamp(self.clock()))
            except Exception:
                self._log("warning", "cms_poll_timestamp_failed")

    async def _durable_acceptance_matches(self, data: Mapping[str, Any]) -> bool:
        try:
            snapshot = await self.store.snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _PollRejected("generation_store_failed") from None
        return (
            isinstance(snapshot, Mapping)
            and type(snapshot.get("acceptedGeneration")) is int
            and snapshot.get("acceptedGeneration") == data["generation"]
            and snapshot.get("acceptedIndexChecksum") == data["indexChecksum"]
            and snapshot.get("materializationState") == "ready"
        )

    async def _apply_generation(
        self, data: dict[str, Any], expected_etag: str | None
    ) -> dict[str, str]:
        if expected_etag is None:
            raise _PollRejected("cms_etag_mismatch")
        try:
            await self.store.set_desired(
                data["generation"], data["indexChecksum"], expected_etag
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _PollRejected("generation_store_failed") from None
        callback_retry = False
        try:
            callback_result = self.on_generation(data)
            if inspect.isawaitable(callback_result):
                callback_result = await callback_result
            callback_retry = (
                isinstance(callback_result, Mapping)
                and "state" in callback_result
                and callback_result.get("state") != "ready"
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise _PollRejected("generation_callback_failed") from None
        if callback_retry:
            raise _PollRejected("generation_callback_retry")
        self._etag = expected_etag
        self._payload = data
        self._log("info", "accepted")
        return {"state": "accepted"}

    async def _run_loop(self) -> None:
        attempt = 0
        while True:
            result = await self.run_once()
            if result["state"] in {"accepted", "not_modified"}:
                attempt = 0
                delay = self.poll_interval
            else:
                delay = min(300, 5 * 2 ** min(attempt, 6))
                attempt += 1
            await self.sleep(delay)

    async def _request(self) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(self._request_with_redirects(), timeout=15.0)
        except asyncio.TimeoutError:
            raise _PollRejected("cms_request_failed") from None

    async def _request_with_redirects(self) -> dict[str, Any]:
        url = self.cms_url
        for hop in range(MAX_REDIRECTS + 1):
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            }
            if self._payload is not None and self._etag is not None:
                headers["If-None-Match"] = self._etag
            try:
                async with self.http.stream("GET", url, headers=headers) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        if hop >= MAX_REDIRECTS:
                            raise _PollRejected("cms_too_many_redirects")
                        location = response.headers.get("location", "")
                        target = urljoin(url, location)
                        try:
                            target_origin = _origin(
                                target, allowed_schemes={self.cms_origin[0]}
                            )
                        except _PollRejected:
                            raise _PollRejected("cms_redirect_rejected") from None
                        if not location or target_origin != self.cms_origin:
                            raise _PollRejected("cms_redirect_rejected")
                        url = target
                        continue
                    if response.status_code == 304:
                        return {"status": 304, "body": b"", "etag": None}
                    if response.status_code != 200:
                        raise _PollRejected("cms_http_status")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise _PollRejected("cms_response_too_large")
                    return {
                        "status": 200,
                        "body": bytes(body),
                        "etag": response.headers.get("etag"),
                    }
            except _PollRejected:
                raise
            except (httpx.HTTPError, OSError, TimeoutError):
                raise _PollRejected("cms_request_failed") from None
        raise _PollRejected("cms_too_many_redirects")

    def _log(self, level: str, code: str) -> None:
        method = getattr(self.log, level, None)
        if callable(method):
            method(f"lesson generation poll state={code}")


def _parse_json(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            body.decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            object_pairs_hook=_object_without_duplicates,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise _PollRejected("cms_invalid_json") from None
    if type(value) is not dict:
        raise _PollRejected("cms_invalid_envelope")
    return value


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _validate_payload(payload: dict[str, Any], allowed_origins: set[tuple[str, str, int]]) -> dict:
    _exact_fields(payload, _ENVELOPE_FIELDS)
    data = payload.get("data")
    if type(data) is not dict:
        raise _PollRejected("cms_invalid_data")
    _exact_fields(data, _DATA_FIELDS)
    generation = _safe_integer(data.get("generation"), positive=True, code="cms_invalid_generation")
    _utc_timestamp(data.get("publishedAt"), code="cms_invalid_published_at")
    checksum = _checksum(data.get("indexChecksum"), "cms_invalid_checksum")
    curriculum_count = _safe_integer(
        data.get("curriculumLessonCount"), positive=False, code="cms_invalid_curriculum_count"
    )
    pack_count = _safe_integer(data.get("packCount"), positive=False, code="cms_invalid_pack_count")
    index = data.get("index")
    if type(index) is not list:
        raise _PollRejected("cms_invalid_index")
    packs = [_validate_pack(pack, allowed_origins) for pack in index]
    lesson_ids = [pack["lessonId"] for pack in packs]
    if len(set(lesson_ids)) != len(lesson_ids):
        raise _PollRejected("cms_duplicate_lesson_id")
    if lesson_ids != sorted(lesson_ids, key=_js_sort_key):
        raise _PollRejected("cms_index_not_sorted")
    if pack_count != len(packs):
        raise _PollRejected("cms_pack_count_mismatch")
    if curriculum_count != sum(pack["classification"] == "curriculum" for pack in packs):
        raise _PollRejected("cms_curriculum_count_mismatch")
    try:
        actual_checksum = hashlib.sha256(canonical_json(index)).hexdigest()
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _PollRejected("cms_invalid_index") from None
    if actual_checksum != checksum:
        raise _PollRejected("cms_index_checksum_mismatch")
    return {
        "generation": generation,
        "publishedAt": data["publishedAt"],
        "indexChecksum": checksum,
        "curriculumLessonCount": curriculum_count,
        "packCount": pack_count,
        "index": packs,
    }


def _validate_pack(value: Any, allowed_origins: set[tuple[str, str, int]]) -> dict[str, Any]:
    if type(value) is not dict:
        raise _PollRejected("cms_invalid_pack")
    _exact_fields(value, _PACK_FIELDS)
    lesson_id = value.get("lessonId")
    if (
        not isinstance(lesson_id, str)
        or _LESSON_ID_RE.fullmatch(lesson_id) is None
        or len(lesson_id.encode("utf-8")) > 128
    ):
        raise _PollRejected("cms_invalid_lesson_id")
    version = _safe_integer(
        value.get("lessonVersion"), positive=True, code="cms_invalid_lesson_version"
    )
    if value.get("profile") != "espTft":
        raise _PollRejected("cms_invalid_profile")
    manifest_checksum = _checksum(
        value.get("manifestChecksum"), "cms_invalid_manifest_checksum"
    )
    cache_key = value.get("cacheKey")
    try:
        expected_cache_key = compose_cache_key(lesson_id, version, manifest_checksum)
    except CacheEvictionRefused:
        raise _PollRejected("cms_cache_key_too_long") from None
    if cache_key != expected_cache_key:
        raise _PollRejected("cms_cache_key_mismatch")
    classification = value.get("classification")
    if classification not in {"curriculum", "demo"}:
        raise _PollRejected("cms_invalid_classification")
    raw_assets = value.get("assets")
    if type(raw_assets) is not list or not (1 <= len(raw_assets) <= MAX_ASSETS):
        raise _PollRejected("cms_invalid_assets")
    assets = [_validate_asset(asset, expected_cache_key, allowed_origins) for asset in raw_assets]
    keys = [asset["key"] for asset in assets]
    if len(set(keys)) != len(keys):
        raise _PollRejected("cms_duplicate_asset_key")
    cue_start = next(
        (index for index, asset in enumerate(assets) if "cueId" in asset),
        len(assets),
    )
    if (
        keys[:cue_start] != sorted(keys[:cue_start], key=_js_sort_key)
        or any("cueId" not in asset for asset in assets[cue_start:])
    ):
        raise _PollRejected("cms_assets_not_sorted")
    basenames = [asset["encodedKey"].lower() for asset in assets]
    if len(set(basenames)) != len(basenames):
        raise _PollRejected("cms_asset_basename_collision")
    return {
        "lessonId": lesson_id,
        "lessonVersion": version,
        "profile": "espTft",
        "manifestChecksum": manifest_checksum,
        "cacheKey": expected_cache_key,
        "classification": classification,
        "assets": [{key: asset[key] for key in asset if key != "encodedKey"} for asset in assets],
    }


def _validate_asset(
    value: Any,
    cache_key: str,
    allowed_origins: set[tuple[str, str, int]],
) -> dict[str, Any]:
    if type(value) is not dict:
        raise _PollRejected("cms_invalid_asset")
    fields = set(value)
    if not _ASSET_FIELDS.issubset(fields) or fields - _ALL_ASSET_FIELDS:
        if fields & {"derivativeId", "phaseId", "cueId"}:
            raise _PollRejected("cms_invalid_renderer_v4_mp4")
        raise _PollRejected("cms_unknown_field")
    key = value.get("key")
    # T5.1: this used to encode with safe="-_.!~*'()" — the OLD encodeURIComponent
    # semantics — while the materializer, the sync path and the pack store all
    # use quote(key, safe=""). The two ESP ingress paths disagreed with each
    # other on any key containing ! ' ( ) *.
    if isinstance(key, str) and len(quote(key, safe="").encode("ascii", "replace")) > MAX_ENCODED_BASENAME_BYTES:
        raise _PollRejected("cms_asset_key_too_long")
    try:
        encoded = encode_asset_basename(key)
    except AssetBasenameRefused:
        raise _PollRejected("cms_invalid_asset_key") from None
    expected_path = f"/sdcard/tbot/lesson-assets/{cache_key}/{encoded}"
    if value.get("sdPath") != value.get("localPath"):
        raise _PollRejected("cms_asset_alias_mismatch")
    if value.get("sdPath") != expected_path:
        if fields & {"derivativeId", "phaseId", "cueId"}:
            raise _PollRejected("cms_invalid_renderer_v4_mp4")
        raise _PollRejected("cms_sd_path_mismatch")
    if value.get("onlineUrl") != value.get("url"):
        raise _PollRejected("cms_asset_alias_mismatch")
    _validate_asset_url(
        value.get("onlineUrl"),
        allowed_origins,
        allow_query_fragment=value.get("mediaType") == "video/mp4",
    )
    _checksum(value.get("sha256"), "cms_invalid_asset_checksum")
    _safe_integer(value.get("size"), positive=False, code="cms_invalid_asset_size")
    if not isinstance(value.get("mediaType"), str) or not value["mediaType"]:
        raise _PollRejected("cms_invalid_media_type")
    if type(value.get("critical")) is not bool:
        raise _PollRejected("cms_invalid_critical")
    media_type = value["mediaType"]
    metadata = value.get("compatibilityMetadata")
    if (
        fields == _ASSET_FIELDS | _SHARED_IDENTITY_FIELDS | _RENDERER_V3_MP4_FIELDS
        and isinstance(metadata, dict)
        and "mediaKind" in metadata
    ):
        try:
            validate_layered_cinematic_generation_asset(value)
        except LayeredCinematicContractError:
            raise _PollRejected("cms_invalid_renderer_v5_asset") from None
    elif media_type == TRGB_MEDIA_TYPE:
        public_url = value["onlineUrl"]
        public_path = urlsplit(public_url).path
        if (
            _origin(public_url, allowed_schemes={"https"}, allow_fragment=False)
            != _TRGB_PROXY_ORIGIN
            or not public_path.startswith(_TRGB_PROXY_PATH_PREFIX)
            or public_path.count(_RENDERER_DERIVATIVE_PATH_PREFIX) != 1
        ):
            raise _PollRejected("cms_invalid_renderer_v4_mp4")
        canonical_path = public_path.removeprefix("/lesson-derivatives/")
        try:
            contract_asset = dict(value)
            contract_asset["path"] = canonical_path
            validate_flattened_cinematic_cache_asset(contract_asset)
        except FlattenedCinematicContractError:
            raise _PollRejected("cms_invalid_renderer_v4_mp4") from None
        if fields != _ASSET_FIELDS | _RENDERER_V4_V2_TRGB_FIELDS:
            raise _PollRejected("cms_invalid_renderer_v4_mp4")
    elif media_type.lower().startswith("video/"):
        if fields & frozenset({"derivativeId", "phaseId", "cueId"}):
            try:
                validate_renderer_v4_flattened_mp4(value)
            except FirmwareSyncPackError:
                raise _PollRejected("cms_invalid_renderer_v4_mp4") from None
            expected_fields = (
                _ASSET_FIELDS | _RENDERER_V4_V1_MP4_FIELDS
                if "phaseId" in fields
                else _ASSET_FIELDS | _RENDERER_V4_V2_MP4_FIELDS
            )
            if fields != expected_fields:
                raise _PollRejected("cms_invalid_renderer_v4_mp4")
        else:
            try:
                validate_renderer_v3_shared_mp4(value)
            except FirmwareSyncPackError:
                raise _PollRejected("cms_invalid_renderer_v3_mp4") from None
            if fields != _ASSET_FIELDS | _SHARED_IDENTITY_FIELDS | _RENDERER_V3_MP4_FIELDS:
                raise _PollRejected("cms_invalid_renderer_v3_mp4")
    elif fields & _RENDERER_V4_MP4_FIELDS:
        raise _PollRejected("cms_invalid_renderer_v4_mp4")
    elif fields & _RENDERER_V3_MP4_FIELDS:
        raise _PollRejected("cms_invalid_renderer_v3_mp4")
    elif fields & _SHARED_IDENTITY_FIELDS:
        if not _SHARED_IDENTITY_FIELDS.issubset(fields):
            raise _PollRejected("cms_invalid_shared_asset_identity")
        shared_key = value.get("sharedAssetKey")
        shared_version = value.get("sharedAssetVersion")
        if not isinstance(shared_key, str) or not shared_key or type(shared_version) is not int or shared_version < 1:
            raise _PollRejected("cms_invalid_shared_asset_identity")
        if value.get("key") != f"{shared_key}@v{shared_version}":
            raise _PollRejected("cms_invalid_shared_asset_identity")
    return {**value, "encodedKey": encoded}


def _validate_asset_url(
    value: Any,
    allowed_origins: set[tuple[str, str, int]],
    *,
    allow_query_fragment: bool = False,
) -> None:
    if not isinstance(value, str):
        raise _PollRejected("cms_asset_url_rejected")
    try:
        parts = urlsplit(value)
        origin = _origin(
            value,
            allowed_schemes={"https"},
            allow_fragment=allow_query_fragment,
        )
    except _PollRejected:
        raise _PollRejected("cms_asset_url_rejected") from None
    if parts.username or parts.password or (not allow_query_fragment and (parts.fragment or parts.query)):
        raise _PollRejected("cms_asset_url_rejected")
    if origin not in allowed_origins:
        raise _PollRejected("cms_asset_origin_rejected")


def _exact_fields(value: dict[str, Any], expected: frozenset[str]) -> None:
    if set(value) != expected:
        raise _PollRejected("cms_unknown_field")


def _safe_integer(value: Any, *, positive: bool, code: str) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or not minimum <= value <= MAX_SAFE_INTEGER:
        raise _PollRejected(code)
    return value


def _checksum(value: Any, code: str) -> str:
    if not isinstance(value, str) or _HEX_RE.fullmatch(value) is None:
        raise _PollRejected(code)
    return value


def _js_sort_key(value: str) -> bytes:
    return value.encode("utf-16-be", errors="surrogatepass")


def _origin(
    url: str,
    *,
    allowed_schemes: set[str],
    allow_fragment: bool = False,
) -> tuple[str, str, int]:
    try:
        if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
            raise ValueError
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        hostname = _normalized_hostname(parts.hostname or "")
        port = parts.port
    except (TypeError, ValueError):
        raise _PollRejected("cms_invalid_url") from None
    if (
        scheme not in allowed_schemes
        or not hostname
        or not parts.netloc
        or parts.username
        or parts.password
        or (parts.fragment and not allow_fragment)
    ):
        raise _PollRejected("cms_invalid_url")
    return scheme, hostname, port or (443 if scheme == "https" else 80)


def _normalized_hostname(hostname: str) -> str:
    if not hostname:
        raise ValueError
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if ":" in hostname or "%" in hostname:
            raise ValueError from None
        try:
            ascii_hostname = hostname.encode("ascii").decode("ascii").lower()
        except UnicodeEncodeError:
            raise ValueError from None
        canonical = ascii_hostname[:-1] if ascii_hostname.endswith(".") else ascii_hostname
        if not canonical or len(canonical) > 253:
            raise ValueError from None
        labels = canonical.split(".")
        if any(_DNS_LABEL_RE.fullmatch(label) is None for label in labels):
            raise ValueError from None
        if len(labels) == 4 and all(label.isdigit() for label in labels):
            raise ValueError from None
        return ascii_hostname
    return address.compressed.lower()


def _allowed_origins(config: Mapping[str, Any]) -> set[tuple[str, str, int]]:
    raw = os.environ.get("LESSON_ASSET_ALLOWED_ORIGINS")
    if raw is None:
        raw = _lesson_config(config).get("asset_allowed_origins", "")
    origins: set[tuple[str, str, int]] = set()
    for item in str(raw).split(","):
        item = item.strip()
        if not item:
            continue
        try:
            parts = urlsplit(item)
            origin = _origin(item, allowed_schemes={"https"})
        except ValueError:
            raise ValueError(
                "LESSON_ASSET_ALLOWED_ORIGINS must contain HTTPS origins"
            ) from None
        if parts.path not in {"", "/"} or parts.query:
            raise ValueError("LESSON_ASSET_ALLOWED_ORIGINS must contain HTTPS origins")
        origins.add(origin)
    if not origins:
        raise ValueError("LESSON_ASSET_ALLOWED_ORIGINS must contain an HTTPS origin")
    return origins


def _cms_url(config: Mapping[str, Any]) -> str:
    value = os.environ.get("LESSON_GENERATION_CMS_URL")
    if value is None:
        value = _lesson_config(config).get("generation_cms_url")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("LESSON_GENERATION_CMS_URL is required")
    return value.strip()


def _poll_interval(config: Mapping[str, Any]) -> float:
    raw = os.environ.get("LESSON_GENERATION_POLL_INTERVAL_SEC")
    if raw is None:
        raw = _lesson_config(config).get("generation_poll_interval_sec", POLL_INTERVAL_SECONDS)
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("LESSON_GENERATION_POLL_INTERVAL_SEC must be positive") from None
    if not 0 < value <= 300:
        raise ValueError("LESSON_GENERATION_POLL_INTERVAL_SEC must be positive and bounded")
    return value


def _lesson_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config.get("lesson", {}) if isinstance(config, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _utc_timestamp(value: Any, *, code: str = "cms_invalid_clock") -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise _PollRejected(code)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or _RFC3339_UTC_RE.fullmatch(value) is None:
        raise _PollRejected(code)
    try:
        datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise _PollRejected(code) from None
    return value
