# Course Mode Production-Readiness E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one immutable local/staging Course Mode candidate for `english-6month-4-6` and qualify it through exhaustive software, browser, database, ESP, firmware, physical robot, soak, evidence, and rollback gates without deploying or cutting over production.

**Architecture:** Converge the existing 26-week backend branch, Admin implementation, ESP runtime, and renderer-v5 firmware into one signed SHA-set. Extend existing modules and test runners rather than creating a second Course Mode implementation. Every gate consumes the same candidate manifest, writes machine-readable evidence, and blocks downstream work on identity drift, skipped required checks, P0/P1 findings, or failed rollback.

**Tech Stack:** NestJS/TypeScript/PostgreSQL/Vitest, Vue 2/Element UI/Playwright, Python 3/Pytest/WebSocket/Docker Compose, ESP-IDF/C++17/native sanitizers, ESP32-S3 AC:20 hardware, Ed25519/SHA-256 evidence.

---

## Source Specification

- `docs/superpowers/specs/2026-08-29-course-mode-production-readiness-e2e-design.md`

## Repository Boundaries

- Backend candidate: `/Users/manhhodinh/Documents/TBOT/tbot-backend`
- Existing backend implementation source: `/Users/manhhodinh/Documents/TBOT/tbot-backend/.worktrees/course-mode-26week-single-version`
- Admin and ESP: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server`
- Firmware: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware`
- Canonical release gate: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server/scripts/course_robot_e2e_gates.sh`
- Evidence root: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness`

## Non-Negotiable Safety Rules

- Preserve unrelated dirty files. The current protected ESP file
  `main/tbot-server/tests/test_lesson_voice_output_discipline.py` must retain SHA-256
  `08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3`
  unless a separately reviewed task explicitly incorporates it.
- Do not deploy production, mutate production DB/Admin data, publish production
  lessons, assign production devices, or enable production rollout flags.
- Do not flash until Tasks 1-9 pass and the attended operator gives explicit
  point-of-use authorization.
- Flash only the application partition at `0x20000`; never write bootloader,
  partition table, OTA, NVS, PHY, reserved, or generated-assets partitions.
- Never claim `PASS` for skipped hardware, network, credential, or real-service
  gates. Never claim "100% bug-free."
- One implementer edits a repository at a time. Each task requires independent
  spec review followed by independent quality/security review.

### Task 1: Freeze the Candidate and Remove Worktree Runtime Authority

**Files:**
- Create: `main/tbot-server/scripts/course_mode_candidate_manifest.py`
- Create: `main/tbot-server/tests/test_course_mode_candidate_manifest.py`
- Modify: `main/tbot-server/scripts/course_mode_26week_simulation.py`
- Modify: `main/tbot-server/tests/test_course_mode_curriculum_e2e.py`
- Create: `docs/qa/ad-hoc/2026-08-29-course-mode-candidate-freeze.md`
- Integrate existing commits into: `/Users/manhhodinh/Documents/TBOT/tbot-backend`

- [ ] **Step 1: Write failing tests that forbid an implicit backend worktree**

```python
def test_backend_source_must_be_explicit_and_committed(tmp_path, monkeypatch):
    monkeypatch.delenv("COURSE_MODE_BACKEND_ROOT", raising=False)
    result = resolve_backend_root(tmp_path)
    assert result.error == "BACKEND_ROOT_REQUIRED"


def test_candidate_rejects_unlisted_dirty_file(candidate):
    candidate["repositories"]["adminEsp"]["dirtyExceptions"] = []
    assert validate_candidate(candidate) == ["repositories.adminEsp.dirty"]
```

- [ ] **Step 2: Run the tests and prove the current fallback fails**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py \
  tests/test_course_mode_curriculum_e2e.py
```

Expected: FAIL because the simulation searches the 26-week worktree implicitly
and no candidate-manifest validator exists.

- [ ] **Step 3: Integrate the existing backend feature with minimal diff**

In `/Users/manhhodinh/Documents/TBOT/tbot-backend`, merge the reviewed
`feature/course-mode-26week-single-version` history without reimplementing its
modules. Before resolving conflicts, record the source tip and target tip. Keep
one copy of:

```text
src/lessons/course-mode/curriculum-course-mode.ts
src/lessons/course-mode/curriculum-pedagogy.ts
src/database/migrations/126_course_mode_curriculum.sql
scripts/verify-course-mode-curriculum.mjs
```

Reject the integration if it creates a parallel `v3`, `next`, `new`, or second
renderer implementation.

- [ ] **Step 4: Add the signed candidate manifest schema**

The validator must require this exact top-level shape:

```python
REQUIRED_KEYS = {
    "candidateId", "createdAt", "expiresAt", "course", "repositories",
    "images", "firmware", "database", "curriculum", "tools", "evidenceRoot",
}
REPOSITORIES = {"backend", "adminEsp", "firmware"}
```

Each repository contains absolute path, exact 40-hex SHA, branch, remote URL,
and hash-bound dirty exceptions. The curriculum contains course ID/key,
renderer-v5 ID, contract identity, lesson/activity counts, and source checksum.

- [ ] **Step 5: Delete implicit worktree discovery**

`course_mode_26week_simulation.py` accepts only `--backend-root` or
`COURSE_MODE_BACKEND_ROOT`. Resolve the path securely, require the candidate's
backend SHA, and return stable JSON `BACKEND_ROOT_REQUIRED` or
`BACKEND_IDENTITY_MISMATCH` rather than searching sibling worktrees.

- [ ] **Step 6: Verify source convergence**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npm run lint
npm run typecheck
npx vitest run src/lessons/course-mode tests/verify-course-mode-curriculum.spec.ts
npm run build

cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
COURSE_MODE_BACKEND_ROOT=/Users/manhhodinh/Documents/TBOT/tbot-backend \
  python3 scripts/course_mode_26week_simulation.py
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py \
  tests/test_course_mode_curriculum_e2e.py
```

