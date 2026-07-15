"""Task-owned shared voice and exclusive connection activity leases."""

from __future__ import annotations

import asyncio
import concurrent.futures
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set


class LeaseKind(str, Enum):
    VOICE = "voice"
    EVICTION_EXCLUSIVE = "eviction-exclusive"


class ExclusiveDisposition(str, Enum):
    DEFINITIVE = "definitive"
    AMBIGUOUS = "ambiguous"


class ActivityOperation(str, Enum):
    LESSON_CACHE_EVICT = "lesson_cache.evict"
    CONNECTION_AUDIO = "connection.audio"
    CONNECTION_TEXT = "connection.text"
    CONNECTION_LISTEN = "connection.listen"
    CONNECTION_CONVERSATION = "connection.conversation"
    CLASSIC_AUDIO = "classic.audio"
    CLASSIC_TEXT = "classic.text"
    CLASSIC_LISTEN = "classic.listen"
    CLASSIC_CONVERSATION = "classic.conversation"
    CLASSIC_START_TO_CHAT = "classic.start_to_chat"
    GOOGLE_OPEN = "google.open"
    GOOGLE_RECONNECT = "google.reconnect"
    GOOGLE_HARD_RECONNECT = "google.hard_reconnect"
    GOOGLE_PREWARM = "google.prewarm"
    GOOGLE_SEND_TEXT = "google.send_text"
    GOOGLE_WAKE_GREETING = "google.wake_greeting"


class ActivityLeaseInvariantError(RuntimeError):
    """Raised when a caller attempts to violate lease ownership."""


@dataclass
class _HandleState:
    lease_id: int
    kind: LeaseKind
    operation: str
    owner_task: asyncio.Task
    handle: "ActivityLease"
    delegated_future: Any = None
    sticky: bool = False


@dataclass
class _VoiceOwnerState:
    record_id: int
    operation: str
    owner_task: asyncio.Task
    depth: int
    handle_ids: Set[int]


class ActivityLease:
    def __init__(
        self,
        coordinator: "ActivityLeaseCoordinator",
        lease_id: int,
        kind: LeaseKind,
        owner_task: asyncio.Task,
    ) -> None:
        self._coordinator = coordinator
        self._lease_id = lease_id
        self._kind = kind
        self._owner_task = owner_task
        self._completed = False
        self._delegated = False

    @property
    def owner_task(self) -> asyncio.Task:
        return self._owner_task

    @property
    def kind(self) -> LeaseKind:
        return self._kind

    @property
    def lease_id(self) -> int:
        return self._lease_id

    def release(self) -> None:
        self._ensure_open_handle()
        if self._delegated:
            raise ActivityLeaseInvariantError("delegated-release")
        self._coordinator._release_voice(self._lease_id)
        self._completed = True

    def release_when_done(self, future: Any) -> None:
        self._ensure_open_handle()
        if self._delegated:
            raise ActivityLeaseInvariantError("delegated-duplicate")
        self._coordinator._delegate_voice(self._lease_id, future)
        self._delegated = True

    def complete_exclusive(self, disposition: ExclusiveDisposition) -> None:
        self._ensure_open_handle()
        self._coordinator._complete_exclusive(self._lease_id, disposition)
        self._completed = True

    def _ensure_open_handle(self) -> None:
        if self._completed:
            raise ActivityLeaseInvariantError("duplicate-release")


