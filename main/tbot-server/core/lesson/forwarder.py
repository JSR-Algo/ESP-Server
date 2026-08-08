"""S9 — lesson progress forwarder (THREE STREAMS NEVER MERGED, plan §6.4.2).

Runs on its OWN ``asyncio`` queue + worker task. It MUST NOT reuse
``conn.report_queue`` / ``_report_worker`` (the chat-history thread = stream 1) or
device telemetry (stream 3). Lesson progress is stream 2.

It batches events per ``(assignmentId, sessionId)`` and POSTs them to
``/v1/devices/:deviceId/lesson-events`` via ``config.manage_api_client``, which
owns the single ``result -> outcome`` rename + COPPA child-speech stripping.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

# Module ref (NOT a from-import) so ``post_lesson_event`` is resolved as an
# attribute at call time: tests inject a fake ``post_fn`` or monkeypatch the
# module attribute, and the real coroutine is bound in production. (conftest does
# NOT stub config.manage_api_client — it only filters warnings.)
from config import manage_api_client as _backend_api
from core.lesson.log_context import with_lesson_log_context

TAG = "LessonForwarder"

_PENDING_TERMINAL_BATCHES: Dict[Tuple[str, str], Dict[str, Any]] = {}
_DEFAULT_TERMINAL_STORE: Any = None

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
        max_queue_size: int = 512,
        terminal_store: Any = None,
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
        self.max_queue_size = max(1, int(max_queue_size))
        self.dead_letters: List[Dict[str, Any]] = []
        self.dropped_events_total = 0
        self.pending_terminal_batch: Optional[Dict[str, Any]] = None
        self._started_batches: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._terminal_store = terminal_store or get_terminal_replay_store()
        self._queue: "asyncio.Queue[Optional[Tuple[Dict[str, Any], int]]]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._closed = False

    def enqueue(self, batch: Dict[str, Any]) -> None:
        """Non-blocking; the worker drains and POSTs. Started lazily on first use.

        The queue is bounded: a backend that stops answering must not let a long
        lesson grow this in memory without limit. Terminal lifecycle batches are
        always admitted — they are what the reconnect replay is built around —
        so only ordinary progress can be shed, and shedding is counted in
        ``dropped_events_total`` rather than being silent.
        """
        if self._closed:
            return
        terminal = self._is_terminal_batch(batch)
        if not terminal and self._queue.qsize() >= self.max_queue_size:
            self._dead_letter(batch)
            self._log(
                "warning",
                f"lesson-events queue full (max={self.max_queue_size}); batch dropped",
                batch,
            )
            return
        if self._is_started_batch(batch):
            self._remember_started_batch(batch)
        if terminal:
            batch = self._with_started_event_for_replay(batch)
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
                if self._is_terminal_batch(item):
                    await self._store_terminal_batch(item)
                await self._post(item)
                if self.pending_terminal_batch is item:
                    self.pending_terminal_batch = None
                await self._clear_terminal_batch(item)
            except Exception as exc:  # never let a forward failure kill the worker
                await self._handle_post_failure(item, attempt, exc)
            finally:
                self._queue.task_done()

    async def _handle_post_failure(
        self, batch: Dict[str, Any], attempt: int, exc: Exception
    ) -> None:
        detail = f"{type(exc).__name__}{self._failure_detail(exc)} {self._batch_events(batch)}"
        if attempt < self.max_reenqueue_attempts and self._is_retryable(exc):
            delay = self.retry_backoff_sec * (self.retry_backoff_multiplier ** attempt)
            if delay > 0:
                await asyncio.sleep(delay)
            self._queue.put_nowait((batch, attempt + 1))
            self._log(
                "warning",
                "lesson-events POST failed; re-enqueued "
                f"attempt={attempt + 1}/{self.max_reenqueue_attempts}: {detail}",
                batch,
            )
            return

        self._dead_letter(batch)
        self._log("warning", f"lesson-events POST dead-lettered: {detail}", batch)

    @staticmethod
    def _failure_detail(exc: Exception) -> str:
        """HTTP status + a short body snippet, when the exception carries a response.

        `type(exc).__name__` alone ("HTTPStatusError") says only that the backend said
        no — not which status, and not why. That is the difference between a retryable
        502 and a permanently rejected payload, and neither the operator nor
        `lesson_e2e_log_verify.py` could tell them apart from the log.
        """
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status is None:
            return ""
        body = ""
        try:
            body = (response.text or "")[:160].replace("\n", " ")
        except Exception:  # pragma: no cover - body may be unread/streamed
            body = ""
        return f" status={status}" + (f" body={body}" if body else "")

    @staticmethod
    def _batch_events(batch: Dict[str, Any]) -> str:
        """Name the events that were rejected — a batch may carry several."""
        events = batch.get("events")
        if not isinstance(events, list):
            return "events=?"
        names = [
            str(e.get("type") or e.get("event") or "lesson_event")
            for e in events
            if isinstance(e, dict)
        ]
        return "events=" + (",".join(names) if names else "none")

    async def _post(self, batch: Dict[str, Any]) -> None:
        post_fn = self._post_fn or _backend_api.post_lesson_event
        if self._post_fn is None:
            await self._ensure_client()
        await post_fn(self._client, self.base_url, self.device_id, batch, token=self.token)
        self._log_forwarded(batch)

    def _log_forwarded(self, batch: Dict[str, Any]) -> None:
        """Record every event a backend 2xx accepted.

        Failures were already logged; success was completely silent, which made the
        whole backend-forwarding leg unobservable: a lesson could post progress and
        completion, drive the assignment to COMPLETED, and leave no log evidence that
        it ever happened. `scripts/lesson_e2e_log_verify.py` verifies that leg purely
        from logs, so its lesson_progress_posted / lesson_completion_posted checkpoints
        could never pass for a simulated OR a live capture.

        The wording matches the checkpoint contract ("backend post <event> ...") so the
        evidence is machine-checkable rather than merely human-readable.
        """
        events = batch.get("events")
        if not isinstance(events, list):
            return
        assignment_id = batch.get("assignmentId") or ""
        session_id = batch.get("sessionId") or ""
        for event in events:
            if not isinstance(event, dict):
                continue
            name = event.get("type") or event.get("event") or "lesson_event"
            parts = [
                f"backend post {name}",
                f"assignmentId={assignment_id}",
                f"sessionId={session_id}",
            ]
            for key in ("stepId", "event", "result", "outcome"):
                value = event.get(key)
                if value is not None:
                    parts.append(f"{key}={value}")
            parts.append("persisted=true")
            self._log("info", " ".join(parts))

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
            self._log("warning", f"lesson terminal replay failed: {type(exc).__name__}", batch)
            return False
        if self.pending_terminal_batch is batch:
            self.pending_terminal_batch = None
        await self._clear_terminal_batch(batch)
        return True

    async def _store_terminal_batch(self, batch: Dict[str, Any]) -> None:
        try:
            await self._terminal_store.store(self.device_id, batch)
        except Exception as exc:
            self._log(
                "warning",
                f"lesson terminal replay store write failed: {type(exc).__name__}",
                batch,
            )

    async def _clear_terminal_batch(self, batch: Dict[str, Any]) -> None:
        try:
            await self._terminal_store.clear(self.device_id, batch)
        except Exception as exc:
            self._log(
                "warning",
                f"lesson terminal replay store clear failed: {type(exc).__name__}",
                batch,
            )

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
            and event.get("type")
            in {"lesson_completed", "lesson_failed", "lesson_cancelled", "lesson_abandoned"}
            for event in events
        )

    @staticmethod
    def _is_started_batch(batch: Dict[str, Any]) -> bool:
        events = batch.get("events")
        if not isinstance(events, list):
            return False
        return any(
            isinstance(event, dict) and event.get("type") == "lesson_started" for event in events
        )

    def _remember_started_batch(self, batch: Dict[str, Any]) -> None:
        key = _session_lifecycle_key(batch)
        if key is not None:
            self._started_batches[key] = batch

    def _with_started_event_for_replay(self, batch: Dict[str, Any]) -> Dict[str, Any]:
        key = _session_lifecycle_key(batch)
        if key is None or self._is_started_batch(batch):
            return batch
        started = self._started_batches.get(key)
        if started is None:
            return batch
        started_events = [
            event
            for event in started.get("events", [])
            if isinstance(event, dict) and event.get("type") == "lesson_started"
        ]
        if not started_events:
            return batch
        events = batch.get("events")
        if not isinstance(events, list):
            return batch
        return {**batch, "events": [*started_events, *events]}

    async def _ensure_client(self) -> None:
        if self._client is not None or httpx is None:
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            limits=httpx.Limits(max_keepalive_connections=0),
            follow_redirects=True,
        )
        self._owns_client = True

    async def drain(self) -> None:
        """Wait for everything already enqueued to reach the backend.

        Forwarding is asynchronous, so "the runtime reached COMPLETED" and "the backend
        knows it" are separated by a queue. Anything that needs to observe the effect of
        a forward -- rather than merely having requested it -- has to wait here first.
        """
        if self._worker is None:
            return
        await self._queue.join()

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

    def _log(self, level: str, message: str, batch: Optional[Dict[str, Any]] = None) -> None:
        if self._logger is None:
            return
        try:
            getattr(self._logger.bind(tag=TAG), level)(_with_lesson_log_context(message, batch))
        except Exception:
            pass


class MemoryTerminalReplayStore:
    async def store(self, device_id: str, batch: Dict[str, Any]) -> None:
        _store_pending_terminal_batch(device_id, batch)

    async def load(self, device_id: str, assignment_id: str) -> Optional[Dict[str, Any]]:
        return _PENDING_TERMINAL_BATCHES.get((device_id, assignment_id))

    async def clear(self, device_id: str, batch: Dict[str, Any]) -> None:
        _clear_pending_terminal_batch(device_id, batch)


class RedisTerminalReplayStore:
    def __init__(
        self,
        *,
        url: str,
        namespace: str = "prod",
        ttl_sec: int = 7 * 24 * 60 * 60,
        client: Any = None,
    ) -> None:
        self.url = url
        self.namespace = namespace or "prod"
        self.ttl_sec = max(60, int(ttl_sec))
        self._client = client

    @classmethod
    def from_env(cls) -> Optional["RedisTerminalReplayStore"]:
        url = os.getenv("REDIS_URL")
        if not url:
            return None
        namespace = os.getenv("TBOT_LIVE_REDIS_NAMESPACE", "prod")
        try:
            ttl_sec = int(os.getenv("LESSON_TERMINAL_REPLAY_TTL_SEC", str(7 * 24 * 60 * 60)))
        except ValueError:
            ttl_sec = 7 * 24 * 60 * 60
        return cls(url=url, namespace=namespace, ttl_sec=ttl_sec)

    async def store(self, device_id: str, batch: Dict[str, Any]) -> None:
        key = _pending_terminal_key(device_id, batch)
        if key is None:
            return
        redis = await self._redis()
        await redis.set(
            self._key(key[0], key[1]),
            json.dumps(batch, ensure_ascii=False, separators=(",", ":")),
            ex=self.ttl_sec,
        )

    async def load(self, device_id: str, assignment_id: str) -> Optional[Dict[str, Any]]:
        redis = await self._redis()
        raw = await redis.get(self._key(device_id, assignment_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    async def clear(self, device_id: str, batch: Dict[str, Any]) -> None:
        key = _pending_terminal_key(device_id, batch)
        if key is None:
            return
        redis = await self._redis()
        await redis.delete(self._key(key[0], key[1]))

    async def _redis(self) -> Any:
        if self._client is not None:
            return self._client
        from redis import asyncio as redis_asyncio

        self._client = redis_asyncio.from_url(self.url, decode_responses=True)
        return self._client

    def _key(self, device_id: str, assignment_id: str) -> str:
        return f"{self.namespace}:lesson-terminal-replay:{device_id}:{assignment_id}"


def get_terminal_replay_store() -> Any:
    global _DEFAULT_TERMINAL_STORE
    if _DEFAULT_TERMINAL_STORE is not None:
        return _DEFAULT_TERMINAL_STORE
    redis_store = RedisTerminalReplayStore.from_env()
    _DEFAULT_TERMINAL_STORE = redis_store or MemoryTerminalReplayStore()
    return _DEFAULT_TERMINAL_STORE


def _pending_terminal_key(device_id: str, batch: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    assignment_id = batch.get("assignmentId")
    if not device_id or not isinstance(assignment_id, str) or not assignment_id:
        return None
    if not LessonEventForwarder._is_terminal_batch(batch):
        return None
    return (device_id, assignment_id)


def _session_lifecycle_key(batch: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    assignment_id = batch.get("assignmentId")
    session_id = batch.get("sessionId")
    if not isinstance(assignment_id, str) or not assignment_id:
        return None
    if not isinstance(session_id, str) or not session_id:
        return None
    return (assignment_id, session_id)


def _with_lesson_log_context(message: str, batch: Optional[Dict[str, Any]]) -> str:
    return with_lesson_log_context(message, batch if isinstance(batch, dict) else None)


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
    terminal_store: Any = None,
) -> bool:
    """Replay a terminal lifecycle event left behind by a prior connection.

    The configured store covers the reconnect/restart case after a backend hiccup:
    the old forwarder dead-lettered a terminal batch without a 2xx, then the device
    reconnects before the course cursor advanced. Backend ingest dedups
    null-sequence lifecycle events by ``(assignment_id,event_type)``.
    """
    store = terminal_store or get_terminal_replay_store()
    batch = await store.load(device_id, assignment_id)
    if batch is None:
        return False
    post = post_fn or _backend_api.post_lesson_event
    try:
        await post(client, base_url, device_id, batch, token=token)
    except Exception as exc:
        if logger is not None:
            try:
                logger.bind(tag=TAG).warning(
                    _with_lesson_log_context(
                        f"lesson terminal reconnect replay failed: {type(exc).__name__}",
                        batch,
                    )
                )
            except Exception:
                pass
        return False
    await store.clear(device_id, batch)
    return True
