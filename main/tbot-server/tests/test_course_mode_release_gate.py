from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


gate = importlib.import_module("scripts.course_mode_release_gate")


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repository(root: Path) -> dict:
    return {
        "path": str(root),
        "sha": _git(root, "rev-parse", "--verify", "HEAD^{commit}"),
        "branch": _git(root, "branch", "--show-current"),
        "remoteUrl": _git(root, "remote", "get-url", "origin"),
        "dirtyExceptions": [],
    }


@pytest.fixture
def candidate_file(tmp_path: Path) -> Path:
    repositories = {}
    for name in ("backend", "adminEsp", "firmware"):
        root = tmp_path / name
        root.mkdir()
        _git(root, "init", "-b", "candidate")
        _git(root, "config", "user.email", "candidate@example.invalid")
        _git(root, "config", "user.name", "Candidate Test")
        _git(root, "remote", "add", "origin", f"https://example.invalid/{name}.git")
        (root / "tracked.txt").write_text(name, encoding="utf-8")
        if name == "backend":
            curriculum = root / "src/lessons/course-mode/curriculum-course-mode.ts"
            curriculum.parent.mkdir(parents=True)
            curriculum.write_text("export const curriculum = 26;\n", encoding="utf-8")
        if name == "adminEsp":
            python_gate = root / "main/tbot-server/scripts/course_mode_release_gate.py"
            shell_gate = root / "scripts/course_robot_e2e_gates.sh"
            python_gate.parent.mkdir(parents=True)
            shell_gate.parent.mkdir(parents=True)
            python_gate.write_text("# candidate gate\n", encoding="utf-8")
            shell_gate.write_text("#!/bin/sh\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "fixture")
        repositories[name] = _repository(root)

    curriculum_path = (
        Path(repositories["backend"]["path"])
        / "src/lessons/course-mode/curriculum-course-mode.ts"
    )
    candidate = {
        "candidateId": "course-mode-2099-01-01.1",
        "createdAt": "2099-01-01T00:00:00Z",
        "expiresAt": "2099-01-08T00:00:00Z",
        "course": {
            "courseId": "10000000-0000-4000-8000-000000000001",
            "courseKey": "english-6month-4-6",
        },
        "repositories": repositories,
        "images": {},
        "firmware": {},
        "database": {},
        "curriculum": {
            "courseId": "10000000-0000-4000-8000-000000000001",
            "courseKey": "english-6month-4-6",
            "rendererId": "teebot-lesson-renderer.v5",
            "contractIdentity": "courseCompanion.v2.contract.v1",
            "lessonCount": 26,
            "activityCount": 256,
            "pedagogyCount": 6,
            "responseClassCount": 11,
            "sourceChecksum": hashlib.sha256(curriculum_path.read_bytes()).hexdigest(),
        },
        "tools": {},
        "evidenceRoot": str(tmp_path / "evidence"),
    }
    path = tmp_path / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    return path


def _lane(name: str, code: str, *, timeout: float = 5.0, required: str | None = None):
    return gate.Lane(
        name=name,
        repository="adminEsp",
        relative_cwd=".",
        command=(sys.executable, "-c", code),
        timeout_sec=timeout,
        required_environment=required,
    )


def test_success_report_is_stable_and_machine_readable(candidate_file: Path) -> None:
    result = gate.run_gate(
        candidate_file, "quick", lanes=(_lane("one", "raise SystemExit(0)"),),
    )

    assert result == {
        "candidateId": "course-mode-2099-01-01.1",
        "verdict": "PASS",
        "lanes": [{"name": "one", "exitCode": 0, "durationMs": result["lanes"][0]["durationMs"]}],
        "failedLane": None,
    }
    assert type(result["lanes"][0]["durationMs"]) is int
    assert result["lanes"][0]["durationMs"] >= 0
    assert json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)


def test_lane_failure_stops_dependent_lanes(candidate_file: Path, tmp_path: Path) -> None:
    marker = tmp_path / "must-not-run"
    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(
            _lane("failure", "raise SystemExit(7)"),
            _lane("dependent", f"from pathlib import Path;Path({str(marker)!r}).touch()"),
        ),
    )

    assert result["verdict"] == "FAIL"
    assert result["failedLane"] == "failure"
    assert result["lanes"][0]["exitCode"] == 7
    assert len(result["lanes"]) == 1
    assert not marker.exists()


