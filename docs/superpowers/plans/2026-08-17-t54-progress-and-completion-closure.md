# T5.4 Progress and Completion Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make renderer-v5 publish live `step_started` progress and make authoritative duplicate completion replay release a stranded assignment, then prove both fixes on the physical robot and close T5.4.

**Architecture:** The ESP runtime extends its existing accepted-ACK telemetry boundary so a step-scoped renderer-v5 `lesson_start` produces the same parent-safe `step_started` event as legacy rendering. The backend keeps progress insertion idempotent but treats a session-bound `lesson_completed` replay as a request to re-evaluate the existing `started_at` completion guard; side effects remain tied to the successful assignment flip.

**Tech Stack:** Python 3.14, pytest, NestJS/TypeScript, PostgreSQL, Vitest, Docker/VPS deployment scripts, Android/ADB, physical TBOT firmware.

---

### Task 1: Renderer-v5 `step_started` telemetry

**Files:**
- Modify: `main/tbot-server/tests/test_lesson_runtime.py`
- Modify: `main/tbot-server/core/lesson/runtime.py:3130`
- Create: `docs/qa/ad-hoc/2026-08-17-t54-step-started-closure.md`

- [ ] **Step 1: Write the failing renderer-v5 ACK tests**

Add tests beside `test_lesson_step_ack_forwards_bounded_operations_telemetry` that construct an accepted, step-scoped renderer-v5 `lesson_start` outstanding frame:

```python
async def test_renderer_v5_lesson_start_ack_forwards_step_started(self):
    from unittest.mock import AsyncMock

    forwarder = _FakeForwarder()
    rt = self._runtime(forwarder=forwarder)
    rt.negotiated_version = "renderer-v5"
    rt._step = {"id": "s4", "type": "listen"}
    rt._step_id = "s4"
    rt._outstanding[9] = {
        "type": "lesson_start",
        "stepId": "s4",
        "body": {"cinematicPhase": {"command": "start", "phaseId": "s4-main"}},
        "retryCount": 2,
    }
    rt._on_frame_acked = AsyncMock()

    await rt.on_lesson_ack(
        _ack(
            9,
            1,
            step_id="s4",
            extra={
                "acks": 9,
                "degraded": False,
                "telemetry": {
                    "internalMinimumFreeBytes": 24_576,
                    "psramFreeBytes": 1_500_000,
                },
            },
        )
    )

    self.assertEqual(forwarder.batches[0]["events"], [{
        "type": "step_started",
        "sequence": 1,
        "stepId": "s4",
        "stepType": "listen",
        "sramFreeBytes": 24_576,
        "psramFreeBytes": 1_500_000,
        "retryCount": 2,
    }])
```

Add negative coverage proving a global `lesson_start` without a step, `lesson_prepare`, a rejected/mismatched ACK, and a duplicate ACK do not emit additional `step_started` events.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
python3 -m pytest main/tbot-server/tests/test_lesson_runtime.py -q -k 'renderer_v5_lesson_start_ack'
```

Expected: the positive test fails because `_forward_lesson_step_ack_telemetry()` returns for every frame whose type is not `lesson_step`; negative cases remain green.

- [ ] **Step 3: Extend the accepted-ACK telemetry boundary**

Change `_forward_lesson_step_ack_telemetry()` to accept either legacy `lesson_step` or a renderer-v5 step-scoped cinematic start:

```python
frame_type = frame.get("type")
frame_body = frame.get("body")
frame_body = frame_body if isinstance(frame_body, dict) else {}
cinematic_phase = frame_body.get("cinematicPhase")
renderer_v5_start = (
    frame_type == "lesson_start"
    and self._renderer_v5_enabled()
    and isinstance(frame.get("stepId"), str)
    and bool(frame.get("stepId"))
    and isinstance(cinematic_phase, dict)
    and cinematic_phase.get("command") == "start"
)
if frame_type != "lesson_step" and not renderer_v5_start:
    return
