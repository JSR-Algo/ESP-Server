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
        """Bind ``device_id`` to ``connection`` and return the entry it displaced.

        The registry owns the mapping, never a socket lifecycle: the caller is
        responsible for superseding whatever comes back (``None`` when the device
        had no live entry, or when it was already bound to this connection).
        """
        async with self._hold_lock(device_id):
            previous = self.get(device_id)
            super().__setitem__(device_id, connection)
            return previous if previous is not connection else None

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
