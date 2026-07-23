"""Fan-out cached lesson SD packs to online robots with durable pending retry."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from contextlib import suppress
from typing import Any

import httpx

from config.manage_api_client import post_lesson_sd_sync_result
from core.lesson.sd_pack_pending_store import (
    LessonSdPendingStore,
    PendingLessonSdWork,
    create_lesson_sd_pending_store,
    normalize_cache_keys,
)
from core.lesson.sd_pack_sync import cached_asset_packs, sync_cached_lesson_assets_to_sd

logger = logging.getLogger(__name__)

ALL_PACKS_MARKER = "*"
_DEFAULT_STORE: LessonSdPendingStore | None = None


def get_pending_store() -> LessonSdPendingStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = create_lesson_sd_pending_store()
    return _DEFAULT_STORE


def set_pending_store_for_tests(store: LessonSdPendingStore) -> None:
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


def clear_pending_for_tests() -> None:
    global _DEFAULT_STORE
    _DEFAULT_STORE = None


async def pending_snapshot(
    store: LessonSdPendingStore | None = None,
) -> dict[str, PendingLessonSdWork]:
    return await (store or get_pending_store()).snapshot()


async def mark_pending(
    device_id: str,
    cache_keys: Iterable[str] | None = None,
    *,
    store: LessonSdPendingStore | None = None,
) -> None:
    await (store or get_pending_store()).mark(device_id, cache_keys)


async def pop_pending(
    device_id: str,
    *,
    store: LessonSdPendingStore | None = None,
) -> set[str] | None:
    """Compatibility helper: load then clear all currently pending keys."""
    selected = store or get_pending_store()
    pending = await selected.load(device_id)
    if pending is None:
        return None
    keys = set(pending["cacheKeys"])
    await selected.clear(device_id, keys)
    return keys


def _filter_packs(
    packs: list[dict[str, Any]],
    *,
    lesson_id: str | None = None,
    cache_key: str | None = None,
    only_cache_keys: set[str] | None = None,
) -> list[dict[str, Any]]:
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
        filtered = [p for p in filtered if str(p.get("cacheKey") or "") in only_cache_keys]
    return filtered


def _normalize_device_ids(raw: Any) -> list[str] | None:
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
    raw = (os.environ.get("LESSON_SD_FANOUT_CONCURRENCY") or "4").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 4
    return max(1, min(value, 32))


async def fanout_sd_pack_sync(
    config: dict[str, Any],
    connections: dict[str, Any],
    *,
    lesson_id: str | None = None,
    cache_key: str | None = None,
    device_ids: Any | None = None,
    queue_offline: bool = True,
    store: LessonSdPendingStore | None = None,
    online_index: Any = None,
) -> dict[str, Any]:
    """Push cached packs to online devices; queue offline/failed targets durably."""
    selected_store = store or get_pending_store()
    packs = _filter_packs(
        list(cached_asset_packs(config or {})),
        lesson_id=lesson_id,
        cache_key=cache_key,
    )
    pack_keys = [str(p.get("cacheKey") or "") for p in packs if p.get("cacheKey")]
    target_filter = _normalize_device_ids(device_ids)
    connections = connections or {}
    resolved_connections = await _resolve_online_connections(
        config or {}, connections, online_index=online_index
    )

    online_ids = sorted(str(k) for k in resolved_connections if k)
    if target_filter is not None:
        sync_ids = [d for d in target_filter if d in resolved_connections]
        offline_ids = [d for d in target_filter if d not in resolved_connections]
    else:
        sync_ids = online_ids
        offline_ids = []

    synced: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    queued: list[dict[str, Any]] = []

    if not packs:
        return {
            "packs": 0,
            "packKeys": [],
            "onlineDeviceIds": online_ids,
            "synced": synced,
            "failed": failed,
            "queued": queued,
            "skipped": [{"reason": "no_matching_packs"}],
            "pending": await pending_snapshot(selected_store),
        }

    only_keys = set(pack_keys) if (lesson_id or cache_key) else None
    sem = asyncio.Semaphore(_fanout_concurrency())

    async def _sync_one(device_id: str) -> dict[str, Any]:
        conn = resolved_connections.get(device_id)
        if conn is None:
            return {"kind": "offline", "deviceId": device_id}
        async with sem:
            try:
                result = await _sync_callback_and_update_pending(
                    conn,
                    config or {},
                    backend_device_id=device_id,
                    cache_keys=pack_keys,
                    only_cache_keys=only_keys,
                    store=selected_store,
                )
            except Exception as exc:
                return {
                    "kind": "failed",
                    "entry": {
                        "deviceId": device_id,
                        "ok": False,
                        "error": f"{type(exc).__name__}: {_stable_error_message(exc)}",
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
                    "retry": True,
                }

            packs_total = _nonnegative_int((result or {}).get("packs"))
            failed_count = _nonnegative_int((result or {}).get("failed"))
            synced_count = len((result or {}).get("clearedCacheKeys") or [])
            entry = {
                "deviceId": device_id,
                "ok": failed_count == 0 and packs_total > 0,
                "result": result,
            }
            if entry["ok"] or synced_count > 0:
                _log_outcome("sync_complete", device_id, pack_keys)
                return {"kind": "synced", "entry": entry, "retry": False}
            return {"kind": "failed", "entry": entry, "retry": True}

    outcomes = await asyncio.gather(*[_sync_one(device_id) for device_id in sync_ids])
    retry_ids: list[str] = []
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
        offline_set = {d for d in offline_ids if d}
        for device_id in list(dict.fromkeys([*offline_ids, *retry_ids])):
            if not device_id:
                continue
            keys = set(pack_keys) if (lesson_id or cache_key) else set(pack_keys)
            await selected_store.mark(device_id, keys)
            _log_outcome(
                "pending_offline" if device_id in offline_set else "retry_wait",
                device_id,
                sorted(keys),
            )
            queued.append(
                {
                    "deviceId": device_id,
                    "cacheKeys": sorted(keys) if keys else [ALL_PACKS_MARKER],
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
        "pending": await pending_snapshot(selected_store),
    }


async def drain_pending_for_connection(
    conn: Any,
    *,
    store: LessonSdPendingStore | None = None,
    pending: PendingLessonSdWork | None = None,
    backend_device_id: str | None = None,
) -> dict[str, Any] | None:
    """If this device has pending work, run SD sync then clear only after callback."""
    selected_store = store or get_pending_store()
    device_id = str(
        backend_device_id
        or getattr(conn, "_lesson_sd_backend_device_id", "")
        or getattr(conn, "device_id", "")
        or ""
    ).strip()
    if not device_id:
        return None
    pending = pending or await selected_store.load(device_id)
    if pending is None and backend_device_id is None:
        resolved = await _resolve_backend_device_id_for_conn(
            conn, getattr(conn, "config", {}) or {}
        )
        if resolved and resolved != device_id:
            device_id = resolved
            pending = await selected_store.load(device_id)
    if pending is None:
        return None
    keys = normalize_cache_keys(pending["cacheKeys"])
    only = set(keys) if keys else None
    logger.info(
        "LessonSdFanout drain_pending device_id=%s keys=%s",
        device_id,
        keys if keys else [ALL_PACKS_MARKER],
    )
    result = await _sync_callback_and_update_pending(
        conn,
        getattr(conn, "config", {}) or {},
        backend_device_id=device_id,
        cache_keys=keys,
        only_cache_keys=only,
        store=selected_store,
    )
    if result.get("clearedCacheKeys"):
        _log_outcome("sync_complete", device_id, keys)
    else:
        _log_outcome("retry_wait", device_id, keys)
    return result


async def _resolve_backend_device_id_for_conn(conn: Any, config: dict[str, Any]) -> str | None:
    cached = str(getattr(conn, "_lesson_sd_backend_device_id", "") or "").strip()
    if cached:
        return cached
    raw_device_id = str(getattr(conn, "device_id", "") or "").strip()
    if not raw_device_id:
        return None
    base_url = _backend_base_url(config)
    if not base_url:
        return None if _looks_like_mac(raw_device_id) else raw_device_id
    try:
        from config.device_token_client import resolve_device_identity

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            backend_device_id, _token = await resolve_device_identity(
                client, base_url, raw_device_id, logger=None
            )
    except Exception:
        return None if _looks_like_mac(raw_device_id) else raw_device_id
    if not backend_device_id:
        return None if _looks_like_mac(raw_device_id) else raw_device_id
    with suppress(Exception):
        conn._lesson_sd_backend_device_id = str(backend_device_id)
    return str(backend_device_id)


async def _resolve_online_connections(
    config: dict[str, Any],
    connections: dict[str, Any],
    *,
    online_index: Any = None,
) -> dict[str, Any]:
    from core.lesson.sd_pack_retry_worker import LessonSdOnlineIndex

    index = online_index or LessonSdOnlineIndex(api_base=_backend_base_url(config))
    resolved: dict[str, Any] = {}
    for _key, conn in connections.items():
        backend_id = await index.resolve_and_upsert(conn)
        if backend_id:
            resolved[str(backend_id)] = conn
    return resolved


async def _sync_callback_and_update_pending(
    conn: Any,
    config: dict[str, Any],
    *,
    backend_device_id: str,
    cache_keys: Iterable[str],
    only_cache_keys: set[str] | None,
    store: LessonSdPendingStore,
) -> dict[str, Any]:
    keys = normalize_cache_keys(cache_keys)
    try:
        result = await sync_cached_lesson_assets_to_sd(conn, only_cache_keys=only_cache_keys)
    except Exception:
        await store.mark(backend_device_id, keys)
        raise
    cleared, retained, errors = await _post_per_cache_results(
        config,
        backend_device_id=backend_device_id,
        cache_keys=keys,
        sync_result=result,
    )
    if cleared:
        await store.clear(backend_device_id, cleared, expected_cache_keys=keys)
    if retained:
        await store.mark(backend_device_id, retained)
    result["clearedCacheKeys"] = cleared
    result["retainedCacheKeys"] = retained
    if errors:
        raise errors[0]
    return result


async def _post_sync_results(
    config: dict[str, Any],
    *,
    backend_device_id: str,
    cache_keys: list[str],
    sync_result: dict[str, Any],
) -> None:
    _cleared, _retained, errors = await _post_per_cache_results(
        config,
        backend_device_id=backend_device_id,
        cache_keys=cache_keys,
        sync_result=sync_result,
    )
    if errors:
        raise errors[0]

async def _post_per_cache_results(
    config: dict[str, Any],
    *,
    backend_device_id: str,
    cache_keys: list[str],
    sync_result: dict[str, Any],
) -> tuple[list[str], list[str], list[Exception]]:
    cleared: list[str] = []
    retained: list[str] = []
    errors: list[Exception] = []
    per_cache = _results_by_cache_key(sync_result, cache_keys)
    for cache_key in cache_keys:
        result = _dto_from_cache_result(backend_device_id, cache_key, per_cache.get(cache_key))
        ready_for_clear = result["ready"] and result["criticalFailedCount"] == 0
        try:
            await _post_one_sync_result(config, result=result)
        except Exception as exc:
            retained.append(cache_key)
            errors.append(exc)
            continue
        if ready_for_clear:
            cleared.append(cache_key)
        else:
            retained.append(cache_key)
    return cleared, normalize_cache_keys(retained), errors

async def _post_one_sync_result(config: dict[str, Any], *, result: dict[str, Any]) -> None:
    base_url = _backend_base_url(config)
    mint_secret = os.getenv("TBOT_DEVICE_MINT_SECRET", "")
    if not base_url or not mint_secret:
        raise RuntimeError("lesson SD sync result callback not configured")
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        await post_lesson_sd_sync_result(
            client,
            base_url,
            result,
            mint_secret=mint_secret,
        )


def _backend_base_url(config: dict[str, Any]) -> str:
    lesson_cfg = config.get("lesson", {}) if isinstance(config, dict) else {}
    server_cfg = config.get("server", {}) if isinstance(config, dict) else {}
    if not isinstance(lesson_cfg, dict):
        lesson_cfg = {}
    if not isinstance(server_cfg, dict):
        server_cfg = {}
    return str(lesson_cfg.get("api_base") or server_cfg.get("api_url") or "").rstrip("/")


def _nonnegative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _stable_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return type(exc).__name__
    return message.splitlines()[0][:160]


def _results_by_cache_key(sync_result: dict[str, Any], cache_keys: list[str]) -> dict[str, Any]:
    raw = sync_result.get("resultsByCacheKey") if isinstance(sync_result, dict) else None
    if isinstance(raw, dict):
        return {str(key): value for key, value in raw.items()}
    return dict.fromkeys(cache_keys, sync_result)

def _dto_from_cache_result(
    backend_device_id: str,
    cache_key: str,
    result: Any,
) -> dict[str, Any]:
    if not isinstance(result, dict):
        result = {}
    critical_failed = _bounded_count(result.get("criticalFailedCount"))
    failed = _bounded_count(result.get("failedCount"))
    ready = bool(result.get("ready", critical_failed == 0)) and critical_failed == 0
    body = {
        "deviceId": str(backend_device_id or "").strip(),
        "cacheKey": str(cache_key or "").strip(),
        "downloadedCount": _bounded_count(result.get("downloadedCount")),
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

def _stable_error_code(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in raw)[:80]

def _looks_like_mac(value: str) -> bool:
    compact = str(value or "").strip().replace("-", ":")
    parts = compact.split(":")
    if len(parts) != 6:
        return False
    return all(
        len(part) == 2 and all(ch in "0123456789abcdefABCDEF" for ch in part)
        for part in parts
    )

def _log_outcome(outcome: str, backend_device_id: str, cache_keys: Iterable[str]) -> None:
    for cache_key in cache_keys:
        logger.info(
            "lesson_sd_fanout outcome=%s backendDeviceId=%s cacheKey=%s",
            outcome,
            backend_device_id,
            cache_key,
        )
