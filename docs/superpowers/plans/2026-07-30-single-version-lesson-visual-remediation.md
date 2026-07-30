# Single-Version Lesson Visual Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the concurrency, historical-version, retry-pipeline, and compatibility-path gaps so the admin remains a simple one-version workflow while lesson visuals and SD rollout stay correct under races and retries.

**Architecture:** The backend is the source of truth for the lesson-wide pair. All lesson mutations share the lesson/step lock protocol, generation commits are fenced against a fresh canonical source identity, and normal admin listing exposes only the authoritative row per lesson key. Retry becomes an aggregate command: request a CMS rebuild and force the ESP global-generation poller to retry immediately; the admin no longer invokes the optional legacy per-lesson worker.

**Tech Stack:** NestJS/TypeScript/PostgreSQL/Vitest, Vue 2/Element UI/Node contract scripts, aiohttp/Python/pytest for the ESP manager server.

---

### Task 1: Enforce the lesson-wide visual invariant at backend compatibility boundaries

**Files:**
- Create: `tbot-backend/src/lessons/lesson-visual-slots.ts`
- Modify: `tbot-backend/src/errors/error-code.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.service.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.service.coverage.spec.ts`
- Modify: `tbot-backend/src/lessons/visual-assets/shared-visual-asset.service.ts`
- Modify: `tbot-backend/src/lessons/visual-assets/shared-visual-asset.service.spec.ts`

- [ ] **Step 1: Write failing update-step tests**

Add coverage that sends `visualRefs` containing `backgroundScene` or `teachingObject` to `updateStep`. Assert a 409/422 domain error with a stable code, no `lesson_visual_refs` insert/delete, and rollback. Keep a passing case for a genuinely per-step slot such as `robotOverlay`.

```ts
await expect(service.updateStep(LESSON_ID, 's1', {
  visualRefs: [{ slot: 'backgroundScene', assetVersionId: SCENE_VERSION_ID }],
}, ADMIN, IP)).rejects.toMatchObject({
  code: ErrorCode.LESSON_VISUALS_ARE_LESSON_WIDE,
  status: 409,
});
expect(statements.some(({ sql }) => /INSERT INTO lesson_visual_refs/.test(sql))).toBe(false);
```

- [ ] **Step 2: Write failing per-step visual endpoint tests**

Cover both set and clear operations for `backgroundScene` and `teachingObject`. Assert no write. Preserve the existing allowed behavior for other slots.

- [ ] **Step 3: Run RED tests**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend/.worktrees/single-version-lesson-visuals
npx vitest run \
  src/lessons/authoring/lesson-authoring.service.coverage.spec.ts \
  src/lessons/visual-assets/shared-visual-asset.service.spec.ts \
  -t "lesson-wide visual"
```

Expected: the two compatibility APIs currently accept the forbidden slots.

- [ ] **Step 4: Add one shared guard and apply it before writes**

Add `LESSON_VISUALS_ARE_LESSON_WIDE` to `ErrorCode`, then create one shared assertion for the two reserved slots:

```ts
import { ErrorCode } from '../errors/error-code';
import { AppError } from '../errors/app-error';

export const LESSON_WIDE_VISUAL_SLOTS = new Set(['backgroundScene', 'teachingObject']);

export function assertPerStepVisualSlot(slot: string): void {
  if (LESSON_WIDE_VISUAL_SLOTS.has(slot)) {
    throw new AppError(
      ErrorCode.LESSON_VISUALS_ARE_LESSON_WIDE,
      'Background and teaching object must be updated through the lesson visual command',
      409,
      { slot },
      false,
    );
  }
}
```

Call it before any asset lookup or mutation in both paths. Do not remove the compatibility endpoints and do not reject unrelated per-step slots.

- [ ] **Step 5: Run GREEN and regressions**

```bash
npx vitest run \
  src/lessons/authoring/lesson-authoring.service.coverage.spec.ts \
  src/lessons/visual-assets/shared-visual-asset.service.spec.ts \
  src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts
