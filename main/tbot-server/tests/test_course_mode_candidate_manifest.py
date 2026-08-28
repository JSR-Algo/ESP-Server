from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import scripts.course_mode_candidate_manifest as manifest
from scripts.course_mode_candidate_manifest import REQUIRED_KEYS, validate_candidate


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True,
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
def repositories(tmp_path: Path) -> dict[str, Path]:
    result = {}
    for name in ("backend", "adminEsp", "firmware"):
        root = tmp_path / name
        root.mkdir()
        _git(root, "init", "-b", "candidate")
        _git(root, "config", "user.email", "candidate@example.invalid")
        _git(root, "config", "user.name", "Candidate Test")
        _git(root, "remote", "add", "origin", f"https://example.invalid/{name}.git")
        (root / "tracked.txt").write_text(name, encoding="utf-8")
        if name == "backend":
            source = root / "src/lessons/course-mode/curriculum-course-mode.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const curriculum = 26;\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "fixture")
        result[name] = root
    return result


@pytest.fixture
def candidate(repositories: dict[str, Path], tmp_path: Path) -> dict:
    return {
        "candidateId": "course-mode-2026-08-29.1",
        "createdAt": "2026-08-29T00:00:00Z",
        "expiresAt": "2026-09-05T00:00:00Z",
        "course": {"courseId": "10000000-0000-4000-8000-000000000001", "courseKey": "english-6month-4-6"},
        "repositories": {name: _repository(root) for name, root in repositories.items()},
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
            "sourceChecksum": hashlib.sha256(
                (repositories["backend"] / "src/lessons/course-mode/curriculum-course-mode.ts").read_bytes(),
            ).hexdigest(),
        },
        "tools": {},
        "evidenceRoot": str(tmp_path / "evidence"),
    }


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def test_candidate_accepts_exact_committed_repository_identity(candidate: dict) -> None:
    assert validate_candidate(candidate, now=NOW) == []


def test_candidate_requires_exact_top_level_and_repository_keys(candidate: dict) -> None:
    candidate["unexpected"] = True
    candidate["repositories"]["other"] = candidate["repositories"]["backend"]

    assert validate_candidate(candidate) == ["repositories.keys", "topLevel.keys"]
    assert set(candidate) != REQUIRED_KEYS


def test_candidate_rejects_unlisted_dirty_file(candidate: dict, repositories: dict[str, Path]) -> None:
    (repositories["adminEsp"] / "tracked.txt").write_text("dirty", encoding="utf-8")
    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = []

    assert validate_candidate(candidate) == ["repositories.adminEsp.dirty"]


def test_candidate_accepts_only_exact_hash_bound_dirty_exception(
    candidate: dict, repositories: dict[str, Path],
) -> None:
    path = repositories["adminEsp"] / "tracked.txt"
    path.write_text("reviewed dirty content", encoding="utf-8")
    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = [{
        "path": "tracked.txt",
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }]

    assert validate_candidate(candidate) == []

    candidate["repositories"]["adminEsp"]["dirtyExceptions"][0]["sha256"] = "0" * 64
    assert validate_candidate(candidate) == ["repositories.adminEsp.dirtyExceptions.hash"]


@pytest.mark.parametrize("field", ["path", "sha", "branch", "remoteUrl"])
def test_candidate_rejects_repository_identity_drift(candidate: dict, field: str) -> None:
    candidate["repositories"]["backend"][field] = {
        "path": "relative/backend",
        "sha": "f" * 40,
        "branch": "wrong",
        "remoteUrl": "https://example.invalid/wrong.git",
    }[field]

    assert validate_candidate(candidate) == [f"repositories.backend.{field}"]


