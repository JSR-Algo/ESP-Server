#!/usr/bin/env python3
"""Validate a Course Mode production-readiness candidate without mutating state."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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
COURSE_KEY = "english-6month-4-6"
RENDERER_ID = "teebot-lesson-renderer.v5"
CONTRACT_IDENTITY = "courseCompanion.v2.contract.v1"


def _git(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments], cwd=root,
        check=check, capture_output=True, text=True,
    )


def _dirty_paths(root: Path) -> list[str]:
    completed = _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    fields = completed.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields) and fields[index]:
        row = fields[index]
        paths.append(row[3:])
        if row[:2][0] in {"R", "C"}:
            index += 1
        index += 1
    return sorted(set(paths))


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
        actual_sha = _git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
        actual_branch = _git(root, "branch", "--show-current").stdout.strip()
        actual_remote = _git(root, "remote", "get-url", "origin").stdout.strip()
        dirty_paths = _dirty_paths(root)
    except subprocess.CalledProcessError:
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
        target = root / relative
        if (
            not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
            or not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != digest
        ):
            hashes_valid = False
    if len(exception_paths) != len(set(exception_paths)):
        reasons.add(f"{prefix}.dirtyExceptions.path")
    if not hashes_valid:
        reasons.add(f"{prefix}.dirtyExceptions.hash")
    if dirty_paths != sorted(set(exception_paths)):
        reasons.add(f"{prefix}.dirty")
    return root


def validate_candidate(candidate: Any) -> list[str]:
    """Return sorted, stable and privacy-safe validation reason codes."""
    reasons: set[str] = set()
    if not isinstance(candidate, dict):
        return ["candidate.type"]
    if set(candidate) != REQUIRED_KEYS:
        reasons.add("topLevel.keys")
    for field in ("candidateId", "createdAt", "expiresAt"):
        if not isinstance(candidate.get(field), str) or not candidate[field]:
            reasons.add(field)
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
    if curriculum.get("lessonCount") != 26:
        reasons.add("curriculum.lessonCount")
    if curriculum.get("activityCount") != 256:
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
    curriculum_source = (
        backend_root / "src/lessons/course-mode/curriculum-course-mode.ts"
        if backend_root is not None else None
    )
    checksum_invalid = not isinstance(checksum, str) or SHA256_RE.fullmatch(checksum) is None
    if backend_root is not None and (
        curriculum_source is None or not curriculum_source.is_file()
        or hashlib.sha256(curriculum_source.read_bytes()).hexdigest() != checksum
    ):
        checksum_invalid = True
    if checksum_invalid:
        reasons.add("curriculum.sourceChecksum")
    return sorted(reasons)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    args = parser.parse_args(argv)
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
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
