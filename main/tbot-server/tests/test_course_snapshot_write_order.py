"""Local asynchronous snapshot writes through real isolated Redis."""

import asyncio
import uuid

import pytest
from redis import asyncio as redis_asyncio

from core.lesson.course_snapshot_store import RedisCourseModeSnapshotStore
from core.lesson.course_orchestrator import SessionState
from core.lesson.runtime import LessonRuntime
from tests.test_course_mode_runtime_integration import (
    _AcknowledgingForwarder, _Conn, _Forwarder, contract,
)
from tests.test_course_terminal_store_race import owned_redis


@pytest.mark.asyncio
async def test_older_pending_snapshot_must_not_replace_newer_operation(owned_redis):
    old_client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    new_client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    read_client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    old_pending = asyncio.Event()
    release_old = asyncio.Event()
    new_written = asyncio.Event()

    class DelayedFirstRequest:
        first = True

        async def set(self, *args, **kwargs):
            if self.first:
                self.first = False
                old_pending.set()
                await release_old.wait()
                return await old_client.set(*args, **kwargs)
            result = await new_client.set(*args, **kwargs)
            new_written.set()
            return result

    namespace = "t13-snapshot-test-" + uuid.uuid4().hex
    store = RedisCourseModeSnapshotStore(
        url=owned_redis, namespace=namespace, client=DelayedFirstRequest(),
    )
    reader = RedisCourseModeSnapshotStore(url=owned_redis, namespace=namespace, client=read_client)
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_Forwarder(), course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
    )
    old_write = asyncio.create_task(runtime.persist_course_mode_snapshot())
    newer_operation = None
    try:
        await asyncio.wait_for(old_pending.wait(), timeout=2)
        newer_operation = asyncio.create_task(runtime.course_continue({
            "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
            "observationId": "newer-durable-opening",
        }))
        # A serialized writer blocks here; an unfenced writer reaches real Redis.
        try:
            await asyncio.wait_for(new_written.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            pass
        release_old.set()
        await asyncio.wait_for(old_write, timeout=2)
        assert (await asyncio.wait_for(newer_operation, timeout=2))["accepted"] is True
        final = await reader.load("device-1", "a1")
        assert final["orchestrator"]["sessionState"] == "OPENING"
        assert final["operationResults"][0]["observationId"] == "newer-durable-opening"
    finally:
        release_old.set()
        tasks = [task for task in (old_write, newer_operation) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await old_client.aclose()
        await new_client.aclose()
        await read_client.aclose()


@pytest.mark.asyncio
async def test_failed_evidence_ack_write_preserves_concurrent_new_evidence(owned_redis):
    client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    read_client = redis_asyncio.from_url(owned_redis, decode_responses=True)
    ack_pending = asyncio.Event()
    release_ack = asyncio.Event()

    class FailSelectedRequest:
        fail_next = False

        async def set(self, *args, **kwargs):
            if self.fail_next:
                self.fail_next = False
                ack_pending.set()
                await release_ack.wait()
                raise OSError("ack snapshot write unavailable")
            return await client.set(*args, **kwargs)

    transport = FailSelectedRequest()
    namespace = "t13-snapshot-ack-" + uuid.uuid4().hex
    store = RedisCourseModeSnapshotStore(url=owned_redis, namespace=namespace, client=transport)
    reader = RedisCourseModeSnapshotStore(url=owned_redis, namespace=namespace, client=read_client)
    forwarder = _AcknowledgingForwarder()
    runtime = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=forwarder, course_mode_snapshot_store=store,
        course_mode_snapshot_device_id="device-1",
    )
    runtime.course_mode.orchestrator.session_state = SessionState.WORD_ACTIVE
    runtime.course_mode.orchestrator.active_mastery.record_model(now_ms=1_000)
    runtime.course_mode.orchestrator.active_mastery.record_intervening_activity()
    observation = {
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "older-recall", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    }
    ack_task = newer_operation = None
    try:
        decision = await runtime.course_observe_child(observation)
        assert decision["accepted"] is True
        older = runtime.course_mode.pending_evidence_batches()[0]
        plan = {
            "lessonSessionId": runtime.session_id, "turnSequenceId": 2,
            "observationId": "recall-plan", "planId": "recall-plan",
            "decisionId": decision["decisionId"], "acknowledgment": "I hear you.",
            "relation": "", "guidance": "", "invitation": "Ready?",
            "questionCount": 1, "embodiedIntent": decision["embodiedIntent"],
            "targetFactsUsed": [], "praiseLevel": "engagement",
            "safetyMode": False, "normalMiss": False,
        }
        assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
        assert await runtime.commit_course_response_plan(plan) is True
        transport.fail_next = True
        ack_task = asyncio.create_task(runtime._ack_course_evidence_batch(older))
        await asyncio.wait_for(ack_pending.wait(), timeout=2)
        newer_operation = asyncio.create_task(runtime.course_observe_child({
            **observation, "turnSequenceId": 3, "observationId": "newer-transfer",
            "activityId": "cat-transfer-scene-01", "contextId": "cat_second_visual_scene",
        }))
        await asyncio.sleep(0)
        release_ack.set()
        await asyncio.wait_for(ack_task, timeout=2)
        newer_result = await asyncio.wait_for(newer_operation, timeout=2)
        assert newer_result["accepted"] is True
        assert newer_result["evidenceEvent"]["activityId"] == "cat-transfer-scene-01"
        await runtime.persist_course_mode_snapshot()
        durable = await reader.load("device-1", "a1")
        assert any(
            event["activityId"] == "cat-transfer-scene-01"
            for batch in durable["pendingEvidenceBatches"] for event in batch["events"]
        )
    finally:
        release_ack.set()
        tasks = [task for task in (ack_task, newer_operation) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.aclose()
        await read_client.aclose()
