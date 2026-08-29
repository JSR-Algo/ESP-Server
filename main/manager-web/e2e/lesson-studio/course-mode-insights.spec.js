const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { gotoAppRoute } = require('./helpers/navigation');
const { adminApi, createCourseModeDraft } = require('./helpers/admin-api');

test('surfaces Course Insights and lifecycle history through the real admin session', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  const fixture = await createCourseModeDraft(page);

  const learners = await adminApi(page, 'GET', '/course-insights/learners?limit=20');
  expect(learners).toEqual(expect.objectContaining({ learners: expect.any(Array) }));
  const quality = await adminApi(page, 'GET', `/course-insights/course-quality?windowDays=30&courseId=${fixture.course.id}`);
  expect(quality).toEqual(expect.objectContaining({ courses: expect.any(Array) }));

  await gotoAppRoute(page, '#/course-insights');
  await expect(page.getByRole('heading', { name: /learner & quality/i })).toBeVisible();
  await expect(page.getByRole('tab', { name: /child personality/i })).toBeVisible();
  await expect(page.getByRole('tab', { name: /course quality/i })).toBeVisible();
  await gotoAppRoute(page, '#/course-management');
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  await expect(page.locator('body')).toContainText(fixture.course.title);
  assertNoUnexpectedPageErrors();
});
