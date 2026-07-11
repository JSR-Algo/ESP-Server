"""Fan-out cached lesson SD packs to online robots; queue offline targets.

Admin/backend publishes lesson bytes into the ESP asset cache, then calls the
internal fan-out API. Online devices receive ``self.lesson_assets.sync_to_sd``
immediately. Offline selected devices are marked pending and drained when they
reconnect and MCP becomes ready.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from core.lesson.sd_pack_sync import (
    cached_asset_packs,
    sync_cached_lesson_assets_to_sd,
)

logger = logging.getLogger(__name__)

# device_id -> set of cache keys; empty set means "all packs currently known".
_PENDING: Dict[str, Set[str]] = {}
_PENDING_LOCK = asyncio.Lock()

ALL_PACKS_MARKER = "*"


def pending_snapshot() -> Dict[str, List[str]]:
    return {
        device_id: sorted(keys) if keys else [ALL_PACKS_MARKER]
        for device_id, keys in sorted(_PENDING.items())
    }


def clear_pending_for_tests() -> None:
    _PENDING.clear()


async def mark_pending(device_id: str, cache_keys: Optional[Iterable[str]] = None) -> None:
    device_id = str(device_id or "").strip()
    if not device_id:
        return
    async with _PENDING_LOCK:
        if cache_keys is None:
            _PENDING[device_id] = set()
            return
        keys = {str(k).strip() for k in cache_keys if str(k).strip()}
        if not keys:
            _PENDING[device_id] = set()
            return
        existing = _PENDING.get(device_id)
        if existing is not None and len(existing) == 0:
            # Already queued for all packs.
            return
        if existing is None:
            _PENDING[device_id] = set(keys)
        else:
            existing.update(keys)


async def pop_pending(device_id: str) -> Optional[Set[str]]:
    """Return pending cache keys for device (empty set => all packs) or None."""
    device_id = str(device_id or "").strip()
    if not device_id:
        return None
    async with _PENDING_LOCK:
        return _PENDING.pop(device_id, None)


def _filter_packs(
    packs: List[Dict[str, Any]],
    *,
    lesson_id: Optional[str] = None,
    cache_key: Optional[str] = None,
    only_cache_keys: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    filtered = list(packs)
    if lesson_id:
        lid = str(lesson_id).strip()
        filtered = [
            p
            for p in filtered
            if str(p.get("lessonId") or "") == lid
            or str(p.get("cacheKey") or "").startswith(lid + "/")
            or str(p.get("cacheKey") or "").startswith(lid)
        ]
    if cache_key:
        ck = str(cache_key).strip()
        filtered = [p for p in filtered if str(p.get("cacheKey") or "") == ck]
    if only_cache_keys is not None and len(only_cache_keys) > 0:
        filtered = [
            p for p in filtered if str(p.get("cacheKey") or "") in only_cache_keys
        ]
    return filtered


def _normalize_device_ids(raw: Any) -> Optional[List[str]]:
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple, set)):
        return None
    out = []
    for item in raw:
        value = str(item or "").strip()
        if value:
            out.append(value)
    return out or None


def _fanout_concurrency() -> int:
    """Limit concurrent MCP SD syncs so a large fleet does not stampede flash I/O."""
    import os

    raw = (os.environ.get("LESSON_SD_FANOUT_CONCURRENCY") or "4").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 4
    return max(1, min(value, 32))


async def fanout_sd_pack_sync(
    config: Dict[str, Any],
    connections: Dict[str, Any],
    *,
    lesson_id: Optional[str] = None,
    cache_key: Optional[str] = None,
    device_ids: Optional[Any] = None,
    queue_offline: bool = True,
) -> Dict[str, Any]:
    """Push cached packs to online devices; optionally queue offline/failed targets."""
    packs = _filter_packs(
        list(cached_asset_packs(config or {})),
        lesson_id=lesson_id,
        cache_key=cache_key,
    )
    pack_keys = [str(p.get("cacheKey") or "") for p in packs if p.get("cacheKey")]
    target_filter = _normalize_device_ids(device_ids)
    connections = connections or {}

    online_ids = sorted(str(k) for k in connections.keys() if k)
    if target_filter is not None:
        sync_ids = [d for d in target_filter if d in connections]
        offline_ids = [d for d in target_filter if d not in connections]
    else:
        sync_ids = online_ids
        offline_ids = []

    synced: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    queued: List[Dict[str, Any]] = []

    if not packs:
        return {
            "packs": 0,
            "packKeys": [],
            "onlineDeviceIds": online_ids,
            "synced": synced,
            "failed": failed,
            "queued": queued,
            "skipped": [{"reason": "no_matching_packs"}],
            "pending": pending_snapshot(),
        }

    only_keys = set(pack_keys) if (lesson_id or cache_key) else None
    sem = asyncio.Semaphore(_fanout_concurrency())

    async def _sync_one(device_id: str) -> Dict[str, Any]:
        conn = connections.get(device_id)
        if conn is None:
            return {"kind": "offline", "deviceId": device_id}
        async with sem:
            try:
                result = await sync_cached_lesson_assets_to_sd(
                    conn,
                    only_cache_keys=only_keys,
                )
            except Exception as exc:
                return {
                    "kind": "failed",
                    "entry": {
                        "deviceId": device_id,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    "retry": True,
                }

            if isinstance(result, dict) and result.get("skipped"):
                return {
                    "kind": "skipped",
                    "entry": {
                        "deviceId": device_id,
                        "ok": False,
                        "skipped": result.get("skipped"),
                        "result": result,
                    },
                    # Still retry on reconnect — skipped often means MCP not ready.
                    "retry": True,
                }

            packs_total = int((result or {}).get("packs") or 0)
            failed_count = int((result or {}).get("failed") or 0)
            synced_count = int((result or {}).get("synced") or 0)
            entry = {
                "deviceId": device_id,
                "ok": failed_count == 0 and packs_total > 0,
                "result": result,
            }
            if entry["ok"] or synced_count > 0:
                await pop_pending(device_id)
                return {"kind": "synced", "entry": entry, "retry": False}
            return {"kind": "failed", "entry": entry, "retry": True}

    outcomes = await asyncio.gather(*[_sync_one(device_id) for device_id in sync_ids])
    retry_ids: List[str] = []
    for outcome in outcomes:
        kind = outcome.get("kind")
        if kind == "offline":
            offline_ids.append(str(outcome.get("deviceId") or ""))
            continue
        entry = outcome.get("entry")
        if kind == "synced" and isinstance(entry, dict):
            synced.append(entry)
        elif kind == "skipped" and isinstance(entry, dict):
            skipped.append(entry)
        elif kind == "failed" and isinstance(entry, dict):
            failed.append(entry)
        if outcome.get("retry"):
            device_id = str((entry or {}).get("deviceId") or "")
            if device_id:
                retry_ids.append(device_id)

    if queue_offline:
        # Offline selected devices + online failures (SD I/O flake) retry on reconnect.
        offline_set = {d for d in offline_ids if d}
        for device_id in list(dict.fromkeys([*offline_ids, *retry_ids])):
            if not device_id:
                continue
            keys = set(pack_keys) if (lesson_id or cache_key) else None
            await mark_pending(device_id, keys)
            queued.append(
                {
                    "deviceId": device_id,
                    "cacheKeys": sorted(pack_keys) if keys is not None else [ALL_PACKS_MARKER],
                    "reason": "offline" if device_id in offline_set else "retry-after-fail",
                }
            )

    return {
        "packs": len(packs),
        "packKeys": pack_keys,
        "onlineDeviceIds": online_ids,
        "synced": synced,
        "failed": failed,
        "queued": queued,
        "skipped": skipped,
        "pending": pending_snapshot(),
    }


async def drain_pending_for_connection(conn: Any) -> Optional[Dict[str, Any]]:
    """If this device was queued offline, run SD sync now (usually on MCP ready)."""
    device_id = str(getattr(conn, "device_id", "") or "").strip()
    if not device_id:
        return None
    pending = await pop_pending(device_id)
    if pending is None:
        return None
    only = None if len(pending) == 0 else pending
    logger.info(
        "LessonSdFanout drain_pending device_id=%s keys=%s",
        device_id,
        sorted(pending) if pending else [ALL_PACKS_MARKER],
    )
    return await sync_cached_lesson_assets_to_sd(conn, only_cache_keys=only)
