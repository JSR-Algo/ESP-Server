#!/usr/bin/env python3
"""Run candidate-bound Course Mode production-readiness lanes."""

from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import json
import math
import os
import secrets
import shutil
import stat
import tempfile
import time
import xml.etree.ElementTree as ET
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
_candidate_git = _manifest._git


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
COURSE_MODE_SOFTWARE_TESTS = "@course-mode-software-tests"
PLAYWRIGHT_CONTRACT_PATH = "main/manager-web/course-mode.playwright.contract.json"
PLAYWRIGHT_PROJECTS = (
    "course-mode-chromium-desktop",
    "course-mode-webkit-desktop",
    "course-mode-chromium-mobile",
    "course-mode-webkit-mobile",
)
PLAYWRIGHT_PROJECT_CONTRACT = (
    {"name": "course-mode-chromium-desktop", "device": "Desktop Chrome", "viewport": {"width": 1440, "height": 900}},
    {"name": "course-mode-webkit-desktop", "device": "Desktop Safari", "viewport": {"width": 1440, "height": 900}},
    {"name": "course-mode-chromium-mobile", "device": "Pixel 7", "viewport": {"width": 390, "height": 844}},
    {"name": "course-mode-webkit-mobile", "device": "iPhone 13", "viewport": {"width": 390, "height": 844}},
)
PLAYWRIGHT_FIXED_CONTRACT = {
    "testDir": "./e2e/lesson-studio",
    "globalSetup": "./e2e/lesson-studio/global-setup.cjs",
    "outputDir": "./output/playwright-e2e/results",
    "timeout": 60000,
    "expectTimeout": 10000,
    "fullyParallel": False,
    "workers": 1,
    "retries": 0,
    "reporter": [["list"], ["html", {"outputFolder": "./output/playwright-e2e/report", "open": "never"}]],
    "use": {
        "baseUrlHelper": "lessonStudioWebOrigin",
        "trace": "retain-on-failure",
        "screenshot": "only-on-failure",
        "video": "retain-on-failure",
        "serviceWorkers": "block",
    },
}
SAFE_PHYSICAL_CONTRACT_TESTS = {
    "test_course_mode_physical_tft_compose.py",
    "test_course_mode_physical_tft_ledger_validate.py",
    "test_course_mode_physical_tft_preflight.py",
    "test_course_mode_physical_tft_receipt_verify.py",
}


@dataclass(frozen=True)
class Lane:
    name: str
    repository: str
    relative_cwd: str
    command: tuple[str, ...]
    timeout_sec: float
    required_environment: tuple[str, ...] | str = ()
    fixed_environment: tuple[tuple[str, str], ...] = ()
    required_source_contract: str | None = None
    reject_pytest_skips: bool = False


def _lane(
    name: str,
    repository: str,
    relative_cwd: str,
    command: tuple[str, ...],
    timeout_sec: float = 900.0,
    required_environment: tuple[str, ...] | str = (),
    fixed_environment: tuple[tuple[str, str], ...] = (),
    required_source_contract: str | None = None,
    reject_pytest_skips: bool = False,
) -> Lane:
    return Lane(
        name, repository, relative_cwd, command, timeout_sec, required_environment,
        fixed_environment, required_source_contract, reject_pytest_skips,
    )


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
    *(
        _lane(
            f"admin-course-mode-playwright-{project.removeprefix('course-mode-')}",
            "adminEsp", "main/manager-web",
            ("npm", "run", "test:e2e:course-mode", "--", f"--project={project}"),
            1200.0, ("COURSE_MODE_ADMIN_E2E_READY",),
            required_source_contract="course-mode-playwright",
        )
        for project in PLAYWRIGHT_PROJECTS
    ),
    _lane(
        "esp-course-mode-full", "adminEsp", "main/tbot-server",
        (COURSE_MODE_SOFTWARE_TESTS,),
        1800.0,
        reject_pytest_skips=True,
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
    1800.0,
    ("COURSE_MODE_V2_TEST_DATABASE_URL", "COURSE_MODE_TEST_DATABASE_URL", "DATABASE_URL"),
    (("TBOT_RUN_LIVE_DB_TESTS", "true"),),
)


