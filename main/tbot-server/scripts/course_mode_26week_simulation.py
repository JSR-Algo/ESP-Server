#!/usr/bin/env python3
"""Software-only 26-week Course Mode simulation using the shipped parser/runtime."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.lesson.course_mode_contract import CourseModeContract
from core.lesson.course_orchestrator import ChildObservation, CourseOrchestrator, SessionState
from core.lesson.runtime import CourseModeRuntimeAdapter


RESPONSE_MATRIX = {
    "correct": ("target_en", "exact", "en", "answer", "high", "normal"),
    "near": ("target_en", "near", "en", "answer", "high", "normal"),
    "incorrect": ("other", "incorrect", "en", "answer", "high", "normal"),
    "silence": ("silence", "silence", "en", "silence", "high", "normal"),
    "vietnamese": ("meaning_vi", "not_applicable", "vi", "answer", "high", "normal"),
    "help": ("help", "not_applicable", "en", "help", "high", "normal"),
    "asr_failure": ("target_en", "exact", "en", "answer", "low", "normal"),
    "fatigue": ("other", "not_applicable", "en", "fatigue", "high", "normal"),
    "refusal": ("other", "not_applicable", "en", "refusal", "high", "normal"),
    "authored_branch": ("related", "not_applicable", "en", "story", "high", "normal"),
    "safety": ("other", "not_applicable", "en", "answer", "high", "unsafe"),
}
EXPECTED_MATRIX_ACTIONS = {
    "correct": "ADVANCE_ACTIVITY", "near": "ADVANCE_ACTIVITY",
    "incorrect": "SUPPORT_WITH_CLUE", "silence": "OFFER_CHOICE_OR_RETRY",
    "vietnamese": "ADVANCE_ACTIVITY", "help": "MODEL_AND_SUPPORT",
    "asr_failure": "OWN_ASR_UNCERTAINTY", "fatigue": "RESPOND_WITHOUT_REDIRECT",
    "refusal": "RESPOND_WITHOUT_REDIRECT", "authored_branch": "RETURN_THROUGH_AUTHORED_BRIDGE",
    "safety": "PAUSE_FOR_SAFETY",
}
TERMINAL_STATES = {SessionState.COMPLETE, SessionState.CLOSING, SessionState.REGULATION_BREAK}
REAL_ACTIONS = {
    "ADVANCE_ACTIVITY", "COMPLETE_COURSE", "CLOSE_BY_OUTCOME", "SUPPORT_WITH_CLUE",
    "OFFER_CHOICE_OR_RETRY", "MODEL_AND_SUPPORT", "OWN_ASR_UNCERTAINTY",
    "RESPOND_WITHOUT_REDIRECT", "OPEN_CONTEXT_BRANCH", "RETURN_THROUGH_AUTHORED_BRIDGE",
    "PAUSE_FOR_SAFETY", "DUPLICATE_IGNORED",
}
PEDAGOGY_WEEKS = {
    "tprGesture": {1, 5, 9, 10, 14, 17},
    "pictureDiscovery": {2, 6, 11, 15, 19},
    "storyContext": {3, 12, 21, 24},
    "rolePlay": {7, 16, 20, 22},
    "spiralCheckpoint": {4, 8, 13, 18, 23, 25},
    "celebrationShowcase": {26},
}
PEDAGOGY_MARKERS = {
    "tprGesture": "movement_greeting",
    "pictureDiscovery": "picture_greeting",
    "storyContext": "story_greeting",
    "rolePlay": "role_greeting",
    "spiralCheckpoint": "checkpoint_welcome",
    "celebrationShowcase": "showcase_welcome",
}


def backend_root() -> Path:
    configured = os.environ.get("TBOT_BACKEND_COURSE_MODE_ROOT")
    if configured:
        return Path(configured).resolve()
    tbot_root = Path(__file__).resolve().parents[5]
    worktree = tbot_root / "tbot-backend/.worktrees/course-mode-26week-single-version"
    return worktree if worktree.is_dir() else tbot_root / "tbot-backend"


def load_backend_contracts(root: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    source_root = (root or backend_root()).resolve()
    verifier = source_root / "scripts/verify-course-mode-curriculum.mjs"
    with tempfile.TemporaryDirectory(prefix="tbot-course-mode-e2e-") as directory:
        output = Path(directory) / "contracts.json"
        completed = subprocess.run(
            ["node", str(verifier), "--contracts-output", str(output)],
            cwd=source_root, check=False, capture_output=True, text=True, timeout=30,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"backend curriculum verifier failed: {completed.stdout}{completed.stderr}")
        fixture = json.loads(output.read_text(encoding="utf-8"))
    if fixture.get("status") != "pass" or fixture.get("lessonCount") != 26:
        raise ValueError("backend curriculum export is not a passing 26-lesson fixture")
    return fixture, fixture["contracts"]


def observation(contract: CourseModeContract, scenario: str, sequence: int) -> ChildObservation:
    activity = contract.activity(contract.activities[0].activity_id)
    semantic, speech, language, intent, confidence, safety = RESPONSE_MATRIX[scenario]
    return ChildObservation(
        observation_id=f"sim-{scenario}-{sequence}", turn_sequence_id=sequence,
        semantic_class=semantic, speech_class=speech, language=language, intent=intent,
        engagement="engaged", safety_class=safety, assessment_eligible=True,
        confidence_band=confidence, activity_id=activity.activity_id,
        context_id=activity.context_id, now_ms=sequence * 1_000,
        robot_audio_contaminated=False, target_text_visible=False,
    )


def _activity_observation(
    contract: CourseModeContract, activity_id: str, scenario: str, sequence: int,
) -> ChildObservation:
    base = observation(contract, scenario, sequence)
    activity = contract.activity(activity_id)
    return ChildObservation(**{
        **base.__dict__, "activity_id": activity_id, "context_id": activity.context_id,
        "observation_id": f"sim-{scenario}-{activity_id}-{sequence}",
    })


def simulate_response(contract: CourseModeContract, scenario: str) -> dict[str, Any]:
    runtime = CourseOrchestrator(contract, started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE
    decision = runtime.observe(observation(contract, scenario, 1))
    if scenario == "authored_branch":
        if not decision.branch_id:
            raise AssertionError("authored story branch did not open")
        decision = runtime.close_context_branch(
            branch_id=decision.branch_id, bridge_intent="resume_active_word_visual",
            child_detail_code="related_pet",
        )
    return {
        "action": decision.action, "attempt": decision.attempt,
        "state": decision.next_state.value, "activityId": decision.activity_id,
    }


def simulate_completion(contract: CourseModeContract) -> dict[str, Any]:
    runtime = CourseOrchestrator(contract, started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE
    actions: list[str] = []
    seen: set[tuple[str, str]] = set()
    for sequence in range(1, len(contract.activities) + 2):
        activity_id = runtime.active_activity_id
        identity = (activity_id, runtime.session_state.value)
        if identity in seen:
            raise AssertionError(f"runtime loop at {activity_id}")
        seen.add(identity)
        decision = runtime.observe(_activity_observation(contract, activity_id, "correct", sequence))
        actions.append(decision.action)
        if runtime.session_state in {SessionState.COMPLETE, SessionState.CLOSING}:
            break
    if runtime.session_state not in {SessionState.COMPLETE, SessionState.CLOSING}:
        raise AssertionError("lesson did not reach terminal completion")
    return {"steps": len(actions), "actions": actions, "state": runtime.session_state.value}


def simulate_attempt_ceiling(contract: CourseModeContract) -> dict[str, Any]:
    runtime = CourseOrchestrator(contract, started_at_ms=0, soft_deadline_ms=480_000)
    runtime.session_state = SessionState.WORD_ACTIVE
    attempts = []
    for sequence in range(1, contract.max_attempts + 1):
        decision = runtime.observe(_activity_observation(
            contract, runtime.active_activity_id, "incorrect", sequence,
        ))
        attempts.append(decision.attempt)
        runtime = CourseOrchestrator.restore(
            contract, json.loads(json.dumps(runtime.snapshot())),
        )
        if runtime.session_state in TERMINAL_STATES:
            break
    if not attempts or max(attempts) > contract.max_attempts:
        raise AssertionError("maxAttempts ceiling was exceeded")
    if runtime.session_state not in TERMINAL_STATES:
        raise AssertionError("attempt ceiling did not choose an authored safe exit")
    return {"attempts": attempts, "state": runtime.session_state.value}


def _adapter_args(adapter: CourseModeRuntimeAdapter, scenario: str) -> dict[str, Any]:
    activity = adapter.contract.activity(adapter.orchestrator.active_activity_id)
    semantic, speech, language, intent, confidence, safety = RESPONSE_MATRIX[scenario]
    return {
        "lessonSessionId": adapter.lesson_session_id, "turnSequenceId": 1,
        "observationId": f"delivery-{adapter.contract.fixture_id}",
        "semanticClass": semantic, "speechClass": speech, "language": language,
        "intent": intent, "engagement": "engaged", "safetyClass": safety,
        "assessmentEligible": True, "confidenceBand": confidence,
        "activityId": activity.activity_id, "contextId": activity.context_id,
        "robotAudioContaminated": False, "targetTextVisible": False,
    }


async def simulate_disconnect_resume(contract: CourseModeContract) -> dict[str, Any]:
    adapter = CourseModeRuntimeAdapter(contract, clock=lambda: 1.0, wall_clock=lambda: 1.0)
    adapter.orchestrator.session_state = SessionState.WORD_ACTIVE
    arguments = _adapter_args(adapter, "correct")
    first = await adapter.course_observe_child(arguments)
    pending = adapter.pending_activity_deliveries()
    if len(pending) != 1:
        raise AssertionError("expected one stable pending activity delivery")
    snapshot = json.loads(json.dumps(adapter.durable_snapshot()))
    restored = CourseModeRuntimeAdapter.restore(
        contract, snapshot, clock=lambda: 2.0, wall_clock=lambda: 2.0,
    )
    replay = await restored.course_observe_child(arguments)
    restored_pending = restored.pending_activity_deliveries()
    if replay != first or len(restored_pending) != 1 or restored_pending[0][1] != pending[0][1]:
        raise AssertionError("disconnect replay changed decision or delivery identity")
    if not restored.mark_activity_decision_delivered(restored_pending[0][0].decision_id):
        raise AssertionError("pending delivery could not be acknowledged")
    if restored.pending_activity_deliveries():
        raise AssertionError("delivery outbox retained an acknowledged duplicate")
    return {"deliveryId": pending[0][1], "deduped": True}


def validate_pedagogy_mapping(contracts: Iterable[CourseModeContract]) -> None:
    contracts = list(contracts)
    for pedagogy, weeks in PEDAGOGY_WEEKS.items():
        marker = PEDAGOGY_MARKERS[pedagogy]
        actual = {
            index for index, contract in enumerate(contracts, 1)
            if contract.activities[0].activity_type == marker
        }
        if actual != weeks:
            raise AssertionError(f"{pedagogy} week mapping drift: {sorted(actual)}")


def simulate_contracts(raw_contracts: list[dict[str, Any]]) -> dict[str, Any]:
    contracts = [CourseModeContract.from_mapping(value) for value in raw_contracts]
    validate_pedagogy_mapping(contracts)
    lessons = []
    all_actions: set[str] = set()
    for week, contract in enumerate(contracts, 1):
        matrix = {scenario: simulate_response(contract, scenario) for scenario in RESPONSE_MATRIX}
        for scenario, expected in EXPECTED_MATRIX_ACTIONS.items():
            if matrix[scenario]["action"] != expected:
                raise AssertionError(
                    f"week {week} {scenario} emitted {matrix[scenario]['action']}, expected {expected}"
                )
        completion = simulate_completion(contract)
        attempt_ceiling = simulate_attempt_ceiling(contract)
        delivery = asyncio.run(simulate_disconnect_resume(contract))
        actions = {result["action"] for result in matrix.values()} | set(completion["actions"])
        if not actions <= REAL_ACTIONS:
            raise AssertionError(f"week {week} emitted invented actions: {sorted(actions - REAL_ACTIONS)}")
        if any(result["attempt"] > contract.max_attempts for result in matrix.values()):
            raise AssertionError(f"week {week} exceeded maxAttempts")
        if completion["steps"] > len(contract.activities):
            raise AssertionError(f"week {week} exceeded authored step bound")
        if sum(activity.expected_duration_sec for activity in contract.activities) > contract.soft_deadline_sec:
            raise AssertionError(f"week {week} exceeded authored time bound")
        for activity in contract.activities:
            visual = activity.visual
            if visual["objectAssetKey"] is None and visual["strategy"] != "embodiedFallback":
                raise AssertionError(f"week {week} object-null fallback mismatch")
            if visual["objectAssetKey"] is not None and visual["strategy"] != "publishedTeachingObject":
                raise AssertionError(f"week {week} object binding mismatch")
            if activity.stage in {"RECALL", "TRANSFER", "DELAYED_RECALL"}:
                if any(activity.answer_policy[field] for field in (
                    "targetTextVisible", "targetAudioBeforeAssessment",
                    "spokenTargetInPrompt", "multipleChoiceContainsTarget",
                )):
                    raise AssertionError(f"week {week} protected activity leaks an answer")
        if week == 19 and not any(
            activity.visual["objectAssetKey"] is None and activity.visual["fallback"] == "robotActing"
            for activity in contract.activities
        ):
            raise AssertionError("week 19 object-null robot fallback is missing")
        all_actions.update(actions)
        lessons.append({
            "week": week, "fixtureId": contract.fixture_id, "activityCount": len(contract.activities),
            "completion": completion, "attemptCeiling": attempt_ceiling,
            "matrix": matrix, "delivery": delivery,
        })
    return {
        "schemaVersion": 1, "simulator": "course-mode-26week.v1", "status": "pass",
        "lessonCount": len(lessons), "scenarioCount": len(RESPONSE_MATRIX),
        "pedagogyCount": len(PEDAGOGY_WEEKS), "actions": sorted(all_actions), "lessons": lessons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path, default=None)
    parser.add_argument("--contracts", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.contracts:
        fixture = json.loads(args.contracts.read_text(encoding="utf-8"))
        contracts = fixture["contracts"]
    else:
        _, contracts = load_backend_contracts(args.backend_root)
    try:
        summary = simulate_contracts(copy.deepcopy(contracts))
    except Exception as exc:
        print(json.dumps({
            "schemaVersion": 1, "simulator": "course-mode-26week.v1", "status": "fail",
            "error": f"{type(exc).__name__}: {exc}",
        }, sort_keys=True))
        return 1
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
