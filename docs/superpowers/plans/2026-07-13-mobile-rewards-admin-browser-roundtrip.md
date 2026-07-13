# Mobile Rewards Admin Browser Round-Trip Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the authenticated manager-web customization round-trip required by the mobile rewards design, including shared-visual impact review, deterministic branch simulation, exact 480x320 preview, immutable publish review, and real browser evidence.

**Architecture:** NestJS remains the authority for validated manifests, shared-asset pins, simulation, checksums, and immutable versions. Manager-web adds focused Vue 2 components that consume those authoritative endpoints without reconstructing backend truth. A Playwright browser suite starts disposable PostgreSQL, the Nest backend, and manager-web with shared-token fallback disabled, then drives the real login and authoring flow.

**Tech Stack:** NestJS, TypeScript, PostgreSQL, Vitest, Vue 2, Element UI, Node contract tests, Playwright, Docker.

---

### Task 1: Harden Authoring Request Validation

**Files:**
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.dto.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.controller.ts`
- Test: `tbot-backend/src/lessons/authoring/lesson-authoring.dto.coverage.spec.ts`
- Test: `tbot-backend/src/lessons/authoring/lesson-authoring.validation-pipe.spec.ts`

- [ ] **Step 1: Write a failing global ValidationPipe regression test**

```ts
it('preserves clone-shared-visual fields through the production ValidationPipe', async () => {
  const transformed = await productionPipe.transform(
    { profile: 'espTft', assetKey: 'teachingObject.glowSeed.v2' },
    { type: 'body', metatype: CloneSharedVisualDto, data: '' },
  );
  expect(transformed).toEqual({
    profile: 'espTft',
    assetKey: 'teachingObject.glowSeed.v2',
  });
});
```

- [ ] **Step 2: Run the regression test and verify RED**

Run: `npx vitest run src/lessons/authoring/lesson-authoring.validation-pipe.spec.ts`

Expected: FAIL because Swagger-only DTO properties are stripped by `whitelist: true`.

- [ ] **Step 3: Add real validation decorators to body DTOs**

```ts
export class CloneSharedVisualDto {
  @ApiProperty({ example: 'espTft' })
  @IsString()
  @IsNotEmpty()
  profile!: string;

  @ApiProperty({ example: 'teachingObject.glowSeed.v2' })
  @IsString()
  @IsNotEmpty()
  assetKey!: string;
}
```

Apply the same production-safe pattern to `CloneCourseDto` and `SetCourseTemplateDto`; keep the existing manual domain validation in controllers.

- [ ] **Step 4: Verify DTO and ValidationPipe coverage**

Run: `npx vitest run src/lessons/authoring/lesson-authoring.dto.coverage.spec.ts src/lessons/authoring/lesson-authoring.validation-pipe.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lessons/authoring/lesson-authoring.dto.ts src/lessons/authoring/lesson-authoring.controller.ts src/lessons/authoring/lesson-authoring.dto.coverage.spec.ts src/lessons/authoring/lesson-authoring.validation-pipe.spec.ts
git commit -m "fix(authoring): preserve validated browser request bodies"
```

### Task 2: Expose Authoritative Lesson Simulation over Admin HTTP

**Files:**
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.dto.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring-simulation.service.ts`
- Modify: `tbot-backend/src/lessons/authoring/lesson-authoring.controller.ts`
- Test: `tbot-backend/src/lessons/authoring/lesson-authoring-simulation.service.spec.ts`
- Test: `tbot-backend/src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts`

- [ ] **Step 1: Write failing parser and controller tests**

