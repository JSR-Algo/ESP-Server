"""S9 — lesson progress forwarder (THREE STREAMS NEVER MERGED, plan §6.4.2).

Runs on its OWN ``asyncio`` queue + worker task. It MUST NOT reuse
``conn.report_queue`` / ``_report_worker`` (the chat-history thread = stream 1) or
device telemetry (stream 3). Lesson progress is stream 2.

It batches events per ``(assignmentId, sessionId)`` and POSTs them to
``/v1/devices/:deviceId/lesson-events`` via ``config.manage_api_client``, which
owns the single ``result -> outcome`` rename + the ``detail.utterance`` strip.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# Module ref (NOT a from-import) so ``post_lesson_event`` is resolved as an
# attribute at call time: tests inject a fake ``post_fn`` or monkeypatch the
# module attribute, and the real coroutine is bound in production. (conftest does
# NOT stub config.manage_api_client — it only filters warnings.)
from config import manage_api_client as _backend_api

TAG = "LessonForwarder"

_PENDING_TERMINAL_BATCHES: Dict[Tuple[str, str], Dict[str, Any]] = {}

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore


class LessonEventForwarder:
    """Dedicated outbound path for lesson progress events.

    ``post_fn`` is injectable for tests; it defaults to
    ``manage_api_client.post_lesson_event`` bound to a private ``httpx`` client.
    """

    def __init__(
        self,
        *,
        device_id: str,
        base_url: str,
        token: Optional[str] = None,
        post_fn: Optional[Callable[..., Awaitable[Optional[Dict[str, Any]]]]] = None,
        client: Any = None,
        logger: Any = None,
        retry_backoff_sec: float = 1.0,
        retry_backoff_multiplier: float = 2.0,
        max_reenqueue_attempts: int = 2,
        dead_letter_limit: int = 100,
    ) -> None:
        self.device_id = device_id
        self.base_url = base_url
        self.token = token
        self._post_fn = post_fn
        self._client = client
        self._owns_client = False
        self._logger = logger
        self.retry_backoff_sec = max(0.0, float(retry_backoff_sec))
        self.retry_backoff_multiplier = max(1.0, float(retry_backoff_multiplier))
        self.max_reenqueue_attempts = max(0, int(max_reenqueue_attempts))
        self.dead_letter_limit = max(1, int(dead_letter_limit))
        self.dead_letters: List[Dict[str, Any]] = []
        self.dropped_events_total = 0
        self.pending_terminal_batch: Optional[Dict[str, Any]] = None
        self._queue: "asyncio.Queue[Optional[Tuple[Dict[str, Any], int]]]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._closed = False

    def enqueue(self, batch: Dict[str, Any]) -> None:
        """Non-blocking; the worker drains and POSTs. Started lazily on first use."""
        if self._closed:
            return
        if self._is_terminal_batch(batch):
            self.pending_terminal_batch = batch
            _store_pending_terminal_batch(self.device_id, batch)
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())
        self._queue.put_nowait((batch, 0))

    async def _run(self) -> None:
        while True:
            batch = await self._queue.get()
            if batch is None:  # poison pill
                self._queue.task_done()
                break
            item, attempt = batch
            try:
                await self._post(item)
                if self.pending_terminal_batch is item:
                    self.pending_terminal_batch = None
                _clear_pending_terminal_batch(self.device_id, item)
            except Exception as exc:  # never let a forward failure kill the worker
                await self._handle_post_failure(item, attempt, exc)
            finally:
                self._queue.task_done()

    async def _handle_post_failure(
        self, batch: Dict[str, Any], attempt: int, exc: Exception
    ) -> None:
        if attempt < self.max_reenqueue_attempts and self._is_retryable(exc):
            delay = self.retry_backoff_sec * (self.retry_backoff_multiplier ** attempt)
            if delay > 0:
                await asyncio.sleep(delay)
            self._queue.put_nowait((batch, attempt + 1))
            self._log(
                "warning",
                "lesson-events POST failed; re-enqueued "
                f"attempt={attempt + 1}/{self.max_reenqueue_attempts}: {type(exc).__name__}",
            )
            return

        self._dead_letter(batch)
        self._log("warning", f"lesson-events POST dead-lettered: {type(exc).__name__}")

    async def _post(self, batch: Dict[str, Any]) -> None:
        post_fn = self._post_fn or _backend_api.post_lesson_event
        if self._post_fn is None:
            await self._ensure_client()
        await post_fn(self._client, self.base_url, self.device_id, batch, token=self.token)

    async def replay_pending_terminal_event(self) -> bool:
        """Re-send a terminal lifecycle event until a backend 2xx clears it.

        The backend lifecycle dedup key is ``(assignment_id,event_type)`` for
        null-sequence rows, so at-least-once replay is safe. This method is called
        from pull-on-connect when the runtime already reached a terminal state but
        the original POST never got a 2xx.
        """
        batch = self.pending_terminal_batch
        if batch is None:
            return False
        try:
            await self._post(batch)
        except Exception as exc:  # keep pending for the next reconnect
            self._log("warning", f"lesson terminal replay failed: {type(exc).__name__}")
            return False
        if self.pending_terminal_batch is batch:
            self.pending_terminal_batch = None
        _clear_pending_terminal_batch(self.device_id, batch)
        return True

    def _is_retryable(self, exc: Exception) -> bool:
        classifier = getattr(_backend_api, "_lesson_is_transient", None)
        if callable(classifier):
            try:
                return bool(classifier(exc))
            except Exception:
                return False
        return False

    def _dead_letter(self, batch: Dict[str, Any]) -> None:
        self.dead_letters.append(batch)
        if len(self.dead_letters) > self.dead_letter_limit:
            self.dead_letters = self.dead_letters[-self.dead_letter_limit :]
        events = batch.get("events")
        self.dropped_events_total += len(events) if isinstance(events, list) else 0

    @staticmethod
    def _is_terminal_batch(batch: Dict[str, Any]) -> bool:
        events = batch.get("events")
        if not isinstance(events, list):
            return False
        return any(
            isinstance(event, dict)
            and event.get("type") in {"lesson_completed", "lesson_failed", "lesson_cancelled"}
            for event in events
        )

    async def _ensure_client(self) -> None:
        if self._client is not None or httpx is None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_keepalive_connections=0),
            follow_redirects=True,
        )
        self._owns_client = True

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._worker is not None:
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._worker, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._worker.cancel()
            self._worker = None
        if self._client is not None and self._owns_client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None
            self._owns_client = False

    def _log(self, level: str, message: str) -> None:
        if self._logger is None:
            return
        try:
            getattr(self._logger.bind(tag=TAG), level)(message)
        except Exception:
            pass


def _pending_terminal_key(device_id: str, batch: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    assignment_id = batch.get("assignmentId")
    if not device_id or not isinstance(assignment_id, str) or not assignment_id:
        return None
    if not LessonEventForwarder._is_terminal_batch(batch):
        return None
    return (device_id, assignment_id)


def _store_pending_terminal_batch(device_id: str, batch: Dict[str, Any]) -> None:
    key = _pending_terminal_key(device_id, batch)
    if key is not None:
        _PENDING_TERMINAL_BATCHES[key] = batch


def _clear_pending_terminal_batch(device_id: str, batch: Dict[str, Any]) -> None:
    key = _pending_terminal_key(device_id, batch)
    if key is None:
        return
    stored = _PENDING_TERMINAL_BATCHES.get(key)
    if stored is batch or stored == batch:
        _PENDING_TERMINAL_BATCHES.pop(key, None)


async def replay_stored_terminal_event(
    *,
    device_id: str,
    assignment_id: str,
    base_url: str,
    token: Optional[str] = None,
    client: Any = None,
    post_fn: Optional[Callable[..., Awaitable[Optional[Dict[str, Any]]]]] = None,
    logger: Any = None,
) -> bool:
    """Replay a terminal lifecycle event left behind by a prior connection.

    The process-local store covers the common reconnect case after a backend hiccup:
    the old forwarder dead-lettered a terminal batch without a 2xx, then the device
    reconnects to the same ESP process before the course cursor advanced. Backend
    ingest dedups null-sequence lifecycle events by ``(assignment_id,event_type)``.
    """
    batch = _PENDING_TERMINAL_BATCHES.get((device_id, assignment_id))
    if batch is None:
        return False
    post = post_fn or _backend_api.post_lesson_event
    try:
        await post(client, base_url, device_id, batch, token=token)
    except Exception as exc:
        if logger is not None:
            try:
                logger.bind(tag=TAG).warning(
                    f"lesson terminal reconnect replay failed: {type(exc).__name__}"
                )
            except Exception:
                pass
        return False
    _clear_pending_terminal_batch(device_id, batch)
    return True
