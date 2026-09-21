#!/usr/bin/env python3
"""Run candidate-bound Course Mode production-readiness lanes."""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import importlib
import ipaddress
import json
import math
import os
import posixpath
import queue
import re
import secrets
import select
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

try:
    _manifest = importlib.import_module("scripts.course_mode_candidate_manifest")
except ModuleNotFoundError:
    _manifest = importlib.import_module("course_mode_candidate_manifest")

_scripts_directory = str(Path(__file__).resolve().parent)
sys.path.insert(0, _scripts_directory)
try:
    _software_snapshot = importlib.import_module("course_mode_software_evidence_snapshot")
    _admission = importlib.import_module("course_mode_physical_flash_admission")
    _backend_native = importlib.import_module("course_mode_backend_native_runtime")
finally:
    sys.path.remove(_scripts_directory)

_DATETIME_TYPE = datetime

MAX_CANDIDATE_BYTES = _manifest.MAX_CANDIDATE_BYTES
_repository_matches_candidate = _manifest._repository_matches_candidate
read_secure_regular = _manifest.read_secure_regular
strict_json_loads = _manifest.strict_json_loads
validate_candidate = _manifest.validate_candidate
_candidate_git = _manifest._git
secure_browser_bundle_descriptor = _manifest.secure_browser_bundle_descriptor
secure_playwright_browser_bundle_descriptor = (
    _manifest.secure_playwright_browser_bundle_descriptor
)


def _terminate_assignment_process_group(process_group: int) -> bool:
    try:
        os.killpg(process_group, signal.SIGTERM)
    except OSError as error:
        if error.errno not in {errno.EPERM, errno.ESRCH}:
            return False
    deadline = time.monotonic() + 0.25
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except OSError as error:
            if error.errno in {errno.EPERM, errno.ESRCH}:
                break
            return False
        time.sleep(0.01)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except OSError as error:
        if error.errno not in {errno.EPERM, errno.ESRCH}:
            return False
    return True


def _assignment_process_group_absent_after_reap(process_group: int) -> bool:
    deadline = time.monotonic() + 1.0
    while True:
        try:
            os.killpg(process_group, 0)
        except OSError as error:
            if error.errno == errno.ESRCH:
                return True
            if error.errno != errno.EPERM:
                return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def run_assignment_bounded_command(
    command: list[str], *, cwd: Path, timeout_sec: float, max_output_bytes: int,
    env: dict[str, str] | None = None,
) -> _manifest.BoundedCommandResult:
    if (
        not isinstance(timeout_sec, (int, float))
        or not math.isfinite(timeout_sec)
        or timeout_sec <= 0
    ):
        return _manifest.BoundedCommandResult(None, "", "invalid_timeout")
    if not all(hasattr(select, name) for name in ("kqueue", "kevent", "KQ_FILTER_PROC")):
        return _manifest.BoundedCommandResult(None, "", "containment")
    selector = None
    exit_events = None
    process = None
    buffers = {}
    pending_exception: BaseException | None = None
    try:
        selector = selectors.DefaultSelector()
        exit_events = select.kqueue()
        try:
            process = subprocess.Popen(
                command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
            )
        except OSError:
            return _manifest.BoundedCommandResult(None, "", "not_found")
        assert process.stdout is not None and process.stderr is not None
        buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
        error = None
        leader_exited = False
        exit_events.control([
            select.kevent(
                process.pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE,
                fflags=select.KQ_NOTE_EXIT,
            ),
        ], 0, 0)
        for stream in buffers:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_sec
        while not leader_exited:
            if time.monotonic() >= deadline:
                error = "timeout"
                break
            leader_exited = bool(exit_events.control(None, 1, 0))
            for key, _ in selector.select(min(0.05, max(0.0, deadline - time.monotonic()))):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                buffers[stream].extend(chunk)
                if sum(len(value) for value in buffers.values()) > max_output_bytes:
                    error = "output"
                    break
            if error:
                break
    except BaseException as caught:
        if process is None:
            raise
        error = "containment"
        if not isinstance(caught, Exception):
            pending_exception = caught
    finally:
        try:
            if process is not None:
                try:
                    if not _terminate_assignment_process_group(process.pid):
                        error = "containment"
                except BaseException as caught:
                    error = "containment"
                    if not isinstance(caught, Exception) and pending_exception is None:
                        pending_exception = caught
                    if process.returncode is None:
                        with contextlib.suppress(OSError):
                            os.killpg(process.pid, signal.SIGKILL)
                # Reap before the final non-signaling absence proof.
                try:
                    returncode = process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    try:
                        process.wait(timeout=1)
                    except (OSError, subprocess.TimeoutExpired):
                        error = "containment"
                    returncode = process.returncode
                try:
                    if not _assignment_process_group_absent_after_reap(process.pid):
                        error = "containment"
                except BaseException as caught:
                    error = "containment"
                    if not isinstance(caught, Exception) and pending_exception is None:
                        pending_exception = caught
        except BaseException as caught:
            if process is not None and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    process.wait(timeout=1)
            if process is not None:
                returncode = process.returncode
                error = "containment"
            if not isinstance(caught, Exception) and pending_exception is None:
                pending_exception = caught
        finally:
            if process is not None:
                if process.stdout is not None:
                    with contextlib.suppress(Exception):
                        process.stdout.close()
                if process.stderr is not None:
                    with contextlib.suppress(Exception):
                        process.stderr.close()
            if exit_events is not None:
                with contextlib.suppress(Exception):
                    exit_events.close()
            if selector is not None:
                with contextlib.suppress(Exception):
                    selector.close()
    if pending_exception is not None:
        raise pending_exception
    if process is None:
        return _manifest.BoundedCommandResult(None, "", "containment")
    stdout = bytes(buffers[process.stdout]).decode("utf-8", errors="replace")
    return _manifest.BoundedCommandResult(returncode, stdout, error)


def run_bounded_command(
    command: list[str], *, cwd: Path, timeout_sec: float, max_output_bytes: int,
    env: dict[str, str] | None = None, contain_process_group: bool = False,
) -> _manifest.BoundedCommandResult:
    if contain_process_group:
        return run_assignment_bounded_command(
            command, cwd=cwd, timeout_sec=timeout_sec,
            max_output_bytes=max_output_bytes, env=env,
        )
    return _manifest.run_bounded_command(
        command, cwd=cwd, timeout_sec=timeout_sec,
        max_output_bytes=max_output_bytes, env=env,
    )


SECURE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
PYTHON_LANE_SANDBOX_PROFILE = """(version 1)
(allow default)
(deny file-write* (subpath "/private/tmp"))
(allow file-write*
    (literal (param "LANE_ROOT"))
    (subpath (param "LANE_ROOT")))
"""
BACKEND_BUILD_SANDBOX_PROFILE = """(version 1)
(allow default)
(deny file-write*)
(deny network*)
(allow file-write*
    (literal "/dev/null")
    (literal (param "BACKEND_ROOT"))
    (subpath (param "BACKEND_ROOT"))
    (literal (param "BUILD_RUNTIME"))
    (subpath (param "BUILD_RUNTIME")))
"""
BASE_ENVIRONMENT = {
    "PATH": SECURE_PATH,
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "CI": "1",
    "NO_COLOR": "1",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "PAGER": "cat",
    "PYTHONNOUSERSITE": "1",
}
MAX_LANE_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 1024 * 1024
MAX_NODE_INSTALL_ENTRIES = 250_000
MAX_NODE_INSTALL_BYTES = 2 * 1024 * 1024 * 1024
MAX_NODE_INSTALL_DEPTH = 128
MAX_NODE_PROJECT_SCAN_ENTRIES = 500_000
MAX_PACKAGE_LOCK_BYTES = 32 * 1024 * 1024
MAX_ASSIGNMENT_BASE_COMPOSE_BYTES = 1024 * 1024
MAX_SNAPSHOT_ENTRIES = 750_000
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024 * 1024
MAX_SNAPSHOT_FILE_BYTES = 512 * 1024 * 1024
MAX_SNAPSHOT_DEPTH = 256
MAX_GIT_ARCHIVE_LISTING_BYTES = 64 * 1024 * 1024
GIT_BLOB_CHUNK_BYTES = 1024 * 1024
MAX_GIT_SYMLINK_BYTES = 16 * 1024
MAX_GITLINK_GIT_FILE_BYTES = 4096
GIT_OBJECT_ENV = MappingProxyType({
    **_manifest.SECURE_ENV,
    "GIT_NO_LAZY_FETCH": "1",
})
BACKEND_COMPILER_OUTPUTS = (
    "dist/lessons/course-mode/curriculum-course-mode.js",
    "dist/lessons/course-mode/curriculum-6month.js",
    "dist/lessons/course-mode/course-mode.contract.js",
)
NODE_TREE_SCHEMA = "sha256-path-mode-bytes-symlink-v1"
ROBOT_PREVIEW_BROWSER_ENVIRONMENT = {
    "root": "TBOT_ROBOT_PREVIEW_BROWSER_ROOT",
    "executable": "TBOT_ROBOT_PREVIEW_BROWSER_EXECUTABLE",
    "engine": "TBOT_ROBOT_PREVIEW_BROWSER_ENGINE",
    "revision": "TBOT_ROBOT_PREVIEW_BROWSER_REVISION",
    "treeSha256": "TBOT_ROBOT_PREVIEW_BROWSER_TREE_SHA256",
    "treeEntryCount": "TBOT_ROBOT_PREVIEW_BROWSER_TREE_ENTRY_COUNT",
    "treeTotalBytes": "TBOT_ROBOT_PREVIEW_BROWSER_TREE_TOTAL_BYTES",
}
MODES = ("quick", "full", "live-db", "physical-preflight")
OPERATOR_ATTESTATION_ENV = "COURSE_MODE_OPERATOR_ATTESTATION"
OPERATOR_ATTESTATION_KEYS = {
    "candidateId", "createdAt", "effectiveUid", "gateSha", "hostName",
    "sameUidThreatModel", "schemaVersion", "trustedOperatorAccountConfirmed",
    "untrustedAutomationStoppedConfirmed",
}
MAX_OPERATOR_ATTESTATION_BYTES = 64 * 1024
MAX_PHYSICAL_ADMISSION_SOURCE_ENV_BYTES = 512 * 1024
COURSE_MODE_SOFTWARE_TESTS = "@course-mode-software-tests"
PLAYWRIGHT_CONTRACT_PATH = "main/manager-web/course-mode.playwright.contract.json"
PLAYWRIGHT_SOURCE_PATHS = (
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
)
PLAYWRIGHT_PROJECTS = (
    "course-mode-chromium-desktop",
    "course-mode-webkit-desktop",
    "course-mode-chromium-mobile",
    "course-mode-webkit-mobile",
)
PLAYWRIGHT_PROJECT_CONTRACT = (
    {"name": "course-mode-chromium-desktop", "device": "Desktop Chrome", "viewport": {"width": 1440, "height": 900}},
    {"name": "course-mode-webkit-desktop", "device": "Desktop Safari", "viewport": {"width": 1440, "height": 900}},
    {"name": "course-mode-chromium-mobile", "device": "Pixel 7", "viewport": {"width": 390, "height": 844}},
    {"name": "course-mode-webkit-mobile", "device": "iPhone 13", "viewport": {"width": 390, "height": 844}},
)
PLAYWRIGHT_ASSIGNMENT_CONTRACT = {
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
}
PLAYWRIGHT_ASSIGNMENT_SCRIPTS = {
    "test:course-mode:assignment-fixture": PLAYWRIGHT_ASSIGNMENT_CONTRACT["fixtureCommand"],
    "test:e2e:course-mode:assignment:new": PLAYWRIGHT_ASSIGNMENT_CONTRACT["newCommand"],
    "test:e2e:course-mode:assignment:rollback": PLAYWRIGHT_ASSIGNMENT_CONTRACT["rollbackCommand"],
}
TASK4_ASSIGNMENT_CANDIDATE_ENV = (
    "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME", "LESSON_STUDIO_E2E_RESOURCE_PREFIX",
    "TASK4_ASSIGNMENT_RUNTIME_ROOT", "JWT_PUBLIC_KEY", "TBOT_DEVICE_MINT_SECRET",
    "LESSON_ASSET_ORIGIN_BASE", "ROBOT_ESP_BASE_URL",
    "LESSON_STUDIO_E2E_BACKEND_HOST_PORT", "LESSON_STUDIO_E2E_WEB_HOST_PORT",
    "TASK4_ASSIGNMENT_MEDIA_HOST_PORT",
)
TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ENV = "TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT"
STATEFUL_ASSIGNMENT_LANES = frozenset({
    "admin-course-mode-assignment-new",
    "admin-course-mode-assignment-rollback",
})
ASSIGNMENT_ROLLBACK_LANE = "admin-course-mode-assignment-rollback"
ASSIGNMENT_ROLLBACK_RESTORE_FAILURE_SIGNAL = (
    'TBOT_COURSE_MODE_CLEANUP_FAILURE={"schemaVersion":1,'
    '"kind":"assignment-rollback-base-restore"}'
)
TASK4_ASSIGNMENT_PORT_ENV = (
    "LESSON_STUDIO_E2E_BACKEND_HOST_PORT", "LESSON_STUDIO_E2E_WEB_HOST_PORT",
    "TASK4_ASSIGNMENT_MEDIA_HOST_PORT",
)
PLAYWRIGHT_COMPOSE_ENV = (
    "COURSE_MODE_ADMIN_E2E_READY", "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME",
    "LESSON_STUDIO_E2E_RESOURCE_PREFIX", "JWT_PUBLIC_KEY", "TBOT_DEVICE_MINT_SECRET",
    "LESSON_ASSET_ORIGIN_BASE", "ROBOT_ESP_BASE_URL",
    "LESSON_STUDIO_E2E_BACKEND_HOST_PORT", "LESSON_STUDIO_E2E_WEB_HOST_PORT",
)
TASK4_STANDARD_HOST_PORTS = frozenset({3100, 8102, 18443})
TASK4_BACKEND_MOUNT_ROOTS = (
    "src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json",
    "src/lessons/fixtures/tvideo-raw-code/assets/admin",
    "src/lessons/fixtures/tvideo-raw-code/assets/esp-tft",
)
TASK4_FIRMWARE_MOUNT_ROOTS = ("lesson/assets",)
TASK4_BACKEND_MOUNT_INPUTS = (
    "src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json",
    "src/lessons/fixtures/tvideo-raw-code/assets/admin/deep-barn-farm-background-6s.mp4",
    "src/lessons/fixtures/tvideo-raw-code/assets/admin/source/objects/barn.png",
    "src/lessons/fixtures/tvideo-raw-code/assets/admin/source/robot-alive/robots-bright-alive-k3-glowface.png",
    "src/lessons/fixtures/tvideo-raw-code/assets/admin/source/scenes/scene-07-farm.png",
    "src/lessons/fixtures/tvideo-raw-code/assets/esp-tft/barn-192.png",
    "src/lessons/fixtures/tvideo-raw-code/assets/esp-tft/robots-bright-alive-k3-glowface-192.png",
    "src/lessons/fixtures/tvideo-raw-code/assets/esp-tft/scene-07-farm-320x180.jpg",
)
TASK4_FIRMWARE_MOUNT_INPUTS = (
    "lesson/assets/background/barn-round-field-poster.jpg",
    "lesson/assets/background/barn-round-field.mp4",
    "lesson/assets/objects/barn-raw-candidate-0.png",
    "lesson/assets/objects/barn.png",
    "lesson/assets/objects/farm-raw-candidate-0.png",
    "lesson/assets/objects/farm.png",
    "lesson/assets/objects/hay-raw-candidate-0.png",
    "lesson/assets/objects/hay.png",
    "lesson/assets/reference/barn-celebrate.png",
    "lesson/assets/reference/barn-step-1.png",
    "lesson/assets/reference/barn-step-2.png",
    "lesson/assets/reference/barn-step-3.png",
    "lesson/assets/reference/lesson-w01-barn.png",
    "lesson/assets/robot/bright-black-sprite-sheet-source.png",
    "lesson/assets/robot/bright-sprite-atlas.json",
    "lesson/assets/robot/bright-sprite-atlas.png",
    "lesson/assets/robot/poses/bright-cards.png",
    "lesson/assets/robot/poses/bright-celebrate.png",
    "lesson/assets/robot/poses/bright-idle.png",
    "lesson/assets/robot/poses/bright-listening.png",
    "lesson/assets/robot/poses/bright-side.png",
    "lesson/assets/robot/poses/bright-teach.png",
    "lesson/assets/robot/poses/bright-thinking.png",
    "lesson/assets/robot/poses/bright-wave.png",
    "lesson/assets/robot/rive-source/teebot-face-import-v2.svg",
    "lesson/assets/robot/rive-source/teebot-face.svg",
    "lesson/assets/robot/rive-source/teebot-states-reference.svg",
)
PLAYWRIGHT_FIXED_CONTRACT = {
    "testDir": "./e2e/lesson-studio",
    "globalSetup": "./e2e/lesson-studio/global-setup.cjs",
    "outputDir": "./output/playwright-course-mode/results",
    "outputRootEnvironment": "LESSON_STUDIO_E2E_OUTPUT_ROOT",
    "timeout": 60000,
    "expectTimeout": 10000,
    "fullyParallel": False,
    "workers": 1,
    "retries": 0,
    "reporter": [["list"], ["html", {"outputFolder": "./output/playwright-course-mode/report", "open": "never"}]],
    "use": {
        "baseUrlHelper": "lessonStudioWebOrigin",
        "trace": "retain-on-failure",
        "screenshot": "only-on-failure",
        "video": "retain-on-failure",
        "serviceWorkers": "block",
    },
}
ESP_COURSE_MODE_FULL_TESTS = (
    "tests/test_course_mode_contract.py",
    "tests/test_course_mode_curriculum.py",
    "tests/test_course_mode_curriculum_e2e.py",
    "tests/test_course_mode_e2e_journeys.py",
    "tests/test_course_mode_forwarder.py",
    "tests/test_course_mode_resource_soak.py",
    "tests/test_course_mode_runtime_compatibility.py",
    "tests/test_course_mode_runtime_integration.py",
    "tests/test_course_mode_task00_contract.py",
    "tests/test_google_live_course_mode.py",
)


@dataclass(frozen=True)
class Lane:
    name: str
    repository: str
    relative_cwd: str
    command: tuple[str, ...]
    timeout_sec: float
    required_environment: tuple[str, ...] | str = ()
    fixed_environment: tuple[tuple[str, str], ...] = ()
    required_source_contract: str | None = None
    reject_pytest_skips: bool = False


@dataclass(frozen=True)
class OperatorAttestationBinding:
    path: Path
    sha256: str


@dataclass(frozen=True)
class PhysicalAdmissionBinding:
    descriptor: tuple[tuple[str, str], ...]
    evidence_root_identity: tuple[int, ...]
    input_identities: tuple[tuple[str, tuple[int, ...], str], ...]
    output_parent_identity: tuple[int, ...]
    software_audit_identity: tuple[int, ...]
    software_audit_sha256: str
    software_snapshot_id: str
    expected_result: bytes


@dataclass(frozen=True)
class PhysicalPythonRuntimeBinding:
    descriptor: tuple[tuple[str, object], ...]
    executable: Path
    executable_identity: tuple[int, ...]
    tree_digest: str


@dataclass(frozen=True)
class PhysicalAdmissionSourceBinding:
    records: tuple[tuple, ...]


@dataclass
class ReportDestination:
    parent_fd: int
    identity: tuple[int, int] | None = None
    report_fd: int | None = None


class RetainedStagingError(RuntimeError):
    def __init__(self, *paths: Path):
        self.paths = tuple(sorted({str(path) for path in paths}))
        super().__init__("retained staging ownership")


@dataclass
class ExecutionStage:
    root: Path
    candidate: dict
    identity: tuple[int, int]
    descriptor: int | None
    _retained_path: Path | None = None
    _cleanup_succeeded: bool | None = None

    def cleanup(self) -> bool:
        if self.descriptor is None:
            return self._cleanup_succeeded is True
        actual = _directory_fd_path(self.descriptor)
        if actual is not None and actual != self.root:
            self._retained_path = actual
            os.close(self.descriptor)
            self.descriptor = None
            self._cleanup_succeeded = False
            return False
        removed = _remove_owned_tree(self.root, self.identity)
        if not removed:
            self._retained_path = (
                _directory_fd_path(self.descriptor)
                or _find_owned_tree(self.root.parent, self.identity) or self.root
            )
        os.close(self.descriptor)
        self.descriptor = None
        self._cleanup_succeeded = removed
        return removed

    def retained_path(self) -> Path:
        return self._retained_path or self.root

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.cleanup()

    def create_lane_execution(self, *, use_read_only_candidate: bool = False) -> LaneExecution:
        lane_root = Path(tempfile.mkdtemp(prefix="course-mode-lane-", dir=self.root.parent))
        lane_identity: tuple[int, int] | None = None
        lane_descriptor: int | None = None
        try:
            lane_identity = _owned_tree_identity(lane_root)
            lane_descriptor = _open_snapshot_directory(lane_root)
            python_runtime = self.root / "tools/python-test-runtime"
            if use_read_only_candidate:
                rebased_candidate = self.candidate
            else:
                execution_root = lane_root / "candidate"
                stable_tool_paths = tuple(
                    path for path in (
                        python_runtime,
                        self.root / "tools/docker",
                        self.root / "tools/docker-compose",
                        self.root / "tools/playwright-browsers",
                    ) if path.exists()
                )

                def ignore_stable_tools(directory: str, _names: list[str]) -> set[str]:
                    if Path(directory) != self.root / "tools":
                        return set()
                    return {path.name for path in stable_tool_paths}

                shutil.copytree(
                    self.root, execution_root, symlinks=True,
                    ignore=ignore_stable_tools if stable_tool_paths else None,
                )
                _make_tree_owner_writable(execution_root)
                source_prefix = str(self.root) + os.sep
                target_prefix = str(execution_root) + os.sep
                stable_prefixes = tuple(
                    (str(path), str(path) + os.sep) for path in stable_tool_paths
                )

                def rebase(value: object) -> object:
                    if isinstance(value, dict):
                        return {key: rebase(item) for key, item in value.items()}
                    if isinstance(value, list):
                        return [rebase(item) for item in value]
                    if isinstance(value, str) and any(
                        value == stable or value.startswith(prefix)
                        for stable, prefix in stable_prefixes
                    ):
                        return value
                    if isinstance(value, str) and value.startswith(source_prefix):
                        return target_prefix + value[len(source_prefix):]
                    return value

                rebased_candidate = rebase(self.candidate)

            if "backendTestInputs" in rebased_candidate["tools"]:
                portal = Path(rebased_candidate["tools"]["backendTestInputs"]["portalOpenapi"]["path"])
                portal.chmod(0o444)
                if not _manifest.backend_test_inputs_valid(rebased_candidate):
                    raise ValueError("lane backend test inputs mismatch")

            runtime = lane_root / "runtime"
            environment = {}
            for name in ("home", "tmp", "cache", "reports"):
                (runtime / name).mkdir(parents=True)
            environment.update({
                "HOME": str(runtime / "home"),
                "TMPDIR": str(runtime / "tmp"),
                "XDG_CACHE_HOME": str(runtime / "cache"),
                "COURSE_MODE_LANE_REPORT_ROOT": str(runtime / "reports"),
            })
            if python_runtime.is_dir():
                descriptor = rebased_candidate["tools"]["pythonTestRuntime"]
                if not _manifest.python_test_runtime_authorized(descriptor):
                    raise ValueError("lane Python test runtime authority mismatch")
            return LaneExecution(
                lane_root, rebased_candidate, environment, lane_identity, lane_descriptor,
            )
        except Exception:
            retained_path = (
                _directory_fd_path(lane_descriptor) if lane_descriptor is not None else None
            ) or lane_root
            removed = _remove_owned_tree(retained_path, lane_identity)
            if not removed and lane_descriptor is not None:
                retained_path = _directory_fd_path(lane_descriptor) or retained_path
            if lane_descriptor is not None:
                os.close(lane_descriptor)
            if not removed:
                raise RetainedStagingError(retained_path)
            raise


