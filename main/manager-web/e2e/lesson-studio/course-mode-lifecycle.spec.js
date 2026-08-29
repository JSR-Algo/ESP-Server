const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { monitorUnexpectedPageErrors } = require('./helpers/page-errors');
const { gotoAppRoute, installCinematicTestRoutes } = require('./helpers/navigation');
const {
  adminApi,
  adminApiResponse,
  apiRoot,
  createCourseModeDraft,
  createPublishableCourseModeVisuals,
  createVisualTriple,
  withCanonicalCourseModeChecksum,
} = require('./helpers/admin-api');

test('binds the provisioned Course Mode visual triple through the admin pickers', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  await installCinematicTestRoutes(page);
  const fixture = await createCourseModeDraft(page);
  const versions = await createVisualTriple(page, fixture.lesson.id, fixture.runId, { bind: false });

  await gotoAppRoute(page, `#/lesson-editor?lessonId=${fixture.lesson.id}`);
  const selections = [
    ['lesson-background-selector', 'scene.playground-park'],
    ['lesson-object-selector', 'object.no'],
    ['lesson-robot-selector', `robot.e2e.${fixture.runId}`],
  ];
  for (const [index, [testId, assetKey]] of selections.entries()) {
    const picker = page.getByTestId(testId);
    const escapedKey = assetKey.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const button = picker.getByRole('button', { name: new RegExp(`^${escapedKey} v`) }).first();
    await expect(button).toBeEnabled();
    const visualSave = index === selections.length - 1
      ? page.waitForResponse((response) => response.request().method() === 'PUT'
        && response.url().includes(`/nestjs/v1/admin/lessons/${fixture.lesson.id}/visuals`))
      : null;
    await button.click();
    if (visualSave) expect((await visualSave).status()).toBe(200);
  }

  const steps = await adminApi(page, 'GET', `/lessons/${fixture.lesson.id}/steps`);
  const refs = steps[0].visual_refs || steps[0].visualRefs;
  expect(Object.fromEntries(refs.map((ref) => [
    ref.slot,
    ref.asset_version_id || ref.assetVersionId,
  ]))).toMatchObject({
    backgroundScene: versions.backgroundScene.id,
    teachingObject: versions.teachingObject.id,
    robotOverlay: versions.robotOverlay.id,
  });
  assertNoUnexpectedPageErrors();
});

test('validates, publishes, clones, and keeps the published Course Mode version immutable', async ({ page }) => {
  const assertNoUnexpectedPageErrors = monitorUnexpectedPageErrors(page);
  await loginAsLessonAuthor(page);
  const fixture = await createCourseModeDraft(page);
  await createVisualTriple(page, fixture.lesson.id, fixture.runId);
  await createPublishableCourseModeVisuals(page, fixture.lesson.id);

  const malformed = structuredClone(fixture.contract);
  malformed.activities[0].expectedDurationSec = 999;
  const malformedResponse = await adminApiResponse(page, 'PUT', `/lessons/${fixture.lesson.id}/course-mode`, {
    expectedChecksum: fixture.contract.contractChecksum,
    contract: malformed,
  });
  expect(malformedResponse.status()).toBe(400);
  expect(await adminApi(page, 'POST', `/lessons/${fixture.lesson.id}/validate`)).toMatchObject({ valid: true });
  const published = await adminApi(page, 'POST', `/lessons/${fixture.lesson.id}/publish`);
  expect(published).toMatchObject({ status: 'published' });

  const immutable = await adminApiResponse(page, 'PUT', `/lessons/${fixture.lesson.id}/course-mode`, {
    expectedChecksum: fixture.contract.contractChecksum,
    contract: fixture.contract,
  });
  expect(immutable.status()).toBe(409);
  const clone = await adminApi(page, 'POST', `/courses/${fixture.course.id}/clone`, {
    courseKey: `clone-${fixture.runId}`,
    title: `Clone ${fixture.runId}`,
  });
  expect(clone).toMatchObject({ source_course_id: fixture.course.id, status: 'draft' });
  const historical = await adminApi(page, 'GET', `/courses/${fixture.course.id}/lessons`);
  expect(historical).toEqual(expect.arrayContaining([expect.objectContaining({ id: fixture.lesson.id, status: 'published' })]));
  assertNoUnexpectedPageErrors();
});

