const { expect } = require('@playwright/test');
const { existsSync, readFileSync } = require('node:fs');
const { get: httpsGet } = require('node:https');
const { resolve } = require('node:path');
const { resetLessonStudioE2EState } = require('../../../scripts/reset-lesson-studio-e2e-state.cjs');

const managerUser = process.env.LESSON_STUDIO_E2E_MANAGER_USER || 'lesson_admin_e2e';
const managerPassword = process.env.LESSON_STUDIO_E2E_MANAGER_PASSWORD || 'TbotE2E!2026';
const captcha = process.env.LESSON_STUDIO_E2E_CAPTCHA || 'E2E42';
const authorEmail = process.env.LESSON_STUDIO_E2E_AUTHOR_EMAIL || 'lesson-author-e2e@local.invalid';
const authorPassword = process.env.LESSON_STUDIO_E2E_AUTHOR_PASSWORD || 'TbotAuthorE2E!2026';

async function installTrustedTask4MediaRoute(page) {
  const tlsRoot = process.env.TASK4_ASSIGNMENT_TLS_ROOT
    || (process.env.TASK4_ASSIGNMENT_RUNTIME_ROOT
      ? resolve(process.env.TASK4_ASSIGNMENT_RUNTIME_ROOT, 'tls') : null);
  const certificate = tlsRoot ? resolve(tlsRoot, 'cert.pem') : null;
  if (!certificate || !existsSync(certificate)) return;
  const ca = readFileSync(certificate);
  await page.route(/^https:\/\/task4-media\.localhost:\d+\/(?:tvideo-demo\/|flattened-cinematic\/)/, async (route) => {
    const reachable = new URL(route.request().url());
    reachable.hostname = '127.0.0.1';
    const response = await new Promise((resolveResponse, reject) => {
      const request = httpsGet(reachable, {
        ca,
        servername: 'task4-media.localhost',
        headers: route.request().headers(),
      }, (incoming) => {
        const chunks = [];
        incoming.on('data', (chunk) => chunks.push(chunk));
        incoming.on('end', () => resolveResponse({
          status: incoming.statusCode,
          headers: incoming.headers,
          body: Buffer.concat(chunks),
        }));
      });
      request.on('error', reject);
    });
    await route.fulfill(response);
  });
}

async function loginAsLessonAuthor(page, credentials = {}) {
  const selectedAuthorEmail = credentials.authorEmail || authorEmail;
  const selectedAuthorPassword = credentials.authorPassword || authorPassword;
  await installTrustedTask4MediaRoute(page);
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

  require('./real-service-evidence').expectObservedFault(page, 'GET', '/nestjs/v1/admin/courses', 401, 'real author sign-in challenge');
  await page.goto('/login#/course-management');

  const authorDialog = page.getByRole('dialog', { name: /sign in as author/i });
  if (!await authorDialog.isVisible().catch(() => false)) {
    // A very fast 401 can precede App.vue's event-listener mount. Re-emit the
    // same UI event so authentication remains a real dialog flow, not token injection.
    await page.evaluate(() => window.dispatchEvent(new CustomEvent('tbot:nest-auth-required')));
  }
  await expect(authorDialog).toBeVisible();
  await authorDialog.getByText('Email').locator('..').getByRole('textbox').fill(selectedAuthorEmail);
  await authorDialog.locator('input[type="password"]').fill(selectedAuthorPassword);
  const [authorLogin] = await Promise.all([
    page.waitForResponse((response) =>
      response.url().includes('/nestjs/v1/admin/auth/login') && response.request().method() === 'POST'),
    authorDialog.locator('input[type="password"]').press('Enter'),
  ]);
  expect(authorLogin.status()).toBe(200);
  await expect(authorDialog).toBeHidden();
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
}

module.exports = { installTrustedTask4MediaRoute, loginAsLessonAuthor, managerUser };
