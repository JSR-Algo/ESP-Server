from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.lesson.course_mode_contract import CourseModeContract
from scripts.course_mode_26week_simulation import (
    PEDAGOGY_WEEKS,
    RESPONSE_MATRIX,
    load_backend_contracts,
    simulate_contracts,
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
    summary = simulate_contracts(contracts)

    assert verifier["lessonCount"] == summary["lessonCount"] == 26
    assert summary["scenarioCount"] == len(RESPONSE_MATRIX) == 11
    assert summary["pedagogyCount"] == len(PEDAGOGY_WEEKS) == 6
    assert all(lesson["completion"]["state"] in {"COMPLETE", "CLOSING"} for lesson in summary["lessons"])
    assert all(lesson["delivery"]["deduped"] is True for lesson in summary["lessons"])


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


def test_cli_is_deterministic_and_machine_readable() -> None:
    command = [sys.executable, str(ROOT / "scripts/course_mode_26week_simulation.py")]
    first = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, timeout=45)
    second = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, timeout=45)

    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["status"] == "pass"
