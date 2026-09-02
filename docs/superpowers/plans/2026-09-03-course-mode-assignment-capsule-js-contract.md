# Course Mode Assignment Capsule JS Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the Task 4 JavaScript assignment runner with the gate-owned private runtime capsule, then qualify the real NEW-to-ROLLBACK runtime and firmware-facing outputs before broad release gates.

**Architecture:** The Python gate remains the authority for capsule creation, inode/descriptor identity, lifecycle, and cleanup. It injects an explicit owner/runtime path pair only into assignment lanes; a small CommonJS validator checks the named filesystem contract immediately before the runner creates media/TLS state or starts Docker. Candidate `.22` is retained as failure evidence, while the reviewed fix is frozen as `.23` before runtime and firmware-facing qualification.

**Tech Stack:** Python 3.11, pytest, Node.js 20 CommonJS and `node:test`, POSIX filesystem metadata, Docker Compose, Playwright WebKit, ESP32 firmware host-native tests.

---

## File Map

- Create `main/manager-web/scripts/task4-assignment-runtime.cjs` for the narrow JavaScript named-path validation contract.
- Modify `main/manager-web/scripts/run-task4-assignment-phase.cjs` to require and consume the validated capsule pair.
- Modify `main/manager-web/scripts/prepare-task4-media-templates.cjs` to revalidate the capsule pair and require the exact validated `runtime/media` path before filesystem or media-tool side effects.
- Modify `main/manager-web/scripts/task4-assignment-fixture.test.cjs` for real-filesystem JavaScript validation and runner source-contract tests.
- Modify `main/tbot-server/scripts/course_mode_release_gate.py` to inject the capsule owner path together with the existing runtime child.
- Modify `main/tbot-server/tests/test_course_mode_release_gate.py` for Python environment isolation and real NEW-to-ROLLBACK integration regressions.
- Modify `main/manager-web/course-mode.playwright.contract.json` so the new helper is candidate-bound by the existing source contract.
- Preserve `docs/docker/task4-admin-assignment/bootstrap.cjs`, `docs/docker/task4-admin-assignment/copy-file.cjs`, `_remove_owned_tree`, backend production code, firmware source, and all physical interfaces.

### Task 1: Specify Python Capsule-Pair Injection

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`

- [ ] **Step 1: Write failing assignment environment tests**

Extend the existing `_child_environment` capsule tests with assertions equivalent to:

```python
def test_assignment_environment_receives_exact_capsule_pair(candidate_file, tmp_path):
    candidate = json.loads(candidate_file.read_text())
    lane = next(item for item in gate.FULL_LANES if item.name == "admin-course-mode-assignment-new")
    capsule = gate.AssignmentRuntimeCapsule.create(())
    try:
        environment = gate._child_environment(
            candidate,
            _assignment_source(candidate_file),
            lane,
            source_candidate=candidate,
            assignment_runtime_capsule_root=capsule.root,
            assignment_runtime_root=capsule.runtime_root,
        )
        assert environment is not None
        assert environment["TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT"] == str(capsule.root)
        assert environment["TASK4_ASSIGNMENT_RUNTIME_ROOT"] == str(capsule.runtime_root)
    finally:
        assert capsule.cleanup() is True


def test_non_assignment_environment_never_receives_capsule_pair(candidate_file, tmp_path):
    candidate = json.loads(candidate_file.read_text())
    lane = _lane("ordinary", "pass")
    environment = gate._child_environment(
        candidate, {}, lane,
        assignment_runtime_capsule_root=tmp_path / "owner",
        assignment_runtime_root=tmp_path / "owner/runtime",
    )
    assert environment is not None
    assert "TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT" not in environment
    assert "TASK4_ASSIGNMENT_RUNTIME_ROOT" not in environment
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment_environment_receives_exact_capsule_pair or non_assignment_environment_never_receives_capsule_pair'
```

Expected: FAIL because `_child_environment` has no owner-path parameter and does not emit `TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT`.

- [ ] **Step 3: Implement minimal gate injection**

Add one constant and one keyword parameter:

```python
TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ENV = "TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT"

def _child_environment(
    candidate: dict,
    source: Mapping[str, str],
    lane: Lane,
    *,
    source_candidate: dict | None = None,
    assignment_runtime_capsule_root: Path | None = None,
    assignment_runtime_root: Path | None = None,
) -> dict[str, str] | None:
    ...