```ts
it('rejects unknown outcomes and invalid transition bounds', () => {
  expect(() => parseSimulationInput({ outcomes: { s2: ['unknown'] } })).toThrow(/outcome/i);
  expect(() => parseSimulationInput({ outcomes: {}, maxTransitions: 0 })).toThrow(/maxTransitions/i);
  expect(() => parseSimulationInput({ outcomes: {}, maxTransitions: 501 })).toThrow(/maxTransitions/i);
});

it('simulates the exact validated manifest preview', async () => {
  authoring.manifestPreview.mockResolvedValue(preview);
  simulation.simulate.mockReturnValue(result);
  await expect(controller.simulateLesson(LESSON_ID, 'espTft', body)).resolves.toEqual({
    data: { checksum: preview.checksum, etag: preview.etag, preview: preview.preview, simulation: result },
  });
});
```

- [ ] **Step 2: Run tests and verify RED**

Run: `npx vitest run src/lessons/authoring/lesson-authoring-simulation.service.spec.ts src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts`

Expected: FAIL because no HTTP simulation contract or strict request parser exists.

- [ ] **Step 3: Define named OpenAPI request/response models**

```ts
export class SimulateLessonDto {
  @ApiProperty({ type: Object })
  projection!: AuthoringBranchProjection;

  @ApiProperty({ type: Object })
  outcomes!: Partial<Record<string, SafeSpeakingOutcome[]>>;

  @ApiPropertyOptional({ minimum: 1, maximum: 500, default: 100 })
  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(500)
  maxTransitions?: number;
}
```

- [ ] **Step 4: Add strict dynamic-map parsing**

```ts
export function parseSimulationInput(input: unknown): ParsedSimulationInput {
  const body = requireRecord(input, 'simulation body');
  return {
    projection: parseProjection(body.projection),
    outcomes: parseOutcomeMap(body.outcomes),
    maxTransitions: parseBoundedInteger(body.maxTransitions, 100, 1, 500),
  };
}
```

Reject unknown outcomes and unknown projection actions rather than treating them as fallback.

- [ ] **Step 5: Add the guarded controller route**

```ts
@Post('lessons/:lessonId/simulate')
@ApiBearerAuth('JWT')
async simulateLesson(
  @Param('lessonId') lessonId: string,
  @Query('profile') profile = 'espTft',
  @Body() body: SimulateLessonDto,
) {
  const input = parseSimulationInput(body);
  const preview = await this.svc.manifestPreview(lessonId, profile);
  const simulation = this.simulation.simulate(
    preview.manifest,
    input.projection,
    input.outcomes,
    input.maxTransitions,
  );
  return { data: { checksum: preview.checksum, etag: preview.etag, preview: preview.preview, simulation } };
}
```

- [ ] **Step 6: Verify backend authoring tests**

