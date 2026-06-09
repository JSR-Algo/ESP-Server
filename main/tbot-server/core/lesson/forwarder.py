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
from typing import Any, Awaitable, Callable, Dict, List, Optional

# Module ref (NOT a from-import) so this module imports cleanly even under the
# test conftest stub that replaces config.manage_api_client; the attribute is
# resolved at call time, where real tests inject a fake post_fn.
from config import manage_api_client as _backend_api

TAG = "LessonForwarder"

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
    ) -> None:
        self.device_id = device_id
        self.base_url = base_url
        self.token = token
        self._post_fn = post_fn
        self._client = client
        self._owns_client = False
        self._logger = logger
        self._queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._closed = False

    def enqueue(self, batch: Dict[str, Any]) -> None:
        """Non-blocking; the worker drains and POSTs. Started lazily on first use."""
        if self._closed:
            return
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())
        self._queue.put_nowait(batch)

    async def _run(self) -> None:
        while True:
            batch = await self._queue.get()
            if batch is None:  # poison pill
                self._queue.task_done()
                break
            try:
                await self._post(batch)
            except Exception as exc:  # never let a forward failure kill the worker
                self._log("warning", f"lesson-events POST failed: {type(exc).__name__}")
            finally:
                self._queue.task_done()

    async def _post(self, batch: Dict[str, Any]) -> None:
        post_fn = self._post_fn or _backend_api.post_lesson_event
        if self._post_fn is None:
            await self._ensure_client()
        await post_fn(self._client, self.base_url, self.device_id, batch, token=self.token)

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