def test_identity_drift_before_next_lane_is_blocked(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    tracked = Path(candidate["repositories"]["adminEsp"]["path"]) / "tracked.txt"
    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(
            _lane("drift", f"from pathlib import Path;Path({str(tracked)!r}).write_text('drift')"),
            _lane("dependent", "raise SystemExit(0)"),
        ),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "dependent"
    assert [lane["name"] for lane in result["lanes"]] == ["drift"]


def test_missing_required_capability_is_skipped_and_blocks(candidate_file: Path) -> None:
    result = gate.run_gate(
        candidate_file,
        "live-db",
        lanes=(_lane("postgres", "raise SystemExit(0)", required="COURSE_MODE_TEST_DATABASE_URL"),),
        source_environment={},
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "postgres"
    assert result["lanes"] == [{"name": "postgres", "exitCode": None, "durationMs": 0}]


def test_live_db_lane_requires_and_forwards_every_backend_live_database_variable(
    candidate_file: Path,
) -> None:
    lane = gate.lanes_for_mode("live-db")[-1]
    source = {
        "COURSE_MODE_V2_TEST_DATABASE_URL": "postgres://v2.invalid/db",
        "COURSE_MODE_TEST_DATABASE_URL": "postgres://curriculum.invalid/db",
        "DATABASE_URL": "postgres://materializer.invalid/db",
    }
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))

    environment = gate._child_environment(candidate, source, lane)

    assert lane.required_environment == tuple(source)
    assert {key: environment[key] for key in source} == source
    assert environment["TBOT_RUN_LIVE_DB_TESTS"] == "true"


def test_live_db_blocks_if_any_backend_database_variable_is_missing(candidate_file: Path) -> None:
    lane = gate.lanes_for_mode("live-db")[-1]
    source = {
        "COURSE_MODE_V2_TEST_DATABASE_URL": "postgres://v2.invalid/db",
        "COURSE_MODE_TEST_DATABASE_URL": "postgres://curriculum.invalid/db",
    }

    result = gate.run_gate(
        candidate_file, "live-db", lanes=(lane,), source_environment=source,
        runtime_root=Path(json.loads(candidate_file.read_text())["repositories"]["adminEsp"]["path"]),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "live-postgres"
    assert result["lanes"] == [{"name": "live-postgres", "exitCode": None, "durationMs": 0}]


def test_timeout_and_output_limits_fail_closed(candidate_file: Path) -> None:
    timeout = gate.run_gate(
        candidate_file, "quick", lanes=(_lane("slow", "import time;time.sleep(5)", timeout=0.05),),
    )
    output = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("noisy", "import sys;sys.stdout.write('x'*2000000)"),),
        max_output_bytes=1024,
    )

    assert timeout["verdict"] == "FAIL" and timeout["lanes"][0]["exitCode"] is None
    assert output["verdict"] == "FAIL" and output["lanes"][0]["exitCode"] is None


def test_child_environment_is_sanitized_and_path_shadow_is_ignored(
    candidate_file: Path, tmp_path: Path,
) -> None:
    shadow = tmp_path / "bin"
    shadow.mkdir()
    (shadow / "python3").write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    (shadow / "python3").chmod(0o755)
    code = (
        "import os;"
        "assert os.environ['HOME']=='/nonexistent';"
        "assert os.environ['PATH']==%r;"
        "assert 'TOP_SECRET' not in os.environ"
    ) % gate.SECURE_PATH

    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("environment", code),),
        source_environment={"PATH": str(shadow), "HOME": str(tmp_path), "TOP_SECRET": "secret"},
    )

    assert result["verdict"] == "PASS"


def test_runtime_gate_must_be_the_candidate_admin_checkout(candidate_file: Path) -> None:
    result = gate.run_gate(candidate_file, "quick")

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "candidate-runtime"
    assert result["lanes"] == []


def test_runtime_identity_requires_both_gate_files_at_candidate_sha(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    admin_root = Path(candidate["repositories"]["adminEsp"]["path"])

    assert gate._runtime_matches_candidate(candidate, admin_root) is True

    (admin_root / "scripts/course_robot_e2e_gates.sh").write_text("#!/bin/sh\nexit 99\n")
    assert gate._runtime_matches_candidate(candidate, admin_root) is False


@pytest.mark.parametrize(
    "raw",
    [
        b'{"candidateId":"one","candidateId":"two"}',
        b'{"candidateId":NaN}',
        b'{"candidateId":1e999}',
        b'[]',
    ],
)
def test_invalid_duplicate_and_nonfinite_candidate_inputs_are_blocked(
    tmp_path: Path, raw: bytes,
) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_bytes(raw)

    result = gate.run_gate(candidate, "quick", lanes=())

    assert result == {
        "candidateId": None, "verdict": "BLOCKED", "lanes": [], "failedLane": "candidate",
    }


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0.0, -1.0])
def test_invalid_lane_timeout_is_blocked(candidate_file: Path, timeout: float) -> None:
    result = gate.run_gate(
        candidate_file, "quick", lanes=(_lane("invalid", "raise SystemExit(0)", timeout=timeout),),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "configuration"
    assert result["lanes"] == []


def test_duplicate_lane_names_are_blocked(candidate_file: Path) -> None:
    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("duplicate", "raise SystemExit(0)"), _lane("duplicate", "raise SystemExit(0)")),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "configuration"
    assert result["lanes"] == []