```

Inside the existing stateful-assignment branch, require both injected paths and set them after validating the source runtime root:

```python
if lane.name in STATEFUL_ASSIGNMENT_LANES:
    if assignment_runtime_capsule_root is None or assignment_runtime_root is None:
        return None
    environment[TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ENV] = str(
        assignment_runtime_capsule_root
    )
    environment["TASK4_ASSIGNMENT_RUNTIME_ROOT"] = str(assignment_runtime_root)
```

At the `run_gate` call site, pass `assignment_runtime.root` and `assignment_runtime.runtime_root`. Do not add the owner variable to `TASK4_ASSIGNMENT_CANDIDATE_ENV`; it is gate-owned, not operator-supplied.

- [ ] **Step 4: Run focused and lifecycle tests**

Run the Step 2 command, then:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment_capsule or assignment_lanes_share_capsule or assignment_lane_background_process'
```

Expected: all selected tests PASS with zero skips.

- [ ] **Step 5: Commit Python injection**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): inject assignment capsule authority"
```

### Task 2: Add The JavaScript Capsule Validator

**Files:**
- Create: `main/manager-web/scripts/task4-assignment-runtime.cjs`
- Modify: `main/manager-web/scripts/task4-assignment-fixture.test.cjs`

- [ ] **Step 1: Write real-filesystem validator tests**

Add tests that create a `0700` owner named `course-mode-assignment-runtime-*` with a direct `runtime` child. Import `validateAssignmentRuntimeCapsule` and assert the valid pair is returned. Add individual rejection cases for a missing owner variable, runtime path mismatch, owner symlink, runtime symlink, owner mode `0755`, runtime mode `0755`, prefix mismatch, and overlap with the admin/backend/firmware roots.

Use this helper shape in the tests:

```javascript
const { chmod, mkdir, mkdtemp, rm, symlink } = require('node:fs/promises');
const { join } = require('node:path');
const { validateAssignmentRuntimeCapsule } = require('./task4-assignment-runtime.cjs');

async function privateCapsule(t) {
  const owner = await mkdtemp(join(tmpdir(), 'course-mode-assignment-runtime-'));
  await chmod(owner, 0o700);
  const runtime = join(owner, 'runtime');
  await mkdir(runtime, { mode: 0o700 });
  t.after(() => rm(owner, { recursive: true, force: true }));
  return { owner, runtime };
}
```

- [ ] **Step 2: Run the Node tests and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
node --test scripts/task4-assignment-fixture.test.cjs
```

Expected: FAIL because `task4-assignment-runtime.cjs` does not exist.

- [ ] **Step 3: Implement the validator**

Create `task4-assignment-runtime.cjs` with this public API:

```javascript
'use strict';

const { lstatSync, realpathSync } = require('node:fs');
const { basename, dirname, isAbsolute, resolve, sep } = require('node:path');

const overlaps = (left, right) => (
  left === right || left.startsWith(`${right}${sep}`) || right.startsWith(`${left}${sep}`)
);

function validateAssignmentRuntimeCapsule(environment, protectedRoots, effectiveUid = process.getuid()) {
  const ownerValue = environment.TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT;
  const runtimeValue = environment.TASK4_ASSIGNMENT_RUNTIME_ROOT;
  if (![ownerValue, runtimeValue].every((value) => (
    typeof value === 'string' && value.length > 0 && !value.includes('\0') && isAbsolute(value)
  ))) throw new Error('Task4 assignment capsule paths are required');

  const owner = resolve(ownerValue);
  const runtime = resolve(runtimeValue);
  if (runtime !== resolve(owner, 'runtime')) {
    throw new Error('Task4 assignment runtime must be the capsule runtime child');
  }
  const ownerMetadata = lstatSync(owner);
  const runtimeMetadata = lstatSync(runtime);
  if (ownerMetadata.isSymbolicLink() || runtimeMetadata.isSymbolicLink()
      || !ownerMetadata.isDirectory() || !runtimeMetadata.isDirectory()) {
    throw new Error('Task4 assignment capsule paths must be real directories');
  }
  const realOwner = realpathSync(owner);
  const realRuntime = realpathSync(runtime);
  if (realRuntime !== resolve(realOwner, 'runtime')
      || !basename(realOwner).startsWith('course-mode-assignment-runtime-')
      || (ownerMetadata.mode & 0o777) !== 0o700
      || (runtimeMetadata.mode & 0o777) !== 0o700
      || ownerMetadata.uid !== effectiveUid || runtimeMetadata.uid !== effectiveUid) {
    throw new Error('Task4 assignment capsule authority mismatch');
  }
  for (const protectedRoot of protectedRoots.map((value) => realpathSync(value))) {
    if (overlaps(realOwner, protectedRoot) || overlaps(realRuntime, protectedRoot)) {
      throw new Error('Task4 assignment capsule overlaps a protected repository');
    }
  }
  return Object.freeze({ capsuleRoot: realOwner, runtimeRoot: realRuntime });
}

module.exports = { validateAssignmentRuntimeCapsule };
```