@dataclass
class LaneExecution:
    root: Path
    candidate: dict
    environment: dict[str, str]
    identity: tuple[int, int]
    descriptor: int | None
    _retained_path: Path | None = None
    _cleanup_succeeded: bool | None = None

    def cleanup(self) -> bool:
        if self.descriptor is None:
            return self._cleanup_succeeded is True
        actual = _directory_fd_path(self.descriptor)
        if actual is not None and actual != self.root:
            self._retained_path = actual
            os.close(self.descriptor)
            self.descriptor = None
            self._cleanup_succeeded = False
            return False
        removed = _remove_owned_tree(self.root, self.identity)
        if not removed:
            self._retained_path = (
                _directory_fd_path(self.descriptor)
                or _find_owned_tree(self.root.parent, self.identity) or self.root
            )
        os.close(self.descriptor)
        self.descriptor = None
        self._cleanup_succeeded = removed
        return removed

    def retained_path(self) -> Path:
        return self._retained_path or self.root

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.cleanup()


@dataclass
class AssignmentRuntimeCapsule:
    root: Path
    identity: tuple[int, int]
    descriptor: int | None
    runtime_root: Path
    runtime_identity: tuple[int, int]
    runtime_descriptor: int | None
    _retained_path: Path | None = None
    _retained_paths: tuple[Path, ...] = ()
    _cleanup_succeeded: bool | None = None

    @classmethod
    def create(cls, protected: Sequence[Path]) -> AssignmentRuntimeCapsule:
        root = Path(tempfile.mkdtemp(prefix="course-mode-assignment-runtime-")).resolve()
        identity: tuple[int, int] | None = None
        descriptor: int | None = None
        runtime_root = root / "runtime"
        runtime_identity: tuple[int, int] | None = None
        runtime_descriptor: int | None = None
        try:
            root.chmod(0o700)
            identity = _owned_tree_identity(root)
            if any(_path_overlaps(root, path.resolve()) for path in protected):
                raise ValueError("assignment runtime overlaps protected path")
            descriptor = _open_snapshot_directory(root)
            runtime_root.mkdir(mode=0o700)
            runtime_identity = _owned_tree_identity(runtime_root)
            (runtime_root / "media").mkdir(mode=0o700)
            (runtime_root / "tls").mkdir(mode=0o700)
            runtime_descriptor = _open_snapshot_directory(runtime_root)
            return cls(
                root, identity, descriptor,
                runtime_root, runtime_identity, runtime_descriptor,
            )
        except BaseException:
            if runtime_descriptor is not None:
                os.close(runtime_descriptor)
            if descriptor is not None:
                os.close(descriptor)
            if os.path.lexists(root) and not _remove_owned_tree(root, identity):
                raise RetainedStagingError(root)
            raise

    def usable(self) -> bool:
        if self.descriptor is None or self.runtime_descriptor is None:
            return False
        try:
            owner_before = _directory_fd_path(self.descriptor)
            owner_opened = os.fstat(self.descriptor)
            owner_named = self.root.lstat()
            runtime_before = _directory_fd_path(self.runtime_descriptor)
            runtime_opened = os.fstat(self.runtime_descriptor)
            runtime_named = self.runtime_root.lstat()
            owner_after = _directory_fd_path(self.descriptor)
            runtime_after = _directory_fd_path(self.runtime_descriptor)
        except OSError:
            return False
        return (
            owner_before == self.root == owner_after
            and stat.S_ISDIR(owner_opened.st_mode)
            and (owner_opened.st_dev, owner_opened.st_ino) == self.identity
            and stat.S_ISDIR(owner_named.st_mode)
            and (owner_named.st_dev, owner_named.st_ino) == self.identity
            and runtime_before == self.runtime_root == runtime_after
            and stat.S_ISDIR(runtime_opened.st_mode)
            and (runtime_opened.st_dev, runtime_opened.st_ino) == self.runtime_identity
            and stat.S_ISDIR(runtime_named.st_mode)
            and (runtime_named.st_dev, runtime_named.st_ino) == self.runtime_identity
        )

    def cleanup(self) -> bool:
        if self.descriptor is None and self.runtime_descriptor is None:
            return self._cleanup_succeeded is True
        owner_actual = (
            _directory_fd_path(self.descriptor) if self.descriptor is not None else None
        )
        runtime_actual = (
            _directory_fd_path(self.runtime_descriptor)
            if self.runtime_descriptor is not None else None
        )
        if owner_actual != self.root or runtime_actual != self.runtime_root:
            owner_retained = (
                owner_actual if owner_actual != self.root else self.root
            ) or self.root
            retained = {owner_retained}
            if (
                runtime_actual is not None
                and not _path_overlaps(owner_retained, runtime_actual)
            ):
                retained.add(runtime_actual)
            self._retained_path = owner_retained
            self._retained_paths = tuple(sorted(retained, key=str))
            if self.runtime_descriptor is not None:
                os.close(self.runtime_descriptor)
            if self.descriptor is not None:
                os.close(self.descriptor)
            self.runtime_descriptor = None
            self.descriptor = None
            self._cleanup_succeeded = False
            return False
        removed = _remove_owned_tree(self.root, self.identity)
        if not removed:
            retained = owner_actual
            try:
                retained_metadata = retained.lstat() if retained is not None else None
            except OSError:
                retained_metadata = None
            if (
                retained_metadata is None
                or not stat.S_ISDIR(retained_metadata.st_mode)
                or (retained_metadata.st_dev, retained_metadata.st_ino) != self.identity
            ):
                retained = None
            self._retained_path = (
                retained or _find_owned_tree(self.root.parent, self.identity) or self.root
            )
            self._retained_paths = (self._retained_path,)
        os.close(self.descriptor)
        os.close(self.runtime_descriptor)
        self.descriptor = None
        self.runtime_descriptor = None
        self._cleanup_succeeded = removed
        return removed

    def retained_path(self) -> Path:
        return self._retained_path or self.root

    def retained_paths(self) -> tuple[Path, ...]:
        return self._retained_paths or (self.retained_path(),)

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.cleanup()


@dataclass(frozen=True)
class BackendSnapshotBinding:
    environment: dict[str, str]
    execution_tree: dict
    authority_sha256: str


def _make_tree_read_only(root: Path) -> None:
    for directory, names, files in os.walk(root, topdown=False):
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & 0o555)
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & 0o555)
        directory_path = Path(directory)
        directory_path.chmod(directory_path.stat().st_mode & 0o555)


def _make_tree_owner_writable(root: Path) -> None:
    for directory, names, files in os.walk(root, topdown=True):
        Path(directory).chmod(Path(directory).stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for name in names:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for name in files:
            path = Path(directory) / name
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR)


def _snapshot_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _owned_tree_identity(root: Path) -> tuple[int, int]:
    metadata = root.lstat()
    if metadata.st_uid != os.geteuid() or not stat.S_ISDIR(metadata.st_mode):
        raise OSError("gate-owned directory required")
    return metadata.st_dev, metadata.st_ino


def _directory_fd_path(descriptor: int) -> Path | None:
    try:
        if hasattr(fcntl, "F_GETPATH"):
            raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
            value = raw.split(b"\0", 1)[0]
        else:
            value = os.fsencode(os.readlink(f"/proc/self/fd/{descriptor}"))
            if value.endswith(b" (deleted)"):
                value = value[:-10]
        path = Path(os.fsdecode(value))
        return path if path.is_absolute() else None
    except (OSError, UnicodeError, ValueError):
        return None


def _find_owned_tree(parent: Path, identity: tuple[int, int]) -> Path | None:
    try:
        parent_fd = _open_snapshot_directory(parent)
    except OSError:
        return None
    try:
        for name in sorted(entry.name for entry in os.scandir(parent_fd)):
            try:
                metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError:
                continue
            if (
                metadata.st_uid == os.geteuid() and stat.S_ISDIR(metadata.st_mode)
                and (metadata.st_dev, metadata.st_ino) == identity
            ):
                return parent / name
        return None
    finally:
        os.close(parent_fd)


class _StagingIdentityChanged(OSError):
    pass


def _remove_owned_tree(root: Path, expected_identity: tuple[int, int] | None = None) -> bool:
    """Remove a private staging tree without following links or hiding retained state."""
    if not os.path.lexists(root):
        return expected_identity is None
    effective_uid = os.geteuid()

    def remove_leaf(directory_fd: int, name: str, before: os.stat_result) -> None:
        leaf_fd = None
        try:
            if stat.S_ISSOCK(before.st_mode):
                raise _StagingIdentityChanged("staging socket cannot be identity-bound for removal")
            quarantine = f".course-mode-cleanup-{secrets.token_hex(16)}"
            os.rename(name, quarantine, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            quarantined = os.stat(quarantine, dir_fd=directory_fd, follow_symlinks=False)
            if (
                quarantined.st_uid != effective_uid
                or (quarantined.st_dev, quarantined.st_ino) != (before.st_dev, before.st_ino)
                or stat.S_IFMT(quarantined.st_mode) != stat.S_IFMT(before.st_mode)
            ):
                raise _StagingIdentityChanged("staging leaf identity changed")
            if stat.S_ISREG(quarantined.st_mode):
                os.chmod(quarantine, 0o600, dir_fd=directory_fd, follow_symlinks=False)
                flags = os.O_RDONLY | os.O_NOFOLLOW
            elif stat.S_ISLNK(quarantined.st_mode) and hasattr(os, "O_PATH"):
                flags = os.O_PATH | os.O_NOFOLLOW
            elif stat.S_ISLNK(quarantined.st_mode) and hasattr(os, "O_SYMLINK"):
                flags = os.O_RDONLY | os.O_SYMLINK
            elif stat.S_ISFIFO(quarantined.st_mode):
                flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            else:
                raise _StagingIdentityChanged("unsupported staging leaf type")
            leaf_fd = os.open(quarantine, flags, dir_fd=directory_fd)
            opened = os.fstat(leaf_fd)
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or stat.S_IFMT(opened.st_mode) != stat.S_IFMT(before.st_mode)
            ):
                raise _StagingIdentityChanged("staging leaf changed before removal")
            pre_unlink_link_count = None
            if stat.S_ISREG(opened.st_mode):
                pre_unlink_link_count = os.fstat(leaf_fd).st_nlink
                if pre_unlink_link_count <= 0:
                    raise _StagingIdentityChanged("staging leaf link count changed")
            os.unlink(quarantine, dir_fd=directory_fd)
            try:
                os.stat(quarantine, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                post_unlink_link_count = os.fstat(leaf_fd).st_nlink
                if (
                    post_unlink_link_count != pre_unlink_link_count - 1
                    if pre_unlink_link_count is not None
                    else post_unlink_link_count != 0
                ):
                    raise _StagingIdentityChanged("staging leaf moved during removal")
            else:
                raise _StagingIdentityChanged("staging leaf path recreated")
        except _StagingIdentityChanged:
            raise
        except OSError as error:
            raise _StagingIdentityChanged("staging leaf removal raced") from error
        finally:
            if leaf_fd is not None:
                os.close(leaf_fd)

    def clear(directory_fd: int, directory_path: Path) -> None:
        for name in sorted(entry.name for entry in os.scandir(directory_fd)):
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if before.st_uid != effective_uid:
                raise _StagingIdentityChanged("staging entry ownership changed")
            if stat.S_ISDIR(before.st_mode):
                os.chmod(name, 0o700, dir_fd=directory_fd, follow_symlinks=False)
                child_fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(child_fd)
                    if (
                        opened.st_uid != effective_uid or not stat.S_ISDIR(opened.st_mode)
                        or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                    ):
                        raise _StagingIdentityChanged("staging directory identity changed")
                    os.fchmod(child_fd, 0o700)
                    clear(child_fd, directory_path / name)
                    try:
                        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError as error:
                        raise _StagingIdentityChanged("staging directory path vanished") from error
                    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                        raise _StagingIdentityChanged("staging directory path changed")
                    try:
                        os.rmdir(name, dir_fd=directory_fd)
                    except OSError as error:
                        raise _StagingIdentityChanged("staging directory removal raced") from error
                    try:
                        os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current = _directory_fd_path(child_fd)
                        if current is None or current != directory_path / name:
                            raise _StagingIdentityChanged("staging directory moved during removal")
                    else:
                        raise _StagingIdentityChanged("staging directory path recreated")
                finally:
                    os.close(child_fd)
            else:
                remove_leaf(directory_fd, name, before)

    for _attempt in range(2):
        parent_fd = None
        root_fd = None
        try:
            parent_fd = _open_snapshot_directory(root.parent)
            metadata = os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                metadata.st_uid != effective_uid or not stat.S_ISDIR(metadata.st_mode)
                or expected_identity is not None
                and (metadata.st_dev, metadata.st_ino) != expected_identity
            ):
                return False
            os.chmod(root.name, 0o700, dir_fd=parent_fd, follow_symlinks=False)
            root_fd = os.open(
                root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd,
            )
            opened = os.fstat(root_fd)
            if (
                opened.st_uid != effective_uid or not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
                or expected_identity is not None
                and (opened.st_dev, opened.st_ino) != expected_identity
            ):
                raise _StagingIdentityChanged("staging root identity changed")
            os.fchmod(root_fd, 0o700)
            clear(root_fd, root)
            try:
                named = os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError as error:
                raise _StagingIdentityChanged("staging root path vanished") from error
            if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                raise _StagingIdentityChanged("staging root path changed")
            try:
                os.rmdir(root.name, dir_fd=parent_fd)
            except OSError as error:
                raise _StagingIdentityChanged("staging root removal raced") from error
            try:
                os.stat(root.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                current = _directory_fd_path(root_fd)
                if current is None or current != root:
                    raise _StagingIdentityChanged("staging root moved during removal")
                return True
            raise _StagingIdentityChanged("staging root path recreated")
        except _StagingIdentityChanged:
            return False
        except OSError:
            pass
        finally:
            if root_fd is not None:
                os.close(root_fd)
            if parent_fd is not None:
                os.close(parent_fd)
    return expected_identity is None and not os.path.lexists(root)


def _collect_gate_owned_retained_paths(
    owned: Sequence[ExecutionStage | LaneExecution | AssignmentRuntimeCapsule | None],
    existing: Sequence[str] = (),
) -> tuple[str, ...]:
    retained = set(existing)
    for item in owned:
        if item is not None and not item.cleanup():
            paths = (
                item.retained_paths()
                if isinstance(item, AssignmentRuntimeCapsule)
                else (item.retained_path(),)
            )
            retained.update(str(path) for path in paths)
    return tuple(sorted(retained))


def _cleanup_gate_owned(
    report: dict,
    *owned: ExecutionStage | LaneExecution | AssignmentRuntimeCapsule | None,
) -> bool:
    retained = _collect_gate_owned_retained_paths(
        owned, report.get("retainedPaths", ()),
    )
    if retained:
        report["verdict"] = "BLOCKED"
        if report.get("failedLane") in (None, "cleanup"):
            report["failedLane"] = "cleanup"
        else:
            report["cleanupFailed"] = True
        report["retainedOwner"] = "current-process"
        report["retainedPaths"] = list(retained)
        return False
    return True


def _assignment_rollback_restore_failed(lane: Lane, stdout: str) -> bool:
    return (
        lane.name == ASSIGNMENT_ROLLBACK_LANE
        and ASSIGNMENT_ROLLBACK_RESTORE_FAILURE_SIGNAL in stdout.splitlines()
    )


@dataclass(frozen=True)
class AssignmentRollbackRestoreBinding:
    candidate_id: str
    admin_sha: str
    admin_root: Path
    compose_path: Path
    compose_sha256: str
    compose_identity: tuple[int, ...]


def _assignment_rollback_restore_binding(
    lane: Lane, source_candidate: dict, trusted_candidate: dict,
) -> AssignmentRollbackRestoreBinding | None:
    if lane.name != ASSIGNMENT_ROLLBACK_LANE:
        return None
    try:
        candidate_id = source_candidate["candidateId"]
        admin_sha = source_candidate["repositories"]["adminEsp"]["sha"]
        trusted_admin = trusted_candidate["repositories"]["adminEsp"]
        admin_root = Path(trusted_admin["path"])
        compose_path = admin_root / "docs/docker/docker-compose.lesson-studio-e2e.yml"
        metadata = compose_path.lstat()
        digest = _secure_file_sha256(compose_path, MAX_ASSIGNMENT_BASE_COMPOSE_BYTES)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None
    if (
        not isinstance(candidate_id, str)
        or not candidate_id
        or not isinstance(admin_sha, str)
        or re.fullmatch(r"[0-9a-f]{40}", admin_sha) is None
        or trusted_candidate.get("candidateId") != candidate_id
        or trusted_admin.get("sha") != admin_sha
        or not admin_root.is_absolute()
        or admin_root.resolve(strict=True) != admin_root
        or compose_path.resolve(strict=True) != compose_path
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o222
        or digest is None
    ):
        return None
    return AssignmentRollbackRestoreBinding(
        candidate_id, admin_sha, admin_root, compose_path, digest,
        _snapshot_identity(metadata),
    )


def _restore_base_stack_after_abnormal_assignment_rollback(
    lane: Lane,
    result: _manifest.BoundedCommandResult,
    candidate_path: Path,
    source_candidate: dict,
    trusted_candidate: dict,
    binding: AssignmentRollbackRestoreBinding | None,
    environment: dict[str, str],
    max_output_bytes: int,
) -> bool | None:
    if lane.name != ASSIGNMENT_ROLLBACK_LANE:
        return None
    if result.error == "containment":
        return False
    if result.error not in {"timeout", "output"}:
        return None
    if binding is None or not _candidate_metadata_matches(candidate_path, source_candidate):
        return False
    try:
        project = environment["LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME"]
        prefix = environment["LESSON_STUDIO_E2E_RESOURCE_PREFIX"]
        compose = trusted_candidate["tools"]["dockerCompose"]["path"]
        trusted_admin = trusted_candidate["repositories"]["adminEsp"]
        metadata = binding.compose_path.lstat()
    except (KeyError, OSError, TypeError, ValueError):
        return False
    if (
        not isinstance(project, str)
        or re.fullmatch(r"tbot-task4-[a-z0-9-]+", project) is None
        or prefix != project
        or trusted_candidate.get("candidateId") != binding.candidate_id
        or trusted_admin.get("sha") != binding.admin_sha
        or Path(trusted_admin.get("path", "")) != binding.admin_root
        or environment.get("TBOT_DOCKER_COMPOSE_EXECUTABLE") != compose
        or not _container_tools_authorized(trusted_candidate)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_mode & 0o222
        or _snapshot_identity(metadata) != binding.compose_identity
        or _secure_file_sha256(
            binding.compose_path, MAX_ASSIGNMENT_BASE_COMPOSE_BYTES,
        ) != binding.compose_sha256
    ):
        return False
    restored = run_bounded_command(
        [
            compose,
            "-p", project,
            "-f", str(binding.compose_path),
            "up", "-d", "--wait", "--no-deps", "--force-recreate", "backend", "web",
        ],
        cwd=binding.admin_root,
        timeout_sec=lane.timeout_sec,
        max_output_bytes=max_output_bytes,
        env=environment,
        contain_process_group=True,
    )
    return restored.error is None and restored.returncode == 0


@dataclass
class AssignmentRuntimeGuard:
    capsule: AssignmentRuntimeCapsule | None = None
    report: dict | None = None

    def own(self, capsule: AssignmentRuntimeCapsule, report: dict) -> None:
        self.capsule = capsule
        self.report = report

    def release(self) -> None:
        self.capsule = None
        self.report = None

    def cleanup(self) -> None:
        if self.capsule is not None:
            assert self.report is not None
            _cleanup_gate_owned(self.report, self.capsule)
            self.release()


def _cleanup_gate_owned_or_raise(
    *owned: ExecutionStage | LaneExecution | AssignmentRuntimeCapsule | None,
) -> None:
    retained = _collect_gate_owned_retained_paths(owned)
    if retained:
        raise RetainedStagingError(*(Path(path) for path in retained))


def _open_snapshot_directory(path: Path) -> int:
    if not path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise OSError("absolute lexical path required")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _copy_open_regular_to_fd(
    source_fd: int, output_fd: int, before: os.stat_result, state: dict[str, int],
) -> str:
    if before.st_nlink != 1:
        raise ValueError("unsafe hard-linked file in snapshot")
    state["bytes"] += before.st_size
    if state["bytes"] > MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot byte limit exceeded")
    digest = hashlib.sha256()
    remaining = before.st_size
    while remaining:
        chunk = os.read(source_fd, min(1024 * 1024, remaining))
        if not chunk:
            raise ValueError("source changed during snapshot")
        digest.update(chunk)
        view = memoryview(chunk)
        while view:
            written = os.write(output_fd, view)
            view = view[written:]
        remaining -= len(chunk)
    if os.read(source_fd, 1):
        raise ValueError("source changed during snapshot")
    os.fsync(output_fd)
    os.fchmod(output_fd, stat.S_IMODE(before.st_mode))
    return digest.hexdigest()


def _copy_open_regular(
    source_fd: int, destination: Path, before: os.stat_result, state: dict[str, int],
) -> str:
    output_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        return _copy_open_regular_to_fd(source_fd, output_fd, before, state)
    finally:
        os.close(output_fd)


def _lexical_symlink_within_root(relative_parent: Path, target: str) -> bool:
    if not target or "\0" in target or Path(target).is_absolute():
        return False
    combined = posixpath.normpath(posixpath.join(relative_parent.as_posix(), target))
    return combined not in {"..", "."} and not combined.startswith("../")


def _open_snapshot_relative_directory(root_fd: int, parts: Sequence[str], *, create: bool) -> int:
    descriptor = os.dup(root_fd)
    try:
        for component in parts:
            if component in {"", ".", ".."}:
                raise OSError("unsafe relative snapshot path")
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(component, 0o700, dir_fd=descriptor)
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _copy_dirty_exception(
    source_root: Path, destination_root: Path, relative: Path, expected_sha256: str,
    state: dict[str, int],
) -> None:
    source_parent_fd = _open_snapshot_directory(source_root)
    destination_root_fd = _open_snapshot_directory(destination_root)
    source_file_fd = None
    destination_parent_fd = None
    output_fd = None
    try:
        source_directory_fd = _open_snapshot_relative_directory(
            source_parent_fd, relative.parts[:-1], create=False,
        )
        os.close(source_parent_fd)
        source_parent_fd = source_directory_fd
        before = os.stat(relative.name, dir_fd=source_parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("dirty exception is not a regular file")
        source_file_fd = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_parent_fd,
        )
        if _snapshot_identity(os.fstat(source_file_fd)) != _snapshot_identity(before):
            raise ValueError("source changed during snapshot")
        destination_parent_fd = _open_snapshot_relative_directory(
            destination_root_fd, relative.parts[:-1], create=True,
        )
        with contextlib.suppress(FileNotFoundError):
            current = os.stat(relative.name, dir_fd=destination_parent_fd, follow_symlinks=False)
            if stat.S_ISDIR(current.st_mode):
                raise ValueError("dirty exception cannot replace directory")
            os.unlink(relative.name, dir_fd=destination_parent_fd)
        output_fd = os.open(
            relative.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=destination_parent_fd,
        )
        digest = _copy_open_regular_to_fd(source_file_fd, output_fd, before, state)
        after = os.stat(relative.name, dir_fd=source_parent_fd, follow_symlinks=False)
        if (
            _snapshot_identity(after) != _snapshot_identity(before)
            or _snapshot_identity(os.fstat(source_file_fd)) != _snapshot_identity(before)
        ):
            raise ValueError("source changed during snapshot")
        if digest != expected_sha256:
            raise ValueError("snapshot descriptor mismatch")
    finally:
        for descriptor in (output_fd, source_file_fd, destination_parent_fd, destination_root_fd, source_parent_fd):
            if descriptor is not None:
                os.close(descriptor)


def _copy_snapshot_tree(
    source: Path, destination: Path, state: dict[str, int], *, exclude_git: bool = False,
    reject_links: bool = False,
) -> None:
    root_fd = _open_snapshot_directory(source)
    root_opened = os.fstat(root_fd)
    if not stat.S_ISDIR(root_opened.st_mode):
        os.close(root_fd)
        raise ValueError("special source root in snapshot")
    destination.mkdir(mode=0o700)

    def visit(directory_fd: int, relative: Path, target: Path, depth: int) -> None:
        if depth > MAX_SNAPSHOT_DEPTH:
            raise ValueError("snapshot depth limit exceeded")
        names = sorted(entry.name for entry in os.scandir(directory_fd))
        for name in names:
            if exclude_git and name == ".git":
                continue
            state["entries"] += 1
            if state["entries"] > MAX_SNAPSHOT_ENTRIES:
                raise ValueError("snapshot entry limit exceeded")
            before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            destination_entry = target / name
            relative_entry = relative / name
            if stat.S_ISDIR(before.st_mode):
                child_fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd,
                )
                try:
                    opened = os.fstat(child_fd)
                    if _snapshot_identity(opened) != _snapshot_identity(before):
                        raise ValueError("source changed during snapshot")
                    destination_entry.mkdir(mode=0o700)
                    visit(child_fd, relative_entry, destination_entry, depth + 1)
                    destination_entry.chmod(stat.S_IMODE(before.st_mode))
                    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    final = os.fstat(child_fd)
                    if (
                        _snapshot_identity(after) != _snapshot_identity(before)
                        or _snapshot_identity(final) != _snapshot_identity(opened)
                    ):
                        raise ValueError("source changed during snapshot")
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(before.st_mode):
                if reject_links and before.st_nlink != 1:
                    raise ValueError("hardlink in strict snapshot")
                file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    opened = os.fstat(file_fd)
                    if _snapshot_identity(opened) != _snapshot_identity(before):
                        raise ValueError("source changed during snapshot")
                    _copy_open_regular(file_fd, destination_entry, before, state)
                    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    final = os.fstat(file_fd)
                    if (
                        _snapshot_identity(after) != _snapshot_identity(before)
                        or _snapshot_identity(final) != _snapshot_identity(opened)
                    ):
                        raise ValueError("source changed during snapshot")
                finally:
                    os.close(file_fd)
            elif stat.S_ISLNK(before.st_mode):
                if reject_links:
                    raise ValueError("symlink in strict snapshot")
                target_value = os.readlink(name, dir_fd=directory_fd)
                if not _lexical_symlink_within_root(relative_entry.parent, target_value):
                    raise ValueError("unsafe symlink in snapshot")
                state["bytes"] += len(os.fsencode(target_value))
                if state["bytes"] > MAX_SNAPSHOT_BYTES:
                    raise ValueError("snapshot byte limit exceeded")
                os.symlink(target_value, destination_entry)
                after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if (
                    os.readlink(name, dir_fd=directory_fd) != target_value
                    or _snapshot_identity(after) != _snapshot_identity(before)
                ):
                    raise ValueError("source changed during snapshot")
            else:
                raise ValueError("special file in snapshot")

    try:
        visit(root_fd, Path(), destination, 0)
        root_final = os.fstat(root_fd)
        if _snapshot_identity(root_final) != _snapshot_identity(root_opened):
            raise ValueError("source root changed during snapshot")
        destination.chmod(stat.S_IMODE(root_opened.st_mode))
    finally:
        os.close(root_fd)


