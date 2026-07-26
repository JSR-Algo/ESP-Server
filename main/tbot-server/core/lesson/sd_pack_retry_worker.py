"""Retry pending lesson SD-pack fanout for robots that remain online."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress
from typing import Any

from config.device_token_client import resolve_device_identity
from core.lesson.sd_pack_fanout import drain_pending_for_connection

logger = logging.getLogger(__name__)


class LessonSdOnlineIndex:
    def __init__(self, *, api_base: str = "") -> None:
        self.api_base = str(api_base or "").rstrip("/")
        self._by_uuid: dict[str, Any] = {}

    def upsert(self, backend_device_id: str, conn: Any) -> None:
        backend_device_id = str(backend_device_id or "").strip()
        if not backend_device_id:
            return
        self._by_uuid[backend_device_id] = conn
        with suppress(Exception):
            conn._lesson_sd_backend_device_id = backend_device_id

    async def resolve_and_upsert(self, conn: Any, *, client: Any = None) -> str | None:
        cached = str(getattr(conn, "_lesson_sd_backend_device_id", "") or "").strip()
        if cached:
            self.upsert(cached, conn)
            return cached
        raw_device_id = str(getattr(conn, "device_id", "") or "").strip()
        if not raw_device_id:
            return None
        if not self.api_base:
            if _looks_like_mac(raw_device_id):
                return None
            self.upsert(raw_device_id, conn)
            return raw_device_id
        close_client = False
        if client is None:
            import httpx

            client = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
            close_client = True
        try:
            backend_device_id, _token = await resolve_device_identity(
                client, self.api_base, raw_device_id, logger=None
            )
        except Exception:
            return None
        finally:
            if close_client:
                await client.aclose()
        if not backend_device_id:
            return None
        self.upsert(str(backend_device_id), conn)
        return str(backend_device_id)

    def get(self, backend_device_id: str) -> Any:
        return self._by_uuid.get(str(backend_device_id or "").strip())

    def invalidate_connection(self, conn: Any) -> None:
        for key, current in list(self._by_uuid.items()):
            if current is conn:
                self._by_uuid.pop(key, None)
        with suppress(Exception):
            delattr(conn, "_lesson_sd_backend_device_id")


class LessonSdRetryWorker:
    def __init__(
        self,
        store: Any,
        online_index: LessonSdOnlineIndex,
        *,
        connections: Any = None,
        interval_sec: float = 5.0,
        batch_size: int | None = None,
    ) -> None:
        self.store = store
        self.online_index = online_index
        self.connections = connections
        self.interval_sec = max(0.1, float(interval_sec or 5.0))
        self.batch_size = batch_size if batch_size is not None else _retry_batch_size()
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> asyncio.Task | None:
        if self._task is not None and not self._task.done():
            return self._task
        self._running = True
        self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self) -> None:
        self._running = False
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self) -> None:
        while self._running:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log_iteration_failure(exc, self.interval_sec)
            await asyncio.sleep(self.interval_sec)

    async def run_once(self) -> dict[str, int]:
        try:
            return await self._run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log_iteration_failure(exc, self.interval_sec)
            return {"checked": 0, "retried": 0, "skippedOffline": 0}

    async def _run_once(self) -> dict[str, int]:
        claim_due = getattr(self.store, "claim_due", None)
        if callable(claim_due):
            due_ids = await claim_due(limit=self.batch_size)
        else:
            due_ids = await self.store.due(limit=self.batch_size)
        checked = retried = skipped = 0
        for backend_device_id in due_ids:
            checked += 1
            conn = self.online_index.get(backend_device_id)
            if conn is None or not self._is_still_online(conn):
                skipped += 1
                continue
            pending = await self.store.load(backend_device_id)
            if pending is None:
                continue
            try:
                await drain_pending_for_connection(
                    conn,
                    store=self.store,
                    pending=pending,
                    backend_device_id=backend_device_id,
                )
            except Exception:
                # drain_pending_for_connection owns pending clear/mark decisions
                # once invoked, including transfer exceptions and callback failures.
                continue
            retried += 1
        return {"checked": checked, "retried": retried, "skippedOffline": skipped}

    def _is_still_online(self, conn: Any) -> bool:
        if self.connections is None:
            return True
        values = getattr(self.connections, "values", None)
        if not callable(values):
            return True
        return any(current is conn for current in values())


def _retry_batch_size() -> int:
    try:
        value = int(os.getenv("LESSON_SD_RETRY_BATCH_SIZE", "25"))
    except ValueError:
        value = 25
    return max(1, min(value, 250))

def _log_iteration_failure(exc: Exception, interval_sec: float) -> None:
    logger.warning(
        "lesson_sd_retry_worker iteration_failed error=%s sleep_sec=%.3f",
        _stable_error_code(type(exc).__name__),
        max(0.0, float(interval_sec or 0.0)),
    )

def _stable_error_code(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "UNKNOWN"
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