Remove the unused `dirname` import if the final implementation does not need it. Catch no validation errors; the runner must fail closed.

- [ ] **Step 4: Run Node tests and verify GREEN**

Run the Step 2 command.

Expected: all Task 4 fixture tests PASS.

- [ ] **Step 5: Commit the validator**

```bash
git add main/manager-web/scripts/task4-assignment-runtime.cjs \
  main/manager-web/scripts/task4-assignment-fixture.test.cjs
git commit -m "test(course-mode): specify assignment capsule contract"
```

### Task 3: Integrate The Runner And Candidate Source Contract

**Files:**
- Modify: `main/manager-web/scripts/run-task4-assignment-phase.cjs`
- Modify: `main/manager-web/scripts/prepare-task4-media-templates.cjs`
- Modify: `main/manager-web/scripts/task4-assignment-fixture.test.cjs`
- Modify: `main/manager-web/course-mode.playwright.contract.json`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Add failing runner and contract tests**

Update the Task 4 source-contract test to require `TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT`, import the validator, use its returned `runtimeRoot`, and reject the old `manager-web/output` prefix check. Add real-filesystem media-preparation tests that require the exact validated `<runtime>/media` path and prove invalid pairs, mismatched media roots, and direct invocation fail before directory creation or `ffmpeg`/`ffprobe`. Add the helper path to the expected Playwright source paths.

Add a Python integration regression using the real assignment NEW lane and candidate-bound Node. Keep the validator return intact, run the real media-preparation script with candidate-bound `ffmpeg`/`ffprobe` stubs, record successful validation and template preparation inside the capsule, and install Docker/Compose traps that fail if invoked. Assert `verdict == "PASS"`, all observations are inside the validated runtime, neither Docker trap ran, and the capsule was removed after the selected lane.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment_runner_accepts_gate_owned_capsule or playwright_contract'
cd main/manager-web
node --test scripts/task4-assignment-fixture.test.cjs
```

Expected: tests fail because the runner still applies the old output-root restriction, media preparation still requires `manager-web/output`, and the new helper is not bound in the contract.

- [ ] **Step 3: Integrate the validator before side effects**

In `run-task4-assignment-phase.cjs`, add the owner variable to `required`, validate after resolving repository roots but before `mkdirSync`, TLS generation, media preparation, image inspection, or Docker execution:

```javascript
const { validateAssignmentRuntimeCapsule } = require('./task4-assignment-runtime.cjs');

const { capsuleRoot, runtimeRoot } = validateAssignmentRuntimeCapsule(
  process.env,
  [repoRoot, backendRoot, firmwareRoot],
);
const mediaRoot = resolve(runtimeRoot, 'media');
const tlsRoot = resolve(runtimeRoot, 'tls');
```

Do not retain the previous `manager-web/output` check. Do not use `capsuleRoot` for deletion; cleanup remains gate-owned.

Add `main/manager-web/scripts/task4-assignment-runtime.cjs` to both `PLAYWRIGHT_SOURCE_PATHS` and `course-mode.playwright.contract.json` in the existing sorted location.

In `prepare-task4-media-templates.cjs`, import the validator and validate the same owner/runtime pair against the staged admin, backend, and firmware roots. Require the supplied media root to equal `resolve(runtimeRoot, 'media')`. Perform this validation before `mkdirSync`, `execFileSync`, or any other filesystem/media-tool side effect. Remove the old `manager-web/output` restriction; do not add a second cleanup owner.

- [ ] **Step 4: Run focused integration tests and verify GREEN**

Run the Step 2 commands plus:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment_capsule or assignment_lanes_share_capsule or source_contract'
```

Expected: all selected tests PASS with zero skips.

- [ ] **Step 5: Commit runner integration**

```bash
git add main/manager-web/scripts/run-task4-assignment-phase.cjs \
  main/manager-web/scripts/task4-assignment-fixture.test.cjs \
  main/manager-web/course-mode.playwright.contract.json \
  main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): accept gate-owned assignment capsule"
```

### Task 4: Source Qualification And Independent Reviews

**Files:**
- Review: `docs/superpowers/specs/2026-09-03-course-mode-assignment-capsule-js-contract-design.md`
- Review: all files modified in Tasks 1-3

