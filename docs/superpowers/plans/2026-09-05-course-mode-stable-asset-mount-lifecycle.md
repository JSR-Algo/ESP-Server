# Course Mode Stable Asset Mount Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Lesson Studio web container bound to stable candidate asset roots after assignment NEW/ROLLBACK snapshots are deleted.

**Architecture:** Preserve staged worktrees for lane execution and assignment media preparation, but introduce dedicated stable web mount roots sourced only from the original frozen candidate. Compose uses those roots for its four read-only web asset mounts, while compatibility fallbacks preserve existing non-gate callers.

**Tech Stack:** Python 3.11, pytest, Node.js test runner, Docker Compose, Playwright, Git, candidate-bound release gates.

---

## File Map

- Modify `main/tbot-server/tests/test_course_mode_release_gate.py`: lock the original-candidate versus staged-candidate environment contract for stateful assignment lanes.
- Modify `main/tbot-server/scripts/course_mode_release_gate.py`: provide stable backend and firmware mount roots to Playwright and assignment lanes.
- Modify `main/manager-web/scripts/lesson-studio-compose.test.cjs`: lock the Compose variable and read-only mount contract.
- Modify `docs/docker/docker-compose.lesson-studio-e2e.yml`: consume stable mount-root variables for the four web asset mounts.
- Create candidate and evidence files under `task-artifacts/course-mode-production-readiness`: freeze and qualify the new exact admin revision without changing backend or firmware identity.

### Task 1: Bind Stable Mount Roots Into Assignment Environments

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py:3940`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py:3340`

- [ ] **Step 1: Write the failing Python regression test**

Add a test using a copy of the candidate as the staged execution candidate. Replace its backend and firmware paths with `/private/tmp/staged-backend` and `/private/tmp/staged-firmware`, then call `_child_environment(..., source_candidate=source_candidate)` for `admin-course-mode-assignment-new` with the required assignment environment and capsule roots.

```python
def test_assignment_lane_keeps_stable_web_mount_roots_from_source_candidate(
    candidate_file: Path,
) -> None:
    source_candidate = json.loads(candidate_file.read_text(encoding="utf-8"))
    execution_candidate = json.loads(json.dumps(source_candidate))
    execution_candidate["repositories"]["backend"]["path"] = "/private/tmp/staged-backend"
    execution_candidate["repositories"]["firmware"]["path"] = "/private/tmp/staged-firmware"
    lane = next(
        item for item in gate.FULL_LANES
        if item.name == "admin-course-mode-assignment-new"
    )
    source = {name: "fixture" for name in gate.TASK4_ASSIGNMENT_CANDIDATE_ENV}
    source.update({
        "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME": "tbot-task4-stable-mounts",
        "LESSON_STUDIO_E2E_RESOURCE_PREFIX": "tbot-task4-stable-mounts",
        "LESSON_STUDIO_E2E_BACKEND_HOST_PORT": "13136",
        "LESSON_STUDIO_E2E_WEB_HOST_PORT": "18136",
        "TASK4_ASSIGNMENT_MEDIA_HOST_PORT": "28436",
        "TASK4_ASSIGNMENT_RUNTIME_ROOT": str(
            Path(source_candidate["repositories"]["adminEsp"]["path"])
            / "main/manager-web/output/task4-stable-mounts"
        ),
    })

    environment = gate._child_environment(
        execution_candidate,
        source,
        lane,
        source_candidate=source_candidate,
        assignment_runtime_capsule_root=Path("/private/tmp/capsule"),
        assignment_runtime_root=Path("/private/tmp/capsule/runtime"),
    )

    assert environment is not None
    assert environment["TBOT_BACKEND_WORKTREE"] == "/private/tmp/staged-backend"
    assert environment["TBOT_FIRMWARE_WORKTREE"] == "/private/tmp/staged-firmware"
    assert environment["TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT"] == source_candidate["repositories"]["backend"]["path"]
    assert environment["TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT"] == source_candidate["repositories"]["firmware"]["path"]
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k assignment_lane_keeps_stable_web_mount_roots_from_source_candidate
```

Expected: FAIL because assignment lanes do not yet receive the two `TBOT_LESSON_STUDIO_*_MOUNT_ROOT` variables.

