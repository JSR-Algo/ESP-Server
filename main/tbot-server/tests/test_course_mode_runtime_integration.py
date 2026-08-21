from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.lesson.runtime import course_mode_runtime_from_manifest


FIXTURE = Path(__file__).parent / "fixtures" / "course-mode" / "course-mode-pilot-cat-ball.json"


def contract():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_v2_requires_explicit_flag_and_exact_contract_without_v1_fallback() -> None:
    assert course_mode_runtime_from_manifest({"courseModeContract": contract()}, enabled=False) is None
    runtime = course_mode_runtime_from_manifest({"courseModeContract": contract()}, enabled=True)
    assert runtime is not None and runtime.course_mode_active is True
    bad = contract(); bad["preset"]["presetVersion"] = 1
    with pytest.raises(ValueError):
        course_mode_runtime_from_manifest({"courseModeContract": bad}, enabled=True)


def test_v1_manifest_is_not_reinterpreted_as_v2() -> None:
    assert course_mode_runtime_from_manifest({"conversation": {"presetId": "tvideoJourney", "presetVersion": 1}}, enabled=True) is None