npm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add src/lessons/authoring/lesson-authoring.service.ts \
  src/lessons/authoring/lesson-authoring.service.coverage.spec.ts \
  src/lessons/visual-assets/shared-visual-asset.service.ts \
  src/lessons/visual-assets/shared-visual-asset.service.spec.ts \
  src/lessons/lesson-visual-slots.ts src/errors/error-code.ts
git commit -m "fix(lessons): enforce lesson-wide visual slots"
```

### Task 2: Serialize publish source reads and checksum writes

**Files:**
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.service.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.publish-happy-path.spec.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.publish-generation.spec.ts`
- Create or modify: `tbot-backend/src/lessons/authoring/lesson-authoring.publish-concurrency.spec.ts`

- [ ] **Step 1: Add a deterministic stale-checksum race test**

Pause publish after it has acquired its transaction/lesson lock, start `applyLessonVisuals`, and assert the visual update waits. Resume publish, then assert the final refs and persisted checksums describe the same pair. Add the inverse ordering and a draft-step mutation case.

```ts
expect(order).toEqual([
  'publish:BEGIN',
  'publish:lesson-lock',
  'visual:waiting',
  'publish:checksum-write',
  'publish:COMMIT',
  'visual:lesson-lock',
]);
```

- [ ] **Step 2: Run the race test RED**

Expected: publish performs source reads before its transaction and can write checksum A after refs B commit.

- [ ] **Step 3: Move the entire publish snapshot into one transaction**

Inside one held client:

1. `BEGIN`.
2. Lock the lesson row `FOR UPDATE`.
3. Lock ordered lesson steps `FOR UPDATE` using the same lesson -> steps ordering as add/reorder/delete/visual update.
4. Load profiles, assets, and visual refs through the transaction client.
5. Validate and compute profile/manifest checksums from that locked snapshot.
6. Write bundle/checksum/status, request generation, enqueue optional legacy work, and audit.
7. `COMMIT`.

Remove pre-transaction reads used to compute the published identity. Keep network-free work inside the transaction; all operations are database reads, hashing, validation, and inserts.

- [ ] **Step 4: Run publish and mutation regressions**

```bash
npx vitest run \
  src/lessons/authoring/lesson-authoring.publish-concurrency.spec.ts \
  src/lessons/authoring/lesson-authoring.publish-happy-path.spec.ts \
  src/lessons/authoring/lesson-authoring.publish-generation.spec.ts \
  src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts \
  src/lessons/authoring/lesson-authoring.service.coverage.spec.ts
npm run typecheck
npm run lint -- --quiet
```

- [ ] **Step 5: Commit**

```bash
git add src/lessons/authoring/lesson-authoring.service.ts \
  src/lessons/authoring/lesson-authoring.publish-concurrency.spec.ts \
  src/lessons/authoring/lesson-authoring.publish-happy-path.spec.ts \
  src/lessons/authoring/lesson-authoring.publish-generation.spec.ts
git commit -m "fix(lessons): serialize publish checksums"
```

### Task 3: Reject stale generation commits

**Files:**
- Modify: `tbot-backend/src/lessons/lesson-asset-generation.repository.ts`
- Modify: `tbot-backend/src/lessons/lesson-asset-generation-build.ts`
- Modify: `tbot-backend/src/lessons/lesson-asset-generation-build.spec.ts`
- Modify: `tbot-backend/src/lessons/lesson-asset-generation.repository.spec.ts`
- Modify: `tbot-backend/src/lessons/lesson-asset-generation-worker.service.spec.ts`

- [ ] **Step 1: Add a stale-build test around the existing validation hook**

Build index B, pause after `validateBuilt`, mutate the authoritative lesson to C, then resume. Assert no generation row for B is inserted and the rebuild returns a retryable stale-source error.

```ts
await expect(buildPromise).rejects.toMatchObject({ code: 'generation_source_changed' });
expect(repo.insertGeneration).not.toHaveBeenCalled();
```

- [ ] **Step 2: Run RED**

Expected: `commitGeneration` validates only the rebuild lease and inserts B.

- [ ] **Step 3: Rebuild the canonical source identity immediately before insert**