Expected: backend gates pass; simulator reports exactly 26 lessons, the expected
activity count, six pedagogies, eleven response classes, and the pinned backend
SHA. No sibling worktree is accessed.

- [ ] **Step 7: Commit separately in affected repositories**

```bash
git add main/tbot-server/scripts/course_mode_candidate_manifest.py \
  main/tbot-server/scripts/course_mode_26week_simulation.py \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_curriculum_e2e.py \
  docs/qa/ad-hoc/2026-08-29-course-mode-candidate-freeze.md
git commit -m "test(course-mode): freeze production readiness candidate"
```

Backend integration uses its own non-amended merge commit.

### Task 2: Make the Canonical Release Gate Exhaustive

**Files:**
- Create: `scripts/course_robot_e2e_gates.sh`
- Create: `tests/test_course_robot_e2e_gates_script.py`
- Create: `main/tbot-server/scripts/course_mode_release_gate.py`
- Create: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Add a failing source-contract test for missing Course Mode lanes**

```python
def test_full_gate_contains_every_required_course_mode_lane():
    script = ESP_REPOSITORY_GATE.read_text()
    for marker in (
        "verify-course-mode-curriculum",
        "test_course_mode_curriculum_e2e.py",
        "test_course_mode_runtime_integration.py",
        "test_course_mode_physical_tft_preflight.py",
        "run_host_native_lesson_cinematic_renderer_test.sh",
        "test:e2e:course-mode",
    ):
        assert marker in script
```

- [ ] **Step 2: Verify the test fails**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT
cd robot/esp32-server
python3 -m pytest -q tests/test_course_robot_e2e_gates_script.py
```

Expected: FAIL because the root full gate omits focused Course Mode suites and
Admin browser Course Mode E2E.

- [ ] **Step 3: Add a candidate-bound gate orchestrator**

`course_mode_release_gate.py` reads the candidate manifest, runs commands with
bounded output/timeouts, and writes:

```json
{
  "candidateId": "...",
  "verdict": "PASS|FAIL|SKIPPED|BLOCKED",
  "lanes": [{"name": "backend-full", "exitCode": 0, "durationMs": 1}],
  "failedLane": null
}
```

It must refuse an identity drift before each lane and never use ambient `dist`,
`coverage`, or cached fixture directories as source authority.

The canonical executable lives inside the ESP repository so its bytes are bound
to the candidate Git SHA. The historical workspace-level
`/Users/manhhodinh/Documents/TBOT/scripts/course_robot_e2e_gates.sh` is not a Git
authority and must not be used as release evidence; it may remain only as an
untrusted developer convenience wrapper.

- [ ] **Step 4: Wire quick, full, live-db, and physical-preflight modes**

`full` includes backend lint/typecheck/full tests/build/curriculum verifier,
Admin logic/browser build and Course Mode Playwright, ESP full Course Mode suites,
firmware renderer/handler/backward-compatibility tests, and cross-contract parity.
`live-db` adds real PostgreSQL migration/materialization tests. `physical-preflight`
is read-only and does not flash.

- [ ] **Step 5: Verify deterministic failure and success aggregation**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT
cd robot/esp32-server
python3 -m pytest -q tests/test_course_robot_e2e_gates_script.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
bash -n scripts/course_robot_e2e_gates.sh
```

Expected: PASS. A fixture lane returning non-zero must make the aggregate report
`FAIL` and prevent later dependent lanes.

- [ ] **Step 6: Commit**

```bash
git add scripts/course_robot_e2e_gates.sh \
  tests/test_course_robot_e2e_gates_script.py \
  main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "test(course-mode): add canonical production readiness gate"
```

### Task 3: Add First-Class Lifecycle Rollback

**Files:**
- Modify: `/Users/manhhodinh/Documents/TBOT/tbot-backend/src/lessons/course-mode/curriculum-course-mode.ts`
- Modify: `/Users/manhhodinh/Documents/TBOT/tbot-backend/src/lessons/course-mode/curriculum-course-mode.spec.ts`
- Modify: `/Users/manhhodinh/Documents/TBOT/tbot-backend/src/lessons/lesson-assignment.course-mode-curriculum.spec.ts`
- Modify: `/Users/manhhodinh/Documents/TBOT/tbot-backend/tests/integration/course-mode-local-materializer.integration.spec.ts`
- Modify: `/Users/manhhodinh/Documents/TBOT/tbot-backend/src/database/migrations/126_course_mode_curriculum.sql`

