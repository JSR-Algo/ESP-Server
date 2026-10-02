const { test, expect } = require('@playwright/test');
const { createHash } = require('node:crypto');
const { deployedReadOnlyTargets } = require('./targets.cjs');
const targets = deployedReadOnlyTargets(process.env);

// Required author/role/mutation journeys are intentionally not claimed by this
// supporting smoke. This file has no mocks, local resets, TLS exceptions or writes.
async function receipt(response, testInfo, name) {
  const body = await response.body();
  const headers = response.headers();
  await testInfo.attach(name, {
    body: JSON.stringify({
      method: 'GET', url: response.url(), status: response.status(),
      requestId: headers['x-request-id'] || headers['x-correlation-id'] || null,
      contentType: headers['content-type'], bytes: body.length,
      sha256: createHash('sha256').update(body).digest('hex'),
    }, null, 2), contentType: 'application/json',
  });
}

test('anonymous deep link is denied and login controls fit the viewport', async ({ page }, testInfo) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto('/#/course-management', { waitUntil: 'networkidle' });
  await expect(page).toHaveURL(/#\/login\?redirect=/);
  const selectors = ['manager-login-username', 'manager-login-password', 'manager-login-captcha', 'manager-login-submit'];
  for (const selector of selectors) {
    const control = page.getByTestId(selector);
    await expect(control).toBeVisible();
    const box = await control.boundingBox();
    expect(box, selector).not.toBeNull();
    const viewport = page.viewportSize();
    expect(box.x, selector).toBeGreaterThanOrEqual(-1);
    expect(box.x + box.width, selector).toBeLessThanOrEqual(viewport.width + 1);
  }
  const image = page.getByRole('img', { name: 'Verification code' });
  await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => node.complete && node.naturalWidth > 0)).toBe(true);
  expect(await page.evaluate(() => localStorage.getItem('token'))).toBeNull();
  expect(errors).toEqual([]);
  await testInfo.attach('anonymous-login', { body: await page.screenshot(), contentType: 'image/png' });
  await page.reload({ waitUntil: 'networkidle' });
  await expect(page.getByTestId('manager-login-username')).toBeVisible();
  expect(errors).toEqual([]);
});

test('real backend and ingress reject anonymous admin access', async ({ request }, testInfo) => {
  const health = await request.get(`${targets.api}/v1/health`);
  await receipt(health, testInfo, 'health');
  expect(health.status()).toBe(200);
  expect((await health.json()).status).toBe('ok');
  const direct = await request.get(`${targets.api}/v1/admin/courses`);
  await receipt(direct, testInfo, 'direct-admin-denial');
  expect(direct.status()).toBe(401);
  expect((await direct.json()).code).toBe('UNAUTHORIZED');
  const proxy = await request.get(`${targets.web}/nestjs/v1/admin/courses`);
  await receipt(proxy, testInfo, 'ingress-admin-denial');
  expect(proxy.status()).toBe(401);
});

test('public latest generation is available with its canonical readback identity', async ({ request }, testInfo) => {
  const response = await request.get(`${targets.api}/v1/public/lesson-assets/latest`);
  await receipt(response, testInfo, 'public-generation');
  // An existing 503 remains a failed availability assertion. Never accept it as
  // a successful publication or skip this required supporting readiness signal.
  expect(response.status()).toBe(200);
  const { data } = await response.json();
  expect(data.generation).toBeGreaterThan(0);
  expect(data.indexChecksum).toMatch(/^[a-f0-9]{64}$/);
  expect(data.curriculumLessonCount).toBeGreaterThan(0);
  expect(data.packCount).toBeGreaterThan(0);
  expect(response.headers().etag).toBe(`"lesson-assets-g${data.generation}-${data.indexChecksum}"`);
});
