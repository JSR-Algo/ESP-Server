"""D10 — the armed disconnect close must survive an esp32-server restart.

Owner decision D10 (`coordination/owner-decisions-20260919.md`): "make the armed
intent durable, following that precedent rather than inventing a second mechanism. A
restart inside the grace window must still close the assignment, and a robot that
reconnects after a restart must still be able to claim its intent and resume."

These cases pin the mechanism. They are NOT the proof — D10 also says "prove both
across a real restart, not only in unit tests", and that lives in
`coordination/durable-armed-close-20260920/runtime/traces/`. What is pinned here is
everything a real restart cannot show cheaply: the record shape, the two
Redis-unavailable decisions, the recovery fences, and the fact that a restart cannot
steal a live lesson.

A restart is simulated the honest way throughout: a SECOND `DisconnectAbandonmentReaper`
is built over the SAME ledger object, with no shared memory of any kind. That is exactly
what a new process sees.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from core.lesson.disconnect_abandonment import (
    DISCONNECT_ABANDONMENT_BINDING_TTL_SEC,
    DISCONNECT_ABANDONMENT_RECORD_SCHEMA,
    DISCONNECT_ABANDONMENT_RECORD_TTL_MARGIN_SEC,
    DisconnectAbandonmentIntent,
    DisconnectAbandonmentReaper,
    InMemoryIntentLedger,
    RedisIntentLedger,
    build_disconnect_abandonment_batch,
    get_intent_ledger,
    reset_intent_ledger,
)

ASSIGNMENT = "11111111-1111-4111-8111-111111111111"
SESSION = "22222222-2222-4222-8222-222222222222"
OTHER_SESSION = "33333333-3333-4333-8333-333333333333"
DEVICE = "44444444-4444-4444-8444-444444444444"
MAC = "14:c1:9f:d1:a8:48"
TOKEN = "device-jwt-that-must-never-be-persisted"


# ── a Redis stand-in that behaves like the real client for the five verbs used ──


class FakeRedis:
    """Only the five commands `RedisIntentLedger` issues, with TTL bookkeeping.

    `decode_responses=True` is how the ledger builds its client, so values come back
    as `str`, which is what the real client would hand back.
    """

    def __init__(self) -> None:
        self.strings: dict = {}
        self.expiries: dict = {}
        self.sets: dict = {}
        self.fail = False
        self.calls: list = []

    def _check(self, name):
        self.calls.append(name)
        if self.fail:
            raise ConnectionError("redis is down")

    async def set(self, key, value, ex=None):
        self._check("set")
        self.strings[key] = str(value)
        self.expiries[key] = ex
        return True

    async def get(self, key):
        self._check("get")
        return self.strings.get(key)

    async def delete(self, key):
        self._check("delete")
        self.strings.pop(key, None)
        self.expiries.pop(key, None)
        return 1

    async def sadd(self, key, *members):
        self._check("sadd")
        self.sets.setdefault(key, set()).update(str(m) for m in members)
        return len(members)

    async def srem(self, key, *members):
        self._check("srem")
        bucket = self.sets.setdefault(key, set())
        for member in members:
            bucket.discard(str(member))
        return len(members)

    async def smembers(self, key):
        self._check("smembers")
        return set(self.sets.get(key, set()))


def make_intent(
    *,
    session_id: str = SESSION,
    deadline: float = 0.0,
    deadline_ms: int = 1_700_000_000_000,
    token=TOKEN,
) -> DisconnectAbandonmentIntent:
    return DisconnectAbandonmentIntent(
        assignment_id=ASSIGNMENT,
        session_id=session_id,
        device_id=DEVICE,
        base_url="http://backend:3000/v1",
        token=token,
        batch=build_disconnect_abandonment_batch(
            assignment_id=ASSIGNMENT,
            lesson_id="lesson-w01",
            lesson_version=3,
            session_id=session_id,
            abandoned_at="2026-09-20T00:00:00.000Z",
        ),
        deadline=deadline,
        device_mac=MAC,
        deadline_ms=deadline_ms,
    )


class DurableMemoryLedger(InMemoryIntentLedger):
    """The in-memory ledger, declared durable.

    Recovery is about what survives a PROCESS, not about Redis specifically: the
    reaper asks the ledger `durable`, and a ledger object that outlives both reapers
    is exactly what a Redis outside the process is. Using this instead of a
    `FakeRedis` where the restart itself is the subject keeps those cases about
    recovery rather than about a Redis stand-in — and the Redis-specific paths are
    pinned separately above, against `FakeRedis`.
    """

    durable = True


_LIVE: list = []


def reaper(ledger, **kwargs) -> DisconnectAbandonmentReaper:
    """A reaper with a frozen clock, so nothing here depends on wall time."""
    kwargs.setdefault("clock", lambda: 1000.0)
    kwargs.setdefault("now_ms_fn", lambda: 1_700_000_000_000)
    kwargs.setdefault("assignment_fn", running_assignment)
    built = DisconnectAbandonmentReaper(ledger=ledger, **kwargs)
    # These cases drive the reaper through `drain()` — the module's own documented
    # test seam — so the background loop is left unstarted. With a frozen clock the
    # loop would otherwise wait on REAL time for a deadline that never arrives, and
    # would also fire due intents concurrently with the explicit `drain()`, making
    # every counter assertion a race. The loop itself is not skipped work: it is what
    # the four real-stack proofs in `runtime/traces/` exercise, on real time.
    built._ensure_task = lambda: None  # type: ignore[method-assign]
    _LIVE.append(built)
    return built


@pytest.fixture(autouse=True)
def close_reapers():
    """Stop every reaper's background loop at the end of each case.

    The loop sleeps until the next deadline in REAL time; a frozen test clock means
    it would otherwise sit in the event loop for the length of the grace window and
    hold the test session open.
    """
    yield
    built, _LIVE[:] = list(_LIVE), []
    for one in built:
        one._closed = True
        task = one._task
        one._task = None
        if task is not None and not task.done():
            task.cancel()


async def running_assignment(intent):
    return {"id": intent.assignment_id, "state": "RUNNING"}


async def drain_side_tasks(reaper_obj) -> None:
    """Let the scheduled ledger writes finish — `register`/`claim` are synchronous."""
    for _ in range(8):
        pending = [t for t in list(reaper_obj._side_tasks) if not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)


# ── 1. the record ─────────────────────────────────────────────────────────────


def test_the_durable_record_never_carries_the_device_token():
    """The one assertion that keeps a bearer out of a shared ledger.

    Scanned over the serialized record rather than over its keys, so a token smuggled
    into `batch` or `trace` by a future change fails this too.
    """
    record = make_intent().to_record()
    assert "token" not in record
    assert TOKEN not in json.dumps(record, default=str)
    # The MAC IS carried: a recovered close mints its own token from it.
    assert record["deviceMac"] == MAC


def test_record_round_trips_into_an_intent_marked_recovered():
    original = make_intent()
    restored = DisconnectAbandonmentIntent.from_record(
        original.to_record(), now_ms=1_700_000_000_000, now_monotonic=500.0
    )
    assert restored is not None
    assert restored.key == original.key
    assert restored.device_mac == MAC
    assert restored.batch == original.batch
    assert restored.recovered is True
    assert restored.durable is True
    # The token did not survive, by construction.
    assert restored.token is None


def test_a_wall_clock_deadline_is_translated_into_this_process_monotonic_clock():
    """A monotonic deadline is meaningless in a new process; a wall-clock one is not."""
    intent = make_intent(deadline_ms=1_700_000_030_000)  # 30 s after "now"
    restored = DisconnectAbandonmentIntent.from_record(
        intent.to_record(), now_ms=1_700_000_000_000, now_monotonic=500.0
    )
    assert restored.deadline == pytest.approx(530.0)


def test_a_deadline_that_expired_during_the_outage_is_due_immediately_not_negative():
    intent = make_intent(deadline_ms=1_699_999_000_000)  # 1000 s in the past
    restored = DisconnectAbandonmentIntent.from_record(
        intent.to_record(), now_ms=1_700_000_000_000, now_monotonic=500.0
    )
    assert restored.deadline == pytest.approx(500.0)


@pytest.mark.parametrize(
    "record",
    [
        None,
        "not-a-mapping",
        {},
        {"schema": 999, "assignmentId": ASSIGNMENT, "sessionId": SESSION, "batch": {}},
        {"schema": DISCONNECT_ABANDONMENT_RECORD_SCHEMA, "sessionId": SESSION, "batch": {}},
        {"schema": DISCONNECT_ABANDONMENT_RECORD_SCHEMA, "assignmentId": ASSIGNMENT, "batch": {}},
        {
            "schema": DISCONNECT_ABANDONMENT_RECORD_SCHEMA,
            "assignmentId": ASSIGNMENT,
            "sessionId": SESSION,
            "batch": "not-a-batch",
        },
    ],
)
def test_an_unreadable_record_is_skipped_rather_than_half_read_into_a_close(record):
    """A record this build cannot read is not a record it may act on."""
    assert DisconnectAbandonmentIntent.from_record(record) is None


# ── 2. the ledgers ────────────────────────────────────────────────────────────


def test_the_two_ledgers_declare_durability_the_way_liveness_lease_does():
    assert InMemoryIntentLedger.durable is False
    assert RedisIntentLedger.durable is True


@pytest.mark.asyncio
async def test_redis_ledger_keys_follow_the_liveness_lease_convention():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    await ledger.put(make_intent(), ttl_sec=100.0)
    await ledger.note_binding(ASSIGNMENT, SESSION)
    keys = sorted(redis.strings)
    assert keys == [
        f"cpr:lesson-disconnect-abandonment:binding:{ASSIGNMENT}",
        f"cpr:lesson-disconnect-abandonment:intent:{ASSIGNMENT}:{SESSION}",
    ]
    assert redis.sets["cpr:lesson-disconnect-abandonment:index"] == {
        f"cpr:lesson-disconnect-abandonment:intent:{ASSIGNMENT}:{SESSION}"
    }


@pytest.mark.asyncio
async def test_a_record_whose_body_expired_is_pruned_from_the_index_on_load():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    await ledger.put(make_intent(), ttl_sec=100.0)
    # The TTL fired: the body is gone, the index member is not.
    redis.strings.clear()
    assert await ledger.load() == ()
    assert redis.sets["cpr:lesson-disconnect-abandonment:index"] == set()


@pytest.mark.asyncio
async def test_drop_removes_both_the_record_and_its_index_member():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    await ledger.put(make_intent(), ttl_sec=100.0)
    await ledger.drop(ASSIGNMENT, SESSION)
    assert redis.strings == {}
    assert redis.sets["cpr:lesson-disconnect-abandonment:index"] == set()
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_a_corrupt_record_body_is_pruned_and_the_rest_still_load():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    await ledger.put(make_intent(), ttl_sec=100.0)
    await ledger.put(make_intent(session_id=OTHER_SESSION), ttl_sec=100.0)
    bad = f"cpr:lesson-disconnect-abandonment:intent:{ASSIGNMENT}:{SESSION}"
    redis.strings[bad] = "{not json"
    records = await ledger.load()
    assert [r["sessionId"] for r in records] == [OTHER_SESSION]


def test_no_redis_url_resolves_to_the_non_durable_ledger(monkeypatch):
    """Same REDIS_URL / TBOT_LIVE_REDIS_NAMESPACE wiring as `get_lease_ledger`.

    Only the no-URL branch is exercised here, deliberately: the URL branch builds a
    live `redis.asyncio` client, and a test that leaves one pointed at a host that
    does not exist contaminates every case after it. The URL branch's observable
    behaviour — the namespace and the key layout — is pinned directly against
    `RedisIntentLedger` above.
    """
    reset_intent_ledger(None)
    try:
        monkeypatch.delenv("REDIS_URL", raising=False)
        assert isinstance(get_intent_ledger(), InMemoryIntentLedger)
        # And it is cached process-wide, exactly like the lease ledger.
        assert get_intent_ledger() is get_intent_ledger()
    finally:
        reset_intent_ledger(None)


def test_the_namespace_comes_from_the_same_variable_the_lease_ledger_uses():
    assert RedisIntentLedger(FakeRedis(), namespace="cpr-s22").namespace == "cpr-s22"
    assert RedisIntentLedger(FakeRedis(), namespace="").namespace == "prod"


# ── 3. arming: the Redis-unavailable decision, at arm time ────────────────────


@pytest.mark.asyncio
async def test_arming_stores_the_intent_and_marks_it_durable():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    r = reaper(ledger)
    intent = make_intent(deadline=1045.0)
    assert r.register(intent) is True
    await drain_side_tasks(r)
    assert intent.durable is True
    assert r.counters["persisted"] == 1
    assert r.counters["armed_non_durable"] == 0
    assert len(await ledger.load()) == 1


@pytest.mark.asyncio
async def test_the_record_ttl_outlives_the_whole_grace_and_retry_budget():
    """The TTL can never be the thing that loses a close — the deviation's premise."""
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    r = reaper(ledger, retry_backoff_sec=5.0, max_attempts=4)
    r.register(make_intent(deadline=1090.0))  # 90 s of grace left
    await drain_side_tasks(r)
    ttl = redis.expiries[f"cpr:lesson-disconnect-abandonment:intent:{ASSIGNMENT}:{SESSION}"]
    assert ttl >= 90 + DISCONNECT_ABANDONMENT_RECORD_TTL_MARGIN_SEC