def _copy_strict_snapshot_tree_fd(
    source: Path, destination_parent: Path, destination_name: str, state: dict[str, int],
) -> None:
    if not destination_name or "/" in destination_name or destination_name in {".", ".."}:
        raise ValueError("unsafe strict snapshot destination")
    source_fd = _open_snapshot_directory(source)
    parent_fd = _open_snapshot_directory(destination_parent)
    destination_fd: int | None = None
    source_opened = os.fstat(source_fd)

    def visit(source_dir_fd: int, destination_dir_fd: int, depth: int) -> None:
        if depth > MAX_SNAPSHOT_DEPTH:
            raise ValueError("snapshot depth limit exceeded")
        for name in sorted(entry.name for entry in os.scandir(source_dir_fd)):
            state["entries"] += 1
            if state["entries"] > MAX_SNAPSHOT_ENTRIES:
                raise ValueError("snapshot entry limit exceeded")
            before = os.stat(name, dir_fd=source_dir_fd, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                source_child = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=source_dir_fd,
                )
                destination_child: int | None = None
                try:
                    if _snapshot_identity(os.fstat(source_child)) != _snapshot_identity(before):
                        raise ValueError("source changed during snapshot")
                    os.mkdir(name, 0o700, dir_fd=destination_dir_fd)
                    destination_child = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=destination_dir_fd,
                    )
                    visit(source_child, destination_child, depth + 1)
                    os.fchmod(destination_child, stat.S_IMODE(before.st_mode))
                    if _snapshot_identity(os.fstat(source_child)) != _snapshot_identity(before):
                        raise ValueError("source changed during snapshot")
                finally:
                    if destination_child is not None:
                        os.close(destination_child)
                    os.close(source_child)
            elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
                source_file = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_dir_fd)
                destination_file: int | None = None
                try:
                    if _snapshot_identity(os.fstat(source_file)) != _snapshot_identity(before):
                        raise ValueError("source changed during snapshot")
                    destination_file = os.open(
                        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600, dir_fd=destination_dir_fd,
                    )
                    _copy_open_regular_to_fd(source_file, destination_file, before, state)
                    os.fchmod(destination_file, stat.S_IMODE(before.st_mode))
                    if _snapshot_identity(os.fstat(source_file)) != _snapshot_identity(before):
                        raise ValueError("source changed during snapshot")
                finally:
                    if destination_file is not None:
                        os.close(destination_file)
                    os.close(source_file)
            else:
                raise ValueError("link in strict snapshot")

    try:
        os.mkdir(destination_name, 0o700, dir_fd=parent_fd)
        destination_fd = os.open(
            destination_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd,
        )
        visit(source_fd, destination_fd, 0)
        os.fchmod(destination_fd, stat.S_IMODE(source_opened.st_mode))
        if _snapshot_identity(os.fstat(source_fd)) != _snapshot_identity(source_opened):
            raise ValueError("source root changed during snapshot")
    finally:
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(parent_fd)
        os.close(source_fd)


def _copy_snapshot_file(
    source: Path, destination: Path, state: dict[str, int], *, expected_sha256: str | None = None,
) -> str:
    parent_fd = _open_snapshot_directory(source.parent)
    file_fd = None
    try:
        before = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("special file in snapshot")
        file_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        opened = os.fstat(file_fd)
        if _snapshot_identity(opened) != _snapshot_identity(before):
            raise ValueError("source changed during snapshot")
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = _copy_open_regular(file_fd, destination, before, state)
        after = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            _snapshot_identity(after) != _snapshot_identity(before)
            or _snapshot_identity(os.fstat(file_fd)) != _snapshot_identity(opened)
        ):
            raise ValueError("source changed during snapshot")
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("snapshot descriptor mismatch")
        return digest
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def _git_object_command_base(*, git_dir: Path | None = None) -> list[str]:
    base = [str(_manifest.TRUSTED_GIT_EXECUTABLE)]
    if git_dir is not None:
        base.append(f"--git-dir={git_dir}")
    base.extend([
        "--no-replace-objects", "--no-optional-locks",
        "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "credential.helper=", "-c", "protocol.ext.allow=never",
    ])
    return base


