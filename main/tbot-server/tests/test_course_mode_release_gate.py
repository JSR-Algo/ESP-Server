from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
import socket
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


gate = importlib.import_module("scripts.course_mode_release_gate")


_PLAYWRIGHT_SOURCE_PATHS = [
    "docs/docker/docker-compose.lesson-studio-e2e.yml",
    "docs/docker/lesson-studio-e2e/seed-postgres.sql",
    "main/manager-web/e2e/lesson-studio/course-mode-authoring.spec.js-snapshots/course-mode-step-1-course-mode-webkit-desktop-darwin.png",
    "main/manager-web/e2e/lesson-studio/course-mode-authoring.spec.js-snapshots/course-mode-step-1-course-mode-webkit-mobile-darwin.png",
    "main/manager-web/e2e/lesson-studio/course-mode-authoring.spec.js-snapshots/course-mode-step-2-course-mode-webkit-desktop-darwin.png",
    "main/manager-web/e2e/lesson-studio/course-mode-authoring.spec.js-snapshots/course-mode-step-2-course-mode-webkit-mobile-darwin.png",
    "main/manager-web/playwright.lesson-studio.config.js",
    "main/manager-web/scripts/check-canonical-demo-ui.mjs",
    "main/manager-web/scripts/check-flattened-cinematic-preview.mjs",
    "main/manager-web/scripts/check-lesson-assignment-ui-contracts.mjs",
    "main/manager-web/scripts/check-lesson-builder-logic.cjs",
    "main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs",
    "main/manager-web/scripts/check-lesson-visual-selection.cjs",
    "main/manager-web/scripts/check-robot-lesson-preview.mjs",
    "main/manager-web/scripts/lesson-studio-e2e-environment.test.cjs",
    "main/manager-web/scripts/page-errors-helper.test.cjs",
    "main/manager-web/src/apis/module/lesson.js",
    "main/manager-web/src/components/lesson/CinematicVideoLayer.vue",
    "main/manager-web/src/components/lesson/RobotEspTftProjectionPreview.vue",
    "main/manager-web/src/components/lesson/flattened-cinematic-preview.js",
    "main/manager-web/src/components/lesson/robot-preview-projection.js",
    "main/manager-web/src/i18n/en.js",
    "main/manager-web/src/i18n/vi.js",
    "main/manager-web/src/views/LessonEditor.vue",
    "main/manager-web/src/views/LessonMonitoring.vue",
    "main/manager-web/src/views/login.vue",
    "main/manager-web/tests/browser/lesson-builder-main.js",
]


def _valid_playwright_contract() -> dict:
    return {
        "version": 1,
        "sourcePaths": list(_PLAYWRIGHT_SOURCE_PATHS),
        "specs": ["e2e/lesson-studio/course-mode-authoring.spec.js"],
        "testMatch": ["course-mode-authoring.spec.js"],
        "projects": [
            {"name": "course-mode-chromium-desktop", "device": "Desktop Chrome", "viewport": {"width": 1440, "height": 900}},
            {"name": "course-mode-webkit-desktop", "device": "Desktop Safari", "viewport": {"width": 1440, "height": 900}},
            {"name": "course-mode-chromium-mobile", "device": "Pixel 7", "viewport": {"width": 390, "height": 844}},
            {"name": "course-mode-webkit-mobile", "device": "iPhone 13", "viewport": {"width": 390, "height": 844}},
        ],
        "assignmentPhases": {
            "fixtureCommand": "node --test scripts/task4-assignment-fixture.test.cjs",
            "newCommand": "node scripts/run-task4-assignment-phase.cjs new",
            "rollbackCommand": "node scripts/run-task4-assignment-phase.cjs rollback",
            "sourcePaths": [
                "docs/docker/task4-admin-assignment/bootstrap.cjs",
                "docs/docker/task4-admin-assignment/docker-compose.new.yml",
                "docs/docker/task4-admin-assignment/docker-compose.rollback.yml",
                "docs/docker/task4-admin-assignment/serve-media.cjs",
                "main/manager-web/e2e/lesson-studio/assignment-rollback-phase.spec.js",
                "main/manager-web/playwright.assignment-rollback.config.js",
                "main/manager-web/scripts/prepare-task4-media-templates.cjs",
                "main/manager-web/scripts/run-task4-assignment-phase.cjs",
                "main/manager-web/scripts/task4-assignment-fixture.test.cjs",
                "main/manager-web/scripts/task4-image-identity.cjs",
            ],
        },
        "fixed": {
            "testDir": "./e2e/lesson-studio",
            "globalSetup": "./e2e/lesson-studio/global-setup.cjs",
            "outputDir": "./output/playwright-e2e/results",
            "timeout": 60000,
            "expectTimeout": 10000,
            "fullyParallel": False,
            "workers": 1,
            "retries": 0,
            "reporter": [["list"], ["html", {"outputFolder": "./output/playwright-e2e/report", "open": "never"}]],
            "use": {
                "baseUrlHelper": "lessonStudioWebOrigin",
                "trace": "retain-on-failure",
                "screenshot": "only-on-failure",
                "video": "retain-on-failure",
                "serviceWorkers": "block",
            },
        },
    }


def _valid_playwright_config() -> str:
    return gate.generate_playwright_config(_valid_playwright_contract())


def _write_playwright_runtime(web: Path) -> None:
    (web / "course-mode.playwright.contract.json").write_text(
        json.dumps(_valid_playwright_contract(), sort_keys=True), encoding="utf-8",
    )
    (web / "playwright.config.js").write_text(_valid_playwright_config(), encoding="utf-8")


def _commit_playwright_fixture(
    root: Path, *, contract: dict | None = None, contract_raw: str | None = None,
    config: str | None = None,
    script: str = "playwright test --config=playwright.config.js",
    assignment_script_mutator=None,
) -> tuple[Path, str]:
    web = root / "main/manager-web"
    spec = web / "e2e/lesson-studio/course-mode-authoring.spec.js"
    spec.parent.mkdir(parents=True)
    spec.write_text("test('course mode', async () => {});\n", encoding="utf-8")
    (spec.parent / "global-setup.cjs").write_text(
        "require('../../scripts/reset-lesson-studio-e2e-state.cjs');\n", encoding="utf-8",
    )
    helpers = spec.parent / "helpers"
    helpers.mkdir()
    (helpers / "session.js").write_text(
        "require('../../../scripts/reset-lesson-studio-e2e-state.cjs');\n"
        "const command = \"require('./runtime-only-module')\";\n",
        encoding="utf-8",
    )
    spec.write_text(
        "require('./helpers/session');\ntest('course mode', async () => {});\n",
        encoding="utf-8",
    )
    scripts = web / "scripts"
    scripts.mkdir()
    (scripts / "reset-lesson-studio-e2e-state.cjs").write_text(
        "require('./lesson-studio-e2e-environment.cjs');\n", encoding="utf-8",
    )
    (scripts / "lesson-studio-e2e-environment.cjs").write_text(
        "module.exports = {};\n", encoding="utf-8",
    )
    package_scripts = {
        "test:e2e:course-mode": script,
        "test:course-mode:assignment-fixture": "node --test scripts/task4-assignment-fixture.test.cjs",
        "test:e2e:course-mode:assignment:new": "node scripts/run-task4-assignment-phase.cjs new",
        "test:e2e:course-mode:assignment:rollback": "node scripts/run-task4-assignment-phase.cjs rollback",
    }
    if assignment_script_mutator is not None:
        assignment_script_mutator(package_scripts)
    (web / "package.json").write_text(
        json.dumps({"scripts": package_scripts}), encoding="utf-8",
    )
    assignment_files = {
        "docs/docker/task4-admin-assignment/bootstrap.cjs": "module.exports = {};\n",
        "docs/docker/task4-admin-assignment/docker-compose.new.yml": "services: {}\n",
        "docs/docker/task4-admin-assignment/docker-compose.rollback.yml": "services: {}\n",
        "docs/docker/task4-admin-assignment/serve-media.cjs": "module.exports = {};\n",
        "main/manager-web/e2e/lesson-studio/assignment-rollback-phase.spec.js": "test('assignment phase', async () => {});\n",
        "main/manager-web/playwright.assignment-rollback.config.js": "module.exports = require('./playwright.config');\n",
        "main/manager-web/scripts/prepare-task4-media-templates.cjs": "module.exports = {};\n",
        "main/manager-web/scripts/run-task4-assignment-phase.cjs": "module.exports = {};\n",
        "main/manager-web/scripts/task4-assignment-fixture.test.cjs": "require('node:test')('fixture', () => {});\n",
        "main/manager-web/scripts/task4-image-identity.cjs": "module.exports = {};\n",
    }
    for relative, source in assignment_files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    for relative in _PLAYWRIGHT_SOURCE_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"source contract fixture\n")
    selected_contract = contract if contract is not None else _valid_playwright_contract()
    (web / "course-mode.playwright.contract.json").write_text(
        contract_raw if contract_raw is not None else json.dumps(selected_contract, sort_keys=True),
        encoding="utf-8",
    )
    generated = config
    if generated is None:
        generated = gate.generate_playwright_config(selected_contract)
    (web / "playwright.config.js").write_text(generated, encoding="utf-8")
    _git(root, "init", "-b", "candidate")
    _git(root, "config", "user.email", "candidate@example.invalid")
    _git(root, "config", "user.name", "Candidate Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")
    return web, _git(root, "rev-parse", "HEAD")


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


