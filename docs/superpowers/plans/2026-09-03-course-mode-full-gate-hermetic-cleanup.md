# Course Mode Full Gate Hermetic Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the canonical Course Mode Full gate remove internally hardlinked staging files safely, execute only an explicit hermetic ESP runtime/contract suite, and preserve the primary lane failure when cleanup also fails.

**Architecture:** Keep the existing release-gate implementation and candidate sandbox. Tighten regular-file unlink verification from "inode has no remaining links" to "this unlink decremented the opened inode exactly once," replace prefix discovery with a candidate-commit-verified allowlist, and classify lane results before cleanup so cleanup metadata cannot erase the primary failure.

**Tech Stack:** Python 3.11, pytest, macOS descriptor-relative filesystem APIs, Git candidate snapshots, JSON release reports.

---

## File Map

- Modify `main/tbot-server/scripts/course_mode_release_gate.py`: hardlink-aware cleanup proof, explicit ESP Full suite, candidate-path verification, and primary-failure-preserving cleanup reporting.
- Modify `main/tbot-server/tests/test_course_mode_release_gate.py`: RED/GREEN regressions for hardlink cleanup, selection boundaries, command construction, and dual failure reporting.
- Modify `main/tbot-server/tests/test_course_mode_contract.py`: remove the host-interpreter compatibility probe from the hermetic runtime module.
- Create `main/tbot-server/tests/test_course_mode_python_compatibility.py`: retain the supported-host-Python import probe in canonical source qualification without selecting it in candidate Full.
- Preserve `docs/superpowers/specs/2026-09-03-course-mode-full-gate-hermetic-cleanup-design.md`: approved requirements and safety boundary.
- Preserve `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.27`: immutable `.27` evidence.

Execution stays on the approved canonical `main` checkout because the user explicitly requested one source version and minimal diff. Do not create another implementation worktree or parallel code version; subagents share this checkout and implementation tasks execute sequentially with review barriers.

### Task 1: Accept Owned Internal Hardlinks Without Weakening Race Detection

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py:887`

- [ ] **Step 1: Add failing internal-hardlink cleanup tests**

Add these tests beside the existing `_remove_owned_tree` leaf and FIFO tests:

```python
@pytest.mark.parametrize("link_count", [2, 3])
def test_owned_cleanup_removes_all_internal_regular_file_hardlinks(
    tmp_path: Path, link_count: int,
) -> None:
    root = tmp_path / "owned-root"
    root.mkdir()
    original = root / "a-original"
    original.write_bytes(b"owned")
    for index in range(1, link_count):
        os.link(original, root / f"z-hardlink-{index}")
    identity = gate._owned_tree_identity(root)

    assert gate._remove_owned_tree(root, identity) is True
    assert not root.exists()
    assert not list(root.parent.glob(".course-mode-cleanup-*"))
```

Do not modify the production cleanup code in this step.

- [ ] **Step 2: Run RED and confirm the diagnosed failure**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'removes_all_internal_regular_file_hardlinks'
```

Expected: both cases fail because `_remove_owned_tree()` returns `False` after unlinking the first path while another hard link remains.

- [ ] **Step 3: Implement the minimal link-count decrement proof**

In `remove_leaf()`, retain every existing rename, identity, type, ownership, path-absence, and path-recreation check. Capture the opened link count before unlink and require an exact decrement afterward:

```python
opened = os.fstat(leaf_fd)
if (
    (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
    or stat.S_IFMT(opened.st_mode) != stat.S_IFMT(before.st_mode)
):
    raise _StagingIdentityChanged("staging leaf changed before removal")
regular_pre_unlink_nlink = opened.st_nlink if stat.S_ISREG(opened.st_mode) else None
if regular_pre_unlink_nlink is not None and regular_pre_unlink_nlink <= 0:
    raise _StagingIdentityChanged("staging leaf has invalid link count")
os.unlink(quarantine, dir_fd=directory_fd)
try:
    os.stat(quarantine, dir_fd=directory_fd, follow_symlinks=False)
except FileNotFoundError:
    post_unlink_nlink = os.fstat(leaf_fd).st_nlink
    expected_nlink = (
        regular_pre_unlink_nlink - 1
        if regular_pre_unlink_nlink is not None else 0
    )
    if post_unlink_nlink != expected_nlink:
        raise _StagingIdentityChanged("staging leaf moved during removal")
else:
    raise _StagingIdentityChanged("staging leaf path recreated")
```