@pytest.mark.asyncio
async def test_a_non_durable_ledger_still_arms_and_says_so():
    """Refusing to arm would be failing INTO the D8 hole. It arms, and it is loud."""
    lines: list = []
    r = reaper(InMemoryIntentLedger(), logger=_recording_logger(lines))
    intent = make_intent(deadline=1045.0)
    assert r.register(intent) is True
    await drain_side_tasks(r)
    assert intent.durable is False
    assert r.counters["armed_non_durable"] == 1
    # The close IS armed — only its restart survival is missing.
    assert r.pending_keys() == ((ASSIGNMENT, SESSION),)
    warned = [l for l in lines if "armed_non_durable" in l[1]]
    assert warned and warned[0][0] == "warning"
    assert "ledger_not_durable" in warned[0][1]


@pytest.mark.asyncio
async def test_redis_down_at_arm_time_arms_anyway_and_reports_the_reason():
    lines: list = []
    redis = FakeRedis()
    redis.fail = True
    r = reaper(RedisIntentLedger(redis, namespace="cpr"), logger=_recording_logger(lines))
    intent = make_intent(deadline=1045.0)
    assert r.register(intent) is True
    await drain_side_tasks(r)
    assert intent.durable is False
    assert r.counters["persist_failed"] == 1
    assert r.counters["armed_non_durable"] == 1
    assert r.pending_keys() == ((ASSIGNMENT, SESSION),)
    warned = [l for l in lines if "armed_non_durable" in l[1]]
    assert warned and "ledger_unavailable" in warned[0][1]


