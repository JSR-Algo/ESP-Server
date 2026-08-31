#!/usr/bin/env python3
"""Validate a Course Mode production-readiness candidate without mutating state."""

from __future__ import annotations

import argparse
import copy
import contextlib
import hashlib
import json
import math
import os
import posixpath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REQUIRED_KEYS = {
    "candidateId", "createdAt", "expiresAt", "course", "repositories",
    "images", "firmware", "database", "curriculum", "tools", "evidenceRoot",
}
REPOSITORIES = {"backend", "adminEsp", "firmware"}
REPOSITORY_KEYS = {"path", "sha", "branch", "remoteUrl", "dirtyExceptions"}
DIRTY_EXCEPTION_KEYS = {"path", "sha256"}
COURSE_KEYS = {"courseId", "courseKey"}
CURRICULUM_KEYS = {
    "courseId", "courseKey", "rendererId", "contractIdentity",
    "lessonCount", "activityCount", "pedagogyCount", "responseClassCount",
    "sourceChecksum",
}
SHA_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
CANDIDATE_ID_RE = re.compile(r"course-mode-[0-9]{4}-[0-9]{2}-[0-9]{2}\.[1-9][0-9]*")
RFC3339_UTC_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z"
)
COURSE_KEY = "english-6month-4-6"
RENDERER_ID = "teebot-lesson-renderer.v5"
CONTRACT_IDENTITY = "courseCompanion.v2.contract.v1"
MAX_CANDIDATE_BYTES = 1024 * 1024
MAX_DIRTY_FILE_BYTES = 4 * 1024 * 1024
MAX_BROWSER_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_BACKEND_SNAPSHOT_ENTRIES = 750_000
MAX_BACKEND_SNAPSHOT_BYTES = 4 * 1024 * 1024 * 1024
MAX_FIRMWARE_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_FIRMWARE_MANIFEST_BYTES = 1024 * 1024
MAX_BROWSER_BUNDLE_DEPTH = 128
MAX_GIT_OUTPUT_BYTES = 1024 * 1024
GIT_TIMEOUT_SEC = 10.0
_GIT_CANDIDATES = (
    Path("/Library/Developer/CommandLineTools/usr/bin/git"),
    Path("/usr/bin/git"),
)
TRUSTED_GIT_EXECUTABLE = next((path for path in _GIT_CANDIDATES if path.is_file()), _GIT_CANDIDATES[-1])
_DOCKER_CANDIDATES = (Path("/usr/local/bin/docker"), Path("/opt/homebrew/bin/docker"))
TRUSTED_DOCKER_EXECUTABLE = next(
    (path for path in _DOCKER_CANDIDATES if path.is_file()), _DOCKER_CANDIDATES[0],
)
CANONICAL_ESP_IDF_ROOT = Path("/Users/manhhodinh/esp/esp-idf")
SECURE_ENV = {
    "PATH": "/usr/bin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
    "HOME": "/nonexistent",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "PAGER": "cat",
}
BROWSER_DESCRIPTOR_KEYS = {"version", "engine", "revision", "root", "executable", "treeDigest"}
BROWSER_TREE_KEYS = {"schema", "sha256", "entryCount", "totalBytes"}
BROWSER_TREE_SCHEMA = "sha256-path-mode-bytes-v1"
IMAGE_KEYS = {"lessonStudioBackend", "lessonStudioWeb"}
IMAGE_DESCRIPTOR_KEYS = {"reference", "id"}
FIRMWARE_KEYS = {
    "appPath", "appOffset", "appBytes", "appSha256", "elfSha256",
    "partitionBytes", "freeBytes", "evidenceManifestPath", "evidenceManifestSha256",
}
DATABASE_KEYS = {"engineImage", "engineImageId", "migrationHead", "migrationHeadSha256"}
TOOLS_KEYS = {"nodeInstalls", "robotPreviewBrowser", "node", "pythonTestRuntime", "espIdf"}
NODE_KEYS = {"backend", "adminManagerWeb"}
NODE_DESCRIPTOR_KEYS = {
    "version", "executable", "sha256", "packageRoot", "packageRootMode",
    "packageTreeSha256", "npm", "npx",
}
NODE_ENTRYPOINT_KEYS = {"entrypoint", "sha256"}
ESP_IDF_KEYS = {"version", "commit", "root", "versionFile", "versionFileSha256"}
NODE_INSTALL_KEYS = {"version", "root", "packageLockSha256", "treeDigest"}
NODE_TREE_KEYS = {"schema", "sha256", "entryCount", "totalBytes"}
NODE_TREE_SCHEMA = "sha256-path-mode-bytes-symlink-v1"
NODE_PACKAGE_TREE_SCHEMA = "sha256-root-mode-path-mode-bytes-v1"
SECURE_NODE_PACKAGE_ROOT_MODES = {0o700, 0o755}
PYTHON_TEST_RUNTIME_KEYS = {
    "version", "distribution", "root", "executable", "pythonVersion", "pytestVersion",
    "treeDigest",
}
PYTHON_TEST_RUNTIME_TREE_KEYS = {
    "schema", "sha256", "entryCount", "totalBytes", "rootMode",
}
PYTHON_TEST_RUNTIME_TREE_SCHEMA = "sha256-root-mode-path-mode-bytes-v1"
SECURE_PYTHON_TEST_RUNTIME_ROOT_MODES = {0o555}
PYTHON_TEST_RUNTIME_DISTRIBUTION = "python-build-standalone"
BACKEND_SNAPSHOT_TREE_SCHEMA = "sha256-backend-path-mode-bytes-v1"
PYTHON_RUNTIME_AUTHORITY_PROBE = (
    "import importlib,json,os,sys,sysconfig;"
    "mods=['pytest','pytest_asyncio','aiohttp','httpx','cryptography','numpy'];"
    "print(json.dumps({'executable':os.path.realpath(sys.executable),"
    "'prefix':os.path.realpath(sys.prefix),'basePrefix':os.path.realpath(sys.base_prefix),"
    "'execPrefix':os.path.realpath(sys.exec_prefix),"
    "'baseExecPrefix':os.path.realpath(sys.base_exec_prefix),"
    "'stdlib':os.path.realpath(sysconfig.get_path('stdlib')),'path':[os.path.realpath(p) for p in sys.path],"
    "'modules':[os.path.realpath(importlib.import_module(m).__file__) for m in mods]},sort_keys=True))"
)
TRUSTED_OTOOL_EXECUTABLE = Path("/usr/bin/otool")
TRUSTED_SANDBOX_EXECUTABLE = Path("/usr/bin/sandbox-exec")
PYTHON_RUNTIME_PROBE_SANDBOX_PROFILE = """(version 1)
(allow default)
(deny file-write*)
"""
FIRMWARE_MANIFEST_KEYS = {
    "status", "profile", "board", "target", "sourceCommit", "createdAt", "app", "elf",
    "partition", "reproducibility", "toolchain", "config", "tests", "safety",
}


@dataclass(frozen=True)
class BoundedCommandResult:
    returncode: int | None
    stdout: str
    error: str | None


def run_bounded_command(
    command: list[str], *, cwd: Path, timeout_sec: float, max_output_bytes: int,
    env: dict[str, str] | None = None,
) -> BoundedCommandResult:
    if not isinstance(timeout_sec, (int, float)) or not math.isfinite(timeout_sec) or timeout_sec <= 0:
        return BoundedCommandResult(None, "", "invalid_timeout")
    try:
        process = subprocess.Popen(
            command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError:
        return BoundedCommandResult(None, "", "not_found")
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in buffers:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_sec
    error = None
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                error = "timeout"
                break
            for key, _ in selector.select(min(0.1, max(0.0, deadline - time.monotonic()))):
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
        if error:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        returncode = process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        returncode = process.returncode
        error = error or "run"
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    stdout = bytes(buffers[process.stdout]).decode("utf-8", errors="replace")
    return BoundedCommandResult(returncode, stdout, error)


def _git(root: Path, *arguments: str) -> str:
    result = run_bounded_command(
        [
            str(TRUSTED_GIT_EXECUTABLE), "--no-optional-locks",
            "-c", "core.fsmonitor=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "credential.helper=",
            "-c", "diff.external=",
            "-c", "core.pager=cat",
            "-c", "core.quotepath=false",
            *arguments,
        ],
        cwd=root, env=SECURE_ENV, timeout_sec=GIT_TIMEOUT_SEC,
        max_output_bytes=MAX_GIT_OUTPUT_BYTES,
    )
    if result.error or result.returncode != 0:
        raise RuntimeError("git command failed")
    return result.stdout


def _dirty_paths(root: Path) -> list[str]:
    fields = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields) and fields[index]:
        row = fields[index]
        paths.append(row[3:])
        if row[:2][0] in {"R", "C"}:
            index += 1
        index += 1
    return sorted(set(paths))


