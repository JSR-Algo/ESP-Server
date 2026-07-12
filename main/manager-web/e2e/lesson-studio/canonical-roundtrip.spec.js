const { existsSync, readFileSync } = require('fs');
const { resolve } = require('path');
const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');

const apiRoot = '/nestjs/v1/admin';

function loadCanonicalSource() {
  const candidates = [
    process.env.TBOT_BACKEND_WORKTREE,
    resolve(process.cwd(), '../../../../tbot-backend/production-lesson-studio'),
    resolve(process.cwd(), '../../../../tbot-backend'),
  ].filter(Boolean);
  const root = candidates.find((candidate) => existsSync(resolve(candidate, 'src/lessons/fixtures/tvideo-raw-code/course.json')));
  if (!root) throw new Error(`Set TBOT_BACKEND_WORKTREE; canonical backend source not found in: ${candidates.join(', ')}`);
  return JSON.parse(readFileSync(resolve(root, 'src/lessons/fixtures/tvideo-raw-code/course.json'), 'utf8'));
}

async function api(page, method, path, data) {
  const token = await page.evaluate(() => localStorage.getItem('nestjs_session_token'));
  expect(token, 'real Nest Author session token must exist').toBeTruthy();
  const response = await page.request.fetch(`${apiRoot}${path}`, {
    method,
    data,
    headers: { 'Content-Type': 'application/json', 'X-Nest-Authorization': `Bearer ${token}` },
  });
  expect(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`).toBe(true);
  const body = await response.json();
  return body.data;
}

async function createVisualVersions(page, source, runId) {
  const categories = {
    backgroundScene: 'scene',
    teachingObject: 'teachingObject',
    robotOverlay: 'robotPose',
  };
  const keys = new Map();
  for (const [slot, assetKey] of Object.entries(source.visuals)) keys.set(assetKey, { slot, category: categories[slot] });
  for (const assetKey of Object.values(source.teachingObjects)) keys.set(assetKey, { slot: 'teachingObject', category: 'teachingObject' });
  const versions = new Map();
  let index = 0;
  for (const [assetKey, meta] of keys) {
    index += 1;
    const e2eKey = `canonical.${runId}.${assetKey}`;
    const version = await api(page, 'POST', `/lesson-visual-assets/${encodeURIComponent(e2eKey)}/versions`, {
      category: meta.category,
      title: `Canonical ${assetKey}`,
      profile: 'espTft',
      storagePath: 'http://127.0.0.1:8102/favicon.ico',
      sha256: index.toString(16).padStart(64, '0'),
      mimeType: 'image/png',
      bytes: 5430,
      width: 64,
      height: 64,
      publicationState: 'published',
    });
    versions.set(assetKey, version.id);
  }
  return versions;
}

async function importCanonicalDraft(page, source, runId) {
  const sourceCourseId = '00000006-0001-0000-0000-000000000001';
  const course = await api(page, 'POST', `/courses/${sourceCourseId}/clone`, {
    courseKey: `e2e-canonical-${runId}`,
    title: `${source.course.title} ${runId}`,
  });
  const [lesson] = await api(page, 'GET', `/courses/${course.id}/lessons`);
  const existingSteps = await api(page, 'GET', `/lessons/${lesson.id}/steps`);
  for (const step of [...existingSteps].reverse()) {
    await api(page, 'DELETE', `/lessons/${lesson.id}/steps/${encodeURIComponent(step.step_key || step.stepKey)}`);
  }
  await api(page, 'PATCH', `/lessons/${lesson.id}`, {
    title: `${source.lesson.title} ${runId}`,
    locale: source.lesson.locale,
    ageBand: source.lesson.ageBand,
    durationPreset: source.lesson.durationPreset,
    estimatedDurationSec: source.lesson.estimatedDurationSec,
    difficultyBand: source.lesson.difficultyBand,
    topicTags: source.lesson.topicTags,
  });
  const versions = await createVisualVersions(page, source, runId);
  const createdSteps = [];
  for (const sourceStep of source.steps) {
    const step = await api(page, 'POST', `/lessons/${lesson.id}/steps`, {
      stepType: sourceStep.stepType,
      prompt: sourceStep.prompt,
      subject: sourceStep.subject,
      choices: sourceStep.choices,
      stepBody: sourceStep.stepBody,
    });
    const stepKey = step.step_key || step.stepKey;
    createdSteps.push({ sourceStep, stepKey });
    const refs = {
      backgroundScene: source.visuals.backgroundScene,
      teachingObject: source.teachingObjects[sourceStep.subject] || source.visuals.teachingObject,
      robotOverlay: source.visuals.robotOverlay,
    };
    for (const [slot, assetKey] of Object.entries(refs)) {
      await api(page, 'PUT', `/lessons/${lesson.id}/steps/${encodeURIComponent(stepKey)}/visual-refs/${slot}`, {
        assetVersionId: versions.get(assetKey),
      });
    }
  }
  const actualKeyBySourceKey = new Map(createdSteps.map(({ stepKey }, index) => [`s${index + 1}`, stepKey]));
  for (const { sourceStep, stepKey } of createdSteps) {
    if (!sourceStep.stepBody.branches) continue;
    const branches = Object.fromEntries(Object.entries(sourceStep.stepBody.branches).map(([name, branch]) => [
      name,
      branch.nextStepKey ? { ...branch, nextStepKey: actualKeyBySourceKey.get(branch.nextStepKey) } : branch,
    ]));
    await api(page, 'PATCH', `/lessons/${lesson.id}/steps/${encodeURIComponent(stepKey)}`, {
      prompt: sourceStep.prompt,
      subject: sourceStep.subject,
      choices: sourceStep.choices,
      stepBody: { ...sourceStep.stepBody, branches },
    });
  }
  return { course, lesson: await api(page, 'GET', `/lessons/${lesson.id}`) };
}

function interactionItem(page, label) {
  return page.locator('.interaction-panel .el-form-item').filter({
    has: page.locator('.el-form-item__label', { hasText: new RegExp(`^${label}$`) }),
  });
}

function pinnedVisualIdentity(manifest) {
  return manifest.steps.flatMap((step) => Object.entries(step.scene || {}).flatMap(([slot, visual]) => [
    ['poster', visual && visual.poster],
    ['asset', visual && visual.asset],
  ].filter(([, pinned]) => pinned && pinned.assetKey)
    .map(([kind, pinned]) => ({
      stepId: step.id,
      slot: `${slot}.${kind}`,
      assetKey: pinned.assetKey,
      version: pinned.version,
      sha256: pinned.sha256,
      src: pinned.src,
    }))))
    .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
}

async function chooseSelect(page, item, label) {
  const input = item.locator('.el-select input');
  await input.click();
  const option = page.locator('body .el-select-dropdown__item:visible').filter({ hasText: new RegExp(`^${label}$`) }).last();
  await expect(option).toBeVisible();
  // Element UI animates and re-parents the shared dropdown while Vue updates the
  // authoring model. A DOM click targets the visible option before that repaint.
  await option.evaluate((element) => element.click());
  await expect(input).toHaveValue(label);
  await expect(option).toBeHidden();
}

test('canonical source imports, customizes, previews, publishes, and preserves v1 immutability', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  test.setTimeout(240_000);
  const source = loadCanonicalSource();
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  await loginAsLessonAuthor(page);
  const fixture = await importCanonicalDraft(page, source, runId);

  const validation = await api(page, 'POST', `/lessons/${fixture.lesson.id}/validate`);
  expect(validation.valid).toBe(true);
  const before = await api(page, 'GET', `/lessons/${fixture.lesson.id}/manifest-preview?profile=espTft`);
  expect(before.manifest.steps).toHaveLength(9);

  await page.goto(`/login#/lesson-editor?lessonId=${fixture.lesson.id}`);
  await expect(page.getByRole('heading', { name: new RegExp(source.lesson.title) })).toBeVisible();
  const customizedTitle = `${source.lesson.title} UI ${runId}`;
  await page.getByRole('button', { name: 'Rename' }).click();
  const renameDialog = page.getByRole('dialog', { name: 'Rename' });
  await renameDialog.getByRole('textbox').fill(customizedTitle);
  const renameResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}`)
    && response.request().method() === 'PATCH' && response.status() === 200);
  await renameDialog.getByRole('button', { name: 'Save' }).click();
  await renameResponse;
  await expect(page.getByRole('heading', { name: new RegExp(customizedTitle) })).toBeVisible();

  await page.locator('.step-nav__item').nth(3).click();
  await page.getByTestId('lesson-step-prompt').fill('Listen carefully, then greet the cow.');
  await page.getByTestId('lesson-step-subject').fill('cow');
  await page.getByTestId('lesson-step-helper').fill('Take one calm breath first.');
  await interactionItem(page, 'English teaching word').locator('input').fill('BARN');
  await chooseSelect(page, interactionItem(page, 'Fun pattern'), 'Robot Forgot');
  await interactionItem(page, 'Goal').locator('input').fill('Help Pip remember the cow greeting.');
  await interactionItem(page, 'Success reaction').locator('input').fill('pet.greetsCowAgain');
  await interactionItem(page, 'Next tease').locator('input').fill('Can Pip remember the corn too?');
  await interactionItem(page, 'Duration').locator('label[role="radio"]').filter({ hasText: /^5 min$/ }).click();
  for (const [slot, motion] of [
    ['Present', 'Teach'],
    ['Listen', 'Rest'],
    ['Correct', 'Goodbye'],
    ['Near Miss', 'Thinking'],
    ['Incorrect', 'Present Left'],
  ]) await chooseSelect(page, interactionItem(page, slot), motion);
  const selectedAssetKey = `canonical.${runId}.${source.teachingObjects.corn}`;
  await page.locator('.asset-tile').filter({ hasText: selectedAssetKey }).locator('.asset-tile__select').click();

  const saveResponse = page.waitForResponse((response) => response.url().includes(`/lessons/${fixture.lesson.id}/steps/`) && response.request().method() === 'PATCH' && response.status() === 200);
  await page.getByRole('button', { name: 'Save step' }).click();
  await saveResponse;

  await page.reload();
  await expect(page.getByRole('heading', { name: new RegExp(customizedTitle) })).toBeVisible();
  await page.locator('.step-nav__item').nth(3).click();
  await expect(page.getByTestId('lesson-step-prompt')).toHaveValue('Listen carefully, then greet the cow.');
  await expect(interactionItem(page, 'English teaching word').locator('input')).toHaveValue('BARN');
  await expect(interactionItem(page, 'Duration').locator('input[type="radio"][value="5"]')).toBeChecked();
  for (const [slot, motion] of [
    ['Present', 'Teach'],
    ['Listen', 'Rest'],
    ['Correct', 'Goodbye'],
    ['Near Miss', 'Thinking'],
    ['Incorrect', 'Present Left'],
  ]) await expect(interactionItem(page, slot).locator('.el-select input')).toHaveValue(motion);
  await expect(page.locator('.asset-tile').filter({ hasText: selectedAssetKey })).toHaveClass(/selected/);

  // Exercise the complete step lifecycle and prove drafts stay scoped to their
  // own selected step across selection, route navigation, reload, and reorder.
  const originalStepCount = await page.locator('.step-nav__item').count();
  const lifecyclePrompt = `Temporary lifecycle step ${runId}`;
  await page.getByRole('button', { name: '+ Add step' }).click();
  const stepDialog = page.getByRole('dialog', { name: 'Add step' });
  await stepDialog.getByRole('textbox', { name: 'Prompt' }).fill(lifecyclePrompt);
  await stepDialog.getByRole('textbox', { name: 'Vocab word / subject' }).fill('temporary');
  const addStepResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/steps`)
    && response.request().method() === 'POST' && response.status() === 201);
  await stepDialog.getByRole('button', { name: 'Save' }).click();
  await addStepResponse;
  await expect(page.locator('.step-nav__item')).toHaveCount(originalStepCount + 1);

  await page.locator('.step-nav__item').last().click();
  await expect(page.getByTestId('lesson-step-prompt')).toHaveValue(lifecyclePrompt);
  await page.locator('.step-nav__item').nth(3).click();
  await expect(page.getByTestId('lesson-step-prompt')).toHaveValue('Listen carefully, then greet the cow.');
  await page.locator('.step-nav__item').last().click();
  await expect(page.getByTestId('lesson-step-prompt')).toHaveValue(lifecyclePrompt);

  await page.goto('/login#/lesson-visual-library');
  await expect(page.getByRole('heading', { name: 'Shared visual library' })).toBeVisible();
  await page.goto(`/login#/lesson-editor?lessonId=${fixture.lesson.id}`);
  await expect(page.locator('.step-nav__item')).toHaveCount(originalStepCount + 1);
  await page.locator('.step-nav__item').last().click();
  await expect(page.getByTestId('lesson-step-prompt')).toHaveValue(lifecyclePrompt);
  await page.reload();
  await page.locator('.step-nav__item').last().click();
  await expect(page.getByTestId('lesson-step-prompt')).toHaveValue(lifecyclePrompt);

  const lifecycleRow = page.getByRole('row').filter({ hasText: lifecyclePrompt });
  const reorderResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/steps/reorder`)
    && response.request().method() === 'POST' && response.status() === 200);
  await lifecycleRow.getByRole('button', { name: '↑' }).click();
  await reorderResponse;
  await expect(page.locator('.step-nav__item').nth(originalStepCount - 1)).toContainText(lifecyclePrompt);
  const deleteResponse = page.waitForResponse((response) => response.url().includes(`/lessons/${fixture.lesson.id}/steps/`)
    && response.request().method() === 'DELETE' && response.status() === 200);
  await page.getByRole('row').filter({ hasText: lifecyclePrompt }).getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('button', { name: /ok|confirm/i }).last().click();
  await deleteResponse;
  await expect(page.locator('.step-nav__item')).toHaveCount(originalStepCount);
  await page.locator('.step-nav__item').nth(3).click();
  await expect(page.getByTestId('lesson-step-prompt')).toHaveValue('Listen carefully, then greet the cow.');

  const persisted = await api(page, 'GET', `/lessons/${fixture.lesson.id}/manifest-preview?profile=espTft`);
  const customizedStep = persisted.manifest.steps[3];
  const persistedSteps = await api(page, 'GET', `/lessons/${fixture.lesson.id}/steps`);
  expect(persisted.manifest.title).toBe(customizedTitle);
  expect(customizedStep.prompt).toBe('Listen carefully, then greet the cow.');
  expect((persistedSteps[3].step_body || persistedSteps[3].stepBody).durationPreset).toBe(5);
  expect(customizedStep.teachingWord.text).toBe('BARN');
  expect(customizedStep.interaction.funPattern).toBe('robotForgot');
  expect(customizedStep.storyBeat).toEqual({
    goal: 'Help Pip remember the cow greeting.',
    successReaction: 'pet.greetsCowAgain',
    nextTease: 'Can Pip remember the corn too?',
  });
  expect(customizedStep.motion).toMatchObject({
    present: 'teach', listen: 'rest', correct: 'goodbye', nearMiss: 'thinking', incorrect: 'presentLeft',
  });
  expect(customizedStep.scene.teachingObject.asset.assetKey).toBe(selectedAssetKey);

  const validateResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/validate`) && response.status() === 200);
  await page.getByRole('button', { name: /validate/i }).click();
  await validateResponse;
  await expect(page.locator('.readiness')).toContainText('READY');
  await expect(page.locator('.readiness')).toContainText('All pathsTerminate');

  const previewResponse = page.waitForResponse((response) => response.url().includes(`/lessons/${fixture.lesson.id}/manifest-preview`) && response.status() === 200);
  await page.getByRole('button', { name: /^preview$/i }).click();
  await previewResponse;
  const screenshotDir = resolve(process.cwd(), 'output/playwright');
  for (const minutes of [3, 5, 8]) {
    await page.locator('label[role="radio"]').filter({ hasText: new RegExp(`^${minutes} min$`) }).click();
    const durationSave = page.waitForResponse((response) => response.url().includes(`/lessons/${fixture.lesson.id}/steps/`)
      && response.request().method() === 'PATCH' && response.status() === 200);
    await page.getByRole('button', { name: 'Save step' }).click();
    await durationSave;
    const durationPreview = page.waitForResponse((response) => response.url().includes(`/lessons/${fixture.lesson.id}/manifest-preview`) && response.status() === 200);
    await page.getByRole('button', { name: /^preview$/i }).click();
    await durationPreview;
    await page.getByTestId('esp-tft-stage').screenshot({ path: resolve(screenshotDir, `canonical-preview-${minutes}m.png`) });
  }
  for (const label of ['Correct', 'Near miss', 'Incorrect', 'Retry', 'Timeout', 'Brave try', 'Completion']) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await expect(page.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('list', { name: 'Robot command timeline' })).toContainText(`Response path: ${label === 'Near miss' ? 'nearMiss' : label === 'Brave try' ? 'braveTry' : label.toLowerCase()}`);
  }

  const publishResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/publish`) && response.status() === 200);
  await page.getByRole('button', { name: /^publish$/i }).click();
  await page.getByRole('button', { name: /ok|confirm/i }).last().click();
  const published = (await (await publishResponse).json()).data;
  await expect(page.locator('.el-alert__title').filter({ hasText: `Published v${published.lessonVersion}` })).toBeVisible();

  const original = await api(page, 'GET', `/lessons/${fixture.lesson.id}`);
  expect(original.status).toBe('published');
  expect(original.manifest_checksum || original.manifestChecksum).toBe(published.checksum);
  await expect(page.getByRole('button', { name: 'Rename' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Publish' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '+ Add step' })).toHaveCount(0);
  await expect(page.getByTestId('lesson-step-prompt')).toBeDisabled();
  const publishedProjection = await api(page, 'GET', `/lessons/${fixture.lesson.id}/manifest-preview?profile=espTft`);
  const publishedPinnedVisuals = pinnedVisualIdentity(publishedProjection.manifest);

  await page.goto(`/login#/course-lessons?courseId=${fixture.course.id}&title=${encodeURIComponent(fixture.course.title)}`);
  await expect(page.getByRole('row').filter({ hasText: customizedTitle }).first()).toContainText('published');
  const newVersionButton = page.getByRole('button', { name: 'New version' });
  await expect(newVersionButton).toHaveCount(1);
  const nextDraftResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/new-version`)
    && response.request().method() === 'POST' && response.status() === 201);
  await newVersionButton.click();
  await page.getByRole('button', { name: /ok|confirm/i }).last().click();
  const nextDraft = (await (await nextDraftResponse).json()).data;
  await expect(page).toHaveURL(new RegExp(`lessonId=${nextDraft.id}`));
  await expect(page.getByRole('button', { name: 'Publish' })).toBeVisible();
  const childVisualKey = selectedAssetKey;
  const childVisualTile = page.locator('.asset-tile').filter({ hasText: childVisualKey });
  await childVisualTile.locator('.asset-tile__select').click();
  const childVisualSave = page.waitForResponse((response) => response.url().includes(`/lessons/${nextDraft.id}/steps/`)
    && response.request().method() === 'PATCH' && response.status() === 200);
  await page.getByRole('button', { name: 'Save step' }).click();
  await childVisualSave;
  const childProjection = await api(page, 'GET', `/lessons/${nextDraft.id}/manifest-preview?profile=espTft`);
  const childPinnedVisuals = pinnedVisualIdentity(childProjection.manifest);
  expect(childPinnedVisuals).not.toEqual(publishedPinnedVisuals);
  expect(childProjection.manifest.steps[0].scene.teachingObject.asset.assetKey).toBe(childVisualKey);
  const originalAfterDraftEdit = await api(page, 'GET', `/lessons/${fixture.lesson.id}`);
  expect(originalAfterDraftEdit.manifest_checksum || originalAfterDraftEdit.manifestChecksum).toBe(published.checksum);
  const originalProjectionAfterDraftEdit = await api(page, 'GET', `/lessons/${fixture.lesson.id}/manifest-preview?profile=espTft`);
  expect(pinnedVisualIdentity(originalProjectionAfterDraftEdit.manifest)).toEqual(publishedPinnedVisuals);
  assertNoUnexpectedPageErrors();
});