@pytest.mark.asyncio
async def test_an_arm_that_missed_a_redis_blink_becomes_durable_when_redis_returns():
    """The defect D10 names — a close that vanishes because Redis blinked — closed."""
    redis = FakeRedis()
    redis.fail = True
    ledger = RedisIntentLedger(redis, namespace="cpr")
    r = reaper(ledger)
    intent = make_intent(deadline=1045.0)
    r.register(intent)
    await drain_side_tasks(r)
    assert intent.durable is False

    redis.fail = False
    r._last_repersist = 0.0  # the loop's own rate limit, wound forward
    await r._repersist_pending()
    assert intent.durable is True
    assert len(await ledger.load()) == 1


@pytest.mark.asyncio
async def test_a_second_teardown_of_the_same_session_repersists_rather_than_double_arming():
    redis = FakeRedis()
    redis.fail = True
    ledger = RedisIntentLedger(redis, namespace="cpr")
    r = reaper(ledger)
    r.register(make_intent(deadline=1045.0))
    await drain_side_tasks(r)
    redis.fail = False
    # A repeat: D8's contract says it must NOT arm a second close.
    assert r.register(make_intent(deadline=1045.0)) is False
    await drain_side_tasks(r)
    assert r.counters["registered"] == 1
    assert len(await ledger.load()) == 1