Do not pre-scan the tree, follow links, accept a non-decrementing inode, or relax the socket and unsupported-file rejection paths.

- [ ] **Step 4: Run GREEN plus existing race tests**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'internal_regular_file_hardlinks or leaf_swap_at_unlink or root_swap or child_swap or fifo or unix_socket or repeated_cleanup'
```

Expected: all selected tests pass. In particular, internal hardlinks are removed and the unlink-swap cases still return `False` while preserving the escaped inode.

- [ ] **Step 5: Commit the cleanup fix**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): clean internal hardlinks safely"
```

### Task 2: Define an Explicit Candidate-Verified Hermetic ESP Full Suite

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py:316`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py:2216`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py:3686`

- [ ] **Step 1: Replace the broad-discovery expectation with failing allowlist tests**

Replace `test_full_esp_lane_discovers_every_committed_software_course_mode_suite` with tests that assert the approved exact tuple and candidate-commit verification:

```python
def test_full_esp_lane_uses_exact_hermetic_runtime_contract_suite() -> None:
    assert gate.ESP_COURSE_MODE_FULL_TESTS == (
        "tests/test_course_mode_contract.py",
        "tests/test_course_mode_curriculum.py",
        "tests/test_course_mode_curriculum_e2e.py",
        "tests/test_course_mode_e2e_journeys.py",
        "tests/test_course_mode_forwarder.py",
        "tests/test_course_mode_resource_soak.py",
        "tests/test_course_mode_runtime_compatibility.py",
        "tests/test_course_mode_runtime_integration.py",
        "tests/test_course_mode_task00_contract.py",
        "tests/test_google_live_course_mode.py",
    )


def test_full_esp_lane_does_not_auto_select_prefix_matching_meta_test(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        gate, "_candidate_git",
        lambda *_args: "\0".join(
            f"main/tbot-server/{relative}" for relative in gate.ESP_COURSE_MODE_FULL_TESTS
        ) + "\0main/tbot-server/tests/test_course_mode_candidate_manifest.py\0",
    )
    command = gate._command_for_lane(
        next(lane for lane in gate.FULL_LANES if lane.name == "esp-course-mode-full"),
        candidate,
    )

    assert command == ("python3", "-m", "pytest", "-q", *gate.ESP_COURSE_MODE_FULL_TESTS)
    assert "tests/test_course_mode_candidate_manifest.py" not in command
    assert "tests/test_course_mode_physical_tft_preflight.py" not in command
    assert "tests/test_course_mode_cross_process_e2e.py" not in command


