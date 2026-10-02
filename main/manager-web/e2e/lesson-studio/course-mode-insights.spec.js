const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { gotoAppRoute } = require('./helpers/navigation');
const { adminApi, createCourseModeDraft } = require('./helpers/admin-api');

test('surfaces Course Insights and lifecycle history through the real admin session', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  const fixture = await createCourseModeDraft(page);

  const learners = await adminApi(page, 'GET', '/course-insights/learners?limit=20');
  expect(learners).toEqual(expect.objectContaining({ learners: expect.any(Array) }));
  const quality = await adminApi(page, 'GET', `/course-insights/course-quality?windowDays=30&courseId=${fixture.course.id}`);
  expect(quality).toEqual(expect.objectContaining({ courses: expect.any(Array) }));

  await gotoAppRoute(page, '#/course-insights');
  await expect(page.getByRole('heading', { name: /learner & quality/i })).toBeVisible();
  await expect(page.getByRole('tab', { name: /child personality/i })).toBeVisible();
  await expect(page.getByRole('tab', { name: /course quality/i })).toBeVisible();
  await gotoAppRoute(page, '#/course-management');
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  await expect(page.locator('body')).toContainText(fixture.course.title);
  assertNoUnexpectedPageErrors();
});

// Controlled response ordering after real login. These cases prove UI isolation;
// the real-service case above remains a separate authentication/API gate.
test('keeps B preview and draft when A preview and save finish late', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const child = id => ({ childId: id, childName: `FE01 ${id}`, personality: { interests: [] }, stats: {} });
  const pending = {};
  await page.route('**/course-insights/learners?*', route => route.fulfill({ json: { learners: [child('A'), child('B')] } }));
  await page.route('**/course-insights/learners/*/lesson-preview?*', route => {
    pending[route.request().url().includes('/A/') ? 'previewA' : 'previewB'] = route;
  });
  await page.route('**/course-insights/learners/A/personality', route => { pending.saveA = route; });
  await gotoAppRoute(page, '#/course-insights');
  await expect.poll(() => Boolean(pending.previewA)).toBe(true);
  await page.getByRole('button', { name: 'Save personality', exact: true }).click();
  await expect.poll(() => Boolean(pending.saveA)).toBe(true);
  await page.locator('.learner-list .el-table__body-wrapper').getByText('FE01 B', { exact: true }).click();
  const draft = page.locator('.personality-form input').first();
  await draft.fill('B unsaved interests');
  await expect.poll(() => Boolean(pending.previewB)).toBe(true);
  await pending.previewB.fulfill({ json: { lessons: [{ lessonId: 'B-only', title: 'B recommendation' }] } });
  await expect(page.locator('.preview-table')).toContainText('B recommendation');
  await pending.previewA.fulfill({ json: { lessons: [{ lessonId: 'A-only', title: 'A recommendation' }] } });
  await pending.saveA.fulfill({ json: { learner: child('A') } });
  await expect(page.locator('.el-message--success').filter({ hasText: 'Personality saved:' })).toContainText('FE01 A');
  await expect(page.locator('.detail-head h3')).toHaveText('FE01 B');
  await expect(draft).toHaveValue('B unsaved interests');
  await expect(page.locator('.preview-table')).toContainText('B recommendation');
  await expect(page.locator('.preview-table')).not.toContainText('A recommendation');
});

test('quality refresh distinguishes empty from unavailable and ignores old errors', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const requests = [];
  await page.route('**/course-insights/course-quality?*', route => { requests.push(route); });
  await gotoAppRoute(page, '#/course-insights?tab=quality&courseId=FE01-A');
  await expect.poll(() => requests.length).toBe(1);
  await gotoAppRoute(page, '#/course-insights?tab=quality&courseId=FE01-B');
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1].request().url()).toContain('courseId=FE01-B');
  await requests[0].fulfill({ status: 503, json: { message: 'FE01 obsolete error' } });
  await expect(page.locator('.main-wrapper .el-loading-mask')).toBeVisible();
  await expect(page.getByText('FE01 obsolete error', { exact: true })).toHaveCount(0);
  await requests[1].fulfill({ json: { courses: [] } });
  await expect(page.locator('.main-wrapper .el-loading-mask')).toBeHidden();
  await expect(page.locator('.main-wrapper')).not.toContainText('Failed to load course quality');
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect.poll(() => requests.length).toBe(3);
  await requests[2].fulfill({ status: 503, json: { message: 'FE01 unavailable' } });
  await expect(page.locator('.main-wrapper')).toContainText('Failed to load course quality');
  await expect(page.locator('.quality-stats strong').first()).toHaveText('-');
});

test('persists learner personality through real auth, API and database across reload', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  await gotoAppRoute(page, '#/course-insights?keyword=FE01-live');
  const rows = page.locator('.learner-list .el-table__body-wrapper');
  await expect(rows.getByText('FE01-live A', { exact: true })).toBeVisible();
  await rows.getByText('FE01-live A', { exact: true }).click();
  await expect(page.locator('.detail-head h3')).toHaveText('FE01-live A');
  const interests = page.locator('.personality-form input').first();
  const savedInterest = `fe01-${Date.now()}`;
  await interests.fill(savedInterest);
  const saveResponse = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().includes('/personality'));
  await page.getByRole('button', { name: 'Save personality', exact: true }).click();
  const saved = await saveResponse;
  expect(saved.status()).toBe(200);
  await expect(page.locator('.el-message--success').filter({ hasText: 'Personality saved:' })).toContainText('FE01-live A');
  const payload = await saved.json();
  expect(payload.data.learner.childName).toBe('FE01-live A');
  expect(payload.data.learner.personality.interests).toEqual([savedInterest]);
  await rows.getByText('FE01-live B', { exact: true }).click();
  await interests.fill('B unsaved draft');
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(rows.getByText('FE01-live B', { exact: true })).toBeVisible();
  await expect(page.locator('.detail-head h3')).toHaveText('FE01-live B');
  await expect(interests).toHaveValue('B unsaved draft');
  const readback = await adminApi(page, 'GET', '/course-insights/learners?keyword=FE01-live&limit=20');
  expect(readback.learners.find(r => r.childName === 'FE01-live A').personality.interests).toEqual([savedInterest]);
  expect(readback.learners.find(r => r.childName === 'FE01-live B').personality.interests).not.toContain('B unsaved draft');
  await page.reload();
  await expect(rows.getByText('FE01-live A', { exact: true })).toBeVisible();
  await rows.getByText('FE01-live A', { exact: true }).click();
  await expect(interests).toHaveValue(savedInterest);
  await expect(page.locator('.preview-table .el-loading-mask')).toBeHidden();
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  assertNoUnexpectedPageErrors();
});
