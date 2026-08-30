#!/usr/bin/env python3
"""Run candidate-bound Course Mode production-readiness lanes."""

from __future__ import annotations

import argparse
import contextlib
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
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

try:
    _manifest = importlib.import_module("scripts.course_mode_candidate_manifest")
except ModuleNotFoundError:
    _manifest = importlib.import_module("course_mode_candidate_manifest")

MAX_CANDIDATE_BYTES = _manifest.MAX_CANDIDATE_BYTES
_repository_matches_candidate = _manifest._repository_matches_candidate
read_secure_regular = _manifest.read_secure_regular
run_bounded_command = _manifest.run_bounded_command
strict_json_loads = _manifest.strict_json_loads
validate_candidate = _manifest.validate_candidate
_candidate_git = _manifest._git
secure_browser_bundle_descriptor = _manifest.secure_browser_bundle_descriptor


SECURE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
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
MAX_SNAPSHOT_ENTRIES = 750_000
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024 * 1024
MAX_SNAPSHOT_FILE_BYTES = 512 * 1024 * 1024
MAX_SNAPSHOT_DEPTH = 256
MAX_GIT_ARCHIVE_LISTING_BYTES = 64 * 1024 * 1024
GIT_BLOB_CHUNK_BYTES = 1024 * 1024
MAX_GIT_SYMLINK_BYTES = 16 * 1024
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
)
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
}
SAFE_PHYSICAL_CONTRACT_TESTS = {
    "test_course_mode_physical_tft_compose.py",
    "test_course_mode_physical_tft_ledger_validate.py",
    "test_course_mode_physical_tft_preflight.py",
    "test_course_mode_physical_tft_receipt_verify.py",
}


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

    def create_lane_execution(self) -> LaneExecution:
        lane_root = Path(tempfile.mkdtemp(prefix="course-mode-lane-", dir=self.root.parent))
        lane_identity: tuple[int, int] | None = None
        lane_descriptor: int | None = None
        try:
            lane_identity = _owned_tree_identity(lane_root)
            lane_descriptor = _open_snapshot_directory(lane_root)
            execution_root = lane_root / "candidate"
            shutil.copytree(self.root, execution_root, symlinks=True)
            python_runtime = self.root / "tools/python-test-runtime"
            excluded = (execution_root / "tools/python-test-runtime",) if python_runtime.is_dir() else ()
            _make_tree_owner_writable(execution_root, excluded_roots=excluded)
            source_prefix = str(self.root) + os.sep
            target_prefix = str(execution_root) + os.sep

            def rebase(value: object) -> object:
                if isinstance(value, dict):
                    return {key: rebase(item) for key, item in value.items()}
                if isinstance(value, list):
                    return [rebase(item) for item in value]
                if isinstance(value, str) and value.startswith(source_prefix):
                    return target_prefix + value[len(source_prefix):]
                return value

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
            rebased_candidate = rebase(self.candidate)
            if excluded:
                descriptor = rebased_candidate["tools"]["pythonTestRuntime"]
                observed, error = _manifest.secure_python_test_runtime_tree_descriptor(
                    Path(descriptor["root"]),
                )
                if error or observed != descriptor["treeDigest"]:
                    raise ValueError("lane Python test runtime descriptor mismatch")
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


