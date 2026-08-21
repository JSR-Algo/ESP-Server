from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "course_mode_task06_runtime_validation.py"


def load_driver():
    spec = importlib.util.spec_from_file_location("task06_runtime_driver", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def test_candidate_revision_rejects_dirty_validation_driver(tmp_path: Path) -> None:
    driver = load_driver()
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "task06@example.invalid")
    git(repo, "config", "user.name", "Task 06")
    script = repo / "scripts" / "validator.py"
    script.parent.mkdir()
    script.write_text("print('v1')\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    script.write_text("print('v2')\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "add validator")
    script.write_text("print('uncommitted')\n", encoding="utf-8")

    revision = driver.candidate_revision(
        repo,
        base,
        ("scripts/validator.py",),
        allowed_dirty_paths=(),
    )

    assert revision["unexpectedTrackedChanges"] == []
    assert revision["unexpectedDirtyTrackedPaths"] == ["scripts/validator.py"]
    assert revision["runtimeTreeMatchesFrozenCandidate"] is False


def test_resource_gate_rejects_retained_heap_growth() -> None:
    driver = load_driver()

    assert driver.resource_gate_passes({
        "threadDelta": 0,
        "fdDelta": 0,
        "heapCurrentDeltaBytes": driver.MAX_RETAINED_HEAP_GROWTH_BYTES + 1,
    }) is False


def test_candidate_revision_rejects_untracked_runtime_file(tmp_path: Path) -> None:
    driver = load_driver()
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "task06@example.invalid")
    git(repo, "config", "user.name", "Task 06")
    tracked = repo / "runtime.py"
    tracked.write_text("VALUE = 'tracked'\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    (repo / "shadow_runtime.py").write_text("VALUE = 'untracked'\n", encoding="utf-8")

    revision = driver.candidate_revision(repo, base, ())

    assert revision["untrackedPaths"] == ["shadow_runtime.py"]
    assert revision["unexpectedUntrackedPaths"] == ["shadow_runtime.py"]
    assert revision["runtimeTreeMatchesFrozenCandidate"] is False


def test_candidate_revision_rejects_ignored_runtime_config(tmp_path: Path) -> None:
    driver = load_driver()
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "task06@example.invalid")
    git(repo, "config", "user.name", "Task 06")
    (repo / ".gitignore").write_text(".config.yaml\n", encoding="utf-8")
    (repo / "runtime.py").write_text("VALUE = 'tracked'\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    (repo / ".config.yaml").write_text("runtime: overridden\n", encoding="utf-8")

    revision = driver.candidate_revision(repo, base, ())

    assert revision["ignoredPathCount"] == 1
    assert revision["unexpectedIgnoredPaths"] == [".config.yaml"]
    assert revision["runtimeTreeMatchesFrozenCandidate"] is False


def test_task06_driver_emits_cross_repository_soak_evidence(tmp_path: Path) -> None:
    backend_root = os.environ.get("TASK06_BACKEND_ROOT")
    firmware_root = os.environ.get("TASK06_FIRMWARE_ROOT")
    if not backend_root or not firmware_root:
        pytest.skip("Task 06 cross-repository roots are not configured")
    output = tmp_path / "runtime.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--backend-root",
            backend_root,
            "--firmware-root",
            firmware_root,
            "--iterations",
            "2",
            "--min-duration-seconds",
            "0.01",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert report["candidate"]["fixtureCopiesEqual"] is True
    assert report["candidate"]["runtimeTreesMatchFrozenCandidate"] is True
    assert all(
        not revision["unexpectedDirtyTrackedPaths"]
        for revision in report["candidate"]["revisions"].values()
    )
    assert report["candidate"]["releaseState"] == {
        "assigned": False,
        "productionEnabled": False,
        "published": False,
        "status": "draft",
    }
    assert report["journeys"]["journeyCount"] == 22
    assert report["soak"]["sessions"] >= 44
    assert report["soak"]["durationSeconds"] >= 0.01
    assert report["soak"]["failures"] == 0
    assert report["resources"]["threadDelta"] == 0
    assert report["resources"]["fdDelta"] == 0
    assert report["visuals"]["captureCount"] == 24
