import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "scripts/course_robot_e2e_gates.sh"


def test_canonical_gate_bootstraps_trusted_sources_before_python() -> None:
    script = GATE.read_text(encoding="utf-8")

    assert "course_mode_release_gate.py" in script
    assert "course_mode_candidate_manifest.py" in script
    assert "scripts/course_robot_e2e_gates.sh" in script
    assert "--candidate" in script
    assert "GIT=/usr/bin/git" in script
    assert "rev-parse" in script and "hash-object" in script
    assert script.index("hash-object") < script.index("exec /usr/bin/env -i")
    assert "blocked to Task9" in script
    assert "Canonical inventory markers" not in script


def test_canonical_gate_does_not_delegate_to_workspace_convenience_script() -> None:
    script = GATE.read_text(encoding="utf-8")

    assert "exec /usr/bin/env -i" in script
    assert "/usr/bin/dirname" in script
    for allowed in (
        "COURSE_MODE_V2_TEST_DATABASE_URL",
        "COURSE_MODE_TEST_DATABASE_URL",
        "DATABASE_URL",
    ):
        assert allowed in script
    assert "exec env -i" not in script
    assert "/Users/manhhodinh/Documents/TBOT/scripts/course_robot_e2e_gates.sh" not in script
    assert "dist/" not in script
    assert "coverage/" not in script
    assert ".pytest_cache" not in script


def _shell_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "repository"
    for relative in (
        "scripts/course_robot_e2e_gates.sh",
        "main/tbot-server/scripts/course_mode_release_gate.py",
        "main/tbot-server/scripts/course_mode_candidate_manifest.py",
    ):
        target = fixture / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    subprocess.run(["git", "init", "-b", "candidate"], cwd=fixture, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "candidate@example.invalid"],
        cwd=fixture, check=True,
    )
    subprocess.run(["git", "config", "user.name", "Candidate Test"], cwd=fixture, check=True)
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=fixture, check=True, capture_output=True)
    return fixture


def test_canonical_gate_executes_real_lane_inventory_contract(tmp_path: Path) -> None:
    fixture = _shell_fixture(tmp_path)

    result = subprocess.run(
        [str(fixture / "scripts/course_robot_e2e_gates.sh"), "--list-lanes"],
        cwd=fixture, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    inventory = json.loads(result.stdout)
    assert inventory["quick"] == [
        "backend-course-mode-focused", "admin-course-mode-logic",
        "esp-course-mode-focused", "firmware-course-mode-focused",
    ]
    assert "backend-curriculum-verifier" in inventory["full"]
    assert "physical-tft-preflight" in inventory["physical-preflight"]


def test_canonical_gate_rejects_dirty_python_bootstrap_before_import(tmp_path: Path) -> None:
    fixture = _shell_fixture(tmp_path)
    helper = fixture / "main/tbot-server/scripts/course_mode_candidate_manifest.py"
    helper.write_text("raise RuntimeError('must not import dirty helper')\n", encoding="utf-8")

    result = subprocess.run(
        [str(fixture / "scripts/course_robot_e2e_gates.sh"), "--list-lanes"],
        cwd=fixture, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "bootstrap source does not match HEAD" in result.stderr
    assert result.stdout == ""


def test_canonical_gate_ignores_hostile_git_repository_environment(tmp_path: Path) -> None:
    fixture = _shell_fixture(tmp_path)
    helper = fixture / "main/tbot-server/scripts/course_mode_candidate_manifest.py"
    helper.write_text("raise RuntimeError('must not import dirty helper')\n", encoding="utf-8")
    attacker = tmp_path / "attacker"
    subprocess.run(["git", "init", "-b", "candidate", str(attacker)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "candidate@example.invalid"], cwd=attacker, check=True)
    subprocess.run(["git", "config", "user.name", "Candidate Test"], cwd=attacker, check=True)
    env = dict(os.environ, GIT_DIR=str(attacker / ".git"), GIT_WORK_TREE=str(fixture))
    subprocess.run(["git", "add", "."], cwd=fixture, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "attacker head"], cwd=fixture, env=env, check=True, capture_output=True)

    result = subprocess.run(
        [str(fixture / "scripts/course_robot_e2e_gates.sh"), "--list-lanes"],
        cwd=fixture, env=env, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "bootstrap source does not match HEAD" in result.stderr
    assert "must not import dirty helper" not in result.stderr