def _commit_then_dirty(candidate: dict, repository_name: str, relative: str) -> None:
    repository = candidate["repositories"][repository_name]
    root = Path(repository["path"])
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("committed\n", encoding="utf-8")
    _git(root, "add", relative)
    _git(root, "commit", "-m", f"add {Path(relative).name}")
    repository.update(_repository(root))
    path.write_text("dirty runtime bytes\n", encoding="utf-8")
    repository["dirtyExceptions"] = [{
        "path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }]


def _add_node_install(candidate: dict, repository_name: str, relative_cwd: str, key: str) -> Path:
    repository = candidate["repositories"][repository_name]
    root = Path(repository["path"])
    install_parent = root / relative_cwd
    lock = install_parent / "package-lock.json"
    ignored = install_parent / ".gitignore"
    package_manifest = install_parent / "package.json"
    install_parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('{"lockfileVersion":3}\n', encoding="utf-8")
    ignored.write_text("node_modules/\n", encoding="utf-8")
    if not package_manifest.exists():
        package_manifest.write_text('{"scripts":{"test":"true"}}\n', encoding="utf-8")
    _git(
        root, "add", str(lock.relative_to(root)), str(ignored.relative_to(root)),
        str(package_manifest.relative_to(root)),
    )
    _git(root, "commit", "-m", f"add {key} lock")
    repository.update(_repository(root))
    install = install_parent / "node_modules"
    package = install / "fixture-package"
    package.mkdir(parents=True)
    (package / "index.js").write_text("module.exports = 1;\n", encoding="utf-8")
    binaries = install / ".bin"
    binaries.mkdir()
    for name in ("playwright", "vitest"):
        binary = binaries / name
        binary.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        binary.chmod(0o755)
    candidate.setdefault("tools", {}).setdefault("nodeInstalls", {})[key] = (
        gate.describe_node_install(install, lock)
    )
    return install


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
            for relative in gate.TASK4_BACKEND_MOUNT_INPUTS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode("utf-8"))
        if name == "firmware":
            for relative in gate.TASK4_FIRMWARE_MOUNT_INPUTS:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode("utf-8"))
        if name == "adminEsp":
            python_gate = root / "main/tbot-server/scripts/course_mode_release_gate.py"
            manifest_helper = root / "main/tbot-server/scripts/course_mode_candidate_manifest.py"
            shell_gate = root / "scripts/course_robot_e2e_gates.sh"
            python_gate.parent.mkdir(parents=True)
            shell_gate.parent.mkdir(parents=True)
            python_gate.write_text("# candidate gate\n", encoding="utf-8")
            manifest_helper.write_text("# candidate helper\n", encoding="utf-8")
            shell_gate.write_text("#!/bin/sh\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "fixture")
        repositories[name] = _repository(root)

    curriculum_path = (
        Path(repositories["backend"]["path"])
        / "src/lessons/course-mode/curriculum-course-mode.ts"
    )
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    candidate = {
        "candidateId": "course-mode-2099-01-01.1",
        "createdAt": "2099-01-01T00:00:00Z",
        "expiresAt": "2099-01-08T00:00:00Z",
        "course": {
            "courseId": "10000000-0000-4000-8000-000000000001",
            "courseKey": "english-6month-4-6",
        },
        "repositories": repositories,
        "images": {
            "lessonStudioBackend": {
                "reference": "local/backend:candidate",
                "id": "sha256:" + "1" * 64,
            },
            "lessonStudioWeb": {
                "reference": "local/web:candidate",
                "id": "sha256:" + "2" * 64,
            },
        },
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
        "evidenceRoot": str(evidence_root),
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
    assert result["failedLane"] == "drift"
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
        "COURSE_MODE_ROLLBACK_TEST_DATABASE_URL": "postgres://rollback.invalid/db",
    }
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))

    environment = gate._child_environment(candidate, source, lane)

    assert lane.required_environment == tuple(source)
    assert {key: environment[key] for key in source} == source
    assert environment["TBOT_RUN_LIVE_DB_TESTS"] == "true"


def test_live_db_lane_fixes_database_confirmation_instead_of_trusting_source(
    candidate_file: Path,
) -> None:
    lane = gate.lanes_for_mode("live-db")[-1]
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))

    environment = gate._child_environment(
        candidate, {"COURSE_MODE_TEST_DATABASE_CONFIRMED": "0"}, lane,
    )

    assert environment["COURSE_MODE_TEST_DATABASE_CONFIRMED"] == "1"


def test_live_db_lane_binds_v5_source_root_to_candidate_instead_of_ambient(
    candidate_file: Path,
) -> None:
    lane = gate.lanes_for_mode("live-db")[-1]
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))

    environment = gate._child_environment(
        candidate, {"COURSE_MODE_V5_SOURCE_ROOT": "/hostile/ambient/source"}, lane,
    )

    assert environment["COURSE_MODE_V5_SOURCE_ROOT"] == candidate["repositories"]["adminEsp"]["path"]


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