- [ ] **Step 1: Run focused runtime and copy-only suites**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'assignment or capsule or process_containment'
cd main/manager-web
node --test scripts/lesson-studio-e2e-environment.test.cjs \
  scripts/lesson-studio-compose.test.cjs \
  scripts/task4-assignment-fixture.test.cjs
```

Expected: all selected Python and Node tests PASS; copy-only distinct-inode, stale-link replacement, and recovery cases remain green.

- [ ] **Step 2: Run the full canonical source suite**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_operator_attestation.py \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  tests/test_course_robot_e2e_gates_script.py
```

Expected: every collected test passes with zero failures and zero skips.

- [ ] **Step 3: Run static and repository checks**

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m py_compile main/tbot-server/scripts/course_mode_release_gate.py
node --check main/manager-web/scripts/task4-assignment-runtime.cjs
node --check main/manager-web/scripts/run-task4-assignment-phase.cjs
git diff --check
git status --short
git fsck --no-progress
```

Expected: syntax/diff checks exit zero, status is clean, and `git fsck` reports no object corruption. Existing dangling objects are informational only.

- [ ] **Step 4: Obtain two independent reviews**

Reviewer one checks exact compliance with the approved design, including explicit owner/runtime pairing, non-assignment isolation, failure-before-Docker behavior, runtime continuity, and `.22` preservation. Reviewer two checks path canonicalization, symlinks, modes, ownership, overlap logic, TOCTOU boundaries, descriptor authority, cleanup, secret handling, and absence of physical/production changes.

Expected: both reviewers return `APPROVED`; every actionable finding is resolved test-first and both reviews are repeated.

### Task 5: Preserve `.22` Failure Evidence And Freeze `.23`

**Files:**
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.22.json`
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/01-quick-gate.json`
- Preserve: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.22/02-full-gate.json`
- Create: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.23.json`
- Create: `.23` validator and operator-attestation files under its evidence root

- [ ] **Step 1: Lock and hash `.22` evidence**

Require `.22` Quick PASS and Full FAIL at `admin-course-mode-assignment-new`, with 14 recorded lanes and no retained fields. Set the Full report to mode `0444`, then record mode, link count, owner, size, and SHA-256 for candidate, validator, attestation, Quick, and Full.

- [ ] **Step 2: Build the exact reviewed web image**

Archive clean `HEAD` into a fresh `mktemp -d` build root, build `Dockerfile-web` for `linux/arm64` with `VUE_APP_NEST_AUTH_DISABLED=false`, and apply:

```bash
TASK23_ADMIN_SHA="$(git rev-parse HEAD)"
test -n "$TASK23_ADMIN_SHA"
```

Use these labels with the exact value stored in `TASK23_ADMIN_SHA`:

```text
org.opencontainers.image.revision=$TASK23_ADMIN_SHA
org.opencontainers.image.source=https://github.com/JSR-Algo/ESP-Server.git
com.tbot.course-mode.build-source=reviewed-clean-git-worktree
```

Use the trusted Docker executable from `.22`. Inspect exact architecture, OS, labels, immutable image ID, and remove only the build root after success.

- [ ] **Step 3: Create and validate candidate `.23`**

Copy `.22` and change only candidate ID to `course-mode-2026-08-31.23`, real UTC creation/expiry, `.23` evidence root, reviewed admin SHA, and new web image reference/ID. Preserve backend SHA/image, firmware SHA/bytes/hashes, database image ID, curriculum identity, and trusted tool descriptors. Run the canonical candidate validator and require `status == "pass"` with `reasons == []`.

Create the `.23` operator attestation with:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  main/tbot-server/scripts/course_mode_operator_attestation.py \
  --candidate /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.23.json \
  --output /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.23/00-operator-attestation.json \
  --confirm-trusted-operator-account \
  --confirm-untrusted-automation-stopped
```

After validation, set candidate, validator, and attestation to mode `0444` and verify link count one.

### Task 6: Prioritize Runtime And Firmware-Facing Qualification

**Files:**
- Create diagnostic reports outside the final `.23` evidence directory
- Create final Quick, Full, and live-db reports inside the `.23` evidence directory

- [ ] **Step 1: Run `.23` Quick as the minimum admission gate**

Run candidate-bound Quick with `COURSE_MODE_OPERATOR_ATTESTATION` set to the locked `.23` attestation. Require exactly four ordered lanes, every exit code zero, `verdict: PASS`, matching attestation hash, and no retained fields. Lock the report to `0444`.

- [ ] **Step 2: Run isolated real NEW-to-ROLLBACK first**