PHYSICAL_PREFLIGHT_LANE = _lane(
    "physical-tft-preflight", "adminEsp", "main/tbot-server",
    ("python3", "scripts/course_mode_physical_tft_preflight.py"),
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


def candidate_paths_match(repository: Mapping[str, object], relative_paths: Sequence[str]) -> bool:
    try:
        root = Path(repository["path"]).resolve(strict=True)
        sha = repository["sha"]
        dirty = {item["path"] for item in repository["dirtyExceptions"]}
        if not isinstance(sha, str):
            return False
        for relative in sorted(set(relative_paths)):
            path_value = Path(relative)
            if (
                not relative or path_value.is_absolute() or ".." in path_value.parts
                or relative in dirty
            ):
                return False
            path = root / relative
            if not path.is_file() or path.is_symlink():
                return False
            committed_blob = _candidate_git(root, "rev-parse", f"{sha}:{relative}").strip()
            working_blob = _candidate_git(root, "hash-object", "--", relative).strip()
            if committed_blob != working_blob:
                return False
        return True
    except (KeyError, OSError, RuntimeError, TypeError):
        return False


def _runtime_matches_candidate(candidate: dict, runtime_root: Path | None) -> bool:
    repository = candidate["repositories"]["adminEsp"]
    expected = Path(repository["path"])
    actual = runtime_root if runtime_root is not None else Path(__file__).resolve().parents[3]
    try:
        if actual.resolve(strict=True) != expected.resolve(strict=True):
            return False
        bound = (
            "main/tbot-server/scripts/course_mode_release_gate.py",
            "main/tbot-server/scripts/course_mode_candidate_manifest.py",
            "scripts/course_robot_e2e_gates.sh",
        )
        if not candidate_paths_match(repository, bound):
            return False
        if runtime_root is None:
            expected_script = expected / "main/tbot-server/scripts/course_mode_release_gate.py"
            expected_helper = expected / "main/tbot-server/scripts/course_mode_candidate_manifest.py"
            return (
                expected_script.resolve(strict=True) == Path(__file__).resolve(strict=True)
                and expected_helper.resolve(strict=True) == Path(_manifest.__file__).resolve(strict=True)
            )
        return True
    except (KeyError, OSError, RuntimeError, TypeError):
        return False


def _required_environment(lane: Lane) -> tuple[str, ...]:
    if lane.required_environment is None:
        return ()
    if isinstance(lane.required_environment, str):
        return (lane.required_environment,) if lane.required_environment else ()
    return lane.required_environment


def discover_esp_course_mode_tests(admin_root: Path, sha: str) -> tuple[str, ...]:
    try:
        root = admin_root.resolve(strict=True)
        tracked = _candidate_git(
            root, "ls-tree", "-r", "--name-only", "-z", sha, "--", "main/tbot-server/tests",
        ).split("\0")
    except (OSError, RuntimeError):
        return ()
    discovered = []
    for relative in sorted(path for path in tracked if path):
        name = Path(relative).name
        if not (
            (name.startswith("test_course_mode") and name.endswith(".py"))
            or name == "test_google_live_course_mode.py"
        ):
            continue
        if name == "test_course_mode_release_gate.py":
            continue
        path = root / relative
        if path.is_file() and not path.is_symlink():
            discovered.append(f"tests/{name}")
    return tuple(discovered)


def classify_esp_course_mode_test(relative: str) -> str:
    name = Path(relative).name
    if name in SAFE_PHYSICAL_CONTRACT_TESTS:
        return "physical-contract"
    if "_physical_" in name:
        return "physical-runtime"
    if "postgres" in name or "live_db" in name:
        return "live-db"
    return "software"


def select_esp_software_tests(discovered: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        relative for relative in discovered
        if classify_esp_course_mode_test(relative) in {"software", "physical-contract"}
    )


def _playwright_spec_paths(admin_root: Path, sha: str) -> tuple[str, ...]:
    try:
        tracked = _candidate_git(
            admin_root, "ls-tree", "-r", "--name-only", sha, "--",
            "main/manager-web/e2e/lesson-studio",
        ).splitlines()
    except RuntimeError:
        return ()
    return tuple(
        relative for relative in tracked
        if Path(relative).name.startswith("course-mode") and relative.endswith(".spec.js")
    )


def _python_test_dependencies(root: Path, sha: str, selected: set[str]) -> set[str]:
    pending = list(selected)
    dependencies: set[str] = set()
    while pending:
        relative = pending.pop()
        source = _committed_text(root, sha, relative)
        if source is None:
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        module_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                module_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                module_names.add(node.module)
        for module in module_names:
            name = module.removeprefix("tests.")
            if "." in name or not name.startswith("test_"):
                continue
            dependency = f"main/tbot-server/tests/{name}.py"
            if dependency not in selected and dependency not in dependencies:
                dependencies.add(dependency)
                pending.append(dependency)
    return dependencies


def lane_candidate_paths(lane: Lane, candidate: dict) -> tuple[str, ...]:
    repository = candidate["repositories"][lane.repository]
    root = Path(repository["path"])
    paths: set[str] = set()
    if lane.repository == "adminEsp" and lane.relative_cwd == "main/manager-web":
        paths.add("main/manager-web/package.json")
        if (root / "main/manager-web/package-lock.json").is_file():
            paths.add("main/manager-web/package-lock.json")
    if lane.required_source_contract == "course-mode-playwright":
        paths.add("main/manager-web/playwright.config.js")
        paths.add(PLAYWRIGHT_CONTRACT_PATH)
        paths.update(_playwright_spec_paths(root, repository["sha"]))
    if lane.name == "esp-course-mode-full":
        paths.add("main/tbot-server/pyproject.toml")
        discovered = discover_esp_course_mode_tests(root, repository["sha"])
        paths.update(f"main/tbot-server/{relative}" for relative in select_esp_software_tests(discovered))
    elif lane.repository == "adminEsp" and lane.relative_cwd == "main/tbot-server":
        paths.update(
            f"main/tbot-server/{token}" for token in lane.command
            if token.endswith(".py") and not Path(token).is_absolute()
        )
    if lane.repository == "adminEsp" and lane.relative_cwd == "main/tbot-server":
        paths.update(_python_test_dependencies(root, repository["sha"], paths))
    if lane.repository == "backend" and lane.command[0] in {"npm", "npx"}:
        paths.add("package.json")
        if (root / "package-lock.json").is_file():
            paths.add("package-lock.json")
    for token in lane.command:
        if "/" in token and not token.startswith(("--", "@")) and not Path(token).is_absolute():
            candidate_path = root / lane.relative_cwd / token
            if candidate_path.is_file():
                paths.add(candidate_path.relative_to(root).as_posix())
    return tuple(sorted(paths))


def lane_dirty_exceptions_authorized(lane: Lane, candidate: dict) -> bool:
    try:
        if lane.name == "physical-tft-preflight" and any(
            candidate["repositories"][name]["dirtyExceptions"]
            for name in ("backend", "firmware")
        ):
            return False
        repository = candidate["repositories"][lane.repository]
        dirty = {item["path"] for item in repository["dirtyExceptions"]}
    except (KeyError, TypeError):
        return False
    if lane.repository in {"backend", "firmware"}:
        return not dirty
    if lane.repository != "adminEsp":
        return False
    root = Path(repository["path"])
    selected = set(lane_candidate_paths(lane, candidate))
    for relative in dirty:
        path = Path(relative)
        is_unselected_standalone_test = (
            path.parent == Path("main/tbot-server/tests")
            and path.name.startswith("test_")
            and path.suffix == ".py"
            and relative not in selected
        )
        if not is_unselected_standalone_test or _test_module_referenced(
            root, repository["sha"], relative,
        ):
            return False
    return True


def _test_module_referenced(root: Path, sha: str, relative: str) -> bool:
    module_name = Path(relative).stem
    try:
        matches = _candidate_git(
            root, "grep", "-l", "-F", module_name, sha, "--", "main/tbot-server/**/*.py",
        ).splitlines()
    except RuntimeError:
        return False
    return any(match.partition(":")[2] != relative for match in matches)


def release_state_matches(
    candidate_path: Path, candidate: dict, lanes: Sequence[Lane], runtime_root: Path | None,
    require_runtime: bool,
) -> bool:
    current = _load_candidate(candidate_path)
    if current != candidate or current is None or validate_candidate(current):
        return False
    if not _candidate_matches(candidate):
        return False
    if require_runtime and not _runtime_matches_candidate(candidate, runtime_root):
        return False
    repositories = candidate["repositories"]
    for lane in lanes:
        if not lane_dirty_exceptions_authorized(lane, candidate):
            return False
        bound_paths = lane_candidate_paths(lane, candidate)
        if bound_paths and not candidate_paths_match(repositories[lane.repository], bound_paths):
            return False
    return True


def _committed_text(admin_root: Path, sha: str, relative: str) -> str | None:
    try:
        return _candidate_git(admin_root, "show", f"{sha}:{relative}")
    except RuntimeError:
        return None


def _json_exact_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(
            _json_exact_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _json_exact_equal(item, expected_item)
            for item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def validate_playwright_contract(contract: object) -> bool:
    if not isinstance(contract, dict) or set(contract) != {
        "version", "specs", "testMatch", "projects", "fixed",
    }:
        return False
    specs = contract.get("specs")
    test_match = contract.get("testMatch")
    if (
        type(contract.get("version")) is not int or contract.get("version") != 1
        or not isinstance(specs, list) or not specs
        or any(
            not isinstance(spec, str) or not spec.startswith("e2e/lesson-studio/")
            or Path(spec).name != spec.removeprefix("e2e/lesson-studio/")
            or not spec.startswith("e2e/lesson-studio/course-mode")
            or not spec.endswith(".spec.js")
            for spec in specs
        )
        or specs != sorted(set(specs))
        or test_match != [Path(spec).name for spec in specs]
        or not _json_exact_equal(contract.get("projects"), list(PLAYWRIGHT_PROJECT_CONTRACT))
        or not _json_exact_equal(contract.get("fixed"), PLAYWRIGHT_FIXED_CONTRACT)
    ):
        return False
    return True


def _js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def generate_playwright_config(contract: object) -> str:
    if not validate_playwright_contract(contract):
        raise ValueError("invalid Course Mode Playwright contract")
    assert isinstance(contract, dict)
    fixed = contract["fixed"]
    projects = contract["projects"]
    project_lines = []
    for project in projects:
        viewport = project["viewport"]
        project_lines.extend([
            "    {",
            f"      name: {_js_string(project['name'])},",
            "      use: {",
            f"        ...devices[{_js_string(project['device'])}],",
            f"        viewport: {{ width: {viewport['width']}, height: {viewport['height']} }},",
            "      },",
            "    },",
        ])
    test_matches = ", ".join(_js_string(value) for value in contract["testMatch"])
    reporter = json.dumps(fixed["reporter"], sort_keys=True, separators=(", ", ": "))
    return "\n".join([
        "const { defineConfig, devices } = require('@playwright/test');",
        "const { lessonStudioWebOrigin } = require('./scripts/lesson-studio-e2e-environment.cjs');",
        "",
        "module.exports = defineConfig({",
        f"  testDir: {_js_string(fixed['testDir'])},",
        f"  globalSetup: {_js_string(fixed['globalSetup'])},",
        f"  testMatch: [{test_matches}],",
        f"  outputDir: {_js_string(fixed['outputDir'])},",
        f"  timeout: {fixed['timeout']},",
        f"  expect: {{ timeout: {fixed['expectTimeout']} }},",
        f"  fullyParallel: {str(fixed['fullyParallel']).lower()},",
        f"  workers: {fixed['workers']},",
        f"  retries: {fixed['retries']},",
        f"  reporter: {reporter},",
        "  use: {",
        "    baseURL: lessonStudioWebOrigin(),",
        f"    trace: {_js_string(fixed['use']['trace'])},",
        f"    screenshot: {_js_string(fixed['use']['screenshot'])},",
        f"    video: {_js_string(fixed['use']['video'])},",
        f"    serviceWorkers: {_js_string(fixed['use']['serviceWorkers'])},",
        "  },",
        "  projects: [",
        *project_lines,
        "  ],",
        "});",
        "",
    ])


def source_contract_ready(admin_root: Path, contract: str, sha: str) -> bool:
    if contract != "course-mode-playwright":
        return False
    try:
        package_raw = _committed_text(admin_root, sha, "main/manager-web/package.json")
        contract_raw = _committed_text(admin_root, sha, PLAYWRIGHT_CONTRACT_PATH)
        config_raw = _committed_text(admin_root, sha, "main/manager-web/playwright.config.js")
        if package_raw is None or contract_raw is None or config_raw is None:
            return False
        package = strict_json_loads(package_raw)
        document = strict_json_loads(contract_raw)
        specs = _playwright_spec_paths(admin_root, sha)
    except (json.JSONDecodeError, ValueError, RuntimeError):
        return False
    scripts = package.get("scripts") if isinstance(package, dict) else None
    script = scripts.get("test:e2e:course-mode") if isinstance(scripts, dict) else None
    if script != "playwright test --config=playwright.config.js":
        return False
    try:
        generated = generate_playwright_config(document)
    except ValueError:
        return False
    normalized_specs = [relative.removeprefix("main/manager-web/") for relative in specs]
    if document["specs"] != normalized_specs or config_raw != generated:
        return False
    bound = (
        "main/manager-web/package.json", "main/manager-web/playwright.config.js",
        PLAYWRIGHT_CONTRACT_PATH, *specs,
    )
    return candidate_paths_match(
        {"path": str(admin_root), "sha": sha, "dirtyExceptions": []}, bound,
    )


def pytest_report_has_skips(path: Path) -> bool | None:
    try:
        root = ET.fromstring(read_secure_regular(path, MAX_REPORT_BYTES))
        suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
        if not suites and root.tag == "testsuites":
            tests = int(root.attrib.get("tests", "0"))
            skipped = int(root.attrib.get("skipped", "0"))
            return skipped > 0 if tests > 0 else None
        tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
        skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)
        return skipped > 0 if tests > 0 else None
    except (OSError, ET.ParseError, TypeError, ValueError):
        return None


def physical_preflight_command(candidate: dict) -> tuple[str, ...] | None:
    if any(not isinstance(candidate.get(key), dict) or not candidate[key] for key in ("images", "firmware", "database")):
        return None
    tools = candidate.get("tools")
    metadata = tools.get("physicalPreflight") if isinstance(tools, dict) else None
    required = {"input", "output", "expectedIdentity", "expectedIdentitySignature"}
    if not isinstance(metadata, dict) or set(metadata) != required:
        return None
    try:
        evidence_root = Path(candidate["evidenceRoot"]).resolve(strict=True)
        resolved = {key: Path(value) for key, value in metadata.items() if isinstance(value, str)}
        if set(resolved) != required or any(not path.is_absolute() for path in resolved.values()):
            return None
        for key in ("input", "expectedIdentity", "expectedIdentitySignature"):
            path = resolved[key].resolve(strict=True)
            path.relative_to(evidence_root)
            raw = read_secure_regular(path, 256 if key == "expectedIdentitySignature" else MAX_CANDIDATE_BYTES)
            if key == "expectedIdentitySignature":
                if len(raw) != 64:
                    return None
            else:
                document = strict_json_loads(raw)
                if not isinstance(document, dict):
                    return None
        output = resolved["output"]
        output.parent.resolve(strict=True).relative_to(evidence_root)
        if output.exists() or output.is_symlink():
            return None
    except (KeyError, OSError, ValueError):
        return None
    return (
        "python3", "scripts/course_mode_physical_tft_preflight.py",
        "--input", str(resolved["input"]), "--output", str(resolved["output"]),
        "--expected-identity", str(resolved["expectedIdentity"]),
        "--expected-identity-signature", str(resolved["expectedIdentitySignature"]),
    )


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
        "TBOT_BACKEND_WORKTREE": candidate["repositories"]["backend"]["path"],
        "TASK06_BACKEND_ROOT": candidate["repositories"]["backend"]["path"],
        "TASK06_FIRMWARE_ROOT": candidate["repositories"]["firmware"]["path"],
        "TBOT_BACKEND_CONTRACTS_DIR": str(
            Path(candidate["repositories"]["backend"]["path"]) / "contracts"
        ),
    })
    for name in _required_environment(lane):
        if source.get(name):
            environment[name] = source[name]
    environment.update(dict(lane.fixed_environment))
    return environment