def _make_tree_owner_writable(
    root: Path, *, excluded_roots: Sequence[Path] = (),
) -> None:
    excluded = {str(path) for path in excluded_roots}
    for directory, names, files in os.walk(root, topdown=True):
        names[:] = [
            name for name in names if str(Path(directory) / name) not in excluded
        ]
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
            raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytearray(1024))
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
            os.unlink(quarantine, dir_fd=directory_fd)
            try:
                os.stat(quarantine, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                if os.fstat(leaf_fd).st_nlink != 0:
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


def _cleanup_gate_owned(report: dict, *owned: ExecutionStage | LaneExecution | None) -> bool:
    retained = []
    for item in owned:
        if item is not None and not item.cleanup():
            retained.append(str(item.retained_path()))
    if retained:
        report["verdict"] = "BLOCKED"
        report["failedLane"] = "cleanup"
        report["retainedOwner"] = "current-process"
        report["retainedPaths"] = sorted(set(retained))
        return False
    return True


def _cleanup_gate_owned_or_raise(*owned: ExecutionStage | LaneExecution | None) -> None:
    retained = []
    for item in owned:
        if item is not None and not item.cleanup():
            retained.append(str(item.retained_path()))
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


def _archive_repository(source: Path, sha: str, destination: Path, state: dict[str, int]) -> None:
    base = [
        str(_manifest.TRUSTED_GIT_EXECUTABLE), "--no-replace-objects", "--no-optional-locks",
        "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
        "-c", "credential.helper=",
    ]
    try:
        resolved = _manifest.run_bounded_command(
            [*base, "rev-parse", "--verify", f"{sha}^{{commit}}"], cwd=source,
            env=_manifest.SECURE_ENV, timeout_sec=60, max_output_bytes=1024,
        )
        if resolved.error or resolved.returncode != 0 or resolved.stdout.strip() != sha:
            raise ValueError("candidate archive failed")
        listing = _manifest.run_bounded_command(
            [*base, "ls-tree", "-r", "-z", "-t", "--full-tree", sha], cwd=source,
            env=_manifest.SECURE_ENV, timeout_sec=60,
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
            [*base, "cat-file", "--batch"], cwd=source, env=_manifest.SECURE_ENV,
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
            if batch.returncode != 0:
                raise ValueError("candidate archive failed")
    except ValueError:
        raise
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise ValueError("candidate archive failed") from error


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
        if any(_python_test_runtime_required(lane) for lane in lanes):
            descriptor = candidate["tools"]["pythonTestRuntime"]
            python_target = tools_root / "python-test-runtime"
            tools_root.mkdir()
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
        if any(lane.name == "admin-browser" for lane in lanes):
            browser = candidate["tools"]["robotPreviewBrowser"]
            browser_target = tools_root / "robot-preview-browser"
            _copy_snapshot_tree(Path(browser["root"]), browser_target, state)
            observed, error = secure_browser_bundle_descriptor(browser_target)
            if error or observed != browser["treeDigest"]:
                raise ValueError("staged browser descriptor mismatch")
            staged["tools"]["robotPreviewBrowser"]["root"] = str(browser_target)
        _make_tree_read_only(root)
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
    _lane("backend-tests", "backend", ".", ("npm", "test", "--", "--no-cache"), 1800.0),
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
            1200.0, ("COURSE_MODE_ADMIN_E2E_READY",),
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
    "physical-tft-preflight", "adminEsp", "main/tbot-server",
    ("python3", "scripts/course_mode_physical_tft_preflight.py"),
    900.0,
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


def discover_esp_course_mode_tests(admin_root: Path, sha: str) -> tuple[str, ...]:
    try:
        tracked = _candidate_git(
            admin_root, "ls-tree", "-r", "--name-only", "-z", sha, "--",
            "main/tbot-server/tests",
        ).split("\0")
    except RuntimeError:
        return ()
    discovered = []
    for relative in sorted(path for path in tracked if path):
        name = Path(relative).name
        if not (
            (name.startswith("test_course_mode") and name.endswith(".py"))
            or name == "test_google_live_course_mode.py"
        ):
            continue
        if name == "test_course_mode_release_gate.py":
            continue
        discovered.append(f"tests/{name}")
    return tuple(discovered)


def classify_esp_course_mode_test(relative: str) -> str:
    name = Path(relative).name
    if name in SAFE_PHYSICAL_CONTRACT_TESTS:
        return "physical-contract"
    if "_physical_" in name:
        return "physical-runtime"
    if "postgres" in name or "live_db" in name:
        return "live-db"
    return "software"


def select_esp_software_tests(discovered: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        relative for relative in discovered
        if classify_esp_course_mode_test(relative) in {"software", "physical-contract"}
    )


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
        discovered = discover_esp_course_mode_tests(root, repository["sha"])
        paths.update(f"main/tbot-server/{relative}" for relative in select_esp_software_tests(discovered))
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
        if lane.name == "physical-tft-preflight" and any(
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
        repository = candidate["repositories"][lane.repository]
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


def release_state_matches(
    candidate_path: Path, candidate: dict, lanes: Sequence[Lane], runtime_root: Path | None,
    require_runtime: bool, node_lanes: Sequence[Lane] | None = None,
) -> bool:
    current = _load_candidate(candidate_path)
    if (
        current != candidate or current is None
        or validate_candidate(current, verify_external_tools=False)
        or validate_candidate(current, verify_external_tools=True)
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
    reporter = json.dumps(fixed["reporter"], sort_keys=True, separators=(", ", ": "))
    return "\n".join([
        "const { defineConfig, devices } = require('@playwright/test');",
        "const { lessonStudioWebOrigin } = require('./scripts/lesson-studio-e2e-environment.cjs');",
        "",
        "module.exports = defineConfig({",
        f"  testDir: {_js_string(fixed['testDir'])},",
        f"  globalSetup: {_js_string(fixed['globalSetup'])},",
        f"  testMatch: [{test_matches}],",
        f"  outputDir: {_js_string(fixed['outputDir'])},",
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
    tools = candidate.get("tools")
    metadata = tools.get("physicalPreflight") if isinstance(tools, dict) else None
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
    return (
        "python3", "scripts/course_mode_physical_tft_preflight.py",
        "--input", str(resolved["input"]), "--output", str(resolved["output"]),
        "--expected-identity", str(resolved["expectedIdentity"]),
        "--expected-identity-signature", str(resolved["expectedIdentitySignature"]),
    )


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


def _child_environment(candidate: dict, source: Mapping[str, str], lane: Lane) -> dict[str, str]:
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
    for name in _required_environment(lane):
        if source.get(name):
            environment[name] = source[name]
    if lane.name == LIVE_DB_LANE.name:
        environment["COURSE_MODE_V5_SOURCE_ROOT"] = candidate["repositories"]["adminEsp"]["path"]
    assignment = _assignment_candidate_environment(candidate, lane)
    if assignment is not None:
        environment.update(assignment)
    environment.update(dict(lane.fixed_environment))
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


def _assignment_candidate_environment(candidate: dict, lane: Lane) -> dict[str, str] | None:
    if lane.name not in {
        "admin-course-mode-assignment-new", "admin-course-mode-assignment-rollback",
    }:
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


def _candidate_metadata_matches(candidate_path: Path, candidate: dict) -> bool:
    observed = _load_candidate(candidate_path)
    return observed is not None and _json_exact_equal(observed, candidate)


def _command_for_lane(lane: Lane, candidate: dict) -> tuple[str, ...] | None:
    if lane.command == (COURSE_MODE_SOFTWARE_TESTS,):
        repository = candidate["repositories"]["adminEsp"]
        tests = select_esp_software_tests(
            discover_esp_course_mode_tests(Path(repository["path"]), repository["sha"])
        )
        return ("python3", "-m", "pytest", "-q", *tests) if tests else None
    if lane.name == "physical-tft-preflight":
        return physical_preflight_command(candidate)
    return lane.command


def _write_report_atomic(path: Path, report: dict, parent_fd: int | None = None) -> bool:
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_REPORT_BYTES or not path.is_absolute():
        return False
    owned_fd = parent_fd is None
    temporary = None
    try:
        if parent_fd is None:
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor_parent = os.fstat(parent_fd)
        named_parent = os.stat(path.parent, follow_symlinks=False)
        if (descriptor_parent.st_dev, descriptor_parent.st_ino) != (
            named_parent.st_dev, named_parent.st_ino,
        ):
            return False
        try:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                return False
        except FileNotFoundError:
            pass
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
        try:
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    return False
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary_stat = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_nlink != 1:
            return False
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary = None
        os.fsync(parent_fd)
        named_parent = os.stat(path.parent, follow_symlinks=False)
        if (descriptor_parent.st_dev, descriptor_parent.st_ino) != (
            named_parent.st_dev, named_parent.st_ino,
        ):
            _invalidate_report(path, parent_fd)
            return False
        return True
    except OSError:
        return False
    finally:
        if temporary is not None and parent_fd is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=parent_fd)
        if owned_fd and parent_fd is not None:
            os.close(parent_fd)


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
) -> int | None:
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
        for component in parent.relative_to(evidence_root).parts:
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = next_fd
        expected_parent = parent.stat()
        actual_parent = os.fstat(parent_fd)
        if (actual_parent.st_dev, actual_parent.st_ino) != (expected_parent.st_dev, expected_parent.st_ino):
            return None
        try:
            metadata = os.stat(report_path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                return None
        except FileNotFoundError:
            pass
        if any(
            _path_overlaps(report_path, protected)
            for protected in (candidate_input, *repository_roots)
        ):
            return None
        prepared = parent_fd
        parent_fd = None
        return prepared
    except (KeyError, OSError, TypeError, ValueError):
        return None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _invalidate_report(path: Path, parent_fd: int) -> None:
    with contextlib.suppress(OSError):
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)


def run_gate(
    candidate_path: Path,
    mode: str,
    *,
    lanes: Sequence[Lane] | None = None,
    source_environment: Mapping[str, str] | None = None,
    max_output_bytes: int = MAX_LANE_OUTPUT_BYTES,
    report_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict:
    candidate = _load_candidate(candidate_path)
    candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    selected: tuple[Lane, ...] = ()
    require_runtime = False
    report_parent_fd = None
    if report_path is not None:
        if candidate is not None:
            report_parent_fd = _prepare_report_destination(candidate_path, candidate, report_path)
        if report_parent_fd is None:
            return _blocked(candidate_id if isinstance(candidate_id, str) else None, "report")
    if candidate is None or validate_candidate(candidate):
        report = _blocked(candidate_id if isinstance(candidate_id, str) else None, "candidate")
    elif mode not in MODES or type(max_output_bytes) is not int or max_output_bytes <= 0:
        report = _blocked(candidate_id, "configuration")
    elif lanes is None and not _runtime_matches_candidate(candidate, runtime_root):
        report = _blocked(candidate_id, "candidate-runtime")
    else:
        selected = tuple(lanes) if lanes is not None else lanes_for_mode(mode)
        require_runtime = lanes is None
        repositories = candidate["repositories"]
        lane_names = [lane.name for lane in selected]
        if (
            len(lane_names) != len(set(lane_names))
            or not all(_valid_lane(lane, repositories) for lane in selected)
        ):
            report = _blocked(candidate_id, "configuration")
        else:
            report = {
                "candidateId": candidate_id, "verdict": "PASS", "lanes": [], "failedLane": None,
            }
            if not release_state_matches(
                candidate_path, candidate, selected, runtime_root, require_runtime,
            ):
                report = _blocked(candidate_id, selected[0].name if selected else "candidate-runtime")
                if selected:
                    report["lanes"].append({
                        "name": selected[0].name, "exitCode": None, "durationMs": 0,
                    })
            source = source_environment if source_environment is not None else os.environ
            for lane in selected if report["verdict"] == "PASS" else ():
                execution_stage = None
                lane_execution = None
                lane_source = source
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
                if any(not lane_source.get(name) for name in required_environment):
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
                if lane.name in {
                    "admin-course-mode-assignment-new", "admin-course-mode-assignment-rollback",
                } and not assignment_input_sources_ready(candidate):
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
                lane_command = _command_for_lane(lane, candidate)
                try:
                    execution_stage = stage_execution_candidate(candidate, (lane,))
                    lane_execution = execution_stage.create_lane_execution()
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
                command = _resolve_candidate_command(lane_command, execution_candidate, lane) if lane_command else None
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
                junit_path: Path | None = None
                if lane.reject_pytest_skips:
                    descriptor, name = tempfile.mkstemp(prefix="course-mode-pytest-", suffix=".xml")
                    os.close(descriptor)
                    junit_path = Path(name).resolve()
                    command = (*command, f"--junitxml={junit_path}")
                try:
                    child_environment = _child_environment(execution_candidate, lane_source, lane)
                    child_environment.update(lane_execution.environment)
                    result = run_bounded_command(
                        list(command), cwd=resolved_cwd, timeout_sec=lane.timeout_sec,
                        max_output_bytes=max_output_bytes,
                        env=child_environment,
                    )
                    skip_state = pytest_report_has_skips(junit_path) if junit_path else False
                except BaseException:
                    try:
                        _cleanup_gate_owned_or_raise(lane_execution, execution_stage)
                    except RetainedStagingError as error:
                        report["verdict"] = "BLOCKED"
                        report["failedLane"] = "cleanup"
                        report["retainedOwner"] = "current-process"
                        report["retainedPaths"] = list(error.paths)
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
                if not _cleanup_gate_owned(report, lane_execution, execution_stage):
                    break
                if not _candidate_metadata_matches(candidate_path, candidate):
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if result.error or result.returncode != 0:
                    report["verdict"] = "FAIL"
                    report["failedLane"] = lane.name
                    break
                if skip_state is not False:
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
            if report["verdict"] == "PASS" and not _candidate_metadata_matches(candidate_path, candidate):
                report = _blocked(candidate_id, selected[-1].name if selected else "candidate-runtime")
    if report_path is not None:
        assert report_parent_fd is not None
        if not _write_report_atomic(report_path, report, report_parent_fd):
            _invalidate_report(report_path, report_parent_fd)
            os.close(report_parent_fd)
            return _blocked(report.get("candidateId"), "report")
        if report["verdict"] == "PASS" and not _candidate_metadata_matches(candidate_path, candidate):
            report = _blocked(candidate_id, selected[-1].name if selected else "candidate-runtime")
            if not _write_report_atomic(report_path, report, report_parent_fd):
                _invalidate_report(report_path, report_parent_fd)
                os.close(report_parent_fd)
                return _blocked(candidate_id, "report")
        os.close(report_parent_fd)
    return report


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
    parser.add_argument("--list-lanes", action="store_true")
    args = parser.parse_args(argv)
    if args.list_lanes:
        if args.candidate is not None or args.report is not None:
            parser.error("--list-lanes cannot be combined with candidate execution")
        print(json.dumps(lane_inventory(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.candidate is None:
        parser.error("--candidate is required")
    report = run_gate(args.candidate, args.mode, report_path=args.report)
    _emit(report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