def _archive_git_tree(
    source: Path, treeish: str, destination: Path, state: dict[str, int], *,
    git_dir: Path | None = None,
) -> None:
    base = _git_object_command_base(git_dir=git_dir)
    try:
        listing = _manifest.run_bounded_command(
            [*base, "ls-tree", "-r", "-z", "-t", "--full-tree", treeish], cwd=source,
            env=GIT_OBJECT_ENV, timeout_sec=60,
            max_output_bytes=MAX_GIT_ARCHIVE_LISTING_BYTES,
        )
        if listing.error or listing.returncode != 0:
            raise ValueError("candidate archive failed")
        entries: list[tuple[str, str, str, Path]] = []
        for raw_text in listing.stdout.split("\0"):
            if "\ufffd" in raw_text:
                raise ValueError("candidate archive failed")
            raw = raw_text.encode("utf-8")
            if not raw:
                continue
            header, separator, path_bytes = raw.partition(b"\t")
            fields = header.split(b" ")
            if not separator or len(fields) != 3:
                raise ValueError("candidate archive failed")
            mode, object_type, object_id = (field.decode("ascii") for field in fields)
            path_text = path_bytes.decode("utf-8")
            relative = Path(path_text)
            if (
                not path_text or relative.is_absolute() or ".." in relative.parts
                or relative.as_posix() != path_text
            ):
                raise ValueError("unsafe candidate archive path")
            if len(relative.parts) > MAX_SNAPSHOT_DEPTH:
                raise ValueError("snapshot depth limit exceeded")
            if object_type not in {"tree", "blob"}:
                raise ValueError("unsupported candidate archive entry")
            entries.append((mode, object_type, object_id, relative))
        if len(entries) + state["entries"] > MAX_SNAPSHOT_ENTRIES:
            raise ValueError("snapshot entry limit exceeded")
        destination.mkdir()
        batch = subprocess.Popen(
            [*base, "cat-file", "--batch"], cwd=source, env=GIT_OBJECT_ENV,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            assert batch.stdin is not None and batch.stdout is not None
            for mode, object_type, object_id, relative in entries:
                state["entries"] += 1
                target = destination / relative
                if object_type == "tree":
                    if mode != "040000":
                        raise ValueError("unsupported candidate archive entry")
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                batch.stdin.write(object_id.encode("ascii") + b"\n")
                batch.stdin.flush()
                header = batch.stdout.readline().rstrip(b"\n").split(b" ")
                if len(header) != 3 or header[0].decode("ascii") != object_id or header[1] != b"blob":
                    raise ValueError("candidate archive failed")
                size = int(header[2])
                if size < 0 or size > MAX_SNAPSHOT_FILE_BYTES:
                    raise ValueError("snapshot file byte limit exceeded")
                if size > MAX_SNAPSHOT_BYTES - state["bytes"]:
                    raise ValueError("snapshot byte limit exceeded")
                state["bytes"] += size
                target.parent.mkdir(parents=True, exist_ok=True)
                if mode == "120000":
                    if size > MAX_GIT_SYMLINK_BYTES:
                        raise ValueError("snapshot symlink byte limit exceeded")
                    content = batch.stdout.read(size)
                    if len(content) != size:
                        raise ValueError("candidate archive truncated")
                    link = content.decode("utf-8")
                    if not _lexical_symlink_within_root(relative.parent, link):
                        raise ValueError("unsafe symlink in candidate archive")
                    os.symlink(link, target)
                elif mode in {"100644", "100755"}:
                    output_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    try:
                        remaining = size
                        while remaining:
                            chunk = batch.stdout.read(min(GIT_BLOB_CHUNK_BYTES, remaining))
                            if not chunk:
                                raise ValueError("candidate archive truncated")
                            remaining -= len(chunk)
                            view = memoryview(chunk)
                            while view:
                                written = os.write(output_fd, view)
                                view = view[written:]
                        os.fchmod(output_fd, 0o755 if mode == "100755" else 0o644)
                    finally:
                        os.close(output_fd)
                else:
                    raise ValueError("unsupported candidate archive entry")
                if batch.stdout.read(1) != b"\n":
                    raise ValueError("candidate archive truncated")
        finally:
            if batch.stdin is not None:
                batch.stdin.close()
            with contextlib.suppress(subprocess.TimeoutExpired):
                batch.wait(timeout=10)
            if batch.poll() is None:
                batch.kill()
                batch.wait()
            for stream in (batch.stdout, batch.stderr):
                close = getattr(stream, "close", None)
                if callable(close):
                    with contextlib.suppress(OSError):
                        close()
            if batch.returncode != 0:
                raise ValueError("candidate archive failed")
    except ValueError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ValueError("candidate archive failed") from error


def _archive_repository(source: Path, sha: str, destination: Path, state: dict[str, int]) -> None:
    base = _git_object_command_base()
    try:
        resolved = _manifest.run_bounded_command(
            [*base, "rev-parse", "--verify", f"{sha}^{{commit}}"], cwd=source,
            env=GIT_OBJECT_ENV, timeout_sec=60, max_output_bytes=1024,
        )
        if resolved.error or resolved.returncode != 0 or resolved.stdout.strip() != sha:
            raise ValueError("candidate archive failed")
        _archive_git_tree(source, sha, destination, state)
    except ValueError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ValueError("candidate archive failed") from error


def _require_owned_canonical_gitlink_path(path: Path, *, directory: bool) -> None:
    metadata = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not path.is_absolute() or not expected_type(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or path.resolve(strict=True) != path
    ):
        raise ValueError("candidate archive failed")


def _gitlink_git_directory(source: Path) -> tuple[Path, Path]:
    try:
        submodule_root = source / "components/json/cJSON"
        submodule_git_file = submodule_root / ".git"
        submodule_git_dir = source / ".git/modules/components/json/cJSON"
        _require_owned_canonical_gitlink_path(submodule_root, directory=True)
        _require_owned_canonical_gitlink_path(submodule_git_file, directory=False)
        _require_owned_canonical_gitlink_path(submodule_git_dir, directory=True)
        raw = read_secure_regular(submodule_git_file, MAX_GITLINK_GIT_FILE_BYTES)
        if (
            b"\0" in raw or not raw.endswith(b"\n") or raw.count(b"\n") != 1
            or not raw.startswith(b"gitdir: ")
        ):
            raise ValueError("candidate archive failed")
        target_text = raw[len(b"gitdir: "):-1].decode("utf-8")
        declared = Path(target_text)
        if not target_text or declared.is_absolute():
            raise ValueError("candidate archive failed")
        if (submodule_root / declared).resolve(strict=True) != submodule_git_dir:
            raise ValueError("candidate archive failed")
        return submodule_root, submodule_git_dir
    except ValueError:
        raise
    except (OSError, RuntimeError, UnicodeError) as error:
        raise ValueError("candidate archive failed") from error


def _cjson_entry(source: Path, commit: str, base: list[str]) -> tuple[str, str, str]:
    listing = _manifest.run_bounded_command(
        [*base, "ls-tree", "-z", commit, "--", "components/json/cJSON"],
        cwd=source, env=GIT_OBJECT_ENV, timeout_sec=60, max_output_bytes=1024,
    )
    raw = listing.stdout
    if (
        listing.error or listing.returncode != 0 or "\ufffd" in raw
        or not raw.endswith("\0") or raw.count("\0") != 1
    ):
        raise ValueError("candidate archive failed")
    record = raw[:-1]
    header, separator, path = record.partition("\t")
    fields = header.split(" ")
    if (
        separator != "\t" or len(fields) != 3
        or path != "components/json/cJSON"
        or re.fullmatch(r"[0-9a-f]{40}", fields[2]) is None
        or (fields[0], fields[1]) not in {("040000", "tree"), ("160000", "commit")}
    ):
        raise ValueError("candidate archive failed")
    return fields[0], fields[1], fields[2]


def _run_backend_native_tests(command, candidate, environment, cwd, timeout_sec, max_output_bytes):
    scratch = None
    scratch_identity = None
    parent_fd = None
    interrupted = None
    result = _manifest.BoundedCommandResult(None, "", "authority")
    try:
        parent = Path(environment["COURSE_MODE_NATIVE_TEST_SCRATCH_ROOT"])
        if not parent.is_absolute() or parent != parent.resolve(strict=True):
            raise _backend_native.NativePrerequisiteError("native prerequisite invalid")
        parent_fd, parent_metadata, ancestry = _manifest._open_trusted_source_directory(parent)
        usage = shutil.disk_usage(parent)
        if usage.free < 128 * 1024 * 1024 or usage.free / usage.total < 0.05:
            raise _backend_native.NativePrerequisiteError("native prerequisite invalid")
        scratch = Path(tempfile.mkdtemp(prefix="course-mode-native-", dir=parent))
        scratch_identity = _owned_tree_identity(scratch)
        verification_fd, verification, verified_ancestry = _manifest._open_trusted_source_directory(parent)
        try:
            authority = _manifest._source_directory_authority_identity
            if (authority(os.fstat(parent_fd)) != authority(parent_metadata)
                    or authority(verification) != authority(parent_metadata)
                    or verified_ancestry != ancestry):
                raise _backend_native.NativePrerequisiteError("native scratch parent changed")
        finally:
            os.close(verification_fd)
        tmp = scratch / "tmp"
        tmp.mkdir()
        (scratch / "runtime").mkdir()
        python = candidate["tools"]["pythonTestRuntime"]
        if not _manifest.python_test_runtime_authorized(python):
            raise _backend_native.NativePrerequisiteError("native prerequisite invalid")
        python_tree, error = _manifest.secure_python_test_runtime_tree_descriptor(Path(python["root"]))
        if error or python_tree != python["treeDigest"]:
            raise _backend_native.NativePrerequisiteError("native prerequisite invalid")
        firmware = Path(candidate["repositories"]["firmware"]["path"])
        esp = Path(candidate["repositories"]["adminEsp"]["path"])
        journey = esp / "main/tbot-server/scripts/retained_media_journey.py"
        if not journey.is_file() or journey.is_symlink():
            raise _backend_native.NativePrerequisiteError("native prerequisite invalid")
        clang = Path("/Library/Developer/CommandLineTools/usr/bin/clang")
        compiler, error = _manifest.secure_executable_descriptor(clang)
        if error or compiler is None or clang.stat().st_uid != 0 or clang.stat().st_mode & 0o022:
            raise _backend_native.NativePrerequisiteError("native compiler unavailable")
        sdk = Path("/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk").resolve(strict=True)
        sdk_metadata = sdk.stat()
        if not sdk.is_dir() or sdk_metadata.st_uid != 0 or sdk_metadata.st_mode & 0o022:
            raise _backend_native.NativePrerequisiteError("native SDK unavailable")
        cxx = scratch / "clang-cxx"
        cxx.write_text('#!/bin/sh\nexec /Library/Developer/CommandLineTools/usr/bin/clang --driver-mode=g++ "$@"\n')
        cxx.chmod(0o500)
        native_environment = {
            **environment,
            "PATH": str(Path(python["root"]) / "bin") + ":" + environment["PATH"],
            "TMPDIR": str(tmp), "CJSON_DIR": str(Path(candidate["tools"]["espIdf"]["root"]) / "components/json/cJSON"),
            "CC": str(clang), "CXX": str(cxx),
            "SDKROOT": str(sdk),
            "RETAINED_CONTRACT_VECTORS": str(firmware / "tests/fixtures/retained-assignment-device.v1.vectors.json"),
            "RETAINED_TEST_PYTHON": str(Path(python["root"]) / python["executable"]),
            "RETAINED_TEST_JOURNEY": str(journey),
            "RETAINED_TEST_RUNTIME_ROOT": str(scratch), "RETAINED_TEST_SCRATCH_ROOT": str(tmp),
        }
        build = run_bounded_command(
            ["/bin/bash", str(firmware / "scripts/run_host_native_retained_mcp_test.sh")],
            cwd=firmware, env=native_environment, timeout_sec=180,
            max_output_bytes=max_output_bytes, contain_process_group=True,
        )
        if build.error or build.returncode != 0:
            raise _backend_native.NativeBuildError("native build failed")
        binaries = list(tmp.glob("retained-mcp.*/retained-mcp-test"))
        if len(binaries) != 1:
            raise _backend_native.NativePrerequisiteError("native prerequisite invalid")
        binary = binaries[0]
        observed, error = _manifest.secure_executable_descriptor(binary)
        if error or observed is None:
            raise _backend_native.NativePrerequisiteError("native prerequisite invalid")
        (scratch / "runtime/retained-mcp-binary.json").write_text(json.dumps({
            "path": str(binary), "sha256": observed["sha256"], "compiler": compiler, "sdk": str(sdk),
        }))
        with _backend_native.OwnedPostgres(
            candidate["tools"]["docker"]["path"], candidate["database"]["engineImageId"], environment,
        ) as database:
            result = run_bounded_command(
                list(command), cwd=cwd, timeout_sec=timeout_sec,
                max_output_bytes=max_output_bytes,
                env={
                    **native_environment,
                    "RETAINED_TEST_DATABASE_URL": database.url,
                    "LESSON_RETAINED_TEST_DATABASE_URL": database.url,
                    "LESSON_STORAGE_TEST_DATABASE_URL": database.url,
                    "LESSON_LIFECYCLE_HARDENING_TEST_DATABASE_URL": database.url,
                },
                contain_process_group=True,
            )
        repeated, error = _manifest.secure_python_test_runtime_tree_descriptor(Path(python["root"]))
        if error or repeated != python_tree or not _manifest.backend_test_inputs_valid(candidate):
            result = _manifest.BoundedCommandResult(None, "", "authority")
        observed, error = _manifest.secure_executable_descriptor(clang)
        if error or observed != compiler:
            result = _manifest.BoundedCommandResult(None, "", "authority")
    except _backend_native.NativeCleanupError as error:
        result = _manifest.BoundedCommandResult(None, str(error), "native-cleanup")
    except _backend_native.NativeBuildError:
        result = _manifest.BoundedCommandResult(None, "", "native-build")
    except (_backend_native.NativePrerequisiteError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        result = _manifest.BoundedCommandResult(None, "", "native-prerequisite")
    except BaseException as error:
        interrupted = error
    finally:
        if parent_fd is not None:
            os.close(parent_fd)
        if scratch is not None and not _remove_owned_tree(scratch, scratch_identity):
            retained = (result.stdout + "\n" if result.error == "native-cleanup" else "") + str(scratch)
            result = _manifest.BoundedCommandResult(None, retained, "native-cleanup")
    if interrupted is not None:
        if result.error == "native-cleanup":
            raise RetainedStagingError(scratch) from interrupted
        raise interrupted
    return result


def _stage_backend_test_inputs(candidate: dict, staged: dict, root: Path, state: dict) -> None:
    if not _manifest.backend_test_inputs_valid(candidate):
        raise ValueError("backend test inputs invalid")
    value = candidate["tools"]["backendTestInputs"]["portalOpenapi"]
    destination = root / "inputs/portal/openapi.json"
    _copy_snapshot_file(Path(value["path"]), destination, state, expected_sha256=value["sha256"])
    destination.chmod(0o444)
    staged["tools"]["backendTestInputs"]["portalOpenapi"]["path"] = str(destination)
    if not _manifest.backend_test_inputs_valid(candidate) or not _manifest.backend_test_inputs_valid(staged):
        raise ValueError("backend test inputs changed during staging")


def stage_execution_candidate(candidate: dict, lanes: Sequence[Lane]) -> ExecutionStage:
    temporary_parent = Path("/private/tmp") if sys.platform == "darwin" else Path(tempfile.gettempdir())
    root = Path(tempfile.mkdtemp(prefix="course-mode-stage-", dir=temporary_parent))
    root_identity: tuple[int, int] | None = None
    root_descriptor: int | None = None
    try:
        root_identity = _owned_tree_identity(root)
        root_descriptor = _open_snapshot_directory(root)
        staged = json.loads(json.dumps(candidate))
        state = {"entries": 0, "bytes": 0}
        if "backendTestInputs" in candidate["tools"] or any(lane.name == "backend-tests" for lane in lanes):
            _stage_backend_test_inputs(candidate, staged, root, state)
        repositories_root = root / "repositories"
        repositories_root.mkdir()
        for name, repository in candidate["repositories"].items():
            source = Path(repository["path"])
            destination = repositories_root / name
            _archive_repository(source, repository["sha"], destination, state)
            for exception in sorted(repository["dirtyExceptions"], key=lambda item: item["path"]):
                relative = Path(exception["path"])
                _copy_dirty_exception(
                    source, destination, relative, exception["sha256"], state,
                )
            staged["repositories"][name]["path"] = str(destination)
        tools_root = root / "tools"
        if any(lane.name in {"firmware-handler", "backend-tests"} for lane in lanes):
            descriptor = candidate["tools"]["espIdf"]
            source_value = descriptor["root"]
            commit = descriptor["commit"]
            if (
                not isinstance(source_value, str) or not isinstance(commit, str)
                or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            ):
                raise ValueError("candidate archive failed")
            source = Path(source_value)
            if (
                not source.is_absolute() or source.is_symlink()
                or source != source.resolve(strict=True) or not source.is_dir()
            ):
                raise ValueError("candidate archive failed")
            base = _git_object_command_base()
            resolved_commit = _manifest.run_bounded_command(
                [*base, "rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=source,
                env=GIT_OBJECT_ENV, timeout_sec=60, max_output_bytes=1024,
            )
            if (
                resolved_commit.error or resolved_commit.returncode != 0
                or resolved_commit.stdout.strip() != commit
            ):
                raise ValueError("candidate archive failed")
            mode, _, object_id = _cjson_entry(source, commit, base)
            cjson_parent = tools_root / "esp-idf/components/json"
            cjson_parent.mkdir(parents=True)
            if mode == "040000":
                _archive_git_tree(source, object_id, cjson_parent / "cJSON", state)
            else:
                submodule_root, submodule_git_dir = _gitlink_git_directory(source)
                submodule_base = _git_object_command_base(git_dir=submodule_git_dir)
                resolved_gitlink = _manifest.run_bounded_command(
                    [*submodule_base, "rev-parse", "--verify", f"{object_id}^{{commit}}"],
                    cwd=submodule_root, env=GIT_OBJECT_ENV, timeout_sec=60,
                    max_output_bytes=1024,
                )
                if (
                    resolved_gitlink.error or resolved_gitlink.returncode != 0
                    or resolved_gitlink.stdout.strip() != object_id
                ):
                    raise ValueError("candidate archive failed")
                resolved_tree = _manifest.run_bounded_command(
                    [*submodule_base, "rev-parse", "--verify", f"{object_id}^{{tree}}"],
                    cwd=submodule_root, env=GIT_OBJECT_ENV, timeout_sec=60,
                    max_output_bytes=1024,
                )
                tree_id = resolved_tree.stdout.strip()
                if (
                    resolved_tree.error or resolved_tree.returncode != 0
                    or re.fullmatch(r"[0-9a-f]{40}", tree_id) is None
                ):
                    raise ValueError("candidate archive failed")
                tree_type = _manifest.run_bounded_command(
                    [*submodule_base, "cat-file", "-t", tree_id], cwd=submodule_root,
                    env=GIT_OBJECT_ENV, timeout_sec=60, max_output_bytes=1024,
                )
                if (
                    tree_type.error or tree_type.returncode != 0
                    or tree_type.stdout.strip() != "tree"
                ):
                    raise ValueError("candidate archive failed")
                _archive_git_tree(
                    submodule_root, tree_id, cjson_parent / "cJSON", state,
                    git_dir=submodule_git_dir,
                )
            staged["tools"]["espIdf"]["root"] = str(tools_root / "esp-idf")
        if any(_container_tools_required(lane) for lane in lanes):
            tools_root.mkdir(exist_ok=True)
            for name, basename in (("docker", "docker"), ("dockerCompose", "docker-compose")):
                descriptor = candidate["tools"][name]
                target = tools_root / basename
                _copy_snapshot_file(
                    Path(descriptor["path"]), target, state,
                    expected_sha256=descriptor["sha256"],
                )
                observed, error = _manifest.secure_executable_descriptor(target)
                if error or observed is None or observed["sha256"] != descriptor["sha256"]:
                    raise ValueError(f"staged {name} executable mismatch")
                staged["tools"][name]["path"] = str(target)
        if any(_python_runtime_stage_required(lane) for lane in lanes):
            descriptor = candidate["tools"]["pythonTestRuntime"]
            python_target = tools_root / "python-test-runtime"
            tools_root.mkdir(exist_ok=True)
            source_observed, source_error = (
                _manifest.secure_python_test_runtime_tree_descriptor(Path(descriptor["root"]))
            )
            if source_error or source_observed != descriptor["treeDigest"]:
                raise ValueError("Python test runtime source descriptor mismatch")
            _copy_strict_snapshot_tree_fd(
                Path(descriptor["root"]), tools_root, python_target.name, state,
            )
            observed, error = _manifest.secure_python_test_runtime_tree_descriptor(python_target)
            if error or observed != descriptor["treeDigest"]:
                raise ValueError("staged Python test runtime descriptor mismatch")
            executable = python_target / descriptor["executable"]
            if not executable.is_file() or executable.is_symlink() or not os.access(executable, os.X_OK):
                raise ValueError("staged Python test runtime executable mismatch")
            staged["tools"]["pythonTestRuntime"]["root"] = str(python_target)
            if not _manifest.python_test_runtime_authorized(
                staged["tools"]["pythonTestRuntime"],
            ):
                raise ValueError("staged Python test runtime authority mismatch")
        requirements = {
            requirement for lane in lanes
            if (requirement := _node_install_requirement(lane)) is not None and requirement[0]
        }
        if any(_backend_compiler_required(lane) for lane in lanes):
            requirements.add(("backend", "."))
        required_node_tools = {key for key, _ in requirements}
        for key, descriptor in candidate["tools"]["node"].items():
            if key not in required_node_tools:
                continue
            prefix = tools_root / key
            node = prefix / "bin/node"
            node.parent.mkdir(parents=True)
            _copy_snapshot_file(
                Path(descriptor["executable"]), node, state,
                expected_sha256=descriptor["sha256"],
            )
            staged_descriptor = staged["tools"]["node"][key]
            staged_descriptor["executable"] = str(node)
            package_root = prefix / "lib/node_modules/npm"
            package_root.parent.mkdir(parents=True, exist_ok=True)
            _copy_snapshot_tree(Path(descriptor["packageRoot"]), package_root, state)
            observed_package = _manifest.secure_node_package_tree_descriptor(package_root)
            if (
                observed_package is None
                or observed_package["sha256"] != descriptor["packageTreeSha256"]
                or observed_package["rootMode"] != descriptor["packageRootMode"]
            ):
                raise ValueError("staged npm package descriptor mismatch")
            staged_descriptor["packageRoot"] = str(package_root)
            staged_descriptor["packageRootMode"] = observed_package["rootMode"]
            for tool in ("npm", "npx"):
                entrypoint = prefix / f"lib/node_modules/npm/bin/{tool}-cli.js"
                if _secure_file_sha256(entrypoint, MAX_PACKAGE_LOCK_BYTES) != descriptor[tool]["sha256"]:
                    raise ValueError("staged npm entrypoint descriptor mismatch")
                staged_descriptor[tool]["entrypoint"] = str(entrypoint)
        for key, relative_cwd in requirements:
            metadata = candidate["tools"]["nodeInstalls"][key]
            install_source = Path(metadata["root"])
            repository_name = "backend" if key == "backend" else "adminEsp"
            install_target = (
                Path(staged["repositories"][repository_name]["path"])
                / relative_cwd / "node_modules"
            )
            _copy_snapshot_tree(install_source, install_target, state)
            staged_lock = install_target.parent / "package-lock.json"
            observed = describe_node_install(install_target, staged_lock)
            expected = json.loads(json.dumps(metadata))
            expected["root"] = str(install_target)
            if not _json_exact_equal(observed, expected):
                raise ValueError("staged Node installation descriptor mismatch")
            staged["tools"]["nodeInstalls"][key] = observed
        if any(_backend_compiler_required(lane) for lane in lanes):
            backend_root = Path(staged["repositories"]["backend"]["path"])
            backend_node = staged["tools"]["node"]["backend"]
            build_runtime = root / "build-runtime"
            build_home = build_runtime / "home"
            build_tmp = build_runtime / "tmp"
            build_cache = build_runtime / "cache"
            for directory in (build_home, build_tmp, build_cache):
                directory.mkdir(parents=True, exist_ok=True)
            build_command = _sandboxed_backend_build_command(
                [
                    backend_node["executable"], backend_node["npm"]["entrypoint"],
                    "run", "build",
                ],
                backend_root, build_runtime,
            )
            if build_command is None:
                raise ValueError("staged backend compiler sandbox unavailable")
            build = _manifest.run_bounded_command(
                list(build_command),
                cwd=backend_root,
                timeout_sec=300.0,
                max_output_bytes=MAX_LANE_OUTPUT_BYTES,
                env={
                    **BASE_ENVIRONMENT,
                    "PATH": f"{Path(backend_node['executable']).parent}:{SECURE_PATH}",
                    "HOME": str(build_home),
                    "TMPDIR": str(build_tmp),
                    "XDG_CACHE_HOME": str(build_cache),
                    "npm_config_cache": str(build_cache / "npm"),
                },
            )
            if build.error or build.returncode != 0:
                raise ValueError("staged backend compiler failed")
            for relative in BACKEND_COMPILER_OUTPUTS:
                output = backend_root / relative
                try:
                    metadata = output.lstat()
                except OSError as error:
                    raise ValueError("staged backend compiler output mismatch") from error
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("staged backend compiler output mismatch")
        if any(_playwright_browsers_required(lane) for lane in lanes):
            browser_cache = tools_root / "playwright-browsers"
            browser_cache.mkdir()
            for engine, browser in candidate["tools"]["playwrightBrowsers"].items():
                browser_source = Path(browser["root"])
                source_observed, source_error = secure_playwright_browser_bundle_descriptor(
                    browser_source,
                )
                if source_error or source_observed != browser["treeDigest"]:
                    raise ValueError(f"Playwright {engine} source descriptor mismatch")
                browser_target = browser_cache / browser_source.name
                _copy_snapshot_tree(browser_source, browser_target, state)
                observed, error = secure_playwright_browser_bundle_descriptor(browser_target)
                if error or observed != browser["treeDigest"]:
                    raise ValueError(f"staged Playwright {engine} descriptor mismatch")
                staged["tools"]["playwrightBrowsers"][engine]["root"] = str(browser_target)
        if any(lane.name == "admin-browser" for lane in lanes):
            browser = candidate["tools"]["robotPreviewBrowser"]
            browser_source = Path(browser["root"])
            browser_target = tools_root / browser_source.parent.name / browser_source.name
            browser_target.parent.mkdir()
            _copy_snapshot_tree(browser_source, browser_target, state)
            observed, error = secure_browser_bundle_descriptor(browser_target)
            if error or observed != browser["treeDigest"]:
                raise ValueError("staged browser descriptor mismatch")
            staged["tools"]["robotPreviewBrowser"]["root"] = str(browser_target)
        if any(
            _python_test_runtime_required(lane) or _backend_compiler_required(lane)
            for lane in lanes
        ):
            backend_root = Path(staged["repositories"]["backend"]["path"])
            _make_tree_read_only(backend_root)
            source_tree, source_tree_error = _manifest.secure_backend_snapshot_tree_descriptor(
                backend_root,
            )
            execution_tree, execution_tree_error = (
                _manifest.secure_backend_execution_tree_descriptor(backend_root)
            )
            if (
                source_tree_error or source_tree is None
                or execution_tree_error or execution_tree is None
            ):
                raise ValueError("staged backend snapshot descriptor mismatch")
            authority_root = root / ".course-mode-authority"
            authority_root.mkdir()
            (authority_root / "backend.json").write_text(json.dumps({
                "repository": "backend", "root": str(backend_root),
                "sha": staged["repositories"]["backend"]["sha"],
                "sourceTreeDigest": source_tree,
                "executionTreeDigest": execution_tree, "version": 3,
            }, sort_keys=True), encoding="utf-8")
        _make_tree_read_only(root)
        if any(_playwright_browsers_required(lane) for lane in lanes):
            for engine, browser in staged["tools"]["playwrightBrowsers"].items():
                observed, error = secure_playwright_browser_bundle_descriptor(
                    Path(browser["root"]),
                )
                if error or observed is None:
                    raise ValueError(f"read-only Playwright {engine} descriptor mismatch")
                browser["treeDigest"] = observed
        stage = ExecutionStage(root, staged, root_identity, root_descriptor)
        root_descriptor = None
        return stage
    except Exception:
        retained_path = (
            _directory_fd_path(root_descriptor) if root_descriptor is not None else None
        ) or root
        removed = _remove_owned_tree(retained_path, root_identity)
        if not removed and root_descriptor is not None:
            retained_path = _directory_fd_path(root_descriptor) or retained_path
        if root_descriptor is not None:
            os.close(root_descriptor)
        if not removed:
            raise RetainedStagingError(retained_path)
        raise


def _lane(
    name: str,
    repository: str,
    relative_cwd: str,
    command: tuple[str, ...],
    timeout_sec: float = 900.0,
    required_environment: tuple[str, ...] | str = (),
    fixed_environment: tuple[tuple[str, str], ...] = (),
    required_source_contract: str | None = None,
    reject_pytest_skips: bool = False,
) -> Lane:
    return Lane(
        name, repository, relative_cwd, command, timeout_sec, required_environment,
        fixed_environment, required_source_contract, reject_pytest_skips,
    )


QUICK_LANES = (
    _lane(
        "backend-course-mode-focused", "backend", ".",
        ("npx", "vitest", "run", "src/lessons/course-mode", "tests/verify-course-mode-curriculum.spec.ts"),
    ),
    _lane(
        "admin-course-mode-logic", "adminEsp", "main/manager-web",
        ("npm", "run", "test:course-admin-ui"),
    ),
    _lane(
        "esp-course-mode-focused", "adminEsp", "main/tbot-server",
        (
            "python3", "-m", "pytest", "-q",
            "tests/test_course_mode_curriculum_e2e.py",
            "tests/test_course_mode_runtime_integration.py",
        ),
    ),
    _lane(
        "firmware-course-mode-focused", "firmware", ".",
        ("bash", "scripts/run_host_native_lesson_cinematic_renderer_test.sh"),
    ),
)


FULL_LANES = (
    _lane("backend-lint", "backend", ".", ("npm", "run", "lint")),
    _lane("backend-typecheck", "backend", ".", ("npm", "run", "typecheck")),
    _lane("backend-tests", "backend", ".", ("npm", "test", "--", "--no-cache"), 1800.0,
          ("COURSE_MODE_NATIVE_TEST_SCRATCH_ROOT",)),
    _lane("backend-build", "backend", ".", ("npm", "run", "build")),
    _lane(
        "backend-curriculum-verifier", "backend", ".",
        ("node", "scripts/verify-course-mode-curriculum.mjs"),
    ),
    _lane("admin-logic", "adminEsp", "main/manager-web", ("npm", "run", "test:course-admin-ui")),
    _lane("admin-browser", "adminEsp", "main/manager-web", ("npm", "run", "test:lesson-studio")),
    _lane("admin-build", "adminEsp", "main/manager-web", ("npm", "run", "build"), 1200.0),
    *(
        _lane(
            f"admin-course-mode-playwright-{project.removeprefix('course-mode-')}",
            "adminEsp", "main/manager-web",
            ("npm", "run", "test:e2e:course-mode", "--", f"--project={project}"),
            1200.0, PLAYWRIGHT_COMPOSE_ENV,
            required_source_contract="course-mode-playwright",
        )
        for project in PLAYWRIGHT_PROJECTS
    ),
    _lane(
        "admin-course-mode-assignment-fixture", "adminEsp", "main/manager-web",
        ("npm", "run", "test:course-mode:assignment-fixture"),
        required_source_contract="course-mode-playwright",
    ),
    _lane(
        "admin-course-mode-assignment-new", "adminEsp", "main/manager-web",
        ("npm", "run", "test:e2e:course-mode:assignment:new"), 1200.0,
        TASK4_ASSIGNMENT_CANDIDATE_ENV,
        required_source_contract="course-mode-playwright",
    ),
    _lane(
        "admin-course-mode-assignment-rollback", "adminEsp", "main/manager-web",
        ("npm", "run", "test:e2e:course-mode:assignment:rollback"), 1200.0,
        TASK4_ASSIGNMENT_CANDIDATE_ENV,
        required_source_contract="course-mode-playwright",
    ),
    _lane(
        "esp-course-mode-full", "adminEsp", "main/tbot-server",
        (COURSE_MODE_SOFTWARE_TESTS,),
        1800.0,
        reject_pytest_skips=True,
    ),
    _lane(
        "firmware-renderer", "firmware", ".",
        ("bash", "scripts/run_host_native_lesson_cinematic_renderer_test.sh"),
    ),
    _lane(
        "firmware-handler", "firmware", ".",
        ("bash", "scripts/run_host_native_lesson_handler_test.sh"),
    ),
    _lane(
        "firmware-backward-compatibility", "firmware", ".",
        ("bash", "scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh"),
    ),
    _lane(
        "cross-contract-parity", "adminEsp", "main/tbot-server",
        (
            "python3", "-m", "pytest", "-q", "tests/test_lesson_contract_vectors_parity.py",
            "tests/test_lesson_passive_parity_with_esp.py", "tests/test_course_mode_runtime_compatibility.py",
        ),
    ),
)


LIVE_DB_LANE = _lane(
    "live-postgres", "backend", ".",
    (
        "npx", "vitest", "run", "tests/course-mode-v2.migration.spec.ts",
        "tests/course-mode-v2.postgres.spec.ts", "tests/course-mode-curriculum.migration.spec.ts",
        "tests/course-mode-curriculum.postgres.spec.ts",
        "tests/integration/course-mode-curriculum.postgres.spec.ts",
        "tests/integration/course-mode-local-materializer.integration.spec.ts",
    ),
    1800.0,
    (
        "COURSE_MODE_V2_TEST_DATABASE_URL",
        "COURSE_MODE_TEST_DATABASE_URL",
        "DATABASE_URL",
        "COURSE_MODE_ROLLBACK_TEST_DATABASE_URL",
    ),
    (
        ("TBOT_RUN_LIVE_DB_TESTS", "true"),
        ("COURSE_MODE_TEST_DATABASE_CONFIRMED", "1"),
    ),
)

LIVE_DB_URL_VARIABLES = (
    "COURSE_MODE_V2_TEST_DATABASE_URL",
    "COURSE_MODE_TEST_DATABASE_URL",
    "DATABASE_URL",
    "COURSE_MODE_ROLLBACK_TEST_DATABASE_URL",
)
POSTGRES_IDENTITY_QUERY_KEYS = {
    "database", "dbname", "host", "hostaddr", "port", "service", "servicefile",
}


PHYSICAL_PREFLIGHT_LANE = _lane(
    "physical-flash-admission", "adminEsp", "main/tbot-server",
    ("python3", "scripts/course_mode_physical_flash_admission.py"),
    900.0,
)

PHYSICAL_ADMISSION_BOOTSTRAP = (
    "import base64,os,sys,types;"
    "m=types.ModuleType('course_mode_candidate_manifest');"
    "m.__file__=sys.argv[1];sys.modules[m.__name__]=m;"
    "exec(compile(base64.b64decode(os.environ.pop('TBOT_ADMISSION_MANIFEST_B64')),"
    "m.__file__,'exec'),m.__dict__);"
    "t=types.ModuleType('course_mode_physical_tft_preflight');"
    "t.__file__=sys.argv[2];sys.modules[t.__name__]=t;"
    "exec(compile(base64.b64decode(os.environ.pop('TBOT_ADMISSION_PREFLIGHT_B64')), "
    "t.__file__,'exec'),t.__dict__);"
    "s=types.ModuleType('course_mode_software_evidence_snapshot');"
    "s.__file__=sys.argv[3];sys.modules[s.__name__]=s;"
    "exec(compile(base64.b64decode(os.environ.pop('TBOT_ADMISSION_SNAPSHOT_B64')), "
    "s.__file__,'exec'),s.__dict__);"
    "sys.argv=sys.argv[4:];p=sys.argv[0];"
    "exec(compile(base64.b64decode(os.environ.pop('TBOT_ADMISSION_MAIN_B64')),"
    "p,'exec'),{'__name__':'__main__','__file__':p,'__builtins__':__builtins__})"
)

PHYSICAL_ADMISSION_SOURCE_PATHS = (
    "main/tbot-server/scripts/course_mode_physical_flash_admission.py",
    "main/tbot-server/scripts/course_mode_candidate_manifest.py",
    "main/tbot-server/scripts/course_mode_physical_tft_preflight.py",
    "main/tbot-server/scripts/course_mode_software_evidence_snapshot.py",
)


def lanes_for_mode(mode: str) -> tuple[Lane, ...]:
    if mode == "quick":
        return QUICK_LANES
    if mode == "full":
        return FULL_LANES
    if mode == "live-db":
        return (*FULL_LANES, LIVE_DB_LANE)
    if mode == "physical-preflight":
        return (PHYSICAL_PREFLIGHT_LANE,)
    raise ValueError("unsupported mode")


def _blocked(candidate_id: str | None, failed_lane: str) -> dict:
    return {"candidateId": candidate_id, "verdict": "BLOCKED", "lanes": [], "failedLane": failed_lane}


def _load_candidate(path: Path) -> dict | None:
    try:
        candidate = strict_json_loads(read_secure_regular(path, MAX_CANDIDATE_BYTES))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return candidate if isinstance(candidate, dict) else None


def _candidate_matches(candidate: dict) -> bool:
    repositories = candidate.get("repositories")
    if not isinstance(repositories, dict):
        return False
    try:
        return all(
            _repository_matches_candidate(Path(repositories[name]["path"]), repositories[name])
            for name in ("backend", "adminEsp", "firmware")
        )
    except (KeyError, TypeError):
        return False


def candidate_paths_match(repository: Mapping[str, object], relative_paths: Sequence[str]) -> bool:
    try:
        root = Path(repository["path"]).resolve(strict=True)
        sha = repository["sha"]
        dirty = {item["path"] for item in repository["dirtyExceptions"]}
        if not isinstance(sha, str):
            return False
        for relative in sorted(set(relative_paths)):
            path_value = Path(relative)
            if (
                not relative or path_value.is_absolute() or ".." in path_value.parts
                or relative in dirty
            ):
                return False
            path = root / relative
            if not path.is_file() or path.is_symlink():
                return False
            committed_blob = _candidate_git(root, "rev-parse", f"{sha}:{relative}").strip()
            working_blob = _candidate_git(root, "hash-object", "--", relative).strip()
            if committed_blob != working_blob:
                return False
        return True
    except (KeyError, OSError, RuntimeError, TypeError):
        return False


def assignment_input_sources_ready(candidate: dict) -> bool:
    try:
        repositories = candidate["repositories"]
        selections = (
            (repositories["backend"], TASK4_BACKEND_MOUNT_ROOTS),
            (repositories["firmware"], TASK4_FIRMWARE_MOUNT_ROOTS),
        )
        for repository, roots in selections:
            dirty = tuple(item["path"] for item in repository["dirtyExceptions"])
            if any(
                dirty_path == root or dirty_path.startswith(f"{root}/")
                or root.startswith(f"{dirty_path}/")
                for dirty_path in dirty for root in roots
            ):
                return False
            tracked = tuple(filter(None, _candidate_git(
                Path(repository["path"]), "ls-tree", "-r", "--name-only", "-z",
                repository["sha"], "--", *roots,
            ).split("\0")))
            if not tracked or not candidate_paths_match(repository, tracked):
                return False
            repository_root = Path(repository["path"])
            current: set[str] = set()
            for relative in roots:
                mounted = repository_root / relative
                if mounted.is_symlink() or not mounted.exists():
                    return False
                candidates = (mounted,) if mounted.is_file() else mounted.rglob("*")
                for path in candidates:
                    if path.is_symlink():
                        return False
                    if path.is_file():
                        current.add(path.relative_to(repository_root).as_posix())
            if current != set(tracked):
                return False
        return True
    except (KeyError, RuntimeError, TypeError):
        return False


def _runtime_matches_candidate(candidate: dict, runtime_root: Path | None) -> bool:
    repository = candidate["repositories"]["adminEsp"]
    expected = Path(repository["path"])
    actual = runtime_root if runtime_root is not None else Path(__file__).resolve().parents[3]
    try:
        if actual.resolve(strict=True) != expected.resolve(strict=True):
            return False
        bound = (
            "main/tbot-server/scripts/course_mode_release_gate.py",
            "main/tbot-server/scripts/course_mode_candidate_manifest.py",
            "scripts/course_robot_e2e_gates.sh",
        )
        if not candidate_paths_match(repository, bound):
            return False
        if runtime_root is None:
            expected_script = expected / "main/tbot-server/scripts/course_mode_release_gate.py"
            expected_helper = expected / "main/tbot-server/scripts/course_mode_candidate_manifest.py"
            return (
                expected_script.resolve(strict=True) == Path(__file__).resolve(strict=True)
                and expected_helper.resolve(strict=True) == Path(_manifest.__file__).resolve(strict=True)
            )
        return True
    except (KeyError, OSError, RuntimeError, TypeError):
        return False


def _required_environment(lane: Lane) -> tuple[str, ...]:
    if lane.required_environment is None:
        return ()
    if isinstance(lane.required_environment, str):
        return (lane.required_environment,) if lane.required_environment else ()
    return lane.required_environment


def select_esp_software_tests(admin_root: Path, sha: str) -> tuple[str, ...]:
    try:
        tracked = set(_candidate_git(
            admin_root, "ls-tree", "-r", "--name-only", "-z", sha, "--",
            "main/tbot-server/tests",
        ).split("\0"))
    except RuntimeError:
        return ()
    required = {f"main/tbot-server/{relative}" for relative in ESP_COURSE_MODE_FULL_TESTS}
    return ESP_COURSE_MODE_FULL_TESTS if required <= tracked else ()

def _playwright_spec_paths(admin_root: Path, sha: str) -> tuple[str, ...]:
    try:
        tracked = _candidate_git(
            admin_root, "ls-tree", "-r", "--name-only", sha, "--",
            "main/manager-web/e2e/lesson-studio",
        ).splitlines()
    except RuntimeError:
        return ()
    return tuple(
        relative for relative in tracked
        if Path(relative).name.startswith("course-mode") and relative.endswith(".spec.js")
    )


_COMMON_JS_REQUIRE = re.compile(
    r"^[ \t]*(?:(?:(?:const|let|var)\b[^\n=]*|})\s*=\s*)?"
    r"require\(\s*(['\"])(\.[^'\"]*)\1\s*\)",
    re.MULTILINE,
)


def _playwright_harness_paths(
    admin_root: Path, sha: str, contract: Mapping[str, object], specs: Sequence[str],
) -> tuple[str, ...] | None:
    fixed = contract.get("fixed")
    global_setup = fixed.get("globalSetup") if isinstance(fixed, dict) else None
    if not isinstance(global_setup, str):
        return None
    web_prefix = "main/manager-web/"
    pending = ["main/manager-web/playwright.config.js", *specs]
    pending.append(web_prefix + global_setup.removeprefix("./"))
    discovered: set[str] = set()
    while pending:
        relative = posixpath.normpath(pending.pop())
        if relative in discovered:
            continue
        if not relative.startswith(web_prefix) or relative.startswith("../"):
            return None
        source = _committed_text(admin_root, sha, relative)
        if source is None:
            return None
        discovered.add(relative)
        for match in _COMMON_JS_REQUIRE.finditer(source):
            requested = match.group(2)
            base = posixpath.normpath(posixpath.join(posixpath.dirname(relative), requested))
            candidates = (base, f"{base}.js", f"{base}.cjs", f"{base}.mjs", f"{base}/index.js")
            resolved = next(
                (candidate for candidate in candidates if _committed_text(admin_root, sha, candidate) is not None),
                None,
            )
            if resolved is None or not resolved.startswith(web_prefix):
                return None
            pending.append(resolved)
    return tuple(sorted(discovered))


def lane_candidate_paths(lane: Lane, candidate: dict) -> tuple[str, ...]:
    repository = candidate["repositories"][lane.repository]
    root = Path(repository["path"])
    paths: set[str] = set()
    if lane.repository == "adminEsp" and lane.relative_cwd == "main/manager-web":
        paths.add("main/manager-web/package.json")
        if (root / "main/manager-web/package-lock.json").is_file():
            paths.add("main/manager-web/package-lock.json")
    if lane.required_source_contract == "course-mode-playwright":
        paths.add("main/manager-web/playwright.config.js")
        paths.add(PLAYWRIGHT_CONTRACT_PATH)
        paths.update(_playwright_spec_paths(root, repository["sha"]))
    if lane.name == "esp-course-mode-full":
        paths.add("main/tbot-server/pyproject.toml")
        paths.update(f"main/tbot-server/{relative}" for relative in ESP_COURSE_MODE_FULL_TESTS)
    elif lane.repository == "adminEsp" and lane.relative_cwd == "main/tbot-server":
        paths.update(
            f"main/tbot-server/{token}" for token in lane.command
            if token.endswith(".py") and not Path(token).is_absolute()
        )
    if lane.repository == "backend" and lane.command[0] in {"npm", "npx", "node"}:
        paths.add("package.json")
        if (root / "package-lock.json").is_file():
            paths.add("package-lock.json")
    for token in lane.command:
        if "/" in token and not token.startswith(("--", "@")) and not Path(token).is_absolute():
            candidate_path = root / lane.relative_cwd / token
            if candidate_path.is_file():
                paths.add(candidate_path.relative_to(root).as_posix())
    return tuple(sorted(paths))


def lane_dirty_exceptions_authorized(lane: Lane, candidate: dict) -> bool:
    try:
        if lane.name == "physical-flash-admission" and any(
            candidate["repositories"][name]["dirtyExceptions"]
            for name in ("backend", "firmware")
        ):
            return False
        repository = candidate["repositories"][lane.repository]
        dirty = {item["path"] for item in repository["dirtyExceptions"]}
    except (KeyError, TypeError):
        return False
    if lane.repository in {"backend", "firmware"}:
        return not dirty
    if lane.repository != "adminEsp":
        return False
    return not dirty


def _digest_field(digest: "hashlib._Hash", value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _stat_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid


def _secure_file_sha256(path: Path, max_bytes: int) -> str | None:
    descriptor = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > max_bytes:
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return None
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                return None
            digest.update(chunk)
        after = path.lstat()
        final = os.fstat(descriptor)
        if (
            _stat_identity(after) != _stat_identity(before)
            or _stat_identity(final) != _stat_identity(opened)
        ):
            return None
        return digest.hexdigest()
    except (OSError, RuntimeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _node_tree_descriptor(root: Path) -> dict | None:
    root_fd = None
    try:
        if not root.is_absolute():
            return None
        root_fd = _open_snapshot_directory(root)
        root_opened = os.fstat(root_fd)
        if not stat.S_ISDIR(root_opened.st_mode):
            return None
        digest = hashlib.sha256()
        _digest_field(digest, NODE_TREE_SCHEMA.encode("ascii"))
        _digest_field(digest, b"directory")
        _digest_field(digest, b".")
        _digest_field(digest, str(stat.S_IMODE(root_opened.st_mode)).encode("ascii"))
        state = {"entryCount": 1, "totalBytes": 0}

        def visit(directory_fd: int, relative_parent: Path, depth: int) -> bool:
            if depth > MAX_NODE_INSTALL_DEPTH:
                return False
            try:
                names = sorted(entry.name for entry in os.scandir(directory_fd))
            except OSError:
                return False
            for name in names:
                relative = relative_parent / name
                relative_bytes = relative.as_posix().encode("utf-8")
                if len(relative_bytes) > 4096:
                    return False
                try:
                    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    return False
                state["entryCount"] += 1
                if state["entryCount"] > MAX_NODE_INSTALL_ENTRIES:
                    return False
                mode = stat.S_IMODE(before.st_mode)
                if stat.S_ISDIR(before.st_mode):
                    _digest_field(digest, b"directory")
                    _digest_field(digest, relative_bytes)
                    _digest_field(digest, str(mode).encode("ascii"))
                    child_fd = None
                    try:
                        child_fd = os.open(
                            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                        opened = os.fstat(child_fd)
                        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                            return False
                        if not visit(child_fd, relative, depth + 1):
                            return False
                        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        final = os.fstat(child_fd)
                        if (
                            _stat_identity(after) != _stat_identity(before)
                            or _stat_identity(final) != _stat_identity(opened)
                        ):
                            return False
                    except OSError:
                        return False
                    finally:
                        if child_fd is not None:
                            os.close(child_fd)
                elif stat.S_ISREG(before.st_mode):
                    if before.st_nlink != 1:
                        return False
                    state["totalBytes"] += before.st_size
                    if state["totalBytes"] > MAX_NODE_INSTALL_BYTES:
                        return False
                    _digest_field(digest, b"regular")
                    _digest_field(digest, relative_bytes)
                    _digest_field(digest, str(mode).encode("ascii"))
                    _digest_field(digest, str(before.st_size).encode("ascii"))
                    file_fd = None
                    try:
                        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
                        opened = os.fstat(file_fd)
                        if (
                            _stat_identity(opened) != _stat_identity(before)
                        ):
                            return False
                        remaining = before.st_size
                        while remaining:
                            chunk = os.read(file_fd, min(1024 * 1024, remaining))
                            if not chunk:
                                return False
                            digest.update(chunk)
                            remaining -= len(chunk)
                        if os.read(file_fd, 1):
                            return False
                        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        final = os.fstat(file_fd)
                        if (
                            _stat_identity(after) != _stat_identity(before)
                            or _stat_identity(final) != _stat_identity(opened)
                        ):
                            return False
                    except OSError:
                        return False
                    finally:
                        if file_fd is not None:
                            os.close(file_fd)
                elif stat.S_ISLNK(before.st_mode):
                    try:
                        target = os.readlink(name, dir_fd=directory_fd)
                        target_bytes = os.fsencode(target)
                        if not _lexical_symlink_within_root(relative.parent, target):
                            return False
                        if os.readlink(name, dir_fd=directory_fd) != target:
                            return False
                        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if _stat_identity(after) != _stat_identity(before):
                            return False
                    except (OSError, RuntimeError, ValueError):
                        return False
                    state["totalBytes"] += len(target_bytes)
                    if state["totalBytes"] > MAX_NODE_INSTALL_BYTES:
                        return False
                    _digest_field(digest, b"symlink")
                    _digest_field(digest, relative_bytes)
                    _digest_field(digest, str(mode).encode("ascii"))
                    _digest_field(digest, target_bytes)
                else:
                    return False
            return True

        if not visit(root_fd, Path(), 0):
            return None
        root_final = os.fstat(root_fd)
        if _stat_identity(root_final) != _stat_identity(root_opened):
            return None
        return {
            "schema": NODE_TREE_SCHEMA,
            "sha256": digest.hexdigest(),
            "entryCount": state["entryCount"],
            "totalBytes": state["totalBytes"],
        }
    except (OSError, RuntimeError):
        return None
    finally:
        if root_fd is not None:
            os.close(root_fd)


def describe_node_install(root: Path, package_lock: Path) -> dict:
    tree = _node_tree_descriptor(root)
    lock_digest = _secure_file_sha256(package_lock, MAX_PACKAGE_LOCK_BYTES)
    if tree is None or lock_digest is None:
        raise ValueError("unsafe Node installation")
    return {
        "version": 1,
        "root": str(root),
        "packageLockSha256": lock_digest,
        "treeDigest": tree,
    }


def _has_unbound_node_modules(project_root: Path, allowed_root: Path) -> bool | None:
    root_fd = None
    try:
        if not project_root.is_absolute():
            return None
        allowed_relative = allowed_root.relative_to(project_root)
        root_fd = _open_snapshot_directory(project_root)
        root_opened = os.fstat(root_fd)
        if not stat.S_ISDIR(root_opened.st_mode):
            return None
        state = {"entries": 0}

        def visit(directory_fd: int, relative_parent: Path, depth: int) -> bool | None:
            if depth > MAX_NODE_INSTALL_DEPTH:
                return None
            try:
                names = sorted(entry.name for entry in os.scandir(directory_fd))
            except OSError:
                return None
            for name in names:
                relative = relative_parent / name
                state["entries"] += 1
                if state["entries"] > MAX_NODE_PROJECT_SCAN_ENTRIES:
                    return None
                try:
                    before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    return None
                if name.casefold() == "node_modules":
                    if relative != allowed_relative or not stat.S_ISDIR(before.st_mode):
                        return True
                    continue
                if relative == Path(".git"):
                    continue
                if stat.S_ISDIR(before.st_mode):
                    child_fd = None
                    try:
                        child_fd = os.open(
                            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory_fd,
                        )
                        opened = os.fstat(child_fd)
                        if _stat_identity(opened) != _stat_identity(before):
                            return None
                        result = visit(child_fd, relative, depth + 1)
                        if result is not False:
                            return result
                        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        final = os.fstat(child_fd)
                        if (
                            _stat_identity(after) != _stat_identity(before)
                            or _stat_identity(final) != _stat_identity(opened)
                        ):
                            return None
                    except OSError:
                        return None
                    finally:
                        if child_fd is not None:
                            os.close(child_fd)
                elif not (stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)):
                    return None
            return False

        result = visit(root_fd, Path(), 0)
        root_final = os.fstat(root_fd)
        if _stat_identity(root_final) != _stat_identity(root_opened):
            return None
        return result
    except (OSError, RuntimeError, ValueError):
        return None
    finally:
        if root_fd is not None:
            os.close(root_fd)


def _node_install_requirement(lane: Lane) -> tuple[str, str] | None:
    if (
        lane.repository == "adminEsp"
        and lane.relative_cwd == "main/tbot-server"
        and (
            lane.command == (COURSE_MODE_SOFTWARE_TESTS,)
            or (
                lane.command[:3] == ("python3", "-m", "pytest")
                and "tests/test_course_mode_curriculum_e2e.py" in lane.command
            )
        )
    ):
        return "backend", "."
    if lane.command[0] not in {"npm", "npx", "node"}:
        return None
    if lane.repository == "backend" and lane.relative_cwd == ".":
        return "backend", "."
    if lane.repository == "adminEsp" and lane.relative_cwd == "main/manager-web":
        return "adminManagerWeb", "main/manager-web"
    return "", ""


def _python_test_runtime_required(lane: Lane) -> bool:
    return (
        lane.command == (COURSE_MODE_SOFTWARE_TESTS,)
        or lane.command[:3] == ("python3", "-m", "pytest")
    )


def _python_runtime_stage_required(lane: Lane) -> bool:
    return lane.name in {"physical-flash-admission", "backend-tests"} or _python_test_runtime_required(lane)


def _container_tools_required(lane: Lane) -> bool:
    return (
        lane.name == "backend-tests"
        or lane.name.startswith("admin-course-mode-playwright-")
        or lane.name.startswith("admin-course-mode-assignment-")
    )


def _playwright_browsers_required(lane: Lane) -> bool:
    return (
        lane.name == "admin-browser"
        or lane.name.startswith("admin-course-mode-playwright-")
        or lane.name in STATEFUL_ASSIGNMENT_LANES
    )


def _backend_compiler_required(lane: Lane) -> bool:
    return (
        lane.name.startswith("admin-course-mode-playwright-")
        or lane.name in STATEFUL_ASSIGNMENT_LANES
    )


def _container_tools_authorized(candidate: dict) -> bool:
    try:
        for name in ("docker", "dockerCompose"):
            descriptor = candidate["tools"][name]
            observed, error = _manifest.secure_executable_descriptor(Path(descriptor["path"]))
            if error or observed is None or observed["sha256"] != descriptor["sha256"]:
                return False
        return True
    except (KeyError, OSError, TypeError, ValueError):
        return False


def node_install_authorized(
    lane: Lane, candidate: dict, cache: dict | None = None,
) -> bool:
    requirement = _node_install_requirement(lane)
    if requirement is None:
        return True
    key, relative_cwd = requirement
    if not key:
        return False
    try:
        repository_name = "backend" if key == "backend" else "adminEsp"
        repository = candidate["repositories"][repository_name]
        repository_root = Path(repository["path"])
        install_parent = repository_root / relative_cwd
        install_root = install_parent / "node_modules"
        package_lock = install_parent / "package-lock.json"
        if any(
            os.path.lexists(ancestor / "node_modules")
            for ancestor in install_parent.parents
        ):
            return False
        if _has_unbound_node_modules(install_parent, install_root) is not False:
            return False
        if lane.command[0] == "npx":
            if len(lane.command) < 2 or "/" in lane.command[1] or lane.command[1] in {".", ".."}:
                return False
            local_binary = install_root / ".bin" / lane.command[1]
            binary_metadata = local_binary.lstat()
            if stat.S_ISLNK(binary_metadata.st_mode):
                target = os.readlink(local_binary)
                if not _lexical_symlink_within_root(Path(".bin"), target):
                    return False
            elif not stat.S_ISREG(binary_metadata.st_mode):
                return False
        metadata = candidate["tools"]["nodeInstalls"][key]
        if not isinstance(metadata, dict) or set(metadata) != {
            "version", "root", "packageLockSha256", "treeDigest",
        }:
            return False
        if type(metadata["version"]) is not int or metadata["version"] != 1:
            return False
        if metadata["root"] != str(install_root):
            return False
        cache_key = (key, str(install_root), str(package_lock))
        observed = cache.get(cache_key) if cache is not None else None
        if observed is None:
            observed = describe_node_install(install_root, package_lock)
            if cache is not None:
                cache[cache_key] = observed
        return _json_exact_equal(metadata, observed)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False


def robot_preview_browser_authorized(candidate: dict) -> bool:
    try:
        descriptor = candidate["tools"]["robotPreviewBrowser"]
        if not isinstance(descriptor, dict) or set(descriptor) != {
            "version", "engine", "revision", "root", "executable", "treeDigest",
        }:
            return False
        observed, error = secure_browser_bundle_descriptor(Path(descriptor["root"]))
        if error or observed != descriptor["treeDigest"]:
            return False
        admin_root = Path(candidate["repositories"]["adminEsp"]["path"])
        metadata_path = admin_root / "main/manager-web/node_modules/playwright-core/browsers.json"
        metadata = strict_json_loads(read_secure_regular(metadata_path, 1024 * 1024))
        browsers = metadata.get("browsers") if isinstance(metadata, dict) else None
        entry = next(
            item for item in browsers
            if isinstance(item, dict) and item.get("name") == "chromium-headless-shell"
        )
        if entry.get("revision") != descriptor["revision"] or descriptor["engine"] != "chromium-headless-shell":
            return False
        machine = os.uname().machine.lower()
        platform_suffix = {
            ("darwin", "arm64"): "chrome-headless-shell-mac-arm64/chrome-headless-shell",
            ("darwin", "x86_64"): "chrome-headless-shell-mac-x64/chrome-headless-shell",
            ("linux", "aarch64"): "chrome-linux/headless_shell",
            ("linux", "x86_64"): "chrome-headless-shell-linux64/chrome-headless-shell",
        }.get((sys.platform, machine))
        if platform_suffix is None:
            return False
        expected_root = Path(f"chromium_headless_shell-{descriptor['revision']}") / Path(platform_suffix).parent
        return (
            Path(descriptor["root"]).parts[-len(expected_root.parts):] == expected_root.parts
            and descriptor["executable"] == Path(platform_suffix).name
        )
    except (KeyError, OSError, StopIteration, TypeError, ValueError):
        return False


def playwright_browsers_authorized(candidate: dict) -> bool:
    try:
        descriptors = candidate["tools"]["playwrightBrowsers"]
        if not isinstance(descriptors, dict) or set(descriptors) != {
            "chromium-headless-shell", "webkit", "ffmpeg",
        }:
            return False
        admin_root = Path(candidate["repositories"]["adminEsp"]["path"])
        metadata_path = admin_root / "main/manager-web/node_modules/playwright-core/browsers.json"
        metadata = strict_json_loads(read_secure_regular(metadata_path, 1024 * 1024))
        browsers = metadata.get("browsers") if isinstance(metadata, dict) else None
        revisions = {
            item["name"]: item["revision"] for item in browsers
            if isinstance(item, dict) and item.get("name") in descriptors
        }
        if (
            set(revisions) != set(descriptors)
            or revisions != _manifest.PLAYWRIGHT_BROWSER_REVISIONS
        ):
            return False
        roots = {Path(descriptor["root"]).parent for descriptor in descriptors.values()}
        if len(roots) != 1:
            return False
        for engine, descriptor in descriptors.items():
            if (
                not isinstance(descriptor, dict)
                or set(descriptor) != {
                    "version", "engine", "revision", "root", "executable", "treeDigest",
                }
                or descriptor["version"] != 1 or descriptor["engine"] != engine
                or descriptor["revision"] != _manifest.PLAYWRIGHT_BROWSER_REVISIONS[engine]
                or descriptor["revision"] != revisions[engine]
                or Path(descriptor["root"]).name
                != f"{engine.replace('-', '_')}-{descriptor['revision']}"
            ):
                return False
            observed, error = secure_playwright_browser_bundle_descriptor(
                Path(descriptor["root"]),
            )
            executable = Path(descriptor["root"]) / descriptor["executable"]
            if (
                error or observed != descriptor["treeDigest"] or not executable.is_file()
                or executable.is_symlink() or not os.access(executable, os.X_OK)
            ):
                return False
        return True
    except (KeyError, OSError, TypeError, ValueError):
        return False


def release_state_matches(
    candidate_path: Path, candidate: dict, lanes: Sequence[Lane], runtime_root: Path | None,
    require_runtime: bool, node_lanes: Sequence[Lane] | None = None,
) -> bool:
    current = _load_candidate(candidate_path)
    profile = candidate.get("qualificationProfile", "production")
    if (
        current != candidate or current is None
        or _validate_profile_candidate(current, profile, verify_external_tools=False)
        or _validate_profile_candidate(current, profile, verify_external_tools=True)
    ):
        return False
    if not _candidate_matches(candidate):
        return False
    if require_runtime and not _runtime_matches_candidate(candidate, runtime_root):
        return False
    repositories = candidate["repositories"]
    for lane in lanes:
        if not lane_dirty_exceptions_authorized(lane, candidate):
            return False
        bound_paths = lane_candidate_paths(lane, candidate)
        if bound_paths and not candidate_paths_match(repositories[lane.repository], bound_paths):
            return False
    node_cache: dict = {}
    for lane in lanes if node_lanes is None else node_lanes:
        if not node_install_authorized(lane, candidate, node_cache):
            return False
        if lane.name == "admin-browser" and not robot_preview_browser_authorized(candidate):
            return False
        if _playwright_browsers_required(lane) and not playwright_browsers_authorized(candidate):
            return False
    return True


def _committed_text(admin_root: Path, sha: str, relative: str) -> str | None:
    try:
        return _candidate_git(admin_root, "show", f"{sha}:{relative}")
    except RuntimeError:
        return None


def _json_exact_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _json_exact_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _json_exact_equal(actual[index], expected[index]) for index in range(len(expected))
        )
    return actual == expected


def validate_playwright_contract(contract: object) -> bool:
    if not isinstance(contract, dict) or set(contract) != {
        "version", "sourcePaths", "specs", "testMatch", "projects", "assignmentPhases", "fixed",
    }:
        return False
    source_paths = contract.get("sourcePaths")
    specs = contract.get("specs")
    test_match = contract.get("testMatch")
    if (
        type(contract.get("version")) is not int or contract.get("version") != 1
        or not isinstance(source_paths, list)
        or source_paths != sorted(set(source_paths))
        or any(
            not isinstance(relative, str) or not relative
            or Path(relative).is_absolute() or ".." in Path(relative).parts
            for relative in source_paths
        )
        or not _json_exact_equal(source_paths, list(PLAYWRIGHT_SOURCE_PATHS))
        or not isinstance(specs, list) or not specs
        or any(
            not isinstance(spec, str) or not spec.startswith("e2e/lesson-studio/")
            or Path(spec).name != spec.removeprefix("e2e/lesson-studio/")
            or not spec.startswith("e2e/lesson-studio/course-mode")
            or not spec.endswith(".spec.js")
            for spec in specs
        )
        or specs != sorted(set(specs))
        or test_match != [Path(spec).name for spec in specs]
        or not _json_exact_equal(contract.get("projects"), list(PLAYWRIGHT_PROJECT_CONTRACT))
        or not _json_exact_equal(contract.get("assignmentPhases"), PLAYWRIGHT_ASSIGNMENT_CONTRACT)
        or not _json_exact_equal(contract.get("fixed"), PLAYWRIGHT_FIXED_CONTRACT)
    ):
        return False
    return True


def _js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def generate_playwright_config(contract: object) -> str:
    if not validate_playwright_contract(contract):
        raise ValueError("invalid Course Mode Playwright contract")
    assert isinstance(contract, dict)
    fixed = contract["fixed"]
    projects = contract["projects"]
    project_lines = []
    for project in projects:
        viewport = project["viewport"]
        project_lines.extend([
            "    {",
            f"      name: {_js_string(project['name'])},",
            "      use: {",
            f"        ...devices[{_js_string(project['device'])}],",
            f"        viewport: {{ width: {viewport['width']}, height: {viewport['height']} }},",
            "      },",
            "    },",
        ])
    test_matches = ", ".join(_js_string(value) for value in contract["testMatch"])
    reporter = '[["list"], ["html", {"open": "never", "outputFolder": path.join(outputRoot, \'report\')}]]'
    return "\n".join([
        "const { defineConfig, devices } = require('@playwright/test');",
        "const path = require('node:path');",
        "const { lessonStudioWebOrigin } = require('./scripts/lesson-studio-e2e-environment.cjs');",
        f"const outputRoot = path.resolve(process.env.{fixed['outputRootEnvironment']} || './output', 'playwright-course-mode');",
        "",
        "module.exports = defineConfig({",
        f"  testDir: {_js_string(fixed['testDir'])},",
        f"  globalSetup: {_js_string(fixed['globalSetup'])},",
        f"  testMatch: [{test_matches}],",
        "  outputDir: path.join(outputRoot, 'results'),",
        f"  timeout: {fixed['timeout']},",
        f"  expect: {{ timeout: {fixed['expectTimeout']} }},",
        f"  fullyParallel: {str(fixed['fullyParallel']).lower()},",
        f"  workers: {fixed['workers']},",
        f"  retries: {fixed['retries']},",
        f"  reporter: {reporter},",
        "  use: {",
        "    baseURL: lessonStudioWebOrigin(),",
        f"    trace: {_js_string(fixed['use']['trace'])},",
        f"    screenshot: {_js_string(fixed['use']['screenshot'])},",
        f"    video: {_js_string(fixed['use']['video'])},",
        f"    serviceWorkers: {_js_string(fixed['use']['serviceWorkers'])},",
        "  },",
        "  projects: [",
        *project_lines,
        "  ],",
        "});",
        "",
    ])


def source_contract_ready(admin_root: Path, contract: str, sha: str) -> bool:
    if contract != "course-mode-playwright":
        return False
    try:
        package_raw = _committed_text(admin_root, sha, "main/manager-web/package.json")
        contract_raw = _committed_text(admin_root, sha, PLAYWRIGHT_CONTRACT_PATH)
        config_raw = _committed_text(admin_root, sha, "main/manager-web/playwright.config.js")
        if package_raw is None or contract_raw is None or config_raw is None:
            return False
        package = strict_json_loads(package_raw)
        document = strict_json_loads(contract_raw)
        specs = _playwright_spec_paths(admin_root, sha)
    except (json.JSONDecodeError, ValueError, RuntimeError):
        return False
    scripts = package.get("scripts") if isinstance(package, dict) else None
    script = scripts.get("test:e2e:course-mode") if isinstance(scripts, dict) else None
    if (
        script != "playwright test --config=playwright.config.js"
        or not all(scripts.get(name) == command for name, command in PLAYWRIGHT_ASSIGNMENT_SCRIPTS.items())
    ):
        return False
    try:
        generated = generate_playwright_config(document)
    except ValueError:
        return False
    normalized_specs = [relative.removeprefix("main/manager-web/") for relative in specs]
    if document["specs"] != normalized_specs or config_raw != generated:
        return False
    harness = _playwright_harness_paths(admin_root, sha, document, specs)
    if harness is None:
        return False
    bound = (
        "main/manager-web/package.json", "main/manager-web/playwright.config.js",
        PLAYWRIGHT_CONTRACT_PATH, *harness, *document["sourcePaths"],
        *document["assignmentPhases"]["sourcePaths"],
    )
    return candidate_paths_match(
        {"path": str(admin_root), "sha": sha, "dirtyExceptions": []}, bound,
    )


def pytest_report_has_skips(path: Path) -> bool | None:
    try:
        root = ET.fromstring(read_secure_regular(path, MAX_REPORT_BYTES))
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        if not suites and root.tag == "testsuites":
            tests = int(root.attrib.get("tests", "0"))
            skipped = int(root.attrib.get("skipped", "0"))
            return skipped > 0 if tests > 0 else None
        tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
        skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
        return skipped > 0 if tests > 0 else None
    except (OSError, ET.ParseError, TypeError, ValueError):
        return None


def physical_preflight_command(candidate: dict) -> tuple[str, ...] | None:
    if any(not isinstance(candidate.get(key), dict) or not candidate[key] for key in ("images", "firmware", "database")):
        return None
    if _physical_admission_binding(candidate, require_output_absent=True) is None:
        return None
    runtime = _physical_python_runtime_binding(candidate, verify_authority=True)
    if runtime is None:
        return None
    tools = candidate.get("tools")
    metadata = tools.get("physicalAdmission") if isinstance(tools, dict) else None
    required = {"input", "output", "expectedIdentity", "expectedIdentitySignature"}
    if not isinstance(metadata, dict) or set(metadata) != required:
        return None
    try:
        evidence_root = Path(candidate["evidenceRoot"]).resolve(strict=True)
        resolved = {key: Path(value) for key, value in metadata.items() if isinstance(value, str)}
        if set(resolved) != required or any(not path.is_absolute() for path in resolved.values()):
            return None
        for key in ("input", "expectedIdentity", "expectedIdentitySignature"):
            path = resolved[key].resolve(strict=True)
            path.relative_to(evidence_root)
            raw = read_secure_regular(path, 256 if key == "expectedIdentitySignature" else MAX_CANDIDATE_BYTES)
            if key == "expectedIdentitySignature":
                if len(raw) != 64:
                    return None
            else:
                document = strict_json_loads(raw)
                if not isinstance(document, dict):
                    return None
        output = resolved["output"]
        output.parent.resolve(strict=True).relative_to(evidence_root)
        if output.exists() or output.is_symlink():
            return None
    except (KeyError, OSError, ValueError):
        return None
    profile = candidate.get("qualificationProfile", "production")
    if profile not in ("production", "m1-staging"):
        return None
    profile_args = ("--profile", profile) if profile == "m1-staging" else ()
    return (
        str(runtime.executable), "-I", "-s", "-c", PHYSICAL_ADMISSION_BOOTSTRAP,
        "scripts/course_mode_physical_flash_admission.py",
        "--input", str(resolved["input"]), "--output", str(resolved["output"]),
        "--expected-identity", str(resolved["expectedIdentity"]),
        "--expected-identity-signature", str(resolved["expectedIdentitySignature"]),
        *profile_args,
    )


def _physical_admission_binding(
    candidate: dict, *, require_output_absent: bool,
    expected_candidate_path: Path | None = None,
) -> PhysicalAdmissionBinding | None:
    required = ("input", "output", "expectedIdentity", "expectedIdentitySignature")
    try:
        metadata = candidate["tools"]["physicalAdmission"]
        if not isinstance(metadata, dict) or set(metadata) != set(required):
            return None
        descriptor = tuple((key, metadata[key]) for key in required)
        if any(not isinstance(value, str) or not value for _, value in descriptor):
            return None
        paths = {key: Path(value) for key, value in descriptor}
        if any(not path.is_absolute() or str(path) != value for (key, value), path in zip(descriptor, paths.values())):
            return None
        evidence_root = Path(candidate["evidenceRoot"])
        if (
            not evidence_root.is_absolute()
            or evidence_root != evidence_root.resolve(strict=True)
        ):
            return None
        evidence_root_metadata = os.stat(evidence_root, follow_symlinks=False)
        if not stat.S_ISDIR(evidence_root_metadata.st_mode):
            return None
        input_identities = []
        documents = {}
        raw_inputs = {}
        for key in ("input", "expectedIdentity", "expectedIdentitySignature"):
            path = paths[key]
            if path != path.resolve(strict=True):
                return None
            path.relative_to(evidence_root)
            before = os.stat(path, follow_symlinks=False)
            raw = read_secure_regular(
                path, 256 if key == "expectedIdentitySignature" else MAX_CANDIDATE_BYTES,
            )
            after = os.stat(path, follow_symlinks=False)
            if _stat_identity(before) != _stat_identity(after):
                return None
            if key == "expectedIdentitySignature":
                if len(raw) != 64:
                    return None
            else:
                document = strict_json_loads(raw)
                if not isinstance(document, dict):
                    return None
                documents[key] = document
            raw_inputs[key] = raw
            input_identities.append((
                key, _stat_identity(after), hashlib.sha256(raw).hexdigest(),
            ))
        output = paths["output"]
        output.parent.relative_to(evidence_root)
        if output.parent != output.parent.resolve(strict=True):
            return None
        output_parent_metadata = os.stat(output.parent, follow_symlinks=False)
        if not stat.S_ISDIR(output_parent_metadata.st_mode):
            return None
        if require_output_absent:
            if os.path.lexists(output):
                return None
        elif os.path.lexists(output):
            output_metadata = os.stat(output, follow_symlinks=False)
            if not stat.S_ISREG(output_metadata.st_mode):
                return None
        input_document = documents["input"]
        identity_document = documents["expectedIdentity"]
        signed_candidate = input_document.get("candidate")
        if (
            not isinstance(signed_candidate, dict)
            or signed_candidate.get("candidateId") != candidate.get("candidateId")
            or identity_document.get("sessionId") != input_document.get("sessionId")
            or identity_document.get("candidate") != signed_candidate
            or identity_document.get("signer") != {
                "algorithm": "ed25519",
                "fingerprint": _admission.PINNED_APPROVAL_KEY_FINGERPRINT,
            }
        ):
            return None
        candidate_path = Path(signed_candidate.get("path", ""))
        if (
            not candidate_path.is_absolute()
            or expected_candidate_path is not None
            and candidate_path != expected_candidate_path
        ):
            return None
        candidate_raw = read_secure_regular(candidate_path, MAX_CANDIDATE_BYTES)
        if (
            hashlib.sha256(candidate_raw).hexdigest() != signed_candidate.get("sha256")
            or strict_json_loads(candidate_raw) != candidate
        ):
            return None
        verified_audit, audit_reasons = _software_snapshot.verify_current_software_audit(
            candidate_path, evidence_root, preserved_roots=(),
        )
        if verified_audit is None or audit_reasons:
            return None
        profile = candidate.get("qualificationProfile", "production")
        policy = _admission.admission_policy(profile)
        expected_result = {
            "schemaVersion": 1,
            "validator": _admission.VALIDATOR,
            "status": "pass",
            "reasons": [],
            "candidateId": candidate["candidateId"],
            "sessionId": input_document["sessionId"],
            "signerFingerprint": _admission.PINNED_APPROVAL_KEY_FINGERPRINT,
            "physicalActionsPerformed": False,
            "serialOpened": False,
            "inputSha256": hashlib.sha256(raw_inputs["input"]).hexdigest(),
            "expectedIdentitySha256": hashlib.sha256(
                raw_inputs["expectedIdentity"],
            ).hexdigest(),
            "candidateSha256": hashlib.sha256(candidate_raw).hexdigest(),
            "softwareAuditSha256": verified_audit.audit_sha256,
            "softwareSnapshotId": verified_audit.snapshot_id,
            "robotMac": _admission.ROBOT_MAC,
            "serialPath": _admission.SERIAL_PATH,
            "firmwareSha": policy["firmwareSha"],
            "appSha256": policy["appSha256"],
            "manifestSha256": policy["manifestSha256"],
        }
        if profile == "m1-staging":
            expected_result["qualificationProfile"] = profile
        return PhysicalAdmissionBinding(
            descriptor=descriptor,
            evidence_root_identity=_directory_identity(evidence_root_metadata),
            input_identities=tuple(input_identities),
            output_parent_identity=_directory_identity(output_parent_metadata),
            software_audit_identity=verified_audit.audit_identity,
            software_audit_sha256=verified_audit.audit_sha256,
            software_snapshot_id=verified_audit.snapshot_id,
            expected_result=json.dumps(
                expected_result, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            ).encode("utf-8"),
        )
    except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None


def _physical_python_runtime_binding(
    candidate: dict, *, verify_authority: bool,
) -> PhysicalPythonRuntimeBinding | None:
    try:
        descriptor = candidate["tools"]["pythonTestRuntime"]
        if not isinstance(descriptor, dict):
            return None
        frozen_descriptor = tuple(sorted(descriptor.items()))
        root = Path(descriptor["root"])
        executable = root / descriptor["executable"]
        if (
            not root.is_absolute()
            or not executable.is_absolute()
            or executable != executable.resolve(strict=True)
            or verify_authority
            and not _manifest.python_test_runtime_authorized(descriptor)
        ):
            return None
        observed, error = _manifest.secure_python_test_runtime_tree_descriptor(root)
        executable_metadata = os.stat(executable, follow_symlinks=False)
        if (
            error or observed != descriptor["treeDigest"]
            or not stat.S_ISREG(executable_metadata.st_mode)
            or executable_metadata.st_mode & 0o222
            or not executable_metadata.st_mode & 0o111
        ):
            return None
        return PhysicalPythonRuntimeBinding(
            frozen_descriptor, executable, _stat_identity(executable_metadata), observed,
        )
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _physical_admission_source_binding(
    execution_candidate: dict, source_candidate: dict,
) -> PhysicalAdmissionSourceBinding | None:
    try:
        execution_root = Path(execution_candidate["repositories"]["adminEsp"]["path"])
        source_repository = source_candidate["repositories"]["adminEsp"]
        source_root = Path(source_repository["path"])
        sha = source_repository["sha"]
        records = []
        for relative in PHYSICAL_ADMISSION_SOURCE_PATHS:
            committed = _committed_text(source_root, sha, relative)
            if committed is None:
                return None
            record, error = _admission._secure_read(
                execution_root / relative, _admission.MAX_JSON_BYTES,
            )
            if error or record is None or record[0] != committed.encode("utf-8"):
                return None
            records.append(record)
        binding = PhysicalAdmissionSourceBinding(tuple(records))
        return binding if _physical_admission_sources_still_bound(binding) else None
    except (KeyError, OSError, RuntimeError, TypeError, UnicodeEncodeError, ValueError):
        return None


def _physical_admission_sources_still_bound(
    binding: PhysicalAdmissionSourceBinding | None,
) -> bool:
    return binding is not None and all(
        _admission._still_bound(record) for record in binding.records
    )


def _physical_admission_bound_execution(
    command: tuple[str, ...], binding: PhysicalAdmissionSourceBinding,
) -> tuple[tuple[str, ...], dict[str, str]] | None:
    try:
        records = binding.records
        if (
            len(records) != 4 or len(command) < 6
            or command[1:5] != ("-I", "-s", "-c", PHYSICAL_ADMISSION_BOOTSTRAP)
            or Path(command[5]).name != Path(records[0][2]).name
            or tuple(Path(record[2]).name for record in records)
            != tuple(Path(path).name for path in PHYSICAL_ADMISSION_SOURCE_PATHS)
        ):
            return None
        environment = {
            "TBOT_ADMISSION_MAIN_B64": base64.b64encode(records[0][0]).decode("ascii"),
            "TBOT_ADMISSION_MANIFEST_B64": base64.b64encode(records[1][0]).decode("ascii"),
            "TBOT_ADMISSION_PREFLIGHT_B64": base64.b64encode(records[2][0]).decode("ascii"),
            "TBOT_ADMISSION_SNAPSHOT_B64": base64.b64encode(records[3][0]).decode("ascii"),
        }
        if sum(len(key) + len(value) + 2 for key, value in environment.items()) > (
            MAX_PHYSICAL_ADMISSION_SOURCE_ENV_BYTES
        ):
            return None
        bound_command = (
            *command[:5], str(records[1][2]), str(records[2][2]), str(records[3][2]),
            command[5], *command[6:],
        )
        return bound_command, environment
    except (IndexError, TypeError, UnicodeDecodeError, ValueError):
        return None


def _physical_admission_result_valid(
    candidate: dict, binding: PhysicalAdmissionBinding, *, started_at: float,
    expected_record: tuple | None = None,
) -> bool:
    try:
        output = Path(dict(binding.descriptor)["output"])
        record, error = _admission._secure_read(output, _admission.MAX_JSON_BYTES)
        if error or record is None:
            return False
        raw, identity, _, _, _ = record
        output_metadata = os.stat(output, follow_symlinks=False)
        birthtime = getattr(output_metadata, "st_birthtime", None)
        document, json_error = _admission._load_json(raw)
        expected = strict_json_loads(binding.expected_result)
        if (
            json_error
            or raw != binding.expected_result + b"\n"
            or expected_record is not None and (
                raw != expected_record[0] or identity != expected_record[1]
            )
            or not _admission._exact_equal(document, expected)
            or identity[4] != _admission.OPERATOR_UID
            or identity[3] != 1
            or identity[2] & 0o222
            or _admission._file_identity(output_metadata) != identity
            or not isinstance(birthtime, (int, float))
            or not math.isfinite(birthtime)
            or birthtime < started_at
            or not _admission._still_bound(record)
            or _physical_admission_binding(
                candidate, require_output_absent=False,
            ) != binding
        ):
            return False
        final, final_error = _admission._secure_read(output, _admission.MAX_JSON_BYTES)
        return (
            final_error is None and final is not None
            and final[0] == raw and final[1] == identity
            and (expected_record is None or final[1] == expected_record[1])
            and _admission._still_bound(final)
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _remove_bound_physical_admission_result(record: tuple | None) -> bool:
    if record is None:
        return True
    parent_fd = None
    try:
        _, identity, path, saved_metadata, saved_ancestry = record
        parent_fd, _, _ = _admission._secure_parent(path.parent)
        if not _admission._parent_still_bound(
            path.parent, parent_fd, saved_metadata, saved_ancestry,
        ):
            return False
        return _quarantine_invalidate_remove(parent_fd, path.name, identity[:2])
    except OSError:
        return False
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _valid_lane(lane: Lane, repositories: Mapping[str, object]) -> bool:
    if (
        not lane.name or lane.repository not in repositories or not lane.command
        or not isinstance(lane.timeout_sec, (int, float))
        or not math.isfinite(lane.timeout_sec) or lane.timeout_sec <= 0
    ):
        return False
    relative = Path(lane.relative_cwd)
    return not relative.is_absolute() and ".." not in relative.parts


def _resolve_command(command: tuple[str, ...]) -> tuple[str, ...] | None:
    executable = command[0]
    if Path(executable).is_absolute():
        path = Path(executable)
        return command if path.is_file() and os.access(path, os.X_OK) else None
    resolved = shutil.which(executable, path=SECURE_PATH)
    return (resolved, *command[1:]) if resolved else None


def _resolve_candidate_command(
    command: tuple[str, ...], candidate: dict, lane: Lane,
) -> tuple[str, ...] | None:
    if lane.name == "physical-flash-admission":
        runtime = _physical_python_runtime_binding(candidate, verify_authority=False)
        return (str(runtime.executable), *command[1:]) if runtime is not None else None
    if command[:3] == ("python3", "-m", "pytest"):
        try:
            descriptor = candidate["tools"]["pythonTestRuntime"]
            executable = Path(descriptor["root"]) / descriptor["executable"]
            return _resolve_command((str(executable), "-I", "-s", *command[1:]))
        except (KeyError, TypeError, ValueError):
            return None
    requirement = _node_install_requirement(lane)
    if requirement is not None and requirement[0]:
        try:
            node = Path(candidate["tools"]["node"][requirement[0]]["executable"])
            if command[0] == "node":
                return _resolve_command((str(node), *command[1:]))
            if command[0] in {"npm", "npx"}:
                entrypoint = candidate["tools"]["node"][requirement[0]][command[0]]["entrypoint"]
                return _resolve_command((str(node), entrypoint, *command[1:]))
        except (KeyError, TypeError, ValueError):
            return None
    return _resolve_command(command)


def _sandboxed_python_lane_command(
    command: tuple[str, ...], execution_stage: ExecutionStage, lane_execution: LaneExecution,
) -> tuple[str, ...] | None:
    if sys.platform != "darwin":
        return None
    executable = _manifest._trusted_sandbox_executable()
    if executable is None:
        return None
    try:
        stage_root = execution_stage.root
        lane_root = lane_execution.root
        if (
            not stage_root.is_absolute() or not lane_root.is_absolute()
            or stage_root.parent != Path("/private/tmp")
            or lane_root.parent != Path("/private/tmp")
            or "\0" in str(stage_root) or "\0" in str(lane_root)
        ):
            return None
    except OSError:
        return None
    return (
        str(executable), "-p", PYTHON_LANE_SANDBOX_PROFILE,
        "-D", f"LANE_ROOT={lane_root}", *command,
    )


def _sandboxed_backend_build_command(
    command: Sequence[str], backend_root: Path, build_runtime: Path,
) -> tuple[str, ...] | None:
    if sys.platform != "darwin":
        return None
    executable = _manifest._trusted_sandbox_executable()
    if executable is None:
        return None
    try:
        if (
            not backend_root.is_absolute() or not build_runtime.is_absolute()
            or backend_root.parent.parent != build_runtime.parent
            or backend_root.parent.parent.parent != Path("/private/tmp")
            or "\0" in str(backend_root) or "\0" in str(build_runtime)
        ):
            return None
    except OSError:
        return None
    return (
        str(executable), "-p", BACKEND_BUILD_SANDBOX_PROFILE,
        "-D", f"BACKEND_ROOT={backend_root}",
        "-D", f"BUILD_RUNTIME={build_runtime}", *command,
    )


def _backend_snapshot_environment(execution_stage: ExecutionStage) -> BackendSnapshotBinding | None:
    try:
        authority = execution_stage.root / ".course-mode-authority/backend.json"
        raw = read_secure_regular(authority, 4096)
        document = strict_json_loads(raw)
        backend = execution_stage.candidate["repositories"]["backend"]
        observed_source, source_error = _manifest.secure_backend_snapshot_tree_descriptor(
            Path(backend["path"]),
        )
        observed_execution, execution_error = (
            _manifest.secure_backend_execution_tree_descriptor(Path(backend["path"]))
        )
        if (
            not isinstance(document, dict) or document.get("version") != 3
            or document.get("repository") != "backend"
            or document.get("root") != backend["path"] or document.get("sha") != backend["sha"]
            or set(document) != {
                "executionTreeDigest", "repository", "root", "sha", "sourceTreeDigest", "version",
            }
            or source_error or observed_source != document.get("sourceTreeDigest")
            or execution_error or observed_execution != document.get("executionTreeDigest")
        ):
            return None
        authority_sha256 = hashlib.sha256(raw).hexdigest()
        environment = {
            "COURSE_MODE_BACKEND_ROOT": backend["path"],
            "COURSE_MODE_BACKEND_SHA": backend["sha"],
            "COURSE_MODE_BACKEND_SNAPSHOT_AUTHORITY": str(authority),
            "COURSE_MODE_BACKEND_SNAPSHOT_AUTHORITY_SHA256": authority_sha256,
        }
        return BackendSnapshotBinding(environment, observed_execution, authority_sha256)
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _backend_execution_snapshot_matches(
    execution_stage: ExecutionStage, binding: BackendSnapshotBinding,
) -> bool:
    try:
        authority = execution_stage.root / ".course-mode-authority/backend.json"
        raw = read_secure_regular(authority, 4096)
        document = strict_json_loads(raw)
        backend = execution_stage.candidate["repositories"]["backend"]
        observed, error = _manifest.secure_backend_execution_tree_descriptor(Path(backend["path"]))
        return (
            hashlib.sha256(raw).hexdigest() == binding.authority_sha256
            and isinstance(document, dict) and document.get("version") == 3
            and document.get("root") == backend["path"] and document.get("sha") == backend["sha"]
            and document.get("executionTreeDigest") == binding.execution_tree
            and not error and observed == binding.execution_tree
        )
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return False


def _assignment_runtime_root(
    source_candidate: dict, execution_candidate: dict, value: object,
) -> str | None:
    try:
        if not isinstance(value, str) or not value or os.path.abspath(value) != value:
            return None
        source_admin = Path(source_candidate["repositories"]["adminEsp"]["path"])
        execution_admin = Path(execution_candidate["repositories"]["adminEsp"]["path"])
        source_output = source_admin / "main/manager-web/output"
        relative = Path(value).relative_to(source_output)
        execution_runtime = execution_admin / "main/manager-web/output" / relative
        for admin, runtime in ((source_admin, Path(value)), (execution_admin, execution_runtime)):
            current = admin
            for component in runtime.relative_to(admin).parts:
                current /= component
                try:
                    metadata = current.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    return None
        return str(execution_runtime)
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _firmware_handler_cjson_directory(candidate: dict) -> str | None:
    try:
        value = candidate["tools"]["espIdf"]["root"]
        if not isinstance(value, str) or not value:
            return None
        root = Path(value)
        if not root.is_absolute() or root.is_symlink():
            return None
        resolved_root = root.resolve(strict=True)
        if root != resolved_root or not root.is_dir():
            return None
        cjson = root / "components/json/cJSON"
        if cjson.is_symlink():
            return None
        resolved_cjson = cjson.resolve(strict=True)
        if cjson != resolved_cjson or not cjson.is_dir():
            return None
        source = cjson / "cJSON.c"
        metadata = source.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return None
        return str(cjson)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None


def _child_environment(
    candidate: dict,
    source: Mapping[str, str],
    lane: Lane,
    *,
    source_candidate: dict | None = None,
    assignment_runtime_capsule_root: Path | None = None,
    assignment_runtime_root: Path | None = None,
) -> dict[str, str] | None:
    environment = dict(BASE_ENVIRONMENT)
    node_requirement = _node_install_requirement(lane)
    if node_requirement is not None and node_requirement[0]:
        node = Path(candidate["tools"]["node"][node_requirement[0]]["executable"])
        environment["PATH"] = f"{node.parent}:{SECURE_PATH}"
    environment.update({
        "COURSE_MODE_BACKEND_ROOT": candidate["repositories"]["backend"]["path"],
        "COURSE_MODE_ADMIN_ESP_ROOT": candidate["repositories"]["adminEsp"]["path"],
        "COURSE_MODE_FIRMWARE_ROOT": candidate["repositories"]["firmware"]["path"],
        "COURSE_MODE_CANDIDATE_ID": candidate["candidateId"],
        "TBOT_BACKEND_WORKTREE": candidate["repositories"]["backend"]["path"],
        "TASK06_BACKEND_ROOT": candidate["repositories"]["backend"]["path"],
        "TASK06_FIRMWARE_ROOT": candidate["repositories"]["firmware"]["path"],
        "TBOT_BACKEND_CONTRACTS_DIR": str(
            Path(candidate["repositories"]["backend"]["path"]) / "contracts"
        ),
    })
    if lane.name == "firmware-handler":
        cjson_directory = _firmware_handler_cjson_directory(candidate)
        if cjson_directory is None:
            return None
        environment["CJSON_DIR"] = cjson_directory
    required_environment = {}
    for name in _required_environment(lane):
        value = source.get(name)
        if value:
            required_environment[name] = value
    environment.update(required_environment)
    assignment_ports = _assignment_port_environment(required_environment, lane)
    if assignment_ports is None:
        return None
    environment.update(assignment_ports)
    if lane.name in STATEFUL_ASSIGNMENT_LANES:
        source_runtime_root = required_environment.get(
            "TASK4_ASSIGNMENT_RUNTIME_ROOT"
        )
        if (
            not source_runtime_root
            or assignment_runtime_capsule_root is None
            or assignment_runtime_root is None
        ):
            return None
        runtime_root = _assignment_runtime_root(
            source_candidate if source_candidate is not None else candidate,
            candidate,
            source_runtime_root,
        )
        if runtime_root is None:
            return None
        environment[TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ENV] = str(
            assignment_runtime_capsule_root
        )
        environment["TASK4_ASSIGNMENT_RUNTIME_ROOT"] = str(assignment_runtime_root)
    if lane.name == LIVE_DB_LANE.name:
        environment["COURSE_MODE_V5_SOURCE_ROOT"] = candidate["repositories"]["adminEsp"]["path"]
    assignment = _assignment_candidate_environment(candidate, lane)
    if assignment is not None:
        environment.update(assignment)
    if (
        lane.name in STATEFUL_ASSIGNMENT_LANES
        or lane.name.startswith("admin-course-mode-playwright-")
    ):
        mount_candidate = source_candidate if source_candidate is not None else candidate
        environment.update({
            "TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT": mount_candidate["repositories"]["backend"]["path"],
            "TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT": mount_candidate["repositories"]["firmware"]["path"],
        })
    if lane.name.startswith("admin-course-mode-playwright-"):
        for name in (
            "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME", "LESSON_STUDIO_E2E_RESOURCE_PREFIX",
            "JWT_PUBLIC_KEY", "TBOT_DEVICE_MINT_SECRET", "LESSON_ASSET_ORIGIN_BASE",
            "ROBOT_ESP_BASE_URL", "LESSON_STUDIO_E2E_BACKEND_HOST_PORT",
            "LESSON_STUDIO_E2E_WEB_HOST_PORT",
        ):
            value = source.get(name)
            if value:
                environment[name] = value
    if _container_tools_required(lane):
        environment.update({
            "TBOT_DOCKER_EXECUTABLE": candidate["tools"]["docker"]["path"],
            "TBOT_DOCKER_COMPOSE_EXECUTABLE": candidate["tools"]["dockerCompose"]["path"],
        })
    if _playwright_browsers_required(lane):
        roots = {
            Path(descriptor["root"]).parent
            for descriptor in candidate["tools"]["playwrightBrowsers"].values()
        }
        if len(roots) == 1:
            environment["PLAYWRIGHT_BROWSERS_PATH"] = str(roots.pop())
    environment.update(dict(lane.fixed_environment))
    if lane.name == "backend-tests":
        try:
            environment["TBOT_PORTAL_OPENAPI_PATH"] = candidate["tools"]["backendTestInputs"]["portalOpenapi"]["path"]
        except (KeyError, TypeError):
            return None
    if lane.name == "admin-browser":
        browser = candidate["tools"]["robotPreviewBrowser"]
        values = {
            "root": browser["root"], "executable": browser["executable"],
            "engine": browser["engine"], "revision": browser["revision"],
            "treeSha256": browser["treeDigest"]["sha256"],
            "treeEntryCount": browser["treeDigest"]["entryCount"],
            "treeTotalBytes": browser["treeDigest"]["totalBytes"],
        }
        for field, name in ROBOT_PREVIEW_BROWSER_ENVIRONMENT.items():
            environment[name] = str(values[field])
    return environment


def _postgres_host_identity(host: str | None) -> str | None:
    if not host:
        return None
    normalized = host.lower().removesuffix(".")
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        if re.fullmatch(r"[0-9.]+", normalized):
            return None
        labels = normalized.split(".")
        if len(normalized) > 253 or any(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
            for label in labels
        ):
            return None
        return normalized
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.compressed


def _postgres_identity(value: object) -> tuple[str, int, str] | None:
    if (
        not isinstance(value, str)
        or not value
        or any(character.isspace() or ord(character) < 32 for character in value)
        or re.search(r"%(?![0-9A-Fa-f]{2})", value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        query = (
            parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            if parsed.query else []
        )
        endpoint = parsed.netloc.rsplit("@", 1)[-1]
        parsed_port = parsed.port
        port = 5432 if parsed_port is None else parsed_port
        host = _postgres_host_identity(parsed.hostname)
    except (UnicodeError, ValueError):
        return None
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.netloc
        or parsed.fragment
        or endpoint.endswith(":")
        or host is None
        or not 1 <= port <= 65535
        or any(key.lower() in POSTGRES_IDENTITY_QUERY_KEYS for key, _ in query)
    ):
        return None
    database = unquote(parsed.path.removeprefix("/"))
    if not database or "/" in database or "\\" in database or "\x00" in database:
        return None
    return (host, port, database)


def _local_postgres_identity(value: object) -> tuple[str, int, str] | None:
    identity = _postgres_identity(value)
    if identity is None:
        return None
    try:
        literal_host = urlsplit(value).hostname.lower()
    except (AttributeError, UnicodeError, ValueError):
        return None
    if literal_host not in {"127.0.0.1", "::1"}:
        return None
    return ("loopback", identity[1], identity[2])


def _resolved_postgres_addresses(host: str, port: int) -> frozenset[str] | None:
    results: queue.SimpleQueue[object] = queue.SimpleQueue()

    def resolve() -> None:
        try:
            results.put(socket.getaddrinfo(
                host, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            ))
        except (OSError, UnicodeError, ValueError):
            results.put(None)

    worker = threading.Thread(target=resolve, daemon=True)
    worker.start()
    worker.join(2.0)
    if worker.is_alive() or results.empty():
        return None
    raw = results.get()
    if not isinstance(raw, list) or not raw or len(raw) > 32:
        return None
    addresses = set()
    try:
        for family, _, _, _, sockaddr in raw:
            if family not in {socket.AF_INET, socket.AF_INET6} or not isinstance(sockaddr, tuple):
                return None
            address = ipaddress.ip_address(sockaddr[0])
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                address = address.ipv4_mapped
            addresses.add(address.compressed)
    except (IndexError, TypeError, ValueError):
        return None
    return frozenset(addresses) if addresses else None


def _live_db_topology_ready(source: Mapping[str, str]) -> bool:
    identities = tuple(_local_postgres_identity(source.get(name)) for name in LIVE_DB_URL_VARIABLES)
    if any(identity is None for identity in identities):
        return False
    v2, curriculum, materializer, rollback = identities
    if not (v2 == curriculum and materializer == rollback and curriculum != materializer):
        return False
    if "PRODUCTION_DATABASE_URL" not in source:
        return True
    production = _postgres_identity(source.get("PRODUCTION_DATABASE_URL"))
    if production is None:
        return False
    host, port, database = production
    addresses = _resolved_postgres_addresses(host, port)
    if addresses is None:
        return False
    production_is_loopback = any(ipaddress.ip_address(address).is_loopback for address in addresses)
    aliases_test_database = production_is_loopback and any(
        (port, database) == identity[1:] for identity in (curriculum, materializer)
    )
    return not aliases_test_database


def _live_db_source_snapshot(source: Mapping[str, str]) -> dict[str, str] | None:
    missing = object()
    snapshot = {}
    try:
        for name in (*LIVE_DB_URL_VARIABLES, "PRODUCTION_DATABASE_URL"):
            value = source.get(name, missing)
            if value is not missing:
                snapshot[name] = value
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return None
    return snapshot


def _assignment_source_snapshot(source: Mapping[str, str]) -> dict[str, str] | None:
    missing = object()
    snapshot = {}
    try:
        for name in TASK4_ASSIGNMENT_CANDIDATE_ENV:
            value = source.get(name, missing)
            if value is not missing:
                snapshot[name] = value
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return None
    return snapshot


def _assignment_candidate_environment(candidate: dict, lane: Lane) -> dict[str, str] | None:
    if (
        lane.name not in STATEFUL_ASSIGNMENT_LANES
        and not lane.name.startswith("admin-course-mode-playwright-")
    ):
        return {}
    try:
        images = candidate["images"]
        backend = images["lessonStudioBackend"]
        web = images["lessonStudioWeb"]
        references = (backend["reference"], web["reference"])
        values = {
            "TBOT_BACKEND_WORKTREE": candidate["repositories"]["backend"]["path"],
            "TBOT_FIRMWARE_WORKTREE": candidate["repositories"]["firmware"]["path"],
            "TBOT_LESSON_STUDIO_BACKEND_IMAGE": backend["id"],
            "TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID": backend["id"],
            "TBOT_LESSON_STUDIO_WEB_IMAGE": web["id"],
            "TBOT_LESSON_STUDIO_WEB_IMAGE_ID": web["id"],
        }
    except (KeyError, TypeError):
        return None
    if any(not isinstance(value, str) or not value for value in (*values.values(), *references)):
        return None
    for key in ("TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID", "TBOT_LESSON_STUDIO_WEB_IMAGE_ID"):
        if re.fullmatch(r"sha256:[0-9a-f]{64}", values[key]) is None:
            return None
    return values


def _assignment_port_environment(
    environment: Mapping[str, str], lane: Lane,
) -> dict[str, str] | None:
    if lane.name not in STATEFUL_ASSIGNMENT_LANES:
        return {}
    values = {name: environment.get(name) for name in TASK4_ASSIGNMENT_PORT_ENV}
    if any(
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]+", value) is None
        or len(value) > 5
        or not 1 <= int(value) <= 65535
        for value in values.values()
    ):
        return None
    ports = {int(value) for value in values.values()}
    if len(ports) != len(TASK4_ASSIGNMENT_PORT_ENV) or ports & TASK4_STANDARD_HOST_PORTS:
        return None
    return values


def _candidate_metadata_matches(candidate_path: Path, candidate: dict) -> bool:
    observed = _load_candidate(candidate_path)
    return observed is not None and _json_exact_equal(observed, candidate)


def _operator_attestation_binding(
    candidate: dict, source: Mapping[str, str], *, now: datetime | None = None,
) -> OperatorAttestationBinding | None:
    try:
        validation_now = now if now is not None else datetime.now(timezone.utc)
        if (
            not isinstance(validation_now, _DATETIME_TYPE)
            or validation_now.tzinfo is None
            or validation_now.utcoffset() != timedelta(0)
        ):
            return None
        validation_now = _DATETIME_TYPE(
            validation_now.year,
            validation_now.month,
            validation_now.day,
            validation_now.hour,
            validation_now.minute,
            validation_now.second,
            validation_now.microsecond,
            tzinfo=timezone.utc,
            fold=validation_now.fold,
        )
    except Exception:  # Injected clock normalization must fail closed.
        return None
    value = source.get(OPERATOR_ATTESTATION_ENV)
    if not isinstance(value, str) or not value:
        return None
    parent_fd: int | None = None
    try:
        path = Path(value)
        evidence_value = Path(candidate["evidenceRoot"])
        if not path.is_absolute() or not evidence_value.is_absolute():
            return None
        absolute = Path(os.path.abspath(path))
        evidence_root = Path(os.path.abspath(evidence_value))
        relative = absolute.relative_to(evidence_root)
        if not relative.parts:
            return None

        effective_uid = os.geteuid()
        parent_fd = _manifest._open_directory_secure(evidence_root)
        evidence_metadata = os.fstat(parent_fd)
        if (
            not stat.S_ISDIR(evidence_metadata.st_mode)
            or evidence_metadata.st_uid != effective_uid
            or evidence_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            return None
        for component in relative.parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = next_fd
            metadata = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != effective_uid
                or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            ):
                return None

        before = os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != effective_uid
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            return None
        raw = read_secure_regular(absolute, MAX_OPERATOR_ATTESTATION_BYTES)
        after = os.stat(relative.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
            before.st_mode, before.st_nlink, before.st_uid,
        )
        if identity != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
            after.st_mode, after.st_nlink, after.st_uid,
        ):
            return None
        payload = strict_json_loads(raw)
        repositories = candidate["repositories"]
        expected = {
            "candidateId": candidate["candidateId"],
            "effectiveUid": os.geteuid(),
            "gateSha": repositories["adminEsp"]["sha"],
            "hostName": socket.gethostname(),
            "sameUidThreatModel": "malicious-process-excluded",
            "schemaVersion": 1,
            "trustedOperatorAccountConfirmed": True,
            "untrustedAutomationStoppedConfirmed": True,
        }
        if not isinstance(payload, dict) or set(payload) != OPERATOR_ATTESTATION_KEYS:
            return None
        try:
            candidate_created = _manifest._parse_rfc3339_utc(candidate.get("createdAt"))
            candidate_expires = _manifest._parse_rfc3339_utc(candidate.get("expiresAt"))
            attestation_created = _manifest._parse_rfc3339_utc(payload.get("createdAt"))
            valid_attestation_time = (
                candidate_created is not None
                and candidate_expires is not None
                and attestation_created is not None
                and validation_now < candidate_expires
                and candidate_created
                <= attestation_created
                <= min(validation_now, candidate_expires)
            )
        except Exception:
            return None
        if not valid_attestation_time:
            return None
        if any(
            not _json_exact_equal(payload.get(key), expected_value)
            for key, expected_value in expected.items()
        ):
            return None
        return OperatorAttestationBinding(
            path=absolute, sha256=hashlib.sha256(raw).hexdigest(),
        )
    except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _paths_alias(left: Path, right: Path) -> bool:
    normalized_left = Path(os.path.abspath(left))
    normalized_right = Path(os.path.abspath(right))
    if normalized_left == normalized_right:
        return True
    try:
        return os.path.samefile(normalized_left, normalized_right)
    except OSError:
        return False


def _command_for_lane(lane: Lane, candidate: dict) -> tuple[str, ...] | None:
    if lane.command == (COURSE_MODE_SOFTWARE_TESTS,):
        repository = candidate["repositories"]["adminEsp"]
        tests = select_esp_software_tests(Path(repository["path"]), repository["sha"])
        return ("python3", "-m", "pytest", "-q", *tests) if tests else None
    if lane.name == "physical-flash-admission":
        return physical_preflight_command(candidate)
    return lane.command


def _write_report_atomic(
    path: Path, report: dict, destination: ReportDestination | None = None,
) -> bool:
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_REPORT_BYTES or not path.is_absolute():
        return False
    owned_destination = destination is None
    temporary = None
    descriptor = None
    try:
        if destination is None:
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            destination = ReportDestination(parent_fd)
        parent_fd = destination.parent_fd
        descriptor_parent = os.fstat(parent_fd)
        named_parent = os.stat(path.parent, follow_symlinks=False)
        if (descriptor_parent.st_dev, descriptor_parent.st_ino) != (
            named_parent.st_dev, named_parent.st_ino,
        ):
            return False
        if destination.identity is None:
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                return False
            except FileNotFoundError:
                pass
        else:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or (current.st_dev, current.st_ino) != destination.identity
                or destination.report_fd is None
            ):
                return False
            owned = os.fstat(destination.report_fd)
            if (
                not stat.S_ISREG(owned.st_mode)
                or owned.st_nlink != 1
                or (owned.st_dev, owned.st_ino) != destination.identity
            ):
                return False
            os.ftruncate(destination.report_fd, 0)
            os.lseek(destination.report_fd, 0, os.SEEK_SET)
            remaining = memoryview(payload)
            while remaining:
                written = os.write(destination.report_fd, remaining)
                if written <= 0:
                    return False
                remaining = remaining[written:]
            os.fsync(destination.report_fd)
            published = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            os.fsync(parent_fd)
            named_parent = os.stat(path.parent, follow_symlinks=False)
            return (
                stat.S_ISREG(published.st_mode)
                and published.st_nlink == 1
                and (published.st_dev, published.st_ino) == destination.identity
                and (named_parent.st_dev, named_parent.st_ino) == (
                    descriptor_parent.st_dev, descriptor_parent.st_ino,
                )
            )
        for _ in range(32):
            temporary = f".{path.name}.{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=parent_fd,
                )
                break
            except FileExistsError:
                continue
        else:
            return False
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                return False
            remaining = remaining[written:]
        os.fsync(descriptor)
        temporary_stat = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_nlink != 1:
            return False
        destination.identity = (temporary_stat.st_dev, temporary_stat.st_ino)
        destination.report_fd = descriptor
        descriptor = None
        os.link(
            temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=parent_fd)
        temporary = None
        published = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(published.st_mode)
            or published.st_nlink != 1
            or (published.st_dev, published.st_ino) != destination.identity
        ):
            return False
        os.fsync(parent_fd)
        named_parent = os.stat(path.parent, follow_symlinks=False)
        if (descriptor_parent.st_dev, descriptor_parent.st_ino) != (
            named_parent.st_dev, named_parent.st_ino,
        ):
            _invalidate_report(path, destination)
            return False
        return True
    except OSError:
        return False
    finally:
        if temporary is not None and destination is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=destination.parent_fd)
        if descriptor is not None:
            os.close(descriptor)
        if owned_destination and destination is not None:
            if destination.report_fd is not None:
                os.close(destination.report_fd)
            os.close(destination.parent_fd)