def test_candidate_reasons_are_sorted_stable_json(candidate: dict) -> None:
    candidate["curriculum"]["rendererId"] = "teebot-lesson-renderer.v4"
    candidate["curriculum"]["lessonCount"] = 25
    candidate["course"]["courseKey"] = "wrong"

    first = validate_candidate(candidate)
    second = validate_candidate(json.loads(json.dumps(candidate)))

    assert first == second == sorted(first)
    assert first == [
        "course.courseKey",
        "curriculum.courseKey",
        "curriculum.lessonCount",
        "curriculum.rendererId",
    ]


def test_candidate_binds_curriculum_checksum_to_backend_source(candidate: dict) -> None:
    candidate["curriculum"]["sourceChecksum"] = "f" * 64

    assert validate_candidate(candidate) == ["curriculum.sourceChecksum"]


@pytest.mark.parametrize("field, value", [
    ("pedagogyCount", 5),
    ("pedagogyCount", "6"),
    ("responseClassCount", 10),
    ("responseClassCount", "11"),
])
def test_candidate_requires_exact_curriculum_class_counts(
    candidate: dict, field: str, value: object,
) -> None:
    candidate["curriculum"][field] = value

    assert validate_candidate(candidate) == [f"curriculum.{field}"]


@pytest.mark.parametrize("field", ["pedagogyCount", "responseClassCount"])
def test_candidate_reports_missing_curriculum_class_count(candidate: dict, field: str) -> None:
    candidate["curriculum"].pop(field)

    assert validate_candidate(candidate) == ["curriculum.keys", f"curriculum.{field}"]


def test_candidate_freeze_report_records_exact_tdd_and_simulator_evidence() -> None:
    report = (
        Path(__file__).resolve().parents[3]
        / "docs/qa/ad-hoc/2026-08-29-course-mode-candidate-freeze.md"
    ).read_text(encoding="utf-8")

    for required in (
        "COURSE_MODE_BACKEND_ROOT=/Users/manhhodinh/Documents/TBOT/tbot-backend/.worktrees/prod-readiness-task1-backend python3 -m pytest -q tests/test_course_mode_candidate_manifest.py tests/test_course_mode_curriculum_e2e.py",
        "python3 scripts/course_mode_26week_simulation.py --backend-root /Users/manhhodinh/Documents/TBOT/tbot-backend/.worktrees/prod-readiness-task1-backend",
        "47 passed",
        "26 lessons, 256 activities, 6 pedagogies, and 11 response classes",
        "missing manifest module and explicit-root resolver",
        "checksum/SHA binding and dirty-root",
        "Final GREEN",
    ):
        assert required in report


@pytest.mark.parametrize("candidate_id", [
    "", "course mode-2026-08-29.1", "course-mode-2026-08-29", "../course-mode-1",
])
def test_candidate_id_has_a_canonical_format(candidate: dict, candidate_id: str) -> None:
    candidate["candidateId"] = candidate_id

    assert "candidateId" in validate_candidate(candidate, now=NOW)


@pytest.mark.parametrize("field, value", [
    ("createdAt", "2026-08-29T00:00:00+00:00"),
    ("createdAt", "2026-08-29 00:00:00Z"),
    ("expiresAt", "2026-09-05T00:00:00z"),
    ("expiresAt", "not-a-time"),
])
def test_candidate_times_require_canonical_rfc3339_utc(
    candidate: dict, field: str, value: str,
) -> None:
    candidate[field] = value

    assert field in validate_candidate(candidate, now=NOW)


def test_candidate_times_are_ordered_and_unexpired(candidate: dict) -> None:
    candidate["createdAt"] = candidate["expiresAt"]
    assert validate_candidate(candidate, now=NOW) == ["timestamps.order"]

    candidate["createdAt"] = "2026-08-20T00:00:00Z"
    candidate["expiresAt"] = "2026-08-21T00:00:00Z"
    assert validate_candidate(candidate, now=NOW) == ["expiresAt.expired"]


