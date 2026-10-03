const { expect } = require('@playwright/test');
const { adminApi } = require('./admin-api');

function uniqueCourseKey(tag = 'crud') { return `fe02-${tag}-${Date.now()}-${Math.random().toString(16).slice(2,8)}`; }
function courseRow(page, key) { return page.locator('.el-table__body-wrapper tr').filter({ has: page.getByText(key, { exact: true }) }); }
async function fillCourseDialog(page, key, title) {
  const dialog = page.getByRole('dialog', { name: 'Create course', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.locator('.el-form-item').filter({ hasText: 'Course key' }).getByRole('textbox').fill(key);
  await dialog.locator('.el-form-item').filter({ hasText: 'Title' }).getByRole('textbox').fill(title);
  return dialog;
}
async function createCourseInUI(page, key, title) {
  await page.getByRole('button', { name: 'Create course', exact: true }).click();
  const dialog = await fillCourseDialog(page,key,title);
  const response = page.waitForResponse(r => /\/courses$/.test(r.url()) && r.request().method()==='POST');
  await dialog.getByTestId('course-submit').click();
  expect((await response).status()).toBe(201);
  await expect(dialog).toBeHidden();
  const rows = await adminApi(page,'GET','/courses?kind=all');
  const course = rows.find(row => row.course_key === key);
  expect(course).toBeTruthy(); return course;
}
module.exports = { uniqueCourseKey, courseRow, fillCourseDialog, createCourseInUI };
