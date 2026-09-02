# Course Mode Assignment Runtime Capsule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve generated Task 4 assignment media across the ordered NEW-to-ROLLBACK lanes without weakening candidate isolation or secure cleanup.

**Architecture:** Add one gate-owned private runtime capsule whose filesystem identity is captured once and injected only into the two stateful assignment lanes. Keep repository/tool snapshots per lane, keep copy-only derivative materialization, and destroy the capsule after the last selected assignment lane or during failure unwind with the existing fail-closed retained-path semantics.

**Tech Stack:** Python 3.11, pytest, POSIX directory descriptors and inode identity, Node.js CommonJS, Docker Compose, Playwright WebKit.

**Working constraint:** Modify the existing canonical repository at `/Users/manhhodinh/Documents/TBOT/robot/esp32-server`; do not create a second release-gate version or implementation worktree.

---

## File Map

- Modify `main/tbot-server/scripts/course_mode_release_gate.py` to own, inject, verify, and clean one assignment runtime capsule.
- Modify `main/tbot-server/tests/test_course_mode_release_gate.py` for lifecycle, handoff, tamper, mutable-environment, and cleanup regressions.
- Preserve `docs/docker/task4-admin-assignment/copy-file.cjs` and `docs/docker/task4-admin-assignment/bootstrap.cjs`; no production or fixture rematerialization change is required.
- Preserve candidate `.21` and its immutable failure report; generate candidate `.22` only after implementation and reviews pass.

### Task 1: Specify The Capsule Primitive With Failing Tests

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`

- [ ] **Step 1: Add focused tests for capsule creation and cleanup**

Add tests beside the existing `LaneExecution` cleanup tests. They must use the real filesystem and assert private mode, captured identity, exact cleanup, and idempotence:

```python
def test_assignment_runtime_capsule_is_private_and_identity_bound() -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    root = capsule.root
    metadata = root.stat()
    assert root.name.startswith("course-mode-assignment-runtime-")
    assert stat.S_IMODE(metadata.st_mode) == 0o700
    assert capsule.identity == (metadata.st_dev, metadata.st_ino)
    assert (root / "media").is_dir()
    assert (root / "tls").is_dir()
    assert capsule.usable() is True
    assert capsule.cleanup() is True
    assert not root.exists()
    assert capsule.cleanup() is True


def test_assignment_runtime_capsule_rejects_path_replacement(
    tmp_path: Path,
) -> None:
    capsule = gate.AssignmentRuntimeCapsule.create(())
    original_remove = gate._remove_owned_tree
    moved = capsule.root.with_name(capsule.root.name + "-moved")
    capsule.root.rename(moved)
    capsule.root.mkdir(mode=0o700)
    assert capsule.usable() is False
    assert capsule.cleanup() is False
    assert capsule.retained_path() == moved
    original_remove(moved, capsule.identity)
    capsule.root.rmdir()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment_runtime_capsule_is_private or assignment_runtime_capsule_rejects_path_replacement'
```

Expected: collection or assertion failure because `AssignmentRuntimeCapsule` does not exist.

- [ ] **Step 3: Implement the minimal owned capsule**

Add a dedicated dataclass near `LaneExecution`. Reuse `_owned_tree_identity`, `_open_snapshot_directory`, `_directory_fd_path`, `_find_owned_tree`, and `_remove_owned_tree`; do not change `_remove_owned_tree`:

```python
@dataclass
class AssignmentRuntimeCapsule:
    root: Path
    identity: tuple[int, int]
    descriptor: int | None
    _retained_path: Path | None = None
    _cleanup_succeeded: bool | None = None

    @classmethod
    def create(cls, protected: Sequence[Path]) -> "AssignmentRuntimeCapsule":
        root = Path(tempfile.mkdtemp(prefix="course-mode-assignment-runtime-"))
        identity: tuple[int, int] | None = None
        descriptor: int | None = None
        try:
            root.chmod(0o700)
            identity = _owned_tree_identity(root)
            if any(_path_overlaps(root, path) for path in protected):
                raise ValueError("assignment runtime overlaps protected path")
            (root / "media").mkdir(mode=0o700)
            (root / "tls").mkdir(mode=0o700)
            descriptor = _open_snapshot_directory(root)
            return cls(root, identity, descriptor)
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            if os.path.lexists(root) and not _remove_owned_tree(root, identity):
                raise RetainedStagingError(root)
            raise

    def usable(self) -> bool:
        if self.descriptor is None:
            return False
        actual = _directory_fd_path(self.descriptor)
        opened = os.fstat(self.descriptor)
        return actual == self.root and (opened.st_dev, opened.st_ino) == self.identity

    def cleanup(self) -> bool:
        if self.descriptor is None:
            return self._cleanup_succeeded is True
        actual = _directory_fd_path(self.descriptor)
        if actual is not None and actual != self.root:
            self._retained_path = actual
            os.close(self.descriptor)
            self.descriptor = None
            self._cleanup_succeeded = False
            return False
        removed = _remove_owned_tree(self.root, self.identity)
        if not removed:
            self._retained_path = actual or _find_owned_tree(self.root.parent, self.identity) or self.root
        os.close(self.descriptor)
        self.descriptor = None
        self._cleanup_succeeded = removed
        return removed

    def retained_path(self) -> Path:
        return self._retained_path or self.root
