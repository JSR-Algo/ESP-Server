import pytest

from core.lesson.global_generation_status import (
    GlobalGenerationStatus,
    GlobalGenerationStatusError,
)

CHECKSUM = "a" * 64


class Store:
    def __init__(self, snapshot):
        self.value = snapshot
        self.calls = 0

    async def snapshot(self):
        self.calls += 1
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class Sessions:
    def __init__(self, counts):
        self.counts = counts
        self.generations = []

    async def aggregate(self, generation):
        self.generations.append(generation)
        if isinstance(self.counts, BaseException):
            raise self.counts
        return self.counts


@pytest.mark.asyncio
async def test_snapshot_projects_only_redacted_aggregate_fields():
    store = Store(
        {
            "desiredGeneration": 8,
            "desiredIndexChecksum": "b" * 64,
            "etag": "secret-etag",
            "acceptedGeneration": 7,
            "acceptedIndexChecksum": CHECKSUM,
            "materializationState": "ready",
            "retryAttempt": 3,
            "nextRetryAt": "tomorrow",
            "lastPollAt": "2026-07-25T01:00:00Z",
            "lastMaterializedAt": "2026-07-25T00:59:00Z",
            "lastErrorCode": None,
        }
    )
    sessions = Sessions({"connected": 4, "current": 2, "retrying": 1, "failed": 1})

    result = await GlobalGenerationStatus(store, sessions).snapshot()

    assert result == {
        "acceptedGeneration": 7,
        "indexChecksum": CHECKSUM,
        "materializationState": "ready",
        "connections": {"connected": 4, "current": 2, "retrying": 1, "failed": 1},
        "lastPollAt": "2026-07-25T01:00:00Z",
        "lastMaterializedAt": "2026-07-25T00:59:00Z",
        "lastErrorCode": None,
    }
    assert store.calls == 1
    assert sessions.generations == [7]


@pytest.mark.asyncio
async def test_snapshot_sanitizes_corrupt_redis_values_and_uses_zero_sentinel():
    store = Store(
        {
            "desiredGeneration": "8",
            "acceptedGeneration": 0,
            "acceptedIndexChecksum": "NOT-A-CHECKSUM",
            "materializationState": "redis-internal-secret",
            "lastPollAt": "redis://token@secret-host/status",
            "lastMaterializedAt": "x" * 10_000,
            "lastErrorCode": "Exception: redis://token@host/key",
        }
    )
    sessions = Sessions(
        {"connected": 3, "current": 99, "retrying": -2, "failed": True, "raw": 999}
    )

    result = await GlobalGenerationStatus(store, sessions).snapshot()

    assert result == {
        "acceptedGeneration": None,
        "indexChecksum": None,
        "materializationState": "empty",
        "connections": {"connected": 3, "current": 3, "retrying": 0, "failed": 0},
        "lastPollAt": None,
        "lastMaterializedAt": None,
        "lastErrorCode": "generation_status_error",
    }
    assert sessions.generations == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({}, "empty"),
        ({"lastPollAt": "2026-07-25T01:00:00Z"}, "polling"),
        ({"desiredGeneration": 2}, "polling"),
        ({"desiredGeneration": 2, "materializationState": "materializing"}, "materializing"),
        (
            {
                "desiredGeneration": 2,
                "materializationState": "retry_wait",
                "lastErrorCode": "download_failed",
            },
            "retry_wait",
        ),
        ({"acceptedGeneration": 2, "materializationState": "ready"}, "polling"),
    ],
)
async def test_snapshot_projects_only_coherent_safe_states(snapshot, expected):
    result = await GlobalGenerationStatus(Store(snapshot), Sessions({})).snapshot()
    assert result["materializationState"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_side", ["store", "sessions"])
async def test_snapshot_translates_dependency_failures_to_one_safe_error(failure_side):
    store = Store(RuntimeError("redis://user:token@secret")) if failure_side == "store" else Store({})
    sessions = Sessions(RuntimeError("raw session id")) if failure_side == "sessions" else Sessions({})

    with pytest.raises(GlobalGenerationStatusError) as raised:
        await GlobalGenerationStatus(store, sessions).snapshot()

    assert raised.value.code == "generation_status_unavailable"
    assert str(raised.value) == "generation_status_unavailable"
    assert raised.value.__cause__ is None
