# Lesson Studio Software Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining software-only rollout, task-lifecycle, event-loop, and local-runtime gaps without changing hardware readiness claims.

**Architecture:** Backend rollout configuration remains authoritative and exposes current-admin capabilities to a fail-closed manager client. ESP runtime fixes preserve explicit task ownership and create asyncio primitives only in their consuming loop. Python 3.10 becomes an executable local-development requirement.

**Tech Stack:** NestJS/TypeScript/Jest, Vue 2/Vue Router/Vitest/Chromium, Python 3.10/asyncio/Pytest.

---

### Task 1: Expose Backend Rollout Capabilities

**Files:**
- Create: `tbot-backend/src/lessons/lesson-rollout.controller.ts`
- Create: `tbot-backend/src/lessons/lesson-rollout.capabilities.spec.ts`
- Modify: `tbot-backend/src/lessons/lesson-rollout.config.ts`
- Modify: `tbot-backend/src/lessons/lessons.module.ts`

- [ ] Write failing tests proving an authenticated allowlisted admin receives independent booleans, while disabled flags, non-allowlisted users, and `auth-disabled` sessions receive false capabilities or authentication rejection.
- [ ] Run `npm test -- lesson-rollout.capabilities.spec.ts`; expect the missing controller/contract failure.
- [ ] Add `capabilitiesForAdmin(adminUserId)` to `LessonRolloutConfig` and an `admin/lesson-rollout-capabilities` controller protected by `AdminSessionGuard`, `@AdminRoles('super_admin')`, and `@Public()` following existing admin controllers.
- [ ] Return exactly `{ data: { sharedVisualAuthoring: boolean, exactEspTftPreview: boolean } }`; never expose allowlists or environment values.
- [ ] Register the controller and rerun rollout guard/config/controller tests, lint, typecheck, and build.
- [ ] Commit in backend: `feat(lessons): expose admin rollout capabilities`.

### Task 2: Gate Manager Lesson Studio Surfaces

**Files:**
- Create: `main/manager-web/src/utils/lessonRolloutCapabilities.js`
- Create: `main/manager-web/src/tests/lesson-rollout-capabilities.test.mjs`
- Modify: `main/manager-web/src/apis/module/lesson.js`
- Modify: `main/manager-web/src/router/index.js`
- Modify: `main/manager-web/src/components/HeaderBar.vue`
- Modify: `main/manager-web/src/views/LessonEditor.vue`

- [ ] Write failing unit and mounted tests for default-false/error behavior, direct-route denial, hidden visual navigation/picker, independent preview hiding, and enabled behavior.
- [ ] Run the new focused tests and confirm they fail because no capability client or route metadata exists.
- [ ] Add a cached capability loader that accepts only literal booleans, deduplicates in-flight requests, and resets to false on logout/error.
- [ ] Add route metadata and an async guard for `sharedVisualAuthoring`; preserve auth and super-admin checks.
- [ ] Render HeaderBar visual navigation, `SharedAssetPicker`, preview button/canvas, and preview requests only when their corresponding capability is true.
- [ ] Run manager Lesson Studio gates and production build.
- [ ] Commit in ESP repo: `feat(admin): gate lesson studio rollout surfaces`.

### Task 3: Preserve Prewarm Task Ownership During Budget Degrade

**Files:**
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`
- Modify: `main/tbot-server/tests/test_session_orchestrator.py`
- Modify: `main/tbot-server/tests/test_google_live_provider_edges.py`

- [ ] Keep the existing over-budget regression red and add tests for completed fallback, non-cancelled owning prewarm, external close cancellation, foreground degrade cancellation, timeout cleanup, and a later replacement prewarm.
- [ ] Run the focused tests; confirm the current parent/child cancellation cycle raises `CancelledError`.
- [ ] Propagate keyword-only `preserve_live_prewarm=False` through `_ensure_live_open_for_audio`, `_open_live_for_audio`, `_activate_budget_degrade`, and `_close_live_resources`.
- [ ] Pass true only from `_schedule_live_prewarm._run`; when true, preserve only `_live_prewarm_task` while closing all other resources.
- [ ] Run orchestrator, provider edge/fallback/reconnect tests and confirm no orphan task or cancellation warning.
- [ ] Commit: `fix(voice): preserve owned live prewarm cleanup`.

### Task 4: Make Audio Rate Control Loop-Safe

**Files:**
- Modify: `main/tbot-server/core/utils/audioRateController.py`
- Modify: `main/tbot-server/core/handle/sendAudioHandle.py`
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`
- Modify: `main/tbot-server/tests/test_audio_rate_controller_edges.py`
- Modify: `main/tbot-server/tests/test_audio_rate_controller_cleanup.py`
- Modify: `main/tbot-server/tests/test_send_audio_tts_stop.py`

- [ ] Add failing tests for synchronous construction, loop-A use, loop-B rebind after loop A closes, active foreign-loop rejection, pre/post-init reset, awaited cancellation, and empty-state preservation.
- [ ] Run focused tests and confirm constructor/cross-loop failures.
- [ ] Store loop-neutral queue state in `__init__`; lazily create events in `_ensure_loop_primitives()` under `get_running_loop()`.
- [ ] Add `wait_until_empty()` and migrate production wait sites; mirror synchronous queue mutations into initialized events without creating events.
- [ ] Reject active foreign-loop ownership with a precise runtime error and permit rebind only after the prior sender is done.
- [ ] Run all audio-rate, send-audio, provider, and connection tests.
- [ ] Commit: `fix(audio): bind rate control to active event loop`.

### Task 5: Enforce the Supported Python Runtime

**Files:**
- Modify: `main/tbot-server/requirements.txt`
- Modify: `main/tbot-server/requirements-google-live.txt`
- Create: `main/tbot-server/scripts/check_python_version.py`
- Create: `main/tbot-server/tests/test_python_runtime_contract.py`
- Modify: `.github/workflows/ci.yml`

- [ ] Write failing tests proving Python 3.9 is rejected with a clear message and Python 3.10+ succeeds.
- [ ] Change requirements comments from recommended to required and add a dependency-free preflight script using `sys.version_info`.
- [ ] Invoke the script before dependency installation/tests in CI so an unsupported interpreter fails before misleading imports.
- [ ] Run the runtime-contract and CI/Docker-order tests.
- [ ] Commit: `build(server): require python 3.10 runtime`.

### Task 6: Final Cross-Repo Verification

- [ ] Run backend rollout/foundation focused tests, lint, typecheck, and build.
- [ ] Run manager Lesson Studio mounted/browser suites and production build.
- [ ] Run ESP focused runtime suites and the bounded broad suite under Python 3.10 with declared dependencies.
- [ ] Run firmware host coverage, Pytest suite, and `build-lcdwiki.sh --no-flash` only if shared contracts changed.
- [ ] Confirm all three owning worktrees are clean, rollout defaults remain false, and Task 14 live matrices remain `NOT PASS`.
- [ ] Commit only intentional evidence/docs changes; do not deploy, flash, or mark hardware proof complete.
