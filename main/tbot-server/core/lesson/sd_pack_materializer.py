"""Verified canonical lesson pack materialization for ESP SD storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.parse import quote, urlsplit

import httpx

from config.logger import setup_logging
from core.lesson.cache_key_contract import CacheEvictionRefused, validate_cache_key
from core.lesson.shared_asset_store import SharedAssetStore

CHUNK_SIZE = 64 * 1024
MAX_ASSETS = 64
MAX_CONFIG_BYTES = 2 * 1024 * 1024 * 1024
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
    }
)
_METRICS = {
    "accepted": 0,
    "replayed": 0,
    "rejected": 0,
    "checksum_failures": 0,
}


@dataclass
class MaterializationError(Exception):
    code: str
    status: int
    retryable: bool
    message: str = ""
    details: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        super().__init__(self.message or self.code)
        self.message = self.message or self.code
        self.details = dict(self.details or {})

    def to_response(self) -> Dict[str, Any]:
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
) -> Dict[str, Any]:
    start = time.monotonic()
    log = logger or setup_logging()
    normalized = _validate_manifest(manifest, config)
    store = _shared_store(config)
    cache_key = normalized["cacheKey"]
    if store.is_pack_ready(cache_key):
        _METRICS["replayed"] += 1
        result = _result(cache_key, len(normalized["assets"]), 0, len(normalized["assets"]))
        _log(log, "info", cache_key, 0, start, "replayed", None)
        return result

    own_client = client is None
    http_client = client or httpx.AsyncClient(timeout=60.0, follow_redirects=False)
    staging_root = _staging_root(config, store)
    staging = Path(tempfile.mkdtemp(prefix=".materialize-", dir=str(staging_root)))
    downloaded: Dict[str, tuple[Path, str]] = {}
    total_bytes = 0
    try:
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
        store.put_files_and_commit_pack(
            cache_key,
            downloaded,
            manifest=_public_pack_manifest(normalized),
        )
        _METRICS["accepted"] += 1
        result = _result(cache_key, len(normalized["assets"]), len(normalized["assets"]), 0)
        _log(log, "info", cache_key, total_bytes, start, "accepted", None)
        return result
    except asyncio.CancelledError:
        shutil.rmtree(staging, ignore_errors=True)
        _log(log, "warning", cache_key, total_bytes, start, "cancelled", "CANCELLED")
        raise
    except MaterializationError as exc:
        shutil.rmtree(staging, ignore_errors=True)
        _METRICS["rejected"] += 1
        if exc.code == "CHECKSUM_MISMATCH":
            _METRICS["checksum_failures"] += 1
        _log(log, "warning", cache_key, total_bytes, start, "rejected", exc.code)
        raise
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        _METRICS["rejected"] += 1
        error = MaterializationError(
            "STORAGE_ERROR",
            500,
            True,
            "Failed to materialize lesson asset pack",
            {"type": type(exc).__name__},
        )
        _log(log, "error", cache_key, total_bytes, start, "rejected", error.code)
        raise error from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if own_client:
            await http_client.aclose()


def materialization_metrics() -> Dict[str, int]:
    return dict(_METRICS)


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


def _validate_manifest(manifest: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
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
) -> Dict[str, Any]:
    if not isinstance(item, Mapping):
        raise _bad("INVALID_ASSET", "Asset must be an object")
    _reject_field_delta(set(item.keys()), _ASSET_FIELDS)
    key = item.get("key")
    if not isinstance(key, str) or not key or len(key.encode("utf-8")) > 160:
        raise _bad("INVALID_ASSET_KEY", "Invalid asset key")
    if key in (".", "..") or any(ord(ch) < 32 for ch in key):
        raise _bad("INVALID_ASSET_KEY", "Invalid asset key")
    encoded = quote(key, safe="")
    if not encoded or encoded in (".", "..") or "/" in encoded or "\\" in encoded:
        raise _bad("INVALID_ASSET_KEY", "Invalid asset key")
    digest = _sha256_value(item.get("sha256"), "INVALID_ASSET_SHA256")
    size = item.get("size")
    if type(size) is not int or size <= 0:
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
    _validate_url(online_url, allowed, production)
    expected_suffix = f"/{cache_key}/{encoded}"
    if not isinstance(sd_path, str) or not sd_path.endswith(expected_suffix):
        raise _bad("INVALID_SD_PATH", "Invalid asset sdPath")
    return {
        "key": key,
        "sha256": digest,
        "size": size,
        "mediaType": media_type,
        "critical": item["critical"],
        "onlineUrl": online_url,
        "sdPath": sd_path,
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


def _validate_url(url: str, allowed: set[str], production: bool) -> None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc or parts.username or parts.password:
        raise _bad("INVALID_URL", "Invalid asset URL")
    if production and parts.scheme != "https":
        raise _bad("NON_HTTPS_URL", "Production asset URLs must use HTTPS")
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in allowed:
        raise _bad("DISALLOWED_ORIGIN", "Asset URL origin is not allowed")


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


def _result(cache_key: str, asset_count: int, downloaded: int, skipped: int) -> Dict[str, Any]:
    return {
        "cacheKey": cache_key,
        "ready": True,
        "assetCount": asset_count,
        "downloadedCount": downloaded,
        "skippedCount": skipped,
    }


def _public_pack_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "cacheKey": manifest["cacheKey"],
        "lessonId": manifest["lessonId"],
        "lessonVersion": manifest["lessonVersion"],
        "profile": manifest["profile"],
        "manifestChecksum": manifest["manifestChecksum"],
        "assets": [dict(asset) for asset in manifest["assets"]],
    }


def _log(
    logger: Any,
    level: str,
    cache_key: str,
    byte_count: int,
    start: float,
    result: str,
    code: Optional[str],
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

