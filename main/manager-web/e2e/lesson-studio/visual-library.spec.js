const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { adminApi, adminAuthHeaders, apiRoot } = require('./helpers/admin-api');


const api = (page, method, path, data) => adminApi(page, method, path, data);

// The lesson-wide visuals command binds the pair on every step, so `usages`
// carries one row per (lesson, step) — the UI dedupes them before display.
// Assert on lessons, not on an incidental step count.
const usageLessonIds = (detail) => [...new Set(detail.usages.map((usage) => usage.lessonId))];

async function createVisualVersion(page, assetKey, version) {
  return api(page, 'POST', `/lesson-visual-assets/${encodeURIComponent(assetKey)}/versions`, {
    category: version.category || 'teachingObject',
    title: `Disposable ${assetKey}`,
    profile: version.profile,
    storagePath: `fixture://lesson-studio-e2e/${assetKey}/${version.name}.png`,
    sha256: version.sha,
    mimeType: 'image/png',
    bytes: version.bytes,
    width: version.width,
    height: version.height,
    publicationState: 'published',
  });
}

async function cloneFixtureCourse(page, sourceCourseId, courseKey, sourceVersionId) {
  const course = await api(page, 'POST', `/courses/${sourceCourseId}/clone`, {
    courseKey,
    title: `Disposable visual course ${courseKey}`,
  });
  const lessons = await api(page, 'GET', `/courses/${course.id}/lessons`);
  expect(lessons).toHaveLength(1);
  const lesson = lessons[0];
  const steps = await api(page, 'GET', `/lessons/${lesson.id}/steps`);
  expect(steps.length).toBeGreaterThan(0);
  const stepKey = steps[0].step_key || steps[0].stepKey;
  // Background + teaching object are lesson-wide, not per-step: the only
  // per-step slot left is robotOverlay (PER_STEP_VISUAL_SLOTS in the backend),
  // so PUT .../visual-refs/teachingObject now 400s. Bind through the lesson-wide
  // visuals command, which requires the pair.
  const background = await createVisualVersion(page, `e2e-visual-bg-${courseKey}`, {
    category: 'scene',
    profile: 'espTft',
    name: 'bg-v1',
    sha: 'c'.repeat(64),
    bytes: 2048,
    // espTft caps a decoded backgroundScene at 153600 bytes (w*h*2), so a full
    // 480x320 poster is rejected by the budget guard.
    width: 480,
    height: 160,
  });
  await api(page, 'PUT', `/lessons/${lesson.id}/visuals`, {
    backgroundAssetVersionId: background.versionId || background.version_id || background.id,
    objectAssetVersionId: sourceVersionId,
  });
  return { course, lesson, stepKey, steps };
}

async function makePublishable(page, fixture) {
  await api(page, 'PATCH', `/lessons/${fixture.lesson.id}`, {
    estimatedDurationSec: 180,
    durationPreset: 3,
  });
  for (const [index, step] of fixture.steps.entries()) {
    const stepKey = step.step_key || step.stepKey;
    const stepBody = { ...(step.step_body || step.stepBody || {}), timeoutSec: 5 };
    if (stepBody.storyBeat) stepBody.storyBeat = {
      ...stepBody.storyBeat,
      goal: 'Find the barn together.',
      successReaction: 'Celebrate finding the barn.',
      nextTease: 'What will we find next?',
    };
    if (index === 0) stepBody.teachingWord = {
      text: 'BARN', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord',
    };
    if (index === fixture.steps.length - 1) stepBody.terminal = true;
    await api(page, 'PATCH', `/lessons/${fixture.lesson.id}/steps/${encodeURIComponent(stepKey)}`, {
      prompt: step.prompt,
      subject: step.subject,
      stepBody,
    });
  }
}

async function visualDetail(page, assetKey, sourceVersionId) {
  return api(page, 'GET', `/lesson-visual-assets/${encodeURIComponent(assetKey)}?sourceVersionId=${sourceVersionId}`);
}

function visibleOption(page, text) {
  return page.locator('.el-select-dropdown__item:visible').filter({ hasText: text });
}

async function chooseSingleSelect(page, testId, optionText, expectedValue) {
  const select = page.getByTestId(testId);
  await select.click();
  const option = visibleOption(page, optionText).last();
  await expect(option).toBeVisible();
  await option.evaluate((element) => element.click());
  await expect(select.locator('input')).toHaveValue(expectedValue);
}

