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
    "refusal": "RESPOND_WITHOUT_REDIRECT", "authored_branch": "OPEN_CONTEXT_BRANCH",
    "safety": "PAUSE_FOR_SAFETY",
}
TERMINAL_STATES = {SessionState.COMPLETE, SessionState.CLOSING, SessionState.REGULATION_BREAK}
REAL_ACTIONS = {
    "ADVANCE_ACTIVITY", "COMPLETE_COURSE", "CLOSE_BY_OUTCOME", "SUPPORT_WITH_CLUE",
    "OFFER_CHOICE_OR_RETRY", "MODEL_AND_SUPPORT", "OWN_ASR_UNCERTAINTY",
    "RESPOND_WITHOUT_REDIRECT", "OPEN_CONTEXT_BRANCH", "RETURN_THROUGH_AUTHORED_BRIDGE",
    "PAUSE_FOR_SAFETY", "DUPLICATE_IGNORED", "CLOSE_BY_CHILD_CHOICE",
    "CLOSE_BY_SAFETY_CHOICE", "PRESENT_INTERVENING_ACTIVITY",
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


def observation(
    contract: CourseModeContract, activity_id: str, scenario: str, sequence: int,
    *, intent_override: str | None = None,
) -> ChildObservation:
    activity = contract.activity(activity_id)
    semantic, speech, language, intent, confidence, safety = RESPONSE_MATRIX[scenario]
    return ChildObservation(
        observation_id=f"sim-{scenario}-{sequence}", turn_sequence_id=sequence,
        semantic_class=semantic, speech_class=speech, language=language,
        intent=intent_override or intent,
        engagement="engaged", safety_class=safety, assessment_eligible=True,
        confidence_band=confidence, activity_id=activity.activity_id,
        context_id=activity.context_id, now_ms=sequence * 1_000,
        robot_audio_contaminated=False, target_text_visible=False,
    )


def _activity_observation(
    contract: CourseModeContract, activity_id: str, scenario: str, sequence: int,
    *, intent_override: str | None = None,
) -> ChildObservation:
    base = observation(
        contract, activity_id, scenario, sequence, intent_override=intent_override,
    )
    activity = contract.activity(activity_id)
    return ChildObservation(**{
        **base.__dict__, "activity_id": activity_id, "context_id": activity.context_id,
        "observation_id": f"sim-{scenario}-{activity_id}-{sequence}",
    })


def _record(decision: Any, scenario: str) -> dict[str, Any]:
    return {
        "scenario": scenario, "outcome": scenario, "action": decision.action,
        "attempt": decision.attempt,
        "state": decision.next_state.value, "activityId": decision.activity_id,
    }


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


async def simulate_terminal_path(contract: CourseModeContract, scenario: str) -> dict[str, Any]:
    adapter = CourseModeRuntimeAdapter(contract, clock=lambda: 1.0, wall_clock=lambda: 1.0)
    adapter.orchestrator.session_state = SessionState.WORD_ACTIVE
    first_arguments = _adapter_args(adapter, "correct")
    first = await adapter.course_observe_child(first_arguments)
    pending = adapter.pending_activity_deliveries()
    if len(pending) != 1:
        raise AssertionError("mid-path disconnect has no stable pending delivery")
    restored = CourseModeRuntimeAdapter.restore(
        contract, json.loads(json.dumps(adapter.durable_snapshot())),
        clock=lambda: 2.0, wall_clock=lambda: 2.0,
    )
    replay = await restored.course_observe_child(first_arguments)
    restored_pending = restored.pending_activity_deliveries()
    if replay != first or len(restored_pending) != 1 or restored_pending[0][1] != pending[0][1]:
        raise AssertionError("mid-path replay changed decision or delivery identity")
    restored.mark_activity_decision_delivered(restored_pending[0][0].decision_id)
    if restored.pending_activity_deliveries():
        raise AssertionError("mid-path delivery dedupe left a pending duplicate")

    runtime = restored.orchestrator
    transitions = [{
        "scenario": "disconnect_resume_correct", "action": first["action"],
        "attempt": first.get("attempt", 0), "state": first["nextState"],
        "activityId": first.get("activityId"),
    }]
    scenario_exercised = False
    seen: set[tuple[Any, ...]] = set()
    limit = len(contract.activities) * (contract.max_attempts + 2) + 8
    for sequence in range(2, limit + 2):
        if runtime.session_state in {SessionState.COMPLETE, SessionState.CLOSING}:
            break
        activity_id = runtime.active_activity_id
        current_scenario = scenario if not scenario_exercised else "correct"
        identity = (
            activity_id, runtime.session_state.value, current_scenario,
            tuple(sorted(runtime.snapshot()["activityAttempts"].items())),
            runtime.snapshot()["activeBranchId"],
        )
        if identity in seen:
            raise AssertionError(f"runtime path loop at {activity_id}/{current_scenario}")
        seen.add(identity)
        decision = runtime.observe(_activity_observation(
            contract, activity_id, current_scenario, sequence,
        ))
        transitions.append(_record(decision, current_scenario))
        if not scenario_exercised:
            scenario_exercised = True
            if decision.action != EXPECTED_MATRIX_ACTIONS[scenario]:
                raise AssertionError(
                    f"{scenario} emitted {decision.action}, expected {EXPECTED_MATRIX_ACTIONS[scenario]}"
                )
            if scenario == "authored_branch":
                if not decision.branch_id:
                    raise AssertionError("authored branch did not open")
                bridged = runtime.close_context_branch(
                    branch_id=decision.branch_id,
                    bridge_intent="resume_active_word_visual", child_detail_code="related_pet",
                )
                transitions.append(_record(bridged, "authored_bridge"))
            elif runtime.session_state in {SessionState.REGULATION_BREAK, SessionState.SAFETY_PAUSED}:
                stopped = runtime.observe(_activity_observation(
                    contract, runtime.active_activity_id, "correct", sequence + limit,
                    intent_override="stop",
                ))
                transitions.append(_record(stopped, "safe_stop"))
            elif runtime.session_state is SessionState.TECHNICAL_RECOVERY:
                recovered = runtime.continue_word(now_ms=sequence * 1_000)
                transitions.append(_record(recovered, "technical_recovery"))
    if not scenario_exercised:
        raise AssertionError(f"{scenario} was not exercised")
    if runtime.session_state not in {SessionState.COMPLETE, SessionState.CLOSING}:
        raise AssertionError(f"{scenario} path did not terminate within {limit} transitions")
    if any(item["attempt"] > contract.max_attempts for item in transitions):
        raise AssertionError(f"{scenario} exceeded maxAttempts")
    return {
        "state": runtime.session_state.value, "steps": len(transitions),
        "visitedActivities": [item["activityId"] for item in transitions if item["activityId"]],
        "actions": [item["action"] for item in transitions], "transitions": transitions,
        "deliveryId": pending[0][1], "deduped": True,
    }


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


def _validate_visual_phases(lesson: dict[str, Any], inventory: dict[str, dict[str, Any]]) -> None:
    contract = lesson["contract"]
    phase_by_activity = {
        activity_id: phase
        for phase in lesson["phases"] for activity_id in phase["activityIds"]
    }
    for activity in contract["activities"]:
        phase = phase_by_activity[activity["activityId"]]
        layers = {layer["slot"]: layer for layer in phase["layers"]}
        for slot in ("backgroundScene", "robotOverlay"):
            layer = layers.get(slot)
            published = inventory.get(layer["assetKey"] if layer else "")
            if not published or published["publication_state"] != "published":
                raise AssertionError(f"{activity['activityId']} missing published {slot}")
            if published["id"] != layer["assetVersionId"] or published["sha256"] != layer["sha256"]:
                raise AssertionError(f"{activity['activityId']} {slot} identity mismatch")
            if not published["storage_path"]:
                raise AssertionError(f"{activity['activityId']} {slot} path is empty")
        if layers["backgroundScene"]["metadata"].get("mediaKind") != "image":
            raise AssertionError(f"{activity['activityId']} background is not an image")
        robot_metadata = layers["robotOverlay"]["metadata"]
        if robot_metadata.get("mediaKind") != "video" or robot_metadata.get("hasAudio") is not False:
            raise AssertionError(f"{activity['activityId']} robot fallback is not the silent video binding")
        object_key = activity["visual"]["objectAssetKey"]
        if object_key:
            if layers.get("teachingObject", {}).get("assetKey") != object_key:
                raise AssertionError(f"{activity['activityId']} object phase mismatch")
            if inventory.get(object_key, {}).get("publication_state") != "published":
                raise AssertionError(f"{activity['activityId']} object is not published")
        elif set(layers) != {"backgroundScene", "robotOverlay"}:
            raise AssertionError(f"{activity['activityId']} fallback is not a scene+robot phase")


def simulate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    raw_contracts = fixture["contracts"]
    contracts = [CourseModeContract.from_mapping(value) for value in raw_contracts]
    validate_pedagogy_mapping(contracts)
    lessons_by_week = {lesson["week"]: lesson for lesson in fixture["lessons"]}
    inventory = {item["asset_key"]: item for item in fixture["inventory"]}
    lessons = []
    all_actions: set[str] = set()
    for week, contract in enumerate(contracts, 1):
        _validate_visual_phases(lessons_by_week[week], inventory)
        matrix = {
            scenario: asyncio.run(simulate_terminal_path(contract, scenario))
            for scenario in RESPONSE_MATRIX
        }
        attempt_ceiling = simulate_attempt_ceiling(contract)
        actions = {action for result in matrix.values() for action in result["actions"]}
        if not actions <= REAL_ACTIONS:
            raise AssertionError(f"week {week} emitted invented actions: {sorted(actions - REAL_ACTIONS)}")
        if any(result["steps"] > len(contract.activities) * (contract.max_attempts + 2) + 8 for result in matrix.values()):
            raise AssertionError(f"week {week} exceeded authored transition bound")
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
            "attemptCeiling": attempt_ceiling, "matrix": matrix,
        })
    return {
        "schemaVersion": 1, "simulator": "course-mode-26week.v1", "status": "pass",
        "lessonCount": len(lessons), "scenarioCount": len(RESPONSE_MATRIX),
        "pedagogyCount": len(PEDAGOGY_WEEKS), "actions": sorted(all_actions), "lessons": lessons,
    }


def simulate_contracts(raw_contracts: list[dict[str, Any]]) -> dict[str, Any]:
    _, canonical = load_backend_contracts()
    fixture = dict(canonical)
    fixture["contracts"] = raw_contracts
    fixture["lessons"] = [
        {**lesson, "contract": raw_contracts[index]}
        for index, lesson in enumerate(canonical["lessons"])
    ]
    return simulate_fixture(fixture)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path, default=None)
    parser.add_argument("--contracts", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.contracts:
        fixture = json.loads(args.contracts.read_text(encoding="utf-8"))
        contracts = fixture["contracts"]
    else:
        fixture, contracts = load_backend_contracts(args.backend_root)
    if args.contracts:
        fixture = fixture
    try:
        fixture = copy.deepcopy(fixture)
        fixture["contracts"] = copy.deepcopy(contracts)
        summary = simulate_fixture(fixture)
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