```

Add the current safe step type when available:

```python
step_type = (self._step or {}).get("type")
if isinstance(step_type, str) and step_type:
    event["stepType"] = step_type
```

Do not move the call earlier than `_cinematic_ack_matches()` / `_accept_inbound()`: those existing gates guarantee that rejected, stale, mismatched, and duplicate ACKs cannot publish telemetry.

- [ ] **Step 4: Run focused and file suites GREEN**

Run:

```bash
python3 -m pytest main/tbot-server/tests/test_lesson_runtime.py -q -k 'lesson_step_ack or renderer_v5_lesson_start_ack'
python3 -m pytest main/tbot-server/tests/test_lesson_runtime.py -q
```

Expected: focused tests pass; full file reports at least the baseline 274 tests plus the new tests, with zero failures.

- [ ] **Step 5: Write ESP evidence and commit**

Record root cause, exact RED output, diff summary, and GREEN output in `docs/qa/ad-hoc/2026-08-17-t54-step-started-closure.md`, then run:

```bash
git add main/tbot-server/core/lesson/runtime.py \
  main/tbot-server/tests/test_lesson_runtime.py \
  docs/qa/ad-hoc/2026-08-17-t54-step-started-closure.md
git commit -m "fix(lesson): publish renderer-v5 step progress"
```

### Task 2: Authoritative duplicate completion reconciliation

**Files:**
- Modify: `src/lessons/lesson-event-ingest.stuck-slot.spec.ts`
- Modify: `src/lessons/lesson-event-ingest.service.ts:305`
- Create: `docs/qa/ad-hoc/2026-08-17-t54-completion-replay-closure.md`

- [ ] **Step 1: Make the stateful test double model duplicate lifecycle rows**

Extend the state in `makeStatefulPool()` with a set of inserted lifecycle event keys. For `INSERT INTO progress_events`, return `rowCount: 0` when the same session/type is inserted again, otherwise record it and return `rowCount: 1`. Use the query parameter holding `event_type` (`params[5]`) rather than parsing SQL.

```typescript
const state = {
  sessionStartedAt: false,
  completedFlipMatchedRows: 0,
  insertedLifecycleEvents: new Set<string>(),
};
```

The query fake accepts `(sql: string, params: unknown[] = [])`; for lifecycle types use `${SESSION_ID}:${String(params[5])}` as the modeled dedup key.

- [ ] **Step 2: Write the failing replay test**

Replace the existing recovery contrast with the physical power-cycle sequence:

```typescript
it('reconciles a duplicate completion after the recovered session becomes authoritative', async () => {
  const { pool, state } = makeStatefulPool();
  const courseEnrollment = makeCourseEnrollment();
  const service = new LessonEventIngestService(pool, courseEnrollment);

  const stranded = await service.ingest(DEVICE_ID, {
    assignmentId: ASSIGNMENT_ID,
    sessionId: SESSION_ID,
    events: [{ type: 'lesson_completed', completedAt: 1_700_000_010_000 }],
  });
  expect(stranded).toMatchObject({ accepted: 1, duplicates: 0 });
  expect(state.completedFlipMatchedRows).toBe(0);

  await service.ingest(DEVICE_ID, {
    assignmentId: ASSIGNMENT_ID,
    sessionId: SESSION_ID,
    events: [{ type: 'lesson_started', startedAt: 1_700_000_100_000 }],
  });

  const replay = await service.ingest(DEVICE_ID, {
    assignmentId: ASSIGNMENT_ID,
    sessionId: SESSION_ID,
    events: [{ type: 'lesson_completed', completedAt: 1_700_000_010_000 }],
  });

  expect(replay).toMatchObject({ accepted: 0, duplicates: 1 });
  expect(state.sessionStartedAt).toBe(true);
  expect(state.completedFlipMatchedRows).toBe(1);
  expect(courseEnrollment.advanceOnCompletion).toHaveBeenCalledTimes(1);
  expect(courseEnrollment.advanceOnCompletion).toHaveBeenCalledWith(DEVICE_ID, ASSIGNMENT_ID);
});
```

Add a companion test replaying the duplicate completion twice and assert the second replay performs no second course advance. Model assignment state in the fake so the completion UPDATE returns a row only on the first successful flip.

- [ ] **Step 3: Run the focused test and verify RED**

Run:

```bash
RUNTIME_NODE_BIN=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin
PATH="$RUNTIME_NODE_BIN:$PATH" ./node_modules/.bin/vitest run \
  src/lessons/lesson-event-ingest.stuck-slot.spec.ts
