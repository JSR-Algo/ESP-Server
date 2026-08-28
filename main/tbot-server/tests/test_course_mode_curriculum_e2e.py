from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.lesson.course_mode_contract import CourseModeContract
from core.lesson.course_orchestrator import CourseOrchestrator
from core.lesson.runtime import CourseModeRuntimeAdapter
from scripts.course_mode_26week_simulation import (
    _attempt_ceiling_turn_index,
    PEDAGOGY_WEEKS,
    RESPONSE_MATRIX,
    load_backend_contracts,
    simulate_contracts,
    simulate_fixture,
    simulate_terminal_path,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def backend_contracts() -> tuple[dict, list[dict]]:
    return load_backend_contracts()


def _checksum(value: dict) -> str:
    payload = {key: child for key, child in value.items() if key != "contractChecksum"}
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_simulates_all_backend_generated_lessons_and_response_modes(backend_contracts) -> None:
    verifier, contracts = backend_contracts
    summary = simulate_fixture(verifier)

    assert verifier["lessonCount"] == summary["lessonCount"] == 26
    assert summary["scenarioCount"] == len(RESPONSE_MATRIX) == 11
    assert summary["pedagogyCount"] == len(PEDAGOGY_WEEKS) == 6
    assert all(
        path["state"] in {"COMPLETE", "CLOSING"}
        for lesson in summary["lessons"] for path in lesson["matrix"].values()
    )
    assert all(
        path["deduped"] is True and len(path["visitedActivities"]) >= 1
        for lesson in summary["lessons"] for path in lesson["matrix"].values()
    )
    assert all(
        len(lesson["matrix"][scenario]["visitedActivities"]) >= lesson["activityCount"]
        for lesson in summary["lessons"]
        for scenario in ("correct", "near", "vietnamese", "authored_branch", "asr_failure")
    )
    assert all(
        lesson["matrix"][scenario]["attemptLevels"] == list(range(1, contracts[0]["session"]["maxAttempts"] + 1))
        and lesson["matrix"][scenario]["attemptCeilingReached"] is True
        and len(set(lesson["matrix"][scenario]["scenarioActivityIds"])) == contracts[0]["session"]["maxAttempts"]
        and lesson["matrix"][scenario]["scenarioReplayCount"] == contracts[0]["session"]["maxAttempts"]
        and len(lesson["matrix"][scenario]["scenarioDeliveryIds"]) == contracts[0]["session"]["maxAttempts"]
        and lesson["matrix"][scenario]["ceilingAction"] == lesson["matrix"][scenario]["expectedCeilingAction"]
        and lesson["matrix"][scenario]["actions"][-2:] == [
            "RESPOND_WITHOUT_REDIRECT", "CLOSE_BY_CHILD_CHOICE",
        ]
        and lesson["matrix"][scenario]["actions"][-1] == "CLOSE_BY_CHILD_CHOICE"
        for lesson in summary["lessons"]
        for scenario in ("incorrect", "silence", "help")
    )
    assert all(
        lesson["matrix"]["asr_failure"]["scenarioObservations"] == 2
        and lesson["matrix"]["asr_failure"]["scenarioReplayCount"] == 2
        and len(lesson["matrix"]["asr_failure"]["scenarioDeliveryIds"]) == 2
        and len(set(lesson["matrix"]["asr_failure"]["scenarioActivityIds"])) == 1
        and lesson["matrix"]["asr_failure"]["attemptLevels"] == []
        and lesson["matrix"]["asr_failure"]["actions"].count("OWN_ASR_UNCERTAINTY") == 2
        and sum(
            transition["scenario"] == "technical_recovery"
            for transition in lesson["matrix"]["asr_failure"]["transitions"]
        ) == 2
        for lesson in summary["lessons"]
    )


def test_representative_pedagogies_and_fallbacks_are_explicit(backend_contracts) -> None:
    _, raw = backend_contracts
    contracts = [CourseModeContract.from_mapping(value) for value in raw]

    assert contracts[0].activities[0].activity_type == "movement_greeting"
    assert contracts[1].activities[1].activity_type == "picture_reveal"
    assert contracts[2].activities[1].activity_type == "story_reveal"
    assert contracts[6].activities[1].activity_type == "role_prop_reveal"
    assert contracts[3].activities[1].activity_type == "review_pool_reveal"
    assert contracts[25].activities[1].activity_type == "child_choice_reveal"
    assert any(
        activity.visual["objectAssetKey"] is None and activity.visual["fallback"] == "robotActing"
        for activity in contracts[18].activities
    )


@pytest.mark.parametrize("mutation", [
    lambda value: value["renderer"].update({"rendererId": "teebot-lesson-renderer.v4"}),
    lambda value: value["activities"][0]["outcomes"]["help"].update(
        {"action": "retry", "activityId": value["activities"][0]["activityId"]},
    ),
    lambda value: next(
        activity for activity in value["activities"] if activity["stage"] == "RECALL"
    )["answerPolicy"].update({"targetTextVisible": True}),
    lambda value: value["activities"][0]["visual"].update(
        {"strategy": "publishedTeachingObject", "objectAssetKey": None},
    ),
])
def test_mutated_backend_contracts_fail_closed(backend_contracts, mutation) -> None:
    _, contracts = backend_contracts
    mutated = copy.deepcopy(contracts)
    mutation(mutated[0])
    mutated[0]["contractChecksum"] = _checksum(mutated[0])

    with pytest.raises((AssertionError, ValueError)):
        simulate_contracts(mutated)


def test_full_path_loop_detector_rejects_a_stuck_runtime(backend_contracts, monkeypatch) -> None:
    fixture, _ = backend_contracts
    original = CourseOrchestrator.observe

    def stuck_after_advance(self, observation):
        decision = original(self, observation)
        if decision.action == "ADVANCE_ACTIVITY":
            self.active_activity_id = observation.activity_id
        return decision

    monkeypatch.setattr(CourseOrchestrator, "observe", stuck_after_advance)
    with pytest.raises(AssertionError, match="runtime path loop"):
        simulate_fixture(copy.deepcopy(fixture))


def test_simulator_cannot_bypass_public_adapter_entry(backend_contracts, monkeypatch) -> None:
    fixture, _ = backend_contracts

    async def reject_bypass(self, arguments):
        raise RuntimeError("adapter-entry-required")

    monkeypatch.setattr(CourseModeRuntimeAdapter, "course_observe_child", reject_bypass)
    with pytest.raises(RuntimeError, match="adapter-entry-required"):
        simulate_fixture(copy.deepcopy(fixture))


def test_path_startup_uses_public_continue_entry(backend_contracts, monkeypatch) -> None:
    _, raw = backend_contracts
    contract = CourseModeContract.from_mapping(raw[0])

    async def reject_bypass(self, arguments):
        raise RuntimeError("continue-entry-required")

    monkeypatch.setattr(CourseModeRuntimeAdapter, "course_continue", reject_bypass)
    with pytest.raises(RuntimeError, match="continue-entry-required"):
        asyncio.run(simulate_terminal_path(contract, "correct", scenario_turn_index=1))


def test_path_startup_fails_closed_when_continue_stalls(backend_contracts, monkeypatch) -> None:
    _, raw = backend_contracts
    contract = CourseModeContract.from_mapping(raw[0])

    async def stalled(self, arguments):
        return {
            "accepted": False, "action": "OPERATION_NOT_ALLOWED",
            "nextState": self.orchestrator.session_state.value,
        }

    monkeypatch.setattr(CourseModeRuntimeAdapter, "course_continue", stalled)
    with pytest.raises(AssertionError, match="startup path loop"):
        asyncio.run(asyncio.wait_for(
            simulate_terminal_path(contract, "correct", scenario_turn_index=1),
            timeout=0.1,
        ))


def test_path_requires_authoritative_activity_delivery(backend_contracts, monkeypatch) -> None:
    _, raw = backend_contracts
    contract = CourseModeContract.from_mapping(raw[0])

    monkeypatch.setattr(CourseModeRuntimeAdapter, "pending_activity_deliveries", lambda self: [])
    with pytest.raises(AssertionError, match="activity delivery"):
        asyncio.run(simulate_terminal_path(contract, "correct", scenario_turn_index=1))


def test_path_rejects_delivery_id_collision_between_decisions(backend_contracts, monkeypatch) -> None:
    _, raw = backend_contracts
    contract = CourseModeContract.from_mapping(raw[0])
    original = CourseModeRuntimeAdapter.pending_activity_deliveries

    def collide(self):
        return [(decision, "course-delivery-collision") for decision, _ in original(self)]

    monkeypatch.setattr(CourseModeRuntimeAdapter, "pending_activity_deliveries", collide)
    with pytest.raises(AssertionError, match="deliveryId collision"):
        asyncio.run(simulate_terminal_path(contract, "correct", scenario_turn_index=1))


def test_attempt_ceiling_rejects_premature_correct_fallback(backend_contracts, monkeypatch) -> None:
    _, raw = backend_contracts
    contract = CourseModeContract.from_mapping(raw[0])
    original = CourseModeRuntimeAdapter.course_observe_child
    incorrect_calls = 0

    async def force_correct_after_first_miss(self, arguments):
        nonlocal incorrect_calls
        altered = dict(arguments)
        if arguments.get("speechClass") == "incorrect":
            incorrect_calls += 1
            if incorrect_calls > 1:
                altered.update({
                    "semanticClass": "target_en", "speechClass": "exact",
                    "language": "en", "intent": "answer", "confidenceBand": "high",
                    "safetyClass": "normal",
                })
        return await original(self, altered)

    monkeypatch.setattr(CourseModeRuntimeAdapter, "course_observe_child", force_correct_after_first_miss)
    with pytest.raises(AssertionError, match="attempt ceiling"):
        asyncio.run(simulate_terminal_path(
            contract, "incorrect",
            scenario_turn_index=_attempt_ceiling_turn_index(contract, "incorrect"),
        ))


def test_attempt_ceiling_rejects_broken_authored_target_carry(backend_contracts, monkeypatch) -> None:
    _, raw = backend_contracts
    contract = CourseModeContract.from_mapping(raw[0])
    original = CourseModeRuntimeAdapter.course_observe_child
    corrupted = False

    async def skip_authored_target(self, arguments):
        nonlocal corrupted
        result = await original(self, arguments)
        if not corrupted and arguments.get("speechClass") == "incorrect" and result.get("attempt") == 1:
            corrupted = True
            index = next(
                i for i, activity in enumerate(self.contract.activities)
                if activity.activity_id == self.orchestrator.active_activity_id
            )
            self.orchestrator.active_activity_id = self.contract.activities[index + 1].activity_id
        return result

    monkeypatch.setattr(CourseModeRuntimeAdapter, "course_observe_child", skip_authored_target)
    with pytest.raises(AssertionError, match="attempt ceiling"):
        asyncio.run(simulate_terminal_path(
            contract, "incorrect",
            scenario_turn_index=_attempt_ceiling_turn_index(contract, "incorrect"),
        ))


def test_asr_recovery_rejects_activity_advance(backend_contracts, monkeypatch) -> None:
    _, raw = backend_contracts
    contract = CourseModeContract.from_mapping(raw[0])
    original = CourseModeRuntimeAdapter.course_continue
    recovery_calls = 0

    async def advance_during_recovery(self, arguments):
        nonlocal recovery_calls
        recovering = self.orchestrator.session_state.value == "TECHNICAL_RECOVERY"
        result = await original(self, arguments)
        if recovering:
            recovery_calls += 1
            if recovery_calls == 1:
                index = next(
                    i for i, activity in enumerate(self.contract.activities)
                    if activity.activity_id == self.orchestrator.active_activity_id
                )
                self.orchestrator.active_activity_id = self.contract.activities[index + 1].activity_id
        return result

    monkeypatch.setattr(CourseModeRuntimeAdapter, "course_continue", advance_during_recovery)
    with pytest.raises(AssertionError, match="asr_failure recovery changed"):
        asyncio.run(simulate_terminal_path(contract, "asr_failure", scenario_turn_index=1))


def test_visual_phase_mutation_is_detected(backend_contracts) -> None:
    fixture, _ = backend_contracts
    mutated = copy.deepcopy(fixture)
    mutated["lessons"][18]["phases"][0]["layers"] = [
        layer for layer in mutated["lessons"][18]["phases"][0]["layers"]
        if layer["slot"] != "robotOverlay"
    ]

    with pytest.raises(AssertionError, match="published robotOverlay"):
        simulate_fixture(mutated)


def test_cli_is_deterministic_and_machine_readable() -> None:
    command = [sys.executable, str(ROOT / "scripts/course_mode_26week_simulation.py")]
    first = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, timeout=45)
    second = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, timeout=45)

    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["status"] == "pass"