def _path_overlaps(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False


def _prepare_report_destination(
    candidate_path: Path, candidate: dict, report_path: Path,
) -> ReportDestination | None:
    if not report_path.is_absolute():
        return None
    parent_fd = None
    try:
        evidence_value = Path(candidate["evidenceRoot"])
        if not evidence_value.is_absolute() or evidence_value.is_symlink():
            return None
        evidence_root = evidence_value.resolve(strict=True)
        if evidence_value != evidence_root or not evidence_root.is_dir():
            return None
        candidate_input = candidate_path.resolve(strict=True)
        repository_roots = [
            Path(candidate["repositories"][name]["path"]).resolve(strict=True)
            for name in ("backend", "adminEsp", "firmware")
        ]
        if _path_overlaps(evidence_root, candidate_input) or any(
            _path_overlaps(evidence_root, root) for root in repository_roots
        ):
            return None
        parent_value = report_path.parent
        parent = parent_value.resolve(strict=True)
        if parent_value != parent or not parent.is_dir():
            return None
        parent.relative_to(evidence_root)
        if report_path == evidence_root or report_path.is_symlink():
            return None
        expected_evidence = evidence_root.stat()
        parent_fd = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened_evidence = os.fstat(parent_fd)
        if (opened_evidence.st_dev, opened_evidence.st_ino) != (
            expected_evidence.st_dev, expected_evidence.st_ino,
        ):
            return None
        effective_uid = os.geteuid()
        if opened_evidence.st_uid != effective_uid or stat.S_IMODE(opened_evidence.st_mode) & 0o022:
            return None
        for component in parent.relative_to(evidence_root).parts:
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = next_fd
            opened_component = os.fstat(parent_fd)
            if opened_component.st_uid != effective_uid or stat.S_IMODE(opened_component.st_mode) & 0o022:
                return None
        expected_parent = parent.stat()
        actual_parent = os.fstat(parent_fd)
        if (actual_parent.st_dev, actual_parent.st_ino) != (expected_parent.st_dev, expected_parent.st_ino):
            return None
        try:
            os.stat(report_path.name, dir_fd=parent_fd, follow_symlinks=False)
            return None
        except FileNotFoundError:
            pass
        if any(
            _path_overlaps(report_path, protected)
            for protected in (candidate_input, *repository_roots)
        ):
            return None
        prepared = ReportDestination(parent_fd)
        parent_fd = None
        return prepared
    except (KeyError, OSError, TypeError, ValueError):
        return None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _invalidate_report(path: Path, destination: ReportDestination) -> None:
    if destination.identity is None or destination.report_fd is None:
        return
    with contextlib.suppress(OSError):
        os.ftruncate(destination.report_fd, 0)
        os.fsync(destination.report_fd)


def _close_report_destination(destination: ReportDestination) -> None:
    if destination.report_fd is not None:
        os.close(destination.report_fd)
        destination.report_fd = None
    os.close(destination.parent_fd)


def _remove_bound_report(
    path: Path, identity: tuple[int, int] | None, parent_identity: tuple[int, int],
) -> bool:
    if identity is None:
        return True
    parent_fd = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened = os.fstat(parent_fd)
        named = os.stat(path.parent, follow_symlinks=False)
        if (
            (opened.st_dev, opened.st_ino) != parent_identity
            or (named.st_dev, named.st_ino) != parent_identity
        ):
            return False
        return _quarantine_invalidate_remove(parent_fd, path.name, identity)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    finally:
        if parent_fd is not None:
            with contextlib.suppress(OSError):
                os.close(parent_fd)


def _rename_no_replace(parent_fd: int, source: str, destination: str) -> None:
    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        operation = library.renameatx_np
        flags = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        operation = library.renameat2
        flags = 0x1  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
    operation.argtypes = (
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    )
    operation.restype = ctypes.c_int
    if operation(parent_fd, source_bytes, parent_fd, destination_bytes, flags) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def _quarantine_invalidate_remove(
    parent_fd: int, source: str, identity: tuple[int, int],
) -> bool:
    quarantine = None
    descriptor = None
    write_descriptor = None
    try:
        for _ in range(32):
            quarantine = f".{source}.invalidate-{secrets.token_hex(16)}"
            try:
                _rename_no_replace(parent_fd, source, quarantine)
                break
            except FileExistsError:
                continue
        else:
            return False
        descriptor = os.open(
            quarantine, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != identity:
            _rename_no_replace(parent_fd, quarantine, source)
            quarantine = None
            os.fsync(parent_fd)
            return False
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        write_descriptor = os.open(
            quarantine, os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent_fd,
        )
        writable = os.fstat(write_descriptor)
        if (writable.st_dev, writable.st_ino) != identity:
            return False
        os.ftruncate(write_descriptor, 0)
        os.fchmod(write_descriptor, 0)
        os.fsync(write_descriptor)
        invalidated = os.fstat(write_descriptor)
        if (
            (invalidated.st_dev, invalidated.st_ino) != identity
            or stat.S_IMODE(invalidated.st_mode) != 0
            or invalidated.st_size != 0
        ):
            return False
        os.stat(quarantine, dir_fd=parent_fd, follow_symlinks=False)
        named_fd = os.open(
            quarantine, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd,
        )
        try:
            named = os.fstat(named_fd)
        finally:
            os.close(named_fd)
        if (named.st_dev, named.st_ino) != identity:
            try:
                _rename_no_replace(parent_fd, quarantine, source)
                quarantine = None
            except OSError:
                pass
            os.fsync(parent_fd)
            return False
        os.fsync(parent_fd)
        return False
    except OSError:
        return False
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)
        if write_descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(write_descriptor)


def _validate_profile_candidate(candidate, profile, **kwargs):
    if profile == "production":
        return validate_candidate(candidate, **kwargs)
    return validate_candidate(candidate, qualification_profile=profile, **kwargs)


def _run_gate_impl(
    candidate_path: Path,
    mode: str,
    *,
    assignment_guard: AssignmentRuntimeGuard,
    lanes: Sequence[Lane] | None = None,
    source_environment: Mapping[str, str] | None = None,
    max_output_bytes: int = MAX_LANE_OUTPUT_BYTES,
    report_path: Path | None = None,
    runtime_root: Path | None = None,
    qualification_profile: str = "production",
) -> dict:
    def blocked(candidate_id: str | None, failed_lane: str) -> dict:
        report = _blocked(candidate_id, failed_lane)
        if qualification_profile == "m1-staging":
            report["qualificationProfile"] = qualification_profile
        return report

    candidate = _load_candidate(candidate_path)
    candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    selected: tuple[Lane, ...] = ()
    require_runtime = False
    operator_binding: OperatorAttestationBinding | None = None
    physical_admission_binding: PhysicalAdmissionBinding | None = None
    published_physical_result: tuple | None = None
    source = source_environment if source_environment is not None else os.environ
    report_destination = None
    if report_path is not None:
        if candidate is not None:
            report_destination = _prepare_report_destination(candidate_path, candidate, report_path)
        if report_destination is None:
            return blocked(candidate_id if isinstance(candidate_id, str) else None, "report")
    if candidate is None or _validate_profile_candidate(candidate, qualification_profile):
        report = blocked(candidate_id if isinstance(candidate_id, str) else None, "candidate")
    elif mode not in MODES or type(max_output_bytes) is not int or max_output_bytes <= 0:
        report = blocked(candidate_id, "configuration")
    elif lanes is None and (
        operator_binding := _operator_attestation_binding(candidate, source)
    ) is None:
        report = blocked(candidate_id, "operator-precondition")
    elif lanes is None and report_path is not None and _paths_alias(
        report_path, operator_binding.path,
    ):
        assert report_destination is not None
        _close_report_destination(report_destination)
        return blocked(candidate_id, "report")
    elif lanes is None and not _runtime_matches_candidate(candidate, runtime_root):
        report = blocked(candidate_id, "candidate-runtime")
    else:
        selected = tuple(lanes) if lanes is not None else lanes_for_mode(mode)
        require_runtime = lanes is None
        repositories = candidate["repositories"]
        lane_names = [lane.name for lane in selected]
        if (
            len(lane_names) != len(set(lane_names))
            or not all(_valid_lane(lane, repositories) for lane in selected)
        ):
            report = blocked(candidate_id, "configuration")
        else:
            report = {
                "candidateId": candidate_id, "verdict": "PASS", "lanes": [], "failedLane": None,
            }
            assignment_source = (
                _assignment_source_snapshot(source)
                if any(lane.name in STATEFUL_ASSIGNMENT_LANES for lane in selected)
                else None
            )
            assignment_runtime: AssignmentRuntimeCapsule | None = None
            last_assignment_name = next(
                (
                    lane.name for lane in reversed(selected)
                    if lane.name in STATEFUL_ASSIGNMENT_LANES
                ),
                None,
            )
            if not release_state_matches(
                candidate_path, candidate, selected, runtime_root, require_runtime,
            ):
                report = blocked(candidate_id, selected[0].name if selected else "candidate-runtime")
                if selected:
                    report["lanes"].append({
                        "name": selected[0].name, "exitCode": None, "durationMs": 0,
                    })
            for lane in selected if report["verdict"] == "PASS" else ():
                execution_stage = None
                lane_execution = None
                rollback_restore_binding = None
                lane_source = (
                    assignment_source if lane.name in STATEFUL_ASSIGNMENT_LANES else source
                )
                if lane_source is None:
                    report["lanes"].append({
                        "name": lane.name, "exitCode": None, "durationMs": 0,
                    })
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if lane.name == LIVE_DB_LANE.name:
                    live_db_source = _live_db_source_snapshot(source)
                    if live_db_source is None:
                        report["lanes"].append({
                            "name": lane.name, "exitCode": None, "durationMs": 0,
                        })
                        report["verdict"] = "BLOCKED"
                        report["failedLane"] = lane.name
                        break
                    lane_source = live_db_source
                required_environment = _required_environment(lane)
                required_source = {
                    name: lane_source.get(name) for name in required_environment
                }
                if any(not required_source[name] for name in required_environment):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if _assignment_port_environment(required_source, lane) is None:
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if lane.name == LIVE_DB_LANE.name and not _live_db_topology_ready(lane_source):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if _assignment_candidate_environment(candidate, lane) is None:
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if (
                    lane.name in STATEFUL_ASSIGNMENT_LANES
                    and not assignment_input_sources_ready(candidate)
                ):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if lane.required_source_contract and not source_contract_ready(
                    Path(repositories["adminEsp"]["path"]), lane.required_source_contract,
                    repositories["adminEsp"]["sha"],
                ):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                physical_admission_binding = (
                    _physical_admission_binding(
                        candidate, require_output_absent=True,
                        expected_candidate_path=candidate_path,
                    )
                    if lane.name == "physical-flash-admission" else None
                )
                physical_source_runtime_binding = (
                    _physical_python_runtime_binding(candidate, verify_authority=True)
                    if lane.name == "physical-flash-admission" else None
                )
                lane_command = _command_for_lane(lane, candidate)
                if lane.name == "physical-flash-admission" and (
                    physical_admission_binding is None
                    or physical_source_runtime_binding is None
                    or _physical_admission_binding(
                        candidate, require_output_absent=True,
                        expected_candidate_path=candidate_path,
                    ) != physical_admission_binding
                ):
                    report["lanes"].append({
                        "name": lane.name, "exitCode": None, "durationMs": 0,
                    })
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                try:
                    if lane.name in STATEFUL_ASSIGNMENT_LANES:
                        if assignment_runtime is None:
                            protected = tuple(
                                Path(item["path"])
                                for item in candidate["repositories"].values()
                            ) + (
                                candidate_path,
                                Path(required_source["TASK4_ASSIGNMENT_RUNTIME_ROOT"]),
                                *(tuple([report_path]) if report_path is not None else ()),
                                *(
                                    tuple([operator_binding.path])
                                    if operator_binding is not None else ()
                                ),
                            )
                            assignment_runtime = AssignmentRuntimeCapsule.create(protected)
                            assignment_guard.own(assignment_runtime, report)
                        if not assignment_runtime.usable():
                            report["verdict"] = "BLOCKED"
                            report["failedLane"] = "cleanup"
                            _cleanup_gate_owned(report, assignment_runtime)
                            assignment_runtime = None
                            assignment_guard.release()
                            break
                    execution_stage = stage_execution_candidate(candidate, (lane,))
                    rollback_restore_binding = _assignment_rollback_restore_binding(
                        lane, candidate, execution_stage.candidate,
                    )
                    lane_execution = (
                        execution_stage.create_lane_execution(use_read_only_candidate=True)
                        if lane.name == "physical-flash-admission"
                        else execution_stage.create_lane_execution()
                    )
                    execution_candidate = lane_execution.candidate
                except RetainedStagingError as error:
                    retained_paths = set(error.paths)
                    if execution_stage is not None and not execution_stage.cleanup():
                        retained_paths.add(str(execution_stage.root))
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = "cleanup"
                    report["retainedOwner"] = "current-process"
                    report["retainedPaths"] = sorted(retained_paths)
                    break
                except (OSError, RuntimeError, TypeError, ValueError):
                    if _cleanup_gate_owned(report, execution_stage):
                        report["verdict"] = "BLOCKED"
                        report["failedLane"] = "snapshot"
                    break
                lane_environment = _child_environment(
                    execution_candidate,
                    required_source,
                    lane,
                    source_candidate=candidate,
                    assignment_runtime_capsule_root=(
                        assignment_runtime.root
                        if assignment_runtime is not None else None
                    ),
                    assignment_runtime_root=(
                        assignment_runtime.runtime_root
                        if assignment_runtime is not None else None
                    ),
                )
                if lane_environment is None:
                    report["lanes"].append({
                        "name": lane.name, "exitCode": None, "durationMs": 0,
                    })
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    _cleanup_gate_owned(report, lane_execution, execution_stage)
                    break
                command = _resolve_candidate_command(lane_command, execution_candidate, lane) if lane_command else None
                physical_runtime_binding = (
                    _physical_python_runtime_binding(
                        execution_candidate, verify_authority=False,
                    )
                    if lane.name == "physical-flash-admission" else None
                )
                physical_source_binding = (
                    _physical_admission_source_binding(execution_candidate, candidate)
                    if lane.name == "physical-flash-admission" else None
                )
                physical_bound_execution = (
                    _physical_admission_bound_execution(command, physical_source_binding)
                    if command is not None and physical_source_binding is not None
                    and lane.name == "physical-flash-admission" else None
                )
                if lane.name == "physical-flash-admission" and physical_bound_execution is None:
                    command = None
                elif physical_bound_execution is not None:
                    command, physical_source_environment = physical_bound_execution
                else:
                    physical_source_environment = {}
                if command is None:
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    _cleanup_gate_owned(report, lane_execution, execution_stage)
                    break
                root = Path(execution_candidate["repositories"][lane.repository]["path"])
                cwd = root / lane.relative_cwd
                try:
                    cwd_fd = _open_snapshot_directory(cwd)
                    os.close(cwd_fd)
                    resolved_cwd = cwd
                except OSError:
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    _cleanup_gate_owned(report, lane_execution, execution_stage)
                    break
                started = time.monotonic_ns()
                started_at = time.time()
                junit_path: Path | None = None
                if lane.reject_pytest_skips:
                    report_root = Path(lane_execution.environment["COURSE_MODE_LANE_REPORT_ROOT"])
                    descriptor, name = tempfile.mkstemp(
                        prefix="course-mode-pytest-", suffix=".xml", dir=report_root,
                    )
                    os.close(descriptor)
                    junit_path = Path(name).resolve()
                    command = (*command, f"--junitxml={junit_path}")
                if _python_test_runtime_required(lane):
                    runtime_descriptor = execution_candidate["tools"]["pythonTestRuntime"]
                    observed, error = _manifest.secure_python_test_runtime_tree_descriptor(
                        Path(runtime_descriptor["root"]),
                    )
                    sandboxed = _sandboxed_python_lane_command(
                        command, execution_stage, lane_execution,
                    )
                    if (
                        error or observed != runtime_descriptor["treeDigest"]
                        or not _manifest.python_test_runtime_authorized(runtime_descriptor)
                        or sandboxed is None
                    ):
                        report["lanes"].append({
                            "name": lane.name, "exitCode": None, "durationMs": 0,
                        })
                        report["verdict"] = "BLOCKED"
                        report["failedLane"] = lane.name
                        if junit_path is not None:
                            with contextlib.suppress(OSError):
                                junit_path.unlink()
                        _cleanup_gate_owned(report, lane_execution, execution_stage)
                        break
                    command = sandboxed
                backend_binding: BackendSnapshotBinding | None = None
                try:
                    child_environment = lane_environment
                    child_environment.update(lane_execution.environment)
                    child_environment.update(physical_source_environment)
                    bounded_result: _manifest.BoundedCommandResult | None = None
                    container_authority = (
                        not _container_tools_required(lane)
                        or _container_tools_authorized(execution_candidate)
                    )
                    browser_authority = (
                        not _playwright_browsers_required(lane)
                        or playwright_browsers_authorized(execution_candidate)
                    )
                    operator_authority = (
                        operator_binding is None
                        or _operator_attestation_binding(candidate, source) == operator_binding
                    )
                    if not container_authority or not browser_authority or not operator_authority:
                        result = _manifest.BoundedCommandResult(None, "", "authority")
                    elif lane.name == "backend-tests":
                        result = _run_backend_native_tests(
                            command, execution_candidate, child_environment, resolved_cwd,
                            lane.timeout_sec, max_output_bytes,
                        )
                        bounded_result = result
                    elif _python_test_runtime_required(lane) or _backend_compiler_required(lane):
                        backend_binding = _backend_snapshot_environment(execution_stage)
                        if backend_binding is None:
                            result = _manifest.BoundedCommandResult(None, "", "authority")
                        else:
                            child_environment.update(backend_binding.environment)
                            if _backend_compiler_required(lane):
                                child_environment["TBOT_BACKEND_WORKTREE"] = (
                                    backend_binding.environment["COURSE_MODE_BACKEND_ROOT"]
                                )
                            result = run_bounded_command(
                                list(command), cwd=resolved_cwd, timeout_sec=lane.timeout_sec,
                                max_output_bytes=max_output_bytes, env=child_environment,
                                contain_process_group=lane.name in STATEFUL_ASSIGNMENT_LANES,
                            )
                            bounded_result = result
                    else:
                        if lane.name == "physical-flash-admission" and (
                            _physical_admission_binding(
                                candidate, require_output_absent=True,
                                expected_candidate_path=candidate_path,
                            ) != physical_admission_binding
                            or physical_runtime_binding is None
                            or _physical_python_runtime_binding(
                                execution_candidate, verify_authority=False,
                            ) != physical_runtime_binding
                            or not _physical_admission_sources_still_bound(
                                physical_source_binding,
                            )
                        ):
                            result = _manifest.BoundedCommandResult(None, "", "authority")
                        else:
                            result = run_bounded_command(
                                list(command), cwd=resolved_cwd, timeout_sec=lane.timeout_sec,
                                max_output_bytes=max_output_bytes, env=child_environment,
                                contain_process_group=lane.name in STATEFUL_ASSIGNMENT_LANES,
                            )
                            if _physical_admission_binding(
                                candidate, require_output_absent=False,
                                expected_candidate_path=candidate_path,
                            ) != physical_admission_binding:
                                result = _manifest.BoundedCommandResult(
                                    None, result.stdout, "authority",
                                )
                        bounded_result = result
                    if (
                        (_python_test_runtime_required(lane) or _backend_compiler_required(lane))
                        and (
                            backend_binding is None
                            or not _backend_execution_snapshot_matches(execution_stage, backend_binding)
                        )
                    ):
                        result = _manifest.BoundedCommandResult(None, "", "authority")
                    if (
                        _container_tools_required(lane)
                        and not _container_tools_authorized(execution_candidate)
                    ):
                        result = _manifest.BoundedCommandResult(None, "", "authority")
                    if (
                        _playwright_browsers_required(lane)
                        and not playwright_browsers_authorized(execution_candidate)
                    ):
                        result = _manifest.BoundedCommandResult(None, "", "authority")
                    parent_restore_succeeded = (
                        _restore_base_stack_after_abnormal_assignment_rollback(
                            lane, bounded_result, candidate_path, candidate,
                            execution_stage.candidate, rollback_restore_binding,
                            child_environment, max_output_bytes,
                        )
                        if bounded_result is not None else None
                    )
                    skip_state = pytest_report_has_skips(junit_path) if junit_path else False
                except BaseException as interrupted_error:
                    retained_paths = set(interrupted_error.paths) if isinstance(interrupted_error, RetainedStagingError) else set()
                    try:
                        _cleanup_gate_owned_or_raise(
                            lane_execution, execution_stage, assignment_runtime,
                        )
                        assignment_runtime = None
                        assignment_guard.release()
                    except RetainedStagingError as error:
                        retained_paths.update(error.paths)
                    if retained_paths:
                        report["verdict"] = "BLOCKED"
                        report["failedLane"] = "cleanup"
                        report["cleanupFailed"] = True
                        report["retainedOwner"] = "current-process"
                        report["retainedPaths"] = sorted(retained_paths)
                        report["interrupted"] = isinstance(interrupted_error, (KeyboardInterrupt, SystemExit)) or isinstance(interrupted_error.__cause__, (KeyboardInterrupt, SystemExit))
                        assignment_runtime = None
                        assignment_guard.release()
                        break
                    raise
                finally:
                    if junit_path is not None:
                        with contextlib.suppress(OSError):
                            junit_path.unlink()
                duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
                exit_code = None if result.error else result.returncode
                report["lanes"].append({
                    "name": lane.name, "exitCode": exit_code, "durationMs": duration_ms,
                })
                rollback_restore_failed = _assignment_rollback_restore_failed(
                    lane, result.stdout,
                )
                lane_failed = result.error or result.returncode != 0 or rollback_restore_failed
                physical_post_run_sources_valid = (
                    _physical_admission_sources_still_bound(physical_source_binding)
                    if lane.name == "physical-flash-admission" else True
                )
                if lane.name == "physical-flash-admission":
                    physical_output = Path(dict(physical_admission_binding.descriptor)["output"])
                    published_physical_result, physical_result_error = _admission._secure_read(
                        physical_output, _admission.MAX_JSON_BYTES,
                    )
                    if physical_result_error is not None:
                        published_physical_result = None
                physical_result_valid = (
                    physical_post_run_sources_valid
                    and _physical_admission_result_valid(
                        candidate, physical_admission_binding,
                        started_at=started_at,
                        expected_record=published_physical_result,
                    )
                    if lane.name == "physical-flash-admission" else True
                )
                physical_final_sources_valid = (
                    _physical_admission_sources_still_bound(physical_source_binding)
                    if lane.name == "physical-flash-admission" else True
                )
                physical_result_still_bound = (
                    published_physical_result is not None
                    and _admission._still_bound(published_physical_result)
                    if lane.name == "physical-flash-admission" else True
                )
                if lane.name == "physical-flash-admission" and (
                    physical_runtime_binding is None
                    or _physical_python_runtime_binding(
                        execution_candidate, verify_authority=False,
                    ) != physical_runtime_binding
                    or not physical_post_run_sources_valid
                    or not physical_result_valid
                    or not physical_final_sources_valid
                    or not physical_result_still_bound
                ):
                    lane_failed = True
                    result = _manifest.BoundedCommandResult(None, result.stdout, "authority")
                    exit_code = None
                    report["lanes"][-1]["exitCode"] = None
                if lane_failed:
                    cleanup_failed = (
                        rollback_restore_failed or parent_restore_succeeded is False
                        or result.error == "native-cleanup"
                    )
                    report["verdict"] = (
                        "BLOCKED"
                        if cleanup_failed or result.error in {"authority", "containment", "native-prerequisite"}
                        else "FAIL"
                    )
                    report["failedLane"] = lane.name
                    if cleanup_failed:
                        report["cleanupFailed"] = True
                        if result.error == "native-cleanup":
                            report["retainedResource"] = result.stdout
                elif skip_state is not False:
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                if not _cleanup_gate_owned(report, lane_execution, execution_stage):
                    break
                if lane.name == last_assignment_name:
                    if not _cleanup_gate_owned(report, assignment_runtime):
                        assignment_runtime = None
                        assignment_guard.release()
                        break
                    assignment_runtime = None
                    assignment_guard.release()
                if operator_binding is not None and _operator_attestation_binding(
                    candidate, source,
                ) != operator_binding:
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = "operator-precondition"
                    break
                if not _candidate_metadata_matches(candidate_path, candidate):
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if lane_failed:
                    break
                if skip_state is not False:
                    break
            if assignment_runtime is not None:
                _cleanup_gate_owned(report, assignment_runtime)
                assignment_runtime = None
                assignment_guard.release()
            if report["verdict"] == "PASS":
                if operator_binding is not None and _operator_attestation_binding(
                    candidate, source,
                ) != operator_binding:
                    report = blocked(candidate_id, "operator-precondition")
                elif not _candidate_metadata_matches(candidate_path, candidate):
                    report = blocked(candidate_id, selected[-1].name if selected else "candidate-runtime")
                elif operator_binding is not None:
                    report["operatorAttestationSha256"] = operator_binding.sha256
    if operator_binding is not None and _operator_attestation_binding(
        candidate, source,
    ) != operator_binding:
        if report.get("failedLane") == "cleanup":
            report["cleanupFailed"] = True
        report["verdict"] = "BLOCKED"
        report["failedLane"] = "operator-precondition"
        report.pop("operatorAttestationSha256", None)
    if (
        report["verdict"] == "PASS" and published_physical_result is not None
        and not _admission._still_bound(published_physical_result)
    ):
        report = blocked(candidate_id, "physical-flash-admission")
    if report["verdict"] == "PASS" and physical_admission_binding is not None and (
        _physical_admission_binding(
            candidate, require_output_absent=False,
            expected_candidate_path=candidate_path,
        ) != physical_admission_binding
    ):
        report = blocked(candidate_id, "physical-flash-admission")
    if report["verdict"] != "PASS" and published_physical_result is not None:
        if _remove_bound_physical_admission_result(published_physical_result):
            published_physical_result = None
        else:
            report["cleanupFailed"] = True
    if qualification_profile == "m1-staging":
        report["qualificationProfile"] = qualification_profile
    if report_path is not None:
        assert report_destination is not None
        report_parent_identity = None
        report_finalization_failed = False
        pending_interrupt = None
        pending_traceback = None
        try:
            report_parent = os.fstat(report_destination.parent_fd)
            report_parent_identity = (report_parent.st_dev, report_parent.st_ino)
            if operator_binding is not None and (
                _operator_attestation_binding(candidate, source) != operator_binding
            ):
                if report.get("failedLane") == "cleanup":
                    report["cleanupFailed"] = True
                report["verdict"] = "BLOCKED"
                report["failedLane"] = "operator-precondition"
                report.pop("operatorAttestationSha256", None)
            if report["verdict"] == "PASS" and physical_admission_binding is not None and (
                _physical_admission_binding(
                    candidate, require_output_absent=False,
                    expected_candidate_path=candidate_path,
                ) != physical_admission_binding
            ):
                report = blocked(candidate_id, "physical-flash-admission")
            if not _write_report_atomic(report_path, report, report_destination):
                report_finalization_failed = True
            else:
                post_publish_report = None
                if report["verdict"] == "PASS" and operator_binding is not None and (
                    _operator_attestation_binding(candidate, source) != operator_binding
                ):
                    post_publish_report = blocked(candidate_id, "operator-precondition")
                elif report["verdict"] == "PASS" and physical_admission_binding is not None and (
                    _physical_admission_binding(
                        candidate, require_output_absent=False,
                        expected_candidate_path=candidate_path,
                    ) != physical_admission_binding
                ):
                    post_publish_report = blocked(candidate_id, "physical-flash-admission")
                elif report["verdict"] == "PASS" and published_physical_result is not None and (
                    not _admission._still_bound(published_physical_result)
                ):
                    post_publish_report = blocked(candidate_id, "physical-flash-admission")
                elif report["verdict"] == "PASS" and not _candidate_metadata_matches(
                    candidate_path, candidate,
                ):
                    post_publish_report = blocked(
                        candidate_id, selected[-1].name if selected else "candidate-runtime",
                    )
                if post_publish_report is not None:
                    report = post_publish_report
                    if published_physical_result is not None:
                        if _remove_bound_physical_admission_result(published_physical_result):
                            published_physical_result = None
                        else:
                            report["cleanupFailed"] = True
                    if not _write_report_atomic(report_path, report, report_destination):
                        report_finalization_failed = True
        except BaseException as error:
            report_finalization_failed = True
            if not isinstance(error, Exception):
                pending_interrupt = error
                pending_traceback = error.__traceback__
        finally:
            if report_finalization_failed:
                _invalidate_report(report_path, report_destination)
            try:
                _close_report_destination(report_destination)
            except BaseException as error:
                report_finalization_failed = True
                if pending_interrupt is None and not isinstance(error, Exception):
                    pending_interrupt = error
                    pending_traceback = error.__traceback__
            if report_finalization_failed:
                report_removed = (
                    report_parent_identity is not None
                    and _remove_bound_report(
                        report_path, report_destination.identity, report_parent_identity,
                    )
                )
                if not report_removed:
                    report = blocked(candidate_id, "report")
                    report["cleanupFailed"] = True
                else:
                    report = blocked(candidate_id, "report")
            if report_finalization_failed and published_physical_result is not None:
                if _remove_bound_physical_admission_result(published_physical_result):
                    published_physical_result = None
                else:
                    report["cleanupFailed"] = True
        if pending_interrupt is not None:
            raise pending_interrupt.with_traceback(pending_traceback)
    if (
        report["verdict"] != "PASS" and published_physical_result is not None
        and not _remove_bound_physical_admission_result(published_physical_result)
    ):
        report["cleanupFailed"] = True
    return report


def run_gate(
    candidate_path: Path,
    mode: str,
    *,
    lanes: Sequence[Lane] | None = None,
    source_environment: Mapping[str, str] | None = None,
    max_output_bytes: int = MAX_LANE_OUTPUT_BYTES,
    report_path: Path | None = None,
    runtime_root: Path | None = None,
    qualification_profile: str = "production",
) -> dict:
    assignment_guard = AssignmentRuntimeGuard()
    try:
        return _run_gate_impl(
            candidate_path,
            mode,
            assignment_guard=assignment_guard,
            lanes=lanes,
            source_environment=source_environment,
            max_output_bytes=max_output_bytes,
            report_path=report_path,
            runtime_root=runtime_root,
            qualification_profile=qualification_profile,
        )
    finally:
        assignment_guard.cleanup()


def _emit(report: dict) -> None:
    print(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ))


def lane_inventory() -> dict[str, list[str]]:
    return {mode: [lane.name for lane in lanes_for_mode(mode)] for mode in MODES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--mode", choices=MODES, default="quick")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--profile", choices=("production", "m1-staging"), default="production")
    parser.add_argument("--list-lanes", action="store_true")
    args = parser.parse_args(argv)
    if args.list_lanes:
        if args.candidate is not None or args.report is not None:
            parser.error("--list-lanes cannot be combined with candidate execution")
        print(json.dumps(lane_inventory(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.candidate is None:
        parser.error("--candidate is required")
    report = run_gate(args.candidate, args.mode, report_path=args.report, qualification_profile=args.profile)
    _emit(report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