def test_live_db_blocks_if_rollback_database_variable_is_missing(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = gate.lanes_for_mode("live-db")[-1]
    source = {
        "COURSE_MODE_V2_TEST_DATABASE_URL": "postgres://v2.invalid/db",
        "COURSE_MODE_TEST_DATABASE_URL": "postgres://curriculum.invalid/db",
        "DATABASE_URL": "postgres://materializer.invalid/db",
    }

    monkeypatch.setattr(gate, "release_state_matches", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("live DB command must not run without rollback URL"),
    )
    result = gate.run_gate(
        candidate_file, "live-db", lanes=(lane,), source_environment=source,
        runtime_root=Path(json.loads(candidate_file.read_text())["repositories"]["adminEsp"]["path"]),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "live-postgres"
    assert result["lanes"] == [{"name": "live-postgres", "exitCode": None, "durationMs": 0}]


def _live_db_test_lane(code: str) -> gate.Lane:
    return gate.Lane(
        name=gate.LIVE_DB_LANE.name,
        repository="adminEsp",
        relative_cwd=".",
        command=(sys.executable, "-c", code),
        timeout_sec=5.0,
        required_environment=gate.LIVE_DB_LANE.required_environment,
        fixed_environment=gate.LIVE_DB_LANE.fixed_environment,
    )


def _live_db_source(
    database_a: str = "postgresql://operator@127.0.0.1:55431/course_mode_a",
    database_b: str = "postgresql://operator@[::1]:55432/course_mode_b?sslmode=disable",
) -> dict[str, str]:
    return {
        "COURSE_MODE_V2_TEST_DATABASE_URL": database_a,
        "COURSE_MODE_TEST_DATABASE_URL": database_a,
        "DATABASE_URL": database_b,
        "COURSE_MODE_ROLLBACK_TEST_DATABASE_URL": database_b,
    }


def _run_live_db_topology_gate(
    candidate_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: dict[str, str],
    code: str = "raise SystemExit(0)",
) -> dict:
    monkeypatch.setattr(gate, "release_state_matches", lambda *args, **kwargs: True)
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    return gate.run_gate(
        candidate_file,
        "live-db",
        lanes=(_live_db_test_lane(code),),
        source_environment=source,
        runtime_root=Path(candidate["repositories"]["adminEsp"]["path"]),
    )


def test_live_db_blocks_when_both_database_groups_have_the_same_identity(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _live_db_source(
        database_a="postgresql://operator@127.0.0.1:55431/course_mode",
        database_b="postgresql://operator@127.0.0.1:55431/course_mode",
    )
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("live DB command must not run for one-DB topology"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "live-postgres"


def test_live_db_identity_ignores_username_differences(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _live_db_source(
        database_a="postgresql://migration@127.0.0.1:5432/course_mode",
        database_b="postgresql://rollback@127.0.0.1/course_mode",
    )
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("username must not create a distinct DB identity"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("COURSE_MODE_V2_TEST_DATABASE_URL", "postgresql://localhost:55439/wrong_v2"),
        ("COURSE_MODE_ROLLBACK_TEST_DATABASE_URL", "postgresql://localhost:55439/wrong_rollback"),
    ],
)
def test_live_db_blocks_when_a_database_group_does_not_share_one_identity(
    candidate_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    variable: str,
    value: str,
) -> None:
    source = _live_db_source()
    source[variable] = value
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("live DB command must not run for a split DB group"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://db.internal:5432/course_mode",
        "not-a-postgresql-url",
        "mysql://localhost:5432/course_mode",
        "postgresql://localhost:5432/course%ZZmode",
        "postgresql://localhost:5432",
        "postgresql://localhost:not-a-port/course_mode",
        "postgresql://localhost:0/course_mode",
        "postgresql://localhost:65536/course_mode",
        "postgresql://localhost:/course_mode",
        "postgresql://localhost:5432/course_mode?host=db.internal",
        "postgresql://localhost:5432/course_mode?hostaddr=10.0.0.1",
        "postgresql://localhost:5432/course_mode?port=6432",
        "postgresql://localhost:5432/course_mode?dbname=other",
    ],
)
def test_live_db_blocks_non_loopback_malformed_and_identity_override_urls(
    candidate_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    source = _live_db_source(database_a=database_url)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("live DB command must not run for an unsafe URL"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"
    assert result["lanes"] == [{"name": "live-postgres", "exitCode": None, "durationMs": 0}]


def test_live_db_accepts_exactly_two_distinct_loopback_database_identities(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    marker = tmp_path / "live-db-ran"
    result = _run_live_db_topology_gate(
        candidate_file,
        monkeypatch,
        _live_db_source(),
        f"from pathlib import Path;Path({str(marker)!r}).touch()",
    )

    assert result["verdict"] == "PASS"
    assert marker.exists()


def test_live_db_missing_port_defaults_to_postgres_port() -> None:
    assert gate._local_postgres_identity("postgresql://operator@127.0.0.1/course_mode") == (
        "loopback", 5432, "course_mode",
    )


def test_postgres_url_without_query_does_not_call_strict_query_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate,
        "parse_qsl",
        lambda *args, **kwargs: pytest.fail("empty query must not reach parse_qsl"),
    )

    assert gate._postgres_identity("postgresql://127.0.0.1:5432/course_mode") == (
        "127.0.0.1", 5432, "course_mode",
    )


def test_localhost_target_is_blocked_without_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate.threading,
        "Thread",
        lambda *args, **kwargs: pytest.fail("target localhost must be rejected without resolution"),
    )

    assert gate._local_postgres_identity("postgresql://localhost:5432/course_mode") is None


def test_numeric_loopback_targets_do_not_call_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate.socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("numeric target URLs must not resolve hostnames"),
    )

    assert gate._local_postgres_identity("postgresql://127.0.0.1:5432/course_mode") == (
        "loopback", 5432, "course_mode",
    )
    assert gate._local_postgres_identity("postgresql://[::1]:5432/course_mode") == (
        "loopback", 5432, "course_mode",
    )


def test_loopback_target_aliases_share_one_database_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate.socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("numeric target URLs must not resolve hostnames"),
    )

    identities = {
        gate._local_postgres_identity(url)
        for url in (
            "postgresql://other@127.0.0.1:5432/course_mode",
            "postgresql://third@[::1]:5432/course_mode",
        )
    }

    assert identities == {("loopback", 5432, "course_mode")}


def test_live_db_uses_one_validated_snapshot_of_mutable_source_environment(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MutatesAfterFirstRead(dict):
        def __init__(self, values):
            super().__init__(values)
            self.reads = {}

        def get(self, key, default=None):
            self.reads[key] = self.reads.get(key, 0) + 1
            if self.reads[key] > 1:
                return "postgresql://prod.invalid/changed"
            return super().get(key, default)

    source = MutatesAfterFirstRead(_live_db_source())
    result = _run_live_db_topology_gate(
        candidate_file,
        monkeypatch,
        source,
        "import os;assert os.environ['DATABASE_URL'].endswith('/course_mode_b?sslmode=disable')",
    )

    assert result["verdict"] == "PASS"
    assert all(source.reads[name] == 1 for name in gate.LIVE_DB_URL_VARIABLES)


@pytest.mark.parametrize(
    "production_url",
    [
        "postgresql://different-user@localhost:55431/course_mode_a",
        "postgresql://different-user@candidate.localhost:55431/course_mode_a",
        "postgres://different-user@[::1]:55432/course_mode_b",
    ],
)
def test_live_db_blocks_when_production_alias_matches_either_test_database(
    candidate_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_url: str,
) -> None:
    source = {**_live_db_source(), "PRODUCTION_DATABASE_URL": production_url}
    monkeypatch.setattr(
        gate.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 0)),
        ],
    )
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("live DB command must not run for a production alias"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"
    assert result["lanes"] == [{"name": "live-postgres", "exitCode": None, "durationMs": 0}]


@pytest.mark.parametrize(
    "production_url",
    [
        "not-a-postgres-url",
        "postgresql://prod.internal:5432/production?host=localhost",
    ],
)
def test_live_db_blocks_malformed_production_database_url(
    candidate_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_url: str,
) -> None:
    source = {**_live_db_source(), "PRODUCTION_DATABASE_URL": production_url}
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("live DB command must not run for malformed production URL"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


def test_live_db_valid_distinct_production_url_is_not_forwarded_to_child(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        **_live_db_source(),
        "PRODUCTION_DATABASE_URL": "postgresql://prod.internal:5432/production",
    }
    monkeypatch.setattr(
        gate.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.10", 5432)),
        ],
    )

    result = _run_live_db_topology_gate(
        candidate_file,
        monkeypatch,
        source,
        "import os;assert 'PRODUCTION_DATABASE_URL' not in os.environ",
    )

    assert result["verdict"] == "PASS"


@pytest.mark.parametrize(
    "production_host", ["localhost.localdomain", "0x7f000001", "2130706433"],
)
def test_live_db_blocks_production_resolver_aliases_to_loopback(
    candidate_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    production_host: str,
) -> None:
    source = {
        **_live_db_source(),
        "PRODUCTION_DATABASE_URL": f"postgresql://prod@{production_host}:55431/course_mode_a",
    }
    monkeypatch.setattr(
        gate.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 55431)),
        ],
    )
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("resolver alias must block before command"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


def test_live_db_blocks_ipv4_mapped_ipv6_production_alias(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        **_live_db_source(),
        "PRODUCTION_DATABASE_URL": "postgresql://prod@mapped.invalid:55431/course_mode_a",
    }
    monkeypatch.setattr(
        gate.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::ffff:127.0.0.1", 55431, 0, 0)),
        ],
    )
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("mapped loopback alias must block before command"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


def test_live_db_allows_distinct_remote_resolved_production_hostname(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        **_live_db_source(),
        "PRODUCTION_DATABASE_URL": "postgresql://prod@production.invalid:55431/course_mode_a",
    }
    monkeypatch.setattr(
        gate.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("203.0.113.10", 55431)),
        ],
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "PASS"


