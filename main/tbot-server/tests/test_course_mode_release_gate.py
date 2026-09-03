from __future__ import annotations

import ast
import contextlib
import errno
import gc
import hashlib
import importlib
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
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
    "main/manager-web/scripts/task4-assignment-runtime.cjs",
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
                "docs/docker/task4-admin-assignment/copy-file.cjs",
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
        "docs/docker/task4-admin-assignment/copy-file.cjs": "module.exports = {};\n",
        "docs/docker/task4-admin-assignment/docker-compose.new.yml": "services: {}\n",
        "docs/docker/task4-admin-assignment/docker-compose.rollback.yml": "services: {}\n",
        "docs/docker/task4-admin-assignment/serve-media.cjs": "module.exports = {};\n",
        "main/manager-web/e2e/lesson-studio/assignment-rollback-phase.spec.js": "test('assignment phase', async () => {});\n",
        "main/manager-web/playwright.assignment-rollback.config.js": "module.exports = require('./playwright.config');\n",
        "main/manager-web/scripts/prepare-task4-media-templates.cjs": "module.exports = {};\n",
        "main/manager-web/scripts/run-task4-assignment-phase.cjs": "module.exports = {};\n",
        "main/manager-web/scripts/task4-assignment-fixture.test.cjs": "require('node:test')('fixture', () => {});\n",
        "main/manager-web/scripts/task4-assignment-runtime.cjs": "module.exports = {};\n",
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


def _install_cjson_gitlink(
    candidate_file: Path, tmp_path: Path,
) -> tuple[dict, Path, Path, str, str]:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = tmp_path / "cjson-repository"
    repository.mkdir()
    _git(repository, "init", "-b", "candidate")
    _git(repository, "config", "user.email", "candidate@example.invalid")
    _git(repository, "config", "user.name", "Candidate Test")
    source = repository / "cJSON.c"
    source.write_text("pinned gitlink bytes\n", encoding="utf-8")
    _git(repository, "add", "cJSON.c")
    _git(repository, "commit", "-m", "pinned cjson")
    pinned_commit = _git(repository, "rev-parse", "HEAD")
    source.write_text("different checked-out bytes\n", encoding="utf-8")
    _git(repository, "commit", "-am", "later cjson")
    checkout_commit = _git(repository, "rev-parse", "HEAD")

    esp_idf = Path(candidate["tools"]["espIdf"]["root"])
    submodule_root = esp_idf / "components/json/cJSON"
    _git(esp_idf, "rm", "-r", "components/json/cJSON")
    _git(
        esp_idf, "-c", "protocol.file.allow=always", "submodule", "add",
        str(repository), "components/json/cJSON",
    )
    _git(submodule_root, "checkout", pinned_commit)
    _git(esp_idf, "add", ".gitmodules", "components/json/cJSON")
    _git(esp_idf, "commit", "-m", "use cjson gitlink")
    candidate["tools"]["espIdf"]["commit"] = _git(esp_idf, "rev-parse", "HEAD")
    entry = _git(esp_idf, "ls-tree", "HEAD", "components/json/cJSON")
    assert entry.split()[:2] == ["160000", "commit"]
    assert entry.split()[2] == pinned_commit
    git_file_record = (submodule_root / ".git").read_text(encoding="utf-8")
    assert git_file_record.startswith("gitdir: ") and git_file_record.endswith("\n")
    declared_git_dir = Path(git_file_record.removeprefix("gitdir: ").strip())
    assert not declared_git_dir.is_absolute()
    assert (submodule_root / declared_git_dir).resolve(strict=True) == (
        esp_idf / ".git/modules/components/json/cJSON"
    )
    _git(submodule_root, "checkout", checkout_commit)
    return candidate, esp_idf, submodule_root, pinned_commit, checkout_commit


