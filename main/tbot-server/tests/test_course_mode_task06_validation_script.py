from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "course_mode_task06_runtime_validation.py"


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