def test_full_esp_lane_blocks_when_an_allowlisted_test_is_missing_from_candidate_commit(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    lane = next(lane for lane in gate.FULL_LANES if lane.name == "esp-course-mode-full")
    monkeypatch.setattr(
        gate, "_candidate_git",
        lambda *_args: "\0".join(
            f"main/tbot-server/{relative}"
            for relative in gate.ESP_COURSE_MODE_FULL_TESTS[:-1]
        ) + "\0",
    )

    assert gate._command_for_lane(lane, candidate) is None
```

- [ ] **Step 2: Run RED against prefix discovery**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'exact_hermetic_runtime_contract_suite or auto_select_prefix or allowlisted_test_is_missing'
```

Expected: tests fail because `ESP_COURSE_MODE_FULL_TESTS` does not exist and `_command_for_lane()` still expands the prefix-discovered 21-module set.

- [ ] **Step 3: Add the exact allowlist and commit-tree validator**

Replace `SAFE_PHYSICAL_CONTRACT_TESTS`, `discover_esp_course_mode_tests()`,
`classify_esp_course_mode_test()`, and the old sequence-only
`select_esp_software_tests()` with:

```python
ESP_COURSE_MODE_FULL_TESTS = (
    "tests/test_course_mode_contract.py",
    "tests/test_course_mode_curriculum.py",
    "tests/test_course_mode_curriculum_e2e.py",
    "tests/test_course_mode_e2e_journeys.py",
    "tests/test_course_mode_forwarder.py",
    "tests/test_course_mode_resource_soak.py",
    "tests/test_course_mode_runtime_compatibility.py",
    "tests/test_course_mode_runtime_integration.py",
    "tests/test_course_mode_task00_contract.py",
    "tests/test_google_live_course_mode.py",
)


def select_esp_software_tests(admin_root: Path, sha: str) -> tuple[str, ...]:
    try:
        tracked = set(_candidate_git(
            admin_root, "ls-tree", "-r", "--name-only", "-z", sha, "--",
            "main/tbot-server/tests",
        ).split("\0"))
    except RuntimeError:
        return ()
    required = {
        f"main/tbot-server/{relative}" for relative in ESP_COURSE_MODE_FULL_TESTS
    }
    return ESP_COURSE_MODE_FULL_TESTS if required <= tracked else ()
```

Update `_command_for_lane()` to call the candidate-aware function:

```python
tests = select_esp_software_tests(
    Path(repository["path"]), repository["sha"],
)
return ("python3", "-m", "pytest", "-q", *tests) if tests else None
```

Remove tests for the deleted discovery and classification helpers. Do not add globbing, directory scans, fallbacks, or physical-contract modules.

- [ ] **Step 4: Run GREEN and command-authority regressions**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'full_esp_lane or esp_python_lane or staged_child_context or python_runtime or pytest_junit_skip'
```

Expected: all selected tests pass; the lane command is exact, missing committed paths block construction, candidate runtime authority remains enforced, and skips remain release-blocking.

- [ ] **Step 5: Commit the explicit suite selection**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): make esp full suite hermetic"
```

### Task 3: Move the Host-Python Probe Out of Candidate Full

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_contract.py`
- Create: `main/tbot-server/tests/test_course_mode_python_compatibility.py`

- [ ] **Step 1: Add a failing no-skip-source ownership test**

Add this release-gate test:

```python
def test_full_esp_suite_modules_do_not_contain_explicit_pytest_skips() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative in gate.ESP_COURSE_MODE_FULL_TESTS:
        source = (root / relative).read_text(encoding="utf-8")
        assert "pytest.skip(" not in source, relative
```

- [ ] **Step 2: Run RED and prove the selected contract module can skip**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'full_esp_suite_modules_do_not_contain_explicit_pytest_skips'
```

Expected: FAIL naming `tests/test_course_mode_contract.py`, because the selected module contains the host compatibility probe's `pytest.skip`.

- [ ] **Step 3: Create the source-qualification module before removing the old test**

Create `main/tbot-server/tests/test_course_mode_python_compatibility.py` with:

```python
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("version", ["3.10", "3.11"])
def test_course_mode_contract_imports_under_supported_python(version: str) -> None:
    python = Path(f"/opt/homebrew/bin/python{version}")
    if not python.exists():
        pytest.skip(f"Python {version} is not installed")
    result = subprocess.run(
        [str(python), "-c", "import core.lesson.course_mode_contract"],
        cwd=Path(__file__).parents[1], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
```

Remove only the matching test, `subprocess` import, and no-longer-used host probe code from `test_course_mode_contract.py`.

- [ ] **Step 4: Verify GREEN, test ownership, and no duplicate probe**

Run:

```bash
rg -n 'test_course_mode_contract_imports_under_supported_python' main/tbot-server/tests
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_contract.py \
  main/tbot-server/tests/test_course_mode_python_compatibility.py \
  -k 'full_esp_suite_modules_do_not_contain_explicit_pytest_skips or course_mode_contract'
```

Expected: the test name appears exactly once in the new module and all selected tests pass without skips on the approved qualification host. If Python 3.10 or 3.11 is absent, stop and restore the qualification prerequisite; do not accept a skip as PASS.

- [ ] **Step 5: Verify the new module is not selected by Full**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, str(Path("main/tbot-server").resolve()))
from scripts import course_mode_release_gate as gate
assert "tests/test_course_mode_python_compatibility.py" not in gate.ESP_COURSE_MODE_FULL_TESTS
assert "tests/test_course_mode_contract.py" in gate.ESP_COURSE_MODE_FULL_TESTS
PY
```

Expected: exit code 0.

- [ ] **Step 6: Commit the qualification ownership move**

```bash
git add main/tbot-server/tests/test_course_mode_contract.py \
  main/tbot-server/tests/test_course_mode_python_compatibility.py
