const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { adminApi } = require('./helpers/admin-api');
const { verifiedCatalogImage, copyVisualVersion, createLegacyFixtureCourse, bindLegacyVisuals } = require('./helpers/legacy-fixtures');


const api = (page, method, path, data) => adminApi(page, method, path, data);

// The lesson-wide visuals command binds the pair on every step, so `usages`
// carries one row per (lesson, step) — the UI dedupes them before display.
// Assert on lessons, not on an incidental step count.
const usageLessonIds = (detail) => [...new Set(detail.usages.map((usage) => usage.lessonId))];

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
  await option.click();
  await expect(select.locator('input')).toHaveValue(expectedValue);
}

test('admin manages disposable shared visuals across clone, selected, global, and published versions', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  test.setTimeout(120_000);
  const runId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const assetKey = `e2e.visual.${runId}`;

  await loginAsLessonAuthor(page);
  const sourceImage = await verifiedCatalogImage(page, { category: 'teachingObject', profile: 'espTft' });
  const targetImage = await verifiedCatalogImage(page, { category: 'teachingObject', profile: 'espTft', excludeSha256: sourceImage.sha256 });
  const mobileImage = await verifiedCatalogImage(page, { category: 'teachingObject', profile: 'mobile' });
  const background = await verifiedCatalogImage(page, { category: 'scene', profile: 'espTft' });
  const robot = await verifiedCatalogImage(page, { category: 'robotPose', profile: 'espTft' });
  const source = await copyVisualVersion(page, assetKey, sourceImage);
  const target = await copyVisualVersion(page, assetKey, targetImage);
  await copyVisualVersion(page, assetKey, mobileImage);

  const fixtures = {};
  for (const name of ['clone', 'selected', 'global', 'published']) {
    const fixture = await createLegacyFixtureCourse(page, `e2e-visual-${name}-${runId}`);
    await bindLegacyVisuals(page, fixture.lesson.id, background.versionId, source.id);
    for (const step of fixture.steps) {
      await api(page, 'PUT', `/lessons/${fixture.lesson.id}/steps/${encodeURIComponent(step.step_key)}/visual-refs/robotOverlay`, { assetVersionId: robot.versionId });
    }
    fixtures[name] = fixture;
  }
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

  await expect(page.getByTestId('visual-detail-facts')).toContainText(`${mobileImage.width} × ${mobileImage.height}`);
  await expect(page.getByTestId('visual-detail-comparison')).toContainText(`${targetImage.width} × ${targetImage.height}`);
  await chooseSingleSelect(page, 'visual-detail-source-version', /^v1 · espTft · published$/, 'v1 · espTft · published');
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  await expect(page.getByTestId('visual-detail-usage-table').getByRole('row')).toHaveCount(5);
  await page.getByTestId('visual-detail-target-version').click();
  await expect(page.locator('.el-select-dropdown__item:visible').filter({ hasText: /mobile/ })).toHaveCount(0);
  await visibleOption(page, /^v2 · espTft · published$/).last().click();
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

  // Replacement callbacks reload the original visual after the POST body resolves.
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  await page.reload();
  await expect(page.getByRole('heading', { name: assetKey })).toBeVisible();
  await chooseSingleSelect(page, 'visual-detail-source-version', /^v1 · espTft · published$/, 'v1 · espTft · published');
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
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

  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  await page.reload();
  await chooseSingleSelect(page, 'visual-detail-source-version', /^v1 · espTft · published$/, 'v1 · espTft · published');
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
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
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  assertNoUnexpectedPageErrors();
});