def test_report_is_written_atomically_and_strictly(candidate_file: Path, tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("one", "raise SystemExit(0)"),),
        report_path=report,
    )

    assert json.loads(report.read_text(encoding="utf-8")) == result
    assert list(tmp_path.glob(".report.json.*")) == []


def test_unsafe_report_destination_fails_closed(candidate_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("preserve", encoding="utf-8")
    report = tmp_path / "report.json"
    report.symlink_to(target)

    result = gate.run_gate(candidate_file, "quick", lanes=(), report_path=report)

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert target.read_text(encoding="utf-8") == "preserve"


def test_full_lane_inventory_is_exhaustive_and_uses_candidate_roots() -> None:
    names = [lane.name for lane in gate.lanes_for_mode("full")]
    commands = "\n".join(" ".join(lane.command) for lane in gate.lanes_for_mode("full"))

    assert names == [
        "backend-lint", "backend-typecheck", "backend-tests", "backend-build",
        "backend-curriculum-verifier", "admin-logic", "admin-browser", "admin-build",
        "admin-course-mode-playwright-chromium-desktop",
        "admin-course-mode-playwright-webkit-desktop",
        "admin-course-mode-playwright-chromium-mobile",
        "admin-course-mode-playwright-webkit-mobile",
        "esp-course-mode-full", "firmware-renderer",
        "firmware-handler", "firmware-backward-compatibility", "cross-contract-parity",
    ]
    for marker in (
        "verify-course-mode-curriculum", "run_host_native_lesson_cinematic_renderer_test.sh",
        "test:e2e:course-mode", gate.COURSE_MODE_SOFTWARE_TESTS,
    ):
        assert marker in commands
    assert all(not Path(lane.relative_cwd).is_absolute() for lane in gate.lanes_for_mode("full"))


def test_full_esp_lane_discovers_every_committed_software_course_mode_suite() -> None:
    root = Path(__file__).resolve().parents[3]
    discovered = gate.discover_esp_course_mode_tests(root, _git(root, "rev-parse", "HEAD"))
    expected = tuple(
        f"tests/{path.name}"
        for path in sorted((root / "main/tbot-server/tests").glob("test_course_mode*.py"))
        if path.name != "test_course_mode_release_gate.py"
    )

    assert discovered == expected
    assert "tests/test_course_mode_candidate_manifest.py" in discovered
    assert "tests/test_course_mode_task07_evidence_validate.py" in discovered
    assert "tests/test_course_mode_physical_tft_preflight.py" in discovered
    assert gate.classify_esp_course_mode_test("tests/test_course_mode_physical_tft_preflight.py") == "physical-contract"
    assert gate.classify_esp_course_mode_test("tests/test_course_mode_runtime_integration.py") == "software"


def test_esp_discovery_ignores_untracked_and_non_source_files(tmp_path: Path) -> None:
    root = tmp_path / "admin"
    tests = root / "main/tbot-server/tests"
    tests.mkdir(parents=True)
    _git(root, "init", "-b", "candidate")
    _git(root, "config", "user.email", "candidate@example.invalid")
    _git(root, "config", "user.name", "Candidate Test")
    (tests / "test_course_mode_committed.py").write_text("def test_ok(): pass\n")
    (tests / "test_course_mode_physical_lab.py").write_text("def test_ok(): pass\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    sha = _git(root, "rev-parse", "HEAD")
    (tests / "test_course_mode_untracked.py").write_text("def test_no(): pass\n")
    cache = root / "main/tbot-server/.pytest_cache/test_course_mode_cached.py"
    cache.parent.mkdir()
    cache.write_text("cached")

    assert gate.discover_esp_course_mode_tests(root, sha) == (
        "tests/test_course_mode_committed.py",
        "tests/test_course_mode_physical_lab.py",
    )
    assert gate.select_esp_software_tests(gate.discover_esp_course_mode_tests(root, sha)) == (
        "tests/test_course_mode_committed.py",
    )


def test_playwright_full_has_named_desktop_and_mobile_chromium_and_webkit_lanes() -> None:
    lanes = [lane for lane in gate.lanes_for_mode("full") if "playwright" in lane.name]

    assert [lane.command[-1] for lane in lanes] == [
        "--project=course-mode-chromium-desktop",
        "--project=course-mode-webkit-desktop",
        "--project=course-mode-chromium-mobile",
        "--project=course-mode-webkit-mobile",
    ]
    assert all(lane.required_source_contract == "course-mode-playwright" for lane in lanes)


def test_current_playwright_config_fails_closed_until_named_projects_are_committed() -> None:
    admin_root = Path(__file__).resolve().parents[3]

    assert gate.source_contract_ready(admin_root, "course-mode-playwright") is False


def test_playwright_contract_requires_named_projects_and_matching_devices(tmp_path: Path) -> None:
    web = tmp_path / "main/manager-web"
    web.mkdir(parents=True)
    (web / "package.json").write_text(json.dumps({"scripts": {"test:e2e:course-mode": "playwright test"}}))
    projects = {
        "course-mode-chromium-desktop": "Desktop Chrome",
        "course-mode-webkit-desktop": "Desktop Safari",
        "course-mode-chromium-mobile": "Pixel 7",
        "course-mode-webkit-mobile": "iPhone 13",
    }
    config = "projects: [\n" + "\n".join(
        f"{{ name: {name!r}, use: {{ ...devices[{device!r}] }} }},"
        for name, device in projects.items()
    ) + "\n]"
    (web / "playwright.config.js").write_text(config)

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright") is True

    (web / "playwright.config.js").write_text("// " + " ".join((*projects, *projects.values())))
    assert gate.source_contract_ready(tmp_path, "course-mode-playwright") is False


def test_live_db_adds_to_full_and_physical_mode_is_read_only_preflight() -> None:
    live = gate.lanes_for_mode("live-db")
    physical = gate.lanes_for_mode("physical-preflight")

    assert live[:-1] == gate.lanes_for_mode("full")
    assert live[-1].name == "live-postgres" and live[-1].required_environment
    assert len(physical) == 1 and physical[0].name == "physical-tft-preflight"
    assert "course_mode_physical_tft_preflight.py" in " ".join(physical[0].command)
    assert all("flash" not in token.lower() for token in physical[0].command)
    assert all("build" not in token.lower() for token in physical[0].command)


def test_physical_preflight_requires_candidate_bound_signed_evidence(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))

    assert gate.physical_preflight_command(candidate) is None

    result = gate.run_gate(
        candidate_file, "physical-preflight",
        runtime_root=Path(candidate["repositories"]["adminEsp"]["path"]),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "physical-tft-preflight"
    assert result["lanes"] == [{"name": "physical-tft-preflight", "exitCode": None, "durationMs": 0}]


def test_physical_preflight_command_uses_only_candidate_evidence_paths(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    paths = {}
    for key, name in (
        ("input", "preflight-input.json"),
        ("expectedIdentity", "expected-identity.json"),
        ("expectedIdentitySignature", "expected-identity.sig"),
    ):
        path = evidence / name
        path.write_bytes(b"x" * 64 if key == "expectedIdentitySignature" else b"{}")
        paths[key] = str(path)
    paths["output"] = str(evidence / "preflight-output.json")
    candidate["evidenceRoot"] = str(evidence)
    candidate["images"] = {"backend": "sha256:" + "1" * 64}
    candidate["firmware"] = {"appSha256": "2" * 64}
    candidate["database"] = {"materializationReceipt": "3" * 64}
    candidate["tools"] = {"physicalPreflight": paths}

    command = gate.physical_preflight_command(candidate)

    assert command == (
        "python3", "scripts/course_mode_physical_tft_preflight.py",
        "--input", paths["input"], "--output", paths["output"],
        "--expected-identity", paths["expectedIdentity"],
        "--expected-identity-signature", paths["expectedIdentitySignature"],
    )
    assert all("flash" not in token.lower() and "build" not in token.lower() for token in command)


def test_physical_preflight_rejects_malformed_json_and_signature_prerequisites(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    input_path = evidence / "input.json"
    identity_path = evidence / "identity.json"
    signature_path = evidence / "identity.sig"
    input_path.write_text("not-json")
    identity_path.write_text("{}")
    signature_path.write_bytes(b"short")
    candidate["evidenceRoot"] = str(evidence)
    candidate["images"] = {"backend": "frozen"}
    candidate["firmware"] = {"app": "frozen"}
    candidate["database"] = {"receipt": "frozen"}
    candidate["tools"] = {"physicalPreflight": {
        "input": str(input_path), "output": str(evidence / "output.json"),
        "expectedIdentity": str(identity_path),
        "expectedIdentitySignature": str(signature_path),
    }}

    assert gate.physical_preflight_command(candidate) is None