def strict_json_loads(raw: bytes | str) -> Any:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite JSON number")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite JSON number")
        return parsed

    return json.loads(
        text, object_pairs_hook=reject_duplicates, parse_constant=reject_constant,
        parse_float=parse_finite_float,
    )


def _open_directory_secure(path: Path) -> int:
    if not path.is_absolute():
        raise OSError("absolute path required")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    current = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except Exception:
        os.close(current)
        raise


def read_secure_regular(path: Path, max_bytes: int) -> bytes:
    absolute = path if path.is_absolute() else Path.cwd() / path
    parent_fd = _open_directory_secure(absolute.parent)
    file_fd: int | None = None
    try:
        file_fd = os.open(
            absolute.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise OSError("invalid bounded input")
        chunks = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise OSError("bounded input too large")
            chunks.append(chunk)
        after = os.fstat(file_fd)
        current = os.stat(absolute.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise OSError("bounded input changed")
        if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
            raise OSError("bounded input changed")
        if total != before.st_size:
            raise OSError("bounded input changed")
        return b"".join(chunks)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def _secure_hash_relative(root: Path, relative: str) -> tuple[str | None, str | None]:
    if not _valid_relative_path(relative):
        return None, "path"
    try:
        directory_fd = _open_directory_secure(root)
    except OSError:
        return None, "path"
    file_fd: int | None = None
    try:
        parts = Path(relative).parts
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(
            parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            return None, "path"
        if before.st_size > MAX_DIRTY_FILE_BYTES:
            return None, "size"
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(file_fd, min(1024 * 1024, MAX_DIRTY_FILE_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DIRTY_FILE_BYTES:
                return None, "size"
            digest.update(chunk)
        after = os.fstat(file_fd)
        current = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            return None, "changed"
        if identity != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns):
            return None, "changed"
        if total != before.st_size:
            return None, "changed"
        return digest.hexdigest(), None
    except OSError:
        return None, "path"
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(directory_fd)


def secure_executable_descriptor(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not path.is_absolute() or str(path) != str(path.resolve(strict=True)):
            return None, "path"
        parent_fd = _open_directory_secure(path.parent)
    except OSError:
        return None, "path"
    file_fd: int | None = None
    try:
        file_fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size <= 0 or before.st_size > MAX_BROWSER_EXECUTABLE_BYTES
            or before.st_mode & 0o111 == 0
        ):
            return None, "path"
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                return None, "changed"
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            return None, "changed"
        after = os.fstat(file_fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns,
        )
        if (
            identity != (
                after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            )
            or identity != (
                current.st_dev, current.st_ino, current.st_mode, current.st_nlink,
                current.st_size, current.st_mtime_ns, current.st_ctime_ns,
            )
        ):
            return None, "changed"
        return {"path": str(path), "sha256": digest.hexdigest(), "bytes": before.st_size}, None
    except OSError:
        return None, "path"
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def secure_regular_descriptor(
    path: Path, max_bytes: int, *, include_content: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        if not path.is_absolute() or str(path) != str(path.resolve(strict=True)):
            return None, "path"
        parent_fd = _open_directory_secure(path.parent)
    except OSError:
        return None, "path"
    file_fd: int | None = None
    try:
        file_fd = os.open(path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        before = os.fstat(file_fd)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size < 0 or before.st_size > max_bytes
        ):
            return None, "path"
        digest = hashlib.sha256()
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_fd, min(1024 * 1024, remaining))
            if not chunk:
                return None, "changed"
            digest.update(chunk)
            if include_content:
                chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(file_fd, 1):
            return None, "changed"
        after = os.fstat(file_fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = (
            before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
            before.st_size, before.st_mtime_ns, before.st_ctime_ns,
        )
        if identity != (
            after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
            after.st_size, after.st_mtime_ns, after.st_ctime_ns,
        ) or identity != (
            current.st_dev, current.st_ino, current.st_mode, current.st_nlink,
            current.st_size, current.st_mtime_ns, current.st_ctime_ns,
        ):
            return None, "changed"
        descriptor: dict[str, Any] = {"sha256": digest.hexdigest(), "bytes": before.st_size}
        if include_content:
            descriptor["content"] = b"".join(chunks)
        return descriptor, None
    except OSError:
        return None, "path"
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def _docker_image_descriptor(reference: str) -> dict[str, Any] | None:
    if (
        not isinstance(reference, str) or not reference or len(reference) > 512
        or any(character.isspace() or ord(character) < 32 for character in reference)
    ):
        return None
    result = run_bounded_command(
        [str(TRUSTED_DOCKER_EXECUTABLE), "image", "inspect", "--format", "{{json .}}", reference],
        cwd=Path("/"), env=SECURE_ENV, timeout_sec=10.0, max_output_bytes=1024 * 1024,
    )
    if result.error or result.returncode != 0:
        return None
    try:
        observed = strict_json_loads(result.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(observed, dict):
        return None
    return observed


def _digest_field(digest: Any, value: bytes) -> None:
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def _tree_metadata_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _secure_browser_bundle_descriptor_fd(
    root_fd: int, root_metadata: os.stat_result, *, require_read_only: bool = False,
    allow_safe_symlinks: bool = False, max_entries: int = 10_000,
    max_bytes: int = MAX_BROWSER_EXECUTABLE_BYTES,
    excluded_root_directories: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any] | None, str | None]:
    digest = hashlib.sha256()
    state = {"entryCount": 0, "totalBytes": 0}

    def visit(directory_fd: int, relative_parent: Path, depth: int) -> bool:
        if depth > MAX_BROWSER_BUNDLE_DEPTH:
            return False
        for name in sorted(entry.name for entry in os.scandir(directory_fd)):
            relative = relative_parent / name
            metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            state["entryCount"] += 1
            if state["entryCount"] > max_entries:
                return False
            mode = stat.S_IMODE(metadata.st_mode)
            if depth == 0 and name in excluded_root_directories:
                if not stat.S_ISDIR(metadata.st_mode) or (require_read_only and mode & 0o222):
                    return False
                _digest_field(digest, b"excluded-directory")
                _digest_field(digest, relative.as_posix().encode())
                _digest_field(digest, str(mode).encode())
                continue
            if require_read_only and not stat.S_ISLNK(metadata.st_mode) and mode & 0o222:
                return False
            if stat.S_ISDIR(metadata.st_mode):
                child_fd = os.open(
                    name, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=directory_fd,
                )
                try:
                    if _tree_metadata_identity(os.fstat(child_fd)) != _tree_metadata_identity(metadata):
                        return False
                    _digest_field(digest, b"directory")
                    _digest_field(digest, relative.as_posix().encode())
                    _digest_field(digest, str(mode).encode())
                    if not visit(child_fd, relative, depth + 1):
                        return False
                    if (
                        _tree_metadata_identity(os.fstat(child_fd))
                        != _tree_metadata_identity(metadata)
                        or _tree_metadata_identity(
                            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        ) != _tree_metadata_identity(metadata)
                    ):
                        return False
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                state["totalBytes"] += metadata.st_size
                if state["totalBytes"] > max_bytes:
                    return False
                file_fd = os.open(
                    name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd,
                )
                try:
                    identity = _tree_metadata_identity(metadata)
                    if _tree_metadata_identity(os.fstat(file_fd)) != identity:
                        return False
                    _digest_field(digest, b"regular")
                    _digest_field(digest, relative.as_posix().encode())
                    _digest_field(digest, str(mode).encode())
                    _digest_field(digest, str(metadata.st_size).encode())
                    remaining = metadata.st_size
                    while remaining:
                        chunk = os.read(file_fd, min(1024 * 1024, remaining))
                        if not chunk:
                            return False
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if (
                        _tree_metadata_identity(os.fstat(file_fd)) != identity
                        or _tree_metadata_identity(
                            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        ) != identity
                    ):
                        return False
                finally:
                    os.close(file_fd)
            elif allow_safe_symlinks and stat.S_ISLNK(metadata.st_mode):
                try:
                    target = os.readlink(name, dir_fd=directory_fd)
                    normalized = posixpath.normpath(posixpath.join(relative_parent.as_posix(), target))
                    if (
                        not target or "\0" in target or posixpath.isabs(target)
                        or normalized == ".." or normalized.startswith("../")
                    ):
                        return False
                    target_bytes = os.fsencode(target)
                    state["totalBytes"] += len(target_bytes)
                    if state["totalBytes"] > max_bytes:
                        return False
                    if (
                        os.readlink(name, dir_fd=directory_fd) != target
                        or _tree_metadata_identity(
                            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        ) != _tree_metadata_identity(metadata)
                    ):
                        return False
                    _digest_field(digest, b"symlink")
                    _digest_field(digest, relative.as_posix().encode())
                    _digest_field(digest, str(mode).encode())
                    _digest_field(digest, target_bytes)
                except (OSError, UnicodeEncodeError):
                    return False
            else:
                return False
        return True

    if not visit(root_fd, Path(), 0):
        return None, "tree"
    if _tree_metadata_identity(os.fstat(root_fd)) != _tree_metadata_identity(root_metadata):
        return None, "changed"
    return {
        "schema": BROWSER_TREE_SCHEMA, "sha256": digest.hexdigest(),
        "entryCount": state["entryCount"], "totalBytes": state["totalBytes"],
    }, None


def secure_browser_bundle_descriptor(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    root_fd = None
    try:
        if not root.is_absolute() or str(root) != str(root.resolve(strict=True)) or root.is_symlink():
            return None, "path"
        root_before = root.lstat()
        if not stat.S_ISDIR(root_before.st_mode):
            return None, "path"
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        if _tree_metadata_identity(os.fstat(root_fd)) != _tree_metadata_identity(root_before):
            return None, "changed"
        descriptor, error = _secure_browser_bundle_descriptor_fd(root_fd, root_before)
        if error is not None:
            return descriptor, error
        if _tree_metadata_identity(root.lstat()) != _tree_metadata_identity(root_before):
            return None, "changed"
        return descriptor, None
    except RecursionError:
        return None, "tree"
    except (OSError, UnicodeEncodeError):
        return None, "path"
    finally:
        if root_fd is not None:
            os.close(root_fd)


def secure_backend_snapshot_tree_descriptor(
    root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    root_fd = None
    try:
        if not root.is_absolute() or str(root) != str(root.resolve(strict=True)) or root.is_symlink():
            return None, "path"
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o222:
            return None, "path"
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        if _tree_metadata_identity(os.fstat(root_fd)) != _tree_metadata_identity(metadata):
            return None, "changed"
        descriptor, error = _secure_browser_bundle_descriptor_fd(
            root_fd, metadata, require_read_only=True,
            excluded_root_directories=frozenset({"node_modules"}),
        )
        if error or descriptor is None:
            return None, error or "tree"
        if _tree_metadata_identity(root.lstat()) != _tree_metadata_identity(metadata):
            return None, "changed"
        digest = hashlib.sha256()
        _digest_field(digest, BACKEND_SNAPSHOT_TREE_SCHEMA.encode("ascii"))
        _digest_field(digest, descriptor["sha256"].encode("ascii"))
        return {**descriptor, "schema": BACKEND_SNAPSHOT_TREE_SCHEMA, "sha256": digest.hexdigest()}, None
    except (OSError, UnicodeEncodeError):
        return None, "path"
    finally:
        if root_fd is not None:
            os.close(root_fd)


def secure_backend_execution_tree_descriptor(
    root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    root_fd = None
    try:
        if not root.is_absolute() or str(root) != str(root.resolve(strict=True)) or root.is_symlink():
            return None, "path"
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o222:
            return None, "path"
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        if _tree_metadata_identity(os.fstat(root_fd)) != _tree_metadata_identity(metadata):
            return None, "changed"
        descriptor, error = _secure_browser_bundle_descriptor_fd(
            root_fd, metadata, require_read_only=True, allow_safe_symlinks=True,
            max_entries=MAX_BACKEND_SNAPSHOT_ENTRIES, max_bytes=MAX_BACKEND_SNAPSHOT_BYTES,
        )
        if error or descriptor is None:
            return None, error or "tree"
        if _tree_metadata_identity(root.lstat()) != _tree_metadata_identity(metadata):
            return None, "changed"
        return descriptor, None
    except (OSError, UnicodeEncodeError):
        return None, "path"
    finally:
        if root_fd is not None:
            os.close(root_fd)


def secure_node_package_tree_descriptor(root: Path) -> dict[str, Any] | None:
    root_fd = None
    try:
        metadata = root.lstat()
    except (OSError, UnicodeEncodeError):
        return None
    root_mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode) or root.is_symlink()
        or root_mode not in SECURE_NODE_PACKAGE_ROOT_MODES
    ):
        return None
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        if _tree_metadata_identity(os.fstat(root_fd)) != _tree_metadata_identity(metadata):
            return None
        descriptor, error = _secure_browser_bundle_descriptor_fd(root_fd, metadata)
        if error is not None or descriptor is None:
            return None
        final_metadata = root.lstat()
    except (OSError, UnicodeEncodeError):
        return None
    finally:
        if root_fd is not None:
            os.close(root_fd)
    if _tree_metadata_identity(final_metadata) != _tree_metadata_identity(metadata):
        return None
    digest = hashlib.sha256()
    _digest_field(digest, NODE_PACKAGE_TREE_SCHEMA.encode("ascii"))
    _digest_field(digest, str(root_mode).encode("ascii"))
    _digest_field(digest, descriptor["sha256"].encode("ascii"))
    return {
        **descriptor,
        "schema": NODE_PACKAGE_TREE_SCHEMA,
        "sha256": digest.hexdigest(),
        "rootMode": root_mode,
    }


def secure_python_test_runtime_tree_descriptor(
    root: Path,
) -> tuple[dict[str, Any] | None, str | None]:
    root_fd = None
    try:
        if not root.is_absolute() or str(root) != str(root.resolve(strict=True)) or root.is_symlink():
            return None, "path"
        metadata = root.lstat()
        root_mode = stat.S_IMODE(metadata.st_mode)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or root_mode not in SECURE_PYTHON_TEST_RUNTIME_ROOT_MODES
        ):
            return None, "path"
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        if _tree_metadata_identity(os.fstat(root_fd)) != _tree_metadata_identity(metadata):
            return None, "changed"
        descriptor, error = _secure_browser_bundle_descriptor_fd(
            root_fd, metadata, require_read_only=True,
        )
        if error is not None or descriptor is None:
            return None, error or "tree"
        if _tree_metadata_identity(root.lstat()) != _tree_metadata_identity(metadata):
            return None, "changed"
        digest = hashlib.sha256()
        _digest_field(digest, PYTHON_TEST_RUNTIME_TREE_SCHEMA.encode("ascii"))
        _digest_field(digest, str(root_mode).encode("ascii"))
        _digest_field(digest, descriptor["sha256"].encode("ascii"))
        return {
            **descriptor,
            "schema": PYTHON_TEST_RUNTIME_TREE_SCHEMA,
            "sha256": digest.hexdigest(),
            "rootMode": root_mode,
        }, None
    except RecursionError:
        return None, "tree"
    except (OSError, UnicodeEncodeError):
        return None, "path"
    finally:
        if root_fd is not None:
            os.close(root_fd)


def _validate_python_test_runtime(
    value: Any, reasons: set[str], *, verify_identity: bool,
) -> None:
    prefix = "tools.pythonTestRuntime"
    if not isinstance(value, dict) or set(value) != PYTHON_TEST_RUNTIME_KEYS:
        reasons.add(f"{prefix}.keys")
        return
    if type(value.get("version")) is not int or value["version"] != 1:
        reasons.add(f"{prefix}.version")
    if value.get("distribution") != PYTHON_TEST_RUNTIME_DISTRIBUTION:
        reasons.add(f"{prefix}.distribution")
    root = value.get("root")
    executable = value.get("executable")
    python_version = value.get("pythonVersion")
    pytest_version = value.get("pytestVersion")
    tree = value.get("treeDigest")
    if not isinstance(root, str) or not Path(root).is_absolute():
        reasons.add(f"{prefix}.root")
    if not _valid_relative_path(executable):
        reasons.add(f"{prefix}.executable")
    elif executable != "bin/python3.11":
        reasons.add(f"{prefix}.executable")
    if not isinstance(python_version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", python_version) is None:
        reasons.add(f"{prefix}.pythonVersion")
    if not isinstance(pytest_version, str) or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", pytest_version) is None:
        reasons.add(f"{prefix}.pytestVersion")
    if not isinstance(tree, dict) or set(tree) != PYTHON_TEST_RUNTIME_TREE_KEYS:
        reasons.add(f"{prefix}.treeDigest.keys")
    elif (
        tree.get("schema") != PYTHON_TEST_RUNTIME_TREE_SCHEMA
        or not isinstance(tree.get("sha256"), str) or SHA256_RE.fullmatch(tree["sha256"]) is None
        or type(tree.get("entryCount")) is not int or tree["entryCount"] <= 0
        or type(tree.get("totalBytes")) is not int or tree["totalBytes"] <= 0
        or type(tree.get("rootMode")) is not int
        or tree["rootMode"] not in SECURE_PYTHON_TEST_RUNTIME_ROOT_MODES
    ):
        reasons.add(f"{prefix}.treeDigest")
    if not verify_identity or any(reason.startswith(f"{prefix}.") for reason in reasons):
        return
    observed, error = secure_python_test_runtime_tree_descriptor(Path(root))
    executable_path = Path(root) / executable
    if (
        error or observed != tree or not executable_path.is_file()
        or executable_path.is_symlink() or not os.access(executable_path, os.X_OK)
    ):
        reasons.add(f"{prefix}.identity")
        return
    version_env = {
        **SECURE_ENV, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
    }
    commands = (
        ([str(executable_path), "-I", "-s", "-c", "import platform; print(platform.python_version())"], python_version),
        ([str(executable_path), "-I", "-s", "-m", "pytest", "-s", "--version"], f"pytest {pytest_version}"),
        ([str(executable_path), "-I", "-s", "-c", "import pytest, pytest_asyncio"], ""),
    )
    for command, expected in commands:
        command = _sandboxed_python_runtime_probe_command(command)
        if command is None:
            reasons.add(f"{prefix}.identity")
            return
        result = run_bounded_command(
            command, cwd=Path("/"), env=version_env, timeout_sec=10.0, max_output_bytes=4096,
        )
        if result.error or result.returncode != 0 or result.stdout.strip() != expected:
            reasons.add(f"{prefix}.identity")
            return
    authority_command = _sandboxed_python_runtime_probe_command([
        str(executable_path), "-I", "-s", "-c", PYTHON_RUNTIME_AUTHORITY_PROBE,
    ])
    if authority_command is None:
        reasons.add(f"{prefix}.identity")
        return
    authority = run_bounded_command(
        authority_command,
        cwd=Path("/"), env=version_env, timeout_sec=15.0, max_output_bytes=64 * 1024,
    )
    try:
        payload = strict_json_loads(authority.stdout)
        root_path = Path(root)
        if (
            authority.error or authority.returncode != 0
            or not _python_runtime_authority_payload_valid(root_path, payload)
            or (root_path / "pyvenv.cfg").exists()
            or not _python_runtime_library_authority(root_path, executable_path)
        ):
            raise ValueError("invalid authority")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        reasons.add(f"{prefix}.authority")
        return
    final_observed, final_error = secure_python_test_runtime_tree_descriptor(Path(root))
    if final_error or final_observed != tree:
        reasons.add(f"{prefix}.identity")


def python_test_runtime_authorized(value: Any) -> bool:
    reasons: set[str] = set()
    _validate_python_test_runtime(value, reasons, verify_identity=True)
    return not reasons


def _sandboxed_python_runtime_probe_command(command: list[str]) -> list[str] | None:
    if sys.platform != "darwin":
        return None
    executable = _trusted_sandbox_executable()
    if executable is None:
        return None
    return [str(executable), "-p", PYTHON_RUNTIME_PROBE_SANDBOX_PROFILE, *command]


def _trusted_sandbox_executable() -> Path | None:
    try:
        metadata = TRUSTED_SANDBOX_EXECUTABLE.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not os.access(TRUSTED_SANDBOX_EXECUTABLE, os.X_OK)
        ):
            return None
    except OSError:
        return None
    return TRUSTED_SANDBOX_EXECUTABLE


def _path_is_within(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _python_runtime_authority_payload_valid(root: Path, payload: Any) -> bool:
    if not isinstance(payload, dict) or set(payload) != {
        "executable", "prefix", "basePrefix", "execPrefix", "baseExecPrefix",
        "stdlib", "path", "modules",
    }:
        return False
    if not isinstance(payload.get("path"), list) or not isinstance(payload.get("modules"), list):
        return False
    paths = [
        payload["executable"], payload["prefix"], payload["basePrefix"],
        payload["execPrefix"], payload["baseExecPrefix"], payload["stdlib"],
        *payload["path"], *payload["modules"],
    ]
    return (
        all(isinstance(payload[name], str) and Path(payload[name]) == root for name in (
            "prefix", "basePrefix", "execPrefix", "baseExecPrefix",
        ))
        and all(
            isinstance(path, str) and Path(path).is_absolute()
            and _path_is_within(root, Path(path))
            for path in paths
        )
    )


def _python_runtime_library_authority(root: Path, executable: Path) -> bool:
    if sys.platform != "darwin":
        return True
    macho_magics = {
        b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
    }
    macho_files: list[Path] = []
    try:
        for directory, _names, files in os.walk(root):
            for name in files:
                path = Path(directory) / name
                with path.open("rb") as stream:
                    if stream.read(4) in macho_magics:
                        macho_files.append(path)
        if executable not in macho_files or not macho_files:
            return False
        dependencies_result = run_bounded_command(
            [str(TRUSTED_OTOOL_EXECUTABLE), "-L", *(str(path) for path in macho_files)],
            cwd=Path("/"), env=SECURE_ENV, timeout_sec=30.0, max_output_bytes=16 * 1024 * 1024,
        )
        load_result = run_bounded_command(
            [str(TRUSTED_OTOOL_EXECUTABLE), "-l", *(str(path) for path in macho_files)],
            cwd=Path("/"), env=SECURE_ENV, timeout_sec=30.0, max_output_bytes=64 * 1024 * 1024,
        )
        if (
            dependencies_result.error or dependencies_result.returncode != 0
            or load_result.error or load_result.returncode != 0
        ):
            return False
        dependencies_by_path = _split_otool_output(dependencies_result.stdout, macho_files)
        loads_by_path = _split_otool_output(load_result.stdout, macho_files)
        if dependencies_by_path is None or loads_by_path is None:
            return False
        for path in macho_files:
            load_authority = _macho_load_authority(root, path, loads_by_path[path])
            if load_authority is None:
                return False
            rpaths, dylib_id = load_authority
            for line in dependencies_by_path[path].splitlines():
                dependency = line.strip().split(" (", 1)[0]
                if not dependency:
                    continue
                if dependency == dylib_id:
                    continue
                if dependency.startswith(("/usr/lib/", "/System/Library/")):
                    continue
                if dependency.startswith("@rpath/"):
                    suffix = dependency.removeprefix("@rpath/")
                    if not any(_internal_existing_path(root, base / suffix) for base in rpaths):
                        return False
                    continue
                if dependency.startswith("@loader_path/"):
                    resolved = path.parent / dependency.removeprefix("@loader_path/")
                elif dependency.startswith("@executable_path/"):
                    resolved = root / "bin" / dependency.removeprefix("@executable_path/")
                else:
                    return False
                if not _internal_existing_path(root, resolved):
                    return False
        return True
    except (OSError, UnicodeError):
        return False


def _split_otool_output(output: str, paths: list[Path]) -> dict[Path, str] | None:
    expected = {str(path): path for path in paths}
    result: dict[Path, list[str]] = {}
    current: Path | None = None
    for line in output.splitlines():
        header = line[:-1] if line.endswith(":") else None
        matched = next(
            (path for text, path in expected.items() if header == text or (
                isinstance(header, str) and header.startswith(f"{text} (architecture ")
            )),
            None,
        )
        if matched is not None:
            current = matched
            result.setdefault(current, [])
        elif current is not None:
            result[current].append(line)
    if set(result) != set(paths):
        return None
    return {path: "\n".join(lines) for path, lines in result.items()}


def _internal_existing_path(root: Path, path: Path) -> bool:
    try:
        return _path_is_within(root, path.resolve(strict=True))
    except OSError:
        return False


def _macho_load_authority(
    root: Path, binary: Path, output: str,
) -> tuple[list[Path], str | None] | None:
    values: list[Path] = []
    command: str | None = None
    dylib_id: str | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if line == "cmd LC_RPATH":
            command = "rpath"
            continue
        if line == "cmd LC_ID_DYLIB":
            command = "id"
            continue
        if command == "rpath" and line.startswith("path "):
            token = line.removeprefix("path ").split(" (offset ", 1)[0]
            command = None
            if token == "@loader_path":
                value = binary.parent
            elif token.startswith("@loader_path/"):
                value = binary.parent / token.removeprefix("@loader_path/")
            elif token == "@executable_path":
                value = root / "bin"
            elif token.startswith("@executable_path/"):
                value = root / "bin" / token.removeprefix("@executable_path/")
            else:
                return None
            try:
                resolved = value.resolve(strict=True)
            except OSError:
                return None
            if not _path_is_within(root, resolved):
                return None
            values.append(resolved)
        elif command == "id" and line.startswith("name "):
            dylib_id = line.removeprefix("name ").split(" (offset ", 1)[0]
            command = None
    return values, dylib_id


def _validate_robot_preview_browser(value: Any, reasons: set[str], *, verify_identity: bool) -> None:
    prefix = "tools.robotPreviewBrowser"
    if not isinstance(value, dict) or set(value) != BROWSER_DESCRIPTOR_KEYS:
        reasons.add(f"{prefix}.keys")
        return
    if type(value.get("version")) is not int or value["version"] != 2:
        reasons.add(f"{prefix}.version")
    if value.get("engine") != "chromium-headless-shell":
        reasons.add(f"{prefix}.engine")
    revision = value.get("revision")
    if not isinstance(revision, str) or re.fullmatch(r"[1-9][0-9]*", revision) is None:
        reasons.add(f"{prefix}.revision")
    root = value.get("root")
    executable = value.get("executable")
    tree = value.get("treeDigest")
    if not isinstance(root, str) or not Path(root).is_absolute():
        reasons.add(f"{prefix}.root")
    if not _valid_relative_path(executable):
        reasons.add(f"{prefix}.executable")
    if not isinstance(tree, dict) or set(tree) != BROWSER_TREE_KEYS:
        reasons.add(f"{prefix}.treeDigest.keys")
    elif (
        tree.get("schema") != BROWSER_TREE_SCHEMA
        or not isinstance(tree.get("sha256"), str) or SHA256_RE.fullmatch(tree["sha256"]) is None
        or type(tree.get("entryCount")) is not int or tree["entryCount"] <= 0
        or type(tree.get("totalBytes")) is not int or tree["totalBytes"] <= 0
    ):
        reasons.add(f"{prefix}.treeDigest")
    if verify_identity and not any(reason.startswith(f"{prefix}.") for reason in reasons):
        observed, error = secure_browser_bundle_descriptor(Path(root))
        executable_path = Path(root) / executable
        if (
            error or observed != tree or not executable_path.is_file()
            or executable_path.is_symlink() or not os.access(executable_path, os.X_OK)
        ):
            reasons.add(f"{prefix}.identity")


def _valid_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\0" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and path.as_posix() == value and ".." not in path.parts


def _validate_repository(name: str, value: Any, reasons: set[str]) -> Path | None:
    prefix = f"repositories.{name}"
    if not isinstance(value, dict) or set(value) != REPOSITORY_KEYS:
        reasons.add(f"{prefix}.keys")
        return None
    path_value = value.get("path")
    if not isinstance(path_value, str) or not Path(path_value).is_absolute():
        reasons.add(f"{prefix}.path")
        return None
    try:
        root = Path(path_value).resolve(strict=True)
    except OSError:
        reasons.add(f"{prefix}.path")
        return None
    if not root.is_dir() or str(root) != path_value:
        reasons.add(f"{prefix}.path")
        return None
    try:
        actual_sha = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
        actual_branch = _git(root, "branch", "--show-current").strip()
        actual_remote = _git(root, "remote", "get-url", "origin").strip()
        dirty_paths = _dirty_paths(root)
    except RuntimeError:
        reasons.add(f"{prefix}.git")
        return None
    sha = value.get("sha")
    if not isinstance(sha, str) or SHA_RE.fullmatch(sha) is None or sha != actual_sha:
        reasons.add(f"{prefix}.sha")
    branch = value.get("branch")
    if not isinstance(branch, str) or not branch or branch != actual_branch:
        reasons.add(f"{prefix}.branch")
    remote = value.get("remoteUrl")
    if not isinstance(remote, str) or not remote or remote != actual_remote:
        reasons.add(f"{prefix}.remoteUrl")

    exceptions = value.get("dirtyExceptions")
    if not isinstance(exceptions, list):
        reasons.add(f"{prefix}.dirtyExceptions")
        return root
    exception_paths: list[str] = []
    hashes_valid = True
    for exception in exceptions:
        if not isinstance(exception, dict) or set(exception) != DIRTY_EXCEPTION_KEYS:
            reasons.add(f"{prefix}.dirtyExceptions")
            continue
        relative = exception.get("path")
        digest = exception.get("sha256")
        if not _valid_relative_path(relative):
            reasons.add(f"{prefix}.dirtyExceptions.path")
            continue
        exception_paths.append(relative)
        actual_digest, read_error = _secure_hash_relative(root, relative)
        if read_error == "path":
            reasons.add(f"{prefix}.dirtyExceptions.path")
        elif read_error == "size":
            reasons.add(f"{prefix}.dirtyExceptions.size")
        elif read_error == "changed":
            reasons.add(f"{prefix}.dirtyExceptions.changed")
        elif (
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
            or actual_digest != digest
        ):
            hashes_valid = False
    if len(exception_paths) != len(set(exception_paths)):
        reasons.add(f"{prefix}.dirtyExceptions.path")
    if not hashes_valid:
        reasons.add(f"{prefix}.dirtyExceptions.hash")
    if dirty_paths != sorted(set(exception_paths)):
        reasons.add(f"{prefix}.dirty")
    return root


def _repository_matches_candidate(root: Path, value: dict[str, Any]) -> bool:
    try:
        if (
            _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip() != value["sha"]
            or _git(root, "branch", "--show-current").strip() != value["branch"]
            or _git(root, "remote", "get-url", "origin").strip() != value["remoteUrl"]
            or _dirty_paths(root) != sorted(item["path"] for item in value["dirtyExceptions"])
        ):
            return False
        return all(
            _secure_hash_relative(root, item["path"]) == (item["sha256"], None)
            for item in value["dirtyExceptions"]
        )
    except (KeyError, RuntimeError, TypeError):
        return False


def _parse_rfc3339_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or RFC3339_UTC_RE.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return None


def _validate_images(
    value: Any, repositories: dict[str, Any], reasons: set[str], *, verify_identity: bool,
    verify_provenance: bool,
) -> None:
    if not isinstance(value, dict) or set(value) != IMAGE_KEYS:
        reasons.add("images.keys")
        value = value if isinstance(value, dict) else {}
    for name in sorted(IMAGE_KEYS):
        descriptor = value.get(name)
        prefix = f"images.{name}"
        if not isinstance(descriptor, dict) or set(descriptor) != IMAGE_DESCRIPTOR_KEYS:
            reasons.add(f"{prefix}.keys")
            continue
        reference = descriptor.get("reference")
        image_id = descriptor.get("id")
        repository_name = "backend" if name == "lessonStudioBackend" else "adminEsp"
        repository = repositories.get(repository_name) if isinstance(repositories, dict) else None
        expected_prefix = "local/tbot-backend" if name == "lessonStudioBackend" else "local/tbot-server-web"
        expected_reference = (
            f"{expected_prefix}:course-mode-physical-tft-{repository.get('sha')}"
            if isinstance(repository, dict) else None
        )
        if not isinstance(reference, str) or not reference:
            reasons.add(f"{prefix}.reference")
        elif verify_provenance and reference != expected_reference:
            reasons.add(f"{prefix}.reference")
        if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
            reasons.add(f"{prefix}.id")
        elif verify_identity and verify_provenance:
            observed = _docker_image_descriptor(reference)
            labels = (
                observed.get("Config", {}).get("Labels")
                if isinstance(observed, dict) and isinstance(observed.get("Config"), dict) else None
            )
            if not isinstance(observed, dict) or observed.get("Id") != image_id:
                reasons.add(f"{prefix}.id")
            if (
                not isinstance(labels, dict) or not isinstance(repository, dict)
                or labels.get("org.opencontainers.image.revision") != repository.get("sha")
                or labels.get("org.opencontainers.image.source") != repository.get("remoteUrl")
            ):
                reasons.add(f"{prefix}.provenance")


def _firmware_manifest_schema_valid(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != FIRMWARE_MANIFEST_KEYS:
        return False
    exact = {
        "app": {"file", "offset", "bytes", "sha256"},
        "elf": {"file", "bytes", "sha256"},
        "partition": {"bytes", "freeBytes", "freePercent"},
        "reproducibility": {"appByteIdentical", "elfByteIdentical", "independentCleanBuilds", "ccacheEnabled"},
        "toolchain": {"espIdf", "espIdfCommit", "python", "compiler", "cmake", "ninja"},
        "config": {"sdkconfigSha256", "sdkconfigDefaultsLocalSha256", "dependenciesLockSha256",
                   "appReproducibleBuild", "productionConfigAudit", "productionArtifactAudit"},
        "tests": {"projectSourceGate", "firmwareVersionAndCourseGates"},
        "safety": {"flashed", "serialAccessed", "hilRun", "physicalDeviceAccessed"},
    }
    return all(isinstance(value.get(name), dict) and set(value[name]) == keys for name, keys in exact.items())


def _validate_firmware(
    value: Any, repositories: dict[str, Any], tools: dict[str, Any], reasons: set[str],
    *, verify_identity: bool, candidate_created: datetime | None, candidate_expires: datetime | None,
) -> None:
    if not isinstance(value, dict) or set(value) != FIRMWARE_KEYS:
        reasons.add("firmware.keys")
        return
    for field in ("appSha256", "elfSha256", "evidenceManifestSha256"):
        if not isinstance(value.get(field), str) or SHA256_RE.fullmatch(value[field]) is None:
            reasons.add(f"firmware.{field}")
    for field in ("appBytes", "partitionBytes", "freeBytes"):
        if type(value.get(field)) is not int or value[field] < 0:
            reasons.add(f"firmware.{field}")
    if value.get("appOffset") != "0x20000":
        reasons.add("firmware.appOffset")
    if (
        type(value.get("appBytes")) is int and type(value.get("partitionBytes")) is int
        and type(value.get("freeBytes")) is int
        and value["partitionBytes"] - value["appBytes"] != value["freeBytes"]
    ):
        reasons.add("firmware.freeBytes")
    for field in ("appPath", "evidenceManifestPath"):
        if not isinstance(value.get(field), str) or not Path(value[field]).is_absolute():
            reasons.add(f"firmware.{field}")
    if not verify_identity or any(reason.startswith("firmware.") for reason in reasons):
        return
    manifest_descriptor, manifest_error = secure_regular_descriptor(
        Path(value["evidenceManifestPath"]), MAX_FIRMWARE_MANIFEST_BYTES, include_content=True,
    )
    if manifest_error or manifest_descriptor is None or manifest_descriptor["sha256"] != value["evidenceManifestSha256"]:
        reasons.add("firmware.evidenceManifestSha256")
        return
    try:
        evidence = strict_json_loads(manifest_descriptor["content"])
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        reasons.add("firmware.evidenceManifestPath")
        return
    if not _firmware_manifest_schema_valid(evidence):
        reasons.add("firmware.evidenceManifestPath")
        return
    app_path = Path(value["appPath"])
    manifest_path = Path(value["evidenceManifestPath"])
    app = evidence["app"]
    elf = evidence["elf"]
    partition = evidence["partition"]
    safe_names = all(
        isinstance(item.get("file"), str) and Path(item["file"]).name == item["file"]
        for item in (app, elf)
    )
    if not safe_names or manifest_path.parent / app.get("file", "") != app_path:
        reasons.add("firmware.appPath")
        return
    app_descriptor, app_error = secure_regular_descriptor(app_path, MAX_FIRMWARE_ARTIFACT_BYTES)
    elf_descriptor, elf_error = secure_regular_descriptor(
        manifest_path.parent / elf["file"], MAX_FIRMWARE_ARTIFACT_BYTES,
    )
    if app_error or app_descriptor is None:
        reasons.add("firmware.appPath")
    else:
        if app_descriptor["bytes"] != value["appBytes"] or app.get("bytes") != value["appBytes"]:
            reasons.add("firmware.appBytes")
        if app_descriptor["sha256"] != value["appSha256"] or app.get("sha256") != value["appSha256"]:
            reasons.add("firmware.appSha256")
    if elf_error or elf_descriptor is None or elf_descriptor["sha256"] != value["elfSha256"] or elf.get("sha256") != value["elfSha256"] or elf_descriptor["bytes"] != elf.get("bytes"):
        reasons.add("firmware.elfSha256")
    firmware_repository = repositories.get("firmware") if isinstance(repositories, dict) else None
    if not isinstance(firmware_repository, dict) or evidence.get("sourceCommit") != firmware_repository.get("sha"):
        reasons.add("firmware.sourceCommit")
    if app.get("offset") != value["appOffset"]:
        reasons.add("firmware.appOffset")
    if partition.get("bytes") != value["partitionBytes"]:
        reasons.add("firmware.partitionBytes")
    if partition.get("freeBytes") != value["freeBytes"]:
        reasons.add("firmware.freeBytes")
    esp_idf = tools.get("espIdf")
    toolchain = evidence["toolchain"]
    if not isinstance(esp_idf, dict) or toolchain.get("espIdf") != esp_idf.get("version"):
        reasons.add("tools.espIdf.version")
    if (
        not isinstance(toolchain.get("espIdfCommit"), str)
        or SHA_RE.fullmatch(toolchain["espIdfCommit"]) is None
        or not isinstance(esp_idf, dict)
        or toolchain["espIdfCommit"] != esp_idf.get("commit")
    ):
        reasons.add("tools.espIdf.commit")
    reproducibility = evidence["reproducibility"]
    config = evidence["config"]
    safety = evidence["safety"]
    evidence_created = _parse_rfc3339_utc(evidence.get("createdAt"))
    free_percent = partition.get("freePercent")
    expected_free_percent = (
        round(value["freeBytes"] / value["partitionBytes"] * 100, 6)
        if type(value.get("partitionBytes")) is int and value["partitionBytes"] > 0 else None
    )
    if (
        evidence.get("status") != "PASS" or evidence.get("profile") != "production"
        or evidence.get("board") != "LCDWiki ES3C35P" or evidence.get("target") != "esp32s3"
        or evidence_created is None
        or (
            candidate_created is not None and candidate_expires is not None
            and not candidate_created <= evidence_created < candidate_expires
        )
        or not isinstance(free_percent, (int, float)) or type(free_percent) is bool
        or expected_free_percent is None or abs(float(free_percent) - expected_free_percent) > 0.000001
        or reproducibility.get("appByteIdentical") is not True
        or reproducibility.get("elfByteIdentical") is not True
        or type(reproducibility.get("independentCleanBuilds")) is not int
        or reproducibility["independentCleanBuilds"] < 2
        or reproducibility.get("ccacheEnabled") is not False
        or config.get("appReproducibleBuild") is not True
        or config.get("productionConfigAudit") != "PASS"
        or config.get("productionArtifactAudit") != "PASS"
        or re.fullmatch(r"[1-9][0-9]* passed(?:, [0-9]+ skipped.*)?", evidence["tests"].get("projectSourceGate", "")) is None
        or re.fullmatch(r"[1-9][0-9]* passed", evidence["tests"].get("firmwareVersionAndCourseGates", "")) is None
        or any(safety.get(field) is not False for field in safety)
    ):
        reasons.add("firmware.evidenceManifestPath")


def _validate_database(
    value: Any, backend_root: Path | None, backend: Any, reasons: set[str], *, verify_identity: bool,
) -> None:
    if not isinstance(value, dict) or set(value) != DATABASE_KEYS:
        reasons.add("database.keys")
        return
    image = value.get("engineImage")
    image_id = value.get("engineImageId")
    head = value.get("migrationHead")
    digest = value.get("migrationHeadSha256")
    if not isinstance(image, str) or not image:
        reasons.add("database.engineImage")
    if not isinstance(image_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is None:
        reasons.add("database.engineImageId")
    if not isinstance(head, str) or Path(head).name != head or not head.endswith(".sql"):
        reasons.add("database.migrationHead")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        reasons.add("database.migrationHeadSha256")
    if not verify_identity:
        return
    if image != "postgres:16-alpine":
        reasons.add("database.engineImage")
    observed_image = _docker_image_descriptor(image) if isinstance(image, str) else None
    if isinstance(image_id, str) and (
        not isinstance(observed_image, dict) or observed_image.get("Id") != image_id
    ):
        reasons.add("database.engineImageId")
    if backend_root is None or not isinstance(head, str) or Path(head).name != head:
        reasons.add("database.migrationHead")
        return
    relative = f"src/database/migrations/{head}"
    try:
        committed_names = _git(
            backend_root, "ls-tree", "--name-only", f"{backend['sha']}:src/database/migrations",
        ).splitlines()
        runtime_names = sorted(entry.name for entry in os.scandir(backend_root / "src/database/migrations"))
    except (KeyError, OSError, RuntimeError, TypeError):
        committed_names = []
        runtime_names = []
    committed_up = sorted(
        name for name in committed_names if name.endswith(".sql") and not name.endswith(".down.sql")
    )
    runtime_up = sorted(
        name for name in runtime_names if name.endswith(".sql") and not name.endswith(".down.sql")
    )
    if not committed_up or not runtime_up or head != committed_up[-1] or head != runtime_up[-1]:
        reasons.add("database.migrationHead")
    runtime, error = secure_regular_descriptor(backend_root / relative, MAX_DIRTY_FILE_BYTES)
    try:
        committed = _git(backend_root, "show", f"{backend['sha']}:{relative}").encode("utf-8")
        committed_digest = hashlib.sha256(committed).hexdigest()
    except (KeyError, RuntimeError, TypeError, UnicodeEncodeError):
        committed_digest = None
    if error or runtime is None or runtime["sha256"] != digest or committed_digest != digest:
        reasons.add("database.migrationHeadSha256")


def _validate_node_tools(value: Any, reasons: set[str], *, verify_identity: bool) -> None:
    if not isinstance(value, dict) or set(value) != NODE_KEYS:
        reasons.add("tools.node.keys")
        return
    for key in sorted(NODE_KEYS):
        descriptor = value.get(key)
        prefix = f"tools.node.{key}"
        if not isinstance(descriptor, dict) or set(descriptor) != NODE_DESCRIPTOR_KEYS:
            reasons.add(f"{prefix}.keys")
            continue
        version = descriptor.get("version")
        executable = descriptor.get("executable")
        digest = descriptor.get("sha256")
        package_root = descriptor.get("packageRoot")
        package_root_mode = descriptor.get("packageRootMode")
        package_tree_sha256 = descriptor.get("packageTreeSha256")
        if not isinstance(version, str) or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version) is None:
            reasons.add(f"{prefix}.version")
        if not isinstance(executable, str) or not Path(executable).is_absolute():
            reasons.add(f"{prefix}.executable")
        elif Path(executable).name != "node" or Path(executable).parent.name != "bin":
            reasons.add(f"{prefix}.executable")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            reasons.add(f"{prefix}.sha256")
        expected_package_root = (
            Path(executable).parent.parent / "lib/node_modules/npm"
            if isinstance(executable, str) and Path(executable).is_absolute() else None
        )
        if (
            not isinstance(package_root, str) or not Path(package_root).is_absolute()
            or expected_package_root is None or Path(package_root) != expected_package_root
        ):
            reasons.add(f"{prefix}.packageRoot")
        if (
            type(package_root_mode) is not int
            or package_root_mode not in SECURE_NODE_PACKAGE_ROOT_MODES
        ):
            reasons.add(f"{prefix}.packageRootMode")
        if (
            not isinstance(package_tree_sha256, str)
            or SHA256_RE.fullmatch(package_tree_sha256) is None
        ):
            reasons.add(f"{prefix}.packageTreeSha256")
        entrypoints: list[tuple[str, str, str]] = []
        for tool in ("npm", "npx"):
            tool_descriptor = descriptor.get(tool)
            tool_prefix = f"{prefix}.{tool}"
            if not isinstance(tool_descriptor, dict) or set(tool_descriptor) != NODE_ENTRYPOINT_KEYS:
                reasons.add(f"{tool_prefix}.keys")
                continue
            entrypoint = tool_descriptor.get("entrypoint")
            expected = tool_descriptor.get("sha256")
            if not isinstance(entrypoint, str) or not Path(entrypoint).is_absolute():
                reasons.add(f"{tool_prefix}.entrypoint")
            elif isinstance(executable, str) and Path(executable).is_absolute():
                expected_entrypoint = expected_package_root / f"bin/{tool}-cli.js"
                if Path(entrypoint) != expected_entrypoint:
                    reasons.add(f"{tool_prefix}.entrypoint")
            if not isinstance(expected, str) or SHA256_RE.fullmatch(expected) is None:
                reasons.add(f"{tool_prefix}.sha256")
            if isinstance(entrypoint, str) and isinstance(expected, str):
                entrypoints.append((tool_prefix, entrypoint, expected))
        if not verify_identity or any(reason.startswith(prefix) for reason in reasons):
            continue
        observed, error = secure_executable_descriptor(Path(executable))
        if error or observed is None:
            reasons.add(f"{prefix}.executable")
            continue
        if observed["sha256"] != digest:
            reasons.add(f"{prefix}.sha256")
        package_tree = (
            secure_node_package_tree_descriptor(Path(package_root))
            if isinstance(package_root, str) else None
        )
        if (
            package_tree is None
            or package_tree["sha256"] != package_tree_sha256
            or package_tree["rootMode"] != package_root_mode
        ):
            reasons.add(f"{prefix}.packageTreeSha256")
            if package_tree is not None and package_tree["rootMode"] != package_root_mode:
                reasons.add(f"{prefix}.packageRootMode")
        result = run_bounded_command(
            [executable, "--version"], cwd=Path("/"), env=SECURE_ENV,
            timeout_sec=5.0, max_output_bytes=4096,
        )
        if result.error or result.returncode != 0 or result.stdout.strip() != version:
            reasons.add(f"{prefix}.version")
        for tool_prefix, entrypoint, expected in entrypoints:
            observed_entrypoint, entrypoint_error = secure_regular_descriptor(
                Path(entrypoint), MAX_DIRTY_FILE_BYTES,
            )
            if entrypoint_error or observed_entrypoint is None:
                reasons.add(f"{tool_prefix}.entrypoint")
            elif observed_entrypoint["sha256"] != expected:
                reasons.add(f"{tool_prefix}.sha256")


def _validate_esp_idf(value: Any, reasons: set[str], *, verify_identity: bool) -> None:
    if not isinstance(value, dict) or set(value) != ESP_IDF_KEYS:
        reasons.add("tools.espIdf.keys")
        return
    version = value.get("version")
    commit = value.get("commit")
    root = value.get("root")
    relative = value.get("versionFile")
    digest = value.get("versionFileSha256")
    if not isinstance(version, str) or re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        reasons.add("tools.espIdf.version")
    if not isinstance(commit, str) or SHA_RE.fullmatch(commit) is None:
        reasons.add("tools.espIdf.commit")
    if not isinstance(root, str) or Path(root) != CANONICAL_ESP_IDF_ROOT:
        reasons.add("tools.espIdf.root")
    if relative != "tools/cmake/version.cmake":
        reasons.add("tools.espIdf.versionFile")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        reasons.add("tools.espIdf.versionFileSha256")
    if not verify_identity or any(reason.startswith("tools.espIdf.") for reason in reasons):
        return
    try:
        checkout = Path(root)
        observed_commit = _git(checkout, "rev-parse", "--verify", "HEAD^{commit}").strip()
        dirty = _dirty_paths(checkout)
    except (RuntimeError, TypeError, OSError):
        reasons.add("tools.espIdf.identity")
        return
    observed, error = secure_regular_descriptor(checkout / relative, MAX_DIRTY_FILE_BYTES, include_content=True)
    if error or observed is None or observed["sha256"] != digest or observed_commit != commit or dirty:
        reasons.add("tools.espIdf.identity")
        return
    text = observed["content"].decode("utf-8", errors="strict")
    parts = []
    for name in ("MAJOR", "MINOR", "PATCH"):
        match = re.search(rf"set\(IDF_VERSION_{name} ([0-9]+)\)", text)
        if match is None:
            reasons.add("tools.espIdf.identity")
            return
        parts.append(match.group(1))
    if version != f"v{'.'.join(parts)}":
        reasons.add("tools.espIdf.version")


def upgrade_candidate_schema(
    candidate: dict[str, Any], *, node_executables: dict[str, str], esp_idf_root: str,
) -> dict[str, Any]:
    upgraded = copy.deepcopy(candidate)
    app_path = Path(upgraded["firmware"]["appPath"])
    evidence_path = app_path.parent / "manifest.json"
    evidence, evidence_error = secure_regular_descriptor(evidence_path, MAX_FIRMWARE_MANIFEST_BYTES)
    if evidence_error or evidence is None:
        raise ValueError("candidate schema upgrade failed")
    upgraded["firmware"]["evidenceManifestPath"] = str(evidence_path)
    upgraded["firmware"]["evidenceManifestSha256"] = evidence["sha256"]
    node = {}
    for key in sorted(NODE_KEYS):
        executable = Path(node_executables[key])
        observed, error = secure_executable_descriptor(executable)
        if error or observed is None:
            raise ValueError("candidate schema upgrade failed")
        descriptor: dict[str, Any] = {
            "version": candidate["tools"]["node"][key],
            "executable": str(executable), "sha256": observed["sha256"],
        }
        package_root = executable.parent.parent / "lib/node_modules/npm"
        package_tree = secure_node_package_tree_descriptor(package_root)
        if package_tree is None:
            raise ValueError("candidate schema upgrade failed")
        descriptor["packageRoot"] = str(package_root)
        descriptor["packageRootMode"] = package_tree["rootMode"]
        descriptor["packageTreeSha256"] = package_tree["sha256"]
        for tool in ("npm", "npx"):
            entrypoint = executable.parent.parent / f"lib/node_modules/npm/bin/{tool}-cli.js"
            entrypoint_observed, entrypoint_error = secure_regular_descriptor(
                entrypoint, MAX_DIRTY_FILE_BYTES,
            )
            if entrypoint_error or entrypoint_observed is None:
                raise ValueError("candidate schema upgrade failed")
            descriptor[tool] = {
                "entrypoint": str(entrypoint), "sha256": entrypoint_observed["sha256"],
            }
        node[key] = descriptor
    upgraded["tools"]["node"] = node
    root = Path(esp_idf_root)
    version_file = root / "tools/cmake/version.cmake"
    version_observed, version_error = secure_regular_descriptor(version_file, MAX_DIRTY_FILE_BYTES)
    if version_error or version_observed is None:
        raise ValueError("candidate schema upgrade failed")
    upgraded["tools"]["espIdf"] = {
        "version": candidate["tools"]["espIdf"],
        "commit": _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip(),
        "root": str(root), "versionFile": "tools/cmake/version.cmake",
        "versionFileSha256": version_observed["sha256"],
    }
    return upgraded


def validate_candidate(
    candidate: Any, *, now: datetime | None = None, verify_external_tools: bool = True,
) -> list[str]:
    """Return sorted, stable and privacy-safe validation reason codes."""
    reasons: set[str] = set()
    validation_now = now or datetime.now(timezone.utc)
    if not isinstance(candidate, dict):
        return ["candidate.type"]
    if set(candidate) != REQUIRED_KEYS:
        reasons.add("topLevel.keys")
    if not isinstance(candidate.get("candidateId"), str) or CANDIDATE_ID_RE.fullmatch(candidate["candidateId"]) is None:
        reasons.add("candidateId")
    created = _parse_rfc3339_utc(candidate.get("createdAt"))
    expires = _parse_rfc3339_utc(candidate.get("expiresAt"))
    if created is None:
        reasons.add("createdAt")
    if expires is None:
        reasons.add("expiresAt")
    if created is not None and expires is not None:
        if created >= expires:
            reasons.add("timestamps.order")
        elif expires <= validation_now:
            reasons.add("expiresAt.expired")
    tools = candidate.get("tools")
    if isinstance(tools, dict):
        if set(tools) != TOOLS_KEYS:
            reasons.add("tools.keys")
        _validate_robot_preview_browser(
            tools.get("robotPreviewBrowser"), reasons, verify_identity=verify_external_tools,
        )
        _validate_node_tools(tools.get("node"), reasons, verify_identity=verify_external_tools)
        _validate_python_test_runtime(
            tools.get("pythonTestRuntime"), reasons, verify_identity=verify_external_tools,
        )
        _validate_esp_idf(
            tools.get("espIdf"), reasons, verify_identity=False,
        )
        installs = tools.get("nodeInstalls")
        if not isinstance(installs, dict) or not set(installs).issubset(NODE_KEYS):
            reasons.add("tools.nodeInstalls.keys")
        else:
            for key, metadata in installs.items():
                if not isinstance(metadata, dict) or set(metadata) != NODE_INSTALL_KEYS:
                    reasons.add(f"tools.nodeInstalls.{key}.keys")
                    continue
                tree = metadata.get("treeDigest")
                if not isinstance(tree, dict) or set(tree) != NODE_TREE_KEYS or tree.get("schema") != NODE_TREE_SCHEMA:
                    reasons.add(f"tools.nodeInstalls.{key}.treeDigest")
    else:
        reasons.add("tools")
    evidence_root = candidate.get("evidenceRoot")
    if not isinstance(evidence_root, str) or not Path(evidence_root).is_absolute():
        reasons.add("evidenceRoot")

    course = candidate.get("course")
    if not isinstance(course, dict) or set(course) != COURSE_KEYS:
        reasons.add("course.keys")
        course = {}
    if not isinstance(course.get("courseId"), str) or not course.get("courseId"):
        reasons.add("course.courseId")
    if course.get("courseKey") != COURSE_KEY:
        reasons.add("course.courseKey")

    repositories = candidate.get("repositories")
    if not isinstance(repositories, dict) or set(repositories) != REPOSITORIES:
        reasons.add("repositories.keys")
        repositories = repositories if isinstance(repositories, dict) else {}
    repository_roots = {
        name: _validate_repository(name, repositories.get(name), reasons)
        for name in sorted(REPOSITORIES)
    }
    if isinstance(tools, dict):
        _validate_esp_idf(
            tools.get("espIdf"), reasons,
            verify_identity=(verify_external_tools and not any(reason.endswith(".git") for reason in reasons)),
        )
    for name, root in repository_roots.items():
        prefix = f"repositories.{name}."
        if root is not None and not any(reason.startswith(prefix) for reason in reasons):
            value = repositories[name]
            if not _repository_matches_candidate(root, value):
                reasons.add(f"repositories.{name}.changed")
    _validate_images(
        candidate.get("images"), repositories, reasons, verify_identity=verify_external_tools,
        verify_provenance=not any(reason.startswith("repositories.") for reason in reasons),
    )
    _validate_firmware(
        candidate.get("firmware"), repositories, tools if isinstance(tools, dict) else {}, reasons,
        verify_identity=(
            verify_external_tools
            and not any(reason.startswith("repositories.firmware.") for reason in reasons)
        ),
        candidate_created=(
            created if created is not None and expires is not None
            and created < expires and expires > validation_now else None
        ),
        candidate_expires=(
            expires if created is not None and expires is not None
            and created < expires and expires > validation_now else None
        ),
    )
    _validate_database(
        candidate.get("database"), repository_roots.get("backend"), repositories.get("backend"),
        reasons, verify_identity=(
            verify_external_tools
            and not any(reason.startswith("repositories.backend.") for reason in reasons)
        ),
    )

    curriculum = candidate.get("curriculum")
    if not isinstance(curriculum, dict) or set(curriculum) != CURRICULUM_KEYS:
        reasons.add("curriculum.keys")
        curriculum = curriculum if isinstance(curriculum, dict) else {}
    if curriculum.get("courseId") != course.get("courseId"):
        reasons.add("curriculum.courseId")
    if curriculum.get("courseKey") != course.get("courseKey"):
        reasons.add("curriculum.courseKey")
    if curriculum.get("rendererId") != RENDERER_ID:
        reasons.add("curriculum.rendererId")
    if curriculum.get("contractIdentity") != CONTRACT_IDENTITY:
        reasons.add("curriculum.contractIdentity")
    if type(curriculum.get("lessonCount")) is not int or curriculum["lessonCount"] != 26:
        reasons.add("curriculum.lessonCount")
    if type(curriculum.get("activityCount")) is not int or curriculum["activityCount"] != 256:
        reasons.add("curriculum.activityCount")
    if type(curriculum.get("pedagogyCount")) is not int or curriculum["pedagogyCount"] != 6:
        reasons.add("curriculum.pedagogyCount")
    if (
        type(curriculum.get("responseClassCount")) is not int
        or curriculum["responseClassCount"] != 11
    ):
        reasons.add("curriculum.responseClassCount")
    checksum = curriculum.get("sourceChecksum")
    backend_root = repository_roots.get("backend")
    checksum_invalid = not isinstance(checksum, str) or SHA256_RE.fullmatch(checksum) is None
    if backend_root is not None:
        source_digest, source_error = _secure_hash_relative(
            backend_root, "src/lessons/course-mode/curriculum-course-mode.ts",
        )
        if source_error or source_digest != checksum:
            checksum_invalid = True
    if checksum_invalid:
        reasons.add("curriculum.sourceChecksum")
    for name, root in repository_roots.items():
        prefix = f"repositories.{name}."
        if root is not None and not any(reason.startswith(prefix) for reason in reasons):
            if not _repository_matches_candidate(root, repositories[name]):
                reasons.add(f"repositories.{name}.changed")
    return sorted(reasons)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        candidate = strict_json_loads(read_secure_regular(args.candidate, MAX_CANDIDATE_BYTES))
        reasons = validate_candidate(candidate)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        reasons = ["candidate.input"]
    print(json.dumps(
        {"schemaVersion": 1, "validator": "course-mode-candidate.v1",
         "status": "pass" if not reasons else "fail", "reasons": reasons},
        sort_keys=True, separators=(",", ":"),
    ))
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
