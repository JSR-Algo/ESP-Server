#!/usr/bin/env python3
"""Emit reproducible Task 06 cross-repository runtime and soak evidence."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import tracemalloc
from pathlib import Path
from typing import Any

from PIL import Image


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
EXPECTED_FIXTURE_SHA = "05e18ae61aee0660c653a9386854552a23f90c8a1f8cfb9e7ff4e15d1d277470"
EXPECTED_SEMANTIC = "cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264"
EXPECTED_LAYOUT = "e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c"
EXPECTED_ESP_SHA = "7e2628a9b9b4c3c7bbde4b426455700a4e0b7268"
EXPECTED_FIRMWARE_SHA = "d47174daebe17b9c1a9d1a1eb506711a57cd3512"
EXPECTED_BACKEND_SHA = "657474ff3b58fba2c3c31f2978d53370ffad8b11"
MAX_RETAINED_HEAP_GROWTH_BYTES = 1024 * 1024
ALLOWED_ESP_EVIDENCE_PATHS = (
    ".gitattributes",
    ".gitignore",
    "docs/qa/ad-hoc/2026-08-22-course-mode-task06-runtime-validation.md",
    "docs/qa/artifacts/2026-08-22-course-mode-task06/",
    "main/tbot-server/scripts/course_mode_task06_runtime_validation.py",
    "main/tbot-server/tests/test_course_mode_e2e_journeys.py",
    "main/tbot-server/tests/test_course_mode_task06_validation_script.py",
)
ALLOWED_ESP_DIRTY_PATHS = (
    "docs/qa/artifacts/2026-08-22-course-mode-task06/",
)
ALLOWED_ESP_IGNORED_GLOBS = (
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    "tmp/server.log",
)
ALLOWED_BACKEND_EVIDENCE_PATHS = (
    "src/lessons/authoring/esptft-publish-budget.logic.spec.ts",
    "src/lessons/authoring/lesson-authoring.service.extra-coverage.spec.ts",
    "src/lessons/lesson-manifest.per-profile-checksum.spec.ts",
    "src/lessons/lesson-manifest.shared-assets.spec.ts",
)
ALLOWED_FIRMWARE_EVIDENCE_PATHS = (
    "tests/test_lesson_dispatch_backward_compat.py",
    "tests/test_realtime_voice_state.py",
)
ALLOWED_FIRMWARE_IGNORED_GLOBS = (
    "__pycache__/**",
    "**/__pycache__/**",
    ".pytest_cache/**",
    "**/.pytest_cache/**",
    "managed_components/**",
)
ALLOWED_BACKEND_IGNORED_GLOBS = (
    "node_modules/**",
    "dist/**",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def candidate_revision(
    root: Path,
    expected_sha: str,
    allowed_paths: tuple[str, ...],
    *,
    allowed_dirty_paths: tuple[str, ...] = (),
    allowed_untracked_paths: tuple[str, ...] = (),
    allowed_ignored_globs: tuple[str, ...] = (),
) -> dict[str, Any]:
    head = git_head(root)
    status_rows = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    dirty = sorted(set(
        subprocess.run(
            ["git", "diff", "--name-only", "--no-renames"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        + subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--no-renames"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.splitlines()
    ))
    untracked = [row[3:] for row in status_rows if row.startswith("?? ")]
    ignored = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", expected_sha, head], cwd=root,
        check=False, capture_output=True, text=True,
    ).returncode == 0
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", f"{expected_sha}..{head}"], cwd=root,
        check=True, capture_output=True, text=True,
    ).stdout.splitlines() if ancestor else []
    unexpected = [
        path for path in changed
        if not any(path == allowed or (allowed.endswith("/") and path.startswith(allowed)) for allowed in allowed_paths)
    ]
    unexpected_dirty = [
        path for path in dirty
        if not any(
            path == allowed or (allowed.endswith("/") and path.startswith(allowed))
            for allowed in allowed_dirty_paths
        )
    ]
    unexpected_untracked = [
        path for path in untracked
        if not any(
            path == allowed or (allowed.endswith("/") and path.startswith(allowed))
            for allowed in allowed_untracked_paths
        )
    ]
    unexpected_ignored = [
        path for path in ignored
        if not any(fnmatch.fnmatch(path, pattern) for pattern in allowed_ignored_globs)
    ]
    return {
        "headSha": head,
        "expectedSha": expected_sha,
        "expectedIsAncestor": ancestor,
        "trackedWorktreeClean": not dirty,
        "dirtyTrackedPaths": dirty,
        "unexpectedDirtyTrackedPaths": unexpected_dirty,
        "untrackedPaths": untracked,
        "unexpectedUntrackedPaths": unexpected_untracked,
        "ignoredPathCount": len(ignored),
        "unexpectedIgnoredPaths": unexpected_ignored,
        "trackedChanges": changed,
        "unexpectedTrackedChanges": unexpected,
        "runtimeTreeMatchesFrozenCandidate": (
            ancestor
            and not unexpected_dirty
            and not unexpected_untracked
            and not unexpected_ignored
            and not unexpected
        ),
    }


def resource_gate_passes(resources: dict[str, int]) -> bool:
    return (
        resources["threadDelta"] == 0
        and resources["fdDelta"] == 0
        and resources["heapCurrentDeltaBytes"] <= MAX_RETAINED_HEAP_GROWTH_BYTES
    )


def load_journey_module():
    path = ROOT / "tests" / "test_course_mode_e2e_journeys.py"
    spec = importlib.util.spec_from_file_location("task06_journeys", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("journey module unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fd_count() -> int:
    path = Path("/dev/fd")
    return len(list(path.iterdir())) if path.exists() else 0


def visual_evidence(backend_root: Path) -> dict[str, Any]:
    captures = sorted((backend_root / "src/lessons/fixtures/course-mode/pilot/v1/captures").glob("*.png"))
    samples = []
    for capture in captures:
        with Image.open(capture) as image:
            samples.append({
                "path": capture.relative_to(backend_root).as_posix(),
                "sha256": sha256(capture),
                "size": list(image.size),
            })
            if image.size != (480, 320):
                raise AssertionError(f"unexpected capture size: {capture}")
    if len(captures) != 24:
        raise AssertionError(f"expected 24 start/middle/end captures, found {len(captures)}")
    return {"captureCount": len(captures), "samples": samples}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--firmware-root", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=250)
    parser.add_argument("--min-duration-seconds", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")

    esp_fixture = ROOT / "tests/fixtures/course-mode/course-mode-pilot-cat-ball.json"
    backend_fixture = args.backend_root / "src/lessons/fixtures/course-mode/course-mode-pilot-cat-ball.json"
    firmware_fixture = args.firmware_root / "tests/fixtures/course-mode/course-mode-pilot-cat-ball.json"
    fixture_hashes = [sha256(path) for path in (esp_fixture, backend_fixture, firmware_fixture)]
    pilot_path = args.backend_root / "src/lessons/fixtures/course-mode/pilot/v1/pilot.json"
    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    identity = pilot["identity"]
    release = pilot["release"]
    revisions = {
        "esp": candidate_revision(
            ROOT,
            EXPECTED_ESP_SHA,
            ALLOWED_ESP_EVIDENCE_PATHS,
            allowed_dirty_paths=ALLOWED_ESP_DIRTY_PATHS,
            allowed_ignored_globs=ALLOWED_ESP_IGNORED_GLOBS,
        ),
        "firmware": candidate_revision(
            args.firmware_root,
            EXPECTED_FIRMWARE_SHA,
            ALLOWED_FIRMWARE_EVIDENCE_PATHS,
            allowed_ignored_globs=ALLOWED_FIRMWARE_IGNORED_GLOBS,
        ),
        "backend": candidate_revision(
            args.backend_root,
            EXPECTED_BACKEND_SHA,
            ALLOWED_BACKEND_EVIDENCE_PATHS,
            allowed_ignored_globs=ALLOWED_BACKEND_IGNORED_GLOBS,
        ),
    }
    candidate_shas = {
        "espSha": revisions["esp"]["headSha"],
        "firmwareSha": revisions["firmware"]["headSha"],
        "backendSha": revisions["backend"]["headSha"],
    }

    journey_module = load_journey_module()
    journeys = journey_module.JOURNEYS
    threads_before = threading.active_count()
    fds_before = fd_count()
    tracemalloc.start()
    heap_before = tracemalloc.get_traced_memory()[0]
    started = time.perf_counter()
    failures = []
    iteration = 0
    while iteration < args.iterations or time.perf_counter() - started < args.min_duration_seconds:
        for row in journeys:
            try:
                result = journey_module._run_full_session(row)
                if (
                    result["initialOutcome"] != journey_module.EXPECTED_OUTCOMES[row["name"]]
                    or result["finalState"] != "CLOSING"
                    or result["steps"] < 4
                ):
                    failures.append({"iteration": iteration, "journey": row["name"], "result": result})
                    break
            except Exception as exc:  # The evidence records the first deterministic failure.
                failures.append({
                    "iteration": iteration,
                    "journey": row["name"],
                    "errorType": type(exc).__name__,
                    "error": str(exc),
                })
                break
        if failures:
            break
        iteration += 1
    duration = time.perf_counter() - started
    heap_current, heap_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    resources = {
        "threadBefore": threads_before,
        "threadAfter": threading.active_count(),
        "threadDelta": threading.active_count() - threads_before,
        "fdBefore": fds_before,
        "fdAfter": fd_count(),
        "fdDelta": fd_count() - fds_before,
        "heapCurrentDeltaBytes": heap_current - heap_before,
        "heapPeakBytes": heap_peak,
        "maximumRetainedHeapGrowthBytes": MAX_RETAINED_HEAP_GROWTH_BYTES,
    }
    candidate_ok = (
        len(set(fixture_hashes)) == 1
        and fixture_hashes[0] == EXPECTED_FIXTURE_SHA
        and identity["semanticChecksum"] == EXPECTED_SEMANTIC
        and identity["layoutChecksum"] == EXPECTED_LAYOUT
        and identity["rendererId"] == "teebot-lesson-renderer.v4"
        and release == {"status": "draft", "published": False, "assigned": False, "productionEnabled": False}
        and all(revision["runtimeTreeMatchesFrozenCandidate"] for revision in revisions.values())
    )
    visuals = visual_evidence(args.backend_root)
    passed = candidate_ok and not failures and resource_gate_passes(resources)
    report = {
        "schemaVersion": 1,
        "generatedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": "PASS" if passed else "FAIL",
        "candidate": {
            **candidate_shas,
            "expectedShas": {
                "espSha": EXPECTED_ESP_SHA,
                "firmwareSha": EXPECTED_FIRMWARE_SHA,
                "backendSha": EXPECTED_BACKEND_SHA,
            },
            "runtimeTreesMatchFrozenCandidate": all(
                revision["runtimeTreeMatchesFrozenCandidate"] for revision in revisions.values()
            ),
            "revisions": revisions,
            "fixtureSha256": fixture_hashes[0],
            "fixtureCopiesEqual": len(set(fixture_hashes)) == 1,
            "semanticChecksum": identity["semanticChecksum"],
            "layoutChecksum": identity["layoutChecksum"],
            "rendererId": identity["rendererId"],
            "releaseState": release,
        },
        "journeys": {"journeyCount": len(journeys), "names": [row["name"] for row in journeys]},
        "soak": {
            "minimumIterations": args.iterations,
            "iterations": iteration,
            "sessions": iteration * len(journeys),
            "durationSeconds": round(duration, 6),
            "failures": len(failures),
            "failureDetails": failures,
        },
        "resources": resources,
        "visuals": visuals,
        "dataPolicy": "synthetic-no-child-data",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": report["verdict"], "sessions": report["soak"]["sessions"], "durationSeconds": report["soak"]["durationSeconds"]}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