Use one `run_gate` invocation with only the canonical assignment NEW and ROLLBACK lane objects, a diagnostic report root outside `.23` final evidence, and these isolated values:

```text
LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME=tbot-task4-candidate23-runtime
LESSON_STUDIO_E2E_RESOURCE_PREFIX=tbot-task4-candidate23-runtime
LESSON_STUDIO_E2E_BACKEND_HOST_PORT=13123
LESSON_STUDIO_E2E_WEB_HOST_PORT=18123
TASK4_ASSIGNMENT_MEDIA_HOST_PORT=18423
TASK4_ASSIGNMENT_RUNTIME_ROOT=/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web/output/task4-candidate23-runtime
COURSE_MODE_ADMIN_E2E_READY=1
```

Load `JWT_PUBLIC_KEY`, `TBOT_DEVICE_MINT_SECRET`, `LESSON_ASSET_ORIGIN_BASE`, and `ROBOT_ESP_BASE_URL` from the healthy local test backend without printing their values. Require NEW and ROLLBACK exit zero, one shared capsule, no retained paths, and zero remaining containers/volumes/networks with the exact diagnostic project label.

- [ ] **Step 3: Run firmware-facing lanes before broad Full**

Use one candidate-bound custom-lane invocation containing, in order:

```text
firmware-renderer
firmware-handler
firmware-backward-compatibility
cross-contract-parity
```

Require all four exit codes zero, frozen firmware repository SHA `b54c6ca33e9beb3747b44feceb7c64fea33fe1d6`, firmware app SHA-256 `782020e2f8ac44bd197f57e9e126c196286a8223005de1e425f8290f12b28dff`, and no repository drift after the run. This is host-native validation only; do not flash, open serial, reboot, or move the robot.

- [ ] **Step 4: Run final `.23` Full**

Keep standard `tbot-ls-e2e` backend/web healthy on `3100/8102` with the exact `.23` candidate images. Run Full using:

```text
LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME=tbot-task4-candidate23
LESSON_STUDIO_E2E_RESOURCE_PREFIX=tbot-task4-candidate23
LESSON_STUDIO_E2E_BACKEND_HOST_PORT=13124
LESSON_STUDIO_E2E_WEB_HOST_PORT=18124
TASK4_ASSIGNMENT_MEDIA_HOST_PORT=18424
TASK4_ASSIGNMENT_RUNTIME_ROOT=/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web/output/task4-candidate23
COURSE_MODE_ADMIN_E2E_READY=1
```

Provide the same four nonempty local fixture values without logging secrets. Require exactly 20 ordered lanes, all exit codes zero, no skips, `verdict: PASS`, `failedLane: null`, matching attestation SHA, no retained fields, and zero exact-project Docker residues. Lock the report to `0444`.

- [ ] **Step 5: Run isolated PostgreSQL 16 live-db**

Start one loopback-only PostgreSQL 16 container pinned to candidate database image ID `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`, with a new exact network/volume and two different database names. Set `COURSE_MODE_V2_TEST_DATABASE_URL == COURSE_MODE_TEST_DATABASE_URL`, set `DATABASE_URL == COURSE_MODE_ROLLBACK_TEST_DATABASE_URL`, ensure the two URL identities differ, and leave `PRODUCTION_DATABASE_URL` unset. Require the same 20 Full lanes plus terminal `live-postgres`, all exit codes zero, no skips/retained fields, then lock the report to `0444` and remove only the exact live-db resources.

### Task 7: Software Evidence Audit And Final Reviews

**Files:**
- Audit the `.23` candidate, validator, attestation, Quick, Full, and live-db reports

- [ ] **Step 1: Run the software-only evidence audit**

Verify exact candidate/repository/tool/image/firmware identities, expected ordered lane lists, attestation hashes, mode `0444`, link count one, current owner, regular non-symlink files, size limits, and absence of secrets, tokens, audio, or transcripts. Do not run `course_mode_evidence_audit.py`, because it requires later physical G0-G10 evidence.

- [ ] **Step 2: Obtain two independent final reviews**

Reviewer one checks approved spec compliance and exact source/candidate identities. Reviewer two checks security, evidence integrity, runtime continuity, firmware-facing results, DB topology, exact cleanup residues, and absence of production/physical actions.

- [ ] **Step 3: Issue the software verdict**

Only when validator, Quick, isolated runtime, firmware-facing qualification, Full, live-db, audit, and both reviews pass, report `SOFTWARE_GO_FOR_ATTENDED_FLASH`. Ask for a fresh point-of-use confirmation before any serial access, firmware flash, HIL action, reboot, or robot motion.
