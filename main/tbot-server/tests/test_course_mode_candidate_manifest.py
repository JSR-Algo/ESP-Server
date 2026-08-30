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
            migration = root / "src/database/migrations/127_shared_visual_layered_cinematic_compatibility.sql"
            migration.parent.mkdir(parents=True)
            migration.write_text("SELECT 127;\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-m", "fixture")
        result[name] = root
    return result


@pytest.fixture
def candidate(repositories: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    browser = tmp_path / "ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell"
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"pinned chromium fixture\n")
    browser.chmod(0o755)
    tree, error = manifest.secure_browser_bundle_descriptor(browser.parent)
    assert error is None and tree is not None
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        "case \"$5\" in\n"
        "  local/backend:candidate) echo sha256:" + "1" * 64 + ";;\n"
        "  local/web:candidate) echo sha256:" + "2" * 64 + ";;\n"
        "  postgres:16-alpine) echo sha256:" + "3" * 64 + ";;\n"
        "  *) exit 1;;\n"
        "esac\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setattr(manifest, "TRUSTED_DOCKER_EXECUTABLE", docker, raising=False)
    firmware_dir = tmp_path / "firmware-artifact"
    firmware_dir.mkdir()
    app = firmware_dir / "xiaozhi.bin"
    elf = firmware_dir / "xiaozhi.elf"
    app.write_bytes(b"firmware-app")
    elf.write_bytes(b"firmware-elf")
    evidence = firmware_dir / "manifest.json"
    evidence_payload = {
        "status": "PASS", "profile": "production", "board": "fixture", "target": "esp32s3",
        "sourceCommit": repositories["firmware"] and _git(repositories["firmware"], "rev-parse", "HEAD"),
        "createdAt": "2026-08-29T00:00:00Z",
        "app": {"file": app.name, "offset": "0x20000", "bytes": app.stat().st_size,
                "sha256": hashlib.sha256(app.read_bytes()).hexdigest()},
        "elf": {"file": elf.name, "bytes": elf.stat().st_size,
                "sha256": hashlib.sha256(elf.read_bytes()).hexdigest()},
        "partition": {"bytes": 1024, "freeBytes": 1024 - app.stat().st_size, "freePercent": 0.0},
        "reproducibility": {"appByteIdentical": True, "elfByteIdentical": True,
                            "independentCleanBuilds": 2, "ccacheEnabled": False},
        "toolchain": {"espIdf": "v5.5.4", "espIdfCommit": "a" * 40, "python": "3.9.6",
                      "compiler": "fixture", "cmake": "fixture", "ninja": "fixture"},
        "config": {"sdkconfigSha256": "a" * 64, "sdkconfigDefaultsLocalSha256": "b" * 64,
                   "dependenciesLockSha256": "c" * 64, "appReproducibleBuild": True,
                   "productionConfigAudit": "PASS", "productionArtifactAudit": "PASS"},
        "tests": {"projectSourceGate": "PASS", "firmwareVersionAndCourseGates": "PASS"},
        "safety": {"flashed": False, "serialAccessed": False, "hilRun": False,
                   "physicalDeviceAccessed": False},
    }
    evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")
    node = {}
    for key, version in (("backend", "v22.23.2"), ("adminManagerWeb", "v20.20.2")):
        executable = tmp_path / f"node-{key}"
        executable.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
        executable.chmod(0o755)
        node[key] = {"version": version, "executable": str(executable),
                     "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
    migration = repositories["backend"] / "src/database/migrations/127_shared_visual_layered_cinematic_compatibility.sql"
    return {
        "candidateId": "course-mode-2026-08-29.1",
        "createdAt": "2026-08-29T00:00:00Z",
        "expiresAt": "2026-09-05T00:00:00Z",
        "course": {"courseId": "10000000-0000-4000-8000-000000000001", "courseKey": "english-6month-4-6"},
        "repositories": {name: _repository(root) for name, root in repositories.items()},
        "images": {
            "lessonStudioBackend": {"reference": "local/backend:candidate", "id": "sha256:" + "1" * 64},
            "lessonStudioWeb": {"reference": "local/web:candidate", "id": "sha256:" + "2" * 64},
        },
        "firmware": {
            "appPath": str(app), "appOffset": "0x20000", "appBytes": app.stat().st_size,
            "appSha256": hashlib.sha256(app.read_bytes()).hexdigest(),
            "elfSha256": hashlib.sha256(elf.read_bytes()).hexdigest(),
            "partitionBytes": 1024, "freeBytes": 1024 - app.stat().st_size,
            "evidenceManifestPath": str(evidence),
            "evidenceManifestSha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
        },
        "database": {
            "engineImage": "postgres:16-alpine", "engineImageId": "sha256:" + "3" * 64,
            "migrationHead": migration.name,
            "migrationHeadSha256": hashlib.sha256(migration.read_bytes()).hexdigest(),
        },
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
        "tools": {
            "nodeInstalls": {},
            "robotPreviewBrowser": {
                "version": 2,
                "engine": "chromium-headless-shell",
                "revision": "1223",
                "root": str(browser.parent),
                "executable": browser.name,
                "treeDigest": tree,
            }, "node": node, "espIdf": "v5.5.4",
        },
        "evidenceRoot": str(tmp_path / "evidence"),
    }


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def test_candidate_accepts_exact_committed_repository_identity(candidate: dict) -> None:
    assert validate_candidate(candidate, now=NOW) == []


@pytest.mark.parametrize(
    "section,field,replacement,reason",
    [
        ("images", "lessonStudioBackend", {}, "images.lessonStudioBackend.keys"),
        ("firmware", "appSha256", "f" * 64, "firmware.appSha256"),
        ("firmware", "elfSha256", "f" * 64, "firmware.elfSha256"),
        ("database", "engineImageId", "sha256:" + "f" * 64, "database.engineImageId"),
        ("database", "migrationHeadSha256", "f" * 64, "database.migrationHeadSha256"),
        ("tools", "espIdf", "v0.0.0", "tools.espIdf"),
    ],
)
def test_candidate_rejects_mutated_artifact_identity(
    candidate: dict, section: str, field: str, replacement: object, reason: str,
) -> None:
    candidate[section][field] = replacement

    assert reason in validate_candidate(candidate, now=NOW)


@pytest.mark.parametrize("section", ["images", "firmware", "database", "tools"])
def test_candidate_artifact_sections_have_exact_schema(candidate: dict, section: str) -> None:
    candidate[section]["unexpected"] = True

    assert f"{section}.keys" in validate_candidate(candidate, now=NOW)


@pytest.mark.parametrize("key", ["backend", "adminManagerWeb"])
@pytest.mark.parametrize("field", ["version", "executable", "sha256"])
def test_candidate_node_runtime_descriptor_is_exact_and_runtime_bound(
    candidate: dict, key: str, field: str,
) -> None:
    candidate["tools"]["node"][key][field] = {
        "version": "v0.0.0", "executable": "/tmp/node", "sha256": "f" * 64,
    }[field]

    assert f"tools.node.{key}.{field}" in validate_candidate(candidate, now=NOW)


def test_candidate_rejects_symlink_and_hardlink_firmware_artifacts(
    candidate: dict, tmp_path: Path,
) -> None:
    original = Path(candidate["firmware"]["appPath"])
    hardlink = tmp_path / "hardlink.bin"
    os.link(original, hardlink)
    assert "firmware.appPath" in validate_candidate(candidate, now=NOW)

    hardlink.unlink()
    symlink = tmp_path / "app-link.bin"
    symlink.symlink_to(original)
    candidate["firmware"]["appPath"] = str(symlink)
    assert "firmware.appPath" in validate_candidate(candidate, now=NOW)


def test_candidate_firmware_secure_read_detects_path_replacement(
    candidate: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = Path(candidate["firmware"]["appPath"])
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(target.read_bytes())
    original_read = os.read
    replaced = False
    target_inode = target.stat().st_ino

    def replace_after_first_read(fd: int, size: int) -> bytes:
        nonlocal replaced
        data = original_read(fd, size)
        if data and not replaced and os.fstat(fd).st_ino == target_inode:
            replaced = True
            os.replace(replacement, target)
        return data

    monkeypatch.setattr(os, "read", replace_after_first_read)

    assert "firmware.appPath" in validate_candidate(candidate, now=NOW)


def test_candidate_browser_is_bound_to_regular_executable_content(candidate: dict) -> None:
    descriptor = candidate["tools"]["robotPreviewBrowser"]
    browser = Path(descriptor["root"]) / descriptor["executable"]
    browser.write_bytes(b"drift")

    assert validate_candidate(candidate, now=NOW) == ["tools.robotPreviewBrowser.identity"]


def test_candidate_browser_rejects_symlink(candidate: dict, tmp_path: Path) -> None:
    root = Path(candidate["tools"]["robotPreviewBrowser"]["root"])
    target = tmp_path / "browser-target"
    target.write_bytes(b"resource")
    (root / "unsafe-resource").symlink_to(target)

    assert validate_candidate(candidate, now=NOW) == ["tools.robotPreviewBrowser.identity"]


def test_browser_bundle_descriptor_rejects_over_depth_tree(tmp_path: Path) -> None:
    root = tmp_path / "browser"
    root.mkdir()
    directory = root
    for index in range(manifest.MAX_BROWSER_BUNDLE_DEPTH + 1):
        directory /= f"d{index}"
        directory.mkdir()

    assert manifest.secure_browser_bundle_descriptor(root) == (None, "tree")


def test_browser_bundle_descriptor_rejects_surrogateescaped_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "browser"
    root.mkdir()
    metadata = root.lstat()

    class InvalidByteEntry:
        name = "invalid-\udcff"
        path = str(root / name)

        @staticmethod
        def stat(*, follow_symlinks: bool):
            assert follow_symlinks is False
            return metadata

    original_scandir = os.scandir
    monkeypatch.setattr(
        manifest.os, "scandir",
        lambda directory: [InvalidByteEntry()] if Path(directory) == root else original_scandir(directory),
    )

    assert manifest.secure_browser_bundle_descriptor(root) == (None, "path")


def test_browser_bundle_descriptor_fails_closed_on_recursion_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "browser"
    root.mkdir()

    def raise_recursion_error(_directory: Path) -> None:
        raise RecursionError

    monkeypatch.setattr(manifest.os, "scandir", raise_recursion_error)

    assert manifest.secure_browser_bundle_descriptor(root) == (None, "tree")


def test_candidate_browser_descriptor_has_exact_schema(candidate: dict) -> None:
    candidate["tools"]["robotPreviewBrowser"]["fallback"] = "/Applications/Google Chrome.app"

    assert validate_candidate(candidate, now=NOW) == ["tools.robotPreviewBrowser.keys"]


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


def test_candidate_cli_rejects_symlink_input(tmp_path: Path) -> None:
    target = tmp_path / "candidate.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "candidate-link.json"
    link.symlink_to(target)

    completed = subprocess.run(
        [sys.executable, str(Path(manifest.__file__)), str(link)],
        check=False, capture_output=True, text=True, timeout=5,
    )

    assert completed.returncode == 1 and completed.stderr == ""
    assert json.loads(completed.stdout)["reasons"] == ["candidate.input"]


def test_candidate_input_detects_path_replacement(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "candidate.json"
    target.write_bytes(b"{}")
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(b'{"replacement":true}')
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

    with pytest.raises(OSError):
        manifest.read_secure_regular(target, manifest.MAX_CANDIDATE_BYTES)


@pytest.mark.parametrize("raw", [
    b'{"candidateId":"one","candidateId":"two"}',
    b'{"value":NaN}', b'{"value":Infinity}', b'{"value":-Infinity}',
    b'{"value":1e999}', b'{"value":-1e999}', b'\xff',
])
def test_candidate_cli_uses_strict_json(raw: bytes, tmp_path: Path) -> None:
    path = tmp_path / "candidate.json"
    path.write_bytes(raw)

    completed = subprocess.run(
        [sys.executable, str(Path(manifest.__file__)), str(path)],
        check=False, capture_output=True, text=True, timeout=5,
    )

    assert completed.returncode == 1 and completed.stderr == ""
    assert json.loads(completed.stdout)["reasons"] == ["candidate.input"]


def test_candidate_rechecks_repository_identity_after_hashing(
    candidate: dict, repositories: dict[str, Path], monkeypatch,
) -> None:
    backend = repositories["backend"]
    (backend / "other.txt").write_text("second commit", encoding="utf-8")
    _git(backend, "add", "other.txt")
    _git(backend, "commit", "-m", "second")
    second = _git(backend, "rev-parse", "HEAD")
    first = candidate["repositories"]["backend"]["sha"]
    _git(backend, "branch", "candidate-next", second)
    _git(backend, "checkout", "--detach", first)
    _git(backend, "branch", "-f", "candidate", first)
    _git(backend, "checkout", "candidate")
    original = manifest._secure_hash_relative
    switched = False

    def switch_after_identity(root: Path, relative: str):
        nonlocal switched
        if root == backend and relative.endswith("curriculum-course-mode.ts") and not switched:
            switched = True
            _git(backend, "checkout", "candidate-next")
        return original(root, relative)

    monkeypatch.setattr(manifest, "_secure_hash_relative", switch_after_identity)

    assert validate_candidate(candidate, now=NOW) == ["repositories.backend.changed"]
