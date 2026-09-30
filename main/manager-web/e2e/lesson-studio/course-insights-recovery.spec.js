const { test, expect } = require('@playwright/test');
const { loginAsLessonAuthor } = require('./helpers/session');
const { adminApi } = require('./helpers/admin-api');
const { gotoAppRoute } = require('./helpers/navigation');

test('real learner saves retain their target when the first response arrives after selection changes', async ({ page }, testInfo) => {
  test.setTimeout(120000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await loginAsLessonAuthor(page);
  const { learners } = await adminApi(page, 'GET', '/course-insights/learners?limit=200');
  expect(learners.length, 'isolated native fixture requires two learners').toBeGreaterThanOrEqual(2);
  const [first, second] = learners;
  for (const learner of [first, second]) {
    expect(['visual', 'audio', 'interactive']).toContain(learner.personality.learningStyle);
    expect(['beginner', 'basic', 'intermediate', 'advanced']).toContain(learner.personality.vocabularyLevel);
    expect(learner.personality.attentionSpanSec).toBeGreaterThan(0);
  }
  const runId = Date.now().toString(36);
  const firstInterest = `admin-a-${runId}`;
  const secondInterest = `admin-b-${runId}`;
  await gotoAppRoute(page, '#/course-insights');
  const select = async learner => {
    const row = page.locator('.learner-list .el-table__body-wrapper tr')
      .filter({ hasText: learner.childName }).filter({ hasText: learner.parentEmail || learner.parentId }).first();
    await row.click();
    await expect(page.locator('.detail-head .small')).toHaveText(learner.childId);
  };
  await select(first);
  const interests = page.locator('.personality-form .el-input input').first();
  await interests.fill(firstInterest);

  let release;
  let received;
  const held = new Promise(resolve => { release = resolve; });
  const arrived = new Promise(resolve => { received = resolve; });
  const path = `/nestjs/v1/admin/course-insights/learners/${first.childId}/personality`;
  let delivery = Promise.resolve();
  const delay = route => {
    delivery = (async () => {
    // Delay only delivery of the actual backend response; do not fabricate data.
    const response = await route.fetch();
    received(response.status());
    await held;
    await route.fulfill({ response });
    })();
    return delivery;
  };
  await page.route(`**${path}`, delay);
  try {
    await page.locator('.personality-form button.el-button--primary').click();
    expect(await arrived).toBe(200);
    await select(second);
    await interests.fill(secondInterest);
    release();
    await expect(page.locator('.personality-form button.el-button--primary')).not.toHaveClass(/is-loading/);
    await expect(page.locator('.detail-head .small')).toHaveText(second.childId);
    await expect(interests).toHaveValue(secondInterest);
    const saved = page.waitForResponse(response => response.request().method() === 'PATCH'
      && response.url().endsWith(`/learners/${second.childId}/personality`));
    await page.locator('.personality-form button.el-button--primary').click();
    expect((await saved).status()).toBe(200);
    await expect(page.locator('.personality-form button.el-button--primary')).not.toHaveClass(/is-loading/);
    const after = await adminApi(page, 'GET', '/course-insights/learners?limit=200');
    expect(after.learners.find(row => row.childId === first.childId).personality.interests).toEqual([firstInterest]);
    expect(after.learners.find(row => row.childId === second.childId).personality.interests).toEqual([secondInterest]);
    await testInfo.attach('learner-save-readback.json', { contentType: 'application/json', body: Buffer.from(JSON.stringify({
      scope: 'ISOLATED_REAL_API_DATABASE_WITH_RESPONSE_DELAY',
      first: { childId: first.childId, interests: [firstInterest] },
      second: { childId: second.childId, interests: [secondInterest] },
    })) });
    expect(errors).toEqual([]);
  } finally {
    release();
    const [delivered] = await Promise.allSettled([delivery]);
    await page.unroute(`**${path}`, delay);
    // Restore via the same real API so repeated runs retain the fixture profile.
    const restores = await Promise.allSettled([first, second].map(learner =>
      adminApi(page, 'PATCH', `/course-insights/learners/${learner.childId}/personality`, learner.personality)));
    const failed = restores.find(result => result.status === 'rejected');
    if (failed) throw failed.reason;
    const restored = await adminApi(page, 'GET', '/course-insights/learners?limit=200');
    for (const learner of [first, second]) {
      expect(restored.learners.find(row => row.childId === learner.childId).personality).toEqual(learner.personality);
    }
    if (delivered.status === 'rejected') throw delivered.reason;
  }
});
