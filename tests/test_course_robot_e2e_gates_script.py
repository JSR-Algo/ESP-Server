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
        "COURSE_MODE_ROLLBACK_TEST_DATABASE_URL",
    ):
        assert allowed in script
    assert "exec env -i" not in script
    assert "/Users/manhhodinh/Documents/TBOT/scripts/course_robot_e2e_gates.sh" not in script
    assert "dist/" not in script
    assert "coverage/" not in script
    assert ".pytest_cache" not in script


def test_canonical_gate_forwards_complete_live_db_snapshot_without_other_secrets(
    tmp_path: Path,
) -> None:
    fixture = _shell_fixture(tmp_path)
    probe = fixture / "main/tbot-server/scripts/course_mode_release_gate.py"
    probe.write_text(
        "import json, os\n"
        "keys = [key for key in os.environ if key.endswith('DATABASE_URL')]\n"
        "print(json.dumps({key: os.environ[key] for key in sorted(keys)}))\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(
        ["git", "commit", "-m", "environment probe"], cwd=fixture,
        check=True, capture_output=True,
    )
    source = {
        **os.environ,
        "COURSE_MODE_V2_TEST_DATABASE_URL": "postgresql://127.0.0.1:55431/course_a",
        "COURSE_MODE_TEST_DATABASE_URL": "postgresql://127.0.0.1:55431/course_a",
        "DATABASE_URL": "postgresql://127.0.0.1:55432/course_b",
        "COURSE_MODE_ROLLBACK_TEST_DATABASE_URL": "postgresql://127.0.0.1:55432/course_b",
        "PRODUCTION_DATABASE_URL": "postgresql://production.invalid/production",
        "UNRELATED_DATABASE_URL": "postgresql://must-not-forward.invalid/secret",
    }

    result = subprocess.run(
        [str(fixture / "scripts/course_robot_e2e_gates.sh"), "--candidate", "unused.json"],
        cwd=fixture, env=source, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "COURSE_MODE_ROLLBACK_TEST_DATABASE_URL": source["COURSE_MODE_ROLLBACK_TEST_DATABASE_URL"],
        "COURSE_MODE_TEST_DATABASE_URL": source["COURSE_MODE_TEST_DATABASE_URL"],
        "COURSE_MODE_V2_TEST_DATABASE_URL": source["COURSE_MODE_V2_TEST_DATABASE_URL"],
        "DATABASE_URL": source["DATABASE_URL"],
        "PRODUCTION_DATABASE_URL": source["PRODUCTION_DATABASE_URL"],
    }


def test_canonical_gate_does_not_materialize_absent_optional_production_url(tmp_path: Path) -> None:
    fixture = _shell_fixture(tmp_path)
    probe = fixture / "main/tbot-server/scripts/course_mode_release_gate.py"
    probe.write_text(
        "import os\n"
        "raise SystemExit(91 if 'PRODUCTION_DATABASE_URL' in os.environ else 0)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(
        ["git", "commit", "-m", "environment probe"], cwd=fixture,
        check=True, capture_output=True,
    )
    source = {key: value for key, value in os.environ.items() if key != "PRODUCTION_DATABASE_URL"}

    result = subprocess.run(
        [str(fixture / "scripts/course_robot_e2e_gates.sh"), "--candidate", "unused.json"],
        cwd=fixture, env=source, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr


def test_canonical_gate_preserves_empty_and_reserved_optional_production_urls(tmp_path: Path) -> None:
    fixture = _shell_fixture(tmp_path)
    probe = fixture / "main/tbot-server/scripts/course_mode_release_gate.py"
    probe.write_text(
        "import json, os\n"
        "print(json.dumps({\n"
        "    'present': 'PRODUCTION_DATABASE_URL' in os.environ,\n"
        "    'value': os.environ.get('PRODUCTION_DATABASE_URL'),\n"
        "}))\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(
        ["git", "commit", "-m", "environment probe"], cwd=fixture,
        check=True, capture_output=True,
    )

    for value in (
        "",
        "postgresql://user:p@ss;word@production.invalid/db?sslmode=require&application_name=a%20b#fragment",
    ):
        result = subprocess.run(
            [str(fixture / "scripts/course_robot_e2e_gates.sh"), "--candidate", "unused.json"],
            cwd=fixture,
            env={**os.environ, "PRODUCTION_DATABASE_URL": value},
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"present": True, "value": value}


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
