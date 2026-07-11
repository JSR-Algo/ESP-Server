"""Background SD-pack sync for already cached lesson assets."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterator, Optional, Set
from urllib.parse import quote

from core.lesson.shared_asset_store import SharedAssetStore
from core.utils.util import get_vision_url

SD_PACK_SYNC_TOOL = "self.lesson_assets.sync_to_sd"
SD_PACK_SYNC_TIMEOUT_SEC = 120
DEFAULT_CACHE_ROOT = "data/lesson_assets"
DEFAULT_LOCAL_ROOT = "sd://tbot/lesson-assets"


def cached_asset_packs(config: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    lesson_cfg = _lesson_config(config)
    cache_root = Path(lesson_cfg.get("asset_cache_root") or DEFAULT_CACHE_ROOT)
    public_base = _lesson_asset_public_base_url(config)
    local_root = str(lesson_cfg.get("asset_pack_local_root") or DEFAULT_LOCAL_ROOT).rstrip("/")
    pack_mount_root = lesson_cfg.get("asset_pack_mount_root")
    shared_store = None
    if pack_mount_root:
        mounted = Path(str(pack_mount_root)).resolve()
        shared_store = SharedAssetStore(mounted.parent, pack_root=mounted)
    if not public_base or not cache_root.is_dir():
        return

    root = cache_root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        asset_names = _asset_names(filenames)
        if not asset_names:
            continue
        pack_dir = Path(dirpath)
        cache_key = pack_dir.relative_to(root).as_posix()
        if shared_store is not None and not shared_store.is_pack_ready(cache_key):
            continue
        assets = []
        token = base64.urlsafe_b64encode(cache_key.encode("utf-8")).decode("ascii").rstrip("=")
        for name in asset_names:
            source_path = _download_source_path(pack_dir / name)
            assets.append(
                {
                    "key": name,
                    "path": name,
                    "url": f"{public_base}/tbot/lesson-assets/{token}/{quote(name, safe='')}",
                    "sha256": _sha256_file(source_path),
                    "size": source_path.stat().st_size,
                    "critical": True,
                    "state": "READY",
                    "checksumOk": True,
                    "localPath": f"{local_root}/{cache_key}/{quote(name, safe='')}",
                }
            )
        if assets:
            yield {
                "assignmentVersion": 0,
                "lessonId": _lesson_id_from_cache_key(cache_key),
                "lessonVersion": _lesson_version_from_cache_key(cache_key),
                "manifestChecksum": _manifest_checksum_from_cache_key(cache_key),
                "cacheKey": cache_key,
                "localRoot": f"{local_root}/{cache_key}",
                "ready": True,
                "assets": assets,
            }


async def sync_cached_lesson_assets_to_sd(
    conn: Any,
    *,
    only_cache_keys: Optional[set] = None,
    busy_check: Optional[Callable[[], bool]] = None,
    sleep: Optional[Callable[[float], Awaitable[None]]] = None,
) -> Dict[str, Any]:
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
            await pause(0.1)
        try:
            result = await call_sd_pack_sync_tool(conn, mcp_client, pack)
            if _sync_result_ready(result):
                synced += 1
            else:
                failed += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed += 1
            if _is_unknown_sd_pack_sync_tool_error(exc):
                _log(conn, "warning", "cached SD pack sync unsupported: firmware missing self.lesson_assets.sync_to_sd")
                return {"packs": len(packs), "synced": synced, "failed": failed, "unsupported": True}
            _log(conn, "warning", f"cached SD pack sync failed cache_key={pack.get('cacheKey')} error={type(exc).__name__}")
        await asyncio.sleep(0)
    _log(conn, "info", f"cached SD pack sync complete packs={len(packs)} synced={synced} failed={failed}")
    return {"packs": len(packs), "synced": synced, "failed": failed}


async def call_sd_pack_sync_tool(conn: Any, mcp_client: Any, pack: Dict[str, Any]) -> Any:
    from core.api.device_mcp_admin_handler import _call_raw_mcp_tool

    return await _call_raw_mcp_tool(
        conn,
        mcp_client,
        SD_PACK_SYNC_TOOL,
        {"assetPack": pack},
        timeout=SD_PACK_SYNC_TIMEOUT_SEC,
    )


def _asset_names(filenames: list[str]) -> list[str]:
    names = []
    for name in sorted(filenames):
        if name.startswith(".") or name.endswith(".part") or name.endswith(".render.jpg"):
            continue
        names.append(name)
    return names


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


def _sd_pack_enabled(config: Dict[str, Any]) -> bool:
    lesson_cfg = _lesson_config(config)
    mode = str(lesson_cfg.get("asset_delivery_mode") or "").strip().lower()
    return mode == "sd_pack" or lesson_cfg.get("sd_asset_pack_enabled") is True


def _lesson_config(config: Dict[str, Any]) -> Dict[str, Any]:
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    return lesson_cfg if isinstance(lesson_cfg, dict) else {}


def _server_config(config: Dict[str, Any]) -> Dict[str, Any]:
    server_cfg = config.get("server", {}) if isinstance(config, dict) else {}
    return server_cfg if isinstance(server_cfg, dict) else {}


def _lesson_asset_public_base_url(config: Dict[str, Any]) -> str:
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
    failed = result.get("failedCount")
    return not (isinstance(failed, int) and failed > 0)


def _is_unknown_sd_pack_sync_tool_error(exc: Exception) -> bool:
    message = str(exc)
    return "Unknown tool" in message and SD_PACK_SYNC_TOOL in message

def _log(conn: Any, level: str, message: str) -> None:
    logger = getattr(conn, "logger", None)
    if logger is None:
        return
    try:
        getattr(logger.bind(tag="LessonSdPackSync"), level)(message)
    except Exception:
        pass
