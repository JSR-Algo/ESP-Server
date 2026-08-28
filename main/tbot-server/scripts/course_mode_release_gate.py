#!/usr/bin/env python3
"""Run candidate-bound Course Mode production-readiness lanes."""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

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
}
MAX_LANE_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_REPORT_BYTES = 1024 * 1024
MODES = ("quick", "full", "live-db", "physical-preflight")


@dataclass(frozen=True)
class Lane:
    name: str
    repository: str
    relative_cwd: str
    command: tuple[str, ...]
    timeout_sec: float
    required_environment: str | None = None


def _lane(
    name: str,
    repository: str,
    relative_cwd: str,
    command: tuple[str, ...],
    timeout_sec: float = 900.0,
    required_environment: str | None = None,
) -> Lane:
    return Lane(name, repository, relative_cwd, command, timeout_sec, required_environment)


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
    _lane("backend-tests", "backend", ".", ("npm", "test"), 1800.0),
    _lane("backend-build", "backend", ".", ("npm", "run", "build")),
    _lane(
        "backend-curriculum-verifier", "backend", ".",
        ("node", "scripts/verify-course-mode-curriculum.mjs"),
    ),
    _lane("admin-logic", "adminEsp", "main/manager-web", ("npm", "run", "test:course-admin-ui")),
    _lane("admin-browser", "adminEsp", "main/manager-web", ("npm", "run", "test:lesson-studio")),
    _lane("admin-build", "adminEsp", "main/manager-web", ("npm", "run", "build"), 1200.0),
    _lane(
        "admin-course-mode-playwright", "adminEsp", "main/manager-web",
        ("npm", "run", "test:e2e:course-mode", "--", "--project=chromium"),
        1200.0, "COURSE_MODE_ADMIN_E2E_READY",
    ),
    _lane(
        "esp-course-mode-full", "adminEsp", "main/tbot-server",
        (
            "python3", "-m", "pytest", "-q", "tests/test_course_mode_curriculum_e2e.py",
            "tests/test_course_mode_runtime_integration.py", "tests/test_course_mode_contract.py",
            "tests/test_course_mode_e2e_journeys.py", "tests/test_course_mode_forwarder.py",
            "tests/test_course_mode_runtime_compatibility.py",
        ),
        1800.0,
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
    1800.0, "COURSE_MODE_V2_TEST_DATABASE_URL",
)


PHYSICAL_PREFLIGHT_LANE = _lane(
    "physical-tft-preflight", "adminEsp", "main/tbot-server",
    ("python3", "-m", "pytest", "-q", "tests/test_course_mode_physical_tft_preflight.py"),
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


def _child_environment(candidate: dict, source: Mapping[str, str], lane: Lane) -> dict[str, str]:
    environment = dict(BASE_ENVIRONMENT)
    environment.update({
        "COURSE_MODE_BACKEND_ROOT": candidate["repositories"]["backend"]["path"],
        "COURSE_MODE_ADMIN_ESP_ROOT": candidate["repositories"]["adminEsp"]["path"],
        "COURSE_MODE_FIRMWARE_ROOT": candidate["repositories"]["firmware"]["path"],
        "COURSE_MODE_CANDIDATE_ID": candidate["candidateId"],
        "TBOT_BACKEND_CONTRACTS_DIR": str(
            Path(candidate["repositories"]["backend"]["path"]) / "contracts"
        ),
    })
    if lane.required_environment and source.get(lane.required_environment):
        environment[lane.required_environment] = source[lane.required_environment]
    return environment


def _write_report_atomic(path: Path, report: dict) -> bool:
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_REPORT_BYTES or not path.is_absolute():
        return False
    try:
        parent = path.parent.resolve(strict=True)
        if not parent.is_dir() or path.parent != parent:
            return False
        if path.exists() or path.is_symlink():
            current = path.lstat()
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                return False
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return True
    except OSError:
        try:
            os.unlink(temporary)
        except (OSError, UnboundLocalError):
            pass
        return False


def run_gate(
    candidate_path: Path,
    mode: str,
    *,
    lanes: Sequence[Lane] | None = None,
    source_environment: Mapping[str, str] | None = None,
    max_output_bytes: int = MAX_LANE_OUTPUT_BYTES,
    report_path: Path | None = None,
) -> dict:
    candidate = _load_candidate(candidate_path)
    candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    if candidate is None or validate_candidate(candidate):
        report = _blocked(candidate_id if isinstance(candidate_id, str) else None, "candidate")
    elif mode not in MODES or type(max_output_bytes) is not int or max_output_bytes <= 0:
        report = _blocked(candidate_id, "configuration")
    else:
        selected = tuple(lanes) if lanes is not None else lanes_for_mode(mode)
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
            source = source_environment if source_environment is not None else os.environ
            for lane in selected:
                if not _candidate_matches(candidate):
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if lane.required_environment and not source.get(lane.required_environment):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                command = _resolve_command(lane.command)
                if command is None:
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                root = Path(repositories[lane.repository]["path"])
                cwd = root / lane.relative_cwd
                try:
                    resolved_cwd = cwd.resolve(strict=True)
                    resolved_cwd.relative_to(root)
                except (OSError, ValueError):
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                started = time.monotonic_ns()
                result = run_bounded_command(
                    list(command), cwd=resolved_cwd, timeout_sec=lane.timeout_sec,
                    max_output_bytes=max_output_bytes,
                    env=_child_environment(candidate, source, lane),
                )
                duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
                exit_code = None if result.error else result.returncode
                report["lanes"].append({
                    "name": lane.name, "exitCode": exit_code, "durationMs": duration_ms,
                })
                if result.error or result.returncode != 0:
                    report["verdict"] = "FAIL"
                    report["failedLane"] = lane.name
                    break
    if report_path is not None and not _write_report_atomic(report_path, report):
        return _blocked(report.get("candidateId"), "report")
    return report


def _emit(report: dict) -> None:
    print(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--mode", choices=MODES, default="quick")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = run_gate(args.candidate, args.mode, report_path=args.report)
    _emit(report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