- [ ] **Step 1: Write failing rollback contract tests**

```ts
it('rolls back only the exact signed cutover snapshot', async () => {
  const cutover = await runLifecycle('cutover', approvedMaterializationReceipt);
  const result = await runLifecycle('rollback', cutover.receipt);
  expect(result.source.status).toBe('PUBLISHED');
  expect(result.replacement.status).toBe('ARCHIVED');
  expect(result.assignmentLessonId).toBe(cutover.sourceLessonId);
});

it.each(['wrong-hmac', 'wrong-snapshot', 'gc-reference', 'active-session'])(
  'fails closed for %s', async (fault) => {
    await expect(runRollback(fault)).rejects.toMatchObject({ code: expect.stringMatching(/^ROLLBACK_/) });
  },
);
```

- [ ] **Step 2: Run tests and verify `rollback` is unsupported**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
npx vitest run src/lessons/course-mode/curriculum-course-mode.spec.ts \
  src/lessons/lesson-assignment.course-mode-curriculum.spec.ts \
  tests/integration/course-mode-local-materializer.integration.spec.ts
```

Expected: FAIL because lifecycle modes contain no `rollback`.

- [ ] **Step 3: Add the mode without adding a parallel lifecycle**

```ts
export type CurriculumLifecycleMode =
  | 'dry-run' | 'materialize' | 'cutover' | 'archive'
  | 'rollback' | 'gc-dry-run' | 'gc';
```

Rollback accepts only the signed cutover/archive receipt, locks the lifecycle row
and both lesson rows, verifies source/replacement IDs and checksums, rejects GC'd
or ambiguous state, restores source publication/assignment, archives the
replacement, and emits a new HMAC receipt bound to both snapshots.

- [ ] **Step 4: Add failure injection and concurrency coverage**

Test interruption before/after each update, two simultaneous rollback callers,
rollback retry, stale receipt, active session policy, already-rolled-back state,
and post-archive rollback. Each case must be atomic and idempotent.

- [ ] **Step 5: Run live PostgreSQL verification**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend
COURSE_MODE_V2_TEST_DATABASE_URL="$COURSE_MODE_LOCAL_TEST_DATABASE_URL" \
  npm run test:integration:course-mode-v2:postgres
npm run lint
npm run typecheck
npm run build
```

Expected: forward, repeated-forward, cutover, archive, rollback, retry, and GC
reference-lock scenarios pass on real PostgreSQL.

- [ ] **Step 6: Commit**

```bash
git add src/lessons/course-mode/curriculum-course-mode.ts \
  src/lessons/course-mode/curriculum-course-mode.spec.ts \
  src/lessons/lesson-assignment.course-mode-curriculum.spec.ts \
  tests/integration/course-mode-local-materializer.integration.spec.ts \
  src/database/migrations/126_course_mode_curriculum.sql
git commit -m "feat(course-mode): add receipt-bound curriculum rollback"
```

### Task 4: Add Real Course Mode Admin Browser Journeys

**Files:**
- Create: `main/manager-web/e2e/lesson-studio/course-mode-authoring.spec.js`
- Create: `main/manager-web/e2e/lesson-studio/course-mode-lifecycle.spec.js`
- Create: `main/manager-web/e2e/lesson-studio/course-mode-insights.spec.js`
- Modify: `main/manager-web/e2e/lesson-studio/helpers/admin-api.js`
- Modify: `main/manager-web/playwright.config.js`
- Modify: `main/manager-web/package.json`
- Modify as defects require: `main/manager-web/src/views/LessonEditor.vue`
- Modify as defects require: `main/manager-web/src/components/lesson/CourseModeActivityTimeline.vue`

- [ ] **Step 1: Add failing Playwright author/save/reload coverage**

```js
test('authors and reloads the canonical Course Mode contract', async ({ page }) => {
  const lesson = await api.createDraftForCourse(COURSE_ID);
  await page.goto(`/lesson-editor?id=${lesson.id}`);
  await page.getByTestId('course-mode-tab').click();
  await page.getByTestId('course-mode-import-week').selectOption('1');
  await page.getByTestId('course-mode-save').click();
  await page.reload();
  await expect(page.getByTestId('course-mode-activity-row')).toHaveCount(week1ActivityCount);
  await expect(page.getByTestId('projected-step-editor')).toBeDisabled();
});
```

- [ ] **Step 2: Add lifecycle, auth, and concurrency journeys**

Cover asset triple selection, renderer-v5 preview, validation errors, publish,
clone, published immutability, assignment, rollback visibility, Course Insights,
two-admin stale update, role denial, IDOR, double-submit, and retry.

- [ ] **Step 3: Add WebKit and Chromium projects**

Desktop viewport is at least `1440x900`; mobile is `390x844`. WebKit is the Safari
qualification lane. Screenshots verify the 480x320 safe area, layer order,
object/robot geometry, spacing, entrance timing controls, and retained static
background/object state after activity changes.

