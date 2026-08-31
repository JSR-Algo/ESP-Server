from __future__ import annotations

import hashlib
import json
import os
import shutil
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
    backend_ref = f"local/tbot-backend:course-mode-physical-tft-{_git(repositories['backend'], 'rev-parse', 'HEAD')}"
    web_ref = f"local/tbot-server-web:course-mode-physical-tft-{_git(repositories['adminEsp'], 'rev-parse', 'HEAD')}"
    docker_payloads = {
        backend_ref: {"Id": "sha256:" + "1" * 64, "Config": {"Labels": {
            "org.opencontainers.image.revision": _git(repositories["backend"], "rev-parse", "HEAD"),
            "org.opencontainers.image.source": "https://example.invalid/backend.git",
        }}},
        web_ref: {"Id": "sha256:" + "2" * 64, "Config": {"Labels": {
            "org.opencontainers.image.revision": _git(repositories["adminEsp"], "rev-parse", "HEAD"),
            "org.opencontainers.image.source": "https://example.invalid/adminEsp.git",
        }}},
        "postgres:16-alpine": {"Id": "sha256:" + "3" * 64, "Config": {"Labels": {}}},
    }
    docker.write_text(
        f"#!{sys.executable}\nimport json,sys\npayloads={docker_payloads!r}\n"
        "value=payloads.get(sys.argv[-1])\n"
        "print(json.dumps(value)) if value is not None else sys.exit(1)\n",
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
        "status": "PASS", "profile": "production", "board": "LCDWiki ES3C35P", "target": "esp32s3",
        "sourceCommit": repositories["firmware"] and _git(repositories["firmware"], "rev-parse", "HEAD"),
        "createdAt": "2026-08-29T00:00:00Z",
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
        executable.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
        executable.chmod(0o755)
        package_tools = {}
        for tool in ("npm", "npx"):
            entrypoint = prefix / f"lib/node_modules/npm/bin/{tool}-cli.js"
            entrypoint.parent.mkdir(parents=True, exist_ok=True)
            entrypoint.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            package_tools[tool] = {"entrypoint": str(entrypoint),
                                   "sha256": hashlib.sha256(entrypoint.read_bytes()).hexdigest()}
        package_root = prefix / "lib/node_modules/npm"
        package_tree = manifest.secure_node_package_tree_descriptor(package_root)
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
        f"#!{sys.executable}\nimport json,pathlib,runpy,sys\n"
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
        "else:\n"
        " sys.argv=sys.argv[1:]; runpy.run_module('pytest', run_name='__main__')\n",
        encoding="utf-8",
    )
    python_executable.chmod(0o555)
    python_executable.parent.chmod(0o555)
    python_root.chmod(0o555)
    monkeypatch.setattr(manifest, "_python_runtime_library_authority", lambda _root, _executable: True)
    python_tree, python_error = manifest.secure_python_test_runtime_tree_descriptor(python_root)
    assert python_error is None and python_tree is not None
    esp_idf = tmp_path / "esp-idf"
    (esp_idf / "tools/cmake").mkdir(parents=True)
    (esp_idf / "tools/cmake/version.cmake").write_text(
        "set(IDF_VERSION_MAJOR 5)\nset(IDF_VERSION_MINOR 5)\nset(IDF_VERSION_PATCH 4)\n",
        encoding="utf-8",
    )
    _git(esp_idf, "init", "-b", "candidate")
    _git(esp_idf, "config", "user.email", "candidate@example.invalid")
    _git(esp_idf, "config", "user.name", "Candidate Test")
    _git(esp_idf, "add", ".")
    _git(esp_idf, "commit", "-m", "fixture")
    esp_commit = _git(esp_idf, "rev-parse", "HEAD")
    monkeypatch.setattr(manifest, "CANONICAL_ESP_IDF_ROOT", esp_idf)
    evidence_payload["toolchain"]["espIdfCommit"] = esp_commit
    evidence.write_text(json.dumps(evidence_payload), encoding="utf-8")
    migration = repositories["backend"] / "src/database/migrations/127_shared_visual_layered_cinematic_compatibility.sql"
    return {
        "candidateId": "course-mode-2026-08-29.1",
        "createdAt": "2026-08-29T00:00:00Z",
        "expiresAt": "2026-09-05T00:00:00Z",
        "course": {"courseId": "10000000-0000-4000-8000-000000000001", "courseKey": "english-6month-4-6"},
        "repositories": {name: _repository(root) for name, root in repositories.items()},
        "images": {
            "lessonStudioBackend": {"reference": backend_ref, "id": "sha256:" + "1" * 64},
            "lessonStudioWeb": {"reference": web_ref, "id": "sha256:" + "2" * 64},
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
            "pythonTestRuntime": {
                "version": 1, "distribution": "python-build-standalone",
                "root": str(python_root), "executable": "bin/python3.11",
                "pythonVersion": "3.11.9", "pytestVersion": "8.4.1",
                "treeDigest": python_tree,
            },
            "nodeInstalls": {},
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
        "evidenceRoot": str(tmp_path / "evidence"),
    }


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def test_candidate_accepts_exact_committed_repository_identity(candidate: dict) -> None:
    assert validate_candidate(candidate, now=NOW) == []


@pytest.mark.parametrize("mutation", ["content", "mode", "symlink", "hardlink"])
def test_candidate_rejects_python_test_runtime_tree_drift(candidate: dict, mutation: str) -> None:
    descriptor = candidate["tools"]["pythonTestRuntime"]
    root = Path(descriptor["root"])
    executable = root / descriptor["executable"]
    root.chmod(0o755)
    executable.parent.chmod(0o755)
    executable.chmod(0o755)
    if mutation == "content":
        executable.write_bytes(executable.read_bytes() + b"# drift\n")
    elif mutation == "mode":
        executable.chmod(0o700)
    elif mutation == "symlink":
        (root / "unsafe").symlink_to(executable)
    else:
        os.link(executable, root / "unsafe-hardlink")

    assert "tools.pythonTestRuntime.identity" in validate_candidate(candidate, now=NOW)


def test_python_test_runtime_descriptor_accepts_immutable_root(candidate: dict) -> None:
    root = Path(candidate["tools"]["pythonTestRuntime"]["root"])
    root.chmod(0o555)

    descriptor, error = manifest.secure_python_test_runtime_tree_descriptor(root)

    assert error is None
    assert descriptor is not None
    assert descriptor["rootMode"] == 0o555


def test_backend_snapshot_descriptor_rejects_named_root_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "backend"
    root.mkdir()
    source = root / "source.txt"
    source.write_text("original", encoding="utf-8")
    source.chmod(0o444)
    root.chmod(0o555)
    original_lstat = Path.lstat
    calls = 0

    def replaced_lstat(path: Path):
        nonlocal calls
        observed = original_lstat(path)
        if path == root:
            calls += 1
            if calls > 1:
                values = list(observed)
                values[1] += 1
                return os.stat_result(values)
        return observed

    monkeypatch.setattr(Path, "lstat", replaced_lstat)

    descriptor, error = manifest.secure_backend_snapshot_tree_descriptor(root)

    assert descriptor is None
    assert error == "changed"


def test_python_runtime_authority_probe_requires_write_denying_sandbox(
    candidate: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = candidate["tools"]["pythonTestRuntime"]
    monkeypatch.setattr(
        manifest, "_sandboxed_python_runtime_probe_command", lambda command: command,
    )

    assert manifest.python_test_runtime_authorized(descriptor) is False

    observed, error = manifest.secure_python_test_runtime_tree_descriptor(
        Path(descriptor["root"]),
    )
    assert error is None
    assert observed == descriptor["treeDigest"]


def test_python_test_runtime_descriptor_rejects_writable_child(candidate: dict) -> None:
    root = Path(candidate["tools"]["pythonTestRuntime"]["root"])
    root.chmod(0o755)
    child = root / "writable"
    child.write_bytes(b"unsafe")
    child.chmod(0o777)
    root.chmod(0o555)

    descriptor, error = manifest.secure_python_test_runtime_tree_descriptor(root)

    assert descriptor is None
    assert error == "tree"


@pytest.mark.parametrize("field", ["prefix", "basePrefix", "stdlib", "path", "modules"])
def test_python_runtime_authority_rejects_external_path(tmp_path: Path, field: str) -> None:
    root = tmp_path / "runtime"
    root.mkdir()
    payload = {
        "executable": str(root / "bin/python3.11"),
        "prefix": str(root), "basePrefix": str(root),
        "execPrefix": str(root), "baseExecPrefix": str(root),
        "stdlib": str(root / "lib/python3.11"),
        "path": [str(root / "lib/python3.11")],
        "modules": [str(root / "lib/python3.11/site-packages/pytest/__init__.py")],
    }
    payload[field] = ["/opt/homebrew/escape"] if field in {"path", "modules"} else "/opt/homebrew/escape"

    assert manifest._python_runtime_authority_payload_valid(root, payload) is False


def test_python_runtime_library_authority_rejects_non_system_absolute_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    staged = tmp_path / "staged"
    for root in (source, staged):
        binary = root / "bin/python3.11"
        library = root / "lib/libpython.dylib"
        binary.parent.mkdir(parents=True)
        library.parent.mkdir(parents=True)
        binary.write_bytes(b"\xcf\xfa\xed\xfe" + b"binary")
        library.write_bytes(b"library")

    def fake_otool(command, **_kwargs):
        target = command[-1]
        if command[1] == "-l":
            return manifest.BoundedCommandResult(0, f"{target}:\n", None)
        return manifest.BoundedCommandResult(
            0, f"{target}:\n\t{source / 'lib/libpython.dylib'} (compatibility version 1.0.0)\n", None,
        )

    monkeypatch.setattr(manifest, "run_bounded_command", fake_otool)
    monkeypatch.setattr(manifest.sys, "platform", "darwin")

    assert manifest._python_runtime_library_authority(source, source / "bin/python3.11") is False
    assert manifest._python_runtime_library_authority(staged, staged / "bin/python3.11") is False


@pytest.mark.parametrize(
    "section,field,replacement,reason",
    [
        ("images", "lessonStudioBackend", {}, "images.lessonStudioBackend.keys"),
        ("firmware", "appSha256", "f" * 64, "firmware.appSha256"),
        ("firmware", "elfSha256", "f" * 64, "firmware.elfSha256"),
        ("database", "engineImageId", "sha256:" + "f" * 64, "database.engineImageId"),
        ("database", "migrationHeadSha256", "f" * 64, "database.migrationHeadSha256"),
        ("tools", "espIdf", {"version": "v0.0.0", "commit": "a" * 40}, "tools.espIdf.version"),
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


def test_candidate_rejects_mutated_node_package_manager_entrypoint(candidate: dict) -> None:
    entrypoint = Path(candidate["tools"]["node"]["backend"]["npm"]["entrypoint"])
    entrypoint.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")

    assert "tools.node.backend.npm.sha256" in validate_candidate(candidate, now=NOW)


def test_candidate_rejects_mutated_node_package_dependency(candidate: dict) -> None:
    descriptor = candidate["tools"]["node"]["backend"]
    dependency = Path(descriptor["packageRoot"]) / "lib/runtime.js"
    dependency.parent.mkdir(parents=True, exist_ok=True)
    package_tree = manifest.secure_node_package_tree_descriptor(Path(descriptor["packageRoot"]))
    assert package_tree is not None
    descriptor["packageRootMode"] = package_tree["rootMode"]
    descriptor["packageTreeSha256"] = package_tree["sha256"]
    dependency.write_text("module.exports = 'changed';\n", encoding="utf-8")

    assert "tools.node.backend.packageTreeSha256" in validate_candidate(candidate, now=NOW)


def test_node_package_tree_descriptor_binds_secure_root_mode(candidate: dict) -> None:
    descriptor = candidate["tools"]["node"]["backend"]
    package_root = Path(descriptor["packageRoot"])
    original = manifest.secure_node_package_tree_descriptor(package_root)
    assert original is not None
    assert original["schema"] == manifest.NODE_PACKAGE_TREE_SCHEMA
    assert original["rootMode"] == 0o755

    package_root.chmod(0o700)
    private = manifest.secure_node_package_tree_descriptor(package_root)
    assert private is not None
    assert private["rootMode"] == 0o700
    assert private["sha256"] != original["sha256"]

    package_root.chmod(0o777)
    assert manifest.secure_node_package_tree_descriptor(package_root) is None


def test_node_package_tree_descriptor_rejects_root_mode_race(
    candidate: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = Path(candidate["tools"]["node"]["backend"]["packageRoot"])
    original = manifest._secure_browser_bundle_descriptor_fd

    def scan_then_change_mode(root_fd: int, root_metadata: os.stat_result):
        descriptor = original(root_fd, root_metadata)
        package_root.chmod(0o700)
        return descriptor

    monkeypatch.setattr(manifest, "_secure_browser_bundle_descriptor_fd", scan_then_change_mode)

    assert manifest.secure_node_package_tree_descriptor(package_root) is None


def test_node_package_tree_descriptor_binds_open_root_across_ancestor_aba(
    candidate: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = Path(candidate["tools"]["node"]["backend"]["packageRoot"])
    expected = manifest.secure_node_package_tree_descriptor(package_root)
    assert expected is not None
    ancestor = package_root.parents[2]
    moved = ancestor.with_name(ancestor.name + "-moved")
    original = manifest._secure_browser_bundle_descriptor_fd
    swapped = False

    def swap_ancestor_during_scan(root_fd: int, root_metadata: os.stat_result):
        nonlocal swapped
        if not swapped:
            swapped = True
            ancestor.rename(moved)
            replacement = ancestor / package_root.relative_to(ancestor)
            replacement.mkdir(parents=True)
            (replacement / "replacement.js").write_text("replacement", encoding="utf-8")
            descriptor = original(root_fd, root_metadata)
            shutil.rmtree(ancestor)
            moved.rename(ancestor)
            return descriptor
        return original(root_fd, root_metadata)

    monkeypatch.setattr(manifest, "_secure_browser_bundle_descriptor_fd", swap_ancestor_during_scan)

    assert manifest.secure_node_package_tree_descriptor(package_root) == expected
    assert swapped is True


def test_node_package_tree_descriptor_rejects_invalid_filename_bytes(
    candidate: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = Path(candidate["tools"]["node"]["backend"]["packageRoot"])
    monkeypatch.setattr(
        manifest, "_secure_browser_bundle_descriptor_fd",
        lambda *_args: (_ for _ in ()).throw(
            UnicodeEncodeError("utf-8", "\udcff", 0, 1, "surrogate filename"),
        ),
    )

    assert manifest.secure_node_package_tree_descriptor(package_root) is None


def test_candidate_rejects_node_package_root_mode_drift(candidate: dict) -> None:
    descriptor = candidate["tools"]["node"]["backend"]
    package_root = Path(descriptor["packageRoot"])
    package_root.chmod(0o700)

    assert "tools.node.backend.packageRootMode" in validate_candidate(candidate, now=NOW)


def test_candidate_rejects_node_package_root_outside_canonical_install(
    candidate: dict, tmp_path: Path,
) -> None:
    descriptor = candidate["tools"]["node"]["backend"]
    package_root = tmp_path / "npm"
    package_root.mkdir()
    (package_root / "package.json").write_text("{}\n", encoding="utf-8")
    package_tree = manifest.secure_node_package_tree_descriptor(package_root)
    assert package_tree is not None
    descriptor["packageRoot"] = str(package_root)
    descriptor["packageTreeSha256"] = package_tree["sha256"]

    assert "tools.node.backend.packageRoot" in validate_candidate(candidate, now=NOW)


@pytest.mark.parametrize("unsafe", ["symlink", "fifo"])
def test_node_package_tree_descriptor_rejects_unmodeled_entry_types(
    tmp_path: Path, unsafe: str,
) -> None:
    root = tmp_path / "npm"
    root.mkdir()
    (root / "package.json").write_text("{}\n", encoding="utf-8")
    if unsafe == "symlink":
        (root / "unsafe").symlink_to("package.json")
    else:
        os.mkfifo(root / "unsafe")

    assert manifest.secure_node_package_tree_descriptor(root) is None


def test_candidate_rejects_hashed_node_entrypoint_outside_canonical_install(candidate: dict, tmp_path: Path) -> None:
    arbitrary = tmp_path / "arbitrary-npm.js"
    arbitrary.write_text("process.exit(0)\n", encoding="utf-8")
    npm = candidate["tools"]["node"]["backend"]["npm"]
    npm.update(entrypoint=str(arbitrary), sha256=hashlib.sha256(arbitrary.read_bytes()).hexdigest())

    assert "tools.node.backend.npm.entrypoint" in validate_candidate(candidate, now=NOW)


def test_candidate_rejects_image_role_and_oci_provenance_mutation(candidate: dict) -> None:
    candidate["images"]["lessonStudioBackend"]["reference"] = candidate["images"]["lessonStudioWeb"]["reference"]

    assert "images.lessonStudioBackend.reference" in validate_candidate(candidate, now=NOW)


@pytest.mark.parametrize("field,value", [
    ("board", "wrong"), ("target", "esp32"), ("createdAt", "2000-01-01T00:00:00Z"),
])
def test_candidate_rejects_firmware_platform_or_stale_evidence(
    candidate: dict, field: str, value: str,
) -> None:
    path = Path(candidate["firmware"]["evidenceManifestPath"])
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence[field] = value
    path.write_text(json.dumps(evidence), encoding="utf-8")
    candidate["firmware"]["evidenceManifestSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    assert "firmware.evidenceManifestPath" in validate_candidate(candidate, now=NOW)


def test_candidate_rejects_firmware_free_percent_drift(candidate: dict) -> None:
    path = Path(candidate["firmware"]["evidenceManifestPath"])
    evidence = json.loads(path.read_text(encoding="utf-8"))
    evidence["partition"]["freePercent"] += 0.1
    path.write_text(json.dumps(evidence), encoding="utf-8")
    candidate["firmware"]["evidenceManifestSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    assert "firmware.evidenceManifestPath" in validate_candidate(candidate, now=NOW)


def test_candidate_rejects_esp_idf_checkout_drift(candidate: dict) -> None:
    root = Path(candidate["tools"]["espIdf"]["root"])
    (root / "tools/cmake/version.cmake").write_text("set(IDF_VERSION_MAJOR 0)\n", encoding="utf-8")

    assert "tools.espIdf.identity" in validate_candidate(candidate, now=NOW)


def test_candidate_schema_upgrade_is_deterministic_without_writing_input(candidate: dict) -> None:
    legacy = json.loads(json.dumps(candidate))
    legacy["firmware"].pop("evidenceManifestPath")
    legacy["firmware"].pop("evidenceManifestSha256")
    legacy["tools"]["node"] = {
        key: descriptor["version"] for key, descriptor in candidate["tools"]["node"].items()
    }
    legacy["tools"]["espIdf"] = candidate["tools"]["espIdf"]["version"]
    before = json.loads(json.dumps(legacy))

    first = manifest.upgrade_candidate_schema(
        legacy, node_executables={
            key: descriptor["executable"] for key, descriptor in candidate["tools"]["node"].items()
        }, esp_idf_root=candidate["tools"]["espIdf"]["root"],
    )
    second = manifest.upgrade_candidate_schema(
        legacy, node_executables={
            key: descriptor["executable"] for key, descriptor in candidate["tools"]["node"].items()
        }, esp_idf_root=candidate["tools"]["espIdf"]["root"],
    )

    assert legacy == before
    assert first == second == candidate


def test_candidate_requires_latest_committed_and_runtime_up_migration(candidate: dict) -> None:
    backend = Path(candidate["repositories"]["backend"]["path"])
    older = backend / "src/database/migrations/126_older.sql"
    older.write_text("SELECT 126;\n", encoding="utf-8")
    _git(backend, "add", str(older.relative_to(backend)))
    _git(backend, "commit", "-m", "older migration")
    candidate["repositories"]["backend"] = _repository(backend)
    candidate["database"]["migrationHead"] = older.name
    candidate["database"]["migrationHeadSha256"] = hashlib.sha256(older.read_bytes()).hexdigest()

    assert "database.migrationHead" in validate_candidate(candidate, now=NOW)

    canonical = backend / "src/database/migrations/127_shared_visual_layered_cinematic_compatibility.sql"
    candidate["database"]["migrationHead"] = canonical.name
    candidate["database"]["migrationHeadSha256"] = hashlib.sha256(canonical.read_bytes()).hexdigest()
    later = backend / "src/database/migrations/128_later.sql"
    later.write_text("SELECT 128;\n", encoding="utf-8")
    candidate["repositories"]["backend"]["dirtyExceptions"] = [{
        "path": str(later.relative_to(backend)),
        "sha256": hashlib.sha256(later.read_bytes()).hexdigest(),
    }]
    assert "database.migrationHead" in validate_candidate(candidate, now=NOW)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda evidence: evidence.update(status="FAIL"),
        lambda evidence: evidence.update(profile="debug"),
        lambda evidence: evidence["reproducibility"].update(appByteIdentical=False),
        lambda evidence: evidence["reproducibility"].update(independentCleanBuilds=1),
        lambda evidence: evidence["config"].update(productionArtifactAudit="FAIL"),
        lambda evidence: evidence["safety"].update(flashed=True),
        lambda evidence: evidence["toolchain"].update(espIdfCommit="invalid"),
    ],
)
def test_candidate_requires_production_firmware_evidence_semantics(candidate: dict, mutate) -> None:
    path = Path(candidate["firmware"]["evidenceManifestPath"])
    evidence = json.loads(path.read_text(encoding="utf-8"))
    mutate(evidence)
    path.write_text(json.dumps(evidence), encoding="utf-8")
    candidate["firmware"]["evidenceManifestSha256"] = hashlib.sha256(path.read_bytes()).hexdigest()

    reasons = validate_candidate(candidate, now=NOW)
    assert any(reason in reasons for reason in ("firmware.evidenceManifestPath", "tools.espIdf.commit"))


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

    def invalid_root_entry(directory):
        if isinstance(directory, int):
            opened = os.fstat(directory)
            if (opened.st_dev, opened.st_ino) == (metadata.st_dev, metadata.st_ino):
                return [InvalidByteEntry()]
        return original_scandir(directory)

    monkeypatch.setattr(
        manifest.os, "scandir", invalid_root_entry,
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