git commit -m "test(course-mode): separate host python compatibility"
```

### Task 4: Preserve Primary Lane Failure When Cleanup Also Fails

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py:1045`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py:4248`

- [ ] **Step 1: Add the dual-failure RED regression**

Add a test beside `test_gate_reports_retained_snapshot_when_owned_cleanup_cannot_finish`:

```python
def test_failed_lane_plus_cleanup_failure_preserves_primary_lane(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    retained: list[Path] = []
    original = gate._remove_owned_tree

    def refuse_lane_cleanup(
        path: Path, expected_identity: tuple[int, int] | None = None,
    ) -> bool:
        if path.name.startswith("course-mode-lane-"):
            retained.append(path)
            return False
        return original(path, expected_identity)

    monkeypatch.setattr(gate, "_remove_owned_tree", refuse_lane_cleanup)
    result = gate.run_gate(candidate_file, "quick", lanes=(_lane("primary-failure", "fail"),))

    assert result["verdict"] == "BLOCKED"
    assert result["failedLane"] == "primary-failure"
    assert result["cleanupFailed"] is True
    assert result["retainedOwner"] == "current-process"
    assert result["retainedPaths"] == sorted({str(path) for path in retained})
    for path in set(retained):
        original(path)
```

Keep the existing successful-lane cleanup test unchanged; it must continue to expect `failedLane == "cleanup"` and no `cleanupFailed` key.

- [ ] **Step 2: Run RED and confirm cleanup masks the lane**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'failed_lane_plus_cleanup_failure or reports_retained_snapshot'
```

Expected: the new test fails because the current report says `failedLane: cleanup`; the pre-existing cleanup-only test passes.

- [ ] **Step 3: Make cleanup preserve an already classified failure**

Update `_cleanup_gate_owned()`:

```python
if retained:
    primary_failed_lane = report.get("failedLane")
    report["verdict"] = "BLOCKED"
    if isinstance(primary_failed_lane, str) and primary_failed_lane != "cleanup":
        report["cleanupFailed"] = True
    else:
        report["failedLane"] = "cleanup"
    report["retainedOwner"] = "current-process"
    report["retainedPaths"] = list(retained)
    return False
```

In the main lane loop, classify `result.error`, non-zero return code, and rejected/malformed JUnit skip state immediately after appending the lane record and before `_cleanup_gate_owned()`:

```python
lane_failed = result.error is not None or result.returncode != 0
if lane_failed:
    report["verdict"] = (
        "BLOCKED"
        if result.error in {"authority", "containment"}
        else "FAIL"
    )
    report["failedLane"] = lane.name
elif skip_state is not False:
    lane_failed = True
    report["verdict"] = "BLOCKED"
    report["failedLane"] = lane.name
if not _cleanup_gate_owned(report, lane_execution, execution_stage):
    break
```

After the existing operator-attestation and candidate-metadata checks, replace the old duplicated result classification with:

```python
if lane_failed:
    break
```

This ordering keeps cleanup mandatory, lets cleanup convert the overall verdict to `BLOCKED`, and retains the lane name only when the lane already had a primary result failure.

- [ ] **Step 4: Run GREEN and report-schema regressions**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'cleanup or timeout or output or authority or containment or pytest_junit_skip or failed_lane'
```

Expected: all selected tests pass. Cleanup-only failures still report `failedLane: cleanup`; dual failures report the lane name plus `cleanupFailed: true`; timeouts, containment, authority, output bounds, and skip rejection retain their previous verdict semantics.

- [ ] **Step 5: Commit failure reporting**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): preserve lane failure through cleanup"
```

### Task 5: Qualify the Implementation and Complete Independent Reviews

**Files:**
- Verify: `main/tbot-server/scripts/course_mode_release_gate.py`
- Verify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Verify: `main/tbot-server/tests/test_course_mode_contract.py`
- Verify: `main/tbot-server/tests/test_course_mode_python_compatibility.py`
- Verify: `docs/superpowers/specs/2026-09-03-course-mode-full-gate-hermetic-cleanup-design.md`

- [ ] **Step 1: Run syntax and focused qualification**

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m py_compile main/tbot-server/scripts/course_mode_release_gate.py
"$PY311" -m pytest -q \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_contract.py \
  main/tbot-server/tests/test_course_mode_python_compatibility.py
```

Expected: syntax succeeds and every selected test passes with zero skips on the approved qualification host. If a supported host interpreter is absent, stop and restore the prerequisite.

- [ ] **Step 2: Execute the explicit suite directly with candidate-bound roots**

```bash
ADMIN=/Users/manhhodinh/Documents/TBOT/robot/esp32-server
BACKEND=/Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
cd "$ADMIN/main/tbot-server"
COURSE_MODE_BACKEND_ROOT="$BACKEND" \
COURSE_MODE_BACKEND_SHA=bb6c484e69b71759462b5915138a262d82068b84 \
TBOT_BACKEND_WORKTREE="$BACKEND" \
TASK06_BACKEND_ROOT="$BACKEND" \
TASK06_FIRMWARE_ROOT=/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock \
"$PY311" -m pytest -q \
  tests/test_course_mode_contract.py \
  tests/test_course_mode_curriculum.py \
  tests/test_course_mode_curriculum_e2e.py \
  tests/test_course_mode_e2e_journeys.py \
  tests/test_course_mode_forwarder.py \
  tests/test_course_mode_resource_soak.py \
  tests/test_course_mode_runtime_compatibility.py \
  tests/test_course_mode_runtime_integration.py \
  tests/test_course_mode_task00_contract.py \
  tests/test_google_live_course_mode.py
```

Expected: zero failures and zero skips. If this direct diagnostic differs from the canonical sandbox, fix the candidate gate or suite ownership test-first rather than adding ambient dependencies.

- [ ] **Step 3: Run canonical repository qualification**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q
git diff --check
git status --short
git fsck --no-progress
```

Expected: the full current test count passes with zero failures and zero skips on the approved qualification host. `git diff --check` is clean; only planned commits exist; dangling Git objects are informational. If a supported host interpreter is absent, restore that prerequisite rather than accepting a reduced qualification result.

- [ ] **Step 4: Dispatch independent spec-compliance review**

Give the reviewer the approved spec, all implementation commit SHAs, and the exact diff. Require checks that:

1. the unlink proof requires exactly one link-count decrement;
2. all existing identity and race protections remain;
3. the ESP suite exactly matches the approved ten modules;
4. every selected path is verified against the candidate commit;
5. host, physical, live-DB, release/meta, and cross-repository tests are excluded;
6. dual failure reporting preserves the primary lane and remains `BLOCKED`.

Any finding returns to the responsible implementer for a test-first fix and spec re-review.

- [ ] **Step 5: Dispatch independent quality/security review**

After spec approval, give a second reviewer the approved diff and test evidence. Require checks for descriptor-relative TOCTOU safety, link-count edge cases, malformed Git output, allowlist drift, report-schema compatibility, cleanup on exceptions, and missing regression tests.

Any finding returns to the responsible implementer and then to both reviewers in order.

- [ ] **Step 6: Commit review-driven fixes and rerun qualification**

For each accepted finding, add a failing regression first, implement the minimal fix, rerun the focused file, and commit with a narrow message. Then repeat Steps 1-5 until both reviewers approve with no open findings.

### Task 6: Remove Verified `.27` Test Residue and Freeze `.28`

**Files and resources:**
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.27.json`
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.27`
- Remove after ownership verification: `/private/tmp/course-mode-lane-l1hkwcfp`
- Remove after ownership verification: `/private/tmp/course-mode-lane-yb4rzzdi`
- Remove after Compose ownership verification: Docker project `tbot-task4-candidate27`
- Create: candidate `.28` and its evidence root.

- [ ] **Step 1: Reverify exact residue ownership before deletion**

```bash
stat -f '%Su %Sp %d %i %N' \
  /private/tmp/course-mode-lane-l1hkwcfp \
  /private/tmp/course-mode-lane-yb4rzzdi
lsof +D /private/tmp/course-mode-lane-l1hkwcfp
lsof +D /private/tmp/course-mode-lane-yb4rzzdi
/usr/local/libexec/tbot-preflight/e757e1339818faa8544025e4154b91a549fc822ab229b177ed7378c6b661c5a5/docker \
  ps -a --filter label=com.docker.compose.project=tbot-task4-candidate27 \
  --format '{{.ID}} {{.Names}} {{.Status}}'
```

Expected: both directories are owned by the current operator, no process has an open handle inside them, and every listed container belongs to exactly `tbot-task4-candidate27`. Stop if any identity differs.

- [ ] **Step 2: Remove only the verified test resources**

Run the corrected cleanup only against the two verified absolute paths:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
"$PY311" -I -s - <<'PY'
from pathlib import Path
from scripts.course_mode_release_gate import _owned_tree_identity, _remove_owned_tree

for raw in (
    "/private/tmp/course-mode-lane-l1hkwcfp",
    "/private/tmp/course-mode-lane-yb4rzzdi",
):
    path = Path(raw)
    if path.exists():
        identity = _owned_tree_identity(path)
        if not _remove_owned_tree(path, identity):
            raise SystemExit(f"cleanup retained {path}")
PY
```

Remove only the exact candidate-27 containers, volumes, and network discovered in Step 1:

```bash
DOCKER=/usr/local/libexec/tbot-preflight/e757e1339818faa8544025e4154b91a549fc822ab229b177ed7378c6b661c5a5/docker
"$DOCKER" rm -f \
  tbot-task4-candidate27-seed-mysql \
  tbot-task4-candidate27-web \
  tbot-task4-candidate27-seed-pg \
  tbot-task4-candidate27-backend \
  tbot-task4-candidate27-derivative-media-1 \
  tbot-task4-candidate27-pg \
  tbot-task4-candidate27-redis \
  tbot-task4-candidate27-mysql
"$DOCKER" volume rm \
  tbot-task4-candidate27-mysql-data \
  tbot-task4-candidate27-pg-data \
  tbot-task4-candidate27-redis-data
"$DOCKER" network rm tbot-task4-candidate27
test ! -e /private/tmp/course-mode-lane-l1hkwcfp
test ! -e /private/tmp/course-mode-lane-yb4rzzdi
test -z "$("$DOCKER" ps -aq --filter label=com.docker.compose.project=tbot-task4-candidate27)"
test -z "$("$DOCKER" volume ls -q --filter label=com.docker.compose.project=tbot-task4-candidate27)"
test -z "$("$DOCKER" network ls -q --filter label=com.docker.compose.project=tbot-task4-candidate27)"
```

Do not use a broad glob, `rm -rf`, Docker system prune, volume prune, network prune, or remove `.27` evidence.

- [ ] **Step 3: Verify exact clean repository identities**

```bash
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server status --short
git -C /Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342 status --short
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock status --short
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server rev-parse HEAD
git -C /Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342 rev-parse HEAD
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock rev-parse HEAD
```

Expected: all trees are clean. Backend remains `bb6c484e69b71759462b5915138a262d82068b84`; firmware remains `b54c6ca33e9beb3747b44feceb7c64fea33fe1d6`; admin is the reviewed implementation SHA.

- [ ] **Step 4: Freeze and independently review candidate `.28`**

Build the candidate-bound web image from a Git archive of the reviewed admin commit, never from mutable working-tree files:

```bash
ADMIN=/Users/manhhodinh/Documents/TBOT/robot/esp32-server
DOCKER=/usr/local/libexec/tbot-preflight/e757e1339818faa8544025e4154b91a549fc822ab229b177ed7378c6b661c5a5/docker
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
ADMIN_SHA=$(git -C "$ADMIN" rev-parse HEAD)
BUILD_ROOT=$(mktemp -d /private/tmp/course-mode-28-web.XXXXXX)
git -C "$ADMIN" archive --format=tar "$ADMIN_SHA" | tar -xf - -C "$BUILD_ROOT"
WEB_REF="local/tbot-server-web:course-mode-physical-tft-$ADMIN_SHA"
"$DOCKER" build --pull=false \
  --build-arg WEB_NODE_IMAGE=node:20 \
  --build-arg VUE_APP_NEST_AUTH_DISABLED=false \
  --label "org.opencontainers.image.revision=$ADMIN_SHA" \
  --label "com.tbot.course-mode.build-source=reviewed-clean-git-worktree" \
  -f "$BUILD_ROOT/Dockerfile-web" -t "$WEB_REF" "$BUILD_ROOT"
WEB_ID=$("$DOCKER" image inspect "$WEB_REF" --format '{{.Id}}')
"$DOCKER" image inspect "$WEB_REF" --format '{{json .Config.Labels}}' | \
  /usr/bin/jq -e --arg sha "$ADMIN_SHA" \
  '."org.opencontainers.image.revision" == $sha and
   ."org.opencontainers.image.source" == "https://github.com/JSR-Algo/ESP-Server.git" and
   ."com.tbot.course-mode.build-source" == "reviewed-clean-git-worktree"'
cd "$ADMIN/main/tbot-server"
COURSE_MODE_BUILD_ROOT="$BUILD_ROOT" "$PY311" -I -s - <<'PY'
import os
from pathlib import Path
from scripts.course_mode_release_gate import _owned_tree_identity, _remove_owned_tree

path = Path(os.environ["COURSE_MODE_BUILD_ROOT"])
identity = _owned_tree_identity(path)
if not _remove_owned_tree(path, identity):
    raise SystemExit(f"cleanup retained {path}")
PY
```

Then create `.28` from `.27` with only the approved mutable fields changed:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
OLD=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.27.json
NEW=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.28.json
EVIDENCE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.28
CREATED=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
EXPIRES=$(date -u -v+7d '+%Y-%m-%dT%H:%M:%SZ')
mkdir -p "$EVIDENCE"
/usr/bin/jq \
  --arg id course-mode-2026-08-31.28 \
  --arg created "$CREATED" --arg expires "$EXPIRES" \
  --arg evidence "$EVIDENCE" --arg sha "$ADMIN_SHA" \
  --arg ref "$WEB_REF" --arg image "$WEB_ID" \
  '.candidateId=$id | .createdAt=$created | .expiresAt=$expires |
   .evidenceRoot=$evidence | .repositories.adminEsp.sha=$sha |
   .images.lessonStudioWeb.reference=$ref | .images.lessonStudioWeb.id=$image' \
  "$OLD" > "$NEW"
"$PY311" /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/scripts/course_mode_candidate_manifest.py \
  "$NEW" > "$EVIDENCE/00-candidate-validator.json"
/usr/bin/jq -e '.status == "pass" and .reasons == []' \
  "$EVIDENCE/00-candidate-validator.json"
"$PY311" /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/scripts/course_mode_operator_attestation.py \
  --candidate "$NEW" --output "$EVIDENCE/00-operator-attestation.json" \
  --confirm-trusted-operator-account --confirm-untrusted-automation-stopped
chmod 0444 "$NEW" "$EVIDENCE/00-candidate-validator.json" \
  "$EVIDENCE/00-operator-attestation.json"
stat -f '%HT %Sp %l %N' "$NEW" "$EVIDENCE/00-candidate-validator.json" \
  "$EVIDENCE/00-operator-attestation.json"
```

Expected: validator PASS; all three files are regular non-symlinks, `0444`, link count one; backend and firmware identities match `.27`; only candidate identity/timestamps/evidence root, admin SHA, and web image reference/ID differ. Dispatch independent freeze spec and quality/security reviews before running gates.

- [ ] **Step 5: Run `.28` software gates in order**

Bind `COURSE_MODE_OPERATOR_ATTESTATION` to `.28`, supply the exact assignment environment without printing secrets, and run canonical Quick:

```bash
CANDIDATE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.28.json
EVIDENCE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.28
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
export COURSE_MODE_OPERATOR_ATTESTATION="$EVIDENCE/00-operator-attestation.json"
scripts/course_robot_e2e_gates.sh --candidate "$CANDIDATE" --mode quick \
  --report "$EVIDENCE/01-quick-gate.json"
/usr/bin/jq -e '.verdict == "PASS" and .failedLane == null' \
  "$EVIDENCE/01-quick-gate.json"
```

Run the existing candidate-bound custom-lane harness for `firmware-renderer`, `firmware-handler`, `firmware-backward-compatibility`, and `cross-contract-parity`, writing `02-firmware-facing.json`. Require PASS before canonical Full:

```bash
scripts/course_robot_e2e_gates.sh --candidate "$CANDIDATE" --mode full \
  --report "$EVIDENCE/03-full-gate.json"
/usr/bin/jq -e \
  '.verdict == "PASS" and .failedLane == null and
   (.lanes | length) == 20 and
   (has("retainedPaths") | not) and (has("cleanupFailed") | not)' \
  "$EVIDENCE/03-full-gate.json"
```

Then run the isolated PostgreSQL 16 live-DB gate with two distinct loopback databases and write `04-live-db-gate.json`, followed by the existing evidence validator/audit into fresh `.28` paths. Require every report to be PASS, every pytest lane to contain zero skips, and no report to contain retained staging or cleanup metadata.

Do not rerun `.27` Full and do not overwrite any `.27` report.

- [ ] **Step 6: Stop at the physical authorization boundary**

If and only if all `.28` software evidence and both review tracks pass, report software readiness without claiming zero bugs. Request a fresh point-of-use confirmation for robot identity, serial port, candidate SHA, firmware application offset `0x20000`, binary size, and preserved partitions before any firmware flash, serial/HIL access, reset, reboot, power-cycle, or motion.
