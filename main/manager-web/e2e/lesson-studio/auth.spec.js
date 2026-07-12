const { test, expect } = require('@playwright/test');

const managerUser = process.env.LESSON_STUDIO_E2E_MANAGER_USER || 'lesson_admin_e2e';
const managerPassword = process.env.LESSON_STUDIO_E2E_MANAGER_PASSWORD || 'TbotE2E!2026';
const captcha = process.env.LESSON_STUDIO_E2E_CAPTCHA || 'TB0T1';
const authorEmail = process.env.LESSON_STUDIO_E2E_AUTHOR_EMAIL || 'lesson-author-e2e@local.invalid';
const authorPassword = process.env.LESSON_STUDIO_E2E_AUTHOR_PASSWORD || 'TbotAuthorE2E!2026';

test('real manager and Nest author authentication unlock Lesson Studio', async ({ page }) => {
  let authorAuthenticated = false;
  const unexpectedHttpErrors = [];
  page.on('response', (response) => {
    if (response.status() < 400) return;
    const expectedAuthorChallenge = !authorAuthenticated
      && response.status() === 401
      && response.url().includes('/nestjs/v1/admin/');
    if (!expectedAuthorChallenge) unexpectedHttpErrors.push(`${response.status()} ${response.url()}`);
  });

  await page.goto('/login');
  await page.getByTestId('manager-login-username').fill(managerUser);
  await page.getByTestId('manager-login-password').fill(managerPassword);
  await page.getByTestId('manager-login-captcha').fill(captcha);

  const managerLogin = page.waitForResponse((response) =>
    response.url().includes('/tbot/user/login') && response.request().method() === 'POST');
  await page.getByTestId('manager-login-submit').click();
  expect((await managerLogin).status()).toBe(200);
  await expect(page.getByText(managerUser)).toBeVisible();

  const capabilityRequest = page.waitForResponse((response) =>
    response.url().includes('/nestjs/v1/admin/lesson-rollout-capabilities'));
  await page.goto('/login#/course-management');
  expect((await capabilityRequest).status()).toBe(401);

  const authorDialog = page.getByRole('dialog', { name: /sign in as author/i });
  await expect(authorDialog).toBeVisible();
  await authorDialog.getByText('Email').locator('..').getByRole('textbox').fill(authorEmail);
  await authorDialog.locator('input[type="password"]').fill(authorPassword);

  const authorLogin = page.waitForResponse((response) =>
    response.url().includes('/nestjs/v1/admin/auth/login') && response.request().method() === 'POST');
  await authorDialog.getByRole('button', { name: /author sign-in/i }).click();
  expect((await authorLogin).status()).toBe(200);
  authorAuthenticated = true;

  await expect(authorDialog).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  expect(page.url()).not.toContain('token=');
  expect(unexpectedHttpErrors).toEqual([]);
});