- [ ] **Step 3: Implement the minimal environment change**

Change the existing Playwright-only block into a shared block for Playwright and stateful assignment lanes:

```python
    if (
        lane.name.startswith("admin-course-mode-playwright-")
        or lane.name in STATEFUL_ASSIGNMENT_LANES
    ):
        mount_candidate = source_candidate if source_candidate is not None else candidate
        environment.update({
            "TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT": mount_candidate["repositories"]["backend"]["path"],
            "TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT": mount_candidate["repositories"]["firmware"]["path"],
        })
    if lane.name.startswith("admin-course-mode-playwright-"):
        for name in (
            "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME", "LESSON_STUDIO_E2E_RESOURCE_PREFIX",
            "JWT_PUBLIC_KEY", "TBOT_DEVICE_MINT_SECRET", "LESSON_ASSET_ORIGIN_BASE",
            "ROBOT_ESP_BASE_URL", "LESSON_STUDIO_E2E_BACKEND_HOST_PORT",
            "LESSON_STUDIO_E2E_WEB_HOST_PORT",
        ):
            value = source.get(name)
            if value:
                environment[name] = value
```

- [ ] **Step 4: Run focused and surrounding Python tests**

Run:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'stable_web_mount_roots or stable_backend_authority_environment or assignment_candidate_environment or playwright_environment'
```

Expected: all selected tests PASS.

- [ ] **Step 5: Commit the environment contract**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): preserve stable assignment asset roots"
```

### Task 2: Make Compose Use The Stable Web Mount Roots

**Files:**
- Modify: `main/manager-web/scripts/lesson-studio-compose.test.cjs:40`
- Modify: `docs/docker/docker-compose.lesson-studio-e2e.yml:145`

- [ ] **Step 1: Write the failing Compose contract test**

Extend `the browser-and-robot origin serves canonical derivatives and seeded assets` with exact stable-variable assertions:

```javascript
  assert.match(web, /\$\{TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT:-\$\{TBOT_BACKEND_WORKTREE:\?[^}]+\}\}\/src\/lessons\/fixtures\/tvideo-raw-code\/assets\/asset-manifest\.json:\/usr\/share\/nginx\/html\/tvideo-demo\/asset-manifest\.json:ro/);
  assert.match(web, /\$\{TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT:-\$\{TBOT_BACKEND_WORKTREE:\?[^}]+\}\}\/src\/lessons\/fixtures\/tvideo-raw-code\/assets\/admin:\/usr\/share\/nginx\/html\/tvideo-demo\/admin:ro/);
  assert.match(web, /\$\{TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT:-\$\{TBOT_BACKEND_WORKTREE:\?[^}]+\}\}\/src\/lessons\/fixtures\/tvideo-raw-code\/assets\/esp-tft:\/usr\/share\/nginx\/html\/tvideo-demo\/esp-tft:ro/);
  assert.match(web, /\$\{TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT:-\$\{TBOT_FIRMWARE_WORKTREE:-\.\.\/\.\.\/\.\.\/TBOT-Firmware\}\}\/lesson\/assets:\/usr\/share\/nginx\/html\/tvideo-demo\/assets:ro/);
```

- [ ] **Step 2: Run the Node test and verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
node --test scripts/lesson-studio-compose.test.cjs
```

Expected: FAIL because Compose still interpolates `TBOT_BACKEND_WORKTREE` and `TBOT_FIRMWARE_WORKTREE` directly.

- [ ] **Step 3: Implement the four stable read-only mounts**

Replace only the four `web.volumes` entries:

```yaml
      - ${TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT:-${TBOT_BACKEND_WORKTREE:?set the built candidate backend worktree}}/src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json:/usr/share/nginx/html/tvideo-demo/asset-manifest.json:ro
      - ${TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT:-${TBOT_BACKEND_WORKTREE:?set the built candidate backend worktree}}/src/lessons/fixtures/tvideo-raw-code/assets/admin:/usr/share/nginx/html/tvideo-demo/admin:ro
      - ${TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT:-${TBOT_BACKEND_WORKTREE:?set the built candidate backend worktree}}/src/lessons/fixtures/tvideo-raw-code/assets/esp-tft:/usr/share/nginx/html/tvideo-demo/esp-tft:ro
      - ${TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT:-${TBOT_FIRMWARE_WORKTREE:-../../../TBOT-Firmware}}/lesson/assets:/usr/share/nginx/html/tvideo-demo/assets:ro
