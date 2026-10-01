const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { adminApi } = require('./helpers/admin-api');
const { gotoAppRoute } = require('./helpers/navigation');

test('published bundle is inspectable and validates against the real service', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const id = process.env.LESSON_STUDIO_E2E_LEGACY_SOURCE_LESSON_ID;
  expect(id).toBeTruthy();
  const lesson = await adminApi(page, 'GET', `/lessons/${id}`);
  expect(lesson.status).toBe('published');
  const bundle = await adminApi(page, 'GET', `/lessons/${id}/assets?profile=espTft`);
  expect(bundle.assets.length).toBeGreaterThan(0);
  await gotoAppRoute(page, `#/lesson-editor?lessonId=${id}&courseId=${lesson.courseId || lesson.course_id}`);
  await expect(page.locator('.asset-row')).toHaveCount(bundle.assets.length);
  await expect(page.locator('.asset-form')).toHaveCount(0);
  await expect(page.locator('.asset-actions')).toHaveCount(0);
  await expect(page.locator('.robot-preview')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Validate', exact: true })).toBeEnabled();
  const validation = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/lessons/${id}/validate`));
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  const response = await validation;
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.data.valid).toBe(true);
  await expect(page.getByTestId('validation-result')).toContainText('PASS');
});

test('canonical legacy draft validates, previews, simulates and publishes through the admin', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const sourceId = process.env.LESSON_STUDIO_E2E_LEGACY_SOURCE_LESSON_ID;
  expect(sourceId).toBeTruthy();
  const source = await adminApi(page, 'GET', `/lessons/${sourceId}`);
  const before = await adminApi(page, 'GET', `/lessons/${sourceId}/manifest-preview?profile=espTft`);
  const key = `legacy-publish-${Date.now().toString(36)}`;
  const course = await adminApi(page, 'POST', `/courses/${source.courseId || source.course_id}/clone`, { courseKey: key, title: key });
  const lessons = await adminApi(page, 'GET', `/courses/${course.id}/lessons`);
  const lesson = lessons.find(row => row.title === source.title);
  expect(lesson).toBeTruthy();
  await gotoAppRoute(page, `#/lesson-editor?lessonId=${lesson.id}&courseId=${course.id}`);
  await expect(page.locator('.robot-preview')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Validate', exact: true })).toBeEnabled();
  const validate = page.waitForResponse(r => r.url().endsWith(`/lessons/${lesson.id}/validate`));
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  expect((await validate).status()).toBe(200);
  const simulate = page.waitForResponse(r => new URL(r.url()).pathname.endsWith(`/lessons/${lesson.id}/simulate`));
  await page.getByRole('button', { name: 'Simulate', exact: true }).click();
  expect((await simulate).status()).toBe(200);
  await expect(page.locator('.simulation-result')).toContainText('lesson_completed');
  await expect(page.getByRole('button', { name: 'Publish', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Publish', exact: true }).click();
  const review = page.getByRole('dialog', { name: /immutable version review/i });
  await expect(review).toBeVisible();
  await review.getByTestId('immutable-ack').click();
  const publish = page.waitForResponse(r => r.url().endsWith(`/lessons/${lesson.id}/publish`));
  await review.getByRole('button', { name: /publish reviewed version/i }).click();
  expect((await publish).status()).toBe(200);
  await expect(page.getByRole('button', { name: 'Publish', exact: true })).toHaveCount(0);
  expect((await adminApi(page, 'GET', `/lessons/${lesson.id}`)).status).toBe('published');
  expect(await adminApi(page, 'GET', `/lessons/${sourceId}/manifest-preview?profile=espTft`)).toEqual(before);
  // Keep the immutable publication in the isolated evidence database.
});

test('legacy timing and ending edits persist without inventing interaction metadata', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const key = `legacy-${Date.now().toString(36)}`;
  const sourceId = process.env.LESSON_STUDIO_E2E_LEGACY_SOURCE_LESSON_ID;
  expect(sourceId, 'canonical published legacy fixture is required').toBeTruthy();
  const course = await adminApi(page, 'POST', '/courses', { courseKey: key, title: key, locale: 'en-US', ageBand: '4-6' });
  let lesson;
  const original = { durationSec: 48, terminal: false, branches: { correct: { nextStepKey: 's1' } } };
  try {
    lesson = await adminApi(page, 'POST', `/courses/${course.id}/lessons`, { lessonKey: key, title: key, locale: 'en-US', ageBand: '4-6' });
    await adminApi(page, 'POST', `/lessons/${lesson.id}/steps`, { stepType: 'greeting', subject: 'barn', prompt: 'This is a barn.', stepBody: original });
    const sourceBundle = await adminApi(page, 'GET', `/lessons/${sourceId}/assets?profile=espTft`);
    expect(sourceBundle.assets.length).toBeGreaterThan(0);
    for (const asset of sourceBundle.assets) {
      await adminApi(page, 'POST', `/lessons/${lesson.id}/assets`, { profile: 'espTft', sourceAssetId: asset.assetId });
    }
    await gotoAppRoute(page, `#/lesson-editor?lessonId=${lesson.id}&courseId=${course.id}`);
    await expect(page.getByRole('button', { name: 'Validate', exact: true })).toBeEnabled();
    const validation = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/lessons/${lesson.id}/validate`));
    await page.getByRole('button', { name: 'Validate', exact: true }).click();
    const response = await validation;
    expect(response.status()).toBe(422);
    const failure = await response.json();
    expect(failure.code).toBe('LESSON_PUBLISH_VALIDATION_FAILED');
    expect(failure.details.errors.length).toBeGreaterThan(0);
    for (const error of failure.details.errors) {
      await expect(page.getByTestId(`readiness-error-${error.code}`).first()).toBeVisible();
      await expect(page.getByTestId('validation-result')).toContainText(error.message);
    }
    const duration = page.getByTestId('step-duration-input').getByRole('spinbutton');
    await expect(duration).toHaveValue('48');
    await duration.fill('15');
    await duration.press('Tab');
    await page.getByTestId('step-terminal-input').click();
    const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().includes(`/lessons/${lesson.id}/steps/`));
    await page.getByRole('button', { name: 'Save step', exact: true }).click();
    expect((await saved).status()).toBe(200);
    const readback = await adminApi(page, 'GET', `/lessons/${lesson.id}/steps`);
    const body = readback[0].stepBody || readback[0].step_body;
    expect(body).toEqual({ ...original, durationSec: 15, terminal: true });
    await page.reload();
    await expect(duration).toHaveValue('15');
    await expect(page.getByTestId('step-terminal-input')).toHaveAttribute('aria-checked', 'true');
  } finally {
    if (lesson) await adminApi(page, 'DELETE', `/lessons/${lesson.id}`);
    await adminApi(page, 'DELETE', `/courses/${course.id}`);
  }
});
