from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
REPRO_ROOT = REPO_ROOT / "tests" / "lesson-production" / "repros"


def test_t21_uses_the_tracked_state_machine_suite_without_branch_refs():
    script = (REPRO_ROOT / "t21.sh").read_text(encoding="utf-8")

    assert "git -C" not in script
    assert "checkout main" not in script
    assert 'TEST_NAME="test_lesson_runtime_state_machine_t21.py"' in script
    assert '"tests/$TEST_NAME"' in script


def test_t22_uses_the_tracked_lifecycle_suite_without_git_history_dependency():
    script = (REPRO_ROOT / "t22.sh").read_text(encoding="utf-8")

    assert "git -C" not in script
    assert "SOURCE_REV=" not in script
    assert "tests/test_lesson_sd_pack_lifecycle.py" in script


def test_t23_runs_from_a_temporary_test_path_and_cleans_it_up():
    script = (REPRO_ROOT / "t23.sh").read_text(encoding="utf-8")

    assert 'TEST_REL="tests/__t23_voice_output_discipline_repro.py"' in script
    assert "trap 'rm -f \"$TEST_REL\"' EXIT" in script
    assert 'cp "$REPRO_DIR/t23_voice_output_discipline_test.py" "$TEST_REL"' in script
    assert 'TEST_REL="tests/test_lesson_voice_output_discipline.py"' not in script