- [ ] **Step 4: Run red tests, fix only observed product defects, rerun**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run test:e2e:course-mode -- --project=chromium
npm run test:e2e:course-mode -- --project=webkit
npm run test
npm run build
```

Expected: all browser journeys pass against the local real backend/database; no
page error, console error, duplicate lesson version, or preview/contract drift.

- [ ] **Step 5: Commit**

```bash
git add main/manager-web/e2e/lesson-studio/course-mode-*.spec.js \
  main/manager-web/e2e/lesson-studio/helpers/admin-api.js \
  main/manager-web/playwright.config.js main/manager-web/package.json \
  main/manager-web/src/views/LessonEditor.vue \
  main/manager-web/src/components/lesson/CourseModeActivityTimeline.vue
git commit -m "test(admin): cover Course Mode browser lifecycle"
```

Only add product files that changed to fix a reproduced failure.

### Task 5: Build a Real Cross-Process Course Mode Journey

**Files:**
- Create: `main/tbot-server/scripts/course_mode_cross_process_e2e.py`
- Create: `main/tbot-server/tests/test_course_mode_cross_process_e2e.py`
- Modify: `main/tbot-server/scripts/course_mode_26week_simulation.py`
- Modify: `docs/docker/docker-compose.course-mode-physical-tft.yml`
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_compose.py`

- [ ] **Step 1: Write a failing journey test that forbids private adapters**

```python
def test_journey_uses_http_websocket_and_database_readback(fake_stack):
    report = run_journey(fake_stack.urls, lesson_key="w01-greetings-politeness")
    assert report["boundaries"] == [
        "admin-http", "postgres", "assignment-http", "manifest-http",
        "device-websocket", "completion-http", "progress-http",
    ]
    assert report["privateAdapterCalls"] == 0
```

- [ ] **Step 2: Implement authenticated public-boundary primitives**

The runner creates or selects a local draft through Admin HTTP, saves/publishes
the Course Mode contract, assigns the local AC:20 identity, fetches the manifest,
connects through the real ESP WebSocket, drives response events, observes
firmware-facing frames, posts completion, and reads progress/insights back.

- [ ] **Step 3: Add the six representative pedagogy journeys**

Run W1 TPR, picture discovery, story/context, role-play, spiral checkpoint, and
W26 showcase. Each report records lesson/version/session/delivery/checksum IDs
and rejects duplicates, missing terminal state, or count disagreement.

- [ ] **Step 4: Add cross-process fault injection at existing seams**

Cover WebSocket drop, ESP restart, backend restart at activity boundary,
completion `429`/`5xx`, stale manifest, checksum mismatch, cache unavailable,
ASR unavailable, silence/help, and duplicate/delayed/out-of-order ACKs. Do not add
a production-only chaos endpoint.

- [ ] **Step 5: Run focused and full cross-process gates**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
python3 -m pytest -q tests/test_course_mode_cross_process_e2e.py \
  tests/test_course_mode_curriculum_e2e.py \
  tests/test_course_mode_runtime_integration.py \
  tests/test_google_live_course_mode.py
python3 scripts/course_mode_cross_process_e2e.py \
  --candidate "$COURSE_MODE_CANDIDATE" --all-26 --report "$COURSE_MODE_REPORT"
```

Expected: 26/26 deterministic journeys and six real cross-process journeys pass;
all progress/completion totals agree.

- [ ] **Step 6: Commit**

```bash
git add main/tbot-server/scripts/course_mode_cross_process_e2e.py \
  main/tbot-server/scripts/course_mode_26week_simulation.py \
  main/tbot-server/tests/test_course_mode_cross_process_e2e.py \
  docs/docker/docker-compose.course-mode-physical-tft.yml \
  main/tbot-server/tests/test_course_mode_physical_tft_compose.py
git commit -m "test(course-mode): add public-boundary cross-process E2E"
```

### Task 6: Close ESP Replay, Cache, Disk, and Resource Gaps

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_runtime_integration.py`
- Modify: `main/tbot-server/tests/test_course_mode_curriculum_e2e.py`
- Modify: `main/tbot-server/tests/test_lesson_sd_pack_gc.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Modify: `main/tbot-server/core/lesson/course_orchestrator.py`
- Modify: `main/tbot-server/core/lesson/sd_pack_materializer.py`
- Create: `main/tbot-server/scripts/course_mode_resource_soak.py`
- Create: `main/tbot-server/tests/test_course_mode_resource_soak.py`

- [ ] **Step 1: Add failing regressions for known replay/progress/disk risks**

```python
@pytest.mark.parametrize("sequence", [None, 0, 1, 1, 2])
def test_same_assignment_multi_session_delivery_is_logically_once(sequence): ...

def test_nine_runtime_activities_produce_nine_authoritative_progress_rows(): ...

