const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { selectByTestId, selectOption } = require('./helpers/select');

test('admin creates and persists an eight-minute safe-speaking lesson draft', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  const runId = Date.now().toString(36);
  const courseKey = `e2e-ls-${runId}`;
  const lessonKey = `e2e-8m-${runId}`;

  await loginAsLessonAuthor(page);
  await page.getByRole('button', { name: 'Create course' }).click();
  const courseDialog = page.getByRole('dialog', { name: 'Create course' });
  await courseDialog.getByPlaceholder('e.g. w02-numbers').fill(courseKey);
  await courseDialog.locator('input').nth(1).fill(`Lesson Studio ${runId}`);
  await selectByTestId(page, courseDialog, 'course-locale', 'en-US');
  await selectByTestId(page, courseDialog, 'course-age-band', '4-6');
  const createCourse = page.waitForResponse((response) =>
    response.url().endsWith('/nestjs/v1/admin/courses') && response.request().method() === 'POST');
  await courseDialog.getByRole('button', { name: 'Save' }).click();
  expect((await createCourse).status()).toBe(201);

  const courseRow = page.getByRole('row').filter({ hasText: courseKey });
  await expect(courseRow).toBeVisible();
  await courseRow.getByRole('button', { name: 'Lessons' }).click();
  await expect(page.getByRole('heading', { name: new RegExp(`Lessons.*${runId}`) })).toBeVisible();

  await page.getByRole('button', { name: 'Create lesson' }).click();
  const lessonDialog = page.getByRole('dialog', { name: 'Create lesson' });
  await lessonDialog.getByPlaceholder('e.g. w02-d01-run-say-it').fill(lessonKey);
  await lessonDialog.locator('input').nth(1).fill(`Safe Speaking ${runId}`);
  await selectByTestId(page, lessonDialog, 'lesson-locale', 'en-US');
  await selectByTestId(page, lessonDialog, 'lesson-age-band', '4-6');
  await lessonDialog.getByPlaceholder('animals, farm, visual').fill('safeSpeaking, story, visual');
  await selectOption(page, lessonDialog.getByPlaceholder('Difficulty'), 'basic');
  await lessonDialog.getByRole('spinbutton').fill('480');
  const createLesson = page.waitForResponse((response) =>
    response.url().includes('/nestjs/v1/admin/courses/')
      && response.url().endsWith('/lessons')
      && response.request().method() === 'POST');
  const expectedDraftReads = [];
  const draftReadsRegistered = createLesson.then(async response => {
    const { data: lesson } = await response.json();
    const courseModePath = `/nestjs/v1/admin/lessons/${lesson.id}/course-mode`;
    const previewPath = `/nestjs/v1/admin/lessons/${lesson.id}/manifest-preview`;
    for (let count = 0; count < 2; count += 1) {
      assertNoUnexpectedPageErrors.expectFault('GET', courseModePath, 404, 'blank draft has no Course Mode contract');
    }
    for (let count = 0; count < 3; count += 1) {
      assertNoUnexpectedPageErrors.expectFault('GET', previewPath, 422, 'blank draft has no espTft asset bundle');
    }
    page.on('response', response => {
      const url = new URL(response.url());
      if (response.request().method() !== 'GET') return;
      if (url.pathname === courseModePath) {
        expectedDraftReads.push(response.json().then(body => ({ kind: 'courseMode', status: response.status(), body }))
          .catch(error => ({ error: error.message })));
      } else if (url.pathname === previewPath) {
        expectedDraftReads.push(response.json().then(body => ({ kind: 'preview', status: response.status(),
          profile: url.searchParams.get('profile'), body })).catch(error => ({ error: error.message })));
      }
    });
  });
  await lessonDialog.getByRole('button', { name: 'Save' }).click();
  expect((await createLesson).status()).toBe(201);
  await draftReadsRegistered;
  await expect(page.getByRole('heading', { name: new RegExp(`Safe Speaking ${runId}`) })).toBeVisible();

  await page.getByRole('button', { name: '+ Add step' }).click();
  const stepDialog = page.getByRole('dialog', { name: 'Add step' });
  await stepDialog.getByRole('textbox', { name: 'Prompt' }).fill('Welcome to the lantern story.');
  await stepDialog.getByRole('textbox', { name: 'Vocab word / subject' }).fill('lantern');
  const createStep = page.waitForResponse((response) =>
    response.url().includes('/nestjs/v1/admin/lessons/')
      && response.url().endsWith('/steps')
      && response.request().method() === 'POST');
  await stepDialog.getByRole('button', { name: 'Save' }).click();
  expect((await createStep).status()).toBe(201);
  await expect(stepDialog).toBeHidden();

  const eightMinutes = page.locator('label[role="radio"]').filter({ hasText: /^8 min$/ });
  await eightMinutes.click();
  const saveStep = page.waitForResponse((response) =>
    response.url().includes('/steps/') && response.request().method() === 'PATCH');
  await page.getByRole('button', { name: 'Save step' }).click();
  const savedStep = await saveStep;
  expect(savedStep.status()).toBe(200);
  expect(await savedStep.finished()).toBeNull();
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  // Fonts are outside the API/image journal and must finish before reload too.
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.reload();

  await expect(eightMinutes.locator('input[type="radio"]')).toBeChecked();
  const authoredValues = await page.locator('input, textarea').evaluateAll((elements) =>
    elements.map((element) => element.value));
  expect(authoredValues).toEqual(expect.arrayContaining([
    'Safe speaking',
    'LANTERN',
    'Welcome to the lantern story.',
    'Celebrate learning lantern.',
    'What will we discover about lantern next?',
  ]));

  const validateLesson = page.waitForResponse((response) =>
    response.url().includes('/nestjs/v1/admin/lessons/')
      && response.url().endsWith('/validate')
      && response.request().method() === 'POST');
  const lessonId = new URLSearchParams(page.url().split('?')[1]).get('lessonId');
  assertNoUnexpectedPageErrors.expectFault('POST', `/nestjs/v1/admin/lessons/${lessonId}/validate`, 422, 'draft has no asset bundle');
  await page.getByRole('button', { name: 'Validate' }).click();
  const validationResponse = await validateLesson;
  expect(validationResponse.status()).toBe(422);
  const validationBody = await validationResponse.json();
  expect(validationBody.code).toBe('ASSET_PROFILE_UNAVAILABLE');
  // The failure surfaces twice — the el-message toast and the readiness panel's
  // server-validation error list — which is the point: the operator cannot miss it.
  await expect(page.getByText(validationBody.message).first()).toBeVisible();
  await expect(page.getByTestId('validation-result').getByText(validationBody.message))
    .toBeVisible();
  await assertNoUnexpectedPageErrors.waitForSettledRequests();
  expect(expectedDraftReads).toHaveLength(5);
  const draftReads = await Promise.all(expectedDraftReads);
  expect(draftReads.filter(read => read.kind === 'courseMode')).toHaveLength(2);
  expect(draftReads.filter(read => read.kind === 'preview')).toHaveLength(3);
  for (const read of draftReads) {
    if (read.kind === 'courseMode') {
      expect(read).toMatchObject({ status: 404,
        body: { code: 'NOT_FOUND', message: 'Course Mode contract is not configured' } });
    } else {
      expect(read).toMatchObject({ kind: 'preview', status: 422, profile: 'espTft',
        body: { code: 'ASSET_PROFILE_UNAVAILABLE', details: { profile: 'espTft' } } });
    }
  }
  assertNoUnexpectedPageErrors();
});