def _record_stage_roots(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    roots: list[Path] = []
    original = gate.tempfile.mkdtemp

    def record(*args, **kwargs) -> str:
        path = Path(original(*args, **kwargs))
        if kwargs.get("prefix") == "course-mode-stage-":
            roots.append(path)
        return str(path)

    monkeypatch.setattr(gate.tempfile, "mkdtemp", record)
    return roots


def _assert_gitlink_stage_rejected(
    candidate: dict, lane: gate.Lane, stage_roots: list[Path],
) -> None:
    with pytest.raises(ValueError, match="candidate archive failed"):
        gate.stage_execution_candidate(candidate, (lane,))
    assert stage_roots and all(not path.exists() for path in stage_roots)


def _stat_result_with_uid(metadata: os.stat_result, uid: int) -> os.stat_result:
    fields = list(metadata)
    fields[4] = uid
    return os.stat_result(fields)


def _repository(root: Path) -> dict:
    return {
        "path": str(root),
        "sha": _git(root, "rev-parse", "--verify", "HEAD^{commit}"),
        "branch": _git(root, "branch", "--show-current"),
        "remoteUrl": _git(root, "remote", "get-url", "origin"),
        "dirtyExceptions": [],
    }


def _refresh_image_reference(candidate: dict, repository_name: str) -> None:
    if repository_name not in {"backend", "adminEsp"}:
        return
    repository = candidate["repositories"][repository_name]
    key = "lessonStudioBackend" if repository_name == "backend" else "lessonStudioWeb"
    image = "local/tbot-backend" if repository_name == "backend" else "local/tbot-server-web"
    candidate["images"][key]["reference"] = f"{image}:course-mode-physical-tft-{repository['sha']}"


def _commit_then_dirty(candidate: dict, repository_name: str, relative: str) -> None:
    repository = candidate["repositories"][repository_name]
    root = Path(repository["path"])
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("committed\n", encoding="utf-8")
    _git(root, "add", relative)
    _git(root, "commit", "-m", f"add {Path(relative).name}")
    repository.update(_repository(root))
    _refresh_image_reference(candidate, repository_name)
    if repository_name == "firmware":
        evidence_path = Path(candidate["firmware"]["evidenceManifestPath"])
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["sourceCommit"] = repository["sha"]
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
        candidate["firmware"]["evidenceManifestSha256"] = hashlib.sha256(
            evidence_path.read_bytes(),
        ).hexdigest()
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
    _refresh_image_reference(candidate, repository_name)
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


def _configure_backend_build_fixture(
    candidate: dict, *, omit: str | None = None, escape: str | Path | None = None,
    unix_socket: Path | None = None,
) -> None:
    descriptor = candidate["tools"]["node"]["backend"]
    npm_entrypoint = Path(descriptor["npm"]["entrypoint"])
    escape_source = (
        "(root.parent / 'adminEsp/build-escape.txt').write_text('escaped')\n"
        if escape == "adminEsp"
        else f"pathlib.Path({str(escape)!r}).write_text('escaped')\n"
        if isinstance(escape, Path)
        else ""
    )
    socket_source = (
        "import socket\n"
        "client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\n"
        f"client.connect({str(unix_socket)!r})\n"
        "client.sendall(b'candidate-build-message')\n"
        "client.close()\n"
        if unix_socket is not None else ""
    )
    npm_entrypoint.write_text(
        "import os, pathlib, sys\n"
        "assert sys.argv[1:] == ['run', 'build']\n"
        "assert pathlib.Path(os.environ['HOME']).parts[-2:] == ('build-runtime', 'home')\n"
        "assert pathlib.Path(os.environ['TMPDIR']).parts[-2:] == ('build-runtime', 'tmp')\n"
        "assert pathlib.Path(os.environ['XDG_CACHE_HOME']).parts[-2:] == ('build-runtime', 'cache')\n"
        "assert os.environ['CI'] == '1'\n"
        "assert 'HOST_BUILD_POISON' not in os.environ\n"
        "root = pathlib.Path.cwd()\n"
        "outputs = {\n"
        " 'curriculum-course-mode.js': 'candidate curriculum',\n"
        " 'curriculum-6month.js': 'candidate six month',\n"
        " 'course-mode.contract.js': 'candidate contract',\n"
        "}\n"
        "target = root / 'dist/lessons/course-mode'\n"
        "target.mkdir(parents=True, exist_ok=True)\n"
        f"{escape_source}"
        f"{socket_source}"
        f"outputs.pop({omit!r}, None)\n"
        "for name, value in outputs.items(): (target / name).write_text(value)\n",
        encoding="utf-8",
    )
    descriptor["npm"]["sha256"] = hashlib.sha256(npm_entrypoint.read_bytes()).hexdigest()
    package_root = Path(descriptor["packageRoot"])
    package_tree = gate._manifest.secure_node_package_tree_descriptor(package_root)
    assert package_tree is not None
    descriptor["packageRootMode"] = package_tree["rootMode"]
    descriptor["packageTreeSha256"] = package_tree["sha256"]


@pytest.fixture
def candidate_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
            migration = root / "src/database/migrations/127_shared_visual_layered_cinematic_compatibility.sql"
            migration.parent.mkdir(parents=True)
            migration.write_text("SELECT 127;\n", encoding="utf-8")
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
    browser = tmp_path / "ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"pinned chromium fixture\n")
    browser.chmod(0o755)
    tree, error = gate.secure_browser_bundle_descriptor(browser.parent)
    assert error is None and tree is not None
    playwright_browsers = {}
    for engine, revision, relative in (
        ("chromium-headless-shell", "1223", "chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"),
        ("webkit", "2287", "webkit-2287/pw_run.sh"),
        ("ffmpeg", "1011", "ffmpeg-1011/ffmpeg-mac"),
    ):
        executable = tmp_path / "playwright-browsers" / relative
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(f"{engine} fixture\n".encode())
        executable.chmod(0o755)
        bundle_root = executable.parents[1] if engine == "chromium-headless-shell" else executable.parent
        if engine == "webkit":
            (bundle_root / "current").symlink_to(executable.name)
        bundle_tree, bundle_error = gate.secure_playwright_browser_bundle_descriptor(bundle_root)
        assert bundle_error is None and bundle_tree is not None
        playwright_browsers[engine] = {
            "version": 1, "engine": engine, "revision": revision,
            "root": str(bundle_root), "executable": str(executable.relative_to(bundle_root)),
            "treeDigest": bundle_tree,
        }
    docker = tmp_path / "docker"
    backend_ref = f"local/tbot-backend:course-mode-physical-tft-{repositories['backend']['sha']}"
    web_ref = f"local/tbot-server-web:course-mode-physical-tft-{repositories['adminEsp']['sha']}"
    docker.write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        f"backend_source={repositories['backend']['remoteUrl']!r}\n"
        f"web_source={repositories['adminEsp']['remoteUrl']!r}\n"
        "if sys.argv[1:] == ['--version']: print('Docker version fixture'); sys.exit(0)\n"
        "ref=sys.argv[-1]\nvalue=None\n"
        "if ref.startswith('local/tbot-backend:course-mode-physical-tft-'): value={'Id':'sha256:'+'1'*64,'Config':{'Labels':{'org.opencontainers.image.revision':ref.rsplit('-',1)[-1],'org.opencontainers.image.source':backend_source}}}\n"
        "elif ref.startswith('local/tbot-server-web:course-mode-physical-tft-'): value={'Id':'sha256:'+'2'*64,'Config':{'Labels':{'org.opencontainers.image.revision':ref.rsplit('-',1)[-1],'org.opencontainers.image.source':web_source}}}\n"
        "elif ref=='postgres:16-alpine': value={'Id':'sha256:'+'3'*64,'Config':{'Labels':{}}}\n"
        "print(json.dumps(value)) if value is not None else sys.exit(1)\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setattr(gate._manifest, "TRUSTED_DOCKER_EXECUTABLE", docker)
    compose = tmp_path / "docker-compose"
    compose.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "print('Docker Compose version fixture') if sys.argv[1:] == ['version'] else sys.exit(0)\n",
        encoding="utf-8",
    )
    compose.chmod(0o755)
    monkeypatch.setattr(gate._manifest, "_container_tool_path_authorized", lambda *_args: True)
    firmware_dir = tmp_path / "firmware-artifact"
    firmware_dir.mkdir()
    app = firmware_dir / "xiaozhi.bin"
    elf = firmware_dir / "xiaozhi.elf"
    app.write_bytes(b"firmware-app")
    elf.write_bytes(b"firmware-elf")
    evidence = firmware_dir / "manifest.json"
    evidence_payload = {
        "status": "PASS", "profile": "production", "board": "LCDWiki ES3C35P", "target": "esp32s3",
        "sourceCommit": repositories["firmware"]["sha"], "createdAt": "2099-01-01T00:00:00Z",
        "app": {"file": app.name, "offset": "0x20000", "bytes": app.stat().st_size,
                "sha256": hashlib.sha256(app.read_bytes()).hexdigest()},
        "elf": {"file": elf.name, "bytes": elf.stat().st_size,
                "sha256": hashlib.sha256(elf.read_bytes()).hexdigest()},
        "partition": {"bytes": 1024, "freeBytes": 1024 - app.stat().st_size,
                      "freePercent": round((1024 - app.stat().st_size) / 1024 * 100, 6)},
        "reproducibility": {"appByteIdentical": True, "elfByteIdentical": True,
                            "independentCleanBuilds": 2, "ccacheEnabled": False},
        "toolchain": {"espIdf": "v5.5.4", "espIdfCommit": "a" * 40, "python": "3.9.6",
                      "compiler": "fixture", "cmake": "fixture", "ninja": "fixture"},
        "config": {"sdkconfigSha256": "a" * 64, "sdkconfigDefaultsLocalSha256": "b" * 64,
                   "dependenciesLockSha256": "c" * 64, "appReproducibleBuild": True,
                   "productionConfigAudit": "PASS", "productionArtifactAudit": "PASS"},
        "tests": {"projectSourceGate": "1378 passed", "firmwareVersionAndCourseGates": "18 passed"},
        "safety": {"flashed": False, "serialAccessed": False, "hilRun": False,
                   "physicalDeviceAccessed": False},
    }
    evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")
    node = {}
    for key, version in (("backend", "v22.23.2"), ("adminManagerWeb", "v20.20.2")):
        prefix = tmp_path / f"node-{key}"
        executable = prefix / "bin/node"
        executable.parent.mkdir(parents=True)
        executable.write_text(f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo {version}; else exec python3 \"$@\"; fi\n", encoding="utf-8")
        executable.chmod(0o755)
        package_tools = {}
        for tool in ("npm", "npx"):
            entrypoint = prefix / f"lib/node_modules/npm/bin/{tool}-cli.js"
            entrypoint.parent.mkdir(parents=True, exist_ok=True)
            entrypoint.write_text("raise SystemExit(0)\n", encoding="utf-8")
            package_tools[tool] = {"entrypoint": str(entrypoint),
                                   "sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest()}
        package_root = prefix / "lib/node_modules/npm"
        package_tree = gate._manifest.secure_node_package_tree_descriptor(package_root)
        assert package_tree is not None
        node[key] = {"version": version, "executable": str(executable),
                     "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                     "packageRoot": str(package_root),
                     "packageRootMode": package_tree["rootMode"],
                     "packageTreeSha256": package_tree["sha256"],
                     **package_tools}
    python_root = tmp_path / "python-test-runtime"
    python_executable = python_root / "bin/python3.11"
    python_executable.parent.mkdir(parents=True)
    python_executable.write_text(
        f"#!{sys.executable}\nimport json,os,pathlib,runpy,subprocess,sys\n"
        "def attack():\n"
        " target=pathlib.Path(__file__).resolve().parents[1]; parent=target.parent\n"
        " target_mode=target.stat().st_mode & 0o7777; parent_mode=parent.stat().st_mode & 0o7777\n"
        " moved=target.with_name(target.name+'-probe-moved')\n"
        " try: target.chmod(0o755); parent.chmod(0o755); target.rename(moved)\n"
        " except PermissionError: assert target.stat().st_mode & 0o222 == 0\n"
        " else:\n"
        "  moved.rename(target); target.chmod(target_mode); parent.chmod(parent_mode)\n"
        "  raise AssertionError('runtime probe escaped sandbox')\n"
        "if sys.argv[1:] == ['-I','-s','-c','import platform; print(platform.python_version())']:\n"
        " attack(); print('3.11.9')\n"
        "elif sys.argv[1:] == ['-I','-s','-m','pytest','-s','--version']:\n"
        " attack(); print('pytest 8.4.1')\n"
        "elif sys.argv[1:] == ['-I','-s','-c','import pytest, pytest_asyncio']:\n"
        " attack()\n"
        "elif len(sys.argv) == 5 and sys.argv[1:4] == ['-I','-s','-c'] and 'importlib,json' in sys.argv[4]:\n"
        " attack();\n"
        " root=pathlib.Path(__file__).resolve().parents[1]; paths=[str(root/'lib/python3.11')];\n"
        " print(json.dumps({'executable':str(root/'bin/python3.11'),'prefix':str(root),'basePrefix':str(root),"
        "'execPrefix':str(root),'baseExecPrefix':str(root),'stdlib':paths[0],'path':paths,'modules':paths}))\n"
        "elif sys.argv[1:6] == ['-I','-s','-m','pytest','-q']:\n"
        " assert os.environ.get('HOME') and os.environ['HOME'] != '/nonexistent'\n"
        " runtime=pathlib.Path(__file__).resolve().parents[1]; stage=runtime.parents[1]\n"
        " backend=pathlib.Path(os.environ['COURSE_MODE_BACKEND_ROOT'])\n"
        " assert backend.is_relative_to(stage) and not (backend/'.git').exists()\n"
        " authority=pathlib.Path(os.environ['COURSE_MODE_BACKEND_SNAPSHOT_AUTHORITY'])\n"
        " assert authority.is_relative_to(stage) and os.environ['COURSE_MODE_BACKEND_SHA']\n"
        " assert __import__('hashlib').sha256(authority.read_bytes()).hexdigest() == os.environ['COURSE_MODE_BACKEND_SNAPSHOT_AUTHORITY_SHA256']\n"
        " for target in (runtime,stage):\n"
        "  target_mode=target.stat().st_mode & 0o7777; parent_mode=target.parent.stat().st_mode & 0o7777\n"
        "  moved=target.with_name(target.name+'-moved')\n"
        "  try:\n"
        "   target.chmod(0o755)\n"
        "   if target == runtime: target.parent.chmod(0o755)\n"
        "   target.rename(moved)\n"
        "  except PermissionError: assert target.stat().st_mode & 0o222 == 0\n"
        "  else:\n"
        "   moved.rename(target); target.chmod(target_mode); target.parent.chmod(parent_mode)\n"
        "   raise AssertionError('sandbox allowed runtime replacement')\n"
        " child=subprocess.run([sys.executable,'-c','import pathlib,sys; pathlib.Path(sys.argv[1]).chmod(0o755)',str(stage)])\n"
        " assert child.returncode != 0\n"
        " for name in ('HOME','TMPDIR','XDG_CACHE_HOME','COURSE_MODE_LANE_REPORT_ROOT'):\n"
        "  path=pathlib.Path(os.environ[name]); path.mkdir(parents=True,exist_ok=True); (path/'write-ok').write_text('ok')\n"
        " junit=next((value.split('=',1)[1] for value in sys.argv if value.startswith('--junitxml=')),None)\n"
        " if junit:\n"
        "  assert pathlib.Path(junit).parent == pathlib.Path(os.environ['COURSE_MODE_LANE_REPORT_ROOT'])\n"
        "  pathlib.Path(junit).write_text('<testsuites tests=\"1\" skipped=\"0\"/>')\n"
        "else:\n"
        " sys.argv=sys.argv[1:]; runpy.run_module('pytest', run_name='__main__')\n",
        encoding="utf-8",
    )
    python_executable.chmod(0o555)
    python_executable.parent.chmod(0o555)
    python_root.chmod(0o555)
    monkeypatch.setattr(
        gate._manifest, "_python_runtime_library_authority", lambda _root, _executable: True,
    )
    python_tree, python_error = gate._manifest.secure_python_test_runtime_tree_descriptor(python_root)
    assert python_error is None and python_tree is not None
    esp_idf = tmp_path / "esp-idf"
    (esp_idf / "tools/cmake").mkdir(parents=True)
    (esp_idf / "tools/cmake/version.cmake").write_text(
        "set(IDF_VERSION_MAJOR 5)\nset(IDF_VERSION_MINOR 5)\nset(IDF_VERSION_PATCH 4)\n",
        encoding="utf-8",
    )
    cjson_source = esp_idf / "components/json/cJSON/cJSON.c"
    cjson_source.parent.mkdir(parents=True)
    cjson_source.write_text("/* candidate ESP-IDF cJSON fixture */\n", encoding="utf-8")
    _git(esp_idf, "init", "-b", "candidate")
    _git(esp_idf, "config", "user.email", "candidate@example.invalid")
    _git(esp_idf, "config", "user.name", "Candidate Test")
    _git(esp_idf, "add", ".")
    _git(esp_idf, "commit", "-m", "fixture")
    esp_commit = _git(esp_idf, "rev-parse", "HEAD")
    monkeypatch.setattr(gate._manifest, "CANONICAL_ESP_IDF_ROOT", esp_idf)
    evidence_payload["toolchain"]["espIdfCommit"] = esp_commit
    evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")
    migration = Path(repositories["backend"]["path"]) / "src/database/migrations/127_shared_visual_layered_cinematic_compatibility.sql"
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
                "reference": backend_ref,
                "id": "sha256:" + "1" * 64,
            },
            "lessonStudioWeb": {
                "reference": web_ref,
                "id": "sha256:" + "2" * 64,
            },
        },
        "firmware": {
            "appPath": str(app), "appOffset": "0x20000", "appBytes": app.stat().st_size,
            "appSha256": hashlib.sha256(app.read_bytes()).hexdigest(),
            "elfSha256": hashlib.sha256(elf.read_bytes()).hexdigest(),
            "partitionBytes": 1024, "freeBytes": 1024 - app.stat().st_size,
            "evidenceManifestPath": str(evidence),
            "evidenceManifestSha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        },
        "database": {"engineImage": "postgres:16-alpine", "engineImageId": "sha256:" + "3" * 64,
                     "migrationHead": migration.name,
                     "migrationHeadSha256": hashlib.sha256(migration.read_bytes()).hexdigest()},
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
        "tools": {
            "docker": {
                "path": str(docker),
                "sha256": hashlib.sha256(docker.read_bytes()).hexdigest(),
                "version": "Docker version fixture",
            },
            "dockerCompose": {
                "path": str(compose),
                "sha256": hashlib.sha256(compose.read_bytes()).hexdigest(),
                "version": "Docker Compose version fixture",
            },
            "pythonTestRuntime": {
                "version": 1, "distribution": "python-build-standalone",
                "root": str(python_root), "executable": "bin/python3.11",
                "pythonVersion": "3.11.9", "pytestVersion": "8.4.1",
                "treeDigest": python_tree,
            },
            "nodeInstalls": {}, "playwrightBrowsers": playwright_browsers,
            "robotPreviewBrowser": {
                "version": 2,
                "engine": "chromium-headless-shell",
                "revision": "1223",
                "root": str(browser.parent),
                "executable": browser.name,
                "treeDigest": tree,
            }, "node": node, "espIdf": {
                "version": "v5.5.4", "commit": esp_commit, "root": str(esp_idf),
                "versionFile": "tools/cmake/version.cmake",
                "versionFileSha256": hashlib.sha256(
                    (esp_idf / "tools/cmake/version.cmake").read_bytes(),
                ).hexdigest(),
            },
        },
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


def _runtime_root(candidate_file: Path) -> Path:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    return Path(candidate["repositories"]["adminEsp"]["path"])


def _assignment_source(candidate_file: Path) -> dict[str, str]:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    return {
        "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME": "tbot-task4-unit",
        "LESSON_STUDIO_E2E_RESOURCE_PREFIX": "tbot-task4-unit",
        "TASK4_ASSIGNMENT_RUNTIME_ROOT": str(
            Path(candidate["repositories"]["adminEsp"]["path"])
            / "main/manager-web/output/task4"
        ),
        "JWT_PUBLIC_KEY": "test-public-key",
        "TBOT_DEVICE_MINT_SECRET": "test-mint-secret",
        "LESSON_ASSET_ORIGIN_BASE": "https://task4-media.localhost:28443/tvideo-demo",
        "ROBOT_ESP_BASE_URL": "http://127.0.0.1:18013",
        "LESSON_STUDIO_E2E_BACKEND_HOST_PORT": "13100",
        "LESSON_STUDIO_E2E_WEB_HOST_PORT": "18102",
        "TASK4_ASSIGNMENT_MEDIA_HOST_PORT": "28443",
    }


def _operator_attestation_payload(candidate_file: Path) -> dict:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    return {
        "candidateId": candidate["candidateId"],
        "createdAt": "2099-01-01T00:00:00Z",
        "effectiveUid": os.geteuid(),
        "gateSha": candidate["repositories"]["adminEsp"]["sha"],
        "hostName": socket.gethostname(),
        "sameUidThreatModel": "malicious-process-excluded",
        "schemaVersion": 1,
        "trustedOperatorAccountConfirmed": True,
        "untrustedAutomationStoppedConfirmed": True,
    }


def _write_operator_attestation(
    candidate_file: Path, payload: dict | None = None, *, path: Path | None = None,
) -> Path:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    attestation = path or Path(candidate["evidenceRoot"]) / "operator-attestation.json"
    attestation.parent.mkdir(parents=True, exist_ok=True)
    attestation.write_text(
        json.dumps(payload or _operator_attestation_payload(candidate_file)), encoding="utf-8",
    )
    attestation.chmod(0o444)
    return attestation


def test_production_gate_blocks_without_operator_attestation(candidate_file: Path) -> None:
    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_production_gate_accepts_exact_operator_attestation(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
    monkeypatch.setattr(gate, "lanes_for_mode", lambda _mode: ())

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "PASS"
    assert result["operatorAttestationSha256"] == hashlib.sha256(
        attestation.read_bytes(),
    ).hexdigest()


def test_production_gate_rejects_report_path_equal_to_operator_attestation(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    original = attestation.read_bytes()
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
    monkeypatch.setattr(gate, "lanes_for_mode", lambda _mode: ())

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
        report_path=attestation,
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert attestation.read_bytes() == original


def test_production_gate_rejects_case_variant_report_alias_before_lane(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    report_alias = attestation.with_name(attestation.name.swapcase())
    try:
        aliases_attestation = os.path.samefile(report_alias, attestation)
    except FileNotFoundError:
        aliases_attestation = False
    if not aliases_attestation:
        pytest.skip("filesystem is case-sensitive")
    original = attestation.read_bytes()
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
    monkeypatch.setattr(
        gate, "lanes_for_mode", lambda _mode: (_lane("must-not-run", "raise SystemExit(0)"),),
    )
    original_run = gate.run_bounded_command
    calls = 0

    def count_lane(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_run(*args, **kwargs)

    monkeypatch.setattr(gate, "run_bounded_command", count_lane)

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
        report_path=report_alias,
    )

    assert calls == 0
    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert attestation.read_bytes() == original


def test_production_gate_replaces_pass_report_if_attestation_changes_during_publish(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    report_path = Path(candidate["evidenceRoot"]) / "report.json"
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
    monkeypatch.setattr(gate, "lanes_for_mode", lambda _mode: ())
    original_write = gate._write_report_atomic
    writes = 0

    def replace_attestation_after_pass(*args, **kwargs):
        nonlocal writes
        written = original_write(*args, **kwargs)
        writes += 1
        if writes == 1:
            replacement = attestation.with_name("operator-attestation-publish-race.json")
            replacement.write_text(
                json.dumps({
                    **_operator_attestation_payload(candidate_file),
                    "createdAt": "2099-01-02T00:00:00Z",
                }),
                encoding="utf-8",
            )
            replacement.chmod(0o444)
            replacement.replace(attestation)
        return written

    monkeypatch.setattr(gate, "_write_report_atomic", replace_attestation_after_pass)

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
        report_path=report_path,
    )

    published = json.loads(report_path.read_text(encoding="utf-8"))
    assert writes == 2
    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"
    assert published["verdict"] == "BLOCKED"
    assert published["failedLane"] == "operator-precondition"
    assert "operatorAttestationSha256" not in published


def test_corrective_report_does_not_replace_or_unlink_foreign_target(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    report_path = Path(candidate["evidenceRoot"]) / "report.json"
    foreign = b'{"owner":"other-run"}\n'
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
    monkeypatch.setattr(gate, "lanes_for_mode", lambda _mode: ())
    original_write = gate._write_report_atomic
    writes = 0

    def replace_owned_report_after_pass(*args, **kwargs):
        nonlocal writes
        written = original_write(*args, **kwargs)
        writes += 1
        if writes == 1:
            replacement_attestation = attestation.with_name("operator-attestation-race.json")
            replacement_attestation.write_text(
                json.dumps({
                    **_operator_attestation_payload(candidate_file),
                    "createdAt": "2099-01-02T00:00:00Z",
                }),
                encoding="utf-8",
            )
            replacement_attestation.chmod(0o444)
            replacement_attestation.replace(attestation)
            replacement_report = report_path.with_name("foreign-report.json")
            replacement_report.write_bytes(foreign)
            replacement_report.replace(report_path)
        return written

    monkeypatch.setattr(gate, "_write_report_atomic", replace_owned_report_after_pass)

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
        report_path=report_path,
    )

    assert writes == 2
    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert report_path.read_bytes() == foreign


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidateId", "course-mode-2099-01-01.2"),
        ("createdAt", "not-a-timestamp"),
        ("effectiveUid", -1),
        ("gateSha", "0" * 40),
        ("hostName", "other-host.invalid"),
        ("sameUidThreatModel", "malicious-process-covered"),
        ("schemaVersion", 2),
        ("trustedOperatorAccountConfirmed", False),
        ("untrustedAutomationStoppedConfirmed", False),
    ],
)
def test_production_gate_blocks_wrong_operator_attestation_field(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    payload = _operator_attestation_payload(candidate_file)
    payload[field] = value
    attestation = _write_operator_attestation(candidate_file, payload)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


@pytest.mark.parametrize("mutation", ["extra", "missing"])
def test_production_gate_requires_exact_operator_attestation_keys(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    payload = _operator_attestation_payload(candidate_file)
    if mutation == "extra":
        payload["unexpected"] = True
    else:
        del payload["createdAt"]
    attestation = _write_operator_attestation(candidate_file, payload)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_production_gate_rejects_operator_attestation_symlink(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _write_operator_attestation(candidate_file)
    symlink = target.with_name("operator-attestation-link.json")
    symlink.symlink_to(target)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(symlink))

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_production_gate_rejects_hardlinked_operator_attestation(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    os.link(attestation, attestation.with_name("operator-attestation-hardlink.json"))
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_production_gate_rejects_operator_attestation_outside_evidence_root(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(
        candidate_file, path=tmp_path / "outside-attestation.json",
    )
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_production_gate_rejects_writable_operator_attestation(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    attestation.chmod(0o664)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_production_gate_rejects_operator_attestation_beneath_writable_directory(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    attestation.parent.chmod(0o775)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


def test_operator_attestation_binding_requires_evidence_root_owned_by_effective_uid(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    effective_uid = os.geteuid()
    monkeypatch.setattr(gate.os, "geteuid", lambda: effective_uid + 1)

    binding = gate._operator_attestation_binding(
        candidate, {"COURSE_MODE_OPERATOR_ATTESTATION": str(attestation)},
    )

    assert binding is None


def test_production_gate_revalidates_operator_attestation_after_each_lane(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
    lanes = (_lane("one", "raise SystemExit(0)"), _lane("two", "raise SystemExit(0)"))
    monkeypatch.setattr(gate, "lanes_for_mode", lambda _mode: lanes)
    original_run = gate.run_bounded_command
    calls = 0

    def replace_after_first_lane(*args, **kwargs):
        nonlocal calls
        result = original_run(*args, **kwargs)
        calls += 1
        if calls == 1:
            replacement = attestation.with_name("operator-attestation-replacement.json")
            replacement.write_text(
                json.dumps({**_operator_attestation_payload(candidate_file), "createdAt": "2099-01-02T00:00:00Z"}),
                encoding="utf-8",
            )
            replacement.chmod(0o444)
            replacement.replace(attestation)
        return result

    monkeypatch.setattr(gate, "run_bounded_command", replace_after_first_lane)

    result = gate.run_gate(
        candidate_file, "quick", runtime_root=_runtime_root(candidate_file),
    )

    assert calls == 1
    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "operator-precondition"


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


def test_pytest_lane_stages_and_resolves_candidate_python_runtime(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane(
        name="python-runtime", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "--version"), timeout_sec=5.0,
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    try:
        staged = stage.candidate["tools"]["pythonTestRuntime"]
        assert staged["root"].startswith(str(stage.root))
        command = gate._resolve_candidate_command(lane.command, stage.candidate, lane)
        assert command == (
            str(Path(staged["root"]) / staged["executable"]),
            "-I", "-s", "-m", "pytest", "--version",
        )
        lane_execution = stage.create_lane_execution()
        try:
            lane_descriptor = lane_execution.candidate["tools"]["pythonTestRuntime"]
            observed, error = gate._manifest.secure_python_test_runtime_tree_descriptor(
                Path(lane_descriptor["root"]),
            )
            assert error is None
            assert observed == lane_descriptor["treeDigest"]
        finally:
            assert lane_execution.cleanup() is True
    finally:
        assert stage.cleanup() is True


def test_pytest_lane_runs_with_sanitized_writable_home(candidate_file: Path) -> None:
    lane = gate.Lane(
        name="python-runtime-home", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )

    report = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert report["verdict"] == "PASS"


def test_pytest_skip_report_is_written_inside_sandboxed_lane(candidate_file: Path) -> None:
    lane = gate.Lane(
        name="python-runtime-junit", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
        reject_pytest_skips=True,
    )

    report = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert report["verdict"] == "PASS"


def test_python_runtime_command_uses_immutable_stage_base_not_lane_copy(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane(
        name="python-stage-base", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )
    stage = gate.stage_execution_candidate(candidate, (lane,))
    lane_execution = stage.create_lane_execution()
    try:
        descriptor = lane_execution.candidate["tools"]["pythonTestRuntime"]
        assert descriptor["root"].startswith(str(stage.root))
        assert not descriptor["root"].startswith(str(lane_execution.root))
        assert not (lane_execution.root / "candidate/tools/python-test-runtime").exists()
    finally:
        assert lane_execution.cleanup() is True
        assert stage.cleanup() is True


def test_python_runtime_lane_fails_closed_without_macos_sandbox(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane(
        name="python-no-sandbox", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )
    stage = gate.stage_execution_candidate(candidate, (lane,))
    execution = stage.create_lane_execution()
    try:
        with monkeypatch.context() as platform_patch:
            platform_patch.setattr(gate.sys, "platform", "linux")
            assert gate._sandboxed_python_lane_command(
                ("/runtime/python", "-m", "pytest"), stage, execution,
            ) is None
    finally:
        assert execution.cleanup() is True
        assert stage.cleanup() is True


def test_python_runtime_lane_fails_closed_when_sandbox_executable_is_missing(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = gate.Lane(
        name="python-missing-sandbox", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )
    monkeypatch.setattr(
        gate._manifest, "TRUSTED_SANDBOX_EXECUTABLE", Path("/missing/sandbox-exec"),
    )

    report = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert report["verdict"] == "BLOCKED"
    assert report["failedLane"] == "candidate"
    assert report["lanes"] == []


def test_python_runtime_regression_detects_missing_process_sandbox(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = gate.Lane(
        name="python-sandbox-regression", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )
    monkeypatch.setattr(
        gate, "_sandboxed_python_lane_command",
        lambda command, _stage, _execution: command,
    )

    report = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert report["verdict"] == "FAIL"
    assert report["failedLane"] == lane.name


def test_python_runtime_authority_probe_regression_detects_missing_sandbox(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    descriptor = candidate["tools"]["pythonTestRuntime"]
    monkeypatch.setattr(
        gate._manifest, "_sandboxed_python_runtime_probe_command", lambda command: command,
    )

    assert gate._manifest.python_test_runtime_authorized(descriptor) is False

    observed, error = gate._manifest.secure_python_test_runtime_tree_descriptor(
        Path(descriptor["root"]),
    )
    assert error is None
    assert observed == descriptor["treeDigest"]


@pytest.mark.parametrize("attack", ["missing", "symlink", "oversize", "read-race"])
def test_backend_snapshot_authority_read_failure_is_bounded(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane(
        name="backend-authority-read", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )
    stage = gate.stage_execution_candidate(candidate, (lane,))
    authority = stage.root / ".course-mode-authority/backend.json"
    try:
        if attack == "read-race":
            original = gate.read_secure_regular
            monkeypatch.setattr(
                gate, "read_secure_regular",
                lambda path, limit: (_ for _ in ()).throw(OSError("raced"))
                if path == authority else original(path, limit),
            )
        else:
            authority.parent.chmod(0o755)
            authority.chmod(0o644)
            authority.unlink()
            if attack == "symlink":
                authority.symlink_to(stage.root / "repositories/backend")
            elif attack == "oversize":
                authority.write_bytes(b"x" * 4097)
                authority.chmod(0o444)
        assert gate._backend_snapshot_environment(stage) is None
    finally:
        assert stage.cleanup() is True


def test_backend_snapshot_authority_failure_blocks_lane_without_spawn(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lane = gate.Lane(
        name="backend-authority-blocked", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )
    monkeypatch.setattr(gate, "_backend_snapshot_environment", lambda _stage: None)

    report = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert report["verdict"] == "BLOCKED"
    assert report["failedLane"] == lane.name
    assert report["lanes"][0]["exitCode"] is None


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "fifo"])
def test_python_runtime_stage_rejects_tree_attack(candidate_file: Path, attack: str) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    descriptor = candidate["tools"]["pythonTestRuntime"]
    root = Path(descriptor["root"])
    executable = root / descriptor["executable"]
    root.chmod(0o755)
    executable.parent.chmod(0o755)
    executable.chmod(0o755)
    unsafe = root / "unsafe"
    if attack == "symlink":
        unsafe.symlink_to(executable)
    elif attack == "hardlink":
        os.link(executable, unsafe)
    else:
        os.mkfifo(unsafe)
    lane = gate.Lane(
        name="python-runtime-attack", repository="adminEsp", relative_cwd=".",
        command=("python3", "-m", "pytest", "-q"), timeout_sec=5.0,
    )

    with pytest.raises(ValueError, match="Python test runtime source descriptor mismatch"):
        gate.stage_execution_candidate(candidate, (lane,))


@pytest.mark.parametrize("attack", ["symlink", "hardlink"])
def test_strict_snapshot_copy_rejects_runtime_link_attack(tmp_path: Path, attack: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = source / "python3"
    payload.write_bytes(b"runtime")
    if attack == "symlink":
        (source / "unsafe").symlink_to(payload)
    else:
        os.link(payload, source / "unsafe")

    with pytest.raises(ValueError, match="link in strict snapshot"):
        gate._copy_snapshot_tree(
            source, tmp_path / "destination", {"entries": 0, "bytes": 0},
            reject_links=True,
        )


def test_strict_snapshot_copy_binds_destination_parent_fd_across_ancestor_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "python3.11").write_bytes(b"runtime")
    destination_parent = tmp_path / "destination-parent"
    destination_parent.mkdir()
    moved = tmp_path / "destination-parent-opened"
    original_mkdir = gate.os.mkdir
    swapped = False

    def swap_then_mkdir(path, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "runtime" and dir_fd is not None and not swapped:
            swapped = True
            destination_parent.rename(moved)
            destination_parent.mkdir()
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gate.os, "mkdir", swap_then_mkdir)

    gate._copy_strict_snapshot_tree_fd(
        source, destination_parent, "runtime", {"entries": 0, "bytes": 0},
    )

    assert swapped is True
    assert not (destination_parent / "runtime").exists()
    assert (moved / "runtime/python3.11").read_bytes() == b"runtime"


def test_non_pytest_python_command_does_not_use_candidate_test_runtime(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane(
        name="python-script", repository="adminEsp", relative_cwd=".",
        command=("python3", "script.py"), timeout_sec=5.0,
    )

    resolved = gate._resolve_candidate_command(lane.command, candidate, lane)

    assert resolved is not None
    assert resolved[0] != str(
        Path(candidate["tools"]["pythonTestRuntime"]["root"])
        / candidate["tools"]["pythonTestRuntime"]["executable"]
    )


@pytest.mark.parametrize("artifact", ["app", "elf", "node", "migration"])
def test_release_state_rechecks_external_artifact_identity(
    candidate_file: Path, artifact: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    if artifact == "app":
        Path(candidate["firmware"]["appPath"]).write_bytes(b"drift")
    elif artifact == "elf":
        evidence = json.loads(Path(candidate["firmware"]["evidenceManifestPath"]).read_text())
        (Path(candidate["firmware"]["evidenceManifestPath"]).parent / evidence["elf"]["file"]).write_bytes(b"drift")
    elif artifact == "node":
        Path(candidate["tools"]["node"]["backend"]["executable"]).write_text("#!/bin/sh\necho v0.0.0\n")
    else:
        root = Path(candidate["repositories"]["backend"]["path"])
        (root / "src/database/migrations" / candidate["database"]["migrationHead"]).write_text("SELECT 0;\n")

    assert gate.release_state_matches(candidate_file, candidate, (), None, False) is False


def test_node_lane_executes_candidate_descriptor_not_ambient_path(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    evidence_path = Path(candidate["firmware"]["evidenceManifestPath"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["sourceCommit"] = candidate["repositories"]["firmware"]["sha"]
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    candidate["firmware"]["evidenceManifestSha256"] = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    (hostile / "node").write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    (hostile / "node").chmod(0o755)
    monkeypatch.setattr(gate, "SECURE_PATH", str(hostile))
    lane = gate.Lane("candidate-node", "backend", ".", ("node", "--version"), 5.0)

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "PASS"


def test_replaced_candidate_npm_entrypoint_is_blocked_before_execution(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    entrypoint = Path(candidate["tools"]["node"]["backend"]["npm"]["entrypoint"])
    marker = entrypoint.parent / "must-not-execute"
    entrypoint.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    lane = gate.Lane("backend-npm", "backend", ".", ("npm", "test"), 5.0)

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert not marker.exists()


@pytest.mark.parametrize("tool", ["npm", "npx"])
def test_staged_package_manager_keeps_complete_descriptor_bound_package_tree(
    candidate_file: Path, tool: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    descriptor = candidate["tools"]["node"]["backend"]
    package_root = Path(descriptor["npm"]["entrypoint"]).parent.parent
    library = package_root / "lib/cli.py"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text("print('fixture-npm-1.0.0')\n", encoding="utf-8")
    for manager in ("npm", "npx"):
        entrypoint = Path(descriptor[manager]["entrypoint"])
        entrypoint.write_text(
            "from pathlib import Path\n"
            "exec((Path(__file__).parent.parent / 'lib/cli.py').read_text())\n",
            encoding="utf-8",
        )
        descriptor[manager]["sha256"] = hashlib.sha256(entrypoint.read_bytes()).hexdigest()
    package_tree = gate._manifest.secure_node_package_tree_descriptor(package_root)
    assert package_tree is not None
    descriptor["packageRoot"] = str(package_root)
    descriptor["packageRootMode"] = package_tree["rootMode"]
    descriptor["packageTreeSha256"] = package_tree["sha256"]

    stage = gate.stage_execution_candidate(
        candidate, (gate.Lane(f"backend-{tool}", "backend", ".", (tool, "--version"), 5.0),),
    )
    try:
        staged = stage.candidate["tools"]["node"]["backend"]
        assert str(package_root) not in json.dumps(staged)
        result = subprocess.run(
            [staged["executable"], staged[tool]["entrypoint"], "--version"],
            cwd=stage.candidate["repositories"]["backend"]["path"],
            env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
            text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "fixture-npm-1.0.0"
    finally:
        stage.cleanup()


def test_real_staged_npm_and_npx_run_without_original_tool_paths(candidate_file: Path) -> None:
    node_path = shutil.which("node")
    assert node_path is not None
    node = Path(node_path).resolve()
    package_root = node.parent.parent / "lib/node_modules/npm"
    assert package_root.is_dir()
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    package_tree = gate._manifest.secure_node_package_tree_descriptor(package_root)
    assert package_tree is not None
    version = subprocess.run(
        [node, "--version"], text=True, capture_output=True, check=True,
    ).stdout.strip()
    descriptor = {
        "version": version,
        "executable": str(node),
        "sha256": hashlib.sha256(node.read_bytes()).hexdigest(),
        "packageRoot": str(package_root),
        "packageRootMode": package_tree["rootMode"],
        "packageTreeSha256": package_tree["sha256"],
    }
    for tool in ("npm", "npx"):
        entrypoint = package_root / f"bin/{tool}-cli.js"
        descriptor[tool] = {
            "entrypoint": str(entrypoint),
            "sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest(),
        }
    candidate["tools"]["node"]["backend"] = descriptor
    lanes = tuple(
        gate.Lane(f"real-{tool}", "backend", ".", (tool, "--version"), 10.0)
        for tool in ("npm", "npx")
    )

    stage = gate.stage_execution_candidate(candidate, lanes)
    try:
        staged = stage.candidate["tools"]["node"]["backend"]
        serialized = json.dumps(staged)
        assert str(node) not in serialized
        assert str(package_root) not in serialized
        for tool in ("npm", "npx"):
            result = subprocess.run(
                [staged["executable"], staged[tool]["entrypoint"], "--version"],
                cwd=stage.candidate["repositories"]["backend"]["path"],
                env={"PATH": str(Path(staged["executable"]).parent) + ":/usr/bin:/bin"},
                text=True, capture_output=True, check=False,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip()
            assert str(package_root) not in result.stdout + result.stderr
    finally:
        stage.cleanup()


def test_lane_executes_private_snapshot_after_original_source_is_replaced(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    source = Path(candidate["repositories"]["adminEsp"]["path"]) / "snapshot-source.txt"
    source.write_text("original", encoding="utf-8")
    _git(source.parent, "add", source.name)
    _git(source.parent, "commit", "-m", "snapshot fixture")
    candidate["repositories"]["adminEsp"] = _repository(source.parent)
    _refresh_image_reference(candidate, "adminEsp")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    original_stage = gate.stage_execution_candidate

    def stage_then_replace(value: dict, lanes):
        staged = original_stage(value, lanes)
        source.write_text("mutated", encoding="utf-8")
        return staged

    monkeypatch.setattr(gate, "stage_execution_candidate", stage_then_replace)
    lane = gate.Lane(
        "snapshot-source", "adminEsp", ".",
        (sys.executable, "-c", "from pathlib import Path;assert Path('snapshot-source.txt').read_text()=='original'"),
        5.0,
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "PASS"


def test_snapshot_cleanup_runs_after_lane_failure(candidate_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed = []
    original_stage = gate.stage_execution_candidate

    def record_stage(candidate: dict, lanes):
        staged = original_stage(candidate, lanes)
        observed.append(staged.root)
        return staged

    monkeypatch.setattr(gate, "stage_execution_candidate", record_stage)
    result = gate.run_gate(candidate_file, "quick", lanes=(_lane("fail", "raise SystemExit(1)"),))

    assert result["verdict"] == "FAIL"
    assert observed and not observed[0].exists()


def test_assignment_runtime_capsule_is_private_and_identity_bound() -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    root = capsule.root
    metadata = root.stat()
    assert root.name.startswith("course-mode-assignment-runtime-")
    assert stat.S_IMODE(metadata.st_mode) == 0o700
    assert capsule.identity == (metadata.st_dev, metadata.st_ino)
    assert capsule.runtime_root == root / "runtime"
    runtime_metadata = capsule.runtime_root.stat()
    assert stat.S_IMODE(runtime_metadata.st_mode) == 0o700
    assert capsule.runtime_identity == (
        runtime_metadata.st_dev, runtime_metadata.st_ino,
    )
    assert capsule.runtime_descriptor is not None
    assert (capsule.runtime_root / "media").is_dir()
    assert (capsule.runtime_root / "tls").is_dir()
    assert capsule.usable() is True
    assert capsule.cleanup() is True
    assert not root.exists()
    assert capsule.cleanup() is True


def test_assignment_runtime_capsule_rejects_path_replacement(
    tmp_path: Path,
) -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    original_remove = gate._remove_owned_tree
    moved = capsule.root.with_name(capsule.root.name + "-moved")
    capsule.root.rename(moved)
    capsule.root.mkdir(mode=0o700)
    assert capsule.usable() is False
    assert capsule.cleanup() is False
    assert capsule.retained_path() == moved
    original_remove(moved, capsule.identity)
    capsule.root.rmdir()


def test_assignment_runtime_capsule_nested_runtime_replacement_reports_owner_root() -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    original_remove = gate._remove_owned_tree
    moved = capsule.runtime_root.with_name("runtime-moved")
    capsule.runtime_root.rename(moved)
    capsule.runtime_root.mkdir(mode=0o700)
    try:
        assert capsule.usable() is False
        assert capsule.cleanup() is False
        assert capsule.retained_path() == capsule.root
        assert moved.is_dir()
        assert capsule.runtime_root.is_dir()
    finally:
        original_remove(capsule.root, capsule.identity)


def test_assignment_runtime_capsule_reports_owner_and_escaped_runtime_inode() -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    original_remove = gate._remove_owned_tree
    escaped = capsule.root.with_name(capsule.root.name + "-runtime-moved")
    capsule.runtime_root.rename(escaped)
    capsule.runtime_root.mkdir(mode=0o700)
    report = {"candidateId": "test", "verdict": "PASS", "failedLane": None}
    try:
        assert gate._cleanup_gate_owned(report, capsule) is False
        assert report["verdict"] == "BLOCKED"
        assert report["failedLane"] == "cleanup"
        assert report["retainedPaths"] == sorted({
            str(capsule.root), str(escaped),
        })
        assert capsule.retained_path() == capsule.root
    finally:
        assert original_remove(capsule.root, capsule.identity) is True
        assert original_remove(escaped, capsule.runtime_identity) is True


def test_assignment_runtime_capsule_usable_rejects_swap_during_path_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    moved = capsule.root.with_name(capsule.root.name + "-moved")
    original_lookup = gate._directory_fd_path
    original_remove = gate._remove_owned_tree
    swapped = False

    def swap_during_lookup(descriptor: int) -> Path | None:
        nonlocal swapped
        actual = original_lookup(descriptor)
        if not swapped:
            swapped = True
            capsule.root.rename(moved)
            capsule.root.mkdir(mode=0o700)
        return actual

    monkeypatch.setattr(gate, "_directory_fd_path", swap_during_lookup)
    try:
        assert capsule.usable() is False
    finally:
        monkeypatch.setattr(gate, "_directory_fd_path", original_lookup)
        capsule.cleanup()
        original_remove(moved, capsule.identity)
        capsule.root.rmdir()


def test_assignment_runtime_capsule_cleanup_reports_moved_owned_inode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    moved = capsule.root.with_name(capsule.root.name + "-moved")
    original_remove = gate._remove_owned_tree

    def move_and_recreate(path: Path, _identity: tuple[int, int] | None) -> bool:
        path.rename(moved)
        path.mkdir(mode=0o700)
        return False

    monkeypatch.setattr(gate, "_remove_owned_tree", move_and_recreate)
    try:
        assert capsule.cleanup() is False
        assert capsule.retained_path() == moved
    finally:
        monkeypatch.setattr(gate, "_remove_owned_tree", original_remove)
        original_remove(moved, capsule.identity)
        capsule.root.rmdir()


def test_assignment_runtime_capsule_gc_closes_descriptor_and_removes_root() -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    root = capsule.root
    identity = capsule.identity
    descriptor = capsule.descriptor
    assert descriptor is not None

    del capsule
    gc.collect()

    try:
        assert not root.exists()
        with pytest.raises(OSError) as caught:
            os.fstat(descriptor)
        assert caught.value.errno == errno.EBADF
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        gate._remove_owned_tree(root, identity)


def test_assignment_runtime_capsule_rejects_canonical_protected_overlap() -> None:
    canonical_temporary_root = Path(tempfile.gettempdir()).resolve()
    temporary_root = Path("/var") / canonical_temporary_root.relative_to("/private/var")
    assert temporary_root != canonical_temporary_root
    assert temporary_root.resolve() == canonical_temporary_root
    before_roots = set(temporary_root.glob("course-mode-assignment-runtime-*"))
    unexpected: list[gate.AssignmentRuntimeCapsule] = []
    try:
        with pytest.raises(ValueError, match="assignment runtime overlaps protected path"):
            unexpected.append(gate.AssignmentRuntimeCapsule.create((temporary_root,)))
    finally:
        for capsule in unexpected:
            capsule.cleanup()

    assert set(temporary_root.glob("course-mode-assignment-runtime-*")) == before_roots


def test_assignment_runtime_capsule_post_open_failure_closes_exact_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots: list[Path] = []
    descriptors: list[int] = []
    original_mkdtemp = gate.tempfile.mkdtemp

    def record_mkdtemp(*args, **kwargs) -> str:
        root = Path(original_mkdtemp(*args, **kwargs))
        roots.append(root)
        return str(root)

    def fail_construction(
        self,
        root: Path,
        identity: tuple[int, int],
        descriptor: int,
        *_args,
        **_kwargs,
    ) -> None:
        descriptors.append(descriptor)
        raise OSError("forced capsule construction failure")

    monkeypatch.setattr(gate.tempfile, "mkdtemp", record_mkdtemp)
    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "__init__", fail_construction)

    with pytest.raises(OSError, match="forced capsule construction failure"):
        gate.AssignmentRuntimeCapsule.create(())

    assert len(descriptors) == 1
    with pytest.raises(OSError) as caught:
        os.fstat(descriptors[0])
    assert caught.value.errno == errno.EBADF
    assert roots and all(not root.exists() for root in roots)


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_assignment_runtime_capsule_create_cleans_up_after_base_exception(
    monkeypatch: pytest.MonkeyPatch, interrupt: type[BaseException],
) -> None:
    roots: list[Path] = []
    descriptors: list[int] = []
    original_mkdtemp = gate.tempfile.mkdtemp
    original_remove = gate._remove_owned_tree

    def record_mkdtemp(*args, **kwargs) -> str:
        root = Path(original_mkdtemp(*args, **kwargs))
        roots.append(root)
        return str(root)

    def interrupt_construction(
        self,
        _root: Path,
        _identity: tuple[int, int],
        descriptor: int,
        _runtime_root: Path,
        _runtime_identity: tuple[int, int],
        runtime_descriptor: int,
        *_args,
        **_kwargs,
    ) -> None:
        descriptors.extend((descriptor, runtime_descriptor))
        raise interrupt()

    monkeypatch.setattr(gate.tempfile, "mkdtemp", record_mkdtemp)
    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "__init__", interrupt_construction)
    try:
        with pytest.raises(interrupt):
            gate.AssignmentRuntimeCapsule.create(())

        assert roots and all(not root.exists() for root in roots)
        assert len(descriptors) == 2
        for descriptor in descriptors:
            with pytest.raises(OSError) as caught:
                os.fstat(descriptor)
            assert caught.value.errno == errno.EBADF
    finally:
        for descriptor in descriptors:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        for root in roots:
            original_remove(root)


def _stateful_assignment_lane(name: str, code: str) -> gate.Lane:
    return gate.Lane(
        name, "adminEsp", ".", (sys.executable, "-c", code), 5.0,
        gate.TASK4_ASSIGNMENT_CANDIDATE_ENV,
    )


def _record_assignment_capsules(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    roots: list[Path] = []
    original = gate.AssignmentRuntimeCapsule.create

    def create(protected):
        capsule = original(protected)
        roots.append(capsule.root)
        return capsule

    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "create", create)
    return roots


def _authorize_assignment_test_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gate, "release_state_matches", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(gate, "assignment_input_sources_ready", lambda _candidate: True)
    monkeypatch.setattr(gate, "_container_tools_authorized", lambda _candidate: True)
    monkeypatch.setattr(gate, "playwright_browsers_authorized", lambda _candidate: True)
    monkeypatch.setattr(gate, "_backend_compiler_required", lambda _lane: False)


def test_assignment_lanes_share_capsule_then_remove_it(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _assignment_source(candidate_file)
    observed: list[Path] = []
    commands = {
        "admin-course-mode-assignment-new": (
            sys.executable, "-c",
            "import os;from pathlib import Path;"
            "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
            "(p/'media').mkdir(parents=True,exist_ok=True);"
            "(p/'media'/'handoff.bin').write_bytes(b'new')",
        ),
        "admin-course-mode-assignment-rollback": (
            sys.executable, "-c",
            "import os;from pathlib import Path;"
            "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
            "assert (p/'media'/'handoff.bin').read_bytes()==b'new'",
        ),
    }
    lanes = tuple(
        gate.Lane(
            name, "adminEsp", ".", commands[name], 5.0,
            gate.TASK4_ASSIGNMENT_CANDIDATE_ENV,
        )
        for name in commands
    )
    original_run = gate.run_bounded_command

    def record_runtime(command, **kwargs):
        observed.append(Path(kwargs["env"]["TASK4_ASSIGNMENT_RUNTIME_ROOT"]))
        return original_run(command, **kwargs)

    monkeypatch.setattr(gate, "run_bounded_command", record_runtime)
    _authorize_assignment_test_lane(monkeypatch)

    result = gate.run_gate(
        candidate_file, "full", lanes=lanes, source_environment=source,
    )

    assert result["verdict"] == "PASS", result
    assert len(observed) == 2 and observed[0] == observed[1]
    assert observed[0].name == "runtime"
    assert observed[0].parent.name.startswith("course-mode-assignment-runtime-")
    assert not observed[0].exists()


def test_assignment_runner_accepts_gate_owned_capsule(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    admin_root = Path(candidate["repositories"]["adminEsp"]["path"])
    scripts = admin_root / "main/manager-web/scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    canonical_root = Path(__file__).resolve().parents[3]
    node_path = shutil.which("node")
    assert node_path is not None
    node = Path(node_path).resolve()
    helper_source = (
        canonical_root / "main/manager-web/scripts/task4-assignment-runtime.cjs"
    ).read_text(encoding="utf-8")
    instrumented_helper = helper_source.replace(
        "  return Object.freeze({ capsuleRoot: realOwner, runtimeRoot: realRuntime });",
        "  require('node:fs').writeFileSync(resolve(realRuntime, 'validated.marker'), "
        "`${realOwner}\\n${realRuntime}\\n${__filename}`);\n"
        "  return Object.freeze({ capsuleRoot: realOwner, runtimeRoot: realRuntime });",
    )
    assert instrumented_helper != helper_source
    assert "return Object.freeze({ capsuleRoot: realOwner, runtimeRoot: realRuntime });" in instrumented_helper
    forbidden_docker_marker = tmp_path / "docker-was-invoked"
    (scripts / "task4-assignment-runtime.cjs").write_text(
        instrumented_helper, encoding="utf-8",
    )
    (scripts / "run-task4-assignment-phase.cjs").write_text(
        (canonical_root / "main/manager-web/scripts/run-task4-assignment-phase.cjs").read_text(
            encoding="utf-8",
        ),
        encoding="utf-8",
    )
    (scripts / "prepare-task4-media-templates.cjs").write_text(
        (canonical_root / "main/manager-web/scripts/prepare-task4-media-templates.cjs").read_text(
            encoding="utf-8",
        ),
        encoding="utf-8",
    )
    (scripts / "task4-image-identity.cjs").write_text(
        "const { statSync, writeFileSync } = require('node:fs');\n"
        "const { resolve } = require('node:path');\n"
        "module.exports = {\n"
        "  inspectAndPinCandidateImages() {\n"
        "    const runtimeRoot = process.env.TASK4_ASSIGNMENT_RUNTIME_ROOT;\n"
        "    const mediaRoot = resolve(runtimeRoot, 'media');\n"
        "    const tlsRoot = resolve(runtimeRoot, 'tls');\n"
        "    if (!statSync(mediaRoot).isDirectory() || !statSync(tlsRoot).isDirectory()) {\n"
        "      throw new Error('runner did not create capsule media and TLS roots');\n"
        "    }\n"
        "    for (const durationMs of [600, 1100, 1200, 1300, 1400, 1600, 2600, 3000, 9500]) {\n"
        "      if (!statSync(resolve(mediaRoot, 'templates', `${durationMs}.mp4`)).isFile()) {\n"
        "        throw new Error(`missing prepared template ${durationMs}`);\n"
        "      }\n"
        "    }\n"
        "    writeFileSync(resolve(runtimeRoot, 'roots.marker'), "
        "`${mediaRoot}\\n${tlsRoot}`);\n"
        "    process.exit(0);\n"
        "  },\n"
        "  verifyStartedServiceImages() {},\n"
        "};\n",
        encoding="utf-8",
    )
    media_tools = scripts / "media-tools"
    media_tools.mkdir()
    (media_tools / "ffmpeg").write_text(
        f"#!{node}\n"
        "require('node:fs').writeFileSync(process.argv.at(-1), 'stub media');\n",
        encoding="utf-8",
    )
    (media_tools / "ffprobe").write_text(
        f"#!{node}\n"
        "const { basename } = require('node:path');\n"
        "const durationMs = Number(basename(process.argv.at(-1), '.mp4'));\n"
        "process.stdout.write(JSON.stringify({ streams: [{ codec_name: 'h264', width: 480, "
        "height: 320, r_frame_rate: '10/1', nb_frames: durationMs / 100 }], "
        "format: { duration: durationMs / 1000 } }));\n",
        encoding="utf-8",
    )
    (media_tools / "openssl").write_text(
        f"#!{node}\n"
        "const { writeFileSync } = require('node:fs');\n"
        "const key = process.argv.indexOf('-keyout');\n"
        "const cert = process.argv.indexOf('-out');\n"
        "if (key < 0 || cert < 0) process.exit(96);\n"
        "writeFileSync(process.argv[key + 1], 'stub key');\n"
        "writeFileSync(process.argv[cert + 1], 'stub cert');\n",
        encoding="utf-8",
    )
    for tool in (media_tools / "ffmpeg", media_tools / "ffprobe", media_tools / "openssl"):
        tool.chmod(0o755)
    (scripts / "reset-lesson-studio-e2e-state.cjs").write_text(
        "module.exports = { composeExecutableFromEnvironment(environment) { "
        "return environment.TBOT_DOCKER_COMPOSE_EXECUTABLE; } };\n",
        encoding="utf-8",
    )
    web_root = admin_root / "main/manager-web"
    (web_root / "package.json").write_text(json.dumps({"scripts": {
        "test:e2e:course-mode:assignment:new":
            "node scripts/run-task4-assignment-phase.cjs new",
    }}), encoding="utf-8")
    _git(admin_root, "add", "main/manager-web/scripts")
    _git(admin_root, "add", "main/manager-web/package.json")
    _git(admin_root, "commit", "-m", "add assignment capsule probe")
    candidate["repositories"]["adminEsp"].update(_repository(admin_root))
    _refresh_image_reference(candidate, "adminEsp")
    backend_root = Path(candidate["repositories"]["backend"]["path"])
    backend_output = backend_root / "dist/lessons/course-mode/curriculum-course-mode.js"
    backend_output.parent.mkdir(parents=True, exist_ok=True)
    backend_output.write_text("module.exports = {};\n", encoding="utf-8")
    _git(backend_root, "add", str(backend_output.relative_to(backend_root)))
    _git(backend_root, "commit", "-m", "add assignment runner build output")
    candidate["repositories"]["backend"].update(_repository(backend_root))
    _refresh_image_reference(candidate, "backend")
    firmware_root = Path(candidate["repositories"]["firmware"]["path"])
    for relative in (
        "lesson/assets/background/barn-round-field-poster.jpg",
        "lesson/assets/robot/poses/bright-teach.png",
        "lesson/assets/robot/poses/bright-listening.png",
        "lesson/assets/robot/poses/bright-celebrate.png",
    ):
        asset = firmware_root / relative
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"candidate asset\n")
    _git(firmware_root, "add", "lesson/assets")
    _git(firmware_root, "commit", "-m", "add assignment runner assets")
    candidate["repositories"]["firmware"].update(_repository(firmware_root))
    evidence_path = Path(candidate["firmware"]["evidenceManifestPath"])
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["sourceCommit"] = candidate["repositories"]["firmware"]["sha"]
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    candidate["firmware"]["evidenceManifestSha256"] = hashlib.sha256(
        evidence_path.read_bytes(),
    ).hexdigest()
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    for tool_name in ("docker", "dockerCompose"):
        descriptor = candidate["tools"][tool_name]
        executable = Path(descriptor["path"])
        lines = executable.read_text(encoding="utf-8").splitlines(keepends=True)
        lines.insert(
            1,
            "import os,pathlib,sys\n"
            "if os.environ.get('TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT'):\n"
            f" pathlib.Path({str(forbidden_docker_marker)!r}).write_text({tool_name!r})\n"
            " sys.exit(97)\n",
        )
        executable.write_text("".join(lines), encoding="utf-8")
        executable.chmod(0o755)
        descriptor["sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
    node_descriptor = candidate["tools"]["node"]["adminManagerWeb"]
    candidate_node = Path(node_descriptor["executable"])
    candidate_node.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = --version ]; then echo v20.20.2; else "
        f"exec {node} \"$@\"; fi\n",
        encoding="utf-8",
    )
    candidate_node.chmod(0o755)
    npm_entrypoint = Path(node_descriptor["npm"]["entrypoint"])
    npm_entrypoint.write_text(
        "const assert = require('node:assert/strict');\n"
        "const { resolve } = require('node:path');\n"
        "assert.deepEqual(process.argv.slice(2), "
        "['run', 'test:e2e:course-mode:assignment:new']);\n"
        "process.env.PATH = `${resolve(process.cwd(), 'scripts/media-tools')}:${process.env.PATH}`;\n"
        "process.argv = [process.argv[0], "
        "resolve(process.cwd(), 'scripts/run-task4-assignment-phase.cjs'), 'new'];\n"
        "require(resolve(process.cwd(), 'scripts/run-task4-assignment-phase.cjs'));\n",
        encoding="utf-8",
    )
    node_descriptor["npm"]["sha256"] = hashlib.sha256(npm_entrypoint.read_bytes()).hexdigest()
    package_tree = gate._manifest.secure_node_package_tree_descriptor(
        Path(node_descriptor["packageRoot"]),
    )
    assert package_tree is not None
    node_descriptor.update({
        "version": "v20.20.2",
        "executable": str(candidate_node),
        "sha256": hashlib.sha256(candidate_node.read_bytes()).hexdigest(),
        "packageRootMode": package_tree["rootMode"],
        "packageTreeSha256": package_tree["sha256"],
    })
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")

    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-assignment-new"
    )
    observed: list[tuple[Path, Path, list[str], list[str]]] = []
    original_run = gate.run_bounded_command

    def observe_marker(command, **kwargs):
        result = original_run(command, **kwargs)
        capsule_root = Path(kwargs["env"]["TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT"])
        runtime_root = Path(kwargs["env"]["TASK4_ASSIGNMENT_RUNTIME_ROOT"])
        marker = runtime_root / "validated.marker"
        roots_marker = runtime_root / "roots.marker"
        observed.append((
            capsule_root, runtime_root, marker.read_text(encoding="utf-8").splitlines(),
            roots_marker.read_text(encoding="utf-8").splitlines(),
        ))
        return result

    monkeypatch.setattr(gate, "run_bounded_command", observe_marker)
    _authorize_assignment_test_lane(monkeypatch)
    monkeypatch.setattr(gate, "source_contract_ready", lambda *_args: True)

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,),
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "PASS", result
    assert len(observed) == 1
    capsule_root, runtime_root, marker, roots = observed[0]
    assert runtime_root == capsule_root / "runtime"
    assert marker[:2] == [str(capsule_root), str(runtime_root)]
    assert marker[2].endswith("/main/manager-web/scripts/task4-assignment-runtime.cjs")
    assert marker[2].startswith(str(admin_root.parent)) is False
    assert roots == [str(runtime_root / "media"), str(runtime_root / "tls")]
    assert not forbidden_docker_marker.exists()
    assert not capsule_root.exists()


def test_assignment_capsule_rename_blocks_rollback_and_reports_owner(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    capsules: list[gate.AssignmentRuntimeCapsule] = []
    original_create = gate.AssignmentRuntimeCapsule.create
    original_remove = gate._remove_owned_tree
    rollback_marker = tmp_path / "rollback-ran"

    def record_create(protected):
        capsule = original_create(protected)
        capsules.append(capsule)
        return capsule

    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "create", record_create)
    lanes = (
        _stateful_assignment_lane(
            "admin-course-mode-assignment-new",
            "import os;from pathlib import Path;"
            "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
            "p.rename(p.with_name(p.name+'-moved'));p.mkdir(mode=0o700)",
        ),
        _stateful_assignment_lane(
            "admin-course-mode-assignment-rollback",
            f"from pathlib import Path;Path({str(rollback_marker)!r}).touch()",
        ),
    )
    try:
        result = gate.run_gate(
            candidate_file, "full", lanes=lanes,
            source_environment=_assignment_source(candidate_file),
        )

        capsule = capsules[0]
        moved_runtime = capsule.runtime_root.with_name(
            capsule.runtime_root.name + "-moved"
        )
        assert result["verdict"] == "BLOCKED"
        assert result["failedLane"] == "cleanup"
        assert result["retainedOwner"] == "current-process"
        assert result["retainedPaths"] == [str(capsule.root)]
        assert moved_runtime.is_dir()
        assert capsule.runtime_root.is_dir()
        assert not rollback_marker.exists()
    finally:
        if capsules:
            original_remove(capsules[0].root, capsules[0].identity)


def test_assignment_capsule_is_cleaned_after_new_failure(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _record_assignment_capsules(monkeypatch)
    _authorize_assignment_test_lane(monkeypatch)
    lane = _stateful_assignment_lane(
        "admin-course-mode-assignment-new", "raise SystemExit(7)",
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,),
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "FAIL"
    assert roots and not roots[0].exists()


def test_assignment_capsule_is_cleaned_after_rollback_failure(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _record_assignment_capsules(monkeypatch)
    _authorize_assignment_test_lane(monkeypatch)
    lanes = (
        _stateful_assignment_lane("admin-course-mode-assignment-new", "pass"),
        _stateful_assignment_lane(
            "admin-course-mode-assignment-rollback", "raise SystemExit(8)",
        ),
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=lanes,
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "FAIL"
    assert roots and not roots[0].exists()


def test_assignment_capsule_single_selected_lane_does_not_leak(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _record_assignment_capsules(monkeypatch)
    _authorize_assignment_test_lane(monkeypatch)
    lane = _stateful_assignment_lane("admin-course-mode-assignment-new", "pass")

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,),
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "PASS"
    assert roots and not roots[0].exists()


def test_assignment_capsule_is_never_sent_to_non_assignment_lane(
    candidate_file: Path, tmp_path: Path,
) -> None:
    marker = tmp_path / "assignment-env-present"
    lane = _lane(
        "ordinary", "import os;from pathlib import Path;"
        f"Path({str(marker)!r}).touch() if "
        "'TASK4_ASSIGNMENT_RUNTIME_ROOT' in os.environ else None",
    )

    result = gate.run_gate(
        candidate_file, "quick", lanes=(lane,),
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "PASS"
    assert not marker.exists()


def test_assignment_capsule_is_cleaned_after_snapshot_failure(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = _record_assignment_capsules(monkeypatch)
    _authorize_assignment_test_lane(monkeypatch)
    original_stage = gate.stage_execution_candidate
    calls = 0

    def fail_rollback_snapshot(candidate: dict, lanes):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("rollback snapshot")
        return original_stage(candidate, lanes)

    monkeypatch.setattr(gate, "stage_execution_candidate", fail_rollback_snapshot)
    lanes = (
        _stateful_assignment_lane("admin-course-mode-assignment-new", "pass"),
        _stateful_assignment_lane("admin-course-mode-assignment-rollback", "pass"),
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=lanes,
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "snapshot"
    assert roots and not roots[0].exists()


def test_assignment_lane_background_process_cannot_race_next_lane(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    new_code = (
        "import os,subprocess,sys,time;from pathlib import Path;"
        "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
        "child=\"import os,signal,time;from pathlib import Path;"
        "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "(p/'descendant-ready').touch();time.sleep(.5);"
        "(p/'descendant-raced').touch();time.sleep(10)\";"
        "subprocess.Popen([sys.executable,'-c',child],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        "deadline=time.monotonic()+2;"
        "exec(\"while not (p/'descendant-ready').exists():\\n"
        " assert time.monotonic()<deadline\\n time.sleep(.01)\");"
        "(p/'leader-finished').touch()"
    )
    rollback_code = (
        "import os,time;from pathlib import Path;"
        "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
        "assert (p/'leader-finished').is_file();time.sleep(.7);"
        "assert not (p/'descendant-raced').exists()"
    )
    lanes = (
        _stateful_assignment_lane("admin-course-mode-assignment-new", new_code),
        _stateful_assignment_lane("admin-course-mode-assignment-rollback", rollback_code),
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=lanes,
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "PASS", result


def test_assignment_capsule_cleanup_failure_overrides_lane_failure(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    capsules: list[gate.AssignmentRuntimeCapsule] = []
    original_create = gate.AssignmentRuntimeCapsule.create
    original_remove = gate._remove_owned_tree

    def record_create(protected):
        capsule = original_create(protected)
        capsules.append(capsule)
        return capsule

    def fail_capsule_cleanup(path: Path, identity=None) -> bool:
        if path.name.startswith("course-mode-assignment-runtime-"):
            return False
        return original_remove(path, identity)

    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "create", record_create)
    monkeypatch.setattr(gate, "_remove_owned_tree", fail_capsule_cleanup)
    lane = _stateful_assignment_lane(
        "admin-course-mode-assignment-new", "raise SystemExit(7)",
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,),
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "cleanup"
    assert result["retainedOwner"] == "current-process"
    assert result["retainedPaths"] == [str(capsules[0].root)]
    monkeypatch.setattr(gate, "_remove_owned_tree", original_remove)
    original_remove(capsules[0].root, capsules[0].identity)


def test_assignment_cleanup_preserves_lane_and_capsule_retained_paths(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    capsules: list[gate.AssignmentRuntimeCapsule] = []
    lane_executions: list[gate.LaneExecution] = []
    original_capsule_create = gate.AssignmentRuntimeCapsule.create
    original_lane_create = gate.ExecutionStage.create_lane_execution
    original_remove = gate._remove_owned_tree

    def record_capsule(protected):
        capsule = original_capsule_create(protected)
        capsules.append(capsule)
        return capsule

    def record_lane(self):
        execution = original_lane_create(self)
        lane_executions.append(execution)
        return execution

    def fail_lane_and_capsule(path: Path, identity=None) -> bool:
        if (
            path.name.startswith("course-mode-lane-")
            or path.name.startswith("course-mode-assignment-runtime-")
        ):
            return False
        return original_remove(path, identity)

    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "create", record_capsule)
    monkeypatch.setattr(gate.ExecutionStage, "create_lane_execution", record_lane)
    monkeypatch.setattr(gate, "_remove_owned_tree", fail_lane_and_capsule)
    lane = _stateful_assignment_lane("admin-course-mode-assignment-new", "pass")

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,),
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "cleanup"
    assert result["retainedPaths"] == sorted([
        str(capsules[0].root), str(lane_executions[0].root),
    ])
    monkeypatch.setattr(gate, "_remove_owned_tree", original_remove)
    original_remove(lane_executions[0].root, lane_executions[0].identity)
    original_remove(capsules[0].root, capsules[0].identity)


def test_assignment_interrupt_reports_lane_owner_and_escaped_runtime_paths(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    capsules: list[gate.AssignmentRuntimeCapsule] = []
    lane_executions: list[gate.LaneExecution] = []
    original_capsule_create = gate.AssignmentRuntimeCapsule.create
    original_lane_create = gate.ExecutionStage.create_lane_execution
    original_remove = gate._remove_owned_tree
    escaped_runtime: Path | None = None

    def record_capsule(protected):
        capsule = original_capsule_create(protected)
        capsules.append(capsule)
        return capsule

    def record_lane(self):
        execution = original_lane_create(self)
        lane_executions.append(execution)
        return execution

    def retain_lane(path: Path, identity=None) -> bool:
        if path.name.startswith("course-mode-lane-"):
            return False
        return original_remove(path, identity)

    def escape_runtime_then_interrupt(*_args, **_kwargs):
        nonlocal escaped_runtime
        capsule = capsules[0]
        escaped_runtime = capsule.root.with_name(capsule.root.name + "-escaped-runtime")
        capsule.runtime_root.rename(escaped_runtime)
        capsule.runtime_root.mkdir(mode=0o700)
        raise KeyboardInterrupt("lane interrupt")

    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "create", record_capsule)
    monkeypatch.setattr(gate.ExecutionStage, "create_lane_execution", record_lane)
    monkeypatch.setattr(gate, "_remove_owned_tree", retain_lane)
    monkeypatch.setattr(gate, "run_bounded_command", escape_runtime_then_interrupt)
    lane = _stateful_assignment_lane("admin-course-mode-assignment-new", "pass")
    try:
        result = gate.run_gate(
            candidate_file, "full", lanes=(lane,),
            source_environment=_assignment_source(candidate_file),
        )

        assert result["verdict"] == "BLOCKED"
        assert result["failedLane"] == "cleanup"
        assert escaped_runtime is not None
        assert result["retainedPaths"] == sorted({
            str(lane_executions[0].root),
            str(capsules[0].root),
            str(escaped_runtime),
        })
    finally:
        monkeypatch.setattr(gate, "_remove_owned_tree", original_remove)
        if lane_executions:
            original_remove(lane_executions[0].root, lane_executions[0].identity)
        if capsules:
            original_remove(capsules[0].root, capsules[0].identity)
        if escaped_runtime is not None:
            original_remove(escaped_runtime, capsules[0].runtime_identity)


@pytest.mark.parametrize("failure_point", ["snapshot", "environment", "command"])
def test_assignment_capsule_finally_cleans_after_interrupt(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    capsules: list[gate.AssignmentRuntimeCapsule] = []
    original_create = gate.AssignmentRuntimeCapsule.create

    def record_capsule(protected):
        capsule = original_create(protected)
        capsules.append(capsule)
        return capsule

    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "create", record_capsule)
    if failure_point == "snapshot":
        monkeypatch.setattr(
            gate, "stage_execution_candidate",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
    elif failure_point == "environment":
        monkeypatch.setattr(
            gate, "_child_environment",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
    else:
        monkeypatch.setattr(
            gate, "_resolve_candidate_command",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
    lane = _stateful_assignment_lane("admin-course-mode-assignment-new", "pass")

    with pytest.raises(KeyboardInterrupt):
        gate.run_gate(
            candidate_file, "full", lanes=(lane,),
            source_environment=_assignment_source(candidate_file),
        )

    assert capsules and not capsules[0].root.exists()


def test_assignment_capsule_cannot_be_redirected_by_mutable_source(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    valid_source = _assignment_source(candidate_file)
    attacker_runtime = tmp_path / "attacker-runtime"

    class MutableAssignmentSource(dict[str, str]):
        runtime_reads = 0

        def get(self, key, default=None):
            if key == "TASK4_ASSIGNMENT_RUNTIME_ROOT":
                self.runtime_reads += 1
                return (
                    valid_source[key]
                    if self.runtime_reads == 1
                    else str(attacker_runtime)
                )
            return super().get(key, default)

    source = MutableAssignmentSource(valid_source)
    observed: list[Path] = []
    original_run = gate.run_bounded_command

    def record_runtime(command, **kwargs):
        observed.append(Path(kwargs["env"]["TASK4_ASSIGNMENT_RUNTIME_ROOT"]))
        return original_run(command, **kwargs)

    monkeypatch.setattr(gate, "run_bounded_command", record_runtime)
    lanes = (
        _stateful_assignment_lane("admin-course-mode-assignment-new", "pass"),
        _stateful_assignment_lane("admin-course-mode-assignment-rollback", "pass"),
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=lanes, source_environment=source,
    )

    assert result["verdict"] == "PASS", result
    assert source.runtime_reads == 1
    assert len(observed) == 2 and observed[0] == observed[1]
    assert not attacker_runtime.exists()


def test_assignment_lane_blocks_when_process_containment_cannot_be_proven(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _authorize_assignment_test_lane(monkeypatch)
    monkeypatch.setattr(
        gate,
        "run_assignment_bounded_command",
        lambda *_args, **_kwargs: gate._manifest.BoundedCommandResult(
            0, "", "containment",
        ),
    )
    lane = _stateful_assignment_lane("admin-course-mode-assignment-new", "pass")

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,),
        source_environment=_assignment_source(candidate_file),
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == lane.name


def _wait_for_pid_file(path: Path) -> int:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            return int(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            time.sleep(0.01)
    raise AssertionError(f"process did not publish PID: {path}")


def _pid_is_absent(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


@pytest.mark.parametrize("failure_source", ["kqueue", "selector"])
def test_assignment_runner_reaps_group_and_closes_fds_after_runtime_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_source: str,
) -> None:
    child_pid_file = tmp_path / "child.pid"
    processes: list[subprocess.Popen] = []
    descriptors: list[int] = []
    original_popen = gate.subprocess.Popen
    original_kqueue = gate.select.kqueue
    original_selector = gate.selectors.DefaultSelector

    class RaisingKqueue:
        def __init__(self) -> None:
            self.inner = original_kqueue()
            descriptors.append(self.inner.fileno())
            self.calls = 0

        def control(self, *args):
            self.calls += 1
            if self.calls > 1:
                _wait_for_pid_file(child_pid_file)
                raise RuntimeError("kqueue runtime failure")
            return self.inner.control(*args)

        def close(self) -> None:
            self.inner.close()

    class RaisingSelector:
        def __init__(self) -> None:
            self.inner = original_selector()
            backend = getattr(self.inner, "_selector", None)
            if backend is not None:
                descriptors.append(backend.fileno())

        def register(self, *args, **kwargs):
            return self.inner.register(*args, **kwargs)

        def select(self, *_args, **_kwargs):
            _wait_for_pid_file(child_pid_file)
            raise RuntimeError("selector runtime failure")

        def close(self) -> None:
            self.inner.close()

    def record_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        assert process.stdout is not None and process.stderr is not None
        descriptors.extend((process.stdout.fileno(), process.stderr.fileno()))
        return process

    monkeypatch.setattr(gate.subprocess, "Popen", record_popen)
    if failure_source == "kqueue":
        stable_selector = RaisingSelector()
        stable_selector.select = stable_selector.inner.select
        monkeypatch.setattr(gate.selectors, "DefaultSelector", lambda: stable_selector)
        monkeypatch.setattr(gate.select, "kqueue", RaisingKqueue)
    else:
        monkeypatch.setattr(gate.selectors, "DefaultSelector", RaisingSelector)
    child_code = "import time;time.sleep(10)"
    leader_code = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"Path({str(child_pid_file)!r}).write_text(str(p.pid));time.sleep(10)"
    )
    child_pid = None
    try:
        result = gate.run_assignment_bounded_command(
            [sys.executable, "-c", leader_code], cwd=tmp_path,
            timeout_sec=5, max_output_bytes=1024,
        )
        child_pid = _wait_for_pid_file(child_pid_file)
        assert result.error == "containment"
        assert processes[0].returncode is not None
        assert _pid_is_absent(child_pid)
        for descriptor in descriptors:
            with pytest.raises(OSError) as caught:
                os.fstat(descriptor)
            assert caught.value.errno == errno.EBADF
    finally:
        if processes and processes[0].returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(processes[0].pid, signal.SIGKILL)
            processes[0].wait()
        if child_pid is None and child_pid_file.exists():
            child_pid = _wait_for_pid_file(child_pid_file)
        if child_pid is not None and not _pid_is_absent(child_pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


@pytest.mark.parametrize("failure_source", ["terminate", "absence"])
def test_assignment_runner_reraises_teardown_interrupt_after_group_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_source: str,
) -> None:
    child_pid_file = tmp_path / f"{failure_source}-child.pid"
    processes: list[subprocess.Popen] = []
    original_popen = gate.subprocess.Popen
    original_terminate = gate._terminate_assignment_process_group
    original_absence = gate._assignment_process_group_absent_after_reap

    class TeardownInterrupt(KeyboardInterrupt):
        pass

    def record_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def interrupt_terminate(process_group: int) -> bool:
        _wait_for_pid_file(child_pid_file)
        raise TeardownInterrupt("terminate interrupt")

    def interrupt_absence(process_group: int) -> bool:
        assert original_absence(process_group) is True
        raise TeardownInterrupt("absence interrupt")

    monkeypatch.setattr(gate.subprocess, "Popen", record_popen)
    monkeypatch.setattr(
        gate,
        "_terminate_assignment_process_group",
        interrupt_terminate if failure_source == "terminate" else original_terminate,
    )
    monkeypatch.setattr(
        gate,
        "_assignment_process_group_absent_after_reap",
        interrupt_absence if failure_source == "absence" else original_absence,
    )
    child_code = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)"
    leader_code = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"Path({str(child_pid_file)!r}).write_text(str(p.pid));time.sleep(.1)"
    )
    child_pid = None
    try:
        with pytest.raises(TeardownInterrupt, match=failure_source):
            gate.run_assignment_bounded_command(
                [sys.executable, "-c", leader_code], cwd=tmp_path,
                timeout_sec=5, max_output_bytes=1024,
            )

        child_pid = _wait_for_pid_file(child_pid_file)
        assert processes[0].returncode is not None
        assert _pid_is_absent(child_pid)
        with pytest.raises(ProcessLookupError):
            os.killpg(processes[0].pid, 0)
    finally:
        if processes and processes[0].returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(processes[0].pid, signal.SIGKILL)
            processes[0].wait()
        if child_pid is None and child_pid_file.exists():
            child_pid = _wait_for_pid_file(child_pid_file)
        if child_pid is not None and not _pid_is_absent(child_pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)


def test_assignment_runner_closes_partial_resources_when_kqueue_init_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class PartialSelector:
        def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(gate.selectors, "DefaultSelector", PartialSelector)
    monkeypatch.setattr(
        gate.select, "kqueue", lambda: (_ for _ in ()).throw(RuntimeError("kqueue init")),
    )

    with pytest.raises(RuntimeError, match="kqueue init"):
        gate.run_assignment_bounded_command(
            [sys.executable, "-c", "pass"], cwd=Path.cwd(),
            timeout_sec=5, max_output_bytes=1024,
        )

    assert closed is True


def test_assignment_runner_closes_resources_when_popen_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptors: list[int] = []
    original_selector = gate.selectors.DefaultSelector
    original_kqueue = gate.select.kqueue

    def selector_factory():
        selector = original_selector()
        descriptors.append(selector._selector.fileno())
        return selector

    def kqueue_factory():
        events = original_kqueue()
        descriptors.append(events.fileno())
        return events

    monkeypatch.setattr(gate.selectors, "DefaultSelector", selector_factory)
    monkeypatch.setattr(gate.select, "kqueue", kqueue_factory)
    monkeypatch.setattr(
        gate.subprocess, "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("popen runtime")),
    )

    with pytest.raises(RuntimeError, match="popen runtime"):
        gate.run_assignment_bounded_command(
            [sys.executable, "-c", "pass"], cwd=Path.cwd(),
            timeout_sec=5, max_output_bytes=1024,
        )

    for descriptor in descriptors:
        with pytest.raises(OSError) as caught:
            os.fstat(descriptor)
        assert caught.value.errno == errno.EBADF


def test_assignment_post_reap_eperm_is_not_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate.os, "killpg",
        lambda *_args: (_ for _ in ()).throw(PermissionError(errno.EPERM, "surviving group")),
    )
    monotonic = iter((0.0, 2.0))
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(monotonic))

    assert gate._assignment_process_group_absent_after_reap(12345) is False


def test_assignment_runner_blocks_when_post_reap_absence_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gate, "_assignment_process_group_absent_after_reap", lambda _group: False,
    )

    result = gate.run_assignment_bounded_command(
        [sys.executable, "-c", "pass"], cwd=tmp_path,
        timeout_sec=5, max_output_bytes=1024,
    )

    assert result.returncode == 0
    assert result.error == "containment"


@pytest.mark.parametrize(
    ("leader_action", "expected_error"),
    [
        ("time.sleep(10)", "timeout"),
        ("print('x'*4096)", "output"),
    ],
)
def test_assignment_runner_failure_paths_leave_no_process_group(
    tmp_path: Path, leader_action: str, expected_error: str,
) -> None:
    child_pid_file = tmp_path / f"{expected_error}-child.pid"
    child_code = "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(10)"
    leader_code = (
        "import subprocess,sys,time;from pathlib import Path;"
        f"p=subprocess.Popen([sys.executable,'-c',{child_code!r}],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"Path({str(child_pid_file)!r}).write_text(str(p.pid));{leader_action}"
    )

    result = gate.run_assignment_bounded_command(
        [sys.executable, "-c", leader_code], cwd=tmp_path,
        timeout_sec=0.2 if expected_error == "timeout" else 5,
        max_output_bytes=1024,
    )

    child_pid = _wait_for_pid_file(child_pid_file)
    assert result.error == expected_error
    assert _pid_is_absent(child_pid)


def test_lane_cleanup_removes_zero_mode_runtime_directories(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[Path] = []
    original = gate.LaneExecution.cleanup

    def record_cleanup(self):
        observed.append(self.root)
        return original(self)

    monkeypatch.setattr(gate.LaneExecution, "cleanup", record_cleanup)
    lane = _lane(
        "hostile-cleanup-permissions",
        "import os;from pathlib import Path;"
        "[Path(os.environ[name]).chmod(0) for name in "
        "('HOME','XDG_CACHE_HOME','COURSE_MODE_LANE_REPORT_ROOT')]",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "PASS", result
    assert observed and all(not root.exists() for root in observed)


def test_lane_cannot_rename_root_to_evade_cleanup(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[gate.LaneExecution] = []
    original_create = gate.ExecutionStage.create_lane_execution
    original_remove = gate._remove_owned_tree

    def record_lane(self):
        execution = original_create(self)
        observed.append(execution)
        return execution

    monkeypatch.setattr(gate.ExecutionStage, "create_lane_execution", record_lane)
    lane = _lane(
        "rename-cleanup-root",
        "import os;from pathlib import Path;"
        "root=Path(os.environ['HOME']).parents[1];"
        "root.rename(root.with_name(root.name+'-retained'))",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "cleanup"
    assert result["retainedOwner"] == "current-process"
    assert observed
    moved = observed[0].root.with_name(observed[0].root.name + "-retained")
    assert result["retainedPaths"] == [str(moved)]
    assert moved.exists()
    original_remove(moved)


def test_owned_cleanup_rejects_root_swap_between_stat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "owned-root"
    root.mkdir()
    (root / "secret.txt").write_text("retained", encoding="utf-8")
    identity = gate._owned_tree_identity(root)
    renamed = tmp_path / "renamed-root"
    original_open = gate.os.open
    swapped = False

    def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == root.name and dir_fd is not None:
            swapped = True
            root.rename(renamed)
            root.mkdir()
            (root / "decoy.txt").write_text("decoy", encoding="utf-8")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gate.os, "open", swap_then_open)

    assert gate._remove_owned_tree(root, identity) is False
    assert (renamed / "secret.txt").read_text(encoding="utf-8") == "retained"

    monkeypatch.setattr(gate.os, "open", original_open)
    gate._remove_owned_tree(root)
    gate._remove_owned_tree(renamed)


def test_owned_cleanup_rejects_child_swap_between_stat_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "owned-root"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "secret.txt").write_text("retained", encoding="utf-8")
    identity = gate._owned_tree_identity(root)
    renamed = root / "renamed-child"
    original_open = gate.os.open
    swapped = False

    def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == child.name and dir_fd is not None:
            swapped = True
            child.rename(renamed)
            child.mkdir()
            (child / "decoy.txt").write_text("decoy", encoding="utf-8")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(gate.os, "open", swap_then_open)

    assert gate._remove_owned_tree(root, identity) is False
    assert (renamed / "secret.txt").read_text(encoding="utf-8") == "retained"

    monkeypatch.setattr(gate.os, "open", original_open)
    gate._remove_owned_tree(root)


def test_owned_cleanup_rejects_root_swap_immediately_before_rmdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "owned-root"
    root.mkdir()
    identity = gate._owned_tree_identity(root)
    renamed = tmp_path / "renamed-root"
    original_rmdir = gate.os.rmdir
    swapped = False

    def swap_then_rmdir(path, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == root.name and dir_fd is not None:
            swapped = True
            root.rename(renamed)
            root.mkdir()
        return original_rmdir(path, dir_fd=dir_fd)

    monkeypatch.setattr(gate.os, "rmdir", swap_then_rmdir)

    assert gate._remove_owned_tree(root, identity) is False
    assert renamed.exists()

    monkeypatch.setattr(gate.os, "rmdir", original_rmdir)
    gate._remove_owned_tree(renamed)


def test_owned_cleanup_rejects_child_swap_immediately_before_rmdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "owned-root"
    child = root / "child"
    child.mkdir(parents=True)
    identity = gate._owned_tree_identity(root)
    renamed = root / "renamed-child"
    original_rmdir = gate.os.rmdir
    swapped = False

    def swap_then_rmdir(path, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == child.name and dir_fd is not None:
            swapped = True
            child.rename(renamed)
            child.mkdir()
        return original_rmdir(path, dir_fd=dir_fd)

    monkeypatch.setattr(gate.os, "rmdir", swap_then_rmdir)

    assert gate._remove_owned_tree(root, identity) is False
    assert renamed.exists()

    monkeypatch.setattr(gate.os, "rmdir", original_rmdir)
    gate._remove_owned_tree(root)


@pytest.mark.parametrize("leaf_type", ["file", "symlink"])
def test_owned_cleanup_rejects_leaf_swap_at_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leaf_type: str,
) -> None:
    root = tmp_path / "owned-root"
    root.mkdir()
    leaf = root / "leaf"
    if leaf_type == "file":
        leaf.write_text("owned", encoding="utf-8")
    else:
        leaf.symlink_to("target")
    identity = gate._owned_tree_identity(root)
    escaped = tmp_path / f"escaped-{leaf_type}"
    original_unlink = gate.os.unlink
    swapped = False

    def swap_then_unlink(path, *, dir_fd=None):
        nonlocal swapped
        if not swapped and dir_fd is not None:
            swapped = True
            directory = gate._directory_fd_path(dir_fd)
            assert directory is not None
            current = directory / path
            current.rename(escaped)
            if leaf_type == "file":
                current.write_text("decoy", encoding="utf-8")
            else:
                current.symlink_to("decoy")
        return original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(gate.os, "unlink", swap_then_unlink)

    assert gate._remove_owned_tree(root, identity) is False
    assert escaped.exists() or escaped.is_symlink()

    monkeypatch.setattr(gate.os, "unlink", original_unlink)
    original_unlink(escaped)
    gate._remove_owned_tree(root, identity)


def test_owned_cleanup_removes_fifo_without_quarantine(tmp_path: Path) -> None:
    root = tmp_path / "owned-root"
    root.mkdir()
    leaf = root / "fifo"
    os.mkfifo(leaf)
    identity = gate._owned_tree_identity(root)

    assert gate._remove_owned_tree(root, identity) is True
    assert not root.exists()
    assert not list(root.parent.glob(".course-mode-cleanup-*"))


def test_owned_cleanup_fails_closed_for_unix_socket_without_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(tempfile.mkdtemp(prefix="cm-socket-", dir="/private/tmp"))
    leaf = root / "socket"
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(leaf))
    listener.close()
    identity = gate._owned_tree_identity(root)
    unlink_calls = 0
    original_unlink = gate.os.unlink

    def swap_if_called(*args, **kwargs):
        nonlocal unlink_calls
        unlink_calls += 1
        return original_unlink(*args, **kwargs)

    monkeypatch.setattr(gate.os, "unlink", swap_if_called)

    assert gate._remove_owned_tree(root, identity) is False
    assert unlink_calls == 0
    assert leaf.exists()
    assert not list(root.glob(".course-mode-cleanup-*"))
    monkeypatch.setattr(gate.os, "unlink", original_unlink)
    original_unlink(leaf)
    root.rmdir()


def test_lane_cleanup_reports_cross_parent_move_during_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "owned-root"
    root.mkdir()
    destination_parent = tmp_path / "relocated"
    destination_parent.mkdir()
    relocated = destination_parent / "owned-root"
    execution = gate.LaneExecution(
        root, {}, {}, gate._owned_tree_identity(root), gate._open_snapshot_directory(root),
    )
    original_rmdir = gate.os.rmdir
    swapped = False

    def move_then_rmdir(path, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == root.name and dir_fd is not None:
            swapped = True
            root.rename(relocated)
            root.mkdir()
        return original_rmdir(path, dir_fd=dir_fd)

    monkeypatch.setattr(gate.os, "rmdir", move_then_rmdir)

    assert execution.cleanup() is False
    assert execution.retained_path() == relocated
    assert relocated.exists()

    monkeypatch.setattr(gate.os, "rmdir", original_rmdir)
    gate._remove_owned_tree(relocated)


@pytest.mark.parametrize("owner_type", ["stage", "lane"])
def test_repeated_cleanup_cannot_hide_retained_moved_root(
    tmp_path: Path, owner_type: str,
) -> None:
    root = tmp_path / f"owned-{owner_type}"
    root.mkdir()
    retained = tmp_path / f"retained-{owner_type}"
    identity = gate._owned_tree_identity(root)
    descriptor = gate._open_snapshot_directory(root)
    if owner_type == "stage":
        owner = gate.ExecutionStage(root, {}, identity, descriptor)
    else:
        owner = gate.LaneExecution(root, {}, {}, identity, descriptor)

    root.rename(retained)

    assert owner.cleanup() is False
    assert owner.cleanup() is False
    assert owner.retained_path() == retained
    assert retained.exists()
    gate._remove_owned_tree(retained, identity)


@pytest.mark.parametrize("owner_type", ["stage", "lane"])
def test_successful_cleanup_remains_successful_when_repeated(
    tmp_path: Path, owner_type: str,
) -> None:
    root = tmp_path / f"owned-{owner_type}"
    root.mkdir()
    identity = gate._owned_tree_identity(root)
    descriptor = gate._open_snapshot_directory(root)
    if owner_type == "stage":
        owner = gate.ExecutionStage(root, {}, identity, descriptor)
    else:
        owner = gate.LaneExecution(root, {}, {}, identity, descriptor)

    assert owner.cleanup() is True
    assert owner.cleanup() is True
    assert not root.exists()


def test_lane_execution_gc_fallback_closes_descriptor_and_removes_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "owned-lane"
    root.mkdir()
    identity = gate._owned_tree_identity(root)
    descriptor = gate._open_snapshot_directory(root)
    execution = gate.LaneExecution(root, {}, {}, identity, descriptor)

    del execution
    gc.collect()

    root_exists = root.exists()
    try:
        descriptor_metadata = os.fstat(descriptor)
    except OSError as exc:
        descriptor_closed = exc.errno == errno.EBADF
    else:
        descriptor_closed = False
        if (descriptor_metadata.st_dev, descriptor_metadata.st_ino) == identity:
            os.close(descriptor)
    shutil.rmtree(root, ignore_errors=True)

    assert descriptor_closed is True
    assert root_exists is False


def test_gate_reports_lane_root_moved_to_another_parent(
    candidate_file: Path, tmp_path: Path,
) -> None:
    relocated = tmp_path / "relocated-lane"
    lane = _lane(
        "relocate-cleanup-root",
        "import os;from pathlib import Path;"
        "root=Path(os.environ['HOME']).parents[1];"
        f"root.rename(Path({str(relocated)!r}))",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "cleanup"
    assert result["retainedOwner"] == "current-process"
    assert result["retainedPaths"] == [str(relocated)]
    assert relocated.exists()
    gate._remove_owned_tree(relocated)


def test_report_serialization_escapes_surrogate_retained_paths(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    retained = "/tmp/retained-\udcff"
    report = {
        "verdict": "BLOCKED",
        "failedLane": "cleanup",
        "retainedOwner": "current-process",
        "retainedPaths": [retained],
    }

    assert gate._write_report_atomic(report_path, report) is True
    payload = report_path.read_bytes()
    assert b"\\udcff" in payload
    assert json.loads(payload) == report


def test_corrective_report_race_does_not_overwrite_foreign_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "report.json"
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    destination = gate.ReportDestination(parent_fd)
    assert gate._write_report_atomic(report_path, {"verdict": "PASS"}, destination) is True
    foreign = b'{"owner":"foreign"}\n'
    real_stat = gate.os.stat
    real_replace = gate.os.replace
    armed = True

    def install_foreign() -> None:
        nonlocal armed
        armed = False
        replacement = tmp_path / "foreign.json"
        replacement.write_bytes(foreign)
        real_replace(replacement, report_path)

    def replace_after_identity_check(path, *args, **kwargs):
        current = real_stat(path, *args, **kwargs)
        if armed and path == report_path.name and kwargs.get("dir_fd") == parent_fd:
            install_foreign()
        return current

    monkeypatch.setattr(gate.os, "stat", replace_after_identity_check)
    try:
        assert gate._write_report_atomic(
            report_path, {"verdict": "BLOCKED"}, destination,
        ) is False
        assert report_path.read_bytes() == foreign
    finally:
        gate._close_report_destination(destination)


def test_report_invalidation_race_does_not_unlink_foreign_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_path = tmp_path / "report.json"
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    destination = gate.ReportDestination(parent_fd)
    assert gate._write_report_atomic(report_path, {"verdict": "PASS"}, destination) is True
    foreign = b'{"owner":"foreign"}\n'
    real_ftruncate = gate.os.ftruncate
    real_replace = gate.os.replace
    armed = True

    def replace_before_fd_invalidation(descriptor, length):
        nonlocal armed
        if armed and descriptor == destination.report_fd:
            armed = False
            replacement = tmp_path / "foreign.json"
            replacement.write_bytes(foreign)
            real_replace(replacement, report_path)
        return real_ftruncate(descriptor, length)

    monkeypatch.setattr(gate.os, "ftruncate", replace_before_fd_invalidation)
    try:
        gate._invalidate_report(report_path, destination)
        assert report_path.read_bytes() == foreign
    finally:
        gate._close_report_destination(destination)


def test_gate_reports_retained_snapshot_when_owned_cleanup_cannot_finish(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: list[Path] = []
    original = gate._remove_owned_tree

    def refuse_lane_cleanup(
        path: Path, expected_identity: tuple[int, int] | None = None,
    ) -> bool:
        if path.name.startswith("course-mode-lane-"):
            retained.append(path)
            return False
        return original(path, expected_identity)

    monkeypatch.setattr(gate, "_remove_owned_tree", refuse_lane_cleanup)

    result = gate.run_gate(candidate_file, "quick", lanes=(_lane("cleanup-owner", "pass"),))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "cleanup"
    assert result["retainedOwner"] == "current-process"
    assert result["retainedPaths"] == sorted({str(path) for path in retained})
    for path in set(retained):
        original(path)


def test_lane_construction_cleanup_failure_still_removes_snapshot(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: list[Path] = []
    snapshots: list[Path] = []
    original_remove = gate._remove_owned_tree
    original_stage = gate.stage_execution_candidate

    def record_stage(candidate, lanes):
        stage = original_stage(candidate, lanes)
        snapshots.append(stage.root)
        return stage

    def refuse_lane_cleanup(
        path: Path, expected_identity: tuple[int, int] | None = None,
    ) -> bool:
        if path.name.startswith("course-mode-lane-"):
            retained.append(path)
            return False
        return original_remove(path, expected_identity)

    monkeypatch.setattr(gate, "stage_execution_candidate", record_stage)
    monkeypatch.setattr(gate, "_remove_owned_tree", refuse_lane_cleanup)
    monkeypatch.setattr(
        gate, "_make_tree_owner_writable",
        lambda _root: (_ for _ in ()).throw(OSError("forced lane construction failure")),
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(_lane("cleanup-owner", "pass"),))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "cleanup"
    assert result["retainedOwner"] == "current-process"
    assert result["retainedPaths"] == sorted({str(path) for path in retained})
    assert snapshots and all(not path.exists() for path in snapshots)
    for path in set(retained):
        original_remove(path)


def test_lane_descriptor_open_failure_removes_created_root(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    stage = gate.stage_execution_candidate(candidate, (_lane("descriptor-open", "pass"),))
    lane_roots: list[Path] = []
    original_mkdtemp = gate.tempfile.mkdtemp
    original_open = gate._open_snapshot_directory

    def record_mkdtemp(*args, **kwargs):
        path = Path(original_mkdtemp(*args, **kwargs))
        if kwargs.get("prefix") == "course-mode-lane-":
            lane_roots.append(path)
        return str(path)

    def refuse_lane_descriptor(path: Path) -> int:
        if path.name.startswith("course-mode-lane-"):
            raise OSError("forced descriptor failure")
        return original_open(path)

    monkeypatch.setattr(gate.tempfile, "mkdtemp", record_mkdtemp)
    monkeypatch.setattr(gate, "_open_snapshot_directory", refuse_lane_descriptor)
    try:
        with pytest.raises(OSError, match="forced descriptor failure"):
            stage.create_lane_execution()
        assert lane_roots and all(not path.exists() for path in lane_roots)
    finally:
        stage.cleanup()


def test_lane_construction_failure_reports_cross_parent_retained_path(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    stage = gate.stage_execution_candidate(candidate, (_lane("lane-retained", "pass"),))
    relocated = tmp_path / "relocated-lane"
    original_copytree = gate.shutil.copytree
    original_remove = gate._remove_owned_tree

    def move_then_fail(source, destination, **kwargs):
        raise OSError("forced lane construction failure")

    def move_during_cleanup(path, _identity):
        Path(path).rename(relocated)
        return False

    monkeypatch.setattr(gate.shutil, "copytree", move_then_fail)
    monkeypatch.setattr(gate, "_remove_owned_tree", move_during_cleanup)
    try:
        with pytest.raises(gate.RetainedStagingError) as caught:
            stage.create_lane_execution()
        assert caught.value.paths == (str(relocated),)
        assert relocated.exists()
    finally:
        monkeypatch.setattr(gate.shutil, "copytree", original_copytree)
        monkeypatch.setattr(gate, "_remove_owned_tree", original_remove)
        original_remove(relocated)
        stage.cleanup()


def test_stage_identity_failure_removes_created_root(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    stage_roots: list[Path] = []
    original_mkdtemp = gate.tempfile.mkdtemp
    original_identity = gate._owned_tree_identity

    def record_mkdtemp(*args, **kwargs):
        path = Path(original_mkdtemp(*args, **kwargs))
        if kwargs.get("prefix") == "course-mode-stage-":
            stage_roots.append(path)
        return str(path)

    def refuse_stage_identity(path: Path) -> tuple[int, int]:
        if path.name.startswith("course-mode-stage-"):
            raise OSError("forced stage identity failure")
        return original_identity(path)

    monkeypatch.setattr(gate.tempfile, "mkdtemp", record_mkdtemp)
    monkeypatch.setattr(gate, "_owned_tree_identity", refuse_stage_identity)

    with pytest.raises(OSError, match="forced stage identity failure"):
        gate.stage_execution_candidate(candidate, (_lane("stage-identity", "pass"),))
    assert stage_roots and all(not path.exists() for path in stage_roots)


def test_stage_construction_failure_reports_cross_parent_retained_path(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    relocated = tmp_path / "relocated-stage"
    original_archive = gate._archive_repository
    original_remove = gate._remove_owned_tree

    def move_then_fail(_source, _sha, destination, _state):
        raise OSError("forced stage construction failure")

    def move_during_cleanup(path, _identity):
        Path(path).rename(relocated)
        return False

    monkeypatch.setattr(gate, "_archive_repository", move_then_fail)
    monkeypatch.setattr(gate, "_remove_owned_tree", move_during_cleanup)
    try:
        with pytest.raises(gate.RetainedStagingError) as caught:
            gate.stage_execution_candidate(candidate, ())
        assert caught.value.paths == (str(relocated),)
        assert relocated.exists()
    finally:
        monkeypatch.setattr(gate, "_archive_repository", original_archive)
        monkeypatch.setattr(gate, "_remove_owned_tree", original_remove)
        original_remove(relocated)


def test_staged_child_context_contains_no_original_repository_or_node_paths(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    originals = {
        descriptor["path"] for descriptor in candidate["repositories"].values()
    } | {
        candidate["tools"]["node"]["backend"]["executable"],
        candidate["tools"]["node"]["backend"]["npm"]["entrypoint"],
    }
    observed = {}

    def capture(command, *, cwd, env, **_kwargs):
        observed.update(command=command, cwd=str(cwd), env=env)
        return gate._manifest.BoundedCommandResult(0, "", None)

    monkeypatch.setattr(gate, "run_bounded_command", capture)
    result = gate.run_gate(
        candidate_file, "quick",
        lanes=(gate.Lane("backend-npm", "backend", ".", ("npm", "test"), 5.0),),
    )

    assert result["verdict"] == "PASS"
    child_context = json.dumps(observed, sort_keys=True)
    assert all(original not in child_context for original in originals)


def test_snapshot_cleanup_runs_after_unexpected_runner_error(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed = []
    original_stage = gate.stage_execution_candidate

    def record_stage(candidate: dict, lanes):
        staged = original_stage(candidate, lanes)
        observed.append(staged.root)
        return staged

    monkeypatch.setattr(gate, "stage_execution_candidate", record_stage)
    monkeypatch.setattr(
        gate, "run_bounded_command", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(RuntimeError, match="boom"):
        gate.run_gate(candidate_file, "quick", lanes=(_lane("error", "pass"),))

    assert observed and not observed[0].exists()


def test_snapshot_copy_preserves_read_only_directory_modes_after_population(tmp_path: Path) -> None:
    source = tmp_path / "read-only-source"
    child = source / "nested"
    child.mkdir(parents=True)
    (child / "payload.txt").write_text("payload", encoding="utf-8")
    child.chmod(0o555)
    source.chmod(0o555)
    destination = tmp_path / "snapshot"

    gate._copy_snapshot_tree(source, destination, {"entries": 0, "bytes": 0})

    assert (destination / "nested/payload.txt").read_text(encoding="utf-8") == "payload"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o555
    assert stat.S_IMODE((destination / "nested").stat().st_mode) == 0o555


def test_unexpected_runner_error_reports_cleanup_failure(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: list[Path] = []
    original_remove = gate._remove_owned_tree

    def refuse_lane_cleanup(
        path: Path, expected_identity: tuple[int, int] | None = None,
    ) -> bool:
        if path.name.startswith("course-mode-lane-"):
            retained.append(path)
            return False
        return original_remove(path, expected_identity)

    monkeypatch.setattr(gate, "_remove_owned_tree", refuse_lane_cleanup)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(_lane("error", "pass"),))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "cleanup"
    assert result["retainedOwner"] == "current-process"
    assert result["retainedPaths"] == sorted({str(path) for path in retained})
    for path in set(retained):
        original_remove(path)


def test_assignment_environment_uses_image_ids_not_mutable_tags(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")

    environment = gate._child_environment(
        candidate,
        _assignment_source(candidate_file),
        lane,
        assignment_runtime_capsule_root=tmp_path / "owner",
        assignment_runtime_root=tmp_path / "owner/runtime",
    )

    assert environment["TBOT_LESSON_STUDIO_BACKEND_IMAGE"] == candidate["images"]["lessonStudioBackend"]["id"]
    assert environment["TBOT_LESSON_STUDIO_WEB_IMAGE"] == candidate["images"]["lessonStudioWeb"]["id"]


def test_assignment_environment_receives_exact_capsule_pair(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")
    capsule = gate.AssignmentRuntimeCapsule.create(())
    try:
        environment = gate._child_environment(
            candidate,
            _assignment_source(candidate_file),
            lane,
            source_candidate=candidate,
            assignment_runtime_capsule_root=capsule.root,
            assignment_runtime_root=capsule.runtime_root,
        )

        assert environment is not None
        assert environment["TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT"] == str(capsule.root)
        assert environment["TASK4_ASSIGNMENT_RUNTIME_ROOT"] == str(capsule.runtime_root)
    finally:
        assert capsule.cleanup() is True


def test_non_assignment_environment_never_receives_capsule_pair(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = _lane("ordinary", "pass")

    environment = gate._child_environment(
        candidate,
        {},
        lane,
        assignment_runtime_capsule_root=tmp_path / "owner",
        assignment_runtime_root=tmp_path / "owner/runtime",
    )

    assert environment is not None
    assert "TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT" not in environment
    assert "TASK4_ASSIGNMENT_RUNTIME_ROOT" not in environment


@pytest.mark.parametrize(
    "lane_name,missing",
    [
        ("admin-course-mode-assignment-new", "LESSON_STUDIO_E2E_BACKEND_HOST_PORT"),
        ("admin-course-mode-assignment-new", "LESSON_STUDIO_E2E_WEB_HOST_PORT"),
        ("admin-course-mode-assignment-new", "TASK4_ASSIGNMENT_MEDIA_HOST_PORT"),
        ("admin-course-mode-assignment-rollback", "LESSON_STUDIO_E2E_BACKEND_HOST_PORT"),
        ("admin-course-mode-assignment-rollback", "LESSON_STUDIO_E2E_WEB_HOST_PORT"),
        ("admin-course-mode-assignment-rollback", "TASK4_ASSIGNMENT_MEDIA_HOST_PORT"),
    ],
)
def test_assignment_lane_requires_isolated_ports_before_command(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, lane_name: str, missing: str,
) -> None:
    lane = next(item for item in gate.FULL_LANES if item.name == lane_name)
    source = _assignment_source(candidate_file)
    source.pop(missing)
    monkeypatch.setattr(gate, "release_state_matches", lambda *_args: True)
    monkeypatch.setattr(
        gate, "stage_execution_candidate",
        lambda *_args: pytest.fail("assignment lane staged before required port validation"),
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,), source_environment=source,
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == lane_name


@pytest.mark.parametrize("name", gate.TASK4_ASSIGNMENT_PORT_ENV)
@pytest.mark.parametrize(
    "value", ["not-a-port", "0", "65536", "+13100", " 13100", "9" * 5000],
)
def test_assignment_lane_rejects_invalid_isolated_port(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")
    source = _assignment_source(candidate_file)
    source[name] = value
    monkeypatch.setattr(gate, "release_state_matches", lambda *_args: True)
    monkeypatch.setattr(
        gate, "stage_execution_candidate",
        lambda *_args: pytest.fail("assignment lane staged before port validation"),
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,), source_environment=source,
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == lane.name


@pytest.mark.parametrize(
    "ports",
    [
        ("13100", "13100", "28443"),
        ("13100", "18102", "13100"),
        ("13100", "18102", "18102"),
        ("3100", "18102", "28443"),
        ("13100", "8102", "28443"),
        ("13100", "18102", "18443"),
    ],
)
def test_assignment_lane_rejects_duplicate_or_standard_stack_port(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch, ports: tuple[str, str, str],
) -> None:
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")
    source = _assignment_source(candidate_file)
    for name, value in zip(
        (
            "LESSON_STUDIO_E2E_BACKEND_HOST_PORT",
            "LESSON_STUDIO_E2E_WEB_HOST_PORT",
            "TASK4_ASSIGNMENT_MEDIA_HOST_PORT",
        ),
        ports,
    ):
        source[name] = value
    monkeypatch.setattr(gate, "release_state_matches", lambda *_args: True)
    monkeypatch.setattr(
        gate, "stage_execution_candidate",
        lambda *_args: pytest.fail("assignment lane staged before port isolation validation"),
    )

    result = gate.run_gate(
        candidate_file, "full", lanes=(lane,), source_environment=source,
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == lane.name


def test_assignment_lane_forwards_isolated_ports_without_changing_namespace(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-rollback")
    source = _assignment_source(candidate_file)

    environment = gate._child_environment(
        candidate,
        source,
        lane,
        assignment_runtime_capsule_root=tmp_path / "owner",
        assignment_runtime_root=tmp_path / "owner/runtime",
    )

    assert environment is not None
    assert environment["LESSON_STUDIO_E2E_BACKEND_HOST_PORT"] == "13100"
    assert environment["LESSON_STUDIO_E2E_WEB_HOST_PORT"] == "18102"
    assert environment["TASK4_ASSIGNMENT_MEDIA_HOST_PORT"] == "28443"
    assert environment["LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME"] == "tbot-task4-unit"
    assert environment["LESSON_STUDIO_E2E_RESOURCE_PREFIX"] == "tbot-task4-unit"


def test_assignment_source_runtime_root_is_validated_before_capsule_injection(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")
    source_runtime = (
        Path(candidate["repositories"]["adminEsp"]["path"])
        / "main/manager-web/output/task4-candidate"
    )
    stage = gate.stage_execution_candidate(candidate, ())
    execution = None
    try:
        execution = stage.create_lane_execution()

        environment = gate._child_environment(
            execution.candidate,
            {**_assignment_source(candidate_file), "TASK4_ASSIGNMENT_RUNTIME_ROOT": str(source_runtime)},
            lane,
            source_candidate=candidate,
            assignment_runtime_capsule_root=tmp_path / "owner",
            assignment_runtime_root=tmp_path / "owner/runtime",
        )

        assert environment is not None
        assert environment["TASK4_ASSIGNMENT_RUNTIME_ROOT"] == str(
            tmp_path / "owner/runtime"
        )
    finally:
        if execution is not None:
            assert execution.cleanup() is True
        assert stage.cleanup() is True


def test_assignment_runtime_root_uses_one_required_environment_snapshot(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")
    source_runtime = str(
        Path(candidate["repositories"]["adminEsp"]["path"])
        / "main/manager-web/output/task4-candidate"
    )

    class MutableSource(dict[str, str]):
        reads = 0

        def get(self, key: str, default: str | None = None) -> str | None:
            if key == "TASK4_ASSIGNMENT_RUNTIME_ROOT":
                self.reads += 1
                return source_runtime if self.reads == 1 else ""
            return super().get(key, default)

    stage = gate.stage_execution_candidate(candidate, ())
    execution = None
    try:
        execution = stage.create_lane_execution()
        source = MutableSource({
            **_assignment_source(candidate_file),
            "TASK4_ASSIGNMENT_RUNTIME_ROOT": source_runtime,
        })

        environment = gate._child_environment(
            execution.candidate,
            source,
            lane,
            source_candidate=candidate,
            assignment_runtime_capsule_root=tmp_path / "owner",
            assignment_runtime_root=tmp_path / "owner/runtime",
        )

        assert environment is not None
        assert environment["TASK4_ASSIGNMENT_RUNTIME_ROOT"] == str(
            tmp_path / "owner/runtime"
        )
        assert source.reads == 1
    finally:
        if execution is not None:
            assert execution.cleanup() is True
        assert stage.cleanup() is True


@pytest.mark.parametrize("runtime", ["outside", "lexical-escape", "symlink-escape"])
def test_assignment_runtime_root_rebase_fails_closed(
    candidate_file: Path, tmp_path: Path, runtime: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")
    admin = Path(candidate["repositories"]["adminEsp"]["path"])
    if runtime == "outside":
        source_runtime = tmp_path / "outside-task4"
    elif runtime == "lexical-escape":
        source_runtime = Path(
            f"{admin}/main/manager-web/output/../outside-task4"
        )
    else:
        output = admin / "main/manager-web/output"
        output.mkdir(parents=True)
        source_runtime = output / "task4-link"
        source_runtime.symlink_to(tmp_path / "outside-task4", target_is_directory=True)

    environment = gate._child_environment(
        candidate,
        {**_assignment_source(candidate_file), "TASK4_ASSIGNMENT_RUNTIME_ROOT": str(source_runtime)},
        lane,
        source_candidate=candidate,
        assignment_runtime_capsule_root=tmp_path / "owner",
        assignment_runtime_root=tmp_path / "owner/runtime",
    )

    assert environment is None


def test_snapshot_rejects_tree_over_entry_limit(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    monkeypatch.setattr(gate, "MAX_SNAPSHOT_ENTRIES", 2)

    with pytest.raises(ValueError, match="entry limit"):
        gate.stage_execution_candidate(candidate, ())


def test_snapshot_rejects_tree_over_byte_limit(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    monkeypatch.setattr(gate, "MAX_SNAPSHOT_BYTES", 1)

    with pytest.raises(ValueError, match="byte limit"):
        gate.stage_execution_candidate(candidate, ())


def test_git_archive_rejects_oversized_blob_before_content_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "archive"
    object_id = "a" * 40
    calls = 0

    def bounded(command, **_kwargs):
        nonlocal calls
        calls += 1
        stdout = object_id if calls == 1 else f"100644 blob {object_id}\tbig.bin\0"
        return gate._manifest.BoundedCommandResult(0, stdout, None)

    class Input:
        def write(self, _value): return None
        def flush(self): return None
        def close(self): return None

    class Output:
        def __init__(self): self.read_sizes = []
        def readline(self): return f"{object_id} blob {3 * 1024 ** 3}\n".encode()
        def read(self, size):
            self.read_sizes.append(size)
            raise AssertionError("oversized blob content must not be read")

    class Process:
        def __init__(self):
            self.stdin = Input()
            self.stdout = Output()
            self.stderr = None
            self.returncode = 0
        def wait(self, timeout=None): return 0
        def poll(self): return self.returncode
        def kill(self): raise AssertionError("completed fake process must not be killed")

    process = Process()
    monkeypatch.setattr(gate._manifest, "run_bounded_command", bounded)
    monkeypatch.setattr(gate.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(ValueError, match="file byte limit"):
        gate._archive_repository(source, object_id, destination, {"entries": 0, "bytes": 0})
    assert process.stdout.read_sizes == []


def test_git_archive_rejects_oversized_symlink_before_content_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "archive"
    object_id = "c" * 40
    calls = 0

    def bounded(command, **_kwargs):
        nonlocal calls
        calls += 1
        stdout = object_id if calls == 1 else f"120000 blob {object_id}\tlink\0"
        return gate._manifest.BoundedCommandResult(0, stdout, None)

    class Input:
        def write(self, _value): return None
        def flush(self): return None
        def close(self): return None

    class Output:
        read_sizes = []
        def readline(self): return f"{object_id} blob {gate.MAX_GIT_SYMLINK_BYTES + 1}\n".encode()
        def read(self, size):
            self.read_sizes.append(size)
            raise AssertionError("oversized symlink content must not be read")

    class Process:
        def __init__(self):
            self.stdin, self.stdout, self.stderr = Input(), Output(), None
            self.returncode = 0
        def wait(self, timeout=None): return 0
        def poll(self): return self.returncode
        def kill(self): raise AssertionError("completed fake process must not be killed")

    process = Process()
    monkeypatch.setattr(gate._manifest, "run_bounded_command", bounded)
    monkeypatch.setattr(gate.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(ValueError, match="symlink byte limit"):
        gate._archive_repository(source, object_id, destination, {"entries": 0, "bytes": 0})
    assert process.stdout.read_sizes == []


def test_git_archive_closes_all_batch_process_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "course-mode@example.invalid")
    _git(source, "config", "user.name", "Course Mode Test")
    (source / "tracked.txt").write_text("candidate\n", encoding="utf-8")
    _git(source, "add", "tracked.txt")
    _git(source, "commit", "-m", "candidate")
    sha = _git(source, "rev-parse", "HEAD")
    before = len(os.listdir("/dev/fd"))

    monkeypatch.setattr(gate, "MAX_SNAPSHOT_BYTES", 0)
    with pytest.raises(ValueError, match="byte limit"):
        gate._archive_repository(source, sha, tmp_path / "archive", {"entries": 0, "bytes": 0})

    assert len(os.listdir("/dev/fd")) == before


def test_git_archive_rejects_bounded_ls_tree_overflow_before_cat_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    object_id = "b" * 40
    calls = 0

    def bounded(command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return gate._manifest.BoundedCommandResult(0, object_id, None)
        return gate._manifest.BoundedCommandResult(None, "", "output")

    monkeypatch.setattr(gate._manifest, "run_bounded_command", bounded)
    monkeypatch.setattr(
        gate.subprocess, "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cat-file must not start")),
    )

    with pytest.raises(ValueError, match="candidate archive failed"):
        gate._archive_repository(
            source, object_id, tmp_path / "archive", {"entries": 0, "bytes": 0},
        )


def test_snapshot_rejects_tree_over_depth_limit(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    monkeypatch.setattr(gate, "MAX_SNAPSHOT_DEPTH", 0)

    with pytest.raises(ValueError, match="depth limit"):
        gate.stage_execution_candidate(candidate, ())


def test_snapshot_ignores_untracked_escaping_symlink(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = Path(candidate["repositories"]["backend"]["path"])
    (repository / "escape").symlink_to("../candidate.json")

    stage = gate.stage_execution_candidate(candidate, ())
    try:
        staged = Path(stage.candidate["repositories"]["backend"]["path"])
        assert not os.path.lexists(staged / "escape")
    finally:
        stage.cleanup()


def test_snapshot_ignores_untracked_special_file(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = Path(candidate["repositories"]["backend"]["path"])
    fifo = repository / "unsafe.fifo"
    os.mkfifo(fifo)

    stage = gate.stage_execution_candidate(candidate, ())
    try:
        staged = Path(stage.candidate["repositories"]["backend"]["path"])
        assert not (staged / "unsafe.fifo").exists()
    finally:
        stage.cleanup()


def test_snapshot_uses_commit_object_when_worktree_file_mutates_during_archive(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    source = Path(candidate["repositories"]["backend"]["path"]) / "tracked.txt"
    source_identity = (source.stat().st_dev, source.stat().st_ino)
    original_read = gate.os.read
    mutated = False

    def mutate_during_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        metadata = os.fstat(descriptor)
        if not mutated and (metadata.st_dev, metadata.st_ino) == source_identity:
            mutated = True
            source.write_text("mutated while copying", encoding="utf-8")
        return original_read(descriptor, size)

    monkeypatch.setattr(gate.os, "read", mutate_during_read)

    stage = gate.stage_execution_candidate(candidate, ())
    try:
        staged = Path(stage.candidate["repositories"]["backend"]["path"])
        assert (staged / "tracked.txt").read_text(encoding="utf-8") == "backend"
    finally:
        stage.cleanup()


def test_snapshot_construction_failure_removes_partial_tree(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    stage_root = tmp_path / "partial-stage"
    stage_root.mkdir()
    monkeypatch.setattr(gate.tempfile, "mkdtemp", lambda **_kwargs: str(stage_root))
    monkeypatch.setattr(gate, "MAX_SNAPSHOT_ENTRIES", 0)

    with pytest.raises(ValueError):
        gate.stage_execution_candidate(candidate, ())

    assert not stage_root.exists()


def test_repository_mutation_after_validation_never_reaches_lane(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    tracked = Path(candidate["repositories"]["adminEsp"]["path"]) / "tracked.txt"
    marker = tmp_path / "mutated-by-race"
    original_release_state_matches = gate.release_state_matches
    mutated = False

    def validate_then_mutate(*args, **kwargs):
        nonlocal mutated
        result = original_release_state_matches(*args, **kwargs)
        if result and not mutated:
            tracked.write_text("poisoned", encoding="utf-8")
            mutated = True
        return result

    monkeypatch.setattr(gate, "release_state_matches", validate_then_mutate)
    lane = _lane(
        "validation-open-race",
        "from pathlib import Path;"
        f"Path({str(marker)!r}).touch() if Path('tracked.txt').read_text() == 'poisoned' else None",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "PASS", result
    assert not marker.exists()


def test_snapshot_reads_literal_commit_when_replace_ref_targets_other_bytes(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = candidate["repositories"]["adminEsp"]
    root = Path(repository["path"])
    original = repository["sha"]
    (root / "tracked.txt").write_text("replacement", encoding="utf-8")
    _git(root, "add", "tracked.txt")
    _git(root, "commit", "-m", "replacement object")
    replacement = _git(root, "rev-parse", "HEAD")
    _git(root, "reset", "--hard", original)
    _git(root, "replace", original, replacement)

    stage = gate.stage_execution_candidate(candidate, ())
    try:
        staged = Path(stage.candidate["repositories"]["adminEsp"]["path"])
        assert (staged / "tracked.txt").read_text(encoding="utf-8") == "adminEsp"
    finally:
        stage.cleanup()


def test_snapshot_ignores_export_attributes_and_preserves_literal_blob_bytes(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = candidate["repositories"]["adminEsp"]
    root = Path(repository["path"])
    (root / ".gitattributes").write_text(
        "kept.txt export-ignore\nsubstituted.txt export-subst\n", encoding="utf-8",
    )
    (root / "kept.txt").write_text("must remain\n", encoding="utf-8")
    literal = "$Format:%H$\n"
    (root / "substituted.txt").write_text(literal, encoding="utf-8")
    _git(root, "add", ".gitattributes", "kept.txt", "substituted.txt")
    _git(root, "commit", "-m", "export attributes")
    repository.update(_repository(root))
    _refresh_image_reference(candidate, "adminEsp")

    stage = gate.stage_execution_candidate(candidate, ())
    try:
        staged = Path(stage.candidate["repositories"]["adminEsp"]["path"])
        assert (staged / "kept.txt").read_text(encoding="utf-8") == "must remain\n"
        assert (staged / "substituted.txt").read_text(encoding="utf-8") == literal
    finally:
        stage.cleanup()


def test_each_lane_gets_a_fresh_verified_repository_snapshot(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lanes = (
        _lane("poison-first-snapshot", "from pathlib import Path;Path('tracked.txt').write_text('poisoned')"),
        _lane("verify-fresh-second-snapshot", "from pathlib import Path;assert Path('tracked.txt').read_text() == 'adminEsp'"),
    )

    result = gate.run_gate(candidate_file, "quick", lanes=lanes)

    assert result["verdict"] == "PASS", result
    assert [item["exitCode"] for item in result["lanes"]] == [0, 0]


def test_lane_workspace_is_writable_for_real_build_outputs_and_is_destroyed(
    candidate_file: Path, tmp_path: Path,
) -> None:
    observed_roots: list[Path] = []
    code = (
        "from pathlib import Path;"
        "Path('dist/nested').mkdir(parents=True);"
        "Path('dist/nested/artifact.js').write_text('built');"
        "Path('.course-cache').mkdir();"
        "Path('.course-cache/result.json').write_text('{}')"
    )
    original = gate.LaneExecution.cleanup

    def record_cleanup(self):
        observed_roots.append(self.root)
        return original(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gate.LaneExecution, "cleanup", record_cleanup)
        result = gate.run_gate(candidate_file, "quick", lanes=(_lane("real-write", code),))

    assert result["verdict"] == "PASS", result
    assert observed_roots and all(not root.exists() for root in observed_roots)


def test_writable_lane_copy_cannot_mutate_immutable_verified_base(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    stage = gate.stage_execution_candidate(candidate, ())
    execution = stage.create_lane_execution()
    try:
        base_file = Path(stage.candidate["repositories"]["adminEsp"]["path"]) / "tracked.txt"
        lane_file = Path(execution.candidate["repositories"]["adminEsp"]["path"]) / "tracked.txt"
        assert stat.S_IMODE(base_file.stat().st_mode) & 0o222 == 0
        assert stat.S_IMODE(lane_file.stat().st_mode) & stat.S_IWUSR
        lane_file.write_text("lane mutation", encoding="utf-8")
        assert base_file.read_text(encoding="utf-8") == "adminEsp"
    finally:
        execution.cleanup()
        stage.cleanup()


def test_staged_node_package_root_preserves_bound_mode(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    descriptor = candidate["tools"]["node"]["backend"]
    package_root = Path(descriptor["packageRoot"])
    package_root.chmod(0o700)
    package_tree = gate._manifest.secure_node_package_tree_descriptor(package_root)
    assert package_tree is not None
    descriptor["packageRootMode"] = package_tree["rootMode"]
    descriptor["packageTreeSha256"] = package_tree["sha256"]

    stage = gate.stage_execution_candidate(
        candidate, (gate.Lane("backend-node", "backend", ".", ("node", "--version"), 5.0),),
    )
    execution = stage.create_lane_execution()
    try:
        staged_root = Path(execution.candidate["tools"]["node"]["backend"]["packageRoot"])
        assert stat.S_IMODE(staged_root.stat().st_mode) == 0o700
    finally:
        execution.cleanup()
        stage.cleanup()


def test_backend_node_lane_stages_symlinked_node_modules_without_python_authority(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(candidate, "backend", ".", "backend")
    (install / "fixture-link").symlink_to("fixture-package", target_is_directory=True)
    candidate["tools"]["nodeInstalls"]["backend"] = gate.describe_node_install(
        install, install.parent / "package-lock.json",
    )
    lane = gate.Lane(
        "backend-node-symlink", "backend", ".", ("npx", "vitest", "run"), 5.0,
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    try:
        staged = Path(stage.candidate["repositories"]["backend"]["path"])
        assert (staged / "node_modules/fixture-link").is_symlink()
        assert not (stage.root / ".course-mode-authority/backend.json").exists()
    finally:
        assert stage.cleanup() is True


def test_python_lane_stages_backend_snapshot_authority(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = gate.Lane(
        "esp-python-authority", "adminEsp", "main/tbot-server",
        ("python3", "-m", "pytest", "-q"), 5.0,
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    try:
        authority = stage.root / ".course-mode-authority/backend.json"
        document = json.loads(authority.read_text(encoding="utf-8"))
        observed, error = gate._manifest.secure_backend_snapshot_tree_descriptor(
            Path(stage.candidate["repositories"]["backend"]["path"]),
        )
        assert error is None
        assert document["sourceTreeDigest"] == observed
        execution, execution_error = gate._manifest.secure_backend_execution_tree_descriptor(
            Path(stage.candidate["repositories"]["backend"]["path"]),
        )
        assert execution_error is None
        assert document["executionTreeDigest"] == execution
    finally:
        assert stage.cleanup() is True


def test_snapshot_directory_open_rejects_symlink_parent(tmp_path: Path) -> None:
    real = tmp_path / "real"
    child = real / "child"
    child.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    with pytest.raises(OSError):
        descriptor = gate._open_snapshot_directory(alias / "child")
        os.close(descriptor)


def test_snapshot_symlink_validation_is_lexical_and_never_calls_path_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.txt").write_text("safe", encoding="utf-8")
    (source / "nested").mkdir()
    (source / "nested/link.txt").symlink_to("../target.txt")
    destination = tmp_path / "destination"

    monkeypatch.setattr(
        Path, "resolve",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Path.resolve forbidden")),
    )

    gate._copy_snapshot_tree(source, destination, {"entries": 0, "bytes": 0})

    assert (destination / "nested/link.txt").is_symlink()
    assert os.readlink(destination / "nested/link.txt") == "../target.txt"


def test_staging_rejects_tool_bytes_changed_after_validation(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "backend", ".", "backend")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    executable = Path(candidate["tools"]["node"]["backend"]["executable"])
    marker = tmp_path / "unverified-tool-executed"
    original_release_state_matches = gate.release_state_matches
    mutated = False

    def validate_then_mutate(*args, **kwargs):
        nonlocal mutated
        result = original_release_state_matches(*args, **kwargs)
        if result and not mutated:
            executable.write_text(f"#!/bin/sh\ntouch {marker}\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            mutated = True
        return result

    monkeypatch.setattr(gate, "release_state_matches", validate_then_mutate)
    lane = gate.Lane("tool-race", "backend", ".", ("node", "--version"), 5.0)

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "snapshot"
    assert not marker.exists()


def test_snapshot_overlays_only_hash_bound_dirty_exception(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = candidate["repositories"]["adminEsp"]
    source = Path(repository["path"]) / "tracked.txt"
    source.write_text("authorized dirty bytes", encoding="utf-8")
    repository["dirtyExceptions"] = [{
        "path": "tracked.txt",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }]

    stage = gate.stage_execution_candidate(candidate, ())
    try:
        staged = Path(stage.candidate["repositories"]["adminEsp"]["path"])
        assert (staged / "tracked.txt").read_text(encoding="utf-8") == "authorized dirty bytes"
    finally:
        stage.cleanup()


def test_snapshot_rejects_dirty_exception_hash_drift(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    repository = candidate["repositories"]["adminEsp"]
    source = Path(repository["path"]) / "tracked.txt"
    source.write_text("changed after authorization", encoding="utf-8")
    repository["dirtyExceptions"] = [{"path": "tracked.txt", "sha256": "0" * 64}]

    with pytest.raises(ValueError, match="descriptor mismatch"):
        gate.stage_execution_candidate(candidate, ())


def test_admin_browser_environment_is_only_candidate_bound_descriptor(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    environment = gate._child_environment(candidate, {
        "CHROME_BIN": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    }, next(lane for lane in gate.FULL_LANES if lane.name == "admin-browser"))
    browser = candidate["tools"]["robotPreviewBrowser"]

    assert environment["TBOT_ROBOT_PREVIEW_BROWSER_ROOT"] == browser["root"]
    assert environment["TBOT_ROBOT_PREVIEW_BROWSER_EXECUTABLE"] == browser["executable"]
    assert environment["TBOT_ROBOT_PREVIEW_BROWSER_ENGINE"] == browser["engine"]
    assert environment["TBOT_ROBOT_PREVIEW_BROWSER_REVISION"] == browser["revision"]
    assert environment["TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256"] == browser["treeDigest"]["sha256"]
    assert environment["TBOT_ROBOT_PREVIEW_BROWSER_TREE_ENTRY_COUNT"] == str(browser["treeDigest"]["entryCount"])
    assert environment["TBOT_ROBOT_PREVIEW_BROWSER_TREE_TOTAL_BYTES"] == str(browser["treeDigest"]["totalBytes"])
    assert "CHROME_BIN" not in environment
    assert "PLAYWRIGHT_BROWSERS_PATH" not in environment


def test_admin_browser_snapshot_preserves_playwright_platform_layout(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    lane = next(lane for lane in gate.FULL_LANES if lane.name == "admin-browser")

    stage = gate.stage_execution_candidate(candidate, (lane,))
    try:
        browser = stage.candidate["tools"]["robotPreviewBrowser"]
        expected_suffix = Path(
            f"chromium_headless_shell-{browser['revision']}",
            "chrome-headless-shell-mac-arm64",
        )

        assert Path(browser["root"]).parts[-len(expected_suffix.parts):] == expected_suffix.parts
        assert Path(browser["root"], browser["executable"]).is_file()
    finally:
        assert stage.cleanup() is True


def test_playwright_lane_stages_candidate_bound_container_tools(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate)
    lane = next(
        lane for lane in gate.FULL_LANES
        if lane.name == "admin-course-mode-playwright-chromium-desktop"
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    execution = None
    try:
        execution = stage.create_lane_execution()
        tools = execution.candidate["tools"]
        docker = Path(tools["docker"]["path"])
        compose = Path(tools["dockerCompose"]["path"])
        assert docker.is_file() and docker.is_relative_to(stage.root)
        assert compose.is_file() and compose.is_relative_to(stage.root)
        assert not docker.is_relative_to(execution.root)
        assert not compose.is_relative_to(execution.root)
        environment = gate._child_environment(execution.candidate, {}, lane)
        environment.update(execution.environment)
        assert environment["TBOT_DOCKER_EXECUTABLE"] == str(docker)
        assert environment["TBOT_DOCKER_COMPOSE_EXECUTABLE"] == str(compose)
        result = gate.run_bounded_command(
            [str(compose), "version"], cwd=Path("/"), env=environment,
            timeout_sec=5.0, max_output_bytes=4096,
        )
        assert result.error is None and result.returncode == 0
        assert result.stdout.strip() == tools["dockerCompose"]["version"]
    finally:
        if execution is not None:
            assert execution.cleanup() is True
        assert stage.cleanup() is True


def test_playwright_lane_builds_candidate_backend_before_read_only_authority(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate)
    backend_source = Path(candidate["repositories"]["backend"]["path"])
    ignored = backend_source / ".gitignore"
    ignored.write_text(ignored.read_text(encoding="utf-8") + "dist/\n", encoding="utf-8")
    _git(backend_source, "add", ".gitignore")
    _git(backend_source, "commit", "-m", "ignore compiler output")
    candidate["repositories"]["backend"].update(_repository(backend_source))
    _refresh_image_reference(candidate, "backend")
    host_output = backend_source / "dist/lessons/course-mode/curriculum-course-mode.js"
    host_output.parent.mkdir(parents=True)
    host_output.write_text("ambient host output", encoding="utf-8")
    monkeypatch.setenv("HOST_BUILD_POISON", "must-not-reach-build")
    build_calls: list[tuple[list[str], Path, dict[str, str]]] = []
    original_run = gate._manifest.run_bounded_command

    def capture_build(command, **kwargs):
        if command[-2:] == ["run", "build"]:
            build_calls.append((command, kwargs["cwd"], kwargs["env"]))
        return original_run(command, **kwargs)

    monkeypatch.setattr(gate._manifest, "run_bounded_command", capture_build)
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-playwright-chromium-desktop"
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    try:
        staged_backend = Path(stage.candidate["repositories"]["backend"]["path"])
        staged_node = stage.candidate["tools"]["node"]["backend"]
        build_runtime = stage.root / "build-runtime"
        assert build_calls == [(
            [
                "/usr/bin/sandbox-exec", "-p", gate.BACKEND_BUILD_SANDBOX_PROFILE,
                "-D", f"BACKEND_ROOT={staged_backend}",
                "-D", f"BUILD_RUNTIME={build_runtime}",
                staged_node["executable"], staged_node["npm"]["entrypoint"], "run", "build",
            ],
            staged_backend,
            {
                **gate.BASE_ENVIRONMENT,
                "PATH": f"{Path(staged_node['executable']).parent}:{gate.SECURE_PATH}",
                "HOME": str(build_runtime / "home"),
                "TMPDIR": str(build_runtime / "tmp"),
                "XDG_CACHE_HOME": str(build_runtime / "cache"),
                "npm_config_cache": str(build_runtime / "cache/npm"),
            },
        )]
        output_root = staged_backend / "dist/lessons/course-mode"
        assert (output_root / "curriculum-course-mode.js").read_text() == "candidate curriculum"
        assert (output_root / "curriculum-6month.js").read_text() == "candidate six month"
        assert (output_root / "course-mode.contract.js").read_text() == "candidate contract"
        assert host_output.read_text(encoding="utf-8") == "ambient host output"
        assert all(
            path.is_file() and not path.is_symlink() and not path.stat().st_mode & 0o222
            for path in output_root.iterdir()
        )
        assert gate._backend_snapshot_environment(stage) is not None
    finally:
        assert stage.cleanup() is True


def test_playwright_lane_uses_stable_backend_authority_environment(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate)
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    lane = gate.Lane(
        "admin-course-mode-playwright-stable-backend", "adminEsp", "main/manager-web",
        ("node", "probe.js"), 5.0,
    )
    observed: dict[str, str] = {}

    def capture_lane(_command, **kwargs):
        observed.update(kwargs["env"])
        return gate._manifest.BoundedCommandResult(0, "", None)

    monkeypatch.setattr(gate, "playwright_browsers_authorized", lambda _candidate: True)
    monkeypatch.setattr(gate, "run_bounded_command", capture_lane)

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "PASS", result
    stable_backend = Path(observed["TBOT_BACKEND_WORKTREE"])
    assert stable_backend == Path(observed["COURSE_MODE_BACKEND_ROOT"])
    assert stable_backend.parent.parent.name.startswith("course-mode-stage-")
    assert not stable_backend.is_relative_to(Path(observed["HOME"]).parents[1])
    assert observed["COURSE_MODE_BACKEND_SNAPSHOT_AUTHORITY_SHA256"]


def test_assignment_lane_rejects_missing_staged_backend_compiler_output(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate, omit="course-mode.contract.js")
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-assignment-new"
    )

    with pytest.raises(ValueError, match="staged backend compiler output mismatch"):
        gate.stage_execution_candidate(candidate, (lane,))


def test_playwright_backend_build_cannot_write_staged_admin_repository(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate, escape="adminEsp")
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-playwright-chromium-desktop"
    )
    stage = None
    try:
        with pytest.raises(ValueError, match="staged backend compiler failed"):
            stage = gate.stage_execution_candidate(candidate, (lane,))
    finally:
        if stage is not None:
            assert stage.cleanup() is True


def test_playwright_backend_build_cannot_write_external_sentinel(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    sentinel = tmp_path / "outside-stage-sentinel"
    _configure_backend_build_fixture(candidate, escape=sentinel)
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-playwright-chromium-desktop"
    )
    stage = None
    try:
        with pytest.raises(ValueError, match="staged backend compiler failed"):
            stage = gate.stage_execution_candidate(candidate, (lane,))
    finally:
        if stage is not None:
            assert stage.cleanup() is True
    assert not sentinel.exists()


def test_playwright_backend_build_cannot_reach_unix_socket(
    candidate_file: Path,
) -> None:
    socket_root = Path(tempfile.mkdtemp(prefix="course-mode-build-socket-", dir="/private/tmp"))
    socket_path = socket_root / "listener.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    listener.settimeout(0.25)
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate, unix_socket=socket_path)
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-playwright-chromium-desktop"
    )
    stage = None
    error = None
    received = b""
    try:
        try:
            stage = gate.stage_execution_candidate(candidate, (lane,))
        except ValueError as build_error:
            error = build_error
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            pass
        else:
            with connection:
                received = connection.recv(1024)
    finally:
        if stage is not None:
            assert stage.cleanup() is True
        listener.close()
        shutil.rmtree(socket_root)

    assert error is not None and "staged backend compiler failed" in str(error)
    assert received == b""


def test_playwright_backend_build_fails_closed_without_supported_sandbox(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate)
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-playwright-chromium-desktop"
    )
    monkeypatch.setattr(gate.sys, "platform", "linux")
    assert gate._sandboxed_backend_build_command(
        ("node", "npm-cli.js", "run", "build"),
        Path("/private/tmp/course-mode-stage-test/repositories/backend"),
        Path("/private/tmp/course-mode-stage-test/build-runtime"),
    ) is None
    monkeypatch.setattr(gate.sys, "platform", "darwin")
    monkeypatch.setattr(gate, "_sandboxed_backend_build_command", lambda *_args: None)

    with pytest.raises(ValueError, match="staged backend compiler sandbox unavailable"):
        gate.stage_execution_candidate(candidate, (lane,))


def test_playwright_lane_blocks_post_run_stable_backend_mutation(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate)
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    lane = gate.Lane(
        "admin-course-mode-playwright-post-mutation", "adminEsp", "main/manager-web",
        ("node", "probe.js"), 5.0,
    )

    def mutate_backend(_command, **kwargs):
        output = (
            Path(kwargs["env"]["TBOT_BACKEND_WORKTREE"])
            / "dist/lessons/course-mode/course-mode.contract.js"
        )
        output.chmod(0o644)
        output.write_text("mutated after authority", encoding="utf-8")
        return gate._manifest.BoundedCommandResult(0, "", None)

    monkeypatch.setattr(gate, "playwright_browsers_authorized", lambda _candidate: True)
    monkeypatch.setattr(gate, "run_bounded_command", mutate_backend)

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "BLOCKED", result
    assert result["failedLane"] == lane.name
    assert result["lanes"][0]["exitCode"] is None


def test_playwright_lane_stages_bound_browser_cache_and_sets_only_its_environment(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _add_node_install(candidate, "adminEsp", "main/manager-web", "adminManagerWeb")
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate)
    lane = next(
        lane for lane in gate.FULL_LANES
        if lane.name == "admin-course-mode-playwright-webkit-desktop"
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    execution = None
    try:
        execution = stage.create_lane_execution()
        environment = gate._child_environment(execution.candidate, {}, lane)
        browser_root = Path(environment["PLAYWRIGHT_BROWSERS_PATH"])
        assert browser_root.is_relative_to(stage.root)
        assert not browser_root.is_relative_to(execution.root)
        for descriptor in execution.candidate["tools"]["playwrightBrowsers"].values():
            root = Path(descriptor["root"])
            assert root.parent == browser_root
            assert (root / descriptor["executable"]).is_file()

        non_playwright_lane = next(
            item for item in gate.FULL_LANES if item.name == "admin-build"
        )
        assert "PLAYWRIGHT_BROWSERS_PATH" not in gate._child_environment(
            execution.candidate, {}, non_playwright_lane,
        )
    finally:
        if execution is not None:
            assert execution.cleanup() is True
        assert stage.cleanup() is True


def test_playwright_browser_authority_survives_stage_read_only_normalization(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(
        candidate, "adminEsp", "main/manager-web", "adminManagerWeb",
    )
    _add_node_install(candidate, "backend", ".", "backend")
    _configure_backend_build_fixture(candidate)
    metadata = install / "playwright-core/browsers.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"browsers": [
        {"name": engine, "revision": descriptor["revision"]}
        for engine, descriptor in candidate["tools"]["playwrightBrowsers"].items()
    ]}), encoding="utf-8")
    lock = Path(candidate["repositories"]["adminEsp"]["path"]) / "main/manager-web/package-lock.json"
    candidate["tools"]["nodeInstalls"]["adminManagerWeb"] = gate.describe_node_install(
        install, lock,
    )
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-playwright-webkit-desktop"
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    execution = None
    try:
        execution = stage.create_lane_execution()
        assert gate.playwright_browsers_authorized(execution.candidate) is True
    finally:
        if execution is not None:
            assert execution.cleanup() is True
        assert stage.cleanup() is True


def test_playwright_browser_authority_matches_candidate_node_metadata(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    root = Path(candidate["repositories"]["adminEsp"]["path"])
    metadata = root / "main/manager-web/node_modules/playwright-core/browsers.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"browsers": [
        {"name": engine, "revision": descriptor["revision"]}
        for engine, descriptor in candidate["tools"]["playwrightBrowsers"].items()
    ]}), encoding="utf-8")

    assert gate.playwright_browsers_authorized(candidate) is True

    candidate["tools"]["playwrightBrowsers"]["webkit"]["revision"] = "9999"
    assert gate.playwright_browsers_authorized(candidate) is False


def test_playwright_browser_authority_rejects_coherent_revision_drift(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    descriptor = candidate["tools"]["playwrightBrowsers"]["webkit"]
    original = Path(descriptor["root"])
    renamed = original.with_name("webkit-9999")
    original.rename(renamed)
    descriptor.update({"revision": "9999", "root": str(renamed)})
    root = Path(candidate["repositories"]["adminEsp"]["path"])
    metadata = root / "main/manager-web/node_modules/playwright-core/browsers.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({"browsers": [
        {"name": engine, "revision": item["revision"]}
        for engine, item in candidate["tools"]["playwrightBrowsers"].items()
    ]}), encoding="utf-8")

    assert gate.playwright_browsers_authorized(candidate) is False


def test_admin_browser_authority_requires_playwright_metadata_revision(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    root = Path(candidate["repositories"]["adminEsp"]["path"])
    metadata = root / "main/manager-web/node_modules/playwright-core/browsers.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({
        "browsers": [{"name": "chromium-headless-shell", "revision": "9999"}],
    }), encoding="utf-8")

    assert gate.robot_preview_browser_authorized(candidate) is False


def test_admin_browser_authority_accepts_exact_binary_and_playwright_revision(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    root = Path(candidate["repositories"]["adminEsp"]["path"])
    metadata = root / "main/manager-web/node_modules/playwright-core/browsers.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({
        "browsers": [{"name": "chromium-headless-shell", "revision": "1223"}],
    }), encoding="utf-8")

    assert gate.robot_preview_browser_authorized(candidate) is True


def test_admin_browser_authority_accepts_playwright_linux_arm64_layout(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    descriptor = candidate["tools"]["robotPreviewBrowser"]
    original_root = Path(descriptor["root"])
    browser = original_root.parents[1] / "chromium_headless_shell-1223/chrome-linux/headless_shell"
    browser.parent.mkdir(parents=True)
    (original_root / descriptor["executable"]).rename(browser)
    tree, error = gate.secure_browser_bundle_descriptor(browser.parent)
    assert error is None and tree is not None
    descriptor.update({
        "root": str(browser.parent),
        "executable": browser.name,
        "treeDigest": tree,
    })
    root = Path(candidate["repositories"]["adminEsp"]["path"])
    metadata = root / "main/manager-web/node_modules/playwright-core/browsers.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({
        "browsers": [{"name": "chromium-headless-shell", "revision": "1223"}],
    }), encoding="utf-8")
    monkeypatch.setattr(gate.sys, "platform", "linux")
    monkeypatch.setattr(gate.os, "uname", lambda: type("Uname", (), {"machine": "aarch64"})())

    assert gate.robot_preview_browser_authorized(candidate) is True


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


def test_original_repository_drift_does_not_change_staged_lane(candidate_file: Path) -> None:
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

    assert result["verdict"] == "PASS"
    assert [lane["name"] for lane in result["lanes"]] == ["drift", "dependent"]


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
        "import os;from pathlib import Path;"
        f"assert os.environ['HOME']!={str(tmp_path)!r};"
        "assert Path(os.environ['HOME']).is_dir();"
        "assert os.access(os.environ['HOME'], os.W_OK);"
        "assert Path(os.environ['TMPDIR']).is_dir();"
        "assert Path(os.environ['XDG_CACHE_HOME']).is_dir();"
        f"assert os.environ['PATH']=={gate.SECURE_PATH!r};"
        "assert 'TOP_SECRET' not in os.environ"
    )

    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("environment", code),),
        source_environment={"PATH": str(shadow), "HOME": str(tmp_path), "TOP_SECRET": "secret"},
    )

    assert result["verdict"] == "PASS"


def test_runtime_gate_must_be_the_candidate_admin_checkout(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    attestation = _write_operator_attestation(candidate_file)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

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


def test_last_lane_original_repository_drift_does_not_invalidate_snapshot(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    tracked = Path(candidate["repositories"]["adminEsp"]["path"]) / "tracked.txt"
    lane = _lane(
        "last",
        f"from pathlib import Path;Path({str(tracked)!r}).write_text('mutated')",
    )

    result = gate.run_gate(candidate_file, "quick", lanes=(lane,))

    assert result["verdict"] == "PASS"
    assert result["lanes"][0]["exitCode"] == 0


def test_report_can_pass_when_only_original_repository_drifts_after_snapshot(
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

    assert result["verdict"] == "PASS"
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_report_write_does_not_revalidate_mutable_original_repository(
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

    assert result["verdict"] == "PASS"
    assert writes == 1
    assert json.loads(report.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_original_repository_drift_does_not_require_corrective_report(
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

    assert result["verdict"] == "PASS"
    assert report.exists()


@pytest.mark.parametrize("name", ["00-candidate-validator.json", "01-quick-gate.json"])
def test_preexisting_evidence_target_is_rejected_before_lane_and_preserved(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    report = tmp_path / "evidence" / name
    original = b'{"validator":"existing"}\n'
    report.write_bytes(original)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *_args, **_kwargs: pytest.fail("lane must not run for an existing report target"),
    )

    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("must-not-run", "raise SystemExit(0)"),),
        report_path=report,
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert report.read_bytes() == original


def test_case_variant_existing_evidence_target_is_rejected_before_lane_and_preserved(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = tmp_path / "evidence/01-QUICK-GATE.json"
    report_alias = existing.with_name(existing.name.swapcase())
    original = b'{"verdict":"PASS"}\n'
    existing.write_bytes(original)
    try:
        aliases_existing = os.path.samefile(report_alias, existing)
    except FileNotFoundError:
        aliases_existing = False
    if not aliases_existing:
        pytest.skip("filesystem is case-sensitive")
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *_args, **_kwargs: pytest.fail("lane must not run for an existing report alias"),
    )

    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("must-not-run", "raise SystemExit(0)"),),
        report_path=report_alias,
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert existing.read_bytes() == original


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


@pytest.mark.parametrize("insecure_component", ["evidence", "nested"])
def test_report_destination_rejects_group_or_other_writable_parent_chain(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    insecure_component: str,
) -> None:
    evidence = tmp_path / "evidence"
    nested = evidence / "nested"
    nested.mkdir()
    (evidence if insecure_component == "evidence" else nested).chmod(0o777)
    monkeypatch.setattr(
        gate, "run_bounded_command",
        lambda *_args, **_kwargs: pytest.fail("lane must not run with an insecure report parent"),
    )

    result = gate.run_gate(
        candidate_file,
        "quick",
        lanes=(_lane("must-not-run", "raise SystemExit(0)"),),
        report_path=nested / "report.json",
    )

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "report"
    assert not (nested / "report.json").exists()


def test_report_destination_rejects_parent_chain_not_owned_by_effective_uid(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    evidence = tmp_path / "evidence"
    nested = evidence / "nested"
    nested.mkdir()
    monkeypatch.setattr(gate.os, "geteuid", lambda: os.getuid() + 1)

    destination = gate._prepare_report_destination(
        candidate_file, candidate, nested / "report.json",
    )

    if destination is not None:
        os.close(destination.parent_fd)
    assert destination is None


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
        "tests/test_course_mode_operator_attestation.py",
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
    _refresh_image_reference(candidate, "adminEsp")
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


def test_esp_python_lane_stages_attested_backend_node_runtime(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    install = _add_node_install(candidate, "backend", ".", "backend")
    vite_node = install / ".bin/vite-node"
    vite_node.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    vite_node.chmod(0o755)
    (install / "fixture-link").symlink_to("fixture-package", target_is_directory=True)
    candidate["tools"]["nodeInstalls"]["backend"] = gate.describe_node_install(
        install, install.parent / "package-lock.json",
    )
    lane = gate.QUICK_LANES[2]

    assert gate._node_install_requirement(lane) == ("backend", ".")
    stage = gate.stage_execution_candidate(candidate, (lane,))
    try:
        staged_backend = Path(stage.candidate["repositories"]["backend"]["path"])
        staged_node = Path(stage.candidate["tools"]["node"]["backend"]["executable"])
        assert (staged_backend / "node_modules/.bin/vite-node").exists()
        assert staged_node.is_file()
        source_before, source_before_error = (
            gate._manifest.secure_backend_snapshot_tree_descriptor(staged_backend)
        )
        before, before_error = gate._manifest.secure_backend_execution_tree_descriptor(staged_backend)
        binding = gate._backend_snapshot_environment(stage)
        assert binding is not None
        assert gate._backend_execution_snapshot_matches(stage, binding) is True
        modules = staged_backend / "node_modules"
        link = modules / "fixture-link"
        modules.chmod(0o755)
        link.unlink()
        link.symlink_to(".bin", target_is_directory=True)
        modules.chmod(0o555)
        source_after, source_after_error = (
            gate._manifest.secure_backend_snapshot_tree_descriptor(staged_backend)
        )
        after, after_error = gate._manifest.secure_backend_execution_tree_descriptor(staged_backend)
        assert source_before_error is None and source_after_error is None
        assert source_before == source_after
        assert before_error is None and after_error is None and before != after
        authority = stage.root / ".course-mode-authority/backend.json"
        document = json.loads(authority.read_text(encoding="utf-8"))
        document["executionTreeDigest"] = after
        authority.parent.chmod(0o755)
        authority.chmod(0o644)
        authority.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        authority.chmod(0o444)
        authority.parent.chmod(0o555)
        assert gate._backend_execution_snapshot_matches(stage, binding) is False
    finally:
        assert stage.cleanup() is True


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
    _refresh_image_reference(candidate, "adminEsp")
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

    assert result["verdict"] == "PASS"


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
    assert calls == 5


def test_original_install_mutation_between_lanes_blocks_fresh_snapshot(
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
    assert result["failedLane"] == "snapshot"
    assert not marker.exists()


@pytest.mark.parametrize("repository_name", ["backend", "firmware"])
def test_physical_preflight_rejects_dirty_cross_repository_authority_before_command(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    repository_name: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    _commit_then_dirty(candidate, repository_name, "runtime-dependency.txt")
    candidate_file.write_text(json.dumps(candidate), encoding="utf-8")
    attestation = _write_operator_attestation(candidate_file)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
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
    attestation = _write_operator_attestation(candidate_file)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))
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


def test_firmware_handler_environment_binds_candidate_esp_idf_cjson(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    cjson = Path(candidate["tools"]["espIdf"]["root"]) / "components/json/cJSON"

    environment = gate._child_environment(
        candidate, {"CJSON_DIR": "/hostile/ambient/cjson"}, lane,
    )

    assert environment is not None
    assert environment["CJSON_DIR"] == str(cjson.resolve(strict=True))


@pytest.mark.parametrize("lane_name", ["firmware-renderer", "firmware-backward-compatibility"])
def test_non_handler_firmware_environment_does_not_receive_cjson(
    candidate_file: Path, lane_name: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == lane_name)

    environment = gate._child_environment(
        candidate, {"CJSON_DIR": "/hostile/ambient/cjson"}, lane,
    )

    assert environment is not None
    assert "CJSON_DIR" not in environment


def test_firmware_handler_environment_rejects_missing_cjson_source(candidate_file: Path) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    cjson_source = (
        Path(candidate["tools"]["espIdf"]["root"]) / "components/json/cJSON/cJSON.c"
    )
    cjson_source.unlink()

    assert gate._child_environment(candidate, {}, lane) is None


def test_firmware_handler_environment_rejects_cjson_directory_symlink(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    cjson = Path(candidate["tools"]["espIdf"]["root"]) / "components/json/cJSON"
    cjson.rename(cjson.with_name("real-cjson"))
    cjson.symlink_to(cjson.with_name("real-cjson"), target_is_directory=True)

    assert gate._child_environment(candidate, {}, lane) is None


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_firmware_handler_environment_rejects_non_regular_cjson_source(
    candidate_file: Path, tmp_path: Path, replacement: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    cjson_source = (
        Path(candidate["tools"]["espIdf"]["root"]) / "components/json/cJSON/cJSON.c"
    )
    cjson_source.unlink()
    if replacement == "symlink":
        target = tmp_path / "other-cJSON.c"
        target.write_text("/* hostile replacement */\n", encoding="utf-8")
        cjson_source.symlink_to(target)
    else:
        cjson_source.mkdir()

    assert gate._child_environment(candidate, {}, lane) is None


@pytest.mark.parametrize("root_kind", ["relative", "symlink"])
def test_firmware_handler_environment_rejects_noncanonical_esp_idf_root(
    candidate_file: Path, tmp_path: Path, root_kind: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    esp_idf = Path(candidate["tools"]["espIdf"]["root"])
    if root_kind == "relative":
        candidate["tools"]["espIdf"]["root"] = "relative/esp-idf"
    else:
        alias = tmp_path / "esp-idf-alias"
        alias.symlink_to(esp_idf, target_is_directory=True)
        candidate["tools"]["espIdf"]["root"] = str(alias)

    assert gate._child_environment(candidate, {}, lane) is None


def test_firmware_handler_environment_rejects_esp_idf_root_under_symlink_loop(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    esp_idf = Path(candidate["tools"]["espIdf"]["root"])
    real_resolve = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        if path == esp_idf:
            raise RuntimeError("Symlink loop from candidate ESP-IDF root")
        return real_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)

    assert gate._child_environment(candidate, {}, lane) is None


def test_firmware_handler_command_receives_usable_candidate_cjson_with_nonexistent_home(
    candidate_file: Path,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    expected_cjson = (
        Path(candidate["tools"]["espIdf"]["root"]) / "components/json/cJSON"
    ).resolve(strict=True)
    command = (
        sys.executable, "-c",
        "import os,pathlib;"
        "root=pathlib.Path(os.environ['CJSON_DIR']);"
        "print(os.environ['HOME']);"
        "print(root.resolve(strict=True));"
        "print((root/'cJSON.c').read_text().strip())",
    )
    environment = gate._child_environment(candidate, {}, lane)
    result = gate.run_bounded_command(
        list(command), cwd=Path(candidate["repositories"]["firmware"]["path"]),
        timeout_sec=5.0, max_output_bytes=4096, env=environment,
    )

    assert result.error is None and result.returncode == 0
    assert result.stdout.splitlines() == [
        "/nonexistent", str(expected_cjson), "/* candidate ESP-IDF cJSON fixture */",
    ]


def test_firmware_handler_stages_exact_cjson_gitlink_commit(
    candidate_file: Path, tmp_path: Path,
) -> None:
    candidate, _, submodule_root, _, _ = _install_cjson_gitlink(candidate_file, tmp_path)
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    assert (submodule_root / "cJSON.c").read_text(encoding="utf-8") == (
        "different checked-out bytes\n"
    )

    stage = gate.stage_execution_candidate(candidate, (lane,))
    staged_cjson = (
        Path(stage.candidate["tools"]["espIdf"]["root"])
        / "components/json/cJSON/cJSON.c"
    )
    try:
        assert staged_cjson.is_relative_to(stage.root / "tools")
        assert staged_cjson.read_text(encoding="utf-8") == "pinned gitlink bytes\n"
    finally:
        assert stage.cleanup() is True
    assert not stage.root.exists()


def test_firmware_handler_cjson_gitlink_rejects_missing_initialized_submodule(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, _, submodule_root, _, _ = _install_cjson_gitlink(candidate_file, tmp_path)
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    shutil.rmtree(submodule_root)
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


def test_firmware_handler_cjson_gitlink_rejects_absent_pinned_commit(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, esp_idf, _, pinned_commit, _ = _install_cjson_gitlink(
        candidate_file, tmp_path,
    )
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    git_dir = esp_idf / ".git/modules/components/json/cJSON"
    shutil.rmtree(git_dir)
    _git(git_dir.parent, "init", "--bare", str(git_dir))
    assert subprocess.run(
        ["git", f"--git-dir={git_dir}", "cat-file", "-e", pinned_commit],
        check=False, capture_output=True,
    ).returncode != 0
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize("object_kind", ["blob", "tree", "annotated-tag"])
def test_firmware_handler_cjson_gitlink_rejects_existing_non_commit_object(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    object_kind: str,
) -> None:
    candidate, esp_idf, submodule_root, _, checkout_commit = _install_cjson_gitlink(
        candidate_file, tmp_path,
    )
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    blob_id = _git(submodule_root, "hash-object", "-w", "cJSON.c")
    if object_kind == "blob":
        object_id = blob_id
    elif object_kind == "tree":
        object_id = _git(submodule_root, "rev-parse", f"{checkout_commit}^{{tree}}")
    else:
        _git(
            submodule_root, "tag", "-a", "non-commit-object", blob_id,
            "-m", "annotated tag targeting a blob",
        )
        object_id = _git(submodule_root, "rev-parse", "refs/tags/non-commit-object")
    assert _git(submodule_root, "cat-file", "-t", object_id) == object_kind.removeprefix(
        "annotated-"
    )
    _git(
        esp_idf, "update-index", "--add", "--cacheinfo", "160000", object_id,
        "components/json/cJSON",
    )
    _git(esp_idf, "commit", "-m", f"point cjson gitlink at {object_kind}")
    candidate["tools"]["espIdf"]["commit"] = _git(esp_idf, "rev-parse", "HEAD")
    entry = _git(esp_idf, "ls-tree", "HEAD", "components/json/cJSON")
    assert entry.split()[:3] == ["160000", "commit", object_id]
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize("root_kind", ["symlink", "noncanonical"])
def test_firmware_handler_cjson_gitlink_rejects_untrusted_submodule_root(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    root_kind: str,
) -> None:
    candidate, _, submodule_root, _, _ = _install_cjson_gitlink(candidate_file, tmp_path)
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    if root_kind == "symlink":
        real_root = submodule_root.with_name("cJSON-real")
        submodule_root.rename(real_root)
        submodule_root.symlink_to(real_root, target_is_directory=True)
    else:
        json_root = submodule_root.parent
        real_json_root = json_root.with_name("json-real")
        json_root.rename(real_json_root)
        json_root.symlink_to(real_json_root, target_is_directory=True)
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize("git_file_kind", ["symlink", "directory"])
def test_firmware_handler_cjson_gitlink_rejects_non_regular_git_file(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    git_file_kind: str,
) -> None:
    candidate, _, submodule_root, _, _ = _install_cjson_gitlink(candidate_file, tmp_path)
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    git_file = submodule_root / ".git"
    original = git_file.with_name(".git-original")
    git_file.rename(original)
    if git_file_kind == "symlink":
        git_file.symlink_to(original)
    else:
        git_file.mkdir()
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize(
    "git_file_content",
    [
        "{redirect}",
        "{absolute}",
        "{relative}unexpected second line\n",
        "{relative}\0",
        "",
        "gitdir: " + "x" * 4097 + "\n",
        "worktree: ../../../../.git/modules/components/json/cJSON\n",
    ],
    ids=("redirected", "absolute", "extra-line", "nul", "empty", "oversized", "malformed"),
)
def test_firmware_handler_cjson_gitlink_rejects_invalid_git_file_record(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    git_file_content: str,
) -> None:
    candidate, esp_idf, submodule_root, _, _ = _install_cjson_gitlink(
        candidate_file, tmp_path,
    )
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    git_file = submodule_root / ".git"
    relative = git_file.read_text(encoding="utf-8")
    canonical = esp_idf / ".git/modules/components/json/cJSON"
    redirect = tmp_path / "redirected-cjson.git"
    _git(tmp_path, "init", "--bare", str(redirect))
    content = git_file_content.format(
        absolute=f"gitdir: {canonical}\n", relative=relative,
        redirect=f"gitdir: {os.path.relpath(redirect, submodule_root)}\n",
    )
    git_file.write_bytes(content.encode("utf-8"))
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize("git_dir_kind", ["symlink", "file"])
def test_firmware_handler_cjson_gitlink_rejects_untrusted_canonical_git_dir(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    git_dir_kind: str,
) -> None:
    candidate, esp_idf, _, _, _ = _install_cjson_gitlink(candidate_file, tmp_path)
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    git_dir = esp_idf / ".git/modules/components/json/cJSON"
    original = git_dir.with_name("cJSON-original")
    git_dir.rename(original)
    if git_dir_kind == "symlink":
        git_dir.symlink_to(original, target_is_directory=True)
    else:
        git_dir.write_text("not a git directory\n", encoding="utf-8")
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize("target_name", ["root", "git-file", "git-dir"])
@pytest.mark.parametrize("writable_bit", [stat.S_IWGRP, stat.S_IWOTH])
def test_firmware_handler_cjson_gitlink_rejects_shared_writable_metadata(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    target_name: str, writable_bit: int,
) -> None:
    candidate, esp_idf, submodule_root, _, _ = _install_cjson_gitlink(
        candidate_file, tmp_path,
    )
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    targets = {
        "root": submodule_root,
        "git-file": submodule_root / ".git",
        "git-dir": esp_idf / ".git/modules/components/json/cJSON",
    }
    target = targets[target_name]
    target.chmod(stat.S_IMODE(target.lstat().st_mode) | writable_bit)
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize("target_name", ["root", "git-file", "git-dir"])
def test_firmware_handler_cjson_gitlink_rejects_wrong_owner(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    target_name: str,
) -> None:
    candidate, esp_idf, submodule_root, _, _ = _install_cjson_gitlink(
        candidate_file, tmp_path,
    )
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    targets = {
        "root": submodule_root,
        "git-file": submodule_root / ".git",
        "git-dir": esp_idf / ".git/modules/components/json/cJSON",
    }
    target = targets[target_name]
    original_lstat = Path.lstat

    def wrong_owner(path: Path) -> os.stat_result:
        metadata = original_lstat(path)
        if path == target:
            return _stat_result_with_uid(metadata, os.geteuid() + 1)
        return metadata

    monkeypatch.setattr(Path, "lstat", wrong_owner)
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize(
    "mode,object_type",
    [
        ("040000", "commit"),
        ("160000", "tree"),
        ("100644", "blob"),
        ("120000", "blob"),
    ],
)
def test_firmware_handler_cjson_gitlink_rejects_unsupported_entry_pair(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    mode: str, object_type: str,
) -> None:
    candidate, _, _, pinned_commit, _ = _install_cjson_gitlink(candidate_file, tmp_path)
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    original = gate._manifest.run_bounded_command

    def invalid_entry(command, **kwargs):
        if "ls-tree" in command and command[-1] == "components/json/cJSON" and "-r" not in command:
            return gate._manifest.BoundedCommandResult(
                0,
                f"{mode} {object_type} {pinned_commit}\tcomponents/json/cJSON\0",
                None,
            )
        return original(command, **kwargs)

    monkeypatch.setattr(gate._manifest, "run_bounded_command", invalid_entry)
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


@pytest.mark.parametrize(
    "listing",
    [
        "{valid}{valid}",
        "{record}",
        "160000 commit {oid}\tcomponents/json/not-cJSON\0",
        "160000 commit {upper}\tcomponents/json/cJSON\0",
    ],
    ids=("duplicate", "unterminated", "wrong-path", "uppercase-oid"),
)
def test_firmware_handler_cjson_gitlink_rejects_malformed_ls_tree_record(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    listing: str,
) -> None:
    candidate, _, _, pinned_commit, _ = _install_cjson_gitlink(candidate_file, tmp_path)
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    valid = f"160000 commit {pinned_commit}\tcomponents/json/cJSON\0"
    rendered = listing.format(
        valid=valid, record=valid.removesuffix("\0"), oid=pinned_commit,
        upper=pinned_commit.upper(),
    )
    original = gate._manifest.run_bounded_command

    def malformed_entry(command, **kwargs):
        if "ls-tree" in command and command[-1] == "components/json/cJSON" and "-r" not in command:
            return gate._manifest.BoundedCommandResult(0, rendered, None)
        return original(command, **kwargs)

    monkeypatch.setattr(gate._manifest, "run_bounded_command", malformed_entry)
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)


def test_firmware_handler_cjson_gitlink_disables_lazy_fetch_and_ext_transport(
    candidate_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, esp_idf, _, pinned_commit, _ = _install_cjson_gitlink(
        candidate_file, tmp_path,
    )
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    git_dir = esp_idf / ".git/modules/components/json/cJSON"
    shutil.rmtree(git_dir)
    _git(git_dir.parent, "init", "--bare", str(git_dir))
    marker = tmp_path / "lazy-fetch-marker"
    helper = tmp_path / "hostile-transport"
    helper.write_text(f"#!/bin/sh\ntouch {str(marker)!r}\nexit 1\n", encoding="utf-8")
    helper.chmod(0o755)
    _git(git_dir, "config", "extensions.partialClone", "origin")
    _git(git_dir, "config", "remote.origin.promisor", "true")
    _git(git_dir, "config", "remote.origin.partialCloneFilter", "blob:none")
    _git(git_dir, "config", "remote.origin.url", f"ext::{helper}")
    probe = subprocess.run(
        [
            "git", "-c", "protocol.ext.allow=always", f"--git-dir={git_dir}",
            "cat-file", "-e", f"{pinned_commit}^{{commit}}",
        ],
        check=False, capture_output=True,
    )
    assert probe.returncode != 0 and marker.exists()
    marker.unlink()
    stage_roots = _record_stage_roots(monkeypatch)

    _assert_gitlink_stage_rejected(candidate, lane, stage_roots)
    assert not marker.exists()


@pytest.mark.parametrize("external_change", ["mutate", "unlink", "symlink"])
def test_firmware_handler_lane_reads_staged_committed_cjson_after_external_change(
    candidate_file: Path, tmp_path: Path, external_change: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    external_source = (
        Path(candidate["tools"]["espIdf"]["root"]) / "components/json/cJSON/cJSON.c"
    )
    stage = gate.stage_execution_candidate(candidate, (lane,))
    execution = None
    try:
        if external_change == "mutate":
            external_source.write_text("hostile external bytes\n", encoding="utf-8")
        else:
            external_source.unlink()
            if external_change == "symlink":
                hostile = tmp_path / "hostile-cJSON.c"
                hostile.write_text("hostile symlink bytes\n", encoding="utf-8")
                external_source.symlink_to(hostile)
        execution = stage.create_lane_execution()
        environment = gate._child_environment(execution.candidate, {}, lane)
        staged_cjson = Path(environment["CJSON_DIR"])
        command = (
            sys.executable, "-c",
            "import os,pathlib;"
            "root=pathlib.Path(os.environ['CJSON_DIR']);"
            "print(root);print((root/'cJSON.c').read_text().strip())",
        )

        result = gate.run_bounded_command(
            list(command),
            cwd=Path(execution.candidate["repositories"]["firmware"]["path"]),
            timeout_sec=5.0, max_output_bytes=4096, env=environment,
        )

        assert staged_cjson.is_relative_to(execution.root / "candidate")
        assert result.error is None and result.returncode == 0
        assert result.stdout.splitlines() == [
            str(staged_cjson), "/* candidate ESP-IDF cJSON fixture */",
        ]
        assert "hostile" not in result.stdout
    finally:
        if execution is not None:
            assert execution.cleanup() is True
        assert stage.cleanup() is True


@pytest.mark.parametrize("subtree_kind", ["missing", "blob"])
def test_firmware_handler_staging_rejects_invalid_committed_cjson_subtree(
    candidate_file: Path, subtree_kind: str,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(item for item in gate.FULL_LANES if item.name == "firmware-handler")
    esp_idf = Path(candidate["tools"]["espIdf"]["root"])
    cjson = esp_idf / "components/json/cJSON"
    shutil.rmtree(cjson)
    if subtree_kind == "blob":
        cjson.write_text("not a tree\n", encoding="utf-8")
    _git(esp_idf, "add", "-A")
    _git(esp_idf, "commit", "-m", f"make cJSON subtree {subtree_kind}")
    candidate["tools"]["espIdf"]["commit"] = _git(esp_idf, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="candidate archive failed"):
        gate.stage_execution_candidate(candidate, (lane,))


def test_assignment_lane_identity_is_derived_only_from_candidate(
    candidate_file: Path, tmp_path: Path,
) -> None:
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
        "LESSON_STUDIO_E2E_BACKEND_HOST_PORT": "13100",
        "LESSON_STUDIO_E2E_WEB_HOST_PORT": "18102",
        "TASK4_ASSIGNMENT_MEDIA_HOST_PORT": "28443",
    }

    environment = gate._child_environment(
        candidate,
        hostile,
        lane,
        assignment_runtime_capsule_root=tmp_path / "owner",
        assignment_runtime_root=tmp_path / "owner/runtime",
    )

    assert environment["TBOT_BACKEND_WORKTREE"] == candidate["repositories"]["backend"]["path"]
    assert environment["TBOT_FIRMWARE_WORKTREE"] == candidate["repositories"]["firmware"]["path"]
    assert environment["TBOT_LESSON_STUDIO_BACKEND_IMAGE"] == candidate["images"]["lessonStudioBackend"]["id"]
    assert environment["TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID"] == "sha256:" + "1" * 64
    assert environment["TBOT_LESSON_STUDIO_WEB_IMAGE"] == candidate["images"]["lessonStudioWeb"]["id"]
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


def test_physical_preflight_requires_candidate_bound_signed_evidence(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    attestation = _write_operator_attestation(candidate_file)
    monkeypatch.setenv("COURSE_MODE_OPERATOR_ATTESTATION", str(attestation))

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