Run: `npx vitest run src/lessons/authoring/lesson-authoring-simulation.service.spec.ts src/lessons/authoring/lesson-authoring.controller.coverage.spec.ts src/lessons/authoring/lesson-authoring.dto.coverage.spec.ts`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lessons/authoring/lesson-authoring.dto.ts src/lessons/authoring/lesson-authoring-simulation.service.ts src/lessons/authoring/lesson-authoring.controller.ts src/lessons/authoring/*.spec.ts
git commit -m "feat(authoring): expose validated branch simulation"
```

### Task 3: Add Manager-Web Authoring API Contracts and Pure Helpers

**Files:**
- Modify: `main/manager-web/src/apis/module/lesson.js`
- Modify: `main/manager-web/src/components/lesson/lesson-builder-logic.js`
- Modify: `main/manager-web/scripts/check-lesson-builder-logic.cjs`
- Modify: `main/manager-web/package.json`

- [ ] **Step 1: Write failing helper contracts**

```js
assert.deepStrictEqual(collectAssetReferences(steps, 'teachingObject.glowSeed.v1'), ['s2', 's5']);
assert.strictEqual(nextClonedAssetKey('teachingObject.glowSeed.v1', assets), 'teachingObject.glowSeed.v2');
assert.deepStrictEqual(replaceStepAssetReference(body, oldKey, clone), expectedBody);
assert.notStrictEqual(replaceStepAssetReference(body, oldKey, clone), body);
```

- [ ] **Step 2: Run and verify RED**

Run: `node scripts/check-lesson-builder-logic.cjs`

Expected: FAIL with missing exports.

- [ ] **Step 3: Implement immutable asset-reference helpers**

```js
function containsAssetKey(value, assetKey) {
  if (Array.isArray(value)) return value.some((item) => containsAssetKey(item, assetKey));
  if (!value || typeof value !== 'object') return false;
  if (value.assetKey === assetKey) return true;
  return Object.values(value).some((item) => containsAssetKey(item, assetKey));
}

export function collectAssetReferences(steps, assetKey) {
  return steps
    .filter((step) => containsAssetKey(step.stepBody || step.body || {}, assetKey))
    .map((step) => step.stepKey || step.stepId)
    .filter(Boolean);
}

export function nextClonedAssetKey(assetKey, assets) {
  const base = assetKey.replace(/\.v\d+$/, '');
  const used = new Set(assets.map((asset) => asset.assetKey));
  let version = 2;
  while (used.has(`${base}.v${version}`)) version += 1;
  return `${base}.v${version}`;
}

export function replaceStepAssetReference(value, fromKey, clone) {
  if (Array.isArray(value)) return value.map((item) => replaceStepAssetReference(item, fromKey, clone));
  if (!value || typeof value !== 'object') return value;
  if (value.assetKey === fromKey) {
    return { ...value, assetId: clone.assetId, assetKey: clone.assetKey, path: clone.path, sha256: clone.sha256 };
  }
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, replaceStepAssetReference(item, fromKey, clone)]),
  );
}
```

- [ ] **Step 4: Add the authoritative API methods**

```js
reviewSharedVisualImpact(assetId, onSuccess, onError) {
  nestRequest({ url: `${getNestUrl()}/assets/${assetId}/impact`, method: 'GET', onSuccess, onError });
},
cloneSharedVisual(lessonId, assetId, data, onSuccess, onError) {
  nestRequest({ url: `${getNestUrl()}/lessons/${lessonId}/assets/${assetId}/clone`, method: 'POST', data, onSuccess, onError });
},
simulate(lessonId, data, onSuccess, onError) {
  nestRequest({ url: `${getNestUrl()}/lessons/${lessonId}/simulate?profile=espTft`, method: 'POST', data, onSuccess, onError });
},
```

- [ ] **Step 5: Register and run the helper gate**

Add `"test:lesson-builder-logic": "node scripts/check-lesson-builder-logic.cjs"`.

Run: `npm run test:lesson-builder-logic`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/manager-web/src/apis/module/lesson.js main/manager-web/src/components/lesson/lesson-builder-logic.js main/manager-web/scripts/check-lesson-builder-logic.cjs main/manager-web/package.json
git commit -m "feat(admin): add lesson roundtrip client contracts"
```

### Task 4: Edit Existing Step Prompts without Recreating Steps

**Files:**
- Create: `main/manager-web/src/components/lesson/LessonStepPromptEditor.vue`
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/src/i18n/en.js`
- Modify: `main/manager-web/src/i18n/vi.js`
- Create: `main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`

- [ ] **Step 1: Write a failing UI contract**

```js
expectContains('src/views/LessonEditor.vue', '<lesson-step-prompt-editor', 'selected draft steps need prompt editing');
expectContains('src/views/LessonEditor.vue', 'prompt: this.promptDraft', 'save must persist the edited prompt');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "this.$emit('input'", 'editor must be controlled');
```

- [ ] **Step 2: Run and verify RED**

Run: `node scripts/check-lesson-editor-ui-contracts.mjs`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement a controlled prompt editor**

```vue
<el-form-item :label="$t('lesson.prompt')">
  <el-input
    type="textarea"
    :value="value"
    :disabled="disabled"
    maxlength="500"
    show-word-limit
    @input="$emit('input', $event)"
  />
</el-form-item>
```

- [ ] **Step 4: Wire selection, dirty state, and save**

Initialize `promptDraft` whenever `selectedStep.stepKey` changes. Include `prompt: this.promptDraft` in `Api.lesson.updateStep()`. Clear dirty state only after the server-confirmed step is fetched.

- [ ] **Step 5: Add EN/VI keys and run UI gates**

Run: `node scripts/check-lesson-editor-ui-contracts.mjs && npm run test:course-admin-ui`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add main/manager-web/src/components/lesson/LessonStepPromptEditor.vue main/manager-web/src/views/LessonEditor.vue main/manager-web/src/i18n/en.js main/manager-web/src/i18n/vi.js main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs
git commit -m "feat(admin): edit existing lesson prompts"
```

### Task 5: Add Shared-Visual Impact Review and Clone-for-Draft

**Files:**
- Create: `main/manager-web/src/components/lesson/SharedVisualImpactDialog.vue`
- Modify: `main/manager-web/src/components/lesson/SharedAssetPicker.vue`
- Modify: `main/manager-web/src/components/LessonAssetManager.vue`
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`

- [ ] **Step 1: Write failing event and safety contracts**

```js
expectContains('src/components/lesson/SharedAssetPicker.vue', "this.$emit('select-intent'", 'selection must review impact first');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'reviewSharedVisualImpact', 'dialog must load backend usage truth');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'cloneSharedVisual', 'dialog must clone without mutating source pins');
```

- [ ] **Step 2: Run and verify RED**

Run: `node scripts/check-lesson-editor-ui-contracts.mjs`

Expected: FAIL.

- [ ] **Step 3: Implement the review dialog**

The dialog must show source key/checksum, every affected lesson/version/status, current-step references, proposed collision-free clone key, and two explicit actions: keep the shared pin or clone for this draft.

```js
async confirmClone() {
  Api.lesson.cloneSharedVisual(this.lessonId, this.asset.assetId, {
    profile: 'espTft',
    assetKey: this.cloneKey,
  }, (clone) => this.$emit('cloned', clone), this.fail);
}
```

- [ ] **Step 4: Rebind only the selected draft step after clone**

Use `replaceStepAssetReference()` and `Api.lesson.updateStep()`; refetch assets, steps, validation, and preview after the server confirms.

- [ ] **Step 5: Gate replacement uploads behind impact review**

`LessonAssetManager.startReplace()` must emit an impact request before enabling file replacement for a shared asset.

- [ ] **Step 6: Verify UI and logic gates**

Run: `npm run test:lesson-builder-logic && node scripts/check-lesson-editor-ui-contracts.mjs && npm run test:course-robot-e2e-ui`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add main/manager-web/src/components/lesson main/manager-web/src/components/LessonAssetManager.vue main/manager-web/src/views/LessonEditor.vue main/manager-web/scripts
git commit -m "feat(admin): review and clone shared visuals safely"
```

### Task 6: Add Authoritative Simulation and Exact 480x320 Preview

**Files:**
- Create: `main/manager-web/src/components/lesson/LessonSimulationPanel.vue`
- Modify: `main/manager-web/src/components/lesson/RobotLessonPreview.vue`
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`
- Modify: `main/manager-web/scripts/check-course-robot-e2e-ui-contracts.mjs`

- [ ] **Step 1: Write failing contracts for exact preview and simulation truth**

```js
expectContains('src/components/lesson/RobotLessonPreview.vue', 'width: 480px', 'inner stage must match espTft width');
expectContains('src/components/lesson/RobotLessonPreview.vue', 'height: 320px', 'inner stage must match espTft height');
expectContains('src/components/lesson/LessonSimulationPanel.vue', 'Api.lesson.simulate', 'simulation must use backend manifest truth');
expectContains('src/views/LessonEditor.vue', 'invalidatePreview', 'all authoring mutations must invalidate stale preview');
```

- [ ] **Step 2: Run and verify RED**

Run: `node scripts/check-lesson-editor-ui-contracts.mjs && npm run test:course-robot-e2e-ui`

Expected: FAIL.

- [ ] **Step 3: Render an authoritative fixed stage**

Use a `480px x 320px` inner stage and a responsive outer scaler. Render only `manifestPreview` data and show server `preview.profile/width/height`, checksum, and ETag.

- [ ] **Step 4: Add deterministic scenario controls**

Expose correct, near-miss, brave-try, incorrect-to-fallback, retry-then-correct, timeout, and completion presets. Render `terminated`, `terminationReason`, attempts, actions, and trace order returned by the backend.

- [ ] **Step 5: Invalidate stale proof**

Every step, order, asset, prompt, lesson-title, duration, teaching-word, story, fun-pattern, or motion mutation must clear preview/simulation results until validate/preview/simulate is rerun.

- [ ] **Step 6: Verify contracts and build**

Run: `node scripts/check-lesson-editor-ui-contracts.mjs && npm run test:course-robot-e2e-ui && npm run build`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add main/manager-web/src/components/lesson main/manager-web/src/views/LessonEditor.vue main/manager-web/scripts
git commit -m "feat(admin): add exact preview and branch simulation"
```

### Task 7: Gate Publish with Validation and Immutable-Version Review

**Files:**
- Create: `main/manager-web/src/components/lesson/LessonPublishReviewDialog.vue`
- Modify: `main/manager-web/src/components/lesson/LessonPublishReadiness.vue`
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/src/i18n/en.js`
- Modify: `main/manager-web/src/i18n/vi.js`
- Modify: `main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`

- [ ] **Step 1: Write failing publish-gate contracts**

```js
expectContains('src/views/LessonEditor.vue', 'validationResult', 'server validation must remain visible');
expectContains('src/views/LessonEditor.vue', 'publishReviewVisible', 'publish needs a review stage');
expectContains('src/components/lesson/LessonPublishReviewDialog.vue', 'originalChecksum', 'review must preserve original evidence');
expectContains('src/components/lesson/LessonPublishReviewDialog.vue', 'previewChecksum', 'review must bind publish to current preview');
```

- [ ] **Step 2: Run and verify RED**

Run: `node scripts/check-lesson-editor-ui-contracts.mjs`

Expected: FAIL.

- [ ] **Step 3: Persist authoritative validation results**

Replace toast-only validation with visible server profiles/errors/warnings and local budget rows. Publish remains disabled when validation is absent/stale, preview checksum is absent/stale, a step is dirty, or any budget fails.

- [ ] **Step 4: Implement publish review**

Show source lesson/version/checksum/pins, target version, step/asset counts, exact preview metadata, simulation completion, and an explicit immutable-version acknowledgement.

- [ ] **Step 5: Re-read original evidence after publish**

After publishing vNext, fetch the original lesson manifest/checksum/assets and compare them with the snapshot. Display PASS only when the original bytes and pins remain unchanged.

- [ ] **Step 6: Verify and commit**

Run: `node scripts/check-lesson-editor-ui-contracts.mjs && npm run test:course-admin-ui && npm run build`

```bash
git add main/manager-web/src/components/lesson main/manager-web/src/views/LessonEditor.vue main/manager-web/src/i18n/en.js main/manager-web/src/i18n/vi.js main/manager-web/scripts
git commit -m "feat(admin): gate immutable lesson publishing"
```

### Task 8: Add Authenticated Playwright Browser Round-Trip

**Files:**
- Modify: `main/manager-web/package.json`
- Create: `main/manager-web/playwright.config.js`
- Create: `main/manager-web/e2e/rewards-admin-roundtrip.spec.js`
- Create: `main/manager-web/scripts/run-rewards-admin-browser-e2e.mjs`
- Modify: `main/manager-web/vue.config.js`
- Modify: `migrate-ui-ux-to-mobile-app-docs/qa/2026-07-12-adhoc-mobile-robot-rewards-leaderboard.md`

- [ ] **Step 1: Add the failing browser spec**

```js
test('admin customizes the canonical lesson without mutating v1', async ({ page }) => {
  await page.goto('/');
  await loginWithRealAdminSession(page, fixture.admin);
  await cloneCanonicalLesson(page);
  await editRequiredFields(page);
  await reviewAndCloneSharedVisual(page);
  await assertExactPreview(page, 480, 320);
  await runRequiredSimulations(page);
  await publishAndAssertOriginalImmutable(page);
});
```

Capture unexpected console errors, failed requests, response status/schema mismatches, stale preview state, and navigation state as test failures.

- [ ] **Step 2: Configure a browser-only admin posture**

Disable `NESTJS_ADMIN_TOKEN` fallback during E2E. Require the real manager-web login/MFA dialog to store a per-user session. Proxy only to the disposable backend URL.

- [ ] **Step 3: Add disposable service orchestration**

The runner must create PostgreSQL, apply migrations, build/start Nest with `PORT=0`, seed a login-capable admin and canonical lesson, start manager-web on a free local port, run Playwright, and clean all process groups/containers on normal exit or SIGINT/SIGTERM.

- [ ] **Step 4: Add deterministic artifacts**

Write trace, screenshot, request summary, console summary, v1/v2 checksums, and pinned-asset comparison under `output/playwright/rewards-admin-roundtrip/`.

- [ ] **Step 5: Run browser E2E and verify GREEN**

Run: `npm run test:e2e:rewards-admin:live`

Expected: PASS with zero unexpected console errors and all required artifacts.

- [ ] **Step 6: Update QA evidence and commit**

Only change the authenticated admin-browser and full post-customization immutability rows to PASS after the live browser command succeeds.

```bash
git add main/manager-web/package.json main/manager-web/playwright.config.js main/manager-web/e2e main/manager-web/scripts main/manager-web/vue.config.js migrate-ui-ux-to-mobile-app-docs/qa/2026-07-12-adhoc-mobile-robot-rewards-leaderboard.md
git commit -m "test(admin): prove rewards lesson browser roundtrip"
```

### Task 9: Run Release Gates and Cross-Repository Review

**Files:**
- Modify: `migrate-ui-ux-to-mobile-app-docs/qa/2026-07-12-adhoc-mobile-robot-rewards-leaderboard.md`

- [ ] **Step 1: Run backend gates**

```bash
npm run typecheck
npm run lint -- --quiet
npm run build
npx vitest run src/lessons/authoring tests/lessons.canonical-admin-roundtrip.integration.spec.ts
```

- [ ] **Step 2: Run manager-web gates**

```bash
npm run test:lesson-builder-logic
node scripts/check-lesson-editor-ui-contracts.mjs
npm run test:course-admin-ui
npm run test:course-robot-e2e-ui
npm run build
```

- [ ] **Step 3: Run live HTTP and browser gates**

```bash
npm run test:e2e:rewards:live
npm run test:e2e:rewards-admin:live
```

- [ ] **Step 4: Review privacy and immutability artifacts**

Confirm browser/network artifacts contain no raw parent email, parent/household IDs, transcripts, scores, precise reward timestamps, passwords, MFA secrets, or admin session tokens.

- [ ] **Step 5: Request independent code review**

Review backend simulation/validation, manager-web UI state, browser authentication, process cleanup, and QA claims. Fix every Critical/Important finding and rerun the affected gates.

- [ ] **Step 6: Commit final evidence**

```bash
git add migrate-ui-ux-to-mobile-app-docs/qa/2026-07-12-adhoc-mobile-robot-rewards-leaderboard.md
git commit -m "docs(qa): record authenticated rewards admin proof"
```

---

## Separate Native Plan

Rewards Detox work remains a separate subsystem plan. It must first install CocoaPods, align the Android AVD name, add a rewards-specific Detox scenario, build native artifacts, and run iOS/Android tests. Physical-robot evidence remains `NOT PASS` until a real robot run is captured.
