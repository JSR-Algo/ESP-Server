const { expect } = require('@playwright/test');
const { resetLessonStudioE2EState } = require('../../../scripts/reset-lesson-studio-e2e-state.cjs');

const managerUser = process.env.LESSON_STUDIO_E2E_MANAGER_USER || 'lesson_admin_e2e';
const managerPassword = process.env.LESSON_STUDIO_E2E_MANAGER_PASSWORD || 'TbotE2E!2026';
const captcha = process.env.LESSON_STUDIO_E2E_CAPTCHA || 'E2E42';
const authorEmail = process.env.LESSON_STUDIO_E2E_AUTHOR_EMAIL || 'lesson-author-e2e@local.invalid';
const authorPassword = process.env.LESSON_STUDIO_E2E_AUTHOR_PASSWORD || 'TbotAuthorE2E!2026';

async function loginAsLessonAuthor(page) {
  resetLessonStudioE2EState();
  await page.goto('/login');
  await expect(page.getByRole('img', { name: 'Verification code' })).toHaveAttribute('src', /^blob:/);
  await expect.poll(() => page.evaluate(() => {
    const config = JSON.parse(localStorage.getItem('pubConfig') || '{}');
    return Boolean(config.sm2PublicKey);
  })).toBe(true);
  await page.getByTestId('manager-login-username').fill(managerUser);
  await page.getByTestId('manager-login-password').fill(managerPassword);
  await page.getByTestId('manager-login-captcha').fill(captcha);

  const managerLogin = page.waitForResponse((response) =>
    response.url().includes('/tbot/user/login') && response.request().method() === 'POST');
  await page.getByTestId('manager-login-submit').click();
  const managerResponse = await managerLogin;
  expect(managerResponse.status()).toBe(200);
  expect((await managerResponse.json()).code).toBe(0);
  await expect(page.getByText(managerUser)).toBeVisible();
  await page.waitForURL(/#\/home$/);

  // The web container may start milliseconds before Docker DNS publishes the
  // backend alias. Verify the same-origin proxy has recovered before opening
  // the Author dialog, otherwise a transient 502 looks like bad credentials.
  // nginx guards /nestjs/ with auth_request against the manager bearer, and the
  // manager token lives in localStorage — so probe from inside the page with the
  // same header axios sends. `page.request` carries cookies only and always 401s.
  await expect.poll(async () => page.evaluate(async () => {
    const stored = JSON.parse(localStorage.getItem('token') || 'null');
    const response = await fetch('/nestjs/v1/health', {
      headers: stored && stored.token ? { Authorization: `Bearer ${stored.token}` } : {},
    });
    return response.status;
  })).toBe(200);

  await page.goto('/login#/course-management');

  const authorDialog = page.getByRole('dialog', { name: /sign in as author/i });
  if (!await authorDialog.isVisible().catch(() => false)) {
    // A very fast 401 can precede App.vue's event-listener mount. Re-emit the
    // same UI event so authentication remains a real dialog flow, not token injection.
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('tbot:nest-auth-required')));
  }
  await expect(authorDialog).toBeVisible();
  await authorDialog.getByText('Email').locator('..').getByRole('textbox').fill(authorEmail);
  await authorDialog.locator('input[type="password"]').fill(authorPassword);
  const authorLogin = page.waitForResponse((response) =>
    response.url().includes('/nestjs/v1/admin/auth/login') && response.request().method() === 'POST');
  await authorDialog.getByRole('button', { name: /author sign-in/i }).click();
  expect((await authorLogin).status()).toBe(200);
  await expect(authorDialog).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
}

module.exports = { loginAsLessonAuthor, managerUser };