# ── 4. claiming ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_claim_deletes_the_record_so_it_survives_the_restart_too():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    r = reaper(ledger)
    r.register(make_intent(deadline=1045.0))
    await drain_side_tasks(r)
    assert r.claim(ASSIGNMENT, SESSION) is True
    await drain_side_tasks(r)
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_a_claim_writes_the_binding_guard_even_when_nothing_was_armed():
    """The case that matters: a robot back with a NEW session after a restart."""
    ledger = InMemoryIntentLedger()
    r = reaper(ledger)
    assert r.claim(ASSIGNMENT, OTHER_SESSION) is False
    await drain_side_tasks(r)
    assert await ledger.binding(ASSIGNMENT) == OTHER_SESSION


@pytest.mark.asyncio
async def test_the_binding_guard_carries_its_own_ttl():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    await ledger.note_binding(ASSIGNMENT, SESSION)
    key = f"cpr:lesson-disconnect-abandonment:binding:{ASSIGNMENT}"
    assert redis.expiries[key] == int(DISCONNECT_ABANDONMENT_BINDING_TTL_SEC)


# ── 5. recovery ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_restart_inside_the_grace_recovers_the_armed_close_and_fires_it():
    """(a) in unit form: arm, restart, the close still happens exactly once."""
    ledger = DurableMemoryLedger()
    first = reaper(ledger)
    first.register(make_intent(deadline=1045.0))
    await drain_side_tasks(first)
    posted: list = []

    async def post(intent):
        posted.append(intent)

    # THE RESTART: a brand-new reaper, no shared memory, same ledger.
    second = reaper(ledger, post_fn=post, token_fn=_static_token)
    summary = await second.recover()
    assert summary == {"durable": True, "recovered": 1, "skipped": 0, "available": True}
    assert second.counters["recovered"] == 1
    await second.drain()
    await drain_side_tasks(second)
    assert second.counters["closed"] == 1
    assert len(posted) == 1
    assert posted[0].batch["events"][0]["reason"] == "disconnected"
    # Exactly one terminal record: the ledger no longer holds it.
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_recovery_is_idempotent_and_never_arms_the_same_close_twice():
    ledger = DurableMemoryLedger()
    first = reaper(ledger)
    first.register(make_intent(deadline=1045.0))
    await drain_side_tasks(first)
    second = reaper(ledger, token_fn=_static_token)
    assert (await second.recover())["recovered"] == 1
    # A second recovery (a retry, a double boot hook) must add nothing.
    again = await second.recover()
    assert again["recovered"] == 0
    assert again["skipped"] == 1
    assert second.counters["recovered"] == 1
    assert len(second.pending_keys()) == 1