```

Expected: the duplicate replay test fails with `completedFlipMatchedRows` remaining zero because `completionRequested` is currently set only when the event insert is lifecycle-eligible.

- [ ] **Step 4: Re-evaluate completion authority on an idempotent replay**

Change only the `LESSON_COMPLETED` lifecycle branch:

```typescript
if (row.eventType === LESSON_COMPLETED && sessionId) {
  completionRequested = true;
  pendingCompletionAt = row.occurredAt ?? pendingCompletionAt;
}
```

Keep event insertion, accepted/duplicate counters, all ownership checks, and the SQL `ls.started_at IS NOT NULL` guard unchanged. Keep notifications, rewards, metrics, and course advancement conditional on `flippedCount > 0` / `completedThisBatch`.

- [ ] **Step 5: Run focused and lesson ingest suites GREEN**

Run:

```bash
RUNTIME_NODE_BIN=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin
PATH="$RUNTIME_NODE_BIN:$PATH" ./node_modules/.bin/vitest run \
  src/lessons/lesson-event-ingest.stuck-slot.spec.ts \
  src/lessons/lesson-event-ingest.service.spec.ts \
  src/lessons/lesson-event-ingest.session-ownership.spec.ts \
  src/lessons/lesson-event-ingest.advisory-identity.spec.ts
```

Expected: all tests pass; no-session and missing-`started_at` guards remain green; exactly-once side effects remain green.

- [ ] **Step 6: Run backend typecheck/build and commit evidence**

Run:

```bash
RUNTIME_NODE_BIN=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin
PATH="$RUNTIME_NODE_BIN:$PATH" ./node_modules/.bin/tsc --noEmit
PATH="$RUNTIME_NODE_BIN:$PATH" ./node_modules/.bin/nest build
```

Record RED/GREEN evidence in `docs/qa/ad-hoc/2026-08-17-t54-completion-replay-closure.md`, then:

```bash
git add src/lessons/lesson-event-ingest.service.ts \
  src/lessons/lesson-event-ingest.stuck-slot.spec.ts \
  docs/qa/ad-hoc/2026-08-17-t54-completion-replay-closure.md
