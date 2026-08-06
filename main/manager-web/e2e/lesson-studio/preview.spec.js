const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { nestFetch } = require('./helpers/admin-api');

const nestApi = (page, path, options) => nestFetch(page, path, options);

test('real admin previews the exact espTft scene and all response paths', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  await loginAsLessonAuthor(page);

  const sourceLessonId = '00000006-0002-0000-0000-000000000001';
  const sourceCourseId = '00000006-0001-0000-0000-000000000001';
  const existing = await nestApi(page, `/courses/${sourceCourseId}/lessons`);
  for (const row of existing.filter((item) => item.lesson_key === 'w01-d01-barn-say-it' && item.status === 'draft')) {
    await nestApi(page, `/lessons/${row.id}`, { method: 'DELETE' });
  }
  const lesson = await nestApi(page, `/lessons/${sourceLessonId}/new-version`, { method: 'POST' });
  await nestApi(page, `/lessons/${lesson.id}`, { method: 'PATCH', body: { title: `Preview lesson ${runId}` } });
  const [step] = await nestApi(page, `/lessons/${lesson.id}/steps`);

  const visuals = [
    { slot: 'backgroundScene', category: 'scene', assetKey: `e2e.${runId}.background`, sha256: 'a'.repeat(64) },
    { slot: 'teachingObject', category: 'teachingObject', assetKey: `e2e.${runId}.object`, sha256: 'b'.repeat(64) },
    { slot: 'robotOverlay', category: 'robotPose', assetKey: `e2e.${runId}.robot`, sha256: 'c'.repeat(64) },
  ];
  const versionIdBySlot = {};
  for (const visual of visuals) {
    const version = await nestApi(page, `/lesson-visual-assets/${visual.assetKey}/versions`, {
      method: 'POST',
      body: {
        category: visual.category,
        title: `E2E ${visual.slot} ${runId}`,
        profile: 'espTft',
        storagePath: 'http://127.0.0.1:8102/favicon.ico',
        sha256: visual.sha256,
        mimeType: 'image/png',
        bytes: 5430,
        width: 64,
        height: 64,
        publicationState: 'published',
      },
    });
    versionIdBySlot[visual.slot] = version.id;
  }

  // Only robotOverlay is a per-step slot (PER_STEP_VISUAL_SLOTS); background and
  // teaching object are lesson-wide and must go through the visuals command.
  await nestApi(page, `/lessons/${lesson.id}/visuals`, {
    method: 'PUT',
    body: {
      backgroundAssetVersionId: versionIdBySlot.backgroundScene,
      objectAssetVersionId: versionIdBySlot.teachingObject,
    },
  });
  await nestApi(page, `/lessons/${lesson.id}/steps/${encodeURIComponent(step.step_key)}/visual-refs/robotOverlay`, {
    method: 'PUT', body: { assetVersionId: versionIdBySlot.robotOverlay },
  });

  await nestApi(page, `/lessons/${lesson.id}/steps/${encodeURIComponent(step.step_key)}`, {
    method: 'PATCH',
    body: {
      prompt: step.prompt,
      subject: step.subject,
      stepBody: {
        durationPreset: 3,
        teachingWord: { text: 'MOON', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord' },
        interaction: { template: 'safeSpeaking', maxAttempts: 3, listenTimeoutSec: 6, correctThreshold: 0.85, braveTryThreshold: 0.7, funPattern: 'copyMyMove' },
        motion: { present: 'teach', listen: 'listen', correct: 'goodbye', nearMiss: 'thinking', incorrect: 'presentRight' },
        storyBeat: { goal: 'Help TeeBot find the moon.', successReaction: 'Celebrate the moon.', nextTease: 'What shines next?' },
      },
    },
  });

  // Register before navigating: the editor auto-generates the espTft preview as
  // soon as steps load, so the 200 can land before any click.
  const previewResponse = page.waitForResponse((response) =>
    response.url().includes(`/lessons/${lesson.id}/manifest-preview`) && response.status() === 200);
  await page.goto(`/login#/lesson-editor?lessonId=${lesson.id}`);
  await expect(page.getByRole('heading', { name: `Preview lesson ${runId}` })).toBeVisible();
  // "Generate preview" lives in the empty state (v-else on previewManifest), so
  // it is absent once the auto-preview has succeeded. Click it only if shown.
  const generatePreview = page.getByRole('button', { name: 'Generate preview' });
  if (await generatePreview.isVisible().catch(() => false)) await generatePreview.click();
  await previewResponse;

  const stage = page.getByTestId('esp-tft-stage');
  await expect(stage).toBeVisible();
  expect(await stage.evaluate((element) => ({ width: element.clientWidth, height: element.clientHeight }))).toEqual({ width: 480, height: 320 });
  await expect(stage.locator('.word-pill')).toHaveText('MOON');
  await expect(stage.locator('img.layer-background')).toBeVisible();
  await expect(stage.locator('img.layer-teachingObject')).toBeVisible();
  await expect(stage.locator('img.layer-robotOverlay')).toBeVisible();

  const expectations = [
    ['Correct', 'Slave command: goodbye'],
    ['Near miss', 'Slave command: thinking'],
    ['Incorrect', 'Slave command: presentRight'],
    ['Retry', 'Slave command: presentRight'],
    ['Timeout', 'Slave command: listen'],
    ['Brave try', 'Slave command: thinking'],
    ['Completion', 'Slave command: goodbye'],
    // silence resolves through PATH_STATE to the 'listen' visual state, and this
    // step authors motion.listen, so the authored value wins over DEFAULT_PATHS'
    // patient-wait. sttUnavailable resolves to 'teach', which the authored map
    // does not key (it authors `present`), so its calm-idle default stands.
    ['Silence', 'Slave command: listen'],
    ['STT unavailable', 'Slave command: calm-idle'],
    ['Missing visual', 'Slave command: teach'],
  ];
  // Scope to the response-path toolbar: labels like "Correct" also name
  // LessonSimulationPanel preset buttons, so an unscoped lookup is ambiguous.
  const responsePaths = page.getByLabel('Response paths');
  for (const [label, command] of expectations) {
    await responsePaths.getByRole('button', { name: label, exact: true }).click();
    await expect(page.getByRole('list', { name: 'Robot command timeline' })).toContainText(command);
    await expect(responsePaths.getByRole('button', { name: label, exact: true }))
      .toHaveAttribute('aria-pressed', 'true');
  }
  await expect(stage.locator('.missing-visual')).toContainText('MOON');
  await expect(stage.locator('img.layer-teachingObject')).toHaveCount(0);
  assertNoUnexpectedPageErrors();
});