class ActivityLeaseCoordinator:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._closed = False
        self._next_lease_id = 1
        self._handles: Dict[int, _HandleState] = {}
        self._voice_records_by_owner: Dict[asyncio.Task, _VoiceOwnerState] = {}
        self._exclusive_lease_id: Optional[int] = None
        self._exclusive_sticky = False

    def try_acquire_voice(self, operation: str) -> Optional[ActivityLease]:
        normalized_operation = self._normalize_operation(operation)
        if self._closed or normalized_operation is None:
            return None
        owner = self._require_owner_task()
        if self._exclusive_lease_id is not None:
            return None
        lease = self._create_handle(LeaseKind.VOICE, normalized_operation, owner)
        record = self._voice_records_by_owner.get(owner)
        if record is None:
            record = _VoiceOwnerState(
                record_id=lease.lease_id,
                operation=normalized_operation,
                owner_task=owner,
                depth=0,
                handle_ids=set(),
            )
            self._voice_records_by_owner[owner] = record
        record.handle_ids.add(lease.lease_id)
        record.depth = len(record.handle_ids)
        return lease

    def try_acquire_eviction(
        self,
        operation: str,
        *,
        busy_probe: Callable[[], bool],
    ) -> Optional[ActivityLease]:
        normalized_operation = self._normalize_operation(operation)
        if self._closed or normalized_operation is None:
            return None
        owner = self._require_owner_task()
        if self._closed or self._exclusive_lease_id is not None or self._voice_records_by_owner:
            return None
        try:
            if bool(busy_probe()):
                return None
        except Exception:
            return None
        if self._closed or self._exclusive_lease_id is not None or self._voice_records_by_owner:
            return None
        lease = self._create_handle(
            LeaseKind.EVICTION_EXCLUSIVE,
            normalized_operation,
            owner,
        )
        self._exclusive_lease_id = lease.lease_id
        return lease

    def has_voice_leases(self) -> bool:
        return bool(self._voice_records_by_owner)

    def has_exclusive_lease(self) -> bool:
        return self._exclusive_lease_id is not None

    def diagnostic_snapshot(self) -> Dict[str, Any]:
        exclusive = None
        if self._exclusive_lease_id is not None:
            state = self._handles.get(self._exclusive_lease_id)
            if state is not None:
                exclusive = {
                    "kind": state.kind.value,
                    "operation": state.operation,
                    "sticky": state.sticky,
                    "leaseIdSuffix": format(state.lease_id, "x")[-6:],
                    "ownerTaskIdSuffix": format(id(state.owner_task), "x")[-6:],
                }
        voice_owners = [
            {
                "kind": LeaseKind.VOICE.value,
                "operation": state.operation,
                "depth": state.depth,
                "recordIdSuffix": format(state.record_id, "x")[-6:],
                "ownerTaskIdSuffix": format(id(state.owner_task), "x")[-6:],
            }
            for state in sorted(
                self._voice_records_by_owner.values(),
                key=lambda item: item.record_id,
            )
        ]
        return {
            "closed": self._closed,
            "voiceLeaseCount": sum(state.depth for state in self._voice_records_by_owner.values()),
            "voiceOwners": voice_owners,
            "exclusive": exclusive,
        }

    def close(self) -> None:
        if self._closed:
            return
        self._require_owner_loop()
        self._closed = True
        self._handles.clear()
        self._voice_records_by_owner.clear()
        self._exclusive_lease_id = None
        self._exclusive_sticky = False

    def _create_handle(
        self,
        kind: LeaseKind,
        operation: str,
        owner: asyncio.Task,
    ) -> ActivityLease:
        lease_id = self._next_lease_id
        self._next_lease_id += 1
        lease = ActivityLease(self, lease_id, kind, owner)
        self._handles[lease_id] = _HandleState(
            lease_id=lease_id,
            kind=kind,
            operation=operation,
            owner_task=owner,
            handle=lease,
        )
        return lease

    def _release_voice(self, lease_id: int) -> None:
        state = self._owned_state(lease_id)
        if state.kind is not LeaseKind.VOICE:
            raise ActivityLeaseInvariantError("wrong-kind-release")
        if state.delegated_future is not None:
            raise ActivityLeaseInvariantError("delegated-release")
        self._remove_voice_state(state)

    def _delegate_voice(self, lease_id: int, future: Any) -> None:
        state = self._owned_state(lease_id)
        if state.kind is not LeaseKind.VOICE:
            raise ActivityLeaseInvariantError("wrong-kind-delegation")
        if state.delegated_future is not None:
            raise ActivityLeaseInvariantError("delegated-duplicate")

        if isinstance(future, asyncio.Future):
            if future.get_loop() is not self._loop:
                raise ActivityLeaseInvariantError("future-loop-mismatch")

            def done_callback(done_future: asyncio.Future) -> None:
                self._complete_delegated(lease_id, done_future)

        elif isinstance(future, concurrent.futures.Future):

            def done_callback(done_future: concurrent.futures.Future) -> None:
                if self._closed or self._loop.is_closed():
                    return
                with suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(
                        self._complete_delegated,
                        lease_id,
                        done_future,
                    )

        else:
            raise ActivityLeaseInvariantError("future-type")

        state.delegated_future = future
        callback_failed = False
        try:
            future.add_done_callback(done_callback)
        except Exception:
            state.delegated_future = None
            callback_failed = True
        if callback_failed:
            raise ActivityLeaseInvariantError("future-callback") from None

    def _complete_delegated(self, lease_id: int, future: Any) -> None:
        if self._closed:
            return
        self._require_owner_loop()
        state = self._handles.get(lease_id)
        if state is None:
            raise ActivityLeaseInvariantError("duplicate-release")
        if state.kind is not LeaseKind.VOICE:
            raise ActivityLeaseInvariantError("wrong-kind-delegation")
        if state.delegated_future is not future:
            raise ActivityLeaseInvariantError("future-mismatch")
        self._remove_voice_state(state)
        state.handle._completed = True

    def _complete_exclusive(
        self,
        lease_id: int,
        disposition: ExclusiveDisposition,
    ) -> None:
        state = self._owned_state(lease_id)
        if state.kind is not LeaseKind.EVICTION_EXCLUSIVE:
            raise ActivityLeaseInvariantError("wrong-kind-exclusive")
        if state.sticky:
            raise ActivityLeaseInvariantError("duplicate-release")
        if not isinstance(disposition, ExclusiveDisposition):
            raise ActivityLeaseInvariantError("disposition")
        if disposition is ExclusiveDisposition.DEFINITIVE:
            self._handles.pop(lease_id)
            self._exclusive_lease_id = None
            self._exclusive_sticky = False
            return
        state.sticky = True
        self._exclusive_sticky = True

    def _owned_state(self, lease_id: int) -> _HandleState:
        if self._closed:
            raise ActivityLeaseInvariantError("closed-coordinator")
        owner = self._require_owner_task()
        state = self._handles.get(lease_id)
        if state is None:
            raise ActivityLeaseInvariantError("duplicate-release")
        if state.owner_task is not owner:
            raise ActivityLeaseInvariantError("wrong-task-release")
        return state

    def _remove_voice_state(self, state: _HandleState) -> None:
        record = self._voice_records_by_owner.get(state.owner_task)
        if record is None or state.lease_id not in record.handle_ids:
            raise ActivityLeaseInvariantError("voice-owner-record")
        self._handles.pop(state.lease_id)
        record.handle_ids.remove(state.lease_id)
        record.depth = len(record.handle_ids)
        if record.depth == 0:
            self._voice_records_by_owner.pop(state.owner_task)

    @staticmethod
    def _normalize_operation(operation: Any) -> Optional[str]:
        try:
            return ActivityOperation(operation).value
        except (TypeError, ValueError):
            return None

    def _require_owner_task(self) -> asyncio.Task:
        self._require_owner_loop()
        owner = asyncio.current_task()
        if owner is None:
            raise ActivityLeaseInvariantError("missing-owner-task")
        return owner

    def _require_owner_loop(self) -> None:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            raise ActivityLeaseInvariantError("owner-loop") from None
        if running is not self._loop:
            raise ActivityLeaseInvariantError("owner-loop")