git commit -m "fix(lessons): reconcile completion replay"
```

### Task 3: Cross-repo verification and review

**Files:**
- Modify: `docs/qa/ad-hoc/2026-08-17-t54-step-started-closure.md`
- Modify: backend `docs/qa/ad-hoc/2026-08-17-t54-completion-replay-closure.md`

- [ ] **Step 1: Rebase both branches onto current `main`**

Run `git fetch origin`, `git rebase main`, and `git status --short --branch` in each worktree. Stop if either worktree contains unrelated changes.

- [ ] **Step 2: Run branch-tip verification**

ESP:

```bash
python3 -m pytest main/tbot-server/tests/test_lesson_runtime.py -q
python3 -m pytest main/tbot-server/tests/test_lesson_forwarder.py -q
```

Backend:

```bash
RUNTIME_NODE_BIN=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin
PATH="$RUNTIME_NODE_BIN:$PATH" ./node_modules/.bin/vitest run src/lessons/lesson-event-ingest*.spec.ts
PATH="$RUNTIME_NODE_BIN:$PATH" ./node_modules/.bin/tsc --noEmit
PATH="$RUNTIME_NODE_BIN:$PATH" ./node_modules/.bin/nest build
```

- [ ] **Step 3: Request code review and resolve all Critical/Important findings**

Review each branch against its base SHA and this plan. Re-run the focused tests after every correction and commit review fixes separately.

### Task 4: Merge, deploy, and production smoke

**Files:**
- Modify: `lesson-prod/GATE_LOG.md` through the repository gate scripts
- Modify: both per-repo evidence files

- [ ] **Step 1: Merge through the project gate**

Use the T0.4 `gate.sh` / `merge-task.sh` protocol when available. Otherwise rebase on `main`, repeat Task 3 verification, merge with a merge commit, and push `main` for each repository.

- [ ] **Step 2: Deploy backend**

Watch the Render deployment triggered by backend `main`, then require production `/v1/health` to return HTTP 200 and record the deployed commit SHA.

- [ ] **Step 3: Deploy ESP server**

From ESP `main`, run the documented sequence:

```bash
bash deploy/backup-db.sh
bash deploy/deploy-vps.sh
bash deploy/smoke-vps.sh
```

Require the production container `current-tbot-esp32-server-1` healthy and re-check MCP pin/config evidence without printing secrets.

- [ ] **Step 4: Re-test code on main**

Use the root helper, never the shared checkout:

```bash
bash /Users/manhhodinh/Documents/TBOT/lesson-prod/scripts/verify-on-main.sh \
  /Users/manhhodinh/Documents/TBOT/robot/esp32-server -- \
  python3 -m pytest main/tbot-server/tests/test_lesson_runtime.py -q

bash /Users/manhhodinh/Documents/TBOT/lesson-prod/scripts/verify-on-main.sh \
  /Users/manhhodinh/Documents/TBOT/tbot-backend -- \
  env PATH="/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH" \
  ./node_modules/.bin/vitest run src/lessons/lesson-event-ingest.stuck-slot.spec.ts
```

### Task 5: Physical T5.4 closure and cleanup

**Files:**
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/docs/evidence/t54-live-20260817-final-closeout/README.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/docs/qa/ad-hoc/2026-08-16-t54-e2e-live.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/lesson-prod/t54-e2e-live.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`

- [ ] **Step 1: Run a fresh physical lesson**

Create a fresh no-PIN assignment for the connected Android/robot, open Parent Today, trigger “bắt đầu bài học”, and capture the robot serial, production ESP log, Android screenshots/API responses after every step, and terminal read-back. Do not expose `TBOT_DEVICE_MINT_SECRET`.

- [ ] **Step 2: Require the full T5.4 proof set**

Verify all of the following from fresh evidence:

```text
step_started count >= 9 with ordered s1-s9 visibility
Parent currentStep/positionPercent advances within checklist SLA after each step
lesson_completed persisted=true
assignment/current read-back state=COMPLETED (or no active assignment plus terminal read-back)
definitive verifier ok=true and 101/101
audible audio, three visible layers, applied MCP motion, conversation face restored
```

Run the task commands:

```bash
python scripts/lesson_e2e_live_capture.py
bash scripts/tbot_live_e2e_probe.sh
```

- [ ] **Step 3: Archive and hash evidence**

Store the fresh logs, JSON, screenshots/video, verifier report, probe output, commit SHAs, deploy results, and SHA-256 hashes under `robot/docs/evidence/`. Update the QA report with repro, fix summaries, physical GREEN evidence, and main/deploy verification.

- [ ] **Step 4: Remove only the two task worktrees and branches**

For each repository, require a clean worktree and:

```bash
git merge-base --is-ancestor lesson-prod/t54-step-started-closure main
git merge-base --is-ancestor lesson-prod/t54-completion-replay-closure main
```

Remove the corresponding worktree, delete the merged local and remote branch, and leave all unrelated pre-existing worktrees untouched.

- [ ] **Step 5: Mark T5.4 DONE**

Only after every preceding proof is present, check the final Parent Progress box, set the task status to `DONE` in both `lesson-prod/t54-e2e-live.md` and `LESSON_PRODUCTION_PLAN.md` section 2, resolve F-T54-57/F-T54-58 in section 5, and link the final evidence directory.
