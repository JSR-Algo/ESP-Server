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

  await loginAsLessonAuthor(page);
  authorAuthenticated = true;
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  expect(page.url()).not.toContain('token=');
  expect(unexpectedHttpErrors).toEqual([]);
  assertNoUnexpectedPageErrors();
});
