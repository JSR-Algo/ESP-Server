from __future__ import annotations

import copy
import hashlib
import json
import unicodedata
from pathlib import Path

import pytest

from core.lesson.course_mode_contract import CourseModeContract, CourseModeContractError


FIXTURE = Path(__file__).parent / "fixtures" / "course-mode" / "course-mode-pilot-cat-ball.json"


def manifest() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parses_frozen_task00_contract_into_immutable_targets_and_activities() -> None:
    contract = CourseModeContract.from_mapping(manifest())
    assert contract.contract_version == "courseCompanion.v2.contract.v1"
    assert contract.contract_checksum == "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264"
    assert contract.primary.target_id == "animals.cat"
    assert contract.secondary is not None
    assert contract.secondary.target_id == "toys.ball"
    assert contract.activity("cat-recall-visual-02").stage == "RECALL"
    assert contract.activities[0].target_ids == ("animals.cat",)
    assert contract.activities[0].outcomes["help"]["activityId"] == "cat-discover-center-01"
    assert contract.activities[0].evidence_policy == "word_mastery"
    with pytest.raises(TypeError):
        contract.activities[0].answer_policy["targetTextVisible"] = True


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.update({"extra": True}), "INVALID_FIELDS"),
        (lambda value: value["preset"].update({"presetVersion": 1}), "UNSUPPORTED_PRESET"),
        (lambda value: value.update({"contractChecksum": "0" * 64}), "CHECKSUM_MISMATCH"),
        (lambda value: value.update({"targets": []}), "TARGET_COUNT"),
        (lambda value: value["targets"].append(copy.deepcopy(value["targets"][0])), "TARGET_COUNT"),
        (lambda value: value["activities"][1].update({"activityId": value["activities"][0]["activityId"]}), "DUPLICATE_ACTIVITY"),
        (lambda value: value["activities"][2].update({"servoValue": 60}), "INVALID_ACTIVITY_FIELDS"),
        (lambda value: value["activities"][2]["answerPolicy"].update({"targetTextVisible": True}), "UNSAFE_ASSESSMENT"),
        (lambda value: value["activities"][4].update({"embodiedIntent": "DANCE_RANDOM"}), "UNSUPPORTED_INTENT"),
    ],
)
def test_rejects_contract_drift_fail_closed(mutate, code: str) -> None:
    value = manifest()
    mutate(value)
    with pytest.raises(CourseModeContractError) as raised:
        CourseModeContract.from_mapping(value)
    assert raised.value.code == code


def test_requires_primary_meaning_transfer_and_delayed_recall() -> None:
    value = manifest()
    value["activities"] = [item for item in value["activities"] if item["stage"] != "TRANSFER"]
    with pytest.raises(CourseModeContractError) as raised:
        CourseModeContract.from_mapping(value, verify_checksum=False)
    assert raised.value.code == "MISSING_REQUIRED_ACTIVITY"


def test_rejects_self_consistent_but_non_frozen_contract_checksum() -> None:
    value = manifest()
    value["targets"][0]["targetWord"] = "dog"
    payload = {key: child for key, child in value.items() if key != "contractChecksum"}
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    value["contractChecksum"] = hashlib.sha256(
        unicodedata.normalize("NFC", canonical).encode("utf-8")
    ).hexdigest()
    with pytest.raises(CourseModeContractError) as raised:
        CourseModeContract.from_mapping(value)
    assert raised.value.code == "UNSUPPORTED_CONTRACT_CHECKSUM"