def test_disk_floor_blocks_new_pack_without_evicting_rollback_reference(): ...
```

- [ ] **Step 2: Reproduce each failure independently**

Run the exact new test node, capture the first bad state transition, and fix the
source boundary rather than adding report-side dedupe.

- [ ] **Step 3: Implement minimal runtime fixes**

Use `(assignmentId, lessonSessionId, deliveryId)` for delivery identity, preserve
attempt/session snapshots across reconnect, reconcile completion from durable
outbox state, and make cache eviction reference-aware. Reject null/ambiguous
delivery identity after session start.

- [ ] **Step 4: Add resource soak**

The soak runs 52 lesson simulations, 60 minutes virtual/real idle as selected,
100 WebSocket reconnects, and 10 SD cache cycles. Report heap RSS, descriptors,
threads/tasks, cache bytes, retry counts, and monotonic slopes.

- [ ] **Step 5: Verify all ESP Course Mode and voice regressions**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
python3 -m pytest -q tests/test_course_mode_*.py \
  tests/test_google_live_course_mode.py tests/test_course_orchestrator.py \
  tests/test_lesson_sd_pack_gc.py
python3 scripts/course_mode_resource_soak.py --cycles 52 --ws-reconnects 100 --sd-cycles 10
```

Expected: zero lost/duplicate progress, zero stuck session, no protected rollback
pack eviction, and bounded resource slopes.

- [ ] **Step 6: Commit**

Stage only files needed for reproduced defects. Do not stage the protected voice
test unless its incorporation is separately approved and reviewed.

```bash
git commit -m "fix(course-mode): harden replay progress and resource bounds"
```

### Task 7: Complete Firmware Renderer-v5 Host and HIL Coverage

**Files:**
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/tests/native/lesson_cinematic_renderer_test.cc`
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/tests/native/lesson_handler_test.cc`
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/tests/test_course_mode_renderer_v4_persistence.py`
- Create: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/tests/test_course_mode_26week_semantics.py`
- Create: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/scripts/run_course_mode_hil_gate.sh`
- Modify only for reproduced failures: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/main/lesson_cinematic_renderer.cc`
- Modify only for reproduced failures: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/main/lesson_handler.cc`

- [ ] **Step 1: Add failing renderer state and recovery tests**

```cpp
TEST(CourseModeRenderer, RetainsStaticLayersAcrossActivityOutcome) { /* assert bg/object identity */ }
TEST(CourseModeRenderer, ReplaysEntranceOnlyWhenRequested) { /* assert entrance count */ }
TEST(CourseModeRenderer, RebootReconcilesDeliveryWithoutDuplicateMotion) { /* durable identity */ }
```

The Python semantic test consumes all 26 canonical contracts and asserts every
firmware-facing frame contains supported renderer/phase/layer/action values.

- [ ] **Step 2: Run red tests under sanitizers**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware
bash scripts/run_host_native_lesson_cinematic_renderer_test.sh
bash scripts/run_host_native_lesson_handler_test.sh
python3 -m pytest -q tests/test_course_mode_*.py
```

- [ ] **Step 3: Fix only measured firmware defects**

Preserve static background/object layers, correct authored geometry, bounded
prepare/tick timing, one motion after successful/degraded visual ACK, durable
delivery recovery, NVS fail-closed behavior, and safe-rest on stop/error.

- [ ] **Step 4: Implement the non-flashing HIL gate**

`run_course_mode_hil_gate.sh` verifies the connected board identity, serial
protocol, current firmware capability, TFT test pattern, SD read/cache behavior,
audio drain, motion ACK, stop/rest, reconnect, and reboot recovery. It refuses to
flash and emits JSON evidence.

- [ ] **Step 5: Run full firmware verification**

Run:

```bash
bash scripts/run_host_native_lesson_coverage.sh --txt --print-summary
bash scripts/run_host_native_lesson_cinematic_renderer_test.sh
bash scripts/run_host_native_lesson_handler_test.sh
python3 -m pytest -q tests/test_course_mode_*.py \
  tests/test_internal_ram_guardrails.py \
  tests/test_lesson_network_stack_contract.py \
  tests/test_tbot_connect_runtime_fsm_contract.py
```

Expected: no sanitizer failure, crash, WDT/OOM simulation, unsupported frame,
duplicate motion, or unbounded resource behavior.

- [ ] **Step 6: Commit**

```bash
git commit -m "test(firmware): qualify renderer-v5 Course Mode runtime"
```

### Task 8: Align Physical Receipt, Ledger, and Evidence with Curriculum v5

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py`
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py`
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py`
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_ledger_validate.py`
- Create: `main/tbot-server/scripts/course_mode_evidence_audit.py`
- Create: `main/tbot-server/tests/test_course_mode_evidence_audit.py`

- [ ] **Step 1: Add failing tests that reject renderer-v4 pilot identity**

```python
def test_receipt_requires_candidate_curriculum_v5(receipt, candidate):
    receipt["lessonId"] = "course-mode-pilot-cat-ball"
    receipt["renderer"] = "teebot-lesson-renderer.v4"
    assert validate(receipt, candidate) == ["receipt.lesson", "receipt.renderer"]
```

- [ ] **Step 2: Replace hard-coded pilot constants with signed candidate fields**

