"""Verified canonical lesson pack materialization for ESP SD storage."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import shutil
import socket
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from config.logger import setup_logging
from core.lesson.cache_key_contract import CacheEvictionRefused, validate_cache_key
from core.lesson.shared_asset_store import (
    PACK_COMMIT_REPLAYED,
    PackReplayMismatchError,
    SharedAssetStore,
)
from core.lesson.sd_pack_mcp_payload import (
    FirmwareSyncPackError,
    validate_renderer_v3_shared_mp4,
    validate_renderer_v4_flattened_mp4,
)

CHUNK_SIZE = 64 * 1024
MAX_ASSETS = 64
MAX_CONFIG_BYTES = 2 * 1024 * 1024 * 1024
# Leaves room for SharedAssetStore's ".pid.uuid.part" atomic temp suffix under
# FAT-style 255-byte component limits.
MAX_ENCODED_BASENAME_BYTES = 200
_TOP_LEVEL_FIELDS = frozenset(
    {"lessonId", "lessonVersion", "profile", "manifestChecksum", "cacheKey", "assets"}
)
_ASSET_FIELDS = frozenset(
    {
        "key",
        "sha256",
        "size",
        "mediaType",
        "critical",
        "onlineUrl",
        "url",
        "sdPath",
        "localPath",
        "sharedAssetKey",
        "sharedAssetVersion",
        "compatibilityMetadata",
        "visualRefs",
        "derivativeId",
        "phaseId",
        "cueId",
        "effect",
        "stepKey",
        "playbackMode",
    }
)
_METRICS = {
    "accepted": 0,
    "replayed": 0,
    "rejected": 0,
    "checksum_failures": 0,
}

class _PinnedAddressAsyncNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        pinned_addresses: Mapping[str, Iterable[str]],
        *,
        delegate: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._pinned = {
            str(host): tuple(str(address) for address in addresses)
            for host, addresses in pinned_addresses.items()
        }
        self._delegate = delegate or AutoBackend()
        self._next_index: dict[str, int] = {}

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = self._pinned.get(host)
        if not addresses:
            raise httpcore.ConnectError("pinned address missing")
        index = self._next_index.get(host, 0)
        self._next_index[host] = index + 1
        pinned_host = addresses[index % len(addresses)]
        return await self._delegate.connect_tcp(
            pinned_host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._delegate.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        return await self._delegate.sleep(seconds)

class _PinnedAddressAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, pinned_addresses: Mapping[str, Iterable[str]]) -> None:
        super().__init__(trust_env=False, http2=False, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            http1=True,
            http2=False,
            retries=0,
            network_backend=_PinnedAddressAsyncNetworkBackend(pinned_addresses),
        )

def _pinned_async_client(pinned_addresses: Mapping[str, Iterable[str]]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_PinnedAddressAsyncHTTPTransport(pinned_addresses),
        timeout=60.0,
        follow_redirects=False,
        trust_env=False,
    )


@dataclass
class MaterializationError(Exception):
    code: str
    status: int
    retryable: bool
    message: str = ""
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        super().__init__(self.message or self.code)
        self.message = self.message or self.code
        self.details = dict(self.details or {})

    def to_response(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


async def materialize_lesson_sd_pack(
    manifest: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    client: Any = None,
    logger: Any = None,
    resolver: Callable[[str], Awaitable[Iterable[str]]] | None = None,
) -> dict[str, Any]:
    start = time.monotonic()
    log = logger or setup_logging()
    cache_key = _safe_cache_key_for_log(manifest)
    own_client = False
    http_client = None
    staging: Path | None = None
    total_bytes = 0
    try:
        normalized = _validate_manifest(manifest, config)
        cache_key = normalized["cacheKey"]
        store = _shared_store(config)
        if store.is_pack_ready(cache_key):
            replay_status = _replay_status(store, normalized)
            if replay_status == "match":
                _METRICS["replayed"] += 1
                result = _result(
                    cache_key,
                    len(normalized["assets"]),
                    0,
                    len(normalized["assets"]),
                )
                _log(log, "info", cache_key, 0, start, "replayed", None)
                return result
            if replay_status == "mismatch":
                raise MaterializationError(
                    "PACK_REPLAY_MISMATCH",
                    409,
                    False,
                    "READY lesson asset pack does not match manifest",
                )

        pinned_addresses = await _attest_manifest_urls(normalized, resolver)
        own_client = client is None
        http_client = client or _pinned_async_client(pinned_addresses)
        staging_root = _staging_root(config, store)
        staging = Path(tempfile.mkdtemp(prefix=".materialize-", dir=str(staging_root)))
        downloaded: dict[str, tuple[Path, str]] = {}
        for asset in normalized["assets"]:
            path, count = await _download_asset(
                http_client,
                asset,
                staging,
                total_bytes,
                normalized,
            )
            total_bytes += count
            downloaded[asset["key"]] = (path, asset["sha256"])
        _pack_path, commit_status = store.put_files_and_commit_pack(
            cache_key,
            downloaded,
            manifest=_public_pack_manifest(normalized),
            return_status=True,
        )
        if commit_status == PACK_COMMIT_REPLAYED:
            _METRICS["replayed"] += 1
            result = _result(cache_key, len(normalized["assets"]), 0, len(normalized["assets"]))
            _log(log, "info", cache_key, 0, start, "replayed", None)
            return result
        _METRICS["accepted"] += 1
        result = _result(cache_key, len(normalized["assets"]), len(normalized["assets"]), 0)
        _log(log, "info", cache_key, total_bytes, start, "accepted", None)
        return result
    except asyncio.CancelledError:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        _log(log, "warning", cache_key, total_bytes, start, "cancelled", "CANCELLED")
        raise
    except MaterializationError as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        _record_rejected(log, cache_key, total_bytes, start, exc, level="warning")
        raise
    except PackReplayMismatchError as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        error = MaterializationError(
            "PACK_REPLAY_MISMATCH",
            409,
            False,
            "READY lesson asset pack does not match manifest",
        )
        _record_rejected(log, cache_key, total_bytes, start, error, level="warning")
        raise error from exc
    except Exception as exc:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        error = MaterializationError(
            "STORAGE_ERROR",
            500,
            True,
            "Failed to materialize lesson asset pack",
            {"type": type(exc).__name__},
        )
        _record_rejected(log, cache_key, total_bytes, start, error, level="error")
        raise error from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if own_client and http_client is not None:
            await http_client.aclose()


def materialization_metrics() -> dict[str, int]:
    return dict(_METRICS)


def _record_rejected(
    logger: Any,
    cache_key: str,
    byte_count: int,
    start: float,
    error: MaterializationError,
    *,
    level: str,
) -> None:
    _METRICS["rejected"] += 1
    if error.code == "CHECKSUM_MISMATCH":
        _METRICS["checksum_failures"] += 1
    _log(logger, level, cache_key, byte_count, start, "rejected", error.code)

def _safe_cache_key_for_log(manifest: Any) -> str:
    if not isinstance(manifest, Mapping):
        return ""
    try:
        return validate_cache_key(manifest.get("cacheKey"))
    except Exception:
        return ""

async def _download_asset(
    client: Any,
    asset: Mapping[str, Any],
    staging: Path,
    current_total: int,
    manifest: Mapping[str, Any],
) -> tuple[Path, int]:
    target = staging / quote(asset["key"], safe="")
    hasher = hashlib.sha256()
    count = 0
    try:
        async with client.stream("GET", asset["onlineUrl"]) as response:
            if 300 <= int(getattr(response, "status_code", 0)) < 400:
                raise MaterializationError(
                    "REDIRECT_NOT_ALLOWED",
                    400,
                    False,
                    "Asset URL redirects are not allowed",
                    {"assetKey": asset["key"]},
                )
            response.raise_for_status()
            with target.open("xb") as handle:
                async for chunk in response.aiter_bytes(CHUNK_SIZE):
                    if not chunk:
                        continue
                    count += len(chunk)
                    if count > asset["size"]:
                        raise MaterializationError(
                            "DECLARED_SIZE_MISMATCH",
                            400,
                            False,
                            "Downloaded byte count does not match manifest",
                            {"assetKey": asset["key"]},
                        )
                    if count > manifest["maxFileBytes"]:
                        raise MaterializationError(
                            "FILE_TOO_LARGE",
                            413,
                            False,
                            "Asset exceeds configured byte limit",
                            {"assetKey": asset["key"]},
                        )
                    if current_total + count > manifest["maxPackBytes"]:
                        raise MaterializationError(
                            "PACK_TOO_LARGE",
                            413,
                            False,
                            "Lesson asset pack exceeds configured byte limit",
                        )
                    hasher.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
    except MaterializationError:
        raise
    except Exception as exc:
        raise MaterializationError(
            "DOWNLOAD_FAILED",
            502,
            True,
            "Failed to download lesson asset",
            {"assetKey": asset["key"], "type": type(exc).__name__},
        ) from exc
    if count != asset["size"]:
        raise MaterializationError(
            "DECLARED_SIZE_MISMATCH",
            400,
            False,
            "Downloaded byte count does not match manifest",
            {"assetKey": asset["key"]},
        )
    if hasher.hexdigest() != asset["sha256"]:
        raise MaterializationError(
            "CHECKSUM_MISMATCH",
            400,
            False,
            "Downloaded checksum does not match manifest",
            {"assetKey": asset["key"]},
        )
    return target, count


def _validate_manifest(manifest: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(manifest, Mapping):
        raise _bad("INVALID_MANIFEST", "Manifest must be an object")
    _reject_field_delta(set(manifest.keys()), _TOP_LEVEL_FIELDS, require_all=True)
    lesson_id = _safe_lesson_id(manifest.get("lessonId"))
    version = _safe_version(manifest.get("lessonVersion"))
    profile = manifest.get("profile")
    checksum = _sha256_value(manifest.get("manifestChecksum"), "INVALID_MANIFEST_CHECKSUM")
    if profile != "espTft":
        raise _bad("INVALID_PROFILE", "Manifest profile must be espTft")
    expected_cache_key = f"{lesson_id}/v{version}-{checksum}"
    try:
        cache_key = validate_cache_key(manifest.get("cacheKey"))
    except CacheEvictionRefused:
        raise _bad("INVALID_CACHE_KEY", "Invalid canonical cache key") from None
    if cache_key != expected_cache_key:
        raise _bad("CACHE_KEY_MISMATCH", "cacheKey does not match lesson manifest identity")
    assets_raw = manifest.get("assets")
    if not isinstance(assets_raw, list) or not (1 <= len(assets_raw) <= MAX_ASSETS):
        raise _bad("INVALID_ASSETS", "assets must contain 1 to 64 entries")
    max_file = _limit(config, "LESSON_SD_MAX_FILE_BYTES", "max_file_bytes")
    max_pack = _limit(config, "LESSON_SD_MAX_PACK_BYTES", "max_pack_bytes")
    allowed = _allowed_origins(config)
    production = _is_production(config)
    assets = []
    seen_keys = set()
    seen_basenames = set()
    declared_total = 0
    for item in assets_raw:
        asset = _validate_asset(item, cache_key, max_file, allowed, production)
        if asset["key"] in seen_keys:
            raise _bad("DUPLICATE_ASSET_KEY", "Duplicate asset key")
        seen_keys.add(asset["key"])
        encoded_lower = quote(asset["key"], safe="").lower()
        if encoded_lower in seen_basenames:
            raise _bad("BASENAME_COLLISION", "Asset names collide on FAT storage")
        seen_basenames.add(encoded_lower)
        declared_total += asset["size"]
        if declared_total > max_pack:
            raise MaterializationError(
                "PACK_TOO_LARGE",
                413,
                False,
                "Lesson asset pack exceeds configured byte limit",
            )
        assets.append(asset)
    return {
        "lessonId": lesson_id,
        "lessonVersion": version,
        "profile": "espTft",
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "assets": assets,
        "maxFileBytes": max_file,
        "maxPackBytes": max_pack,
    }


def _validate_asset(
    item: Any,
    cache_key: str,
    max_file: int,
    allowed: set[str],
    production: bool,
) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        raise _bad("INVALID_ASSET", "Asset must be an object")
    _reject_field_delta(set(item.keys()), _ASSET_FIELDS)
    key = item.get("key")
    if not isinstance(key, str) or not key:
        raise _bad("INVALID_ASSET_KEY", "Invalid asset key")
    if key in (".", "..") or any(ord(ch) < 32 for ch in key):
        raise _bad("INVALID_ASSET_KEY", "Invalid asset key")
    encoded = quote(key, safe="")
    if len(encoded.encode("ascii")) > MAX_ENCODED_BASENAME_BYTES:
        raise _bad("INVALID_ASSET_KEY", "Invalid asset key")
    if not encoded or encoded in (".", "..") or "/" in encoded or "\\" in encoded:
        raise _bad("INVALID_ASSET_KEY", "Invalid asset key")
    digest = _sha256_value(item.get("sha256"), "INVALID_ASSET_SHA256")
    size = item.get("size")
    if type(size) is not int or size < 0:
        raise _bad("INVALID_ASSET_SIZE", "Invalid asset size")
    if size > max_file:
        raise MaterializationError(
            "FILE_TOO_LARGE",
            413,
            False,
            "Asset exceeds configured byte limit",
            {"assetKey": key},
        )
    media_type = item.get("mediaType")
    if not isinstance(media_type, str) or not media_type.strip():
        raise _bad("INVALID_MEDIA_TYPE", "Invalid asset mediaType")
    if type(item.get("critical")) is not bool:
        raise _bad("INVALID_CRITICAL", "Invalid asset critical flag")
    online_url = _alias(item, "onlineUrl", "url")
    sd_path = _alias(item, "sdPath", "localPath")
    _validate_url(
        online_url,
        allowed,
        production,
        allow_fragment=media_type == "video/mp4",
    )
    expected_sd_path = f"/sdcard/tbot/lesson-assets/{cache_key}/{encoded}"
    if sd_path != expected_sd_path:
        raise _bad("INVALID_SD_PATH", "Invalid asset sdPath")
    renderer_v3_fields: dict[str, Any] = {}
    if media_type.lower().startswith("video/"):
        if "derivativeId" in item or "phaseId" in item or "cueId" in item:
            try:
                renderer_v3_fields = validate_renderer_v4_flattened_mp4(dict(item))
            except FirmwareSyncPackError:
                raise _bad("INVALID_RENDERER_V4_MP4", "Invalid renderer-v4 flattened MP4") from None
        else:
            try:
                renderer_v3_fields = validate_renderer_v3_shared_mp4(dict(item))
            except FirmwareSyncPackError:
                raise _bad("INVALID_RENDERER_V3_MP4", "Invalid renderer-v3 shared MP4") from None
    elif any(field in item for field in (
        "sharedAssetKey", "sharedAssetVersion", "compatibilityMetadata", "visualRefs",
        "derivativeId", "phaseId", "cueId", "effect", "stepKey", "playbackMode",
    )):
        if "compatibilityMetadata" in item or "visualRefs" in item:
            raise _bad("INVALID_RENDERER_V3_MP4", "Renderer-v3 metadata is only valid for shared MP4")
        shared_key = item.get("sharedAssetKey")
        shared_version = item.get("sharedAssetVersion")
        if not isinstance(shared_key, str) or not shared_key or type(shared_version) is not int or shared_version < 1:
            raise _bad("INVALID_SHARED_ASSET_IDENTITY", "Invalid shared asset identity")
        if key != f"{shared_key}@v{shared_version}":
            raise _bad("INVALID_SHARED_ASSET_IDENTITY", "Invalid shared asset identity")
        renderer_v3_fields = {
            "sharedAssetKey": shared_key,
            "sharedAssetVersion": shared_version,
        }
    return {
        "key": key,
        "sha256": digest,
        "size": size,
        "mediaType": media_type,
        "critical": item["critical"],
        "onlineUrl": online_url,
        "sdPath": sd_path,
        **renderer_v3_fields,
    }


def _reject_field_delta(
    actual: set[str],
    allowed: Iterable[str],
    *,
    require_all: bool = False,
) -> None:
    allowed_set = set(allowed)
    if actual - allowed_set:
        raise _bad("UNKNOWN_FIELD", "Manifest contains unknown fields")
    if require_all and allowed_set - actual:
        raise _bad("MISSING_FIELD", "Manifest is missing required fields")


def _alias(item: Mapping[str, Any], primary: str, alias: str) -> str:
    primary_value = item.get(primary)
    alias_value = item.get(alias)
    if primary_value is None and alias_value is None:
        raise _bad("MISSING_FIELD", f"Missing {primary}")
    if primary_value is not None and alias_value is not None and primary_value != alias_value:
        raise _bad("ALIAS_CONFLICT", f"{primary} and {alias} disagree")
    value = primary_value if primary_value is not None else alias_value
    if not isinstance(value, str) or not value:
        raise _bad("MISSING_FIELD", f"Missing {primary}")
    return value


def _validate_url(url: str, allowed: set[str], production: bool, *, allow_fragment: bool = False) -> None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc or parts.username or parts.password:
        raise _bad("INVALID_URL", "Invalid asset URL")
    if parts.fragment and not allow_fragment:
        raise _bad("URL_FRAGMENT", "Asset URL fragments are not allowed")
    if production and parts.scheme != "https":
        raise _bad("NON_HTTPS_URL", "Production asset URLs must use HTTPS")
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in allowed:
        raise _bad("DISALLOWED_ORIGIN", "Asset URL origin is not allowed")
    _validate_url_path(parts.path)
    _validate_literal_host(parts.hostname or "")

def _validate_url_path(path: str) -> None:
    if "\\" in path or _contains_encoded_separator(path):
        raise _bad("UNSAFE_URL_PATH", "Asset URL path is unsafe")
    decoded = path
    for _index in range(2):
        decoded = unquote(decoded)
        if "\\" in decoded or _contains_encoded_separator(decoded):
            raise _bad("UNSAFE_URL_PATH", "Asset URL path is unsafe")
        if any(segment in (".", "..") for segment in decoded.split("/")):
            raise _bad("UNSAFE_URL_PATH", "Asset URL path is unsafe")

def _contains_encoded_separator(path: str) -> bool:
    lower = path.lower()
    return "%2f" in lower or "%5c" in lower

def _validate_literal_host(host: str) -> None:
    try:
        address = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    if not address.is_global:
        raise _bad("PRIVATE_ADDRESS", "Asset URL host must resolve to public addresses")

async def _attest_url_addresses(
    url: str,
    resolver: Callable[[str], Awaitable[Iterable[str]]] | None,
) -> list[str]:
    host = urlsplit(url).hostname or ""
    try:
        address = ipaddress.ip_address(host.strip("[]"))
        return [str(address)]
    except ValueError:
        pass
    lookup = resolver or _default_resolver
    try:
        addresses = list(await lookup(host))
    except MaterializationError:
        raise
    except Exception as exc:
        raise MaterializationError(
            "DNS_RESOLUTION_FAILED",
            502,
            True,
            "Asset URL host could not be resolved",
            {"type": type(exc).__name__},
        ) from exc
    if not addresses:
        raise MaterializationError(
            "DNS_RESOLUTION_FAILED",
            502,
            True,
            "Asset URL host could not be resolved",
        )
    for value in addresses:
        try:
            address = ipaddress.ip_address(str(value).strip("[]"))
        except ValueError as exc:
            raise MaterializationError(
                "DNS_RESOLUTION_FAILED",
                502,
                True,
                "Asset URL host resolved to an invalid address",
            ) from exc
        if not address.is_global:
            raise MaterializationError(
                "PRIVATE_ADDRESS",
                400,
                False,
                "Asset URL host must resolve to public addresses",
            )
    return [str(ipaddress.ip_address(str(value).strip("[]"))) for value in addresses]

async def _attest_manifest_urls(
    manifest: Mapping[str, Any],
    resolver: Callable[[str], Awaitable[Iterable[str]]] | None,
) -> dict[str, list[str]]:
    pinned: dict[str, list[str]] = {}
    for asset in manifest["assets"]:
        host = urlsplit(asset["onlineUrl"]).hostname or ""
        if host not in pinned:
            pinned[host] = await _attest_url_addresses(asset["onlineUrl"], resolver)
    return pinned

async def _default_resolver(host: str) -> list[str]:
    def resolve() -> list[str]:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos})

    return await asyncio.to_thread(resolve)


def _safe_lesson_id(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise _bad("INVALID_LESSON_ID", "Invalid lessonId")
    probe = f"{value}/v1-{'0' * 64}"
    try:
        validate_cache_key(probe)
    except CacheEvictionRefused:
        raise _bad("INVALID_LESSON_ID", "Invalid lessonId") from None
    return value


def _safe_version(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise _bad("INVALID_LESSON_VERSION", "Invalid lessonVersion")
    return value


def _sha256_value(value: Any, code: str) -> str:
    if not isinstance(value, str):
        raise _bad(code, "Invalid sha256")
    if (
        len(value) != 64
        or value.lower() != value
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise _bad(code, "Invalid sha256")
    return value


def _bad(code: str, message: str) -> MaterializationError:
    return MaterializationError(code, 400, False, message)


def _lesson_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    lesson = config.get("lesson", {}) if isinstance(config, Mapping) else {}
    return lesson if isinstance(lesson, Mapping) else {}


def _shared_store(config: Mapping[str, Any]) -> SharedAssetStore:
    lesson = _lesson_config(config)
    pack_root = lesson.get("asset_pack_mount_root")
    if not pack_root:
        raise MaterializationError(
            "STORAGE_NOT_CONFIGURED",
            503,
            True,
            "LESSON_ASSET_PACK_MOUNT_ROOT is not configured",
        )
    pack_root_path = Path(str(pack_root)).resolve()
    return SharedAssetStore(
        pack_root_path.parent,
        pack_root=pack_root_path,
        failure_hook=lesson.get("shared_asset_store_failure_hook"),
    )


def _staging_root(config: Mapping[str, Any], store: SharedAssetStore) -> Path:
    lesson = _lesson_config(config)
    value = lesson.get("materialize_staging_root")
    root = Path(str(value)).resolve() if value else store.root / ".materialize"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _limit(config: Mapping[str, Any], env_name: str, config_name: str) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = _lesson_config(config).get(config_name)
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        raise MaterializationError(
            "INVALID_LIMIT",
            503,
            True,
            f"{env_name} must be a positive bounded integer",
        ) from None
    if value <= 0 or value > MAX_CONFIG_BYTES:
        raise MaterializationError(
            "INVALID_LIMIT",
            503,
            True,
            f"{env_name} must be a positive bounded integer",
        )
    return value


def _allowed_origins(config: Mapping[str, Any]) -> set[str]:
    raw = os.environ.get("LESSON_ASSET_ALLOWED_ORIGINS")
    if raw is None:
        raw = _lesson_config(config).get("asset_allowed_origins", "")
    return {origin.strip().rstrip("/") for origin in str(raw).split(",") if origin.strip()}


def _is_production(config: Mapping[str, Any]) -> bool:
    values = [
        os.environ.get("APP_ENV"),
        os.environ.get("ENVIRONMENT"),
        os.environ.get("NODE_ENV"),
        os.environ.get("TBOT_ENV"),
        str(config.get("environment", "")) if isinstance(config, Mapping) else "",
    ]
    return any(str(value).strip().lower() == "production" for value in values)


def _result(cache_key: str, asset_count: int, downloaded: int, skipped: int) -> dict[str, Any]:
    return {
        "cacheKey": cache_key,
        "ready": True,
        "criticalReady": True,
        "optionalFailedCount": 0,
        "assetCount": asset_count,
        "downloadedCount": downloaded,
        "skippedCount": skipped,
    }


def _public_pack_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cacheKey": manifest["cacheKey"],
        "lessonId": manifest["lessonId"],
        "lessonVersion": manifest["lessonVersion"],
        "profile": manifest["profile"],
        "manifestChecksum": manifest["manifestChecksum"],
        "assets": [dict(asset) for asset in manifest["assets"]],
    }


def _replay_status(store: SharedAssetStore, manifest: Mapping[str, Any]) -> str:
    """Return match, mismatch, or historical for an already READY pack."""
    try:
        pack_dir = (store.pack_root / manifest["cacheKey"]).resolve()
        root = store.pack_root.resolve()
        if root not in pack_dir.parents:
            return "mismatch"
        stored = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return "mismatch"
    assets = stored.get("assets") if isinstance(stored, dict) else None
    if isinstance(assets, dict):
        return "historical"
    if not isinstance(assets, list):
        return "mismatch"
    stored_projection = _replay_manifest_projection(stored)
    incoming_projection = _replay_manifest_projection(manifest)
    return "match" if stored_projection == incoming_projection else "mismatch"

def _replay_manifest_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "cacheKey": manifest.get("cacheKey"),
        "lessonId": manifest.get("lessonId"),
        "lessonVersion": manifest.get("lessonVersion"),
        "profile": manifest.get("profile"),
        "manifestChecksum": manifest.get("manifestChecksum"),
        "assets": sorted(
            (
                _replay_asset_projection(asset)
                for asset in manifest.get("assets", [])
                if isinstance(asset, Mapping)
            ),
            key=lambda asset: str(asset.get("key")),
        ),
    }


def _replay_asset_projection(asset: Mapping[str, Any]) -> dict[str, Any]:
    projection = {
                    "key": asset.get("key"),
                    "sha256": asset.get("sha256"),
                    "size": asset.get("size"),
                    "mediaType": asset.get("mediaType"),
                    "critical": asset.get("critical"),
                    "sdPath": asset.get("sdPath"),
    }
    if asset.get("mediaType") == "video/mp4":
        projection["onlineUrl"] = asset.get("onlineUrl")
        projection["compatibilityMetadata"] = asset.get("compatibilityMetadata")
        if asset.get("derivativeId") is not None or asset.get("phaseId") is not None:
            projection.update({
                "derivativeId": asset.get("derivativeId"),
                "phaseId": asset.get("phaseId"),
            })
        else:
            projection.update({
                "sharedAssetKey": asset.get("sharedAssetKey"),
                "sharedAssetVersion": asset.get("sharedAssetVersion"),
                "visualRefs": sorted(
                    (dict(ref) for ref in asset.get("visualRefs", []) if isinstance(ref, Mapping)),
                    key=lambda ref: (str(ref.get("phase")), str(ref.get("slot")), str(ref.get("stepKey"))),
                ),
            })
    return projection

def _log(
    logger: Any,
    level: str,
    cache_key: str,
    byte_count: int,
    start: float,
    result: str,
    code: str | None,
) -> None:
    try:
        bound = logger.bind(
            tag="lesson_sd_materialize",
            cacheKey=cache_key,
            bytes=byte_count,
            durationMs=int((time.monotonic() - start) * 1000),
            result=result,
            errorCode=code or "",
        )
        getattr(bound, level)(
            "lesson_sd_materialize cacheKey={} bytes={} result={} errorCode={}".format(
                cache_key,
                byte_count,
                result,
                code or "",
            )
        )
    except Exception:
        return