Within the generation transaction and advisory lock, load the current published packs again and build their canonical index. Compare `indexChecksum`, `packCount`, and `curriculumLessonCount` to the worker-built index. On mismatch:

```ts
throw new AppError(
  'generation_source_changed',
  'lesson asset generation source changed during build',
  409,
  undefined,
  true,
);
```

Do not insert or mark the rebuild complete. Let the existing worker retry path requeue it.

- [ ] **Step 4: Run generation regressions**

```bash
npx vitest run \
  src/lessons/lesson-asset-generation-build.spec.ts \
  src/lessons/lesson-asset-generation.repository.spec.ts \
  src/lessons/lesson-asset-generation-worker.service.spec.ts \
  src/lessons/lesson-asset-generation-rebuild-main.spec.ts
npm run typecheck
```

- [ ] **Step 5: Commit**

```bash
git add src/lessons/lesson-asset-generation.repository.ts \
  src/lessons/lesson-asset-generation-build.ts \
  src/lessons/lesson-asset-generation-build.spec.ts \
  src/lessons/lesson-asset-generation.repository.spec.ts \
  src/lessons/lesson-asset-generation-worker.service.spec.ts
git commit -m "fix(lessons): fence stale asset generations"
```

### Task 4: Expose one authoritative lesson row and protect published visual updates

**Files:**
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.service.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts`
- Modify: `robot/esp32-server/main/manager-web/src/views/CourseLessons.vue`
- Modify: `robot/esp32-server/main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`

- [ ] **Step 1: Add authoritative-listing and inactive-row RED tests**

Backend list fixture:

- `key-a`: published v1, published v2 -> return v2 only.
- `key-b`: published v1, draft v2 -> return published v1 for the normal single-version workflow.
- `key-c`: draft v1 only -> return draft v1.

Add a visual command test that rejects applying visuals to published v1 when published v2 exists for the same key.

- [ ] **Step 2: Add a normal-workflow query without deleting compatibility data**

Change the existing course lesson list used by `CourseLessons` to select one row per `lesson_key` with this precedence:

1. Highest published version when any published row exists.
2. Otherwise highest draft version.

Use `DISTINCT ON (lesson_key)` or a ranked CTE with explicit status precedence. Historical rows and the new-version endpoint remain in storage/API compatibility code; the normal list does not expose them.

- [ ] **Step 3: Gate changed published visual updates to the authoritative published row**

After the idempotent same-pair return and before mutation, query for a higher published version with the same `course_id` and `lesson_key`. If found, reject with a stable conflict code and include the authoritative lesson id/version. Draft visual editing remains available only when no published row exists in the normal workflow.

- [ ] **Step 4: Add the manager contract**

Require the UI to render only the backend-provided authoritative list and keep version column/action absent. Do not recreate client-side version heuristics.

- [ ] **Step 5: Verify and commit backend, then admin**

```bash
# backend
npx vitest run \
  src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts \
  src/lessons/authoring/lesson-authoring.lesson-visuals.spec.ts
git commit -am "fix(lessons): expose authoritative lesson rows"

# admin
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/single-version-lesson-visuals/main/manager-web
npm run test:lesson-editor-ui
git add src/views/CourseLessons.vue scripts/check-lesson-editor-ui-contracts.mjs
git commit -m "fix(admin): show authoritative lessons only"
```

The editor contract may still stop only at the accepted `RobotLessonPreview.vue` width baseline.

### Task 5: Make lesson visual reads latest-only and route/session safe

**Files:**
- Modify: `robot/esp32-server/main/manager-web/src/views/LessonEditor.vue`
- Modify: `robot/esp32-server/main/manager-web/scripts/check-lesson-visual-selection.cjs`

- [ ] **Step 1: Add RED behavior harnesses**

Background versions: draft v5, published v4, published v2 -> selector must submit v4.

Step reads: start request A, save visuals, start authoritative request B, resolve B with pair B, then resolve A with pair A. Assert pair B remains displayed and reconciliation is not cleared by A. Repeat A -> B -> A route navigation with the same lesson id reopened but a different `lessonLoadRequestId`.

- [ ] **Step 2: Preserve first published background version**

Mirror the object mapping:

```js
if (row && row.asset_key && row.publication_state === 'published' && !versions[row.asset_key]) {
  versions[row.asset_key] = row.version_id;
}
```

- [ ] **Step 3: Add a monotonic step-read request id**

Add `lessonStepsRequestId` to editor state. Every `fetchSteps()` increments it and captures both that id and `lessonLoadRequestId`. Success/error callbacks return before any side effect unless all identities still match. Reset/invalidate it on route change and destroy.

- [ ] **Step 4: Gate visual callbacks with the navigation epoch**

Capture `lessonLoadRequestId` before `applyLessonVisuals`. Both PUT callbacks and the nested authoritative fetch callbacks must check it before changing saving/pending/reconciliation/message/preview/status state.

- [ ] **Step 5: Verify and commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/single-version-lesson-visuals/main/manager-web
npm run test:lesson-visual-selection
npm run test:lesson-sd-sync-ui
npm run build
git add src/views/LessonEditor.vue scripts/check-lesson-visual-selection.cjs
git commit -m "fix(admin): order lesson visual refreshes"
```