@pytest.mark.asyncio
async def test_a_recovered_close_mints_its_own_token_because_the_record_has_none():
    ledger = DurableMemoryLedger()
    first = reaper(ledger)
    first.register(make_intent(deadline=1045.0))
    await drain_side_tasks(first)
    seen: list = []

    async def post(intent):
        seen.append(intent.token)

    async def token_fn(intent):
        assert intent.device_mac == MAC
        return "freshly-minted"

    second = reaper(ledger, post_fn=post, token_fn=token_fn)
    await second.recover()
    await second.drain()
    assert seen == ["freshly-minted"]


@pytest.mark.asyncio
async def test_a_robot_that_reconnects_after_the_restart_claims_its_recovered_intent():
    """(b) in unit form: the close must lose to a returning robot, across a restart."""
    ledger = DurableMemoryLedger()
    first = reaper(ledger)
    first.register(make_intent(deadline=1045.0))
    await drain_side_tasks(first)
    posted: list = []

    async def post(intent):
        posted.append(intent)

    second = reaper(ledger, post_fn=post, token_fn=_static_token)
    await second.recover()
    # The robot is back on the SAME session — D8's claim key, preserved.
    assert second.claim(ASSIGNMENT, SESSION) is True
    await drain_side_tasks(second)
    await second.drain()
    assert posted == []
    assert second.counters["closed"] == 0
    assert second.counters["claimed"] == 1
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_a_recovered_close_refuses_an_assignment_a_new_session_has_taken_over():
    """Must not steal a live lesson. The read-back cannot see this; the guard can."""
    ledger = DurableMemoryLedger()
    first = reaper(ledger)
    first.register(make_intent(deadline=1045.0))
    await drain_side_tasks(first)
    posted: list = []

    async def post(intent):
        posted.append(intent)

    second = reaper(ledger, post_fn=post, token_fn=_static_token)
    await second.recover()
    # The robot came back on a DIFFERENT session for the same assignment. The
    # assignment is legitimately RUNNING, so `assignment_fn` reports RUNNING and
    # only the binding guard can refuse this close.
    second.claim(ASSIGNMENT, OTHER_SESSION)
    await drain_side_tasks(second)
    await second.drain()
    await drain_side_tasks(second)
    assert posted == []
    assert second.counters["skipped_superseded_session"] == 1
    assert second.counters["closed"] == 0
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_a_recovered_close_still_loses_to_a_genuine_completion():
    """(c) in unit form: the pre-fire read-back applies to recovered intents too."""
    ledger = DurableMemoryLedger()
    first = reaper(ledger)
    first.register(make_intent(deadline=1045.0))
    await drain_side_tasks(first)
    posted: list = []

    async def post(intent):
        posted.append(intent)

    async def completed(intent):
        return {"id": intent.assignment_id, "state": "COMPLETED"}

    second = reaper(
        ledger, post_fn=post, assignment_fn=completed, token_fn=_static_token
    )
    await second.recover()
    await second.drain()
    await drain_side_tasks(second)
    assert posted == []
    assert second.counters["skipped_already_terminal"] == 1
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_an_in_process_intent_wins_over_a_recovered_duplicate():
    ledger = DurableMemoryLedger()
    r = reaper(ledger, token_fn=_static_token)
    live = make_intent(deadline=1045.0)
    r.register(live)
    await drain_side_tasks(r)
    summary = await r.recover()
    assert summary["recovered"] == 0
    assert summary["skipped"] == 1
    assert r._intents[(ASSIGNMENT, SESSION)] is live
    assert live.token == TOKEN


