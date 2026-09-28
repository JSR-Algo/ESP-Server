const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor, managerUser } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');

test('real manager and Nest author authentication unlock Lesson Studio', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  let authorAuthenticated = false;
  const unexpectedHttpErrors = [];
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const expectedAuthorChallenge = !authorAuthenticated
      && response.status() === 401
      && response.url().includes('/nestjs/v1/admin/');
    if (!expectedAuthorChallenge) unexpectedHttpErrors.push(`${response.status()} ${response.url()}`);
  });

  const courseQuality = page.waitForResponse(response =>
    response.request().method() === 'GET'
      && new URL(response.url()).pathname === '/nestjs/v1/admin/course-insights/course-quality');
  await loginAsLessonAuthor(page);
  authorAuthenticated = true;
  // The heading can render before the quality request body has finished.
  const qualityResponse = await courseQuality;
  expect(qualityResponse.status()).toBe(200);
  expect(await qualityResponse.finished()).toBeNull();
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  expect(page.url()).not.toContain('token=');
  expect(unexpectedHttpErrors).toEqual([]);
  assertNoUnexpectedPageErrors();
});
