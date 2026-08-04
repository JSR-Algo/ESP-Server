"""Background SD-pack sync for already cached lesson assets."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import os
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.lesson.flattened_cinematic_contract import (
    TRGB_MEDIA_TYPE,
    FlattenedCinematicContractError,
    validate_pathless_flattened_cinematic_cache_asset,
)
from core.lesson.sd_pack_mcp_payload import (
    FirmwareSyncPackError,
    build_firmware_sync_pack,
    validate_renderer_v3_shared_mp4,
    validate_renderer_v4_flattened_mp4,
)
from core.lesson.shared_asset_store import SharedAssetStore
from core.utils.util import get_vision_url

SD_PACK_SYNC_TOOL = "self.lesson_assets.sync_to_sd"
SD_PACK_SYNC_TIMEOUT_FLOOR_SEC = 120
SD_PACK_SYNC_TIMEOUT_CEILING_SEC = 6 * 60 * 60
SD_PACK_SYNC_BASE_SEC = 30
SD_PACK_SYNC_PER_ASSET_SEC = 8
SD_PACK_SYNC_BYTES_PER_SEC = 16 * 1024
SD_PACK_SYNC_RECOVERY_BACKOFF_SEC = 30
DEFAULT_CACHE_ROOT = "data/lesson_assets"
DEFAULT_LOCAL_ROOT = "sd://tbot/lesson-assets"
_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ERROR_CODE_MAX_LEN = 64
_COORDINATOR_ATTR = "_sd_pack_sync_coordinator"


@dataclass
class _QueuedSdSync:
    cache_key: str
    operation: Callable[[], Awaitable[Any]]
    future: asyncio.Future[Any]
    foreground: bool
    started: bool = False


class SdPackSyncRecoveryPendingError(RuntimeError):
    """The previous firmware storage mutation has an unknown completion state."""


class SdPackSyncCoordinator:
    """Serialize firmware SD mutations while prioritizing lesson-start work."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        recovery_backoff_sec: float = SD_PACK_SYNC_RECOVERY_BACKOFF_SEC,
    ) -> None:
        self._foreground: deque[_QueuedSdSync] = deque()
        self._background: deque[_QueuedSdSync] = deque()
        self._by_cache_key: dict[str, _QueuedSdSync] = {}
        self._worker: asyncio.Task[None] | None = None
        self._clock = clock
        self._recovery_backoff_sec = max(0.0, float(recovery_backoff_sec))
        self._uncertain_cache_key: str | None = None
        self._recover_after = 0.0

    async def request(
        self,
        cache_key: str,
        operation: Callable[[], Awaitable[Any]],
        *,
        foreground: bool = False,
    ) -> Any:
        self._check_recovery_admission(cache_key)
        queued = self._by_cache_key.get(cache_key)
        if queued is None:
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.add_done_callback(_consume_unobserved_exception)
            queued = _QueuedSdSync(cache_key, operation, future, foreground)
            self._by_cache_key[cache_key] = queued
            (self._foreground if foreground else self._background).append(queued)
        elif foreground and not queued.foreground and queued in self._background:
            self._background.remove(queued)
            queued.foreground = True
            self._foreground.append(queued)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
        try:
            return await asyncio.shield(queued.future)
        except asyncio.CancelledError:
            if not queued.started:
                self._remove_queued(queued)
            raise

    async def _run(self) -> None:
        while self._foreground or self._background:
            queued = (
                self._foreground.popleft()
                if self._foreground
                else self._background.popleft()
            )
            queued.started = True
            try:
                result = await queued.operation()
            except Exception as exc:
                if _sd_sync_completion_unknown(exc):
                    self._enter_recovery(queued.cache_key)
                if not queued.future.done():
                    queued.future.set_exception(exc)
            else:
                if _sd_sync_storage_busy(result):
                    self._enter_recovery(queued.cache_key)
                elif self._uncertain_cache_key == queued.cache_key:
                    self._uncertain_cache_key = None
                    self._recover_after = 0.0
                if not queued.future.done():
                    queued.future.set_result(result)
            finally:
                if self._by_cache_key.get(queued.cache_key) is queued:
                    self._by_cache_key.pop(queued.cache_key, None)

    def _remove_queued(self, queued: _QueuedSdSync) -> None:
        for queue in (self._foreground, self._background):
            with suppress(ValueError):
                queue.remove(queued)
        if self._by_cache_key.get(queued.cache_key) is queued:
            self._by_cache_key.pop(queued.cache_key, None)
        if not queued.future.done():
            queued.future.cancel()

    def _check_recovery_admission(self, cache_key: str) -> None:
        uncertain = self._uncertain_cache_key
        if uncertain is None:
            return
        if self._clock() < self._recover_after or cache_key != uncertain:
            raise SdPackSyncRecoveryPendingError("sd_sync_recovery_pending")

    def _enter_recovery(self, cache_key: str) -> None:
        self._uncertain_cache_key = cache_key
        self._recover_after = self._clock() + self._recovery_backoff_sec
        pending_error = SdPackSyncRecoveryPendingError("sd_sync_recovery_pending")
        for queue in (self._foreground, self._background):
            while queue:
                pending = queue.popleft()
                if self._by_cache_key.get(pending.cache_key) is pending:
                    self._by_cache_key.pop(pending.cache_key, None)
                if not pending.future.done():
                    pending.future.set_exception(pending_error)


