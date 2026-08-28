#!/usr/bin/env python3
"""Validate a Course Mode production-readiness candidate without mutating state."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
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
MAX_GIT_OUTPUT_BYTES = 1024 * 1024
GIT_TIMEOUT_SEC = 10.0
_GIT_CANDIDATES = (
    Path("/Library/Developer/CommandLineTools/usr/bin/git"),
    Path("/usr/bin/git"),
)
TRUSTED_GIT_EXECUTABLE = next((path for path in _GIT_CANDIDATES if path.is_file()), _GIT_CANDIDATES[-1])
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


@dataclass(frozen=True)
class BoundedCommandResult:
    returncode: int | None
    stdout: str
    error: str | None


def run_bounded_command(
    command: list[str], *, cwd: Path, timeout_sec: float, max_output_bytes: int,
    env: dict[str, str] | None = None,
) -> BoundedCommandResult:
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


def _parse_rfc3339_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or RFC3339_UTC_RE.fullmatch(value) is None:
        return None
    try:
        return datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        return None


def validate_candidate(candidate: Any, *, now: datetime | None = None) -> list[str]:
    """Return sorted, stable and privacy-safe validation reason codes."""
    reasons: set[str] = set()
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
        elif expires <= (now or datetime.now(timezone.utc)):
            reasons.add("expiresAt.expired")
    for field in ("images", "firmware", "database", "tools"):
        if not isinstance(candidate.get(field), dict):
            reasons.add(field)
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
    return sorted(reasons)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        path = args.candidate.resolve(strict=True)
        if path.is_symlink() or path.stat().st_size > MAX_CANDIDATE_BYTES:
            raise OSError("invalid candidate input")
        with path.open("rb") as source:
            raw = source.read(MAX_CANDIDATE_BYTES + 1)
        if len(raw) > MAX_CANDIDATE_BYTES:
            raise OSError("candidate input too large")
        candidate = json.loads(raw.decode("utf-8"))
        reasons = validate_candidate(candidate)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        reasons = ["candidate.input"]
    print(json.dumps(
        {"schemaVersion": 1, "validator": "course-mode-candidate.v1",
         "status": "pass" if not reasons else "fail", "reasons": reasons},
        sort_keys=True, separators=(",", ":"),
    ))
    return 0 if not reasons else 1


if __name__ == "__main__":
    raise SystemExit(main())
