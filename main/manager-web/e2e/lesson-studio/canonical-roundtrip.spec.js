const { existsSync, readFileSync } = require('fs');
const { createHash } = require('crypto');
const { resolve } = require('path');
const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { adminApi, adminAuthHeaders, apiRoot } = require('./helpers/admin-api');
const { lessonStudioAssetUrl } = require('../../scripts/lesson-studio-e2e-environment.cjs');

const responseVisualSourceIds = {
  'feedback.correct.star': '00000006-0016-4000-8000-000000000011',
  'feedback.near-miss.spark': '00000006-0016-4000-8000-000000000012',
  'feedback.incorrect.try-again': '00000006-0016-4000-8000-000000000013',
  'ending.farm.parade': '00000006-0016-4000-8000-000000000014',
};

function loadCanonicalFixture() {
  const candidates = [
    process.env.TBOT_BACKEND_WORKTREE,
    resolve(process.cwd(), '../../../../tbot-backend/production-lesson-studio'),
    resolve(process.cwd(), '../../../../tbot-backend'),
  ].filter(Boolean);
  const root = candidates.find((candidate) => existsSync(resolve(candidate, 'src/lessons/fixtures/tvideo-raw-code/course.json')));
  if (!root) throw new Error(`Set TBOT_BACKEND_WORKTREE; canonical backend source not found in: ${candidates.join(', ')}`);
  return {
    source: JSON.parse(readFileSync(resolve(root, 'src/lessons/fixtures/tvideo-raw-code/course.json'), 'utf8')),
    assetManifest: JSON.parse(readFileSync(resolve(root, 'src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json'), 'utf8')),
  };
}

const api = (page, method, path, data) => adminApi(page, method, path, data);
// Publishing goes through the immutable-version review dialog: acknowledge the
// immutability notice, then confirm. It is no longer a plain OK/confirm box.
async function confirmPublishReview(page) {
  const reviewDialog = page.getByRole('dialog', { name: /immutable version review/i });
  await expect(reviewDialog).toBeVisible();
  await reviewDialog.getByTestId('immutable-ack').click();
  await reviewDialog.getByRole('button', { name: /publish reviewed version/i }).click();
}


function espTftAssetForKey(assetManifest, assetKey) {
  const matches = assetManifest.espTft.filter((asset) => asset.keys.includes(assetKey));
  expect(matches, `one pinned espTft derivative must exist for ${assetKey}`).toHaveLength(1);
  return matches[0];
}

async function assertServedAsset(page, path, expected) {
  const response = await page.request.get(lessonStudioAssetUrl(path));
  expect(response.ok(), `GET tvideo demo asset ${path}`).toBe(true);
  const bytes = await response.body();
  expect(bytes).toHaveLength(expected.bytes);
  expect(createHash('sha256').update(bytes).digest('hex')).toBe(expected.sha256);
}

async function createVisualVersions(page, source, assetManifest, runId) {
  const categories = {
    backgroundScene: 'scene',
    teachingObject: 'teachingObject',
    robotOverlay: 'robotPose',
  };
  const keys = new Map();
  for (const [slot, assetKey] of Object.entries(source.visuals)) keys.set(assetKey, { slot, category: categories[slot] });
  for (const assetKey of Object.values(source.teachingObjects)) keys.set(assetKey, { slot: 'teachingObject', category: 'teachingObject' });
  for (const [slot, assetKey] of Object.entries(source.responseVisuals)) keys.set(assetKey, { slot, category: slot });
  const versions = new Map();
  for (const [assetKey, meta] of keys) {
    const e2eKey = `canonical.${runId}.${assetKey}`;
    const asset = espTftAssetForKey(assetManifest, assetKey);
    await assertServedAsset(page, asset.path, asset);
    const version = await api(page, 'POST', `/lesson-visual-assets/${encodeURIComponent(e2eKey)}/versions`, {
      category: meta.category,
      title: `Canonical ${assetKey}`,
      profile: 'espTft',
      storagePath: asset.path,
      sha256: asset.sha256,
      mimeType: asset.mediaType,
      bytes: asset.bytes,
      width: asset.width,
      height: asset.height,
      publicationState: 'published',
    });
    versions.set(assetKey, version.id);
  }
  return versions;
}