def _sd_sync_completion_unknown(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    message = str(exc).strip().lower().replace("-", "_").replace(" ", "_")
    return "storage_busy" in message


def _sd_sync_storage_busy(result: Any) -> bool:
    candidate = result
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError:
            return "storage_busy" in candidate.lower().replace("-", "_").replace(" ", "_")
    if not isinstance(candidate, dict):
        return False
    error_code = str(candidate.get("errorCode") or candidate.get("error") or "")
    normalized = error_code.lower().replace("-", "_").replace(" ", "_")
    return "storage_busy" in normalized


def _consume_unobserved_exception(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


def _sd_pack_sync_coordinator(conn: Any) -> SdPackSyncCoordinator:
    coordinator = getattr(conn, _COORDINATOR_ATTR, None)
    if coordinator is None:
        coordinator = SdPackSyncCoordinator()
        setattr(conn, _COORDINATOR_ATTR, coordinator)
    return coordinator


async def request_sd_pack_sync(
    conn: Any,
    cache_key: str,
    operation: Callable[[], Awaitable[Any]],
    *,
    foreground: bool = False,
) -> Any:
    return await _sd_pack_sync_coordinator(conn).request(
        cache_key,
        operation,
        foreground=foreground,
    )


def sd_pack_sync_timeout_sec(config: dict[str, Any], pack: dict[str, Any]) -> int:
    lesson_cfg = _lesson_config(config)
    floor = min(
        SD_PACK_SYNC_TIMEOUT_CEILING_SEC,
        _positive_float(
            lesson_cfg.get("sd_sync_timeout_floor_sec")
            or os.getenv("LESSON_SD_SYNC_TIMEOUT_SEC"),
            SD_PACK_SYNC_TIMEOUT_FLOOR_SEC,
        ),
    )
    ceiling = min(
        SD_PACK_SYNC_TIMEOUT_CEILING_SEC,
        max(
            floor,
            _positive_float(
                lesson_cfg.get("sd_sync_timeout_ceiling_sec")
                or os.getenv("LESSON_SD_SYNC_TIMEOUT_MAX_SEC"),
                SD_PACK_SYNC_TIMEOUT_CEILING_SEC,
            ),
        ),
    )
    assets = pack.get("assets") if isinstance(pack, dict) else None
    asset_list = assets if isinstance(assets, list) else []
    total_bytes = sum(
        max(0, int(asset.get("size") or 0))
        for asset in asset_list
        if isinstance(asset, dict)
    )
    estimated = (
        SD_PACK_SYNC_BASE_SEC
        + len(asset_list) * SD_PACK_SYNC_PER_ASSET_SEC
        + total_bytes / SD_PACK_SYNC_BYTES_PER_SEC
    )
    return int(min(ceiling, max(floor, math.ceil(estimated))))


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if math.isfinite(parsed) and parsed > 0 else float(default)


def cached_asset_packs(config: dict[str, Any]) -> Iterator[dict[str, Any]]:
    lesson_cfg = _lesson_config(config)
    pack_mount_root = lesson_cfg.get("asset_pack_mount_root")
    cache_root = Path(
        lesson_cfg.get("asset_cache_root") or pack_mount_root or DEFAULT_CACHE_ROOT
    )
    public_base = _lesson_asset_public_base_url(config)
    local_root = str(lesson_cfg.get("asset_pack_local_root") or DEFAULT_LOCAL_ROOT).rstrip("/")
    shared_store = None
    if pack_mount_root:
        mounted = Path(str(pack_mount_root)).resolve()
        shared_store = SharedAssetStore(mounted.parent, pack_root=mounted)
    if not cache_root.is_dir():
        return

    root = cache_root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        pack_dir = Path(dirpath)
        cache_key = pack_dir.relative_to(root).as_posix()
        asset_names = _asset_names(filenames)
        if "pack.json" not in filenames and not asset_names:
            continue
        if shared_store is not None and not shared_store.is_pack_ready(cache_key):
            continue
        if shared_store is not None and "pack.json" in filenames:
            rich_pack = _ready_rich_asset_pack(pack_dir, cache_key)
            if rich_pack is not None:
                yield rich_pack
            continue
        if not public_base:
            continue
        assets = []
        token = base64.urlsafe_b64encode(cache_key.encode("utf-8")).decode("ascii").rstrip("=")
        for name in asset_names:
            source_path = _download_source_path(pack_dir / name)
            url = f"{public_base}/tbot/lesson-assets/{token}/{quote(name, safe='')}"
            local_path = f"{local_root}/{cache_key}/{quote(name, safe='')}"
            assets.append(
                {
                    "key": name,
                    "path": name,
                    "url": url,
                    "onlineUrl": url,
                    "sha256": _sha256_file(source_path),
                    "size": source_path.stat().st_size,
                    "mediaType": "application/octet-stream",
                    "critical": True,
                    "state": "READY",
                    "checksumOk": True,
                    "sdPath": local_path,
                    "localPath": local_path,
                }
            )
        checksum = _manifest_checksum_from_cache_key(cache_key)
        canonical_checksum = checksum if _LOWER_SHA256_RE.fullmatch(checksum) else ""
        if assets:
            pack = {
                "assignmentVersion": 0,
                "lessonId": _lesson_id_from_cache_key(cache_key),
                "lessonVersion": _lesson_version_from_cache_key(cache_key),
                "manifestChecksum": canonical_checksum,
                "cacheKey": cache_key,
                "localRoot": f"{local_root}/{cache_key}",
                "ready": True,
                "assets": assets,
            }
            if checksum and not canonical_checksum:
                pack["historicalManifestChecksum"] = checksum
            yield pack


async def sync_cached_lesson_assets_to_sd(
    conn: Any,
    *,
    only_cache_keys: set | None = None,
    busy_check: Callable[[], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    poll_interval: float = 0.1,
) -> dict[str, Any]:
    config = getattr(conn, "config", {}) or {}
    if not _sd_pack_enabled(config):
        return {"skipped": "sd_pack_disabled"}
    mcp_client = getattr(conn, "mcp_client", None)
    if mcp_client is None:
        return {"skipped": "no_mcp_client"}
    is_ready = getattr(mcp_client, "is_ready", None)
    if callable(is_ready) and not await is_ready():
        return {"skipped": "mcp_not_ready"}
    synced = failed = 0
    results_by_cache_key: dict[str, dict[str, Any]] = {}
    is_busy = busy_check or getattr(conn, "is_realtime_busy", None) or (lambda: False)
    pause = sleep or asyncio.sleep
    packs = list(cached_asset_packs(config))
    if only_cache_keys is not None and len(only_cache_keys) > 0:
        packs = [
            pack
            for pack in packs
            if str(pack.get("cacheKey") or "") in only_cache_keys
        ]
    for pack in packs:
        while is_busy():
            await pause(poll_interval)
        try:
            result = await _call_sd_pack_sync_with_voice_guard(
                conn,
                mcp_client,
                pack,
                busy_check=is_busy,
                sleep=pause,
                poll_interval=poll_interval,
            )
            cache_key = str(pack.get("cacheKey") or "")
            normalized = normalize_firmware_sync_result(cache_key, result, pack)
            if normalized["ready"]:
                synced += 1
            else:
                failed += 1
            results_by_cache_key[cache_key] = normalized
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            cache_key = str(pack.get("cacheKey") or "")
            from core.api.device_mcp_admin_handler import (
                MCPLessonAssetSyncInvalidRequestError,
                MCPLessonAssetSyncStorageBusyError,
            )

            error_code = (
                "invalid_request"
                if isinstance(exc, MCPLessonAssetSyncInvalidRequestError)
                else "storage_busy"
                if isinstance(exc, MCPLessonAssetSyncStorageBusyError)
                else "sd_sync_recovery_pending"
                if isinstance(exc, SdPackSyncRecoveryPendingError)
                else "sd_sync_result_unknown"
                if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                else type(exc).__name__
            )
            results_by_cache_key[cache_key] = normalize_firmware_sync_result(
                cache_key,
                {
                    "ready": False,
                    "downloadedCount": 0,
                    "skippedCount": 0,
                    "failedCount": 1,
                    "criticalFailedCount": 1,
                    "errorCode": error_code,
                },
            )
            if _is_unknown_sd_pack_sync_tool_error(exc):
                _log(conn, "warning", "cached SD pack sync unsupported: firmware missing self.lesson_assets.sync_to_sd")
                return {
                    "packs": len(packs),
                    "synced": synced,
                    "failed": failed,
                    "unsupported": True,
                    "resultsByCacheKey": results_by_cache_key,
                }
            _log(conn, "warning", f"cached SD pack sync failed cache_key={pack.get('cacheKey')} error={type(exc).__name__}")
        await asyncio.sleep(0)
    _log(conn, "info", f"cached SD pack sync complete packs={len(packs)} synced={synced} failed={failed}")
    return {
        "packs": len(packs),
        "synced": synced,
        "failed": failed,
        "resultsByCacheKey": results_by_cache_key,
    }


async def _call_sd_pack_sync_with_voice_guard(
    conn: Any,
    mcp_client: Any,
    pack: dict[str, Any],
    *,
    busy_check: Callable[[], bool],
    sleep: Callable[[float], Awaitable[None]],
    poll_interval: float,
) -> Any:
    """Wait for voice before admission; never cancel a dispatched device call."""
    while busy_check():
        await sleep(poll_interval)
    cache_key = str(pack.get("cacheKey") or "")
    return await request_sd_pack_sync(
        conn,
        cache_key,
        lambda: call_sd_pack_sync_tool(conn, mcp_client, pack),
    )


async def call_sd_pack_sync_tool(conn: Any, mcp_client: Any, pack: dict[str, Any]) -> Any:
    from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

    mcp_pack = build_firmware_sync_pack(pack)

    return await _call_raw_mcp_tool(
        conn,
        mcp_client,
        SD_PACK_SYNC_TOOL,
        {"assetPack": mcp_pack},
        timeout=sd_pack_sync_timeout_sec(getattr(conn, "config", {}) or {}, mcp_pack),
    )


def _asset_names(filenames: list[str]) -> list[str]:
    names = []
    for name in sorted(filenames):
        if (
            name.startswith(".")
            or name in {"READY", "pack.json"}
            or name.endswith(".part")
            or name.endswith(".render.jpg")
        ):
            continue
        names.append(name)
    return names

def _ready_rich_asset_pack(pack_dir: Path, cache_key: str) -> dict[str, Any] | None:
    try:
        manifest = json.loads((pack_dir / "pack.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("cacheKey") != cache_key:
        return None
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list) or not raw_assets:
        return None
    assets = []
    seen: set[str] = set()
    for item in raw_assets:
        asset = _rich_asset_record(item, pack_dir, cache_key)
        if asset is None or asset["key"] in seen:
            return None
        seen.add(asset["key"])
        assets.append(asset)
    checksum = manifest.get("manifestChecksum")
    if not isinstance(checksum, str) or _LOWER_SHA256_RE.fullmatch(checksum) is None:
        return None
    return {
        "assignmentVersion": _safe_int(manifest.get("assignmentVersion"), 0),
        "lessonId": str(manifest.get("lessonId") or _lesson_id_from_cache_key(cache_key)),
        "lessonVersion": _safe_int(
            manifest.get("lessonVersion"),
            _lesson_version_from_cache_key(cache_key),
        ),
        "manifestChecksum": checksum,
        "cacheKey": cache_key,
        "localRoot": f"/sdcard/tbot/lesson-assets/{cache_key}",
        "ready": True,
        "assets": sorted(assets, key=lambda asset: asset["key"]),
    }

def _rich_asset_record(item: Any, pack_dir: Path, cache_key: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    key = item.get("key")
    sha256 = item.get("sha256")
    size = item.get("size")
    media_type = item.get("mediaType")
    critical = item.get("critical")
    online_url = item.get("onlineUrl")
    sd_path = item.get("sdPath")
    if (
        not isinstance(key, str)
        or not key
        or not isinstance(sha256, str)
        or _LOWER_SHA256_RE.fullmatch(sha256) is None
        or type(size) is not int
        or size < 0
        or not isinstance(media_type, str)
        or not media_type.strip()
        or type(critical) is not bool
        or not isinstance(online_url, str)
        or not online_url
        or not isinstance(sd_path, str)
        or sd_path != f"/sdcard/tbot/lesson-assets/{cache_key}/{quote(key, safe='')}"
    ):
        return None
    source_path = pack_dir / quote(key, safe="")
    try:
        if (
            not source_path.is_file()
            or source_path.stat().st_size != size
            or _sha256_file(source_path) != sha256
        ):
            return None
    except OSError:
        return None
    rich_identity: dict[str, Any] = {}
    if media_type == TRGB_MEDIA_TYPE:
        try:
            rich_identity = validate_pathless_flattened_cinematic_cache_asset(dict(item))
        except FlattenedCinematicContractError:
            return None
    elif media_type == "video/mp4":
        try:
            if "derivativeId" in item or "phaseId" in item or "cueId" in item:
                rich_identity = validate_renderer_v4_flattened_mp4(dict(item))
            else:
                rich_identity = validate_renderer_v3_shared_mp4(dict(item))
        except FirmwareSyncPackError:
            return None
    return {
        "key": key,
        "path": key,
        "url": online_url,
        "onlineUrl": online_url,
        "sha256": sha256,
        "size": size,
        "mediaType": media_type,
        "critical": critical,
        "state": "READY",
        "checksumOk": True,
        "sdPath": sd_path,
        "localPath": sd_path,
        **rich_identity,
    }

def _safe_int(value: Any, default: int) -> int:
    return value if type(value) is int and value >= 0 else default


def _download_source_path(path: Path) -> Path:
    render_safe = Path(f"{path}.render.jpg")
    if render_safe.is_file():
        return render_safe
    return path


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(64 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _sd_pack_enabled(config: dict[str, Any]) -> bool:
    lesson_cfg = _lesson_config(config)
    mode = str(lesson_cfg.get("asset_delivery_mode") or "").strip().lower()
    return mode == "sd_pack" or lesson_cfg.get("sd_asset_pack_enabled") is True


def _lesson_config(config: dict[str, Any]) -> dict[str, Any]:
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    return lesson_cfg if isinstance(lesson_cfg, dict) else {}


def _server_config(config: dict[str, Any]) -> dict[str, Any]:
    server_cfg = config.get("server", {}) if isinstance(config, dict) else {}
    return server_cfg if isinstance(server_cfg, dict) else {}


def _lesson_asset_public_base_url(config: dict[str, Any]) -> str:
    lesson_cfg = _lesson_config(config)
    server_cfg = _server_config(config)
    explicit = (
        lesson_cfg.get("asset_public_base_url")
        or lesson_cfg.get("asset_public_base")
        or server_cfg.get("asset_public_base_url")
    )
    if explicit:
        return str(explicit).rstrip("/")
    vision_url = get_vision_url(config)
    if vision_url and "/mcp/vision/explain" in vision_url:
        return vision_url.replace("/mcp/vision/explain", "").rstrip("/")
    return ""


def _lesson_id_from_cache_key(cache_key: str) -> str:
    return cache_key.split("/", 1)[0]


def _lesson_version_from_cache_key(cache_key: str) -> int:
    leaf = cache_key.rsplit("/", 1)[-1]
    if not leaf.startswith("v"):
        return 0
    digits = []
    for char in leaf[1:]:
        if not char.isdigit():
            break
        digits.append(char)
    return int("".join(digits) or 0)


def _manifest_checksum_from_cache_key(cache_key: str) -> str:
    leaf = cache_key.rsplit("/", 1)[-1]
    if "-" not in leaf:
        return ""
    return leaf.split("-", 1)[1]


def _sync_result_ready(result: Any, requested_pack: dict[str, Any]) -> bool:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return False
    if not isinstance(result, dict):
        return False
    assets = requested_pack.get("assets")
    expected_cache_key = requested_pack.get("cacheKey")
    expected_checksum = requested_pack.get("manifestChecksum")
    if (
        not isinstance(assets, list)
        or not isinstance(expected_cache_key, str)
        or not expected_cache_key
        or not isinstance(expected_checksum, str)
        or not expected_checksum
    ):
        return False
    if result.get("ready") is not True or result.get("cacheKey") != expected_cache_key:
        return False
    response_checksums = [
        result[key]
        for key in ("manifestChecksum", "packChecksum")
        if key in result
    ]
    if not response_checksums or any(
        value != expected_checksum for value in response_checksums
    ):
        return False
    counts = []
    for key in ("downloadedCount", "skippedCount", "reusedCount", "failedCount"):
        value = result.get(key, 0) if key == "reusedCount" else result.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return False
        counts.append(value)
    return counts[3] == 0 and sum(counts) == len(assets)


def normalize_firmware_sync_result(
    cache_key: str,
    result: Any,
    requested_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"ready": False, "failedCount": 1, "criticalFailedCount": 1}
    if not isinstance(result, dict):
        result = {"ready": False, "failedCount": 1, "criticalFailedCount": 1}
    critical_failed = _bounded_count(result.get("criticalFailedCount"))
    failed = _bounded_count(result.get("failedCount"))
    ready = (
        _sync_result_ready(result, requested_pack)
        if requested_pack is not None
        else bool(result.get("ready", critical_failed == 0)) and critical_failed == 0
    )
    body = {
        "cacheKey": str(cache_key or "").strip(),
        "downloadedCount": _bounded_count(
            result.get("downloadedCount", 1 if ready else 0)
        ),
        "skippedCount": _bounded_count(result.get("skippedCount")),
        "reusedCount": _bounded_count(result.get("reusedCount")),
        "failedCount": failed,
        "criticalFailedCount": critical_failed,
        "ready": ready,
    }
    error_code = _stable_error_code(result.get("errorCode"))
    if error_code:
        body["errorCode"] = error_code
    return body

def _bounded_count(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(parsed, 1_000_000))

def normalize_lesson_sd_error_code(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = []
    in_separator_run = False
    for ch in raw.lower():
        if ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch == "_":
            normalized.append(ch)
            in_separator_run = False
            continue
        if not in_separator_run:
            normalized.append("_")
            in_separator_run = True
    while normalized and not (
        ("a" <= normalized[0] <= "z") or ("0" <= normalized[0] <= "9")
    ):
        normalized.pop(0)
    if not any(("a" <= ch <= "z") or ("0" <= ch <= "9") for ch in normalized):
        return ""
    return "".join(normalized)[:_ERROR_CODE_MAX_LEN]

def _stable_error_code(value: Any) -> str:
    return normalize_lesson_sd_error_code(value)

def _is_unknown_sd_pack_sync_tool_error(exc: Exception) -> bool:
    message = str(exc)
    return "Unknown tool" in message and SD_PACK_SYNC_TOOL in message

def _log(conn: Any, level: str, message: str) -> None:
    logger = getattr(conn, "logger", None)
    if logger is None:
        return
    with suppress(Exception):
        getattr(logger.bind(tag="LessonSdPackSync"), level)(message)