### Task 6: Replace legacy retry with aggregate generation rollout retry

**Files:**
- Modify: `tbot-backend/src/lessons/lesson-asset-generation.controller.ts`
- Modify: `tbot-backend/src/lessons/lesson-asset-generation.controller.spec.ts`
- Modify: `tbot-backend/src/lessons/lesson-asset-generation.repository.ts`
- Modify: `tbot-backend/src/robot/robot-esp.client.ts`
- Modify: `tbot-backend/src/robot/robot-esp.types.ts`
- Modify: `robot/esp32-server/main/tbot-server/core/http_server.py`
- Modify: `robot/esp32-server/main/tbot-server/core/lesson/global_generation_poller.py`
- Modify: `robot/esp32-server/main/tbot-server/tests/test_http_server.py`
- Modify: `robot/esp32-server/main/tbot-server/tests/test_global_generation_poller.py`
- Modify: `robot/esp32-server/main/manager-web/src/apis/module/lesson.js`
- Modify: `robot/esp32-server/main/manager-web/src/views/LessonEditor.vue`
- Modify: `robot/esp32-server/main/manager-web/scripts/check-lesson-sd-sync-ui.mjs`
- Modify: `robot/esp32-server/main/manager-web/src/i18n/en.js`
- Modify: `robot/esp32-server/main/manager-web/src/i18n/vi.js`

- [ ] **Step 1: Add RED tests for each displayed failure domain**

Cover:

- CMS build `failed`: aggregate retry creates a generation rebuild request.
- ESP materialization `retry_wait`/connection failures: backend calls an internal ESP immediate-poll endpoint.
- Default legacy worker disabled/no legacy job: aggregate retry still queues useful work.
- Duplicate clicks and stale route callbacks remain fenced.

- [ ] **Step 2: Add an internal ESP immediate retry endpoint**

Expose only the internal route:

```py
web.post(
    "/internal/lesson-assets/generation/retry",
    self.handle_generation_retry,
)
```

The handler requires the same internal-auth boundary used by other `/internal/lesson-assets/*` routes, invokes `await generation_poller.run_once()`, and returns a strict result containing `state` and optional safe `errorCode`. It must not expose raw exception details or add a public unauthenticated POST route.

- [ ] **Step 3: Add the typed RobotEspClient call**

```ts
async retryLessonAssetGeneration(baseUrl: string): Promise<{
  state: 'accepted' | 'not_modified' | 'rejected';
  errorCode?: string;
}> {
  return this.request({
    baseUrl,
    method: 'POST',
    path: '/internal/lesson-assets/generation/retry',
    endpoint: 'POST /internal/lesson-assets/generation/retry',
    body: {},
  });
}
```

Use the existing configured ESP base iteration/failover pattern; never accept a caller-provided URL.

- [ ] **Step 4: Add the aggregate admin endpoint**

Add `POST /v1/admin/lesson-assets/retry` (no lesson id). It:

1. Calls `requestRebuild` with reason `admin_retry` in a short DB transaction.
2. Attempts the internal ESP immediate retry through configured bases.
3. Returns a truthful result:

