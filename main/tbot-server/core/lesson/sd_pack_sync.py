"""Background SD-pack sync for already cached lesson assets."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.lesson.sd_pack_mcp_payload import build_firmware_sync_pack
from core.lesson.shared_asset_store import SharedAssetStore
from core.utils.util import get_vision_url

SD_PACK_SYNC_TOOL = "self.lesson_assets.sync_to_sd"
SD_PACK_SYNC_TIMEOUT_SEC = 120
DEFAULT_CACHE_ROOT = "data/lesson_assets"
DEFAULT_LOCAL_ROOT = "sd://tbot/lesson-assets"
_LOWER_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ERROR_CODE_MAX_LEN = 64


def cached_asset_packs(config: dict[str, Any]) -> Iterator[dict[str, Any]]:
    lesson_cfg = _lesson_config(config)
    cache_root = Path(lesson_cfg.get("asset_cache_root") or DEFAULT_CACHE_ROOT)
    public_base = _lesson_asset_public_base_url(config)
    local_root = str(lesson_cfg.get("asset_pack_local_root") or DEFAULT_LOCAL_ROOT).rstrip("/")
    pack_mount_root = lesson_cfg.get("asset_pack_mount_root")
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
            if _sync_result_ready(result):
                synced += 1
            else:
                failed += 1
            results_by_cache_key[cache_key] = normalize_firmware_sync_result(
                cache_key, result
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            cache_key = str(pack.get("cacheKey") or "")
            results_by_cache_key[cache_key] = normalize_firmware_sync_result(
                cache_key,
                {
                    "ready": False,
                    "downloadedCount": 0,
                    "skippedCount": 0,
                    "failedCount": 1,
                    "criticalFailedCount": 1,
                    "errorCode": type(exc).__name__,
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
    """Cancel an idempotent pack sync when voice wins, then retry from READY state."""
    interval = max(0.001, float(poll_interval))
    while True:
        while busy_check():
            await sleep(poll_interval)
        transfer = asyncio.create_task(call_sd_pack_sync_tool(conn, mcp_client, pack))
        try:
            while not transfer.done():
                await asyncio.sleep(0)
                if busy_check():
                    transfer.cancel()
                    await asyncio.gather(transfer, return_exceptions=True)
                    break
                await asyncio.wait({transfer}, timeout=interval)
            else:
                return await transfer
        except asyncio.CancelledError:
            transfer.cancel()
            await asyncio.gather(transfer, return_exceptions=True)
            raise


async def call_sd_pack_sync_tool(conn: Any, mcp_client: Any, pack: dict[str, Any]) -> Any:
    from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

    mcp_pack = build_firmware_sync_pack(pack)

    return await _call_raw_mcp_tool(
        conn,
        mcp_client,
        SD_PACK_SYNC_TOOL,
        {"assetPack": mcp_pack},
        timeout=SD_PACK_SYNC_TIMEOUT_SEC,
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


def _sync_result_ready(result: Any) -> bool:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return False
    if not isinstance(result, dict):
        return False
    if result.get("ready") is False:
        return False
    return _bounded_count(result.get("criticalFailedCount")) == 0


def normalize_firmware_sync_result(cache_key: str, result: Any) -> dict[str, Any]:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            result = {"ready": False, "failedCount": 1, "criticalFailedCount": 1}
    if not isinstance(result, dict):
        result = {"ready": False, "failedCount": 1, "criticalFailedCount": 1}
    critical_failed = _bounded_count(result.get("criticalFailedCount"))
    failed = _bounded_count(result.get("failedCount"))
    ready = bool(result.get("ready", critical_failed == 0)) and critical_failed == 0
    body = {
        "cacheKey": str(cache_key or "").strip(),
        "downloadedCount": _bounded_count(
            result.get("downloadedCount", 1 if ready else 0)
        ),
        "skippedCount": _bounded_count(result.get("skippedCount")),
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