# ── 6. the Redis-unavailable decision, at recovery time ───────────────────────


@pytest.mark.asyncio
async def test_a_ledger_that_cannot_be_read_is_reported_as_unavailable_not_as_empty():
    lines: list = []
    redis = FakeRedis()
    redis.fail = True
    r = reaper(RedisIntentLedger(redis, namespace="cpr"), logger=_recording_logger(lines))
    summary = await r.recover()
    assert summary["available"] is False
    assert summary["recovered"] == 0
    assert r.counters["recovery_failed"] == 1
    failed = [l for l in lines if "recovery_failed" in l[1]]
    assert failed and failed[0][0] == "error"
    # And crucially, it is NOT reported as a completed recovery.
    assert not [l for l in lines if "recovery_complete" in l[1]]


@pytest.mark.asyncio
async def test_recovery_retries_and_succeeds_when_redis_comes_back():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    await ledger.put(make_intent(deadline_ms=1_700_000_030_000), ttl_sec=1000.0)
    redis.fail = True
    slept: list = []

    async def sleep(delay):
        slept.append(delay)
        redis.fail = False  # Redis returns during the first backoff.

    r = reaper(ledger, sleep=sleep, token_fn=_static_token)
    summary = await r.recover_with_retry(backoff=(5.0, 15.0))
    assert slept == [5.0]
    assert summary["available"] is True
    assert summary["recovered"] == 1


@pytest.mark.asyncio
async def test_a_recovery_that_never_succeeds_says_armed_closes_may_have_been_lost():
    lines: list = []
    redis = FakeRedis()
    redis.fail = True

    async def sleep(_delay):
        return None

    r = reaper(
        RedisIntentLedger(redis, namespace="cpr"),
        sleep=sleep,
        logger=_recording_logger(lines),
    )
    summary = await r.recover_with_retry(backoff=(1.0, 2.0))
    assert summary["available"] is False
    assert r.counters["recovery_failed"] == 3
    final = [l for l in lines if "recovery_unavailable" in l[1]]
    assert final and final[0][0] == "error"
    assert "may not have been recovered" in final[0][1]


@pytest.mark.asyncio
async def test_a_deployment_with_no_redis_says_so_once_rather_than_reporting_success():
    lines: list = []
    r = reaper(InMemoryIntentLedger(), logger=_recording_logger(lines))
    summary = await r.recover()
    assert summary == {"durable": False, "recovered": 0, "skipped": 0, "available": True}
    skipped = [l for l in lines if "recovery_skipped" in l[1]]
    assert skipped and skipped[0][0] == "info"
    assert "ledger_not_durable" in skipped[0][1]


# ── 7. the record's lifecycle around firing ───────────────────────────────────


@pytest.mark.asyncio
async def test_a_close_that_fired_removes_its_record():
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")
    posted: list = []

    async def post(intent):
        posted.append(intent)

    r = reaper(ledger, post_fn=post)
    r.register(make_intent(deadline=0.0))
    await drain_side_tasks(r)
    await r.drain()
    await drain_side_tasks(r)
    assert len(posted) == 1
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_a_close_that_exhausted_its_retries_removes_its_record_too():
    """Otherwise every later restart would re-attempt a close this one gave up on."""
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")

    async def post(_intent):
        raise ConnectionError("backend down")

    r = reaper(ledger, post_fn=post, max_attempts=1)
    r.register(make_intent(deadline=0.0))
    await drain_side_tasks(r)
    await r.drain()
    await drain_side_tasks(r)
    assert r.counters["failed"] == 1
    assert await ledger.load() == ()


