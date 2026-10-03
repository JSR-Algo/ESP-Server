const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { adminApi } = require('./helpers/admin-api');
const { gotoAppRoute } = require('./helpers/navigation');
const { createInitialAuthoringFields } = require('../../src/components/lesson/lesson-builder-logic');
test.use({ actionTimeout: 15000 });

test('late course save cannot close a newly opened edit dialog', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const key = `modal-${Date.now().toString(36)}`;
  const courses = [];
  let release = () => {};
  let delivery = Promise.resolve();
  let routePattern;
  try {
    for (const suffix of ['a', 'b']) courses.push(await adminApi(page, 'POST', '/courses', {
      courseKey: `${key}-${suffix}`, title: `${key}-${suffix}`, locale: 'en-US', ageBand: '4-6',
    }));
    await gotoAppRoute(page, '#/course-management');
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    const open = suffix => page.getByRole('row').filter({ hasText: `${key}-${suffix}` }).getByRole('button', { name: 'Edit', exact: true }).click();
    await open('a');
    const dialog = page.getByRole('dialog', { name: 'Edit course' });
    await dialog.locator('input').nth(1).fill('Saved A');
    let arrived;
    let failed;
    const received = new Promise((resolve, reject) => { arrived = resolve; failed = reject; });
    const held = new Promise(resolve => { release = resolve; });
    routePattern = `**/nestjs/v1/admin/courses/${courses[0].id}`;
    await page.route(routePattern, route => {
      delivery = (async () => {
        try {
          const response = await route.fetch(); arrived(response.status());
          await held; await route.fulfill({ response });
        } catch (error) { failed(error); throw error; }
      })();
      return delivery;
    });
    await dialog.getByRole('button', { name: 'Save', exact: true }).click();
    expect(await received).toBe(200);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
    await expect(dialog).toBeHidden();
    await open('b');
    await dialog.locator('input').nth(1).fill('Unsaved B');
    const completed = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith(`/courses/${courses[0].id}`));
    release(); await (await completed).finished();
    await expect(dialog).toBeVisible(); await expect(dialog.locator('input').nth(1)).toHaveValue('Unsaved B');
    expect((await adminApi(page, 'GET', `/courses/${courses[1].id}`)).title).toBe(`${key}-b`);
    await dialog.getByRole('button', { name: 'Cancel', exact: true }).click();
  } finally {
    release();
    const pending = await Promise.allSettled([delivery]);
    if (routePattern) await page.unroute(routePattern);
    const cleanup = await Promise.allSettled(courses.map(c => adminApi(page, 'DELETE', `/courses/${c.id}`)));
    const error = [...pending, ...cleanup].find(result => result.status === 'rejected');
    if (error) throw error.reason;
  }
});