```

Add `AssignmentRuntimeCapsule` to the accepted type union in `_cleanup_gate_owned` and `_cleanup_gate_owned_or_raise`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the command from Step 2.

Expected: both tests PASS, zero skips.

- [ ] **Step 5: Commit the primitive and tests**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "test(course-mode): specify assignment runtime capsule"
```

### Task 2: Drive NEW-To-ROLLBACK Handoff Test-First

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`

- [ ] **Step 1: Add a failing real-filesystem handoff test**

Create two custom lanes with the real assignment lane names and commands that communicate only through `TASK4_ASSIGNMENT_RUNTIME_ROOT`. Patch candidate-only authority checks, not the runtime behavior:

```python
def test_assignment_lanes_share_capsule_then_remove_it(
    candidate_file: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _assignment_source(candidate_file)
    observed: list[Path] = []
    commands = {
        "admin-course-mode-assignment-new": (
            sys.executable, "-c",
            "import os;from pathlib import Path;"
            "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
            "(p/'media').mkdir();(p/'media'/'handoff.bin').write_bytes(b'new')",
        ),
        "admin-course-mode-assignment-rollback": (
            sys.executable, "-c",
            "import os;from pathlib import Path;"
            "p=Path(os.environ['TASK4_ASSIGNMENT_RUNTIME_ROOT']);"
            "assert (p/'media'/'handoff.bin').read_bytes()==b'new'",
        ),
    }
    lanes = tuple(
        gate.Lane(name, "adminEsp", ".", commands[name], 5.0, gate.TASK4_ASSIGNMENT_CANDIDATE_ENV)
        for name in commands
    )
    original_run = gate.run_bounded_command

    def record_runtime(command, **kwargs):
        observed.append(Path(kwargs["env"]["TASK4_ASSIGNMENT_RUNTIME_ROOT"]))
        return original_run(command, **kwargs)

    monkeypatch.setattr(gate, "run_bounded_command", record_runtime)
    monkeypatch.setattr(gate, "assignment_input_sources_ready", lambda _candidate: True)
    monkeypatch.setattr(gate, "_container_tools_authorized", lambda _candidate: True)
    monkeypatch.setattr(gate, "playwright_browsers_authorized", lambda _candidate: True)
    monkeypatch.setattr(gate, "_backend_compiler_required", lambda _lane: False)

    result = gate.run_gate(
        candidate_file, "full", lanes=lanes, source_environment=source,
    )

    assert result["verdict"] == "PASS", result
    assert len(observed) == 2 and observed[0] == observed[1]
    assert not observed[0].exists()
```

- [ ] **Step 2: Add failing lifecycle and isolation cases**

Add this reusable lane constructor and capsule recorder beside the handoff test:

```python
def _stateful_assignment_lane(name: str, code: str) -> gate.Lane:
    return gate.Lane(
        name, "adminEsp", ".", (sys.executable, "-c", code), 5.0,
        gate.TASK4_ASSIGNMENT_CANDIDATE_ENV,
    )


def _record_assignment_capsules(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    roots: list[Path] = []
    original = gate.AssignmentRuntimeCapsule.create

    def create(protected):
        capsule = original(protected)
        roots.append(capsule.root)
        return capsule

    monkeypatch.setattr(gate.AssignmentRuntimeCapsule, "create", create)
    return roots
```

Add individual tests using `_assignment_source(candidate_file)` and the same four authority patches from Step 1:

```python
def test_assignment_capsule_is_cleaned_after_new_failure(candidate_file, monkeypatch):
    roots = _record_assignment_capsules(monkeypatch)
    lane = _stateful_assignment_lane(
        "admin-course-mode-assignment-new", "raise SystemExit(7)",
    )
    result = gate.run_gate(candidate_file, "full", lanes=(lane,), source_environment=_assignment_source(candidate_file))
    assert result["verdict"] == "FAIL"
    assert roots and not roots[0].exists()


def test_assignment_capsule_is_cleaned_after_rollback_failure(candidate_file, monkeypatch):
    roots = _record_assignment_capsules(monkeypatch)
    lanes = (
        _stateful_assignment_lane("admin-course-mode-assignment-new", "pass"),
        _stateful_assignment_lane("admin-course-mode-assignment-rollback", "raise SystemExit(8)"),
    )
    result = gate.run_gate(candidate_file, "full", lanes=lanes, source_environment=_assignment_source(candidate_file))
    assert result["verdict"] == "FAIL"
    assert roots and not roots[0].exists()


def test_single_selected_assignment_lane_does_not_leak_capsule(candidate_file, monkeypatch):
    roots = _record_assignment_capsules(monkeypatch)
    lane = _stateful_assignment_lane("admin-course-mode-assignment-new", "pass")
    result = gate.run_gate(candidate_file, "full", lanes=(lane,), source_environment=_assignment_source(candidate_file))
    assert result["verdict"] == "PASS"
    assert roots and not roots[0].exists()


def test_non_assignment_lane_never_receives_capsule(candidate_file, tmp_path):
    marker = tmp_path / "assignment-env-present"
    lane = _lane(
        "ordinary", "import os;from pathlib import Path;"
        f"Path({str(marker)!r}).touch() if 'TASK4_ASSIGNMENT_RUNTIME_ROOT' in os.environ else None",
    )
    result = gate.run_gate(candidate_file, "quick", lanes=(lane,), source_environment=_assignment_source(candidate_file))
    assert result["verdict"] == "PASS"
    assert not marker.exists()
```

Add a snapshot-failure case by wrapping `stage_execution_candidate`, raising `OSError("rollback snapshot")` on its second call, and asserting the one recorded capsule root no longer exists.

- [ ] **Step 3: Run the handoff/lifecycle group and verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment_capsule or assignment_lanes_share_capsule'
```

Expected: handoff fails because NEW's staged runtime is deleted and ROLLBACK receives a fresh path; lifecycle tests fail because `run_gate` does not own a capsule.

- [ ] **Step 4: Add stateful assignment-lane constants and runtime validation**

Add one canonical name set:

```python
STATEFUL_ASSIGNMENT_LANES = frozenset({
    "admin-course-mode-assignment-new",
    "admin-course-mode-assignment-rollback",
})
```

Snapshot the entire assignment source once before lane execution so NEW and ROLLBACK cannot observe different values from a mutable mapping:

```python
def _assignment_source_snapshot(source: Mapping[str, str]) -> dict[str, str] | None:
    missing = object()
    snapshot = {}
    try:
        for name in TASK4_ASSIGNMENT_CANDIDATE_ENV:
            value = source.get(name, missing)
            if value is not missing:
                snapshot[name] = value
    except (AttributeError, KeyError, RuntimeError, TypeError):
        return None
    return snapshot
```

Replace repeated two-name literals in runtime mapping, browser requirements, backend compiler requirements, and `run_gate` with this constant.

Keep `_assignment_runtime_root` as the lexical/operator-scope validator. Extend `_child_environment` with a parent-owned override:

```python
def _child_environment(
    candidate: dict,
    source: Mapping[str, str],
    lane: Lane,
    *,
    source_candidate: dict | None = None,
    assignment_runtime_root: Path | None = None,
) -> dict[str, str] | None:
    if lane.name in STATEFUL_ASSIGNMENT_LANES:
        validated = _assignment_runtime_root(
            source_candidate if source_candidate is not None else candidate,
            candidate,
            required_environment.get("TASK4_ASSIGNMENT_RUNTIME_ROOT"),
        )
        if validated is None:
            return None
        environment["TASK4_ASSIGNMENT_RUNTIME_ROOT"] = str(
            assignment_runtime_root if assignment_runtime_root is not None else validated
        )
```

The required environment must still be read once into `required_source`; never re-read the mutable source mapping to select the capsule.

- [ ] **Step 5: Own the capsule across the ordered lane sequence**

In `run_gate`, compute the last selected stateful assignment lane, create the capsule lazily immediately before the first stateful assignment lane is staged, and pass its root to `_child_environment`:

```python
assignment_runtime: AssignmentRuntimeCapsule | None = None
last_assignment_name = next(
    (lane.name for lane in reversed(selected) if lane.name in STATEFUL_ASSIGNMENT_LANES),
    None,
)
try:
    for lane in selected if report["verdict"] == "PASS" else ():
        if lane.name in STATEFUL_ASSIGNMENT_LANES:
            if assignment_runtime is None:
                protected = tuple(
                    Path(item["path"]) for item in candidate["repositories"].values()
                ) + (
                    candidate_path,
                    Path(required_source["TASK4_ASSIGNMENT_RUNTIME_ROOT"]),
                    *(tuple([report_path]) if report_path is not None else ()),
                    *(tuple([operator_binding.path]) if operator_binding is not None else ()),
                )
                assignment_runtime = AssignmentRuntimeCapsule.create(protected)
            if not assignment_runtime.usable():
                report["verdict"] = "BLOCKED"
                report["failedLane"] = "cleanup"
                break
        lane_environment = _child_environment(
            execution_candidate,
            required_source,
            lane,
            source_candidate=candidate,
            assignment_runtime_root=(assignment_runtime.root if assignment_runtime else None),
        )
        if lane.name == last_assignment_name:
            if not _cleanup_gate_owned(report, assignment_runtime):
                assignment_runtime = None
                break
            assignment_runtime = None
finally:
    if assignment_runtime is not None:
        _cleanup_gate_owned(report, assignment_runtime)
```

Preserve cleanup precedence: a capsule cleanup failure changes the report to `BLOCKED`, `failedLane: "cleanup"`, and records the exact retained path even if the lane command also failed.

- [ ] **Step 6: Run the focused group and verify GREEN**

Run the command from Step 3.

Expected: all capsule/handoff/lifecycle cases PASS with zero skips.

- [ ] **Step 7: Commit the handoff implementation**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): preserve assignment runtime across lanes"
```

### Task 3: Prove Tamper Resistance And Prevent Regression

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Verify unchanged: `docs/docker/task4-admin-assignment/copy-file.cjs`
- Verify unchanged: `docs/docker/task4-admin-assignment/bootstrap.cjs`

- [ ] **Step 1: Add failing tamper and mutable-mapping tests**

Add a rename test whose NEW command moves `TASK4_ASSIGNMENT_RUNTIME_ROOT` to the same name plus `-moved`, recreates the original directory, and exits zero. The ROLLBACK command touches a marker outside the capsule. Assert:

```python
assert result["verdict"] == "BLOCKED"
assert result["failedLane"] == "cleanup"
assert result["retainedOwner"] == "current-process"
assert result["retainedPaths"] == [str(recorded_root.with_name(recorded_root.name + "-moved"))]
assert not rollback_marker.exists()
```

After assertions, remove the moved original with `_remove_owned_tree(moved, captured_identity)` and remove only the empty replacement directory.

Add a mutable-source test with this mapping and record the runtime passed to both commands:

```python
class MutableAssignmentSource(dict[str, str]):
    runtime_reads = 0

    def get(self, key, default=None):
        if key == "TASK4_ASSIGNMENT_RUNTIME_ROOT":
            self.runtime_reads += 1
            return valid_runtime if self.runtime_reads == 1 else str(attacker_runtime)
        return super().get(key, default)


assert result["verdict"] == "PASS"
assert source.runtime_reads == 1
assert observed_runtime[0] == observed_runtime[1]
assert not attacker_runtime.exists()
```

Add a cleanup-precedence test that patches `_remove_owned_tree` to return `False` only when `path.name.startswith("course-mode-assignment-runtime-")`, runs a failing NEW command, and asserts:

```python
assert result["verdict"] == "BLOCKED"
assert result["failedLane"] == "cleanup"
assert result["retainedOwner"] == "current-process"
assert result["retainedPaths"] == [str(recorded_root)]
```

Restore the real remover and delete the recorded capsule after the assertions.

- [ ] **Step 2: Run tests and verify RED where protection is missing**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment_capsule_rename or assignment_capsule_cannot_be_redirected or assignment_capsule_cleanup_failure'
```

Expected: at least one case fails until identity is checked before each assignment lane and cleanup failure precedence is correct.

- [ ] **Step 3: Add the minimal identity checks**

Before injecting the capsule into either assignment lane, require `assignment_runtime.usable()`. If it is false, skip command execution and call `_cleanup_gate_owned` so the moved inode is reported. Do not follow the replacement path and do not remove unrelated content at the original name.

- [ ] **Step 4: Run the tamper group and verify GREEN**

Run the command from Step 2.

Expected: all three cases PASS; moved original trees are retained and reported until the test explicitly removes them using the captured identity.

- [ ] **Step 5: Run related security and copy-only regression suites**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
node --test scripts/lesson-studio-e2e-environment.test.cjs \
  scripts/lesson-studio-compose.test.cjs \
  scripts/task4-assignment-fixture.test.cjs
```

Expected: entire release-gate test file passes with zero skips; related Node suite passes, including distinct inode, identical bytes, stale hard-link replacement, and injected-copy-failure recovery.

- [ ] **Step 6: Run static checks and inspect scope**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m py_compile main/tbot-server/scripts/course_mode_release_gate.py
git diff --check
git diff --stat d0a354b3e69c28c6edc918ee62104eee677463a5..HEAD
git status --short
```

Expected: compile/diff checks exit zero; only the canonical release gate, its tests, and approved documentation are changed.

- [ ] **Step 7: Commit regression coverage if it was not included in Task 2**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "test(course-mode): harden assignment runtime ownership"
```

Skip this commit only when `git status --short` proves Task 2 already committed all required tamper tests.

### Task 4: Independent Reviews And Source Qualification

**Files:**
- Review: `docs/superpowers/specs/2026-09-02-course-mode-assignment-runtime-capsule-design.md`
- Review: `main/tbot-server/scripts/course_mode_release_gate.py`
- Review: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Run independent spec-compliance review**

Dispatch a fresh reviewer with the approved spec and commit range `d0a354b3e69c28c6edc918ee62104eee677463a5..HEAD`. Require explicit findings on shared identity, NEW survival, final cleanup, early-failure cleanup, retained-path semantics, non-assignment isolation, and `.21` preservation.

- [ ] **Step 2: Run independent quality/security review**

Dispatch a different reviewer for descriptor lifetime, path replacement, symlink handling, mutable mappings, exception unwind, verdict precedence, repeated cleanup, broad deletion risk, and accidental changes to copy-only or `_remove_owned_tree`.

- [ ] **Step 3: Resolve every actionable finding test-first**

For each finding, add the smallest failing test, observe RED, implement one root-cause fix, observe GREEN, commit, and repeat both reviews. Do not batch unrelated findings.

- [ ] **Step 4: Run the full canonical release-gate suite**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_operator_attestation.py \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  tests/test_course_robot_e2e_gates_script.py
```

Expected: every test passes, zero failures and zero skips.

- [ ] **Step 5: Require a clean committed repository**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
git diff --check
git status --short
git fsck --no-progress
```

Expected: no diff/status output and no Git object errors.

### Task 5: Preserve `.21`, Remove Its Exact Disposable Stack, And Freeze `.22`

**Files:**
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.21.json`
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.21/02-full-gate.json`
- Create: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.22.json`
- Create: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/00-candidate-validator.json`
- Create: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/00-operator-attestation.json`

- [ ] **Step 1: Verify and hash immutable `.21` failure evidence**

```bash
/usr/bin/jq -e '.candidateId=="course-mode-2026-08-31.21" and .verdict=="FAIL" and .failedLane=="admin-course-mode-assignment-rollback" and (.lanes|length)==15' \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.21/02-full-gate.json
/usr/bin/stat -f '%Sp %l %Su:%Sg %z %N' \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.21.json \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.21/02-full-gate.json
/usr/bin/shasum -a 256 \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.21.json \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.21/02-full-gate.json
```

Expected: candidate and report are one-link regular immutable files; the report remains FAIL at ROLLBACK.

- [ ] **Step 2: Remove only the exact `.21` Compose project**

Use the trusted Compose executable and the original project name `tbot-task4-candidate21` with the canonical base/new/rollback files. Run `down --volumes --remove-orphans`; then require zero containers, volumes, and networks carrying that exact Compose project label. Do not enumerate or delete unrelated resources.

- [ ] **Step 3: Build the exact reviewed web image**

Set `ESP_SHA=$(git rev-parse HEAD)`, create the build root with `mktemp -d "/private/tmp/tbot-web-${ESP_SHA}.XXXXXX"`, and archive the exact clean commit into that directory. Build `Dockerfile-web` with `VUE_APP_NEST_AUTH_DISABLED=false` and labels:

```text
org.opencontainers.image.revision=$ESP_SHA
org.opencontainers.image.source=https://github.com/JSR-Algo/ESP-Server.git
com.tbot.course-mode.build-source=reviewed-clean-git-worktree
```

Inspect the new image and require `linux/arm64`, exact labels, and a new immutable image ID.

- [ ] **Step 4: Assemble and validate candidate `.22`**

Copy `.21` and change only candidate ID, real UTC creation/expiry, `.22` evidence root, reviewed admin SHA, and the exact new web image reference/ID. Preserve backend SHA `bb6c484e69b71759462b5915138a262d82068b84`, firmware SHA `b54c6ca33e9beb3747b44feceb7c64fea33fe1d6`, firmware bytes/hash, database image ID, curriculum identity, and trusted tool descriptors unless validation proves actual drift.

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  main/tbot-server/scripts/course_mode_candidate_manifest.py \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.22.json \
  > /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/00-candidate-validator.json
/usr/bin/jq -e '.validator=="course-mode-candidate.v1" and .status=="pass" and .reasons==[]' \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/00-candidate-validator.json
```

Create the exact `.22` operator attestation with the canonical CLI and both explicit confirmations. Set candidate, validator, and attestation to mode `0444` only after validation passes.

### Task 6: Run `.22` Software Gates And Reviews

**Files:**
- Create: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/01-quick-gate.json`
- Create: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/02-full-gate.json`
- Create: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/03-live-db-gate.json`

- [ ] **Step 1: Run Quick**

Run the candidate-bound Quick gate once with a fresh report path. Require exactly four ordered lanes, all `exitCode: 0`, `verdict: PASS`, `failedLane: null`, matching candidate/attestation identity, and no retained fields. Lock the report to `0444`.

- [ ] **Step 2: Run Full with isolated assignment resources**

Use:

```text
LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME=tbot-task4-candidate22
LESSON_STUDIO_E2E_RESOURCE_PREFIX=tbot-task4-candidate22
LESSON_STUDIO_E2E_BACKEND_HOST_PORT=13122
LESSON_STUDIO_E2E_WEB_HOST_PORT=18122
TASK4_ASSIGNMENT_MEDIA_HOST_PORT=18422
TASK4_ASSIGNMENT_RUNTIME_ROOT=/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web/output/task4-candidate22
```

Keep the standard `tbot-ls-e2e` web healthy on `3100/8102` using the exact `.22` web image. Require exactly 20 ordered Full lanes, every exit code zero, no skip, `verdict: PASS`, `failedLane: null`, matching attestation SHA, and no retained fields. Lock the report to `0444` and verify the exact `tbot-task4-candidate22` project leaves no container, volume, or network.

- [ ] **Step 3: Run isolated PostgreSQL 16 live-db**

Start one loopback-only PostgreSQL 16 container pinned to image ID `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`, with a new exact project/network/volume and two different database names. Set:

```text
COURSE_MODE_V2_TEST_DATABASE_URL == COURSE_MODE_TEST_DATABASE_URL
DATABASE_URL == COURSE_MODE_ROLLBACK_TEST_DATABASE_URL
```

The two URL groups must differ by `(host, port, database)`, host must be literal `127.0.0.1` or `::1`, and `PRODUCTION_DATABASE_URL` must be unset. Use a trap to clean only those exact resources. Require the same 20 Full lanes plus terminal `live-postgres`, all exit codes zero, no skips or retained fields; lock the report to `0444`.

- [ ] **Step 4: Run software-only evidence audit**

Require exactly these five `.22` files: validator, attestation, Quick, Full, and live-db. Validate candidate/repository/tool/image/firmware identities, ordered lane lists, attestation hashes, mode `0444`, link count one, current owner, regular non-symlink files, size limits, and absence of secrets, tokens, audio, or transcripts. Do not run `course_mode_evidence_audit.py`; it requires later physical G0-G10 evidence.

- [ ] **Step 5: Obtain two independent final reviews**

Reviewer one checks exact spec compliance and source/candidate identities. Reviewer two checks security, evidence integrity, DB topology, cleanup residues, and absence of production/physical actions. Any unresolved actionable finding or identity drift yields `SOFTWARE_NO_GO`.

- [ ] **Step 6: Issue the software verdict**

Only when validator, Quick, Full, live-db, software audit, and both reviews all pass, report `SOFTWARE_GO_FOR_ATTENDED_FLASH`. This verdict does not authorize flashing. Ask for a fresh point-of-use confirmation before any serial access, firmware flash, HIL action, reboot, or robot motion.