test('admin manages disposable shared visuals across clone, selected, global, and published versions', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  test.setTimeout(120_000);
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const assetKey = `e2e.visual.${runId}`;

  try {
    await loginAsLessonAuthor(page);
  } catch (error) {
    const token = await page.evaluate(() => localStorage.getItem('nestjs_session_token'));
    if (!token) throw error;
  }

  const courses = await api(page, 'GET', '/courses?kind=all');
  const sourceCourse = courses.find((course) => (course.course_key || course.courseKey) === 'w01-place-words');
  expect(sourceCourse, 'canonical local lesson fixture must exist').toBeTruthy();

  const source = await createVisualVersion(page, assetKey, { name: 'source', profile: 'espTft', sha: '1'.repeat(64), bytes: 4096, width: 160, height: 120 });
  const target = await createVisualVersion(page, assetKey, { name: 'target', profile: 'espTft', sha: '2'.repeat(64), bytes: 4352, width: 160, height: 120 });
  await createVisualVersion(page, assetKey, { name: 'mobile', profile: 'mobile', sha: '3'.repeat(64), bytes: 8192, width: 640, height: 480 });

  const fixtures = {};
  for (const name of ['clone', 'selected', 'global', 'published']) {
    fixtures[name] = await cloneFixtureCourse(page, sourceCourse.id, `e2e-visual-${name}-${runId}`, source.id);
  }
  await makePublishable(page, fixtures.published);
  await api(page, 'POST', `/lessons/${fixtures.published.lesson.id}/publish`);

  await page.goto('/login#/lesson-visual-library');
  await expect(page.getByRole('heading', { name: 'Shared visual library' })).toBeVisible();
  await page.getByTestId('visual-library-search').locator('input').fill(assetKey);
  await expect(page.getByTestId('visual-library-table')).toContainText(assetKey);
  await page.getByTestId('visual-library-category').getByRole('textbox').click();
  await visibleOption(page, /^teachingObject$/).click();
  await page.getByTestId('visual-library-profile').getByRole('textbox').click();
  await visibleOption(page, /^espTft$/).click();
  await expect(page.getByTestId('visual-library-table')).toContainText('espTft');
  await page.getByTestId(`visual-library-inspect-${assetKey}`).click();

  await expect(page.getByTestId('visual-detail-facts')).toContainText('640 × 480');
  await expect(page.getByTestId('visual-detail-comparison')).toContainText('160 × 120');
  await chooseSingleSelect(page, 'visual-detail-source-version', /^v1 · espTft · published$/, 'v1 · espTft · published');
  await expect(page.getByTestId('visual-detail-usage-table').getByRole('row')).toHaveCount(5);
  await page.getByTestId('visual-detail-target-version').click();
  await expect(page.locator('.el-select-dropdown__item:visible').filter({ hasText: /mobile/ })).toHaveCount(0);
  await visibleOption(page, /^v2 · espTft · published$/).last().evaluate((element) => element.click());
  await expect(page.getByTestId('visual-detail-target-version').locator('input')).toHaveValue('v2 · espTft · published');

  await page.getByTestId('visual-detail-replacement-mode').getByText('cloneForLesson').click();
  await page.getByTestId('visual-detail-lessons').click();
  await visibleOption(page, new RegExp(`e2e-visual-clone-${runId}`)).click();
  const cloneResponse = page.waitForResponse((response) => response.url().endsWith('/lesson-visual-assets/replacements') && response.request().method() === 'POST');
  await page.getByTestId('visual-detail-review-replacement').click();
  const cloneResult = (await (await cloneResponse).json()).data;
  expect(cloneResult.clonedAssetKey).toMatch(/^clone\./);
  const clonedDetail = await visualDetail(page, cloneResult.clonedAssetKey, cloneResult.clonedVersionId);
  expect(usageLessonIds(clonedDetail)).toEqual([fixtures.clone.lesson.id]);

  await page.reload();
  await expect(page.getByRole('heading', { name: assetKey })).toBeVisible();
  await chooseSingleSelect(page, 'visual-detail-source-version', /^v1 · espTft · published$/, 'v1 · espTft · published');
  await chooseSingleSelect(page, 'visual-detail-target-version', /^v2 · espTft · published$/, 'v2 · espTft · published');
  await page.getByTestId('visual-detail-replacement-mode').getByText('selectedLessons').click();
  await page.getByTestId('visual-detail-lessons').click();
  await visibleOption(page, new RegExp(`e2e-visual-selected-${runId}`)).click();
  await visibleOption(page, new RegExp(`e2e-visual-published-${runId}`)).click();
  await page.keyboard.press('Escape');
  await page.getByTestId('visual-detail-review-replacement').click();
  await expect(page.getByTestId('visual-impact-dialog')).toBeVisible();
  const selectedResponse = page.waitForResponse((response) => response.url().endsWith('/lesson-visual-assets/replacements') && response.request().method() === 'POST');
  await page.getByTestId('visual-impact-confirm').click();
  const selectedResult = (await (await selectedResponse).json()).data;
  expect(selectedResult.branchedLessonIds).toHaveLength(1);
  let sourceDetail = await visualDetail(page, assetKey, source.id);
  expect(usageLessonIds(sourceDetail)).toContain(fixtures.published.lesson.id);
  let targetDetail = await visualDetail(page, assetKey, target.id);
  expect(usageLessonIds(targetDetail)).toEqual(expect.arrayContaining([fixtures.selected.lesson.id, selectedResult.branchedLessonIds[0]]));

  await page.reload();
  await chooseSingleSelect(page, 'visual-detail-source-version', /^v1 · espTft · published$/, 'v1 · espTft · published');
  await chooseSingleSelect(page, 'visual-detail-target-version', /^v2 · espTft · published$/, 'v2 · espTft · published');
  await page.getByTestId('visual-detail-replacement-mode').getByText('global').click();
  await page.getByTestId('visual-detail-review-replacement').click();
  await expect(page.getByTestId('visual-impact-dialog')).toBeVisible();
  const globalResponse = page.waitForResponse((response) => response.url().endsWith('/lesson-visual-assets/replacements') && response.request().method() === 'POST');
  await page.getByTestId('visual-impact-confirm').click();
  const globalResult = (await (await globalResponse).json()).data;
  expect(globalResult.updateDraftLessonIds).toContain(fixtures.global.lesson.id);
  expect(globalResult.branchedLessonIds).toHaveLength(1);
  sourceDetail = await visualDetail(page, assetKey, source.id);
  expect(usageLessonIds(sourceDetail)).toEqual([fixtures.published.lesson.id]);
  targetDetail = await visualDetail(page, assetKey, target.id);
  expect(usageLessonIds(targetDetail)).toEqual(expect.arrayContaining([
    fixtures.selected.lesson.id,
    fixtures.global.lesson.id,
    selectedResult.branchedLessonIds[0],
    globalResult.branchedLessonIds[0],
  ]));
  assertNoUnexpectedPageErrors();
});
