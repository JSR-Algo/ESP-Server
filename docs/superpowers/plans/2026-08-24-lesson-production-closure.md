# Lesson Production Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close every remaining lesson deployment, terminal-state, hardware, and production-observation gate without a waiver.

**Architecture:** Treat the release as four fail-closed lanes: terminal ownership, canonical backend activation, physical H1, and production verification. Each lane produces immutable evidence and may advance only after its predecessor is green; production mutation is guarded by explicit leases, backups, and tested rollback targets.

**Tech Stack:** NestJS/TypeScript/PostgreSQL/Render, Python 3.11/aiohttp/pytest, ESP32 firmware, Android/ADB, Docker/VPS, physical TeeBot hardware.

---

## File Map

- `tbot-backend/src/lessons/lesson-progress-watchdog.service.ts`: backend owner for stalled active assignments when no terminal event arrives.
- `tbot-backend/src/lessons/lesson-progress-watchdog.service.spec.ts`: focused stale-running detection and terminal transition tests.
- `tbot-backend/src/lessons/lesson-event-ingest.service.ts`: existing durable terminal event ingestion; modify only if watchdog cannot reuse its state-transition contract.
- `tbot-backend/scripts/repair-canonical-demo-v6-assets.mjs`: fail-closed canonical v6 discovery and repair.
- `tbot-backend/tests/canonical-demo-v6-asset-repair.spec.ts`: legacy checksum and production repair regression tests.
- `tbot-backend/docs/qa/ad-hoc/2026-08-24-lesson-production-closure.md`: backend backup, repair, deploy, worker, smoke, and rollback evidence.
- `robot/esp32-server/main/tbot-server/core/lesson/runtime.py`: ESP terminal forwarding/replay ownership; modify only if current-main reproduction identifies an ESP gap.
- `robot/esp32-server/main/tbot-server/tests/test_lesson_runtime.py`: disconnect/runtime-fault terminal regression coverage.
- `robot/esp32-server/tests/lesson-production/`: cross-component terminal and deploy smoke checks.
- `robot/evidence/lesson-production-closure-20260824/`: H1, fault, power-cycle, mobile, and observation artifacts.
- `LESSON_PRODUCTION_PLAN.md`, `lesson-prod/t54-e2e-live.md`, `lesson-prod/t72-deploy-backend.md`, and `lesson-prod/t74-post-deploy-verify.md`: final status surfaces updated only from fresh evidence.

### Task 1: Freeze State And Acquire Release Leases

**Files:**
- Create: `tbot-backend/docs/qa/ad-hoc/2026-08-24-lesson-production-closure.md`
- Create: `robot/evidence/lesson-production-closure-20260824/identity.txt`

- [ ] **Step 1: Record repository and dirty-worktree identity**

Run:

```bash
for repo in tbot-backend tbot-mobile robot/esp32-server robot/TBOT-Firmware; do
  git -C "$repo" status --short
  git -C "$repo" branch --show-current
  git -C "$repo" rev-parse HEAD
done
```

Expected: exact SHAs and all pre-existing changes are recorded; no unrelated file is staged or modified.

- [ ] **Step 2: Prove exclusive hardware ownership**

Run read-only checks for `/dev/cu.usbmodem1101`, ADB device `efc5314f`, serial holders, active capture helpers, active lesson assignments, and current production ESP connection count. Abort this task if any competing holder, deploy, or lesson exists.

- [ ] **Step 3: Freeze deploy and rollback identities**

Record Render web/worker deploy IDs and SHAs, VPS image/container IDs, firmware SHA, approved APK SHA-256, production endpoints, database ID, latest usable PITR/export, and exact application rollback deploy.

- [ ] **Step 4: Commit evidence skeleton only**

Commit only newly created evidence metadata in its owning repository. Do not commit secrets, tokens, database URLs, or the user's pre-existing dirty files.

### Task 2: Reproduce `F-T25-01` On Current Main

**Files:**
- Test: `tbot-backend/src/lessons/lesson-progress-watchdog.service.spec.ts`
- Test: `robot/esp32-server/main/tbot-server/tests/test_lesson_runtime.py`
- Test: `robot/esp32-server/tests/lesson-production/test_terminal_assignment_closure.py`

- [ ] **Step 1: Write a backend failing test for stale RUNNING assignment closure**

Construct an assignment whose latest durable event is `lesson_started`, whose heartbeat/progress age exceeds the configured threshold, and whose state is `RUNNING`. Assert the watchdog performs a compare-and-set transition to `FAILED`, persists a deterministic watchdog error code, and emits the same parent/projection side effects as `lesson_failed` ingest.

- [ ] **Step 2: Write an ESP failing test for post-start disconnect**

Start a runtime, accept `lesson_started`, tear down the connection before a normal terminal event, drain pending replay, and assert exactly one durable `lesson_failed` is retained or forwarded with the original assignment/session identity.

