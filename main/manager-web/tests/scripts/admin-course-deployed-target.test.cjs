const { test } = require('node:test');
const assert = require('node:assert/strict');
const { deployedReadOnlyTargets } = require('../../e2e/admin-course-deployed/targets.cjs');

test('verified deployed origins are explicit and never inherited from local E2E', () => {
  assert.deepEqual(deployedReadOnlyTargets({ ADMIN_COURSE_DEPLOYED_WEB_URL: 'https://admin.tjbot.vn/', ADMIN_COURSE_DEPLOYED_API_URL: 'https://backend.tjbot.vn' }), { web: 'https://admin.tjbot.vn', api: 'https://backend.tjbot.vn' });
  assert.throws(() => deployedReadOnlyTargets({ LESSON_STUDIO_E2E_WEB_ORIGIN: 'http://127.0.0.1:8080' }), /explicit/);
});

test('credentials, insecure and unverified targets, paths and query strings are rejected', () => {
  for (const web of ['http://admin.tjbot.vn', 'https://someone:secret@admin.tjbot.vn', 'https://127.0.0.1', 'https://other.invalid', 'https://admin.tjbot.vn/login', 'https://admin.tjbot.vn?token=secret', 'https://admin.tjbot.vn#login', 'https://admin.tjbot.vn:8443']) {
    assert.throws(() => deployedReadOnlyTargets({ ADMIN_COURSE_DEPLOYED_WEB_URL: web, ADMIN_COURSE_DEPLOYED_API_URL: 'https://backend.tjbot.vn' }), /verified HTTPS origin/);
  }
  assert.throws(() => deployedReadOnlyTargets({ ADMIN_COURSE_DEPLOYED_WEB_URL: 'https://admin.tjbot.vn', ADMIN_COURSE_DEPLOYED_API_URL: 'https://other.invalid' }), /verified HTTPS origin/);
});