test('course edit, clone and draft metadata deletion persist through the real API', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const key = `detail-${Date.now().toString(36)}`;
  const course = await adminApi(page, 'POST', '/courses', { courseKey: key, title: key, locale: 'en-US', ageBand: '4-6' });
  let cloned;
  let lesson;
  try {
    await gotoAppRoute(page, '#/course-management');
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    let row = page.getByRole('row').filter({ hasText: key });
    await row.getByRole('button', { name: 'Edit', exact: true }).click();
    const edit = page.getByRole('dialog', { name: 'Edit course' });
    await edit.locator('input').nth(1).fill(`${key} edited`);
    const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith(`/courses/${course.id}`));
    await edit.getByRole('button', { name: 'Save', exact: true }).click();
    expect((await saved).status()).toBe(200);
    await expect(edit).toBeHidden();
    expect((await adminApi(page, 'GET', `/courses/${course.id}`)).title).toBe(`${key} edited`);
    await row.getByRole('button', { name: 'Clone', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: 'Clone → customize' });
    const response = page.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/courses/${course.id}/clone`));
    await dialog.getByRole('button', { name: 'Clone', exact: true }).click();
    const result = await response; expect(result.status()).toBe(201); cloned = (await result.json()).data;
    await expect(page).toHaveURL(new RegExp(`courseId=${cloned.id}`));
    lesson = await adminApi(page, 'POST', `/courses/${cloned.id}/lessons`, {
      lessonKey: `${key}-lesson`, title: 'Draft metadata', locale: 'en-US', ageBand: '4-6', estimatedDurationSec: 480,
    });
    await page.getByRole('button', { name: 'Refresh', exact: true }).click();
    row = page.locator('.el-table__fixed-right .el-table__fixed-body-wrapper tr').filter({ hasText: `${key}-lesson` });
    await row.getByRole('button', { name: 'Metadata', exact: true }).click();
    const metadata = page.getByRole('dialog', { name: 'Edit lesson metadata' });
    await expect(metadata).toBeVisible();
    await expect.poll(async () => {
      const bounds = await metadata.boundingBox();
      return bounds && bounds.x >= 0 && bounds.x + bounds.width <= page.viewportSize().width;
    }).toBe(true);
    await metadata.locator('input').nth(1).fill('Updated draft metadata');
    const updated = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().endsWith(`/lessons/${lesson.id}`));
    await metadata.getByRole('button', { name: 'Save', exact: true }).click();
    expect((await updated).status()).toBe(200);
    await expect(metadata).toBeHidden();
    expect((await adminApi(page, 'GET', `/lessons/${lesson.id}`)).title).toBe('Updated draft metadata');
    await row.getByRole('button', { name: 'Delete', exact: true }).click();
    const deleted = page.waitForResponse(r => r.request().method() === 'DELETE' && r.url().endsWith(`/lessons/${lesson.id}`));
    await page.locator('.el-message-box').getByRole('button', { name: 'OK', exact: true }).click();
    expect((await deleted).status()).toBe(200);
    expect(await adminApi(page, 'GET', `/courses/${cloned.id}/lessons`)).toEqual([]);
    lesson = null;
    await gotoAppRoute(page, '#/course-management');
    row = page.getByRole('row').filter({ hasText: cloned.courseKey || cloned.course_key });
    await row.getByRole('button', { name: 'Delete', exact: true }).click();
    const courseDeleted = page.waitForResponse(r => r.request().method() === 'DELETE' && r.url().endsWith(`/courses/${cloned.id}`));
    await page.locator('.el-message-box').getByRole('button', { name: 'OK', exact: true }).click();
    expect((await courseDeleted).status()).toBe(200);
    const courses = await adminApi(page, 'GET', '/courses');
    expect(courses.some(c => c.id === cloned.id)).toBe(false);
    cloned = null;
  } finally {
    if (lesson) await adminApi(page, 'DELETE', `/lessons/${lesson.id}`);
    if (cloned) await adminApi(page, 'DELETE', `/courses/${cloned.id}`);
    await adminApi(page, 'DELETE', `/courses/${course.id}`);
  }
});

test('published lesson exposes its existing next-version draft', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const sourceId = process.env.LESSON_STUDIO_E2E_VISUAL_SOURCE_LESSON_ID;
  expect(sourceId).toBeTruthy();
  const published = await adminApi(page, 'GET', `/lessons/${sourceId}`);
  expect(published.status).toBe('published');
  published.courseId = published.courseId || published.course_id;
  published.lessonKey = published.lessonKey || published.lesson_key;
  expect(published.courseId).toBeTruthy();
  const versions = await adminApi(page, 'GET', `/courses/${published.courseId}/lessons`);
  let draft = versions.find(l => (l.lessonKey || l.lesson_key) === published.lessonKey && l.status === 'draft');
  let created = false;
  if (!draft) {
    draft = await adminApi(page, 'POST', `/lessons/${sourceId}/new-version`, {});
    created = true;
  }
  try {
    await gotoAppRoute(page, `#/course-lessons?courseId=${published.courseId}`);
    const row = page.locator('.el-table__fixed-right .el-table__fixed-body-wrapper tr').filter({ hasText: published.lessonKey });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText('published');
    await row.getByRole('button', { name: 'Resume draft', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`lessonId=${draft.id}`));
  } finally {
    if (created) await adminApi(page, 'DELETE', `/lessons/${draft.id}`);
  }
});

test('clearing step hints survives save and reload', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const key = `hints-${Date.now().toString(36)}`;
  const course = await adminApi(page, 'POST', '/courses', { courseKey: key, title: key, locale: 'en-US', ageBand: '4-6' });
  let lesson;
  try {
    lesson = await adminApi(page, 'POST', `/courses/${course.id}/lessons`, { lessonKey: key, title: key, locale: 'en-US', ageBand: '4-6' });
    await adminApi(page, 'POST', `/lessons/${lesson.id}/steps`, {
      stepType: 'greeting', subject: 'lantern', prompt: 'Say lantern', helperText: 'Old helper', l1TransferHint: 'Old transfer',
      stepBody: createInitialAuthoringFields({ subject: 'lantern', prompt: 'Say lantern' }),
    });
    await gotoAppRoute(page, `#/lesson-editor?lessonId=${lesson.id}&courseId=${course.id}`);
    const helper = page.getByTestId('lesson-step-helper');
    const transfer = page.getByTestId('lesson-step-l1-hint');
    await expect(helper).toHaveValue('Old helper');
    await helper.fill(''); await transfer.fill('');
    const saved = page.waitForResponse(r => r.request().method() === 'PATCH' && r.url().includes(`/lessons/${lesson.id}/steps/`));
    await page.getByRole('button', { name: 'Save step', exact: true }).click();
    const response = await saved; expect(response.status()).toBe(200);
    expect(response.request().postDataJSON()).toMatchObject({ helperText: '', l1TransferHint: '' });
    await expect(page.getByRole('button', { name: 'Save step', exact: true })).not.toHaveClass(/is-loading/);
    const steps = await adminApi(page, 'GET', `/lessons/${lesson.id}/steps`);
    expect(steps[0].helperText ?? steps[0].helper_text ?? '').toBe('');
    expect(steps[0].l1TransferHint ?? steps[0].l1_transfer_hint ?? '').toBe('');
    await page.reload();
    await expect(helper).toHaveValue(''); await expect(transfer).toHaveValue('');
  } finally {
    if (lesson) await adminApi(page, 'DELETE', `/lessons/${lesson.id}`);
    await adminApi(page, 'DELETE', `/courses/${course.id}`);
  }
});