```

- [ ] **Step 4: Run the manager-web contract suites**

Run:

```bash
node --test scripts/lesson-studio-compose.test.cjs \
  scripts/lesson-studio-e2e-environment.test.cjs \
  scripts/reset-lesson-studio-e2e-state.test.cjs \
  scripts/task4-assignment-fixture.test.cjs
```

Expected: all tests PASS, including stale-mount rejection and rollback recreate assertions.

- [ ] **Step 5: Commit the Compose contract**

```bash
git add docs/docker/docker-compose.lesson-studio-e2e.yml \
  main/manager-web/scripts/lesson-studio-compose.test.cjs
git commit -m "fix(course-mode): keep web asset mounts stable"
```

### Task 3: Restore Base Service Configuration After Rollback

Before runtime verification, add and commit the post-ROLLBACK base-stack
restore:

- [ ] **Step 1: Write a failing launcher contract**

Extend `Task 4 release commands run candidate-bound NEW and ROLLBACK
orchestration` in `main/manager-web/scripts/task4-assignment-fixture.test.cjs`.
Require a separate base Compose argument list and require the following restore
to occur after the final rollback `verify-rollback` readback:

```javascript
baseComposeRun('up', '-d', '--wait', '--no-deps', '--force-recreate', 'backend', 'web');
```

Also assert this command is guarded by `phase === 'rollback'` so NEW retains the
state needed by the next lane.

- [ ] **Step 2: Run the launcher test and verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
node --test scripts/task4-assignment-fixture.test.cjs
```

Expected: FAIL because no base-Compose restore exists.

- [ ] **Step 3: Implement the minimal restore**

In `main/manager-web/scripts/run-task4-assignment-phase.cjs`, define a base
Compose argument list containing only the project and
`docker-compose.lesson-studio-e2e.yml`, plus `baseComposeRun`. After the final
phase readback, run the exact restore only for ROLLBACK. Reuse the already
pinned image IDs and current environment; do not run `down`, recreate
dependencies, or remove volumes.

- [ ] **Step 4: Run focused tests and commit**

```bash
node --test scripts/task4-assignment-fixture.test.cjs \
  scripts/lesson-studio-compose.test.cjs \
  scripts/reset-lesson-studio-e2e-state.test.cjs
git diff --check
git add scripts/run-task4-assignment-phase.cjs \
  scripts/task4-assignment-fixture.test.cjs
git commit -m "fix(course-mode): restore base stack after rollback"
```

Expected: all selected Node tests PASS and diff check is clean.

### Task 4: Verify Runtime Continuity Before Freezing A Candidate

**Files:**
- No source files
- Preserve diagnostics under `task-artifacts/course-mode-production-readiness/diagnostics/course-mode-stable-mounts-2026-09-05`