Receipt and ledger validation must bind candidate ID, course, lesson/version,
replacement/materialization/cutover receipt, renderer-v5 contract/manifest/asset
checksums, repository/image/firmware identities, MAC, app offset, partition/NVS
anchors, assignment/session/delivery IDs, DB terminal readback, and evidence
file hashes.

- [ ] **Step 3: Add aggregate evidence auditing**

The auditor rejects stale timestamps, wrong candidate, missing sidecars,
duplicate journey IDs, contradictory PASS/FAIL, absent checksums, unsafe output,
raw child audio/transcript, or a historical artifact used as current proof.

- [ ] **Step 4: Verify strict redaction and deterministic JSON**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
python3 -m pytest -q tests/test_course_mode_physical_tft_receipt_verify.py \
  tests/test_course_mode_physical_tft_ledger_validate.py \
  tests/test_course_mode_evidence_audit.py
```

Expected: candidate-bound fixtures pass; renderer-v4, wrong SHA, missing receipt,
duplicate completion, secrets, transcript, and stale evidence fail with stable
reason codes.

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py \
  main/tbot-server/scripts/course_mode_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/tests/test_course_mode_physical_tft_ledger_validate.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py
git commit -m "test(course-mode): bind physical evidence to curriculum v5"
```

### Task 9: Close Darwin Preflight Trust and Provision Operator Signing

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`
- Create: `main/tbot-server/scripts/provision_course_mode_preflight_tools.sh`
- Create: `main/tbot-server/tests/test_provision_course_mode_preflight_tools.py`
- Create: `main/tbot-server/docs/course-mode-physical-preflight-signing.md`

- [ ] **Step 1: Preserve a failing post-verification swap regression**

```python
def test_darwin_tool_cannot_change_after_verification_before_exec(...):
    result = run_with_post_verify_swap()
    assert result == ("", False, "executable")
    assert not attacker_marker.exists()
```

- [ ] **Step 2: Remove user-owned sealed-copy execution from the trust path**

On Darwin, accept only exact signed paths whose file and every parent component
are root-owned, non-symlink, and not group/world writable. Provision exact
approved Docker and Compose bytes under:

```text
/usr/local/libexec/tbot-preflight/<sha256>/docker
/usr/local/libexec/tbot-preflight/<sha256>/docker-compose
```

Pin the Command Line Tools or Xcode Git implementation path, not `/usr/bin/git`.
Keep Linux `/proc/self/fd` verified-FD execution.

- [ ] **Step 3: Add a reviewed provisioning script**

The script requires an attended `sudo`, copies from already verified source FDs,
sets `root:wheel` and `0555`, verifies every parent component, rehashes installed
files, and prints only public paths/hashes. It never creates a private signing
key or modifies Docker.app.

- [ ] **Step 4: Provision the Ed25519 operator key only with explicit authority**

Generate the private key outside repositories/evidence with mode `0600`; pin only
the raw public key and SHA-256 fingerprint. Add an offline signing command that
canonicalizes the expected-identity JSON and writes a detached 64-byte signature.

- [ ] **Step 5: Run the complete preflight suite and real tool smoke**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py \
  tests/test_provision_course_mode_preflight_tools.py
python3 scripts/course_mode_physical_tft_preflight.py \
  --expected "$COURSE_MODE_EXPECTED" \
  --expected-identity "$COURSE_MODE_IDENTITY" \
  --expected-identity-signature "$COURSE_MODE_IDENTITY_SIG" \
  --output "$COURSE_MODE_PREFLIGHT_REPORT"
```

Expected: target macOS PASS path succeeds with real pinned Git/Docker/Compose;
path swap, mutable parent, symlink, wrong owner/mode/hash/signature, and hostile
PATH/config all fail closed. No flash command is executed.

- [ ] **Step 6: Commit source and public key pin only**

```bash
git commit -m "fix(course-mode): close physical preflight trust boundary"
```

Never commit the private key, unsigned identity, or operator secret material.

### Task 10: Run Full Software Qualification and Freeze the Flash Candidate

**Files:**
- Create: `docs/qa/ad-hoc/2026-08-29-course-mode-production-readiness-software.md`
- Generate: `task-artifacts/course-mode-production-readiness/<candidate-id>/G1-G9/*`

- [ ] **Step 1: Regenerate the final candidate manifest from committed SHAs**

Require only the approved hash-bound dirty exception. Pin image IDs, firmware
binary, migration head, curriculum checksum, tools, signer, and evidence root.

- [ ] **Step 2: Run all canonical software gates from fresh source**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT
COURSE_MODE_CANDIDATE="$COURSE_MODE_CANDIDATE" \
  LOG_DIR="$COURSE_MODE_EVIDENCE/G1" \
  ./robot/esp32-server/scripts/course_robot_e2e_gates.sh full

COURSE_MODE_CANDIDATE="$COURSE_MODE_CANDIDATE" \
  LOG_DIR="$COURSE_MODE_EVIDENCE/G2" \
  ./robot/esp32-server/scripts/course_robot_e2e_gates.sh live-db