@pytest.mark.parametrize("field, value", [
    ("lessonCount", 26.0), ("lessonCount", True),
    ("activityCount", 256.0), ("activityCount", True),
    ("pedagogyCount", 6.0), ("pedagogyCount", True),
    ("responseClassCount", 11.0), ("responseClassCount", True),
])
def test_candidate_counts_are_exact_json_integers(
    candidate: dict, field: str, value: object,
) -> None:
    candidate["curriculum"][field] = value

    assert validate_candidate(candidate, now=NOW) == [f"curriculum.{field}"]


def test_candidate_git_ignores_repo_local_fsmonitor_and_hooks(
    candidate: dict, repositories: dict[str, Path], tmp_path: Path, monkeypatch,
) -> None:
    marker = tmp_path / "git-config-executed"
    executable = tmp_path / "hostile.sh"
    executable.write_text(f"#!/bin/sh\necho invoked > {marker}\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "post-index-change").symlink_to(executable)
    _git(repositories["adminEsp"], "config", "core.fsmonitor", str(executable))
    _git(repositories["adminEsp"], "config", "core.hooksPath", str(hooks))
    hostile_bin = tmp_path / "bin"
    hostile_bin.mkdir()
    (hostile_bin / "git").symlink_to(executable)
    monkeypatch.setenv("PATH", str(hostile_bin))

    assert validate_candidate(candidate, now=NOW) == []
    assert not marker.exists()


def test_candidate_git_output_is_bounded(candidate: dict, tmp_path: Path, monkeypatch) -> None:
    fake_git = tmp_path / "fake-git"
    fake_git.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write('x' * 2000000)\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setattr(manifest, "TRUSTED_GIT_EXECUTABLE", fake_git)

    assert validate_candidate(candidate, now=NOW) == [
        "repositories.adminEsp.git",
        "repositories.backend.git",
        "repositories.firmware.git",
    ]


def test_candidate_rejects_oversized_dirty_exception(
    candidate: dict, repositories: dict[str, Path],
) -> None:
    path = repositories["adminEsp"] / "large.bin"
    path.write_bytes(b"x" * (manifest.MAX_DIRTY_FILE_BYTES + 1))
    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = [{
        "path": "large.bin", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }]

    assert validate_candidate(candidate, now=NOW) == [
        "repositories.adminEsp.dirtyExceptions.size",
    ]


def test_candidate_rejects_symlink_dirty_exception_escape(
    candidate: dict, repositories: dict[str, Path], tmp_path: Path,
) -> None:
    external = tmp_path / "external-secret"
    external.write_text("not repository data", encoding="utf-8")
    link = repositories["adminEsp"] / "external-link"
    link.symlink_to(external)
    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = [{
        "path": "external-link", "sha256": hashlib.sha256(external.read_bytes()).hexdigest(),
    }]

    assert validate_candidate(candidate, now=NOW) == [
        "repositories.adminEsp.dirtyExceptions.path",
    ]


def test_secure_dirty_read_detects_path_replacement(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "dirty.txt"
    target.write_bytes(b"reviewed")
    replacement = root / "replacement.txt"
    replacement.write_bytes(b"replacement")
    original_read = os.read
    replaced = False

    def replace_after_first_read(fd: int, size: int) -> bytes:
        nonlocal replaced
        data = original_read(fd, size)
        if data and not replaced:
            replaced = True
            os.replace(replacement, target)
        return data

    monkeypatch.setattr(os, "read", replace_after_first_read)
    digest, error = manifest._secure_hash_relative(root, "dirty.txt")

    assert digest is None and error == "changed"


def test_candidate_cli_bounds_input_without_traceback_or_echo(tmp_path: Path) -> None:
    path = tmp_path / "candidate.json"
    secret = "secret-do-not-echo"
    path.write_bytes((secret * 100_000).encode())
    completed = subprocess.run(
        [sys.executable, str(Path(manifest.__file__)), str(path)],
        check=False, capture_output=True, text=True, timeout=5,
    )

    assert completed.returncode == 1 and completed.stderr == ""
    assert json.loads(completed.stdout)["reasons"] == ["candidate.input"]
    assert secret not in completed.stdout
