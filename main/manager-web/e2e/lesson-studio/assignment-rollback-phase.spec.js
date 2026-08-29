const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { adminApi, adminApiResponse } = require('./helpers/admin-api');
const { gotoAppRoute } = require('./helpers/navigation');

const DEVICE_ID = '91deb5af-c1c0-416b-956d-266d510eac5e';
const CHILD_ID = '44444444-4444-4444-8444-444444444444';
const COURSE_ID = '66666666-6666-4666-8666-666666666666';
const LESSON_KEY = 'tvideo-farm-real-assets-v2';

async function openMonitoring(page) {
  await gotoAppRoute(page, `#/lesson-monitoring?keyword=${DEVICE_ID}`);
  await expect(page.getByTestId('monitoring-lesson-version').first()).toBeVisible();
}

function monitoringRow(page, version, state) {
  return page.locator('.el-table__body-wrapper tbody tr').filter({
    has: page.getByTestId('monitoring-lesson-version').filter({ hasText: `v${version}` }),
  }).filter({ hasText: state });
}

test('executes NEW through CourseLessons UI and ROLLBACK through public admin APIs', async ({ page }) => {
  const phase = process.env.TASK4_ASSIGNMENT_PHASE;
  expect(['new', 'rollback']).toContain(phase);
  await loginAsLessonAuthor(page);

  const versions = await adminApi(page, 'GET', `/courses/${COURSE_ID}/lessons`);
  expect(versions).toEqual(expect.arrayContaining([
    expect.objectContaining({ lesson_key: LESSON_KEY, lesson_version: 8, status: 'published' }),
    expect.objectContaining({ lesson_key: LESSON_KEY, lesson_version: 9, status: 'published' }),
  ]));
  const lessonByVersion = new Map(versions.map((lesson) => [lesson.lesson_version, lesson]));

  if (phase === 'new') {
    await gotoAppRoute(page, `#/course-lessons?courseId=${COURSE_ID}`);
    await page.locator('.filter-row input').first().fill(lessonByVersion.get(9).title);
    // Element Plus renders fixed action columns in a separate table from the lesson cells.
    const assignButton = page.getByRole('button', { name: /assign to child/i });
    await expect(assignButton).toBeVisible();
    await assignButton.click();
    const dialog = page.getByRole('dialog', { name: /assign lesson to child/i });
    await expect(dialog).toContainText(`${LESSON_KEY} · v9`);
    await dialog.locator('.el-select').first().click();
    await page.getByText('Mai', { exact: true }).last().click();
    const deviceRow = dialog.locator('.assignment-devices .el-table__body-wrapper tbody tr')
      .filter({ hasText: 'Task 4 Robot' });
    await expect(deviceRow).toBeVisible();
    const assignmentResponse = page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && response.url().includes('/nestjs/v1/admin/lesson-assignments')
    ));
    await deviceRow.getByRole('button', { name: /^assign$/i }).click();
    const response = await assignmentResponse;
    expect(response.status(), await response.text()).toBe(201);
    await expect(dialog.getByText('Assignment created', { exact: true })).toBeVisible();
    const created = (await response.json()).data.assignment;
    expect(created).toMatchObject({ deviceId: DEVICE_ID, childId: CHILD_ID, lessonId: LESSON_KEY, lessonVersion: 9 });
    await openMonitoring(page);
    await expect(monitoringRow(page, 9, 'ASSIGNED').first()).toBeVisible();
  } else {
    const before = await adminApi(page, 'GET', `/lesson-monitoring/assignments?deviceId=${DEVICE_ID}&limit=20`);
    const current = before.assignments.find((row) => (
      row.lessonId === lessonByVersion.get(9).id
      && ['ASSIGNED', 'PRELOADING', 'READY', 'RUNNING', 'PAUSED'].includes(row.state)
    ));
    expect(current).toMatchObject({
      lessonId: lessonByVersion.get(9).id, lessonKey: LESSON_KEY, lessonVersion: 9, state: 'ASSIGNED',
    });
    await adminApi(page, 'POST', `/lesson-assignment-operations/${current.assignmentId}/cancel`, {
      expectedAssignmentVersion: current.assignmentVersion, reason: 'CONTENT_REPLACED',
    });
    const rollback = await adminApiResponse(page, 'POST', '/lesson-assignments', {
      deviceId: DEVICE_ID, childId: CHILD_ID, lessonId: LESSON_KEY, lessonVersion: 8, profile: 'espTft',
    });
    expect(rollback.status(), await rollback.text()).toBe(201);
    const rollbackAssignment = (await rollback.json()).data.assignment;
    expect(rollbackAssignment).toMatchObject({ lessonId: LESSON_KEY, lessonVersion: 8, state: 'ASSIGNED' });
    expect(rollbackAssignment.assignmentId).toBeTruthy();
    const after = await adminApi(page, 'GET', `/lesson-monitoring/assignments?deviceId=${DEVICE_ID}&limit=20`);
    expect(after.assignments).toEqual(expect.arrayContaining([
      expect.objectContaining({
        assignmentId: current.assignmentId, lessonId: lessonByVersion.get(9).id,
        lessonKey: LESSON_KEY, lessonVersion: 9, state: 'CANCELLED', assignmentVersion: current.assignmentVersion + 1,
      }),
      expect.objectContaining({
        assignmentId: rollbackAssignment.assignmentId, lessonId: lessonByVersion.get(8).id,
        lessonKey: LESSON_KEY, lessonVersion: 8, state: 'ASSIGNED', assignmentVersion: 1,
      }),
    ]));
    await openMonitoring(page);
    await expect(monitoringRow(page, 9, 'CANCELLED').first()).toBeVisible();
    await expect(monitoringRow(page, 8, 'ASSIGNED').first()).toBeVisible();
  }
});