```

- [ ] **Step 3: Run independent spec and quality/security reviews**

Reviewers inspect the cumulative candidate, reports, skips, identity manifest,
and diff. Any P0/P1 or required skip returns to the owning task.

- [ ] **Step 4: Build twice and compare firmware/application identities**

Two clean firmware builds must produce the approved reproducibility result or a
documented deterministic normalization boundary. Freeze `xiaozhi.bin`, size,
SHA-256, partition map, and known-good rollback app.

- [ ] **Step 5: Issue software GO/NO-GO**

Only `SOFTWARE_GO_FOR_ATTENDED_FLASH` authorizes asking the operator for the
point-of-use flash confirmation. This is not production GO.

- [ ] **Step 6: Commit the redacted report**

```bash
git add docs/qa/ad-hoc/2026-08-29-course-mode-production-readiness-software.md
git commit -m "docs(course-mode): record software readiness evidence"
```

### Task 11: Attended App-Only Flash and Rollback Rehearsal

**Files:**
- Generate only: `task-artifacts/course-mode-production-readiness/<candidate-id>/G7-preflight/*`
- Generate only: `task-artifacts/course-mode-production-readiness/<candidate-id>/G10-rollback/*`

- [ ] **Step 1: Acquire the exclusive physical lease**

Confirm AC:20 identity, sole serial/device ownership, safety observer, motion
clearance, emergency power isolation, stable LAN/power, and evidence capture.

- [ ] **Step 2: Run signed preflight and stop on any non-PASS result**

Capture partition table, security info, current app, full NVS, firmware identity,
and generated-assets boundary. Validate candidate and rollback compatibility.

- [ ] **Step 3: Rehearse the known-good rollback first**

Flash only the known-good app partition, read back app/NVS, boot, and run boot,
network, TFT, audio, motion, stop/rest, and W1 smoke. If this fails, stop the
campaign; the candidate must not be flashed.

- [ ] **Step 4: Ask for explicit candidate flash authorization**

The operator confirmation must occur after seeing the exact MAC, port, app hash,
offset, size, and preserved partitions.

- [ ] **Step 5: Flash candidate app only and read back before boot**

Use the approved `esptool` command with `--after no-reset`, write only
`0x20000 xiaozhi.bin`, then read back the application and NVS. App bytes must
match; pre-boot NVS must be byte-identical.

- [ ] **Step 6: Boot and run the non-mutating HIL smoke**

Verify capability, network, display, audio, motion ACK, stop/rest, SD/cache, and
reboot recovery. Any anomaly triggers the rehearsed rollback.

- [ ] **Step 7: Validate and sign preflight/flash receipts**

Run receipt, ledger, and evidence auditors. All must exit zero before physical
lesson journeys begin.

### Task 12: Run Six Representative Physical Journeys

**Files:**
- Create: `main/tbot-server/scripts/course_mode_physical_matrix.py`
- Create: `main/tbot-server/tests/test_course_mode_physical_matrix.py`
- Generate: `task-artifacts/course-mode-production-readiness/<candidate-id>/G7-representative/*`

- [ ] **Step 1: Add a dry-run matrix test**

```python
def test_matrix_covers_six_pedagogies_and_required_faults():
    matrix = build_matrix()
    assert {row.pedagogy for row in matrix} == SIX_PEDAGOGIES
    assert REQUIRED_PHYSICAL_PATHS <= {path for row in matrix for path in row.paths}
```

- [ ] **Step 2: Implement an operator-controlled matrix runner**

The runner never flashes or deploys. It allocates a fresh local assignment and
session, prints one attended action at a time, records safe identifiers/log
windows/resource counters, and validates the result before advancing.

- [ ] **Step 3: Execute the representative matrix**

Distribute normal, silence/help, ASR unavailable, disconnect/resume, cache
recovery, and controlled power-cycle paths across TPR W1, picture discovery,
story/context, role-play, checkpoint, and W26 showcase.

- [ ] **Step 4: Validate each receipt immediately**

Require correct TFT geometry/layer order/entrance frequency, audio/motion order,
safe-rest, completion/safe-exit, backend progress, no duplicate delivery, and
bounded resource counters.

- [ ] **Step 5: Stop and fix on first unexplained anomaly**

Preserve evidence before any retry. Add a deterministic regression test before
changing runtime code, rerun owning software gates, then restart physical
qualification from the invalidated gate.

- [ ] **Step 6: Commit only the reusable runner/tests**

```bash
git commit -m "test(course-mode): add attended physical pedagogy matrix"
```

### Task 13: Run All-26 Physical Corpus, Fault Matrix, and Soak

**Files:**
- Create: `main/tbot-server/scripts/course_mode_physical_soak.py`
- Create: `main/tbot-server/tests/test_course_mode_physical_soak.py`
- Generate: `task-artifacts/course-mode-production-readiness/<candidate-id>/G7-corpus/*`
- Generate: `task-artifacts/course-mode-production-readiness/<candidate-id>/G7-faults/*`
- Generate: `task-artifacts/course-mode-production-readiness/<candidate-id>/G7-soak/*`

- [ ] **Step 1: Add a dry-run schedule and invariant test**

```python
def test_soak_schedule_has_two_complete_corpora_and_unique_sessions():
    schedule = build_schedule()
    assert len(schedule.lesson_runs) == 52
    assert set(schedule.first_corpus) == set(range(1, 27))
    assert set(schedule.second_corpus) == set(range(1, 27))
    assert len({run.session_id for run in schedule.lesson_runs}) == 52
```

- [ ] **Step 2: Run the first 26-lesson happy-path corpus**

Use a new assignment/session for every lesson. Require correct manifest,
contract, pack, render/audio/motion, completion, progress/insights, stop/rest,
and receipt evidence before moving to the next lesson.

- [ ] **Step 3: Run the attended fault matrix**

Inject Wi-Fi/WS disconnect, ESP/backend restart at activity boundary, asset
timeout/checksum mismatch/SD unavailable/full/corrupt cache, duplicate/missing/
delayed ACK, completion `429`/`5xx`, controlled reset after prepare/mid-activity/
after progress/during closing, stop request, servo failure, and audio runaway.

- [ ] **Step 4: Run the second corpus soak**

Reverse or deterministically shuffle lesson order, include 60-minute idle, 100
WebSocket reconnects, and 10 SD cache cycles. Record heap, tasks, descriptors,
temperature, reset reason, cache bytes, progress totals, and latency percentiles.

- [ ] **Step 5: Apply hard pass criteria**

Require zero unexplained crash/WDT/OOM/reset, checksum/content/identity mismatch,
duplicate/lost progress, stale/wrong visual, unsafe motion/audio/privacy event,
or monotonic resource staircase. Every run ends completed or authored safe exit.

- [ ] **Step 6: Roll back immediately on trigger**

Use Task 11's verified rollback for unsafe behavior, NVS/partition drift,
corrupted content, repeated reset, duplicate completion, unrecoverable session,
resource staircase, or evidence-verifier failure.

### Task 14: Audit Evidence and Issue the Final Local/Staging Verdict

**Files:**
- Create: `docs/qa/ad-hoc/2026-08-29-course-mode-production-readiness-final.md`
- Generate: `task-artifacts/course-mode-production-readiness/<candidate-id>/final-report.json`
- Modify: `LESSON_PRODUCTION_PLAN.md`

- [ ] **Step 1: Run the aggregate evidence auditor**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
python3 scripts/course_mode_evidence_audit.py \
  --candidate "$COURSE_MODE_CANDIDATE" \
  --evidence-root "$COURSE_MODE_EVIDENCE" \
  --output "$COURSE_MODE_EVIDENCE/final-report.json"
```

Expected: all G0-G10 checks are present, candidate-bound, hash-valid, redacted,
non-contradictory, and `PASS`.

- [ ] **Step 2: Run final independent spec and quality/security reviews**

Reviewers inspect all SHAs, diffs, reports, physical captures, resource trends,
rollback evidence, open findings, and required skips. Findings are primary; a
summary is secondary.

- [ ] **Step 3: Classify residual findings**

Any P0/P1, required skip, red physical journey, unproven rollback, identity
drift, or contradictory evidence forces `NO-GO`. Only explicitly accepted P2/P3
may produce `CONDITIONAL-GO`.

- [ ] **Step 4: Issue the bounded verdict**

The report states one of:

```text
GO_FOR_SEPARATELY_AUTHORIZED_PRODUCTION_CANARY
CONDITIONAL_GO_NOT_AUTHORIZED_FOR_PRODUCTION
NO_GO
```

It must state that qualification applies only to the recorded candidate and does
not prove the absence of all future defects.

- [ ] **Step 5: Update production plan and commit redacted evidence summary**

```bash
git add docs/qa/ad-hoc/2026-08-29-course-mode-production-readiness-final.md \
  LESSON_PRODUCTION_PLAN.md
git commit -m "docs(course-mode): record production readiness verdict"
```

## Final Verification Checklist

- [ ] Backend, Admin, ESP, and firmware exact SHAs match the final candidate.
- [ ] Protected/unrelated dirty files remain preserved or are explicitly incorporated.
- [ ] Full software, browser, live-DB, cross-process, host, HIL, and security gates pass.
- [ ] Exactly 26 lessons and the canonical curriculum counts/checksums match.
- [ ] Lifecycle rollback passes post-cutover and post-archive fault injection.
- [ ] Six representative physical journeys pass.
- [ ] First 26-lesson corpus, fault matrix, and second-corpus soak pass.
- [ ] Known-good rollback and candidate app-only flash both have verified readback.
- [ ] NVS, protected partitions, and generated assets remain preserved.
- [ ] Evidence contains no credentials, raw child audio, full transcript, or private key.
- [ ] Zero open P0/P1 and zero required `SKIPPED`/waived gates.
- [ ] Final verdict is bounded to local/staging and the immutable tested candidate.

## Execution Assignment

Use subagent-driven development. Assign Tasks 1-9 serially by repository conflict
boundary, parallelizing only independent read-only review or verification lanes.
Tasks 10-14 are gated execution tasks; physical work remains single-lease and
attended. Every implementation task follows red-green-refactor, commits its own
minimal diff, receives spec review, then quality/security review.