async function importCanonicalDraft(page, source, assetManifest, runId) {
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
  for (const assetKey of Object.values(source.responseVisuals)) {
    const sourceAssetId = responseVisualSourceIds[assetKey];
    expect(sourceAssetId, `seeded response visual source must exist for ${assetKey}`).toBeTruthy();
    await api(page, 'POST', `/lessons/${lesson.id}/assets`, { profile: 'espTft', sourceAssetId });
  }
  const versions = await createVisualVersions(page, source, assetManifest, runId);
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
    // robotOverlay is the only per-step visual slot the backend still accepts
    // (PER_STEP_VISUAL_SLOTS); background + teaching object are lesson-wide.
    await api(page, 'PUT', `/lessons/${lesson.id}/steps/${encodeURIComponent(stepKey)}/visual-refs/robotOverlay`, {
      assetVersionId: versions.get(source.visuals.robotOverlay),
    });
  }
  // One lesson-wide pair for every step, mirroring what the studio's
  // "Lesson background and object" control does.
  await api(page, 'PUT', `/lessons/${lesson.id}/visuals`, {
    backgroundAssetVersionId: versions.get(source.visuals.backgroundScene),
    objectAssetVersionId: versions.get(source.visuals.teachingObject),
  });
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
  return { course, lesson: await api(page, 'GET', `/lessons/${lesson.id}`), versions };
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

