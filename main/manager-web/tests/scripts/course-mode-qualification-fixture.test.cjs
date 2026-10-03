const test = require('node:test');
const assert = require('node:assert/strict');
const { createCourseModeDraft } = require('../../e2e/lesson-studio/helpers/admin-api');

// Unit transport only; the curriculum compiler is the built candidate's real compiler.
function fixturePage() {
  const calls = [];
  let contract;
  return { calls, page: {
    evaluate: async () => ({ manager: 'unit-manager', nest: 'unit-author' }),
    request: { fetch: async (url, options) => {
      calls.push({ url, ...options });
      let data = { id: 'draft', visualChecksum: 'initial' };
      if (url.includes('/lesson-visual-assets')) data = [{ publicationState: 'published', assetKey: 'scene.barn-farm' }];
      else if (url.includes('/assets')) data = { assets: [{ assetId: 'existing' }] };
      else if (options.method === 'PUT') { contract = options.data.contract; data = { contract }; }
      return { ok: () => true, status: () => 200, text: async () => '', json: async () => ({ data }) };
    } },
  } };
}

async function withWeek(value, run) {
  const previous = process.env.LESSON_STUDIO_E2E_COURSE_MODE_WEEK;
  if (value === undefined) delete process.env.LESSON_STUDIO_E2E_COURSE_MODE_WEEK;
  else process.env.LESSON_STUDIO_E2E_COURSE_MODE_WEEK = value;
  try { await run(); } finally {
    if (previous === undefined) delete process.env.LESSON_STUDIO_E2E_COURSE_MODE_WEEK;
    else process.env.LESSON_STUDIO_E2E_COURSE_MODE_WEEK = previous;
  }
}

test('qualification week uses the genuine Farm curriculum and semantic key', async () => withWeek('9', async () => {
  const fixture = fixturePage();
  const result = await createCourseModeDraft(fixture.page, { runId: 'unit-farm' });
  assert.equal(result.contract.fixtureId, 'curriculum.w09');
  assert.equal(result.contract.activities[0].visual.backgroundAssetKey, 'scene.barn-farm');
  assert.match(fixture.calls.find(call => call.url.endsWith('/lessons')).data.lessonKey, /-w09-/);
}));

test('explicit fixture week takes precedence over the environment', async () => withWeek('9', async () => {
  const result = await createCourseModeDraft(fixturePage().page, { weekNumber: 1, runId: 'unit-explicit' });
  assert.equal(result.contract.fixtureId, 'curriculum.w01');
}));

test('default curriculum week remains one', async () => withWeek(undefined, async () => {
  const result = await createCourseModeDraft(fixturePage().page, { runId: 'unit-default' });
  assert.equal(result.contract.fixtureId, 'curriculum.w01');
}));

test('invalid qualification weeks fail before any API writes', async () => {
  for (const value of ['0', '27', '9.5', '9junk', '', '-1']) await withWeek(value, async () => {
    const fixture = fixturePage();
    await assert.rejects(createCourseModeDraft(fixture.page), /curriculum week/);
    assert.equal(fixture.calls.length, 0);
  });
});