def test_live_db_blocks_unresolved_production_hostname(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        **_live_db_source(),
        "PRODUCTION_DATABASE_URL": "postgresql://prod@unresolved.invalid:55431/course_mode_a",
    }

    def fail_resolution(*args, **kwargs):
        raise socket.gaierror(socket.EAI_NONAME, "not known")

    monkeypatch.setattr(gate.socket, "getaddrinfo", fail_resolution)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("unresolved production host must block before command"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


def test_live_db_blocks_production_resolution_timeout(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {
        **_live_db_source(),
        "PRODUCTION_DATABASE_URL": "postgresql://prod@slow.invalid:55431/course_mode_a",
    }

    class NeverFinishes:
        def __init__(self, *, target, daemon):
            assert callable(target) and daemon is True

        def start(self) -> None:
            pass

        def join(self, timeout: float) -> None:
            assert timeout == 2.0

        def is_alive(self) -> bool:
            return True

    monkeypatch.setattr(gate.threading, "Thread", NeverFinishes)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("resolver timeout must block before command"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


@pytest.mark.parametrize("target_host", ["localhost.localdomain", "0x7f000001"])
def test_live_db_target_urls_reject_resolver_aliases(
    candidate_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_host: str,
) -> None:
    source = _live_db_source(
        database_a=f"postgresql://operator@{target_host}:55431/course_mode_a",
    )
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("target DB resolver aliases must not run"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


def test_live_db_gate_blocks_localhost_target_before_command(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _live_db_source(
        database_a="postgresql://operator@localhost:55431/course_mode_a",
    )
    monkeypatch.setattr(
        gate.threading,
        "Thread",
        lambda *args, **kwargs: pytest.fail("target localhost must not resolve"),
    )
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *args, **kwargs: pytest.fail("localhost target must block before command"),
    )

    result = _run_live_db_topology_gate(candidate_file, monkeypatch, source)

    assert result["verdict"] == "BLOCKED"


def test_live_db_does_not_forward_ambient_production_database_url(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    environment = gate._child_environment(
        candidate,
        {**_live_db_source(), "PRODUCTION_DATABASE_URL": "postgresql://prod.internal/prod"},
        gate.LIVE_DB_LANE,
    )

    assert "PRODUCTION_DATABASE_URL" not in environment


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


def test_runtime_identity_requires_gate_wrapper_and_imported_helper_at_candidate_sha(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    admin_root = Path(candidate["repositories"]["adminEsp"]["path"])

    assert gate._runtime_matches_candidate(candidate, admin_root) is True

    (admin_root / "main/tbot-server/scripts/course_mode_candidate_manifest.py").write_text("# drift\n")
    assert gate._runtime_matches_candidate(candidate, admin_root) is False

    helper = "main/tbot-server/scripts/course_mode_candidate_manifest.py"
    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = [{
        "path": helper,
        "sha256": hashlib.sha256((admin_root / helper).read_bytes()).hexdigest(),
    }]
    assert gate._runtime_matches_candidate(candidate, admin_root) is False


def test_candidate_paths_are_bound_to_commit_and_cannot_be_dirty_exceptions(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = candidate["repositories"]["adminEsp"]
    root = Path(repository["path"])
    selected = ("main/tbot-server/scripts/course_mode_release_gate.py",)

    assert gate.candidate_paths_match(repository, selected) is True

    path = root / selected[0]
    path.write_text("# drift\n")
    repository["dirtyExceptions"] = [{
        "path": selected[0], "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }]
    assert gate.candidate_paths_match(repository, selected) is False


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
    report = tmp_path / "evidence/report.json"
    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("one", "raise SystemExit(0)"),),
        report_path=report,
    )

    assert json.loads(report.read_text(encoding="utf-8")) == result
    assert list((tmp_path / "evidence").glob(".report.json.*")) == []


def test_atomic_report_handles_partial_os_writes(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "evidence/report.json"
    real_write = gate.os.write

    def partial_write(descriptor: int, payload) -> int:
        return real_write(descriptor, bytes(payload[:7]))

    monkeypatch.setattr(gate.os, "write", partial_write)

    result = gate.run_gate(candidate_file, "quick", lanes=(), report_path=report)

    assert json.loads(report.read_text(encoding="utf-8")) == result


def test_last_lane_repository_drift_blocks_after_successful_subprocess(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    tracked = Path(candidate["repositories"]["adminEsp"]["path"]) / "tracked.txt"
    lane = _lane(
        "last",
        f"from pathlib import Path;Path({str(tracked)!r}).write_text('mutated')",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "last"
    assert result["lanes"][0]["exitCode"] == 0


def test_final_revalidation_never_publishes_pass_report_after_lane_drift(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    tracked = Path(candidate["repositories"]["firmware"]["path"]) / "tracked.txt"
    report = tmp_path / "evidence/report.json"
    lane = _lane(
        "last",
        f"from pathlib import Path;Path({str(tracked)!r}).write_text('mutated')",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,), report_path=report)

    assert result["verdict"] == "BLOCKED"
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "BLOCKED"


def test_report_write_is_followed_by_release_state_revalidation(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    tracked = Path(candidate["repositories"]["firmware"]["path"]) / "tracked.txt"
    report = tmp_path / "evidence/report.json"
    real_write = gate._write_report_atomic
    writes = 0

    def write_then_drift(path: Path, payload: dict, parent_fd: int) -> bool:
        nonlocal writes
        writes += 1
        written = real_write(path, payload, parent_fd)
        if writes == 1:
            tracked.write_text("drift-after-report", encoding="utf-8")
        return written

    monkeypatch.setattr(gate, "_write_report_atomic", write_then_drift)

    result = gate.run_gate(candidate_file, "quick", lanes=(), report_path=report)

    assert result["verdict"] == "BLOCKED"
    assert writes == 2
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "BLOCKED"


def test_failed_corrective_report_write_removes_stale_pass(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    tracked = Path(candidate["repositories"]["firmware"]["path"]) / "tracked.txt"
    report = tmp_path / "evidence/report.json"
    real_write = gate._write_report_atomic
    writes = 0

    def first_write_only(path: Path, payload: dict, parent_fd: int) -> bool:
        nonlocal writes
        writes += 1
        if writes > 1:
            return False
        written = real_write(path, payload, parent_fd)
        tracked.write_text("drift-after-report", encoding="utf-8")
        return written

    monkeypatch.setattr(gate, "_write_report_atomic", first_write_only)

    result = gate.run_gate(candidate_file, "quick", lanes=(), report_path=report)

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert not report.exists()


def test_failed_initial_report_write_removes_preexisting_stale_pass(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = tmp_path / "evidence/report.json"
    report.write_text('{"verdict":"PASS"}\n', encoding="utf-8")
    monkeypatch.setattr(gate, "_write_report_atomic", lambda *_args: False)

    result = gate.run_gate(candidate_file, "quick", lanes=(), report_path=report)

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert not report.exists()


def test_last_lane_candidate_manifest_drift_is_revalidated(candidate_file: Path) -> None:
    lane = _lane(
        "last",
        f"from pathlib import Path;Path({str(candidate_file)!r}).write_text('{{}}')",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "last"


def test_unsafe_report_destination_fails_closed(candidate_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("preserve", encoding="utf-8")
    report = tmp_path / "evidence/report.json"
    report.symlink_to(target)

    result = gate.run_gate(candidate_file, "quick", lanes=(), report_path=report)

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert target.read_text(encoding="utf-8") == "preserve"


def test_tracked_report_target_is_blocked_and_preserved(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    target = Path(candidate["repositories"]["adminEsp"]["path"]) / "tracked.txt"
    original = target.read_bytes()

    result = gate.run_gate(candidate_file, "quick", lanes=(), report_path=target)

    assert result == {
        "candidateId": candidate["candidateId"], "verdict": "BLOCKED",
        "lanes": [], "failedLane": "report",
    }
    assert target.read_bytes() == original


def test_repository_contained_evidence_root_is_blocked(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository_root = Path(candidate["repositories"]["adminEsp"]["path"])
    evidence_root = repository_root / "evidence"
    evidence_root.mkdir()
    candidate["evidenceRoot"] = str(evidence_root)
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")

    result = gate.run_gate(
        candidate_file, "quick", lanes=(), report_path=evidence_root / "report.json",
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert not (evidence_root / "report.json").exists()


def test_report_parent_swap_cannot_redirect_write_into_repository(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    evidence = tmp_path / "evidence"
    moved = tmp_path / "moved-evidence"
    repository = Path(candidate["repositories"]["adminEsp"]["path"])
    lane = _lane(
        "swap-report-parent",
        "from pathlib import Path; "
        f"Path({str(evidence)!r}).rename({str(moved)!r}); "
        f"Path({str(evidence)!r}).symlink_to({str(repository)!r}, target_is_directory=True)",
    )

    result = gate.run_gate(
        candidate_file, "quick", lanes=(lane,), report_path=evidence / "report.json",
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert not (repository / "report.json").exists()
    assert not (moved / "report.json").exists()


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
        "admin-course-mode-assignment-fixture",
        "admin-course-mode-assignment-new",
        "admin-course-mode-assignment-rollback",
        "esp-course-mode-full", "firmware-renderer",
        "firmware-handler", "firmware-backward-compatibility", "cross-contract-parity",
    ]
    for marker in (
        "verify-course-mode-curriculum", "run_host_native_lesson_cinematic_renderer_test.sh",
        "test:e2e:course-mode", "test:e2e:course-mode:assignment:new",
        "test:e2e:course-mode:assignment:rollback", gate.COURSE_MODE_SOFTWARE_TESTS,
    ):
        assert marker in commands
    assert all(not Path(lane.relative_cwd).is_absolute() for lane in gate.lanes_for_mode("full"))


def test_full_backend_test_lane_disables_vitest_cache() -> None:
    backend_tests = next(
        lane for lane in gate.lanes_for_mode("full") if lane.name == "backend-tests"
    )

    assert backend_tests.command == ("npm", "test", "--", "--no-cache")


def test_full_esp_lane_discovers_every_committed_software_course_mode_suite() -> None:
    root = Path(__file__).resolve().parents[3]
    discovered = gate.discover_esp_course_mode_tests(root, _git(root, "rev-parse", "HEAD"))
    expected = (
        "tests/test_course_mode_candidate_manifest.py",
        "tests/test_course_mode_contract.py",
        "tests/test_course_mode_cross_process_e2e.py",
        "tests/test_course_mode_curriculum.py",
        "tests/test_course_mode_curriculum_e2e.py",
        "tests/test_course_mode_e2e_journeys.py",
        "tests/test_course_mode_evidence_audit.py",
        "tests/test_course_mode_forwarder.py",
        "tests/test_course_mode_physical_tft_compose.py",
        "tests/test_course_mode_physical_tft_ledger_validate.py",
        "tests/test_course_mode_physical_tft_preflight.py",
        "tests/test_course_mode_physical_tft_receipt_verify.py",
        "tests/test_course_mode_renderer_v4_persistence.py",
        "tests/test_course_mode_resource_soak.py",
        "tests/test_course_mode_runtime_compatibility.py",
        "tests/test_course_mode_runtime_integration.py",
        "tests/test_course_mode_task00_contract.py",
        "tests/test_course_mode_task06_validation_script.py",
        "tests/test_course_mode_task07_evidence_validate.py",
        "tests/test_google_live_course_mode.py",
    )

    assert discovered == expected
    assert "tests/test_course_mode_candidate_manifest.py" in discovered
    assert "tests/test_course_mode_task07_evidence_validate.py" in discovered
    assert gate.classify_esp_course_mode_test("tests/test_course_mode_cross_process_e2e.py") == "software"
    assert gate.classify_esp_course_mode_test("tests/test_course_mode_evidence_audit.py") == "software"
    assert "tests/test_google_live_course_mode.py" in discovered
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
    (tests / "test_google_live_course_mode.py").write_text("def test_ok(): pass\n")
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
        "tests/test_google_live_course_mode.py",
    )
    assert gate.select_esp_software_tests(gate.discover_esp_course_mode_tests(root, sha)) == (
        "tests/test_course_mode_committed.py",
        "tests/test_google_live_course_mode.py",
    )


def test_selected_esp_test_drift_blocks_before_execution(candidate_file: Path, tmp_path: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = candidate["repositories"]["adminEsp"]
    root = Path(repository["path"])
    selected = root / "main/tbot-server/tests/test_course_mode_selected.py"
    selected.parent.mkdir(parents=True)
    selected.write_text("def test_ok(): pass\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "selected test")
    repository.update(_repository(root))
    candidate_file.write_text(json.dumps(candidate))
    selected.write_text("def test_drift(): pass\n")
    repository["dirtyExceptions"] = [{
        "path": "main/tbot-server/tests/test_course_mode_selected.py",
        "sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
    }]
    candidate_file.write_text(json.dumps(candidate))
    marker = tmp_path / "must-not-run"
    lane = gate.Lane(
        name="esp-course-mode-full", repository="adminEsp", relative_cwd="main/tbot-server",
        command=(sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"),
        timeout_sec=5.0,
    )

    result = gate.run_gate(candidate_file, "full", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "esp-course-mode-full"
    assert not marker.exists()


@pytest.mark.parametrize(
    ("repository_name", "relative", "lane"),
    [
        (
            "backend", "src/runtime-dependency.ts",
            gate.Lane("backend-policy", "backend", ".", (sys.executable, "-c", "pass"), 5.0),
        ),
        (
            "adminEsp", "main/manager-web/src/runtime-dependency.js",
            gate.Lane(
                "admin-policy", "adminEsp", "main/manager-web",
                (sys.executable, "-c", "pass"), 5.0,
            ),
        ),
        (
            "adminEsp", "main/tbot-server/scripts/runtime_dependency.py",
            gate.Lane(
                "esp-policy", "adminEsp", "main/tbot-server",
                (sys.executable, "-c", "pass"), 5.0,
            ),
        ),
        (
            "adminEsp", "main/tbot-server/tests/conftest.py",
            gate.Lane(
                "esp-policy", "adminEsp", "main/tbot-server",
                (sys.executable, "-c", "pass"), 5.0,
            ),
        ),
        (
            "adminEsp", "main/tbot-server/scripts/course_mode_physical_tft_helper.py",
            gate.PHYSICAL_PREFLIGHT_LANE,
        ),
        (
            "firmware", "main/runtime_dependency.cpp",
            gate.Lane("firmware-policy", "firmware", ".", (sys.executable, "-c", "pass"), 5.0),
        ),
    ],
)
def test_lane_dirty_runtime_dependencies_have_no_execution_authority(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    repository_name: str, relative: str, lane: gate.Lane,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _commit_then_dirty(candidate, repository_name, relative)
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    marker = tmp_path / "must-not-run"
    probe = lane
    if lane is gate.PHYSICAL_PREFLIGHT_LANE:
        monkeypatch.setattr(
            gate, "_command_for_lane",
            lambda _lane, _candidate: (
                sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()",
            ),
        )
    else:
        probe = gate.Lane(
            lane.name, lane.repository, lane.relative_cwd,
            (sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"),
            lane.timeout_sec,
        )

    assert gate.lane_dirty_exceptions_authorized(probe, candidate) is False
    result = gate.run_gate(candidate_file, "full", lanes=(probe,))
    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == probe.name
    assert not marker.exists()


def test_unselected_standalone_voice_test_dirty_exception_blocks_admin_lane(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    voice_test = "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    _commit_then_dirty(candidate, "adminEsp", voice_test)
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    marker = tmp_path / "ran"
    lane = gate.Lane(
        "esp-policy", "adminEsp", "main/tbot-server",
        (sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()"), 5.0,
    )

    assert gate.lane_dirty_exceptions_authorized(lane, candidate) is False
    result = gate.run_gate(candidate_file, "full", lanes=(lane,))
    assert result["verdict"] == "BLOCKED"
    assert not marker.exists()


def test_all_admin_test_dirty_exceptions_are_rejected(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = candidate["repositories"]["adminEsp"]
    root = Path(repository["path"])
    selected = root / "main/tbot-server/tests/test_selected.py"
    dependency = root / "main/tbot-server/tests/test_dependency.py"
    selected.parent.mkdir(parents=True, exist_ok=True)
    selected.write_text("import test_dependency\n", encoding="utf-8")
    dependency.write_text("VALUE = 'committed'\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "add imported test fixture")
    repository.update(_repository(root))
    dependency.write_text("VALUE = 'dirty'\n", encoding="utf-8")
    repository["dirtyExceptions"] = [{
        "path": "main/tbot-server/tests/test_dependency.py",
        "sha256": hashlib.sha256(dependency.read_bytes()).hexdigest(),
    }]
    lane = gate.Lane(
        "esp-policy", "adminEsp", "main/tbot-server",
        (sys.executable, "-m", "pytest", "-q", "tests/test_selected.py"), 5.0,
    )

    assert gate.lane_dirty_exceptions_authorized(lane, candidate) is False


@pytest.mark.parametrize(
    "relative",
    ["docs/course-mode.md", "docker/course-mode.Dockerfile", "deploy/course-mode.yaml"],
)
def test_admin_lane_rejects_dirty_exceptions_outside_unselected_standalone_tests(
    candidate_file: Path, relative: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _commit_then_dirty(candidate, "adminEsp", relative)
    lane = gate.Lane(
        "esp-policy", "adminEsp", "main/tbot-server",
        (sys.executable, "-c", "pass"), 5.0,
    )

    assert gate.lane_dirty_exceptions_authorized(lane, candidate) is False


def test_node_lane_requires_candidate_bound_install_metadata(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane("backend-node", "backend", ".", ("node", "script.js"), 5.0)

    assert gate.node_install_authorized(lane, candidate) is False

    _add_node_install(candidate, "backend", ".", "backend")

    assert gate.node_install_authorized(lane, candidate) is True


def test_non_node_lane_does_not_require_install_metadata(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane(
        "esp-python", "adminEsp", "main/tbot-server",
        (sys.executable, "-c", "pass"), 5.0,
    )

    assert gate.node_install_authorized(lane, candidate) is True


@pytest.mark.parametrize("mutation", ["content", "mode", "outside-symlink", "special"])
def test_node_install_tree_rejects_unbound_or_unsafe_changes(
    candidate_file: Path, tmp_path: Path, mutation: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(candidate, "backend", ".", "backend")
    lane = gate.Lane("backend-node", "backend", ".", ("node", "script.js"), 5.0)
    target = install / "fixture-package/index.js"
    if mutation == "content":
        target.write_text("module.exports = 2;\n", encoding="utf-8")
    elif mutation == "mode":
        target.chmod(0o755)
    elif mutation == "outside-symlink":
        target.unlink()
        target.symlink_to(tmp_path / "outside.js")
    else:
        target.unlink()
        os.mkfifo(target.parent / "unsafe.fifo")

    assert gate.node_install_authorized(lane, candidate) is False


def test_node_install_symlink_loop_fails_closed_without_exception(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(candidate, "backend", ".", "backend")
    (install / "loop-a").symlink_to("loop-b")
    (install / "loop-b").symlink_to("loop-a")
    lane = gate.Lane("backend-node", "backend", ".", ("node", "script.js"), 5.0)

    assert gate.node_install_authorized(lane, candidate) is False


def test_node_install_metadata_binds_lock_digest_counts_and_bytes(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    lane = gate.Lane("backend-node", "backend", ".", ("npm", "test"), 5.0)
    metadata = candidate["tools"]["nodeInstalls"]["backend"]

    for field, replacement in (
        ("packageLockSha256", "0" * 64),
        ("entryCount", metadata["treeDigest"]["entryCount"] + 1),
        ("totalBytes", metadata["treeDigest"]["totalBytes"] + 1),
    ):
        altered = json.loads(json.dumps(candidate))
        if field == "packageLockSha256":
            altered["tools"]["nodeInstalls"]["backend"][field] = replacement
        else:
            altered["tools"]["nodeInstalls"]["backend"]["treeDigest"][field] = replacement
        assert gate.node_install_authorized(lane, altered) is False


def test_admin_manager_node_install_uses_exact_manager_web_root(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    lane = gate.Lane(
        "admin-node", "adminEsp", "main/manager-web", ("npx", "playwright", "test"), 5.0,
    )

    assert gate.node_install_authorized(lane, candidate) is True
    candidate["tools"]["nodeInstalls"]["adminManagerWeb"]["root"] = str(
        Path(candidate["repositories"]["adminEsp"]["path"]) / "node_modules"
    )
    assert gate.node_install_authorized(lane, candidate) is False


def test_npx_lane_requires_candidate_bound_local_binary(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(candidate, "backend", ".", "backend")
    lane = gate.Lane("backend-npx", "backend", ".", ("npx", "vitest", "run"), 5.0)

    assert gate.node_install_authorized(lane, candidate) is True
    (install / ".bin/vitest").unlink()
    assert gate.node_install_authorized(lane, candidate) is False


def test_node_install_rejects_ancestor_node_modules(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    repository_root = Path(candidate["repositories"]["adminEsp"]["path"])
    (repository_root / "node_modules").mkdir()
    lane = gate.Lane(
        "admin-node", "adminEsp", "main/manager-web", ("npm", "test"), 5.0,
    )

    assert gate.node_install_authorized(lane, candidate) is False


def test_node_install_rejects_nested_ignored_node_modules(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    repository_root = Path(candidate["repositories"]["backend"]["path"])
    nested = repository_root / "src/node_modules/unbound-package"
    nested.mkdir(parents=True)
    (nested / "index.js").write_text("module.exports = 'unbound';\n", encoding="utf-8")
    lane = gate.Lane("backend-node", "backend", ".", ("node", "src/run.js"), 5.0)

    assert gate.node_install_authorized(lane, candidate) is False


def test_node_install_rejects_casefold_equivalent_nested_install(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    repository_root = Path(candidate["repositories"]["backend"]["path"])
    (repository_root / "src/NODE_MODULES").mkdir(parents=True)
    lane = gate.Lane("backend-node", "backend", ".", ("node", "src/run.js"), 5.0)

    assert gate.node_install_authorized(lane, candidate) is False


def test_node_install_is_revalidated_after_lane_execution(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(candidate, "backend", ".", "backend")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    lane = gate.Lane("backend-node", "backend", ".", ("node", "script.js"), 5.0)

    def mutate_install(*_args, **_kwargs):
        (install / "fixture-package/index.js").write_text("changed\n", encoding="utf-8")
        return gate._manifest.BoundedCommandResult(0, "", None)

    monkeypatch.setattr(gate, "run_bounded_command", mutate_install)
    monkeypatch.setattr(gate, "_resolve_command", lambda command: command)

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "backend-node"


def test_node_digest_calls_scale_with_current_node_lanes_not_all_checkpoints(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    lanes = (
        gate.Lane("backend-node", "backend", ".", ("node", "-e", ""), 5.0),
        gate.Lane("python-one", "adminEsp", "main/tbot-server", (sys.executable, "-c", "pass"), 5.0),
        gate.Lane("backend-npm", "backend", ".", ("npm", "test"), 5.0),
        gate.Lane("admin-npm", "adminEsp", "main/manager-web", ("npm", "test"), 5.0),
        gate.Lane("python-two", "adminEsp", "main/tbot-server", (sys.executable, "-c", "pass"), 5.0),
    )
    real_descriptor = gate._node_tree_descriptor
    calls = 0

    def counted_descriptor(root: Path):
        nonlocal calls
        calls += 1
        return real_descriptor(root)

    monkeypatch.setattr(gate, "_node_tree_descriptor", counted_descriptor)
    monkeypatch.setattr(gate, "_resolve_command", lambda command: command)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *_args, **_kwargs: gate._manifest.BoundedCommandResult(0, "", None),
    )

    result = gate.run_gate(candidate_file, "quick", lanes=lanes)

    assert result["verdict"] == "PASS"
    assert calls == 11


def test_install_mutation_between_lanes_blocks_before_next_relevant_lane(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(candidate, "backend", ".", "backend")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    marker = tmp_path / "must-not-run"
    lanes = (
        gate.Lane("python-mutator", "adminEsp", "main/tbot-server", ("python", "mutate"), 5.0),
        gate.Lane("backend-node", "backend", ".", ("node", "script.js"), 5.0),
    )

    def run(command, **_kwargs):
        if command[0] == "python":
            (install / "fixture-package/index.js").write_text("changed\n", encoding="utf-8")
        else:
            marker.touch()
        return gate._manifest.BoundedCommandResult(0, "", None)

    monkeypatch.setattr(gate, "_resolve_command", lambda command: command)
    monkeypatch.setattr(gate, "run_bounded_command", run)

    result = gate.run_gate(candidate_file, "quick", lanes=lanes)

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "backend-node"
    assert not marker.exists()


@pytest.mark.parametrize("repository_name", ["backend", "firmware"])
def test_physical_preflight_rejects_dirty_cross_repository_authority_before_command(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    repository_name: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _commit_then_dirty(candidate, repository_name, "runtime-dependency.txt")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    marker = tmp_path / "must-not-run"
    monkeypatch.setattr(
        gate, "_command_for_lane",
        lambda _lane, _candidate: (
            sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()",
        ),
    )
    monkeypatch.setattr(gate, "lane_candidate_paths", lambda _lane, _candidate: ())

    result = gate.run_gate(
        candidate_file, "physical-preflight",
        runtime_root=Path(candidate["repositories"]["adminEsp"]["path"]),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "physical-tft-preflight"
    assert not marker.exists()


@pytest.mark.parametrize(
    "relative",
    ["main/manager-web/src/dirty.js", "shared/admin-runtime.txt"],
)
def test_physical_preflight_rejects_dirty_admin_paths_outside_unselected_tests(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _commit_then_dirty(candidate, "adminEsp", relative)
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    marker = tmp_path / "must-not-run"
    monkeypatch.setattr(
        gate, "_command_for_lane",
        lambda _lane, _candidate: (
            sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()",
        ),
    )
    monkeypatch.setattr(gate, "lane_candidate_paths", lambda _lane, _candidate: ())

    result = gate.run_gate(
        candidate_file, "physical-preflight",
        runtime_root=Path(candidate["repositories"]["adminEsp"]["path"]),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "physical-tft-preflight"
    assert not marker.exists()


def test_physical_preflight_blocks_protected_unselected_voice_exception(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    voice_test = "main/tbot-server/tests/test_lesson_voice_output_discipline.py"
    _commit_then_dirty(candidate, "adminEsp", voice_test)
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    marker = tmp_path / "ran"
    monkeypatch.setattr(
        gate, "_command_for_lane",
        lambda _lane, _candidate: (
            sys.executable, "-c", f"from pathlib import Path;Path({str(marker)!r}).touch()",
        ),
    )
    monkeypatch.setattr(gate, "lane_candidate_paths", lambda _lane, _candidate: ())

    result = gate.run_gate(
        candidate_file, "physical-preflight",
        runtime_root=Path(candidate["repositories"]["adminEsp"]["path"]),
    )

    assert result["verdict"] == "BLOCKED"
    assert not marker.exists()


def test_full_esp_lane_maps_task06_roots_and_rejects_skips(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(lane for lane in gate.lanes_for_mode("full") if lane.name == "esp-course-mode-full")

    environment = gate._child_environment(candidate, {}, lane)

    assert environment["TASK06_BACKEND_ROOT"] == candidate["repositories"]["backend"]["path"]
    assert environment["TASK06_FIRMWARE_ROOT"] == candidate["repositories"]["firmware"]["path"]
    assert lane.reject_pytest_skips is True


def test_assignment_lane_identity_is_derived_only_from_candidate(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(
        item for item in gate.lanes_for_mode("full")
        if item.name == "admin-course-mode-assignment-new"
    )
    hostile = {
        "TBOT_BACKEND_WORKTREE": "/attacker/backend",
        "TBOT_FIRMWARE_WORKTREE": "/attacker/firmware",
        "TBOT_LESSON_STUDIO_BACKEND_IMAGE": "attacker/backend:latest",
        "TBOT_LESSON_STUDIO_WEB_IMAGE": "attacker/web:latest",
        "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME": "tbot-task4-unit",
        "LESSON_STUDIO_E2E_RESOURCE_PREFIX": "tbot-task4-unit",
        "TASK4_ASSIGNMENT_RUNTIME_ROOT": str(Path(candidate["repositories"]["adminEsp"]["path"]) / "main/manager-web/output/task4"),
        "JWT_PUBLIC_KEY": "test-public-key",
        "TBOT_DEVICE_MINT_SECRET": "test-mint-secret",
        "LESSON_ASSET_ORIGIN_BASE": "https://task4-media.localhost:18443/tvideo-demo",
        "ROBOT_ESP_BASE_URL": "http://127.0.0.1:18013",
    }

    environment = gate._child_environment(candidate, hostile, lane)

    assert environment["TBOT_BACKEND_WORKTREE"] == candidate["repositories"]["backend"]["path"]
    assert environment["TBOT_FIRMWARE_WORKTREE"] == candidate["repositories"]["firmware"]["path"]
    assert environment["TBOT_LESSON_STUDIO_BACKEND_IMAGE"] == "local/backend:candidate"
    assert environment["TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID"] == "sha256:" + "1" * 64
    assert environment["TBOT_LESSON_STUDIO_WEB_IMAGE"] == "local/web:candidate"
    assert environment["TBOT_LESSON_STUDIO_WEB_IMAGE_ID"] == "sha256:" + "2" * 64
    assert environment["LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME"] == "tbot-task4-unit"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda images: images.pop("lessonStudioWeb"),
        lambda images: images["lessonStudioBackend"].pop("id"),
        lambda images: images["lessonStudioWeb"].update({"id": "sha256:mutable"}),
        lambda images: images["lessonStudioBackend"].update({"reference": ""}),
    ],
)
def test_assignment_lane_blocks_malformed_candidate_image_identity(
    candidate_file: Path, mutation,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    mutation(candidate["images"])
    lane = next(
        item for item in gate.lanes_for_mode("full")
        if item.name == "admin-course-mode-assignment-new"
    )

    assert gate._assignment_candidate_environment(candidate, lane) is None


def test_assignment_mount_inputs_are_bound_to_backend_and_firmware_commits(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))

    assert gate.assignment_input_sources_ready(candidate) is True

    backend = Path(candidate["repositories"]["backend"]["path"])
    (backend / gate.TASK4_BACKEND_MOUNT_INPUTS[0]).write_bytes(b"retag-race-input")
    assert gate.assignment_input_sources_ready(candidate) is False


@pytest.mark.parametrize("repository,relative", [
    ("backend", "src/lessons/fixtures/tvideo-raw-code/assets/admin"),
    ("backend", "src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json"),
    ("firmware", "lesson/assets"),
    ("firmware", "lesson/assets/robot/poses/bright-teach.png"),
])
def test_assignment_mount_inputs_reject_overlapping_dirty_exceptions(
    candidate_file: Path, repository: str, relative: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    candidate["repositories"][repository]["dirtyExceptions"] = [{
        "path": relative, "sha256": "1" * 64,
    }]

    assert gate.assignment_input_sources_ready(candidate) is False


@pytest.mark.parametrize("skipped,expected", [(0, False), (1, True), (7, True)])
def test_pytest_junit_skip_detection_is_deterministic(tmp_path: Path, skipped: int, expected: bool) -> None:
    report = tmp_path / "pytest.xml"
    root = ET.Element("testsuites", tests="8", failures="0", errors="0", skipped=str(skipped))
    ET.ElementTree(root).write(report, encoding="utf-8", xml_declaration=True)

    assert gate.pytest_report_has_skips(report) is expected


def test_missing_or_malformed_pytest_report_blocks_skip_admission(tmp_path: Path) -> None:
    missing = tmp_path / "missing.xml"
    malformed = tmp_path / "malformed.xml"
    empty = tmp_path / "empty.xml"
    malformed.write_text("not xml")
    empty.write_text('<testsuites tests="0" skipped="0"/>')

    assert gate.pytest_report_has_skips(missing) is None
    assert gate.pytest_report_has_skips(malformed) is None
    assert gate.pytest_report_has_skips(empty) is None


def test_exit_zero_with_required_pytest_skip_blocks_aggregate(candidate_file: Path) -> None:
    code = (
        "import pathlib,sys;"
        "path=next(value.split('=',1)[1] for value in sys.argv if value.startswith('--junitxml='));"
        "pathlib.Path(path).write_text('<testsuites tests=\"1\" skipped=\"1\"/>')"
    )
    lane = gate.Lane(
        name="skip-aware", repository="adminEsp", relative_cwd=".",
        command=(sys.executable, "-c", code), timeout_sec=5.0,
        reject_pytest_skips=True,
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "skip-aware"
    assert result["lanes"][0]["exitCode"] == 0


def test_playwright_full_has_named_desktop_and_mobile_chromium_and_webkit_lanes() -> None:
    lanes = [lane for lane in gate.lanes_for_mode("full") if "playwright" in lane.name]

    assert [lane.command[-1] for lane in lanes] == [
        "--project=course-mode-chromium-desktop",
        "--project=course-mode-webkit-desktop",
        "--project=course-mode-chromium-mobile",
        "--project=course-mode-webkit-mobile",
    ]
    assert all(lane.required_source_contract == "course-mode-playwright" for lane in lanes)


def test_playwright_full_adds_explicit_new_and_rollback_assignment_lanes() -> None:
    lanes = {lane.name: lane for lane in gate.lanes_for_mode("full")}

    assert lanes["admin-course-mode-assignment-fixture"].command == (
        "npm", "run", "test:course-mode:assignment-fixture",
    )
    assert lanes["admin-course-mode-assignment-new"].command == (
        "npm", "run", "test:e2e:course-mode:assignment:new",
    )
    assert lanes["admin-course-mode-assignment-rollback"].command == (
        "npm", "run", "test:e2e:course-mode:assignment:rollback",
    )
    assert lanes["admin-course-mode-assignment-new"].required_environment == gate.TASK4_ASSIGNMENT_CANDIDATE_ENV
    assert lanes["admin-course-mode-assignment-rollback"].required_environment == gate.TASK4_ASSIGNMENT_CANDIDATE_ENV
    assert all(
        lanes[name].required_source_contract == "course-mode-playwright"
        for name in (
            "admin-course-mode-assignment-fixture",
            "admin-course-mode-assignment-new",
            "admin-course-mode-assignment-rollback",
        )
    )


def test_current_playwright_source_contract_is_complete_and_committed() -> None:
    admin_root = Path(__file__).resolve().parents[3]

    assert gate.source_contract_ready(
        admin_root, "course-mode-playwright", _git(admin_root, "rev-parse", "HEAD"),
    ) is True


def test_playwright_contract_requires_named_projects_and_matching_devices(tmp_path: Path) -> None:
    _, sha = _commit_playwright_fixture(tmp_path)

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is True

@pytest.mark.parametrize(
    "script",
    [
        "playwright test --config=playwright.config.js --list",
        "playwright test --config=playwright.config.js --pass-with-no-tests",
        "playwright test --config=playwright.config.js e2e/lesson-studio/course-mode-authoring.spec.js",
    ],
)
def test_playwright_contract_rejects_noncanonical_or_nonexecuting_scripts(
    tmp_path: Path, script: str,
) -> None:
    _, sha = _commit_playwright_fixture(tmp_path, script=script)

    assert gate.source_contract_ready(
        tmp_path, "course-mode-playwright", sha,
    ) is False


@pytest.mark.parametrize(
    "config_mutator",
    [
        lambda value: value + "// comment\n",
        lambda value: "if (process.env.CI) { throw new Error('branch'); }\n" + value,
        lambda value: value.replace("@playwright/test", "playwright"),
        lambda value: value.replace("lessonStudioWebOrigin()", "'http://localhost:3000'"),
    ],
)
def test_playwright_contract_rejects_any_noncanonical_config_bytes(
    tmp_path: Path, config_mutator,
) -> None:
    _, sha = _commit_playwright_fixture(
        tmp_path, config=config_mutator(_valid_playwright_config()),
    )

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


@pytest.mark.parametrize(
    "mutator",
    [
        lambda contract: contract.update({"extra": True}),
        lambda contract: contract["projects"][0].update({"device": "Desktop Safari"}),
        lambda contract: contract["projects"][2]["viewport"].update({"width": 391}),
        lambda contract: contract.update({"testMatch": ["rewards.spec.js"]}),
        lambda contract: contract.update({"specs": ["e2e/lesson-studio/missing.spec.js"]}),
        lambda contract: contract["assignmentPhases"].update({"newCommand": "playwright test"}),
        lambda contract: contract["assignmentPhases"]["sourcePaths"].pop(),
        lambda contract: contract["fixed"].update({"workers": 2}),
    ],
)
def test_playwright_contract_rejects_schema_or_inventory_variation(
    tmp_path: Path, mutator,
) -> None:
    contract = _valid_playwright_contract()
    mutator(contract)
    _, sha = _commit_playwright_fixture(tmp_path, contract=contract, config="module.exports = {};\n")

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


@pytest.mark.parametrize(
    "mutator",
    [
        lambda paths: paths.reverse(),
        lambda paths: paths.append(paths[0]),
        lambda paths: paths.__setitem__(0, "../outside.yml"),
        lambda paths: paths.__setitem__(0, "/absolute.yml"),
        lambda paths: paths.__setitem__(0, ""),
    ],
)
def test_playwright_contract_rejects_nondeterministic_or_unsafe_source_paths(mutator) -> None:
    contract = _valid_playwright_contract()
    mutator(contract["sourcePaths"])

    assert gate.validate_playwright_contract(contract) is False


@pytest.mark.parametrize(
    "relative",
    [
        "docs/docker/docker-compose.lesson-studio-e2e.yml",
        "docs/docker/lesson-studio-e2e/seed-postgres.sql",
        "main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs",
        "main/manager-web/src/components/lesson/CinematicVideoLayer.vue",
        "main/manager-web/src/i18n/vi.js",
        "main/manager-web/e2e/lesson-studio/course-mode-authoring.spec.js-snapshots/course-mode-step-1-course-mode-webkit-desktop-darwin.png",
    ],
)
def test_changed_course_mode_sources_are_candidate_bound(tmp_path: Path, relative: str) -> None:
    _, sha = _commit_playwright_fixture(tmp_path)
    (tmp_path / relative).write_bytes(b"dirty working bytes\n")

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


def test_playwright_source_inventory_requires_every_path_to_exist(tmp_path: Path) -> None:
    _, sha = _commit_playwright_fixture(tmp_path)
    (tmp_path / _PLAYWRIGHT_SOURCE_PATHS[-1]).unlink()

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


@pytest.mark.parametrize(
    "mutator",
    [
        lambda contract: contract.update({"version": True}),
        lambda contract: contract["fixed"].update({"workers": True}),
        lambda contract: contract["projects"][0]["viewport"].update({"width": 1440.0}),
    ],
)
def test_playwright_contract_rejects_json_type_substitution(
    tmp_path: Path, mutator,
) -> None:
    contract = _valid_playwright_contract()
    mutator(contract)
    assert gate.validate_playwright_contract(contract) is False
    _, sha = _commit_playwright_fixture(tmp_path, contract=contract, config="module.exports = {};\n")

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


def test_release_gate_source_is_python_39_compatible() -> None:
    source = Path(gate.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 9))

    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "zip"
        and any(keyword.arg == "strict" for keyword in node.keywords)
        for node in ast.walk(tree)
    )
    assert ".stat(follow_symlinks=" not in source


@pytest.mark.parametrize("contract_raw", ["{", '{"version":1,"version":1}'])
def test_playwright_contract_rejects_malformed_or_duplicate_json(
    tmp_path: Path, contract_raw: str,
) -> None:
    _, sha = _commit_playwright_fixture(
        tmp_path, contract_raw=contract_raw, config=_valid_playwright_config(),
    )

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


@pytest.mark.parametrize(
    "relative",
    [
        "package.json", "playwright.config.js", "course-mode.playwright.contract.json",
        "e2e/lesson-studio/course-mode-authoring.spec.js",
    ],
)
def test_playwright_package_config_contract_and_specs_are_candidate_bound(
    tmp_path: Path, relative: str,
) -> None:
    web, sha = _commit_playwright_fixture(tmp_path)
    (web / relative).write_text("dirty working bytes\n", encoding="utf-8")

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


@pytest.mark.parametrize(
    "script_name",
    [
        "test:course-mode:assignment-fixture",
        "test:e2e:course-mode:assignment:new",
        "test:e2e:course-mode:assignment:rollback",
    ],
)
def test_playwright_contract_rejects_noncanonical_assignment_commands(
    tmp_path: Path, script_name: str,
) -> None:
    _, sha = _commit_playwright_fixture(
        tmp_path,
        assignment_script_mutator=lambda scripts: scripts.__setitem__(script_name, "true"),
    )

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


@pytest.mark.parametrize("relative", _valid_playwright_contract()["assignmentPhases"]["sourcePaths"])
def test_assignment_phase_sources_are_candidate_bound(tmp_path: Path, relative: str) -> None:
    _, sha = _commit_playwright_fixture(tmp_path)
    (tmp_path / relative).write_text("dirty working bytes\n", encoding="utf-8")

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


@pytest.mark.parametrize(
    "relative",
    [
        "e2e/lesson-studio/global-setup.cjs",
        "e2e/lesson-studio/helpers/session.js",
        "scripts/reset-lesson-studio-e2e-state.cjs",
        "scripts/lesson-studio-e2e-environment.cjs",
    ],
)
def test_playwright_transitive_harness_files_are_candidate_bound(
    tmp_path: Path, relative: str,
) -> None:
    web, sha = _commit_playwright_fixture(tmp_path)
    (web / relative).write_text("dirty working bytes\n", encoding="utf-8")

    assert gate.source_contract_ready(tmp_path, "course-mode-playwright", sha) is False


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
    evidence.mkdir(exist_ok=True)
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
    evidence.mkdir(exist_ok=True)
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
