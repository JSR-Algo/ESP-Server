"""A failed later operation must retain earlier unacknowledged evidence."""

import pytest

from core.lesson.course_orchestrator import SessionState
from core.lesson.course_snapshot_store import MemoryCourseModeSnapshotStore
from core.lesson.runtime import LessonRuntime
from tests.test_course_mode_runtime_integration import (
    _AcknowledgingForwarder,
    _Conn,
    contract,
)


class FailingStore(MemoryCourseModeSnapshotStore):
    fail = False

    async def store(self, device_id, assignment_id, snapshot):
        if self.fail:
            raise RuntimeError("snapshot unavailable")
        await super().store(device_id, assignment_id, snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    ["apply", "invalid_apply", "rollback", "commit", "mark_delivery"],
)
async def test_failed_later_operation_preserves_unacknowledged_evidence(operation):
    store = FailingStore()
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
    decision = await runtime.course_observe_child({
        "lessonSessionId": runtime.session_id, "turnSequenceId": 1,
        "observationId": "older-evidence", "semanticClass": "target_en",
        "speechClass": "exact", "language": "en", "intent": "answer",
        "engagement": "engaged", "safetyClass": "normal", "assessmentEligible": True,
        "confidenceBand": "high", "activityId": "cat-recall-visual-02",
        "contextId": "cat_primary_visual_recall", "robotAudioContaminated": False,
        "targetTextVisible": False,
    })
    assert decision["accepted"] is True
    pending = runtime.course_mode.pending_evidence_batches()
    assert len(pending) == 1
    assert pending[0]["events"][0]["type"] == "word_evidence_recorded"
    assert forwarder.batches == pending
    plan = {
        "lessonSessionId": runtime.session_id, "turnSequenceId": 2,
        "observationId": "later-plan", "planId": "later-plan",
        "decisionId": decision["decisionId"], "acknowledgment": "I hear you.",
        "relation": "", "guidance": "", "invitation": "Ready?",
        "questionCount": 1, "embodiedIntent": decision["embodiedIntent"],
        "targetFactsUsed": [], "praiseLevel": "engagement",
        "safetyMode": False, "normalMiss": False,
    }
    if operation not in {"apply", "invalid_apply"}:
        assert (await runtime.course_apply_response_plan(plan))["accepted"] is True
    if operation == "commit":
        assert await runtime.mark_response_plan_delivery_attempted(plan) is True
    if operation == "invalid_apply":
        plan = {**plan, "questionCount": 2}

    durable_before = await store.load("device-1", "a1")
    assert durable_before["pendingEvidenceBatches"] == pending
    store.fail = True
    if operation in {"apply", "invalid_apply"}:
        assert await runtime.course_apply_response_plan(plan) == {
            "accepted": False, "code": "COURSE_SNAPSHOT_PERSIST_FAILED",
        }
    elif operation == "rollback":
        assert await runtime.rollback_course_response_plan(plan) is False
    elif operation == "commit":
        assert await runtime.commit_course_response_plan(plan) is False
    else:
        assert await runtime.mark_response_plan_delivery_attempted(plan) is False
    store.fail = False

    assert runtime.course_mode.pending_evidence_batches() == pending
    await runtime.persist_course_mode_snapshot()
    durable_after = await store.load("device-1", "a1")
    assert durable_after["pendingEvidenceBatches"] == pending

    restarted = LessonRuntime(
        _Conn(), assignment={"assignmentId": "a1", "lessonId": "l1"},
        manifest={"courseModeContract": contract()}, asset_cache=object(),
        forwarder=_AcknowledgingForwarder(), course_mode_snapshot=durable_after,
    )
    assert restarted.course_mode.pending_evidence_batches() == pending