async function assertManifestVisualAssetsServed(page, manifest) {
  const visualsByUrl = new Map();
  for (const visual of pinnedVisualIdentity(manifest)) {
    expect(visual.src).toMatch(/^https?:\/\//);
    expect(visual.src.match(/https?:\/\//g)).toHaveLength(1);
    const existingSha = visualsByUrl.get(visual.src);
    expect(existingSha ?? visual.sha256, `one URL must not identify multiple assets: ${visual.src}`)
      .toBe(visual.sha256);
    visualsByUrl.set(visual.src, visual.sha256);
  }
  expect(visualsByUrl.size).toBeGreaterThan(0);
  for (const [url, sha256] of visualsByUrl) {
    const response = await page.request.get(url);
    expect(response.ok(), `GET manifest visual ${url}`).toBe(true);
    const bytes = await response.body();
    expect(createHash('sha256').update(bytes).digest('hex'), `SHA-256 for ${url}`).toBe(sha256);
  }
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
  const { source, assetManifest } = loadCanonicalFixture();
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  await loginAsLessonAuthor(page);
  expect(source.demo.adminPreview).toEqual(assetManifest.adminPreview);
  expect(assetManifest.adminPreview).toMatchObject({
    sourcePath: 'assets/scenes/deep-barn-farm-background-6s.mp4',
    mediaType: 'video/mp4',
    sha256: '53d3ac70d166ba83029d5d122493dc48304d2caf933e03c09b0907152531f5f1',
    bytes: 6228276,
    width: 1280,
    height: 720,
  });
  await assertServedAsset(page, assetManifest.adminPreview.path, assetManifest.adminPreview);
  const fixture = await importCanonicalDraft(page, source, assetManifest, runId);

  const validation = await api(page, 'POST', `/lessons/${fixture.lesson.id}/validate`);
  expect(validation.valid).toBe(true);
  const before = await api(page, 'GET', `/lessons/${fixture.lesson.id}/manifest-preview?profile=espTft`);
  expect(before.manifest.steps).toHaveLength(9);
  expect(JSON.stringify(before.manifest)).not.toMatch(/video\/mp4|\.mp4/i);
  const authoredVisuals = pinnedVisualIdentity(before.manifest);
  expect(authoredVisuals.length).toBeGreaterThan(0);
  await assertManifestVisualAssetsServed(page, before.manifest);
  for (const assetKey of Object.values(source.responseVisuals)) {
    expect(before.manifest.assets).toEqual(expect.arrayContaining([expect.objectContaining({
      id: assetKey,
      path: 'esp-tft/robots-bright-alive-k3-glowface-192.png',
      sha256: '4e2f33a3eada6222b814bb226042e614fcd81f876efa42327b5c2196d1caa9c4',
      mediaType: 'image/png',
      critical: false,
    })]));
  }

  await page.goto(`/login#/lesson-editor?lessonId=${fixture.lesson.id}&demoSource=tvideo-raw-code`);
  await expect(page.getByRole('heading', { name: new RegExp(source.lesson.title) })).toBeVisible();
  const sourceVideo = page.getByTestId('canonical-source-video');
  await expect(sourceVideo).toBeVisible();
  await expect(sourceVideo).toHaveAttribute('src', `/tvideo-demo/${assetManifest.adminPreview.path}`);
  await expect(sourceVideo).toHaveAttribute('poster', `/tvideo-demo/${assetManifest.adminPreview.posterPath}`);
  const sourceAssets = page.getByTestId('canonical-source-asset');
  await expect(sourceAssets).toHaveCount(3);
  for (const [index, asset] of assetManifest.espTft.entries()) {
    await expect(sourceAssets.nth(index)).toHaveAttribute('src', `/tvideo-demo/${asset.sourceCopyPath}`);
  }
  await expect.poll(() => sourceVideo.evaluate((video) => (
    video.readyState >= 1 && Number.isFinite(video.duration) ? video.duration : null
  ))).toBeCloseTo(assetManifest.adminPreview.durationMs / 1000, 1);
  await sourceVideo.evaluate(async (video) => {
    video.currentTime = 0;
    await video.play();
  });
  await expect.poll(() => sourceVideo.evaluate((video) => video.paused), { timeout: 10_000 }).toBe(false);
  await expect.poll(() => sourceVideo.evaluate((video) => video.currentTime), { timeout: 10_000 }).toBeGreaterThan(0);
  await sourceVideo.evaluate((video) => video.pause());
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
  expect(JSON.stringify(persisted.manifest)).not.toMatch(/video\/mp4|\.mp4/i);
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
  // budgetRows renders the branch-termination row as label "All paths" + PASS/FAIL.
  await expect(page.locator('.readiness')).toContainText('All pathsPASS');

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
  // Scope to the response-path toolbar: labels like "Correct" also name
  // LessonSimulationPanel preset buttons, so an unscoped lookup is ambiguous.
  const responsePaths = page.getByLabel('Response paths');
  for (const label of ['Correct', 'Near miss', 'Incorrect', 'Retry', 'Timeout', 'Brave try', 'Completion']) {
    await responsePaths.getByRole('button', { name: label, exact: true }).click();
    await expect(responsePaths.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('list', { name: 'Robot command timeline' })).toContainText(`Response path: ${label === 'Near miss' ? 'nearMiss' : label === 'Brave try' ? 'braveTry' : label.toLowerCase()}`);
  }

  // Publish requires validation, preview and simulation evidence all pinned to
  // the same proofVersion (canPublishCurrentProof). The duration-preset saves
  // above cleared the validation, so re-validate before simulating.
  const revalidateResponse = page.waitForResponse((response) =>
    response.url().endsWith(`/lessons/${fixture.lesson.id}/validate`) && response.status() === 200);
  await page.getByRole('button', { name: /validate/i }).click();
  await revalidateResponse;

  const simulateResponse = page.waitForResponse((response) =>
    response.url().includes(`/lessons/${fixture.lesson.id}/simulate`) && response.status() === 200);
  await page.getByRole('button', { name: 'Simulate', exact: true }).click();
  await simulateResponse;
  await expect(page.locator('.simulation-result')).toContainText('lesson_completed');

  const publishResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/publish`) && response.status() === 200);
  await expect(page.getByRole('button', { name: /^publish$/i })).toBeEnabled();
  await page.getByRole('button', { name: /^publish$/i }).click();
  await confirmPublishReview(page);
  const published = (await (await publishResponse).json()).data;
  // The confirmation shows twice: the page banner and the review dialog's result alert.
  await expect(page.locator('.el-alert__title')
    .filter({ hasText: `Published v${published.lessonVersion}` }).first()).toBeVisible();

  const original = await api(page, 'GET', `/lessons/${fixture.lesson.id}`);
  expect(original.status).toBe('published');
  expect(original.manifest_checksum || original.manifestChecksum).toBe(published.checksum);
  await expect(page.getByRole('button', { name: 'Rename' })).toHaveCount(0);
  // exact: the review dialog's own "Publish reviewed version" button would
  // otherwise match this substring lookup.
  await expect(page.getByRole('button', { name: 'Publish', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '+ Add step' })).toHaveCount(0);
  await expect(page.getByTestId('lesson-step-prompt')).toBeDisabled();
  const publishedProjection = await api(page, 'GET', `/lessons/${fixture.lesson.id}/manifest-preview?profile=espTft`);
  expect(JSON.stringify(publishedProjection.manifest)).not.toMatch(/video\/mp4|\.mp4/i);
  await assertManifestVisualAssetsServed(page, publishedProjection.manifest);
  const publishedPinnedVisuals = pinnedVisualIdentity(publishedProjection.manifest);

  await page.goto(`/login#/course-lessons?courseId=${fixture.course.id}&title=${encodeURIComponent(fixture.course.title)}`);
  await expect(page.getByRole('row').filter({ hasText: customizedTitle }).first()).toContainText('published');
  // Creating the next editable version moved out of the lesson list and onto the
  // lesson editor, as "Create editable version" (data-testid create-next-version).
  await page.goto(`/login#/lesson-editor?lessonId=${fixture.lesson.id}`);
  await expect(page.getByRole('heading', { name: new RegExp(customizedTitle) })).toBeVisible();
  const newVersionButton = page.getByTestId('create-next-version');
  await expect(newVersionButton).toHaveCount(1);
  await expect(page.getByTestId('create-course-mode-v5-version')).toHaveCount(0);
  const nextDraftResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/new-version`)
    && response.request().method() === 'POST' && response.status() === 201);
  await newVersionButton.click();
  await page.getByRole('button', { name: /ok|confirm/i }).last().click();
  const nextDraftHttpResponse = await nextDraftResponse;
  expect(nextDraftHttpResponse.request().postData()).toBeNull();
  const nextDraft = (await nextDraftHttpResponse.json()).data;
  await expect(page).toHaveURL(new RegExp(`lessonId=${nextDraft.id}`));
  await expect(page.getByRole('button', { name: 'Publish', exact: true })).toBeVisible();
  // Must differ from what v1 published (the parent draft already pinned corn
  // lesson-wide), otherwise this proves nothing about the child diverging.
  const childVisualKey = `canonical.${runId}.${source.teachingObjects.hen}`;
  expect(childVisualKey).not.toBe(selectedAssetKey);
  // The teaching object is a lesson-wide visual now: CinematicLayerPicker commits
  // straight to PUT /lessons/:id/visuals, so there is no per-step "Save step" hop.
  const childVisualSave = page.waitForResponse((response) => response.url().endsWith(`/lessons/${nextDraft.id}/visuals`)
    && response.request().method() === 'PUT' && response.status() === 200);
  const childVisualTile = page.getByTestId('lesson-object-selector').locator('.asset-tile')
    .filter({ hasText: childVisualKey });
  await childVisualTile.locator('.asset-tile__select').click();
  const childVisualSaveResponse = await childVisualSave;
  expect(childVisualSaveResponse.request().postDataJSON()).toMatchObject({
    backgroundAssetVersionId: fixture.versions.get(source.visuals.backgroundScene),
    objectAssetVersionId: fixture.versions.get(source.teachingObjects.hen),
    robotAssetVersionId: fixture.versions.get(source.visuals.robotOverlay),
  });
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
