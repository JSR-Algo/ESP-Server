const { existsSync, readFileSync } = require('fs');
const { resolve } = require('path');
const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');

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
  return page.locator('.interaction-panel .el-form-item').filter({ has: page.locator('.el-form-item__label', { hasText: label }) });
}

async function chooseSelect(page, item, label) {
  await item.locator('.el-select input').click();
  await page.locator('body .el-select-dropdown__item:visible').filter({ hasText: label }).last().click();
}

test('canonical source imports, customizes, previews, publishes, and preserves v1 immutability', async ({ page }) => {
  test.setTimeout(90_000);
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
  await page.locator('.step-nav__item').nth(3).click();
  await page.getByTestId('lesson-step-prompt').fill('Listen carefully, then greet the cow.');
  await page.getByTestId('lesson-step-subject').fill('cow');
  await page.getByTestId('lesson-step-helper').fill('Take one calm breath first.');
  await interactionItem(page, 'English teaching word').locator('input').fill('BARN');
  await chooseSelect(page, interactionItem(page, 'Fun pattern'), 'Robot Forgot');
  await interactionItem(page, 'Goal').locator('input').fill('Help Pip remember the cow greeting.');
  await interactionItem(page, 'Success reaction').locator('input').fill('pet.greetsCowAgain');
  await interactionItem(page, 'Next tease').locator('input').fill('Can Pip remember the corn too?');
  await chooseSelect(page, interactionItem(page, 'Present'), 'Present Right');
  await page.locator('.asset-tile__select').first().click();

  const saveResponse = page.waitForResponse((response) => response.url().includes(`/lessons/${fixture.lesson.id}/steps/`) && response.request().method() === 'PATCH' && response.status() === 200);
  await page.getByRole('button', { name: 'Save step' }).click();
  await saveResponse;

  const validateResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/validate`) && response.status() === 200);
  await page.getByRole('button', { name: /validate/i }).click();
  await validateResponse;

  const previewResponse = page.waitForResponse((response) => response.url().includes(`/lessons/${fixture.lesson.id}/manifest-preview`) && response.status() === 200);
  await page.getByRole('button', { name: /^preview$/i }).click();
  await previewResponse;
  for (const label of ['Correct', 'Near miss', 'Incorrect', 'Retry', 'Timeout', 'Brave try', 'Completion']) {
    await page.getByRole('button', { name: label, exact: true }).click();
    await expect(page.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-pressed', 'true');
  }

  const publishResponse = page.waitForResponse((response) => response.url().endsWith(`/lessons/${fixture.lesson.id}/publish`) && response.status() === 200);
  await page.getByRole('button', { name: /^publish$/i }).click();
  await page.getByRole('button', { name: /ok|confirm/i }).last().click();
  const published = (await (await publishResponse).json()).data;
  await expect(page.locator('.el-alert__title').filter({ hasText: `Published v${published.lessonVersion}` })).toBeVisible();

  const original = await api(page, 'GET', `/lessons/${fixture.lesson.id}`);
  expect(original.status).toBe('published');
  expect(original.manifest_checksum || original.manifestChecksum).toBe(published.checksum);
  const nextDraft = await api(page, 'POST', `/lessons/${fixture.lesson.id}/new-version`);
  const [nextStep] = await api(page, 'GET', `/lessons/${nextDraft.id}/steps`);
  await api(page, 'PATCH', `/lessons/${nextDraft.id}/steps/${encodeURIComponent(nextStep.step_key || nextStep.stepKey)}`, {
    prompt: 'A new draft must not mutate published v1.',
    subject: nextStep.subject,
    stepBody: nextStep.step_body || nextStep.stepBody,
  });
  const originalAfterDraftEdit = await api(page, 'GET', `/lessons/${fixture.lesson.id}`);
  expect(originalAfterDraftEdit.manifest_checksum || originalAfterDraftEdit.manifestChecksum).toBe(published.checksum);
});
