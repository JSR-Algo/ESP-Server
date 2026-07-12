const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');

test('admin creates and persists an eight-minute safe-speaking lesson draft', async ({ page }) => {
  const runId = Date.now().toString(36);
  const courseKey = `e2e-ls-${runId}`;
  const lessonKey = `e2e-8m-${runId}`;

  await loginAsLessonAuthor(page);
  await page.getByRole('button', { name: 'Create course' }).click();
  const courseDialog = page.getByRole('dialog', { name: 'Create course' });
  await courseDialog.getByPlaceholder('e.g. w02-numbers').fill(courseKey);
  await courseDialog.locator('input').nth(1).fill(`Lesson Studio ${runId}`);
  await courseDialog.getByPlaceholder('en').fill('en-US');
  await courseDialog.getByPlaceholder('6-8').fill('6-8');
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
  await lessonDialog.getByPlaceholder('en').fill('en-US');
  await lessonDialog.getByPlaceholder('6-8').fill('6-8');
  await lessonDialog.getByPlaceholder('animals, farm, visual').fill('safeSpeaking, story, visual');
  await lessonDialog.getByPlaceholder('Difficulty').click();
  await page.getByRole('listitem').filter({ hasText: /^basic$/ }).click();
  await lessonDialog.getByRole('spinbutton').fill('480');
  const createLesson = page.waitForResponse((response) =>
    response.url().includes('/nestjs/v1/admin/courses/')
      && response.url().endsWith('/lessons')
      && response.request().method() === 'POST');
  await lessonDialog.getByRole('button', { name: 'Save' }).click();
  expect((await createLesson).status()).toBe(201);
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
  expect((await saveStep).status()).toBe(200);
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
  await page.getByRole('button', { name: 'Validate' }).click();
  const validationResponse = await validateLesson;
  expect(validationResponse.status()).toBe(422);
  const validationBody = await validationResponse.json();
  expect(validationBody.code).toBe('ASSET_PROFILE_UNAVAILABLE');
  await expect(page.getByText(validationBody.message)).toBeVisible();
});