test('deduplicates identical retries and rejects a stale second-admin draft write', async ({ browser }) => {
  const first = await browser.newPage();
  const second = await browser.newPage();
  await loginAsLessonAuthor(first, { authorEmail: 'lesson-author-b-e2e@local.invalid' });
  await loginAsLessonAuthor(second);
  const fixture = await createCourseModeDraft(first);
  const visuals = await createVisualTriple(second, fixture.lesson.id, fixture.runId);
  await createPublishableCourseModeVisuals(second, fixture.lesson.id);
  const stale = await adminApi(second, 'GET', `/lessons/${fixture.lesson.id}/course-mode`);

  const before = await adminApi(first, 'GET', `/courses/${fixture.course.id}/lessons`);
  const [retryA, retryB] = await Promise.all([
    adminApiResponse(first, 'PUT', `/lessons/${fixture.lesson.id}/course-mode`, {
      expectedChecksum: stale.contract.contractChecksum, contract: stale.contract,
    }),
    adminApiResponse(first, 'PUT', `/lessons/${fixture.lesson.id}/course-mode`, {
      expectedChecksum: stale.contract.contractChecksum, contract: stale.contract,
    }),
  ]);
  expect([retryA.status(), retryB.status()].sort()).toEqual([200, 200]);
  const retryBodies = await Promise.all([retryA.json(), retryB.json()]);
  expect(retryBodies.map((body) => body.data.checksum)).toEqual([
    stale.contract.contractChecksum,
    stale.contract.contractChecksum,
  ]);
  expect((await adminApi(first, 'GET', `/lessons/${fixture.lesson.id}/course-mode`)).contract.contractChecksum)
    .toBe(stale.contract.contractChecksum);
  expect(await adminApi(first, 'GET', `/courses/${fixture.course.id}/lessons`)).toHaveLength(before.length);

  const winner = structuredClone(stale.contract);
  winner.activities[0].contextId = `${winner.activities[0].contextId}.admin-a`;
  const winnerWithChecksum = withCanonicalCourseModeChecksum(winner);
  const winnerResponse = await adminApi(first, 'PUT', `/lessons/${fixture.lesson.id}/course-mode`, {
    expectedChecksum: stale.contract.contractChecksum,
    contract: winnerWithChecksum,
  });
  expect(winnerResponse.checksum).toBe(winnerWithChecksum.contractChecksum);
  const loser = structuredClone(stale.contract);
  loser.activities[0].contextId = `${loser.activities[0].contextId}.admin-b`;
  const loserWithChecksum = withCanonicalCourseModeChecksum(loser);
  const staleWrite = await adminApiResponse(second, 'PUT', `/lessons/${fixture.lesson.id}/course-mode`, {
    expectedChecksum: stale.contract.contractChecksum,
    contract: loserWithChecksum,
  });
  expect(staleWrite.status()).toBe(409);
  expect(await staleWrite.json()).toMatchObject({ code: 'LESSON_COURSE_MODE_CONFLICT' });
  const persistedWinner = await adminApi(first, 'GET', `/lessons/${fixture.lesson.id}/course-mode`);
  expect(persistedWinner.contract.contractChecksum).toBe(winnerWithChecksum.contractChecksum);
  expect(persistedWinner.contract.activities[0].contextId).toBe(winnerWithChecksum.activities[0].contextId);
  await adminApi(second, 'PUT', `/lessons/${fixture.lesson.id}/visuals`, {
    backgroundAssetVersionId: visuals.backgroundScene.id,
    objectAssetVersionId: visuals.teachingObject.id,
    robotAssetVersionId: visuals.robotOverlay.id,
  });

  await adminApi(second, 'POST', `/lessons/${fixture.lesson.id}/publish`);
  expect((await adminApi(second, 'GET', `/lessons/${fixture.lesson.id}`)).status).toBe('published');
  await first.close();
  await second.close();
});

test('fails closed for missing auth, role-equivalent manager-only auth, IDOR, and unsafe assignment', async ({ page }) => {
  await loginAsLessonAuthor(page);
  const authless = await page.request.get(`${apiRoot}/courses`);
  expect(authless.status()).toBe(401);

  const managerAuthorization = await page.evaluate(() => ({
    Authorization: `Bearer ${(JSON.parse(localStorage.getItem('token') || '{}')).token || ''}`,
  }));
  const managerLogin = await page.request.post(`${apiRoot}/auth/login`, {
    headers: { ...managerAuthorization, 'Content-Type': 'application/json' },
    data: {
      email: 'lesson-manager-e2e@local.invalid',
      password: 'TbotAuthorE2E!2026',
    },
  });
  expect(managerLogin.status()).toBe(200);
  const managerLoginPayload = await managerLogin.json();
  const managerSession = (managerLoginPayload.data || managerLoginPayload).session_token;
  expect(managerSession).toBeTruthy();
  const managerHeaders = {
    ...managerAuthorization,
    'Content-Type': 'application/json',
    'X-Nest-Authorization': `Bearer ${managerSession}`,
  };
  const managerOnly = await page.request.get(`${apiRoot}/lesson-assignments/eligible-devices`, {
    headers: managerHeaders,
  });
  expect(managerOnly.status()).toBe(403);
  const ownScoped = await page.request.get(
    `${apiRoot}/lessons/00000006-0099-4000-8000-000000000002/course-mode`,
    { headers: managerHeaders },
  );
  expect(ownScoped.status()).toBe(404);
  const idor = await page.request.get(
    `${apiRoot}/lessons/00000006-0002-0000-0000-000000000001/course-mode`,
    { headers: managerHeaders },
  );
  expect(idor.status()).toBe(403);
  const assignment = await page.request.post(`${apiRoot}/lesson-assignments`, {
    headers: managerHeaders,
    data: {
      deviceId: '00000000-0000-4000-8000-000000000000',
      lessonId: '00000006-0002-0000-0000-000000000001',
      lessonVersion: 1,
      childId: '00000000-0000-4000-8000-000000000000',
      profile: 'espTft',
    },
  });
  expect(assignment.status()).toBe(403);
});