- [ ] **Step 1: Run source qualification**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
git diff --check
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py
cd main/manager-web
npm run test:course-mode:assignment-fixture
```

Expected: `git diff --check` produces no output; Python and Node suites PASS.

- [ ] **Step 2: Recreate the isolated stack from canonical roots**

Use project `tbot-task4-course-mode-37`, loopback ports `13137`, `18137`, and `28437`. Load `JWT_PUBLIC_KEY` and `TBOT_DEVICE_MINT_SECRET` from the existing healthy local test backend without printing them. Set both stable mount variables to the clean backend and firmware candidate roots before Compose `up -d --wait`.

```text
TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT=/Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342
TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT=/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock
```

Expected: backend and web are healthy, loopback-only, exact-image containers with exactly four canonical read-only web mounts.

- [ ] **Step 3: Run real NEW and ROLLBACK lanes against one stack**

Invoke `run_gate` programmatically with only the canonical NEW and ROLLBACK lane objects, using:

```text
LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME=tbot-task4-course-mode-37
LESSON_STUDIO_E2E_RESOURCE_PREFIX=tbot-task4-course-mode-37
LESSON_STUDIO_E2E_BACKEND_HOST_PORT=13137
LESSON_STUDIO_E2E_WEB_HOST_PORT=18137
TASK4_ASSIGNMENT_MEDIA_HOST_PORT=28437
TASK4_ASSIGNMENT_RUNTIME_ROOT=/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web/output/task4-course-mode-37
COURSE_MODE_ADMIN_E2E_READY=1
```

Expected: both lanes exit zero and cleanup reports no retained paths.

- [ ] **Step 4: Prove the stack survives snapshot cleanup**

Inspect `tbot-task4-course-mode-37-web` after gate cleanup. Require every `/usr/share/nginx/html/tvideo-demo/*` mount source to remain under the two canonical roots, exist on disk, use type `bind`, and have `RW=false`. Confirm the backend asset origin has returned to the caller-provided base URL. Then run Chromium desktop and WebKit desktop lanes against the same stack.

Expected: both browser lanes PASS without recreating the stack manually between assignment and Playwright.

### Task 5: Freeze And Qualify The Next Candidate

**Files:**
- Preserve: `task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-05.36.json`
- Preserve: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-05.36/*`
- Create: `task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-05.37.json`
- Create: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-05.37/*`

- [ ] **Step 1: Obtain independent source reviews**

Dispatch one reviewer for spec/behavior compliance and one for security/lifecycle integrity. Require no actionable findings before candidate creation.

- [ ] **Step 2: Build the exact-revision web image**

Build from a clean Git archive/worktree at the final admin HEAD. Tag it:

```text
local/tbot-server-web:course-mode-physical-tft-<full-admin-sha>
```

Require OCI revision equal to the full admin SHA, source equal to the ESP Server repository URL, and `com.tbot.course-mode.build-source=reviewed-clean-git-worktree`. Keep the backend image, backend SHA, firmware SHA, firmware binary and PostgreSQL image identity unchanged from `.35`.

- [ ] **Step 3: Create and validate `.37`**

Copy `.35`, changing only candidate ID, real UTC timestamps, evidence root, final admin SHA, and exact web image reference/ID. Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
CANDIDATE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-05.37.json
EVIDENCE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-05.37
"$PY311" main/tbot-server/scripts/course_mode_candidate_manifest.py "$CANDIDATE" \
  > "$EVIDENCE/00-candidate-validator.json"
/usr/bin/jq -e '.status == "pass" and .reasons == []' \
  "$EVIDENCE/00-candidate-validator.json"
"$PY311" main/tbot-server/scripts/course_mode_operator_attestation.py \
  --candidate "$CANDIDATE" --output "$EVIDENCE/00-operator-attestation.json" \
  --confirm-trusted-operator-account --confirm-untrusted-automation-stopped
chmod 0444 "$CANDIDATE" "$EVIDENCE/00-candidate-validator.json" \
  "$EVIDENCE/00-operator-attestation.json"
```

Expected: validator PASS and all three artifacts are regular, single-link, non-symlink files with mode `0444`.

- [ ] **Step 4: Run software gates in order**

Run candidate-bound Quick, Full, and live-db through `scripts/course_robot_e2e_gates.sh`, always using fresh report paths and the `.37` operator attestation. Require 4/4 Quick lanes, 20/20 Full lanes, and 21/21 live-db lanes with terminal `live-postgres`, `failedLane: null`, no retained paths, no cleanup failure, and matching attestation hash.

- [ ] **Step 5: Audit and clean isolated test resources**

Lock successful reports to `0444`, validate exact image/repository/firmware/database identities, scan evidence for secrets, and remove only containers, networks, volumes, and runtime output owned by exact `.37` test namespaces. Preserve every `.35` and `.36` PASS, FAIL, and BLOCKED artifact.

- [ ] **Step 6: Stop at the physical boundary**

Report software readiness without claiming zero bugs. Request fresh point-of-use confirmation of robot identity/MAC, serial port, firmware SHA `b54c6ca33e9beb3747b44feceb7c64fea33fe1d6`, app offset `0x20000`, app size `3637200`, and preserved partitions before flash, serial access, reboot, power-cycle, HIL, or robot motion.