- [ ] **Step 3: Add a cross-component probe**

The probe must feed the emitted ESP terminal batch into backend ingest and assert the assignment leaves `RUNNING`. It must also simulate complete loss of the ESP terminal batch and assert the backend watchdog eventually closes the assignment.

- [ ] **Step 4: Run the RED gate**

Run focused backend Vitest, ESP pytest, and the cross-component probe. Expected: at least one behavioral assertion fails on current main. If all pass, mark `F-T25-01` already fixed only after the probe is independently repeated in a clean main worktree; do not make speculative code changes.

- [ ] **Step 5: Commit only biting tests**

Commit tests after proving they fail for behavior rather than file/function presence.

### Task 3: Close Terminal Ownership Test-First

**Files:**
- Modify: `tbot-backend/src/lessons/lesson-progress-watchdog.service.ts`
- Modify: `tbot-backend/src/lessons/lesson-event-ingest.service.ts` only if a shared terminal transition helper is required
- Modify: `robot/esp32-server/main/tbot-server/core/lesson/runtime.py` only if Task 2 proves an ESP gap
- Test: files from Task 2

- [ ] **Step 1: Implement backend compare-and-set terminalization**

Within one transaction, lock the stale assignment, re-read its latest event/state, and update only if it is still the same active stale generation. Persist a deterministic `lesson_failed`-equivalent event/code so progress, notifications, metrics, and single-active-assignment release follow existing terminal behavior. A concurrent real terminal event must win without duplicate side effects.

- [ ] **Step 2: Implement ESP replay repair only if required**

Route every post-activation runtime teardown through the existing one-terminal forwarder/replay store. Preserve absorbing terminal state and assignment/session identity; never emit two terminal events for reconnect or late ACK paths.

- [ ] **Step 3: Run focused GREEN tests**

Run the exact Task 2 commands. Expected: all selected tests pass and duplicate/concurrent terminal tests report exactly one terminal transition.

- [ ] **Step 4: Verify RED-GREEN mechanically**

Run the regression against the pre-fix base in a throwaway worktree and the fix branch tip. Expected: RED at base, GREEN at tip.

- [ ] **Step 5: Run adjacent suites and commit**

Run lesson event ingest, assignment lifecycle, parent projection/notification, ESP runtime, forwarder, websocket reconnect, and failure-path suites. Commit backend and ESP changes separately so each repository has an independent rollback commit.

### Task 4: Diagnose And Repair Canonical V6 Data

**Files:**
- Modify: `tbot-backend/scripts/repair-canonical-demo-v6-assets.mjs`
- Test: `tbot-backend/tests/canonical-demo-v6-asset-repair.spec.ts`
- Update: backend closure evidence

- [ ] **Step 1: Reproduce the production row read-only**

Run the repair script in discovery/dry-run mode against production and capture the redacted candidate checksums, bundle/lesson identity, asset metadata classification, migration-081 history, and exact mismatch reason. Do not write data.

- [ ] **Step 2: Add a failing fixture for the observed mismatch**

Create a fixture matching production's exact safe legacy shape. Assert the repair either recognizes a known canonical predecessor and produces the already-reviewed v6 checksum, or fails with a more precise non-repairable reason. Never accept arbitrary checksum mismatch.

- [ ] **Step 3: Implement the narrow repair rule**

Permit repair only when frozen v1 provenance, lesson/version/profile, all asset hashes/geometry, steps, visual refs, and migration history match a fully enumerated known state. Keep unknown or partially matching rows fail-closed.

- [ ] **Step 4: Run backend verification**

Run the canonical repair suite, renderer migration suites, generation contract verification, numbered migration tests, build, and `git diff --check`. Expected: all pass with unknown-checksum rejection retained.

- [ ] **Step 5: Commit the repair**

Commit only script/tests/docs. Tag the reviewed commit before any production mutation.

### Task 5: Backup, Repair, And Activate Backend Web

**Files:**
- Update: backend closure evidence
- Update: `lesson-prod/t72-deploy-backend.md` after verification

- [ ] **Step 1: Take and verify a fresh database backup**

Create a PostgreSQL custom-format backup with restrictive permissions, record its SHA-256 and size, verify `pg_restore --list`, and confirm PITR/export availability. Record the restore command before proceeding.

- [ ] **Step 2: Apply canonical repair under supervision**

Run dry-run, compare the discovered row to Task 4's fixture, apply once, then run read-only verification. Expected: one identified canonical row/history transition and no unrelated row changes.

- [ ] **Step 3: Deploy exact reviewed web SHA**

Trigger a manual Render deploy with auto-deploy still off. Watch preflight, numbered migrations, renderer migration, process health, and traffic swap. Abort and retain the old live release if any gate fails.

