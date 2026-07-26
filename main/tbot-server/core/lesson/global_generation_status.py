"""Redacted public projection of global lesson generation state."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_ERROR_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z$"
)
_MAX_COUNT = (1 << 31) - 1


class GlobalGenerationStatusError(RuntimeError):
    def __init__(self) -> None:
        self.code = "generation_status_unavailable"
        super().__init__(self.code)


class GlobalGenerationStatus:
    def __init__(self, store: Any, sessions: Any) -> None:
        self.store = store
        self.sessions = sessions

    async def snapshot(self) -> dict[str, Any]:
        try:
            raw = await self.store.snapshot()
            raw = raw if isinstance(raw, dict) else {}
            accepted = _positive_int(raw.get("acceptedGeneration"))
            counts = await self.sessions.aggregate(accepted or 0)
            connections = _counts(counts)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise GlobalGenerationStatusError() from None

        checksum = _checksum(raw.get("acceptedIndexChecksum")) if accepted else None
        return {
            "acceptedGeneration": accepted,
            "indexChecksum": checksum,
            "materializationState": _state(raw, accepted, checksum),
            "connections": connections,
            "lastPollAt": _timestamp(raw.get("lastPollAt")),
            "lastMaterializedAt": _timestamp(raw.get("lastMaterializedAt")),
            "lastErrorCode": _error_code(raw.get("lastErrorCode")),
        }


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _checksum(value: Any) -> str | None:
    return value if isinstance(value, str) and _CHECKSUM_RE.fullmatch(value) else None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or _TIMESTAMP_RE.fullmatch(value) is None:
        return None
    try:
        datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return value


def _error_code(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and _ERROR_CODE_RE.fullmatch(value):
        return value
    return "generation_status_error"


def _validated_count(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_COUNT:
        raise ValueError("invalid connection aggregate")
    return value


def _counts(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("invalid connection aggregate")
    connected = _validated_count(value.get("connected"))
    current = _validated_count(value.get("current"))
    retrying = _validated_count(value.get("retrying"))
    failed = _validated_count(value.get("failed"))
    buckets = (current, retrying, failed)
    if any(count > connected for count in buckets) or sum(buckets) != connected:
        raise ValueError("invalid connection aggregate")
    return {
        "connected": connected,
        "current": current,
        "retrying": retrying,
        "failed": failed,
    }


def _state(raw: dict[str, Any], accepted: int | None, checksum: str | None) -> str:
    desired = _positive_int(raw.get("desiredGeneration"))
    last_poll = _timestamp(raw.get("lastPollAt"))
    state = raw.get("materializationState")
    if accepted is None and desired is None and last_poll is None:
        return "empty"
    if state == "materializing" and desired is not None:
        return "materializing"
    if state == "ready" and accepted is not None and checksum is not None:
        return "ready"
    if (
        state == "retry_wait"
        and desired is not None
        and _error_code(raw.get("lastErrorCode")) not in {None, "generation_status_error"}
    ):
        return "retry_wait"
    return "polling"
