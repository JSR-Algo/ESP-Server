from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager


class _LockEntry:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.users = 0


class ConnectionRegistry(dict):
    def __init__(self):
        super().__init__()
        self._device_locks = {}

    @property
    def lock_entry_count(self) -> int:
        return len(self._device_locks)

    @asynccontextmanager
    async def _hold_lock(self, device_id: str):
        entry = self._device_locks.get(device_id)
        if entry is None:
            entry = _LockEntry()
            self._device_locks[device_id] = entry
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.users -= 1
            if entry.users == 0 and self._device_locks.get(device_id) is entry:
                self._device_locks.pop(device_id, None)

    async def replace(self, device_id: str, connection):
        """Install ``connection`` as current and return whoever it displaced.

        T2.5: the displaced handler is a *stale socket* — its lesson runtime
        still points at the old websocket and would keep emitting frames meant
        for the session that just superseded it. Returning it (instead of
        dropping it on the floor) is what lets the caller scrap it.
        """
        async with self._hold_lock(device_id):
            previous = self.get(device_id)
            super().__setitem__(device_id, connection)
            return previous if previous is not connection else None

    def is_current(self, device_id: str, connection) -> bool:
        """Lock-free read of "does this connection still own the device?"."""
        return self.get(device_id) is connection

    async def remove_if_current(self, device_id: str, connection) -> bool:
        async with self._hold_lock(device_id):
            if self.get(device_id) is not connection:
                return False
            super().pop(device_id, None)
            return True

    @asynccontextmanager
    async def reserve_current(
        self, device_id: str, connection, session_id: str
    ):
        async with self._hold_lock(device_id):
            current = self.get(device_id)
            current_session = str(getattr(current, "session_id", "") or "")
            yield current is connection and current_session == session_id
