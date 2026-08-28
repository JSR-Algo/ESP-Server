from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts/course_robot_e2e_gates.sh"


def test_canonical_gate_is_candidate_bound_and_contains_required_lanes() -> None:
    script = GATE.read_text(encoding="utf-8")

    assert "course_mode_release_gate.py" in script
    assert "--candidate" in script
    for marker in (
        "verify-course-mode-curriculum",
        "test_course_mode_curriculum_e2e.py",
        "test_course_mode_runtime_integration.py",
        "test_course_mode_physical_tft_preflight.py",
        "run_host_native_lesson_cinematic_renderer_test.sh",
        "test:e2e:course-mode",
    ):
        assert marker in script


def test_canonical_gate_does_not_delegate_to_workspace_convenience_script() -> None:
    script = GATE.read_text(encoding="utf-8")

    assert "exec /usr/bin/env -i" in script
    assert "/usr/bin/dirname" in script
    assert "exec env -i" not in script
    assert "/Users/manhhodinh/Documents/TBOT/scripts/course_robot_e2e_gates.sh" not in script
    assert "dist/" not in script
    assert "coverage/" not in script
    assert ".pytest_cache" not in script