@pytest.mark.asyncio
async def test_a_retry_persists_its_new_deadline_and_attempt_count():
    """A restart mid-retry resumes the retry rather than restarting the budget."""
    redis = FakeRedis()
    ledger = RedisIntentLedger(redis, namespace="cpr")

    async def post(_intent):
        raise ConnectionError("backend down")

    r = reaper(ledger, post_fn=post, max_attempts=4, retry_backoff_sec=5.0)
    r.register(make_intent(deadline=0.0))
    await drain_side_tasks(r)
    await r.drain()
    await drain_side_tasks(r)
    records = await ledger.load()
    assert len(records) == 1
    assert records[0]["attempts"] == 1
    assert records[0]["deadlineMs"] == 1_700_000_000_000 + 5000


# ── helpers ───────────────────────────────────────────────────────────────────


async def _static_token(_intent):
    return "recovered-token"


def _recording_logger(sink: list):
    class _Bound:
        def bind(self, **_kwargs):
            return self

        def info(self, message):
            sink.append(("info", message))

        def warning(self, message):
            sink.append(("warning", message))

        def error(self, message):
            sink.append(("error", message))

    return _Bound()


# ── 8. a HUNG ledger, which is not the same as a refusing one ─────────────────
#
# Found on the real stack (runtime/traces/d10-d2-redis-blink-at-arm-v3/), not here: a
# `docker pause`d Redis keeps its socket open and simply never answers, and
# `redis.asyncio` sets no default socket timeout. The store blocked across the whole
# outage, no exception was ever raised, and the intent was silently non-durable with
# NO warning — the exact defect D10 names, in a new place. Every unit ledger before
# these cases either answered or raised, which is why nothing here caught it.


class HangingLedger(InMemoryIntentLedger):
    """A ledger that accepts the call and never answers."""

    durable = True

    def __init__(self) -> None:
        super().__init__()
        self.hanging = True

    async def put(self, intent, *, ttl_sec):
        if self.hanging:
            await asyncio.Event().wait()
        return await super().put(intent, ttl_sec=ttl_sec)

    async def load(self):
        if self.hanging:
            await asyncio.Event().wait()
        return await super().load()


@pytest.mark.asyncio
async def test_a_hung_ledger_is_reported_exactly_like_a_refusing_one():
    lines: list = []
    ledger = HangingLedger()
    r = reaper(ledger, logger=_recording_logger(lines), ledger_timeout_sec=0.02)
    intent = make_intent(deadline=1045.0)
    assert r.register(intent) is True
    await drain_side_tasks(r)
    assert intent.durable is False
    assert r.counters["persist_failed"] == 1
    assert r.counters["armed_non_durable"] == 1
    warned = [l for l in lines if "armed_non_durable" in l[1]]
    assert warned and warned[0][0] == "warning"
    assert "ledger_unavailable" in warned[0][1]
    assert "TimeoutError" in warned[0][1]
    # And the close is still armed, which is the whole point.
    assert r.pending_keys() == ((ASSIGNMENT, SESSION),)


@pytest.mark.asyncio
async def test_a_hung_ledger_becomes_durable_once_it_answers_again():
    ledger = HangingLedger()
    r = reaper(ledger, ledger_timeout_sec=0.02)
    intent = make_intent(deadline=1045.0)
    r.register(intent)
    await drain_side_tasks(r)
    assert intent.durable is False
    ledger.hanging = False
    r._last_repersist = 0.0
    await r._repersist_pending()
    assert intent.durable is True


@pytest.mark.asyncio
async def test_a_hung_ledger_at_recovery_is_unavailable_not_empty():
    lines: list = []
    r = reaper(HangingLedger(), logger=_recording_logger(lines), ledger_timeout_sec=0.02)
    summary = await r.recover()
    assert summary["available"] is False
    assert r.counters["recovery_failed"] == 1
    failed = [l for l in lines if "recovery_failed" in l[1]]
    assert failed and "TimeoutError" in failed[0][1]
