#!/usr/bin/env python3
"""Software-only 26-week Course Mode simulation using the shipped parser/runtime."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.lesson.course_mode_contract import CourseModeContract, CourseModeContractError
from core.lesson.course_orchestrator import SessionState
from core.lesson.runtime import CourseModeRuntimeAdapter
from scripts.course_mode_candidate_manifest import (
    MAX_CANDIDATE_BYTES,
    _git,
    run_bounded_command,
)


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
ATTEMPT_CEILING_SCENARIOS = {"incorrect", "silence", "help"}
ASR_RECOVERY_REPETITIONS = 2
ATTEMPT_RECOVERY_ACTIONS = {
    "incorrect": {"SUPPORT_WITH_CLUE"},
    "silence": {"OFFER_CHOICE_OR_RETRY", "OFFER_NONVERBAL_CHOICE"},
    "help": {"MODEL_AND_SUPPORT"},
}
REAL_ACTIONS = {
    "GREET_AND_CHECK_IN", "ACKNOWLEDGE_AND_BUILD_CURIOSITY", "CLUE_AND_ELICIT",
    "ADVANCE_ACTIVITY", "COMPLETE_COURSE", "CLOSE_BY_OUTCOME", "SUPPORT_WITH_CLUE",
    "OFFER_CHOICE_OR_RETRY", "OFFER_NONVERBAL_CHOICE", "MODEL_AND_SUPPORT", "OWN_ASR_UNCERTAINTY",
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


class CourseModeSimulationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class BackendRootResolution:
    path: Path | None
    error: str | None
    sha: str | None = None


def resolve_backend_root(
    requested: Path | None, *, expected_sha: str | None = None,
) -> BackendRootResolution:
    configured = requested or (
        Path(value) if (value := os.environ.get("COURSE_MODE_BACKEND_ROOT")) else None
    )
    if configured is None:
        return BackendRootResolution(None, "BACKEND_ROOT_REQUIRED")
    if not configured.is_absolute():
        return BackendRootResolution(None, "BACKEND_IDENTITY_MISMATCH")
    try:
        root = configured.resolve(strict=True)
    except OSError:
        return BackendRootResolution(None, "BACKEND_IDENTITY_MISMATCH")
    if not root.is_dir() or not (root / "scripts/verify-course-mode-curriculum.mjs").is_file():
        return BackendRootResolution(None, "BACKEND_IDENTITY_MISMATCH")
    try:
        top_level = Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
        head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
        dirty = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    except (OSError, RuntimeError):
        return BackendRootResolution(None, "BACKEND_IDENTITY_MISMATCH")
    if top_level != root or dirty or (expected_sha is not None and expected_sha != head):
        return BackendRootResolution(None, "BACKEND_IDENTITY_MISMATCH")
    return BackendRootResolution(root, None, head)


def _read_bounded_regular(path: Path, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise OSError("invalid bounded input")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError("bounded input too large")
            chunks.append(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
        ) or total != before.st_size:
            raise OSError("bounded input changed")
        return b"".join(chunks)
    finally:
        os.close(fd)


def load_backend_contracts(
    root: Path | None = None, *, backend_command: list[str] | None = None,
    timeout_sec: float = 30, expected_sha: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    resolution = resolve_backend_root(root, expected_sha=expected_sha)
    if resolution.error or resolution.path is None:
        raise CourseModeSimulationError(
            resolution.error or "BACKEND_IDENTITY_MISMATCH",
            "an explicit committed backend root is required",
        )
    source_root = resolution.path
    bound_sha = expected_sha or resolution.sha
    if bound_sha is None:
        raise CourseModeSimulationError("BACKEND_IDENTITY_MISMATCH", "backend SHA is unavailable")
    verifier = source_root / "scripts/verify-course-mode-curriculum.mjs"
    with tempfile.TemporaryDirectory(prefix="tbot-course-mode-e2e-") as directory:
        output = Path(directory) / "contracts.json"
        command = backend_command or ["node", str(verifier)]
        try:
            completed = run_bounded_command(
                [*command, "--contracts-output", str(output)], cwd=source_root,
                env=os.environ.copy(), timeout_sec=timeout_sec,
                max_output_bytes=1024 * 1024,
            )
        except OSError:
            raise CourseModeSimulationError("BACKEND_COMMAND_FAILED", "backend command failed") from None
        if completed.error == "not_found":
            raise CourseModeSimulationError("BACKEND_COMMAND_NOT_FOUND", "backend command not found")
        if completed.error == "timeout":
            raise CourseModeSimulationError(
                "BACKEND_COMMAND_TIMEOUT", f"backend command exceeded {timeout_sec:g}s",
            )
        if completed.error == "output":
            raise CourseModeSimulationError(
                "BACKEND_COMMAND_OUTPUT_LIMIT", "backend command output exceeded the limit",
            )
        if completed.error or completed.returncode != 0:
            raise CourseModeSimulationError(
                "BACKEND_COMMAND_FAILED", f"backend command exited {completed.returncode}",
            )
        post_resolution = resolve_backend_root(source_root, expected_sha=bound_sha)
        if post_resolution.error:
            raise CourseModeSimulationError(
                "BACKEND_IDENTITY_MISMATCH", "backend identity changed during verification",
            )
        try:
            fixture = json.loads(_read_bounded_regular(output, 64 * 1024 * 1024).decode("utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CourseModeSimulationError("BACKEND_OUTPUT_INVALID_JSON", str(exc)) from None
    if fixture.get("status") != "pass" or fixture.get("lessonCount") != 26:
        raise CourseModeSimulationError(
            "BACKEND_OUTPUT_INVALID_ENVELOPE",
            "backend curriculum export is not a passing 26-lesson fixture",
        )
    fixture["backendSha"] = bound_sha
    return fixture, fixture["contracts"]


def _record(decision: dict[str, Any], scenario: str) -> dict[str, Any]:
    return {
        "scenario": scenario, "outcome": scenario, "action": decision["action"],
        "attempt": decision.get("attempt", 0),
        "state": decision["nextState"], "activityId": decision.get("activityId"),
    }


def _adapter_args(
    adapter: CourseModeRuntimeAdapter, scenario: str, operation_id: str,
    *, intent_override: str | None = None,
) -> dict[str, Any]:
    activity = adapter.contract.activity(adapter.orchestrator.active_activity_id)
    semantic, speech, language, intent, confidence, safety = RESPONSE_MATRIX[scenario]
    identity = adapter.tool_context()["identity"]
    return {
        "lessonSessionId": identity["lessonSessionId"],
        "turnSequenceId": identity["turnSequenceId"], "observationId": operation_id,
        "semanticClass": semantic, "speechClass": speech, "language": language,
        "intent": intent_override or intent, "engagement": "engaged", "safetyClass": safety,
        "assessmentEligible": True, "confidenceBand": confidence,
        "activityId": activity.activity_id, "contextId": activity.context_id,
        "robotAudioContaminated": False, "targetTextVisible": False,
    }


def _plan_arguments(adapter: CourseModeRuntimeAdapter, decision: dict[str, Any], operation_id: str) -> dict[str, Any]:
    identity = adapter.tool_context()["identity"]
    safety = decision["nextState"] in {"SAFETY_PAUSED", "REGULATION_BREAK"}
    return {
        "lessonSessionId": identity["lessonSessionId"],
        "turnSequenceId": identity["turnSequenceId"], "observationId": operation_id,
        "planId": f"plan-{operation_id}", "decisionId": decision["decisionId"],
        "acknowledgment": "Robot is here." if safety else "I hear you.",
        "relation": "", "guidance": "", "invitation": "", "questionCount": 0,
        "embodiedIntent": decision["embodiedIntent"], "targetFactsUsed": [],
        "praiseLevel": "engagement", "safetyMode": safety, "normalMiss": False,
    }


async def _settle_decision(
    adapter: CourseModeRuntimeAdapter, decision: dict[str, Any], operation_id: str,
    *, acknowledge_deliveries: bool = True,
) -> list[tuple[str, str]]:
    if decision.get("accepted") is True and adapter.tool_context().get("pendingDecision"):
        plan_arguments = _plan_arguments(adapter, decision, f"{operation_id}-plan")
        applied = await adapter.course_apply_response_plan(plan_arguments)
        if applied.get("accepted") is not True:
            raise AssertionError(f"response plan rejected: {applied}")
        if not adapter.mark_response_plan_delivery_attempted(plan_arguments):
            raise AssertionError("response plan delivery was not marked")
        if not adapter.commit_course_response_plan(plan_arguments):
            raise AssertionError("response plan did not commit")
    pending_deliveries = adapter.pending_activity_deliveries()
    activity_id = decision.get("activityId")
    authoritative = (
        activity_id is not None
        and adapter.contract.activity(activity_id).navigation_mode == "authoritative_graph"
    )
    if decision.get("accepted") is True and authoritative:
        if not any(item.decision_id == decision["decisionId"] for item, _ in pending_deliveries):
            raise AssertionError("accepted activity decision has no pending activity delivery")
    delivery_records = [
        (pending_decision.decision_id, delivery_id)
        for pending_decision, delivery_id in pending_deliveries
    ]
    if acknowledge_deliveries:
        for pending_decision, _ in list(adapter.pending_activity_deliveries()):
            if not adapter.mark_activity_decision_delivered(pending_decision.decision_id):
                raise AssertionError("activity delivery ACK failed")
        if adapter.pending_activity_deliveries():
            raise AssertionError("activity outbox did not empty after ACK")
    return delivery_records


def _track_deliveries(
    records: list[tuple[str, str]], owners: dict[str, str], emitted: list[dict[str, str]],
) -> None:
    for decision_id, delivery_id in records:
        owner = owners.setdefault(delivery_id, decision_id)
        if owner != decision_id:
            raise AssertionError(
                f"deliveryId collision: {delivery_id} belongs to {owner} and {decision_id}"
            )
        emitted.append({"decisionId": decision_id, "deliveryId": delivery_id})


def _attempt_recovery_outcome(contract: CourseModeContract, activity_id: str, scenario: str) -> Any:
    activity = contract.activity(activity_id)
    return activity.outcomes.get(scenario) or activity.outcomes.get("help")


def _attempt_ceiling_turn_index(contract: CourseModeContract, scenario: str) -> int:
    for index in range(1, len(contract.activities)):
        activity_id = contract.activities[index].activity_id
        for _ in range(contract.max_attempts - 1):
            outcome = _attempt_recovery_outcome(contract, activity_id, scenario)
            if not outcome or outcome.get("action") not in {"support", "retry"}:
                break
            activity_id = outcome.get("activityId")
            if not activity_id:
                break
        else:
            return index
    raise AssertionError(f"contract has no authored {scenario} recovery runway")


def _expected_attempt_ceiling_action(contract: CourseModeContract, activity_id: str) -> str:
    actions = {outcome["action"] for outcome in contract.activity(activity_id).outcomes.values()}
    if "pause" in actions or not actions.intersection({"close", "complete", "advance"}):
        return "RESPOND_WITHOUT_REDIRECT"
    if "close" in actions:
        return "CLOSE_BY_OUTCOME"
    if "complete" in actions:
        return "COMPLETE_COURSE"
    return "ADVANCE_ACTIVITY"


async def simulate_terminal_path(
    contract: CourseModeContract, scenario: str, *, scenario_turn_index: int,
) -> dict[str, Any]:
    adapter = CourseModeRuntimeAdapter(contract, clock=lambda: 1.0, wall_clock=lambda: 1.0)
    transitions: list[dict[str, Any]] = []
    scenario_exercised = False
    scenario_observations = 0
    scenario_activity_ids: list[str] = []
    scenario_delivery_ids: list[str] = []
    scenario_replay_count = 0
    attempt_levels: list[int] = []
    attempt_ceiling_reached = False
    expected_ceiling_action: str | None = None
    ceiling_action: str | None = None
    seen: set[tuple[Any, ...]] = set()
    delivery_owners: dict[str, str] = {}
    delivery_records: list[dict[str, str]] = []
    replay_delivery_id: str | None = None
    for startup_step in range(3):
        if adapter.orchestrator.session_state is SessionState.WORD_ACTIVE:
            break
        context = adapter.tool_context()
        operation_id = f"{contract.fixture_id}-{scenario}-startup-{startup_step + 1}"
        started = await adapter.course_continue({
            **context["identity"], "observationId": operation_id,
        })
        transitions.append(_record(started, "startup"))
        _track_deliveries(
            await _settle_decision(adapter, started, operation_id),
            delivery_owners, delivery_records,
        )
    if adapter.orchestrator.session_state is not SessionState.WORD_ACTIVE:
        raise AssertionError("startup path loop before WORD_ACTIVE")
    limit = len(contract.activities) * (contract.max_attempts + 2) + 8
    for step in range(limit):
        if adapter.orchestrator.session_state in {SessionState.COMPLETE, SessionState.CLOSING}:
            break
        activity_id = adapter.orchestrator.active_activity_id
        current_scenario = scenario if not scenario_exercised and step >= scenario_turn_index else "correct"
        identity = (
            activity_id, adapter.orchestrator.session_state.value, current_scenario,
            scenario_observations,
            tuple(sorted(adapter.orchestrator.snapshot()["activityAttempts"].items())),
            adapter.orchestrator.snapshot()["activeBranchId"],
        )
        if identity in seen:
            raise AssertionError(f"runtime path loop at {activity_id}/{current_scenario}")
        seen.add(identity)
        operation_id = f"{contract.fixture_id}-{scenario}-turn-{step + 1}"
        arguments = _adapter_args(adapter, current_scenario, operation_id)
        before_sequence = arguments["turnSequenceId"]
        decision = await adapter.course_observe_child(arguments)
        transitions.append(_record(decision, current_scenario))
        scenario_now = current_scenario == scenario and not scenario_exercised
        if scenario_now:
            scenario_observations += 1
            scenario_activity_ids.append(arguments["activityId"])
            attempt = decision.get("attempt", 0)
            if attempt > 0:
                attempt_levels.append(attempt)
            if scenario in ATTEMPT_CEILING_SCENARIOS:
                attempt_ceiling_reached = attempt == contract.max_attempts
                scenario_exercised = attempt_ceiling_reached
                if attempt_ceiling_reached:
                    expected_ceiling_action = _expected_attempt_ceiling_action(
                        contract, arguments["activityId"],
                    )
                    ceiling_action = decision["action"]
                expected_actions = (
                    {expected_ceiling_action} if attempt_ceiling_reached
                    else ATTEMPT_RECOVERY_ACTIONS[scenario]
                )
            elif scenario == "asr_failure":
                scenario_exercised = scenario_observations >= ASR_RECOVERY_REPETITIONS
                expected_actions = {EXPECTED_MATRIX_ACTIONS[scenario]}
            else:
                scenario_exercised = True
                expected_actions = {EXPECTED_MATRIX_ACTIONS[scenario]}
            if decision["action"] not in expected_actions:
                raise AssertionError(
                    f"{scenario} attempt ceiling emitted {decision['action']}, expected {sorted(expected_actions)}"
                )
            pending_before = adapter.pending_activity_deliveries()
            snapshot = json.loads(json.dumps(adapter.durable_snapshot()))
            restored = CourseModeRuntimeAdapter.restore(
                contract, snapshot, clock=lambda: 2.0, wall_clock=lambda: 2.0,
            )
            replay = await restored.course_observe_child(arguments)
            if replay != decision or restored.tool_context()["identity"]["turnSequenceId"] != before_sequence + 1:
                raise AssertionError("scenario replay changed result or turn sequence")
            pending_after = restored.pending_activity_deliveries()
            if [(item.decision_id, delivery) for item, delivery in pending_after] != [
                (item.decision_id, delivery) for item, delivery in pending_before
            ]:
                raise AssertionError("scenario replay changed pending outbox")
            scenario_replay_count += 1
            if decision.get("activityId") is not None:
                scenario_delivery = next(
                    (delivery for item, delivery in pending_after
                     if item.decision_id == decision["decisionId"]),
                    None,
                )
                if scenario_delivery is None:
                    raise AssertionError(
                        f"{scenario} replay has no stable activity delivery for {decision['decisionId']}"
                    )
                scenario_delivery_ids.append(scenario_delivery)
            adapter = restored
            replay_delivery_id = pending_after[-1][1] if pending_after else None
        hold_for_scenario = not scenario_exercised and step + 1 == scenario_turn_index
        new_delivery_records = await _settle_decision(
            adapter, decision, operation_id,
            acknowledge_deliveries=not hold_for_scenario,
        )
        _track_deliveries(new_delivery_records, delivery_owners, delivery_records)
        if scenario_now and new_delivery_records and replay_delivery_id != new_delivery_records[-1][1]:
            raise AssertionError("scenario deliveryId changed after replay")
        if scenario_now and scenario == "authored_branch":
            context = adapter.tool_context()
            active = context.get("activeContext")
            if not active:
                raise AssertionError("authored branch did not expose active context")
            identity_args = context["identity"]
            close_args = {
                **identity_args, "observationId": f"{operation_id}-bridge",
                "branchId": active["branchId"], "bridgeIntent": "resume_active_word_visual",
                "childDetailCode": "related_pet",
            }
            bridged = await adapter.course_close_context(close_args)
            transitions.append(_record(bridged, "authored_bridge"))
            _track_deliveries(
                await _settle_decision(adapter, bridged, f"{operation_id}-bridge"),
                delivery_owners, delivery_records,
            )
        elif scenario_now and adapter.orchestrator.session_state in {SessionState.REGULATION_BREAK, SessionState.SAFETY_PAUSED}:
            stop_args = _adapter_args(adapter, "correct", f"{operation_id}-stop", intent_override="stop")
            stopped = await adapter.course_observe_child(stop_args)
            transitions.append(_record(stopped, "safe_stop"))
            _track_deliveries(
                await _settle_decision(adapter, stopped, f"{operation_id}-stop"),
                delivery_owners, delivery_records,
            )
        elif scenario_now and adapter.orchestrator.session_state is SessionState.TECHNICAL_RECOVERY:
            context = adapter.tool_context()
            continue_args = {**context["identity"], "observationId": f"{operation_id}-recover"}
            recovered = await adapter.course_continue(continue_args)
            transitions.append(_record(recovered, "technical_recovery"))
            _track_deliveries(
                await _settle_decision(adapter, recovered, f"{operation_id}-recover"),
                delivery_owners, delivery_records,
            )
    if not scenario_exercised:
        raise AssertionError(f"{scenario} was not exercised")
    if scenario in ATTEMPT_CEILING_SCENARIOS:
        expected_attempts = list(range(1, contract.max_attempts + 1))
        authored_recovery = all(
            (outcome := _attempt_recovery_outcome(contract, source_id, scenario)) is not None
            and outcome.get("action") in {"support", "retry"}
            and outcome.get("activityId") == target_id
            for source_id, target_id in zip(
                scenario_activity_ids, scenario_activity_ids[1:],
            )
        )
        if (attempt_levels != expected_attempts or not attempt_ceiling_reached
            or not authored_recovery or ceiling_action != expected_ceiling_action):
            raise AssertionError(
                f"{scenario} attempt ceiling coverage {attempt_levels}/{scenario_activity_ids}"
            )
    if scenario == "asr_failure" and scenario_observations < ASR_RECOVERY_REPETITIONS:
        raise AssertionError("asr_failure recovery was not repeated through the public adapter")
    if scenario == "asr_failure" and len(set(scenario_activity_ids)) != 1:
        raise AssertionError("asr_failure recovery changed the eligible activity")
    if adapter.orchestrator.session_state not in {SessionState.COMPLETE, SessionState.CLOSING}:
        raise AssertionError(f"{scenario} path did not terminate within {limit} transitions")
    if any(item["attempt"] > contract.max_attempts for item in transitions):
        raise AssertionError(f"{scenario} exceeded maxAttempts")
    return {
        "state": adapter.orchestrator.session_state.value, "steps": len(transitions),
        "visitedActivities": [item["activityId"] for item in transitions if item["activityId"]],
        "actions": [item["action"] for item in transitions], "transitions": transitions,
        "deliveryIds": list(delivery_owners), "deliveryRecords": delivery_records,
        "replayDeliveryId": replay_delivery_id, "deduped": True,
        "attemptLevels": attempt_levels, "attemptCeilingReached": attempt_ceiling_reached,
        "scenarioObservations": scenario_observations,
        "scenarioActivityIds": scenario_activity_ids,
        "scenarioReplayCount": scenario_replay_count,
        "scenarioDeliveryIds": scenario_delivery_ids,
        "expectedCeilingAction": expected_ceiling_action,
        "ceilingAction": ceiling_action,
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


def _validate_visual_phases(
    lesson: dict[str, Any], inventory: dict[str, dict[str, Any]],
    contract: dict[str, Any],
) -> None:
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


def _normalized_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _contract_drift_message(expected: dict[str, Any], actual: dict[str, Any], week: int) -> str:
    if expected.get("renderer") != actual.get("renderer"):
        return f"week {week} renderer differs from canonical contract"
    for expected_activity, actual_activity in zip(
        expected.get("activities", []), actual.get("activities", []),
    ):
        activity_id = expected_activity.get("activityId", "unknown")
        if expected_activity.get("outcomes") != actual_activity.get("outcomes"):
            return f"week {week} activity {activity_id} outcome differs from canonical contract"
        if expected_activity.get("answerPolicy") != actual_activity.get("answerPolicy"):
            return f"week {week} activity {activity_id} answer policy differs from canonical contract"
        if expected_activity.get("visual") != actual_activity.get("visual"):
            return f"week {week} activity {activity_id} visual differs from canonical contract"
    return f"week {week} contract differs from canonical contract"


def _simulate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    raw_contracts = fixture["contracts"]
    lesson_rows = fixture["lessons"]
    if not isinstance(raw_contracts, list) or len(raw_contracts) != 26:
        raise CourseModeSimulationError(
            "CONTRACT_ENVELOPE_COUNT", "top-level contracts must contain exactly 26 rows",
        )
    if not isinstance(lesson_rows, list) or len(lesson_rows) != 26:
        raise CourseModeSimulationError(
            "LESSON_ENVELOPE_COUNT", "lessons envelope must contain exactly 26 rows",
        )
    if any(not isinstance(lesson, dict) or type(lesson.get("week")) is not int for lesson in lesson_rows):
        raise CourseModeSimulationError(
            "LESSON_WEEK_INVALID", "every lesson week must be an integer",
        )
    actual_weeks = [lesson.get("week") for lesson in lesson_rows]
    if len(set(actual_weeks)) != len(actual_weeks):
        raise CourseModeSimulationError(
            "LESSON_WEEK_DUPLICATE", f"lesson weeks contain duplicates: {actual_weeks}",
        )
    expected_weeks = list(range(1, 27))
    if actual_weeks != expected_weeks:
        raise CourseModeSimulationError(
            "LESSON_WEEK_ORDER_MISMATCH",
            f"lesson weeks must be exactly {expected_weeks}, found {actual_weeks}",
        )
    lessons_by_week = {lesson["week"]: lesson for lesson in lesson_rows}
    canonical_lesson_keys = fixture.get("lessonKeys")
    canonical_checksums = fixture.get("contractChecksums")
    course_key = fixture.get("courseKey")
    if (not isinstance(canonical_lesson_keys, list) or len(canonical_lesson_keys) != 26
        or not isinstance(canonical_checksums, list) or len(canonical_checksums) != 26
        or not isinstance(course_key, str)):
        raise CourseModeSimulationError(
            "CANONICAL_MAPPING_INVALID", "backend export canonical mapping is incomplete",
        )
    normalized_contracts = []
    contracts = []
    for week, raw_contract in enumerate(raw_contracts, 1):
        lesson = lessons_by_week.get(week)
        if lesson is None or _normalized_json(lesson.get("contract")) != _normalized_json(raw_contract):
            raise CourseModeSimulationError(
                "CONTRACT_ENVELOPE_MISMATCH", f"envelope contract mismatch at week {week}",
            )
        expected_fixture_id = f"curriculum.w{week:02d}"
        manifest = lesson.get("manifest", {})
        if (
            raw_contract.get("fixtureId") != expected_fixture_id
            or raw_contract.get("lesson", {}).get("lessonId") != expected_fixture_id
            or raw_contract.get("contractChecksum") != canonical_checksums[week - 1]
            or lesson.get("lessonKey") != canonical_lesson_keys[week - 1]
            or manifest.get("lessonKey") != canonical_lesson_keys[week - 1]
            or manifest.get("courseKey") != course_key
            or manifest.get("contractChecksum") != canonical_checksums[week - 1]
        ):
            raise CourseModeSimulationError(
                "CANONICAL_WEEK_IDENTITY_MISMATCH",
                f"week {week} does not match backend canonical identity",
            )
        normalized = _normalized_json(raw_contract)
        normalized_contracts.append(normalized)
        try:
            contracts.append(CourseModeContract.from_mapping(normalized))
        except CourseModeContractError as exc:
            raise CourseModeSimulationError(
                "CONTRACT_INVALID", f"week {week} {exc}",
            ) from None
    validate_pedagogy_mapping(contracts)
    inventory = {item["asset_key"]: item for item in fixture["inventory"]}
    lessons = []
    all_actions: set[str] = set()
    for week, (contract, normalized_contract) in enumerate(
        zip(contracts, normalized_contracts), 1,
    ):
        _validate_visual_phases(lessons_by_week[week], inventory, normalized_contract)
        matrix = {
            scenario: asyncio.run(simulate_terminal_path(
                contract, scenario,
                scenario_turn_index=(
                    _attempt_ceiling_turn_index(contract, scenario) if scenario in ATTEMPT_CEILING_SCENARIOS
                    else 1 + (week + scenario_index) % (len(contract.activities) - 2)
                ),
            ))
            for scenario_index, scenario in enumerate(RESPONSE_MATRIX)
        }
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
            "matrix": matrix,
        })
    summary = {
        "schemaVersion": 1, "simulator": "course-mode-26week.v1", "status": "pass",
        "lessonCount": len(lessons), "scenarioCount": len(RESPONSE_MATRIX),
        "pedagogyCount": len(PEDAGOGY_WEEKS), "actions": sorted(all_actions), "lessons": lessons,
    }
    if "backendSha" in fixture:
        summary["backendSha"] = fixture["backendSha"]
    return summary


def simulate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    try:
        return _simulate_fixture(fixture)
    except CourseModeSimulationError:
        raise
    except (AssertionError, CourseModeContractError, KeyError, TypeError, ValueError) as exc:
        raise CourseModeSimulationError("SIMULATION_VALIDATION_FAILED", str(exc)) from None


def simulate_contracts(raw_contracts: list[dict[str, Any]]) -> dict[str, Any]:
    canonical, canonical_contracts = load_backend_contracts()
    if len(raw_contracts) != len(canonical_contracts):
        raise CourseModeSimulationError(
            "CONTRACT_CANONICAL_MISMATCH",
            f"contract count {len(raw_contracts)} differs from canonical {len(canonical_contracts)}",
        )
    for week, (expected, actual) in enumerate(zip(canonical_contracts, raw_contracts), 1):
        if _normalized_json(expected) != _normalized_json(actual):
            raise CourseModeSimulationError(
                "CONTRACT_CANONICAL_MISMATCH", _contract_drift_message(expected, actual, week),
            )
    fixture = copy.deepcopy(canonical)
    fixture["contracts"] = raw_contracts
    fixture["lessons"] = [
        {**lesson, "contract": raw_contracts[index]}
        for index, lesson in enumerate(canonical["lessons"])
    ]
    return simulate_fixture(fixture)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path, default=None)
    parser.add_argument("--candidate", type=Path, default=None)
    parser.add_argument("--contracts", type=Path, default=None)
    parser.add_argument("--backend-command-json", default=None)
    parser.add_argument("--backend-timeout-sec", type=float, default=30)
    args = parser.parse_args(argv)
    try:
        if args.contracts:
            if not args.contracts.exists():
                raise CourseModeSimulationError(
                    "CONTRACTS_INPUT_NOT_FOUND", f"contracts input does not exist: {args.contracts}",
                )
            if not args.contracts.is_file():
                raise CourseModeSimulationError(
                    "CONTRACTS_INPUT_NOT_FILE", f"contracts input is not a file: {args.contracts}",
                )
            try:
                fixture = json.loads(_read_bounded_regular(args.contracts, 64 * 1024 * 1024).decode("utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CourseModeSimulationError("CONTRACTS_INPUT_INVALID_JSON", str(exc)) from None
            contracts = fixture["contracts"]
        else:
            expected_sha = None
            if args.candidate is not None:
                try:
                    candidate = json.loads(
                        _read_bounded_regular(args.candidate, MAX_CANDIDATE_BYTES).decode("utf-8")
                    )
                    expected_sha = candidate["repositories"]["backend"]["sha"]
                except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
                    raise CourseModeSimulationError(
                        "BACKEND_IDENTITY_MISMATCH", "candidate backend identity is invalid",
                    ) from None
            resolution = resolve_backend_root(args.backend_root, expected_sha=expected_sha)
            if resolution.error or resolution.path is None:
                raise CourseModeSimulationError(
                    resolution.error or "BACKEND_IDENTITY_MISMATCH",
                    "an explicit committed backend root is required",
                )
            backend_command = None
            if args.backend_command_json is not None:
                try:
                    backend_command = json.loads(args.backend_command_json)
                except json.JSONDecodeError as exc:
                    raise CourseModeSimulationError("BACKEND_COMMAND_INVALID", str(exc)) from None
                if not isinstance(backend_command, list) or not backend_command or not all(
                    isinstance(item, str) for item in backend_command
                ):
                    raise CourseModeSimulationError(
                        "BACKEND_COMMAND_INVALID", "backend command must be a non-empty JSON string array",
                    )
            fixture, contracts = load_backend_contracts(
                resolution.path, backend_command=backend_command,
                timeout_sec=args.backend_timeout_sec, expected_sha=expected_sha or resolution.sha,
            )
        fixture = copy.deepcopy(fixture)
        fixture["contracts"] = copy.deepcopy(contracts)
        summary = simulate_fixture(fixture)
    except CourseModeSimulationError as exc:
        print(json.dumps({
            "schemaVersion": 1, "simulator": "course-mode-26week.v1", "status": "fail",
            "error": {"code": exc.code, "message": exc.message},
        }, sort_keys=True, separators=(",", ":")))
        return 1
    except Exception as exc:
        print(json.dumps({
            "schemaVersion": 1, "simulator": "course-mode-26week.v1", "status": "fail",
            "error": {"code": "UNEXPECTED_SIMULATION_FAILURE", "message": str(exc)},
        }, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