def _command_for_lane(lane: Lane, candidate: dict) -> tuple[str, ...] | None:
    if lane.command == (COURSE_MODE_SOFTWARE_TESTS,):
        repository = candidate["repositories"]["adminEsp"]
        tests = select_esp_software_tests(
            discover_esp_course_mode_tests(Path(repository["path"]), repository["sha"])
        )
        return ("python3", "-m", "pytest", "-q", *tests) if tests else None
    if lane.name == "physical-tft-preflight":
        return physical_preflight_command(candidate)
    return lane.command


def _write_report_atomic(path: Path, report: dict, parent_fd: int | None = None) -> bool:
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8") + b"\n"
    if len(payload) > MAX_REPORT_BYTES or not path.is_absolute():
        return False
    owned_fd = parent_fd is None
    temporary = None
    try:
        if parent_fd is None:
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor_parent = os.fstat(parent_fd)
        named_parent = os.stat(path.parent, follow_symlinks=False)
        if (descriptor_parent.st_dev, descriptor_parent.st_ino) != (
            named_parent.st_dev, named_parent.st_ino,
        ):
            return False
        try:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                return False
        except FileNotFoundError:
            pass
        for _ in range(32):
            temporary = f".{path.name}.{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=parent_fd,
                )
                break
            except FileExistsError:
                continue
        else:
            return False
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary_stat = os.stat(temporary, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(temporary_stat.st_mode) or temporary_stat.st_nlink != 1:
            return False
        os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temporary = None
        os.fsync(parent_fd)
        named_parent = os.stat(path.parent, follow_symlinks=False)
        if (descriptor_parent.st_dev, descriptor_parent.st_ino) != (
            named_parent.st_dev, named_parent.st_ino,
        ):
            _invalidate_report(path, parent_fd)
            return False
        return True
    except OSError:
        return False
    finally:
        if temporary is not None and parent_fd is not None:
            with contextlib.suppress(OSError):
                os.unlink(temporary, dir_fd=parent_fd)
        if owned_fd and parent_fd is not None:
            os.close(parent_fd)


def _path_overlaps(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        try:
            right.relative_to(left)
            return True
        except ValueError:
            return False


def _prepare_report_destination(
    candidate_path: Path, candidate: dict, report_path: Path,
) -> int | None:
    if not report_path.is_absolute():
        return None
    parent_fd = None
    try:
        evidence_value = Path(candidate["evidenceRoot"])
        if not evidence_value.is_absolute() or evidence_value.is_symlink():
            return None
        evidence_root = evidence_value.resolve(strict=True)
        if evidence_value != evidence_root or not evidence_root.is_dir():
            return None
        candidate_input = candidate_path.resolve(strict=True)
        repository_roots = [
            Path(candidate["repositories"][name]["path"]).resolve(strict=True)
            for name in ("backend", "adminEsp", "firmware")
        ]
        if _path_overlaps(evidence_root, candidate_input) or any(
            _path_overlaps(evidence_root, root) for root in repository_roots
        ):
            return None
        parent_value = report_path.parent
        parent = parent_value.resolve(strict=True)
        if parent_value != parent or not parent.is_dir():
            return None
        parent.relative_to(evidence_root)
        if report_path == evidence_root or report_path.is_symlink():
            return None
        expected_evidence = evidence_root.stat()
        parent_fd = os.open(evidence_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        opened_evidence = os.fstat(parent_fd)
        if (opened_evidence.st_dev, opened_evidence.st_ino) != (
            expected_evidence.st_dev, expected_evidence.st_ino,
        ):
            return None
        for component in parent.relative_to(evidence_root).parts:
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = next_fd
        expected_parent = parent.stat()
        actual_parent = os.fstat(parent_fd)
        if (actual_parent.st_dev, actual_parent.st_ino) != (expected_parent.st_dev, expected_parent.st_ino):
            return None
        try:
            metadata = os.stat(report_path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                return None
        except FileNotFoundError:
            pass
        if any(
            _path_overlaps(report_path, protected)
            for protected in (candidate_input, *repository_roots)
        ):
            return None
        prepared = parent_fd
        parent_fd = None
        return prepared
    except (KeyError, OSError, TypeError, ValueError):
        return None
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def _invalidate_report(path: Path, parent_fd: int) -> None:
    with contextlib.suppress(OSError):
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)


def run_gate(
    candidate_path: Path,
    mode: str,
    *,
    lanes: Sequence[Lane] | None = None,
    source_environment: Mapping[str, str] | None = None,
    max_output_bytes: int = MAX_LANE_OUTPUT_BYTES,
    report_path: Path | None = None,
    runtime_root: Path | None = None,
) -> dict:
    candidate = _load_candidate(candidate_path)
    candidate_id = candidate.get("candidateId") if isinstance(candidate, dict) else None
    selected: tuple[Lane, ...] = ()
    require_runtime = False
    report_parent_fd = None
    if report_path is not None:
        if candidate is not None:
            report_parent_fd = _prepare_report_destination(candidate_path, candidate, report_path)
        if report_parent_fd is None:
            return _blocked(candidate_id if isinstance(candidate_id, str) else None, "report")
    if candidate is None or validate_candidate(candidate):
        report = _blocked(candidate_id if isinstance(candidate_id, str) else None, "candidate")
    elif mode not in MODES or type(max_output_bytes) is not int or max_output_bytes <= 0:
        report = _blocked(candidate_id, "configuration")
    elif lanes is None and not _runtime_matches_candidate(candidate, runtime_root):
        report = _blocked(candidate_id, "candidate-runtime")
    else:
        selected = tuple(lanes) if lanes is not None else lanes_for_mode(mode)
        require_runtime = lanes is None
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
                if not release_state_matches(
                    candidate_path, candidate, selected, runtime_root, require_runtime,
                ):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                required_environment = _required_environment(lane)
                if any(not source.get(name) for name in required_environment):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if lane.required_source_contract and not source_contract_ready(
                    Path(repositories["adminEsp"]["path"]), lane.required_source_contract,
                    repositories["adminEsp"]["sha"],
                ):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if not release_state_matches(
                    candidate_path, candidate, selected, runtime_root, require_runtime,
                ):
                    report["lanes"].append({"name": lane.name, "exitCode": None, "durationMs": 0})
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                lane_command = _command_for_lane(lane, candidate)
                command = _resolve_command(lane_command) if lane_command else None
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
                junit_path: Path | None = None
                if lane.reject_pytest_skips:
                    descriptor, name = tempfile.mkstemp(prefix="course-mode-pytest-", suffix=".xml")
                    os.close(descriptor)
                    junit_path = Path(name).resolve()
                    command = (*command, f"--junitxml={junit_path}")
                try:
                    result = run_bounded_command(
                        list(command), cwd=resolved_cwd, timeout_sec=lane.timeout_sec,
                        max_output_bytes=max_output_bytes,
                        env=_child_environment(candidate, source, lane),
                    )
                    skip_state = pytest_report_has_skips(junit_path) if junit_path else False
                finally:
                    if junit_path is not None:
                        with contextlib.suppress(OSError):
                            junit_path.unlink()
                duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
                exit_code = None if result.error else result.returncode
                report["lanes"].append({
                    "name": lane.name, "exitCode": exit_code, "durationMs": duration_ms,
                })
                if not release_state_matches(
                    candidate_path, candidate, selected, runtime_root, require_runtime,
                ):
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
                if result.error or result.returncode != 0:
                    report["verdict"] = "FAIL"
                    report["failedLane"] = lane.name
                    break
                if skip_state is not False:
                    report["verdict"] = "BLOCKED"
                    report["failedLane"] = lane.name
                    break
            if not release_state_matches(
                candidate_path, candidate, selected, runtime_root, require_runtime,
            ):
                report["verdict"] = "BLOCKED"
                if report["failedLane"] is None:
                    report["failedLane"] = selected[-1].name if selected else "candidate-runtime"
    if report_path is not None:
        assert report_parent_fd is not None
        if not _write_report_atomic(report_path, report, report_parent_fd):
            os.close(report_parent_fd)
            return _blocked(report.get("candidateId"), "report")
        if (
            report["verdict"] == "PASS"
            and not release_state_matches(
                candidate_path, candidate, selected, runtime_root, require_runtime,
            )
        ):
            report = _blocked(candidate_id, selected[-1].name if selected else "candidate-runtime")
            if not _write_report_atomic(report_path, report, report_parent_fd):
                _invalidate_report(report_path, report_parent_fd)
                os.close(report_parent_fd)
                return _blocked(candidate_id, "report")
        os.close(report_parent_fd)
    return report


def _emit(report: dict) -> None:
    print(json.dumps(
        report, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ))


def lane_inventory() -> dict[str, list[str]]:
    return {mode: [lane.name for lane in lanes_for_mode(mode)] for mode in MODES}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--mode", choices=MODES, default="quick")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--list-lanes", action="store_true")
    args = parser.parse_args(argv)
    if args.list_lanes:
        if args.candidate is not None or args.report is not None:
            parser.error("--list-lanes cannot be combined with candidate execution")
        print(json.dumps(lane_inventory(), sort_keys=True, separators=(",", ":")))
        return 0
    if args.candidate is None:
        parser.error("--candidate is required")
    report = run_gate(args.candidate, args.mode, report_path=args.report)
    _emit(report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