```ts
{
  generationQueued: true,
  espRetry: 'accepted' | 'not_modified' | 'deferred' | 'unavailable',
  errorCode?: string,
}
```

Queueing the generation rebuild is the durable success condition. ESP unavailability returns `espRetry: 'unavailable'` without rolling back the queued rebuild. Do not invoke the legacy lesson sync repository.

- [ ] **Step 5: Rewire the admin client and UI**

Replace `retrySdSync(lessonId, ...)` with `retryLessonAssetGeneration(...)` calling `/lesson-assets/retry`. Keep the dedicated retry flag/token and navigation guards. Show queued success when `generationQueued === true`; optionally mention ESP immediate retry deferral with localized warning while continuing status polling.

- [ ] **Step 6: Run backend, ESP, and manager verification**

```bash
# backend
npx vitest run \
  src/lessons/lesson-asset-generation.controller.spec.ts \
  src/lessons/lesson-asset-generation.repository.spec.ts \
  src/robot/robot-esp.client.spec.ts
npm run typecheck
npm run build

# ESP manager server
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/single-version-lesson-visuals/main/tbot-server
pytest -q tests/test_global_generation_poller.py tests/test_http_server.py

# admin
cd ../manager-web
npm run test:lesson-sd-sync-ui
npm run test:lesson-visual-selection
npm run build
```

- [ ] **Step 7: Commit backend and ESP/admin slices separately**

```bash
# backend
git add src/lessons/lesson-asset-generation.controller.ts \
  src/lessons/lesson-asset-generation.controller.spec.ts \
  src/lessons/lesson-asset-generation.repository.ts \
  src/robot/robot-esp.client.ts src/robot/robot-esp.types.ts
git commit -m "feat(lessons): retry aggregate asset rollout"

# ESP/admin repo
git add main/tbot-server/core/http_server.py \
  main/tbot-server/core/lesson/global_generation_poller.py \
  main/tbot-server/tests/test_http_server.py \
  main/tbot-server/tests/test_global_generation_poller.py \
  main/manager-web/src/apis/module/lesson.js \
  main/manager-web/src/views/LessonEditor.vue \
  main/manager-web/scripts/check-lesson-sd-sync-ui.mjs \
  main/manager-web/src/i18n/en.js main/manager-web/src/i18n/vi.js
git commit -m "feat(admin): retry aggregate lesson rollout"
```

### Task 7: Re-run cross-layer proof and update evidence

**Files:**
- Modify: `robot/docs/TEST_MATRIX.md`

- [ ] **Step 1: Run backend focused/concurrency/generation tests**

Include the original seven-file suite plus the new per-step invariant, publish concurrency, generation stale-source, authoritative listing, aggregate retry, typecheck, lint, build, and OpenAPI validation.

- [ ] **Step 2: Run ESP manager server tests**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/single-version-lesson-visuals/main/tbot-server
pytest -q tests/test_global_generation_poller.py tests/test_global_generation_sync.py tests/test_http_server.py
```

- [ ] **Step 3: Run manager contracts and build**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/single-version-lesson-visuals/main/manager-web
npm run test:lesson-visual-selection
npm run test:lesson-sd-sync-ui
npm run test:lesson-editor-ui
npm run build
```

Report the accepted preview-width baseline honestly if it remains the only editor-contract failure.

- [ ] **Step 4: Attempt the existing authoring E2E only when its Compose state reset is available**

Do not reinstall packages. Record the exact blocker if the named Playwright project remains absent or the Docker Redis reset remains unavailable.

- [ ] **Step 5: Update the 2026-07-30 evidence section**

Use `apply_patch` on `/Users/manhhodinh/Documents/TBOT/robot/docs/TEST_MATRIX.md`. Record exact HEADs, totals, concurrency/fencing proof, aggregate retry proof, and browser/live status. Do not stage this file in either nested repository.

- [ ] **Step 6: Final hygiene and reviews**

Run `git diff --check`, clean-status checks, whole-feature spec review, whole-feature quality review, and `verification-before-completion`. Then use `finishing-a-development-branch` for the handoff.