- [ ] **Step 4: Run authenticated production smoke**

Verify `/v1/health`, catalog, assignment, manifest, asset/preload status, terminal readback, generation contract, schema version, correlation IDs, and zero migration drift against the new live SHA.

- [ ] **Step 5: Verify rollback without executing it**

Confirm the previous deploy remains selectable, the database backup is readable, and the documented application/database rollback commands reference immutable IDs.

### Task 6: Activate Lesson Workers Safely

**Files:**
- Update: backend closure evidence

- [ ] **Step 1: Capture queue baseline**

Record due, leased, retrying, failed, and dead-letter counts for general, lesson-generation, parent-learning, notification, and cinematic queues. Confirm zero active lease collision.

- [ ] **Step 2: Enable one worker lane at a time**

Activate general/background processing first with the exact reviewed SHA and required acknowledgment flags. Observe bounded queue delta and health before enabling cinematic processing.

- [ ] **Step 3: Validate idempotency and side effects**

Confirm no duplicate generation, notification, projection, or cinematic publication; all new leases expire/complete normally; failed historical jobs are not silently retried outside policy.

- [ ] **Step 4: Record rollback posture**

Prove worker flags can return to standby and that disabling workers does not affect the healthy web service.

### Task 7: Run Strict H1 Physical Gate

**Files:**
- Create: `robot/evidence/lesson-production-closure-20260824/h1/`
- Update: `lesson-prod/t54-e2e-live.md`

- [ ] **Step 1: Reacquire exclusive hardware lease**

Verify robot MAC `14:c1:9f:d1:ac:20`, serial `/dev/cu.usbmodem1101`, Android `efc5314f`, approved APK hash, charger, SD card, Wi-Fi, production server identity, and no competing process.

- [ ] **Step 2: Start the full evidence collector**

Capture serial, ESP logs, backend logs, mobile UI state, metrics, assignment/session readback, and video. Wait for `passive_lesson_websocket_opened` after serial-open reboot before speaking.

- [ ] **Step 3: Run the happy path**

Say only `bắt đầu bài học` at the approved cadence. Require assignment/manifest/preload/start, every authored step, all response windows, audio/render markers, mobile/parent progress, durable completion, and safe return to conversation.

- [ ] **Step 4: Freeze and verify H1 evidence**

Run the checkpoint verifier and manually review video/audio/display/arm safety. Expected: the complete checklist passes; no partial score or waived box closes H1.

### Task 8: Physical Fault And Power-Cycle Gates

**Files:**
- Create: `robot/evidence/lesson-production-closure-20260824/fault/`
- Create: `robot/evidence/lesson-production-closure-20260824/power-cycle/`

- [ ] **Step 1: Run an isolated mid-step transport fault**

At the first approved response-window marker, interrupt only the robot's lesson transport for the bounded interval. Require exactly one terminal assignment result, safe display/rest ownership, bounded reconnect, and no stale-session resurrection.

- [ ] **Step 2: Run a controlled power cycle**

Power-cycle after an active lesson marker, verify SD/runtime recovery and terminal disposition, then start a fresh lesson. The subsequent lesson must be usable without manual database cleanup.

- [ ] **Step 3: Reconcile all identities**

Match assignment/session IDs across robot, ESP, backend, mobile, and metrics. Any unexplained mismatch is NO-GO.

### Task 9: Production Observation And Final Verdict

**Files:**
- Create: `robot/evidence/lesson-production-closure-20260824/watch-summary.md`
- Update: `LESSON_PRODUCTION_PLAN.md`
- Update: `lesson-prod/t54-e2e-live.md`
- Update: `lesson-prod/t72-deploy-backend.md`
- Update: `lesson-prod/t74-post-deploy-verify.md`

- [ ] **Step 1: Run bounded observation samples**

Collect repeated read-only samples of lesson error rate, watchdog terminalizations, websocket reconnects, dropped events, queue failures, worker lease health, correlation IDs, and active assignment age.

- [ ] **Step 2: Execute release-wide verification**

Run backend build/tests/contracts, ESP focused/integration tests, firmware lesson tests, mobile contract/build tests, production endpoint smoke, and the lesson-production repro suite against exact main/deployed SHAs.

- [ ] **Step 3: Audit the strict gate line by line**

Require backend web/workers live on reviewed SHA, canonical data verified, `F-T25-01` closed, H1 green without waiver, fault/power-cycle green, clean observation window, and executable rollback.

- [ ] **Step 4: Publish GO or NO-GO**

Update status surfaces with evidence links and immutable IDs. Use GO only if every criterion passes; otherwise record the exact blocker and retain rollback/standby posture.

- [ ] **Step 5: Commit documentation separately**

Commit only final evidence/status updates in each owning repository. Do not clean worktrees or delete branches until the user explicitly selects the integration/cleanup option.
