const { test, expect } = require('@playwright/test');

// Prepared for BE06's integrated, owned production-mode proxy stack. Tokens
// must be issued by its real manager service; never substitute fake auth routes.
// The standard global setup/login reset behavior still requires owned resources.
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} required for real ingress qualification`);
  return value;
}

test('real manager boundary denies anonymous, ordinary, expired and revoked sessions before mutations', async ({ request }) => {
  const superToken = required('BE06_MANAGER_SUPER_TOKEN');
  const courseId = required('BE06_OWNED_COURSE_ID');
  const lessonId = required('BE06_OWNED_LESSON_ID');
  const childId = required('BE06_OWNED_CHILD_ID');
  // Expired/revoked tokens must first have succeeded against /user/proxy-auth,
  // then be expired/logged out with native manager fixtures on the owned stack.
  const actors = [
    { name: 'anonymous', token: '', status: 401 },
    { name: 'ordinary manager', token: required('BE06_MANAGER_ORDINARY_TOKEN'), status: 403 },
    { name: 'expired manager', token: required('BE06_MANAGER_EXPIRED_TOKEN'), status: 401 },
    { name: 'revoked manager', token: required('BE06_MANAGER_REVOKED_TOKEN'), status: 401 },
  ];
  const adminPath = '/nestjs/v1/admin';
  const allowedHeaders = { Authorization: `Bearer ${superToken}` };
  const before = await request.get(`${adminPath}/courses/${courseId}`, { headers: allowedHeaders });
  expect(before.status()).toBe(200);
  expect(before.headers()['x-tbot-manager-auth-status']).toBe('204');
  const initial = await before.json();
  const routes = [
    ['GET', '/courses', undefined],
    ['PATCH', `/courses/${courseId}`, { title: 'BE06 denial must not persist' }],
    ['GET', '/course-insights/learners', undefined],
    ['PATCH', `/course-insights/learners/${childId}/personality`, { learningStyle: 'audio' }],
    ['GET', `/lessons/${lessonId}/assets`, undefined],
    ['POST', '/assets/upload', {}],
    ['GET', '/lesson-visual-assets', undefined],
    ['POST', `/lessons/${lessonId}/publish`, {}],
    ['POST', '/lesson-assignments', { lessonId: 'be06-denied', lessonVersion: 1 }],
    ['GET', '/lesson-rollout-capabilities', undefined],
  ];
  for (const actor of actors) {
    const authHeaders = actor.token ? { Authorization: `Bearer ${actor.token}` } : {};
    const manager = await request.get('/tbot/user/proxy-auth', { headers: authHeaders });
    expect(manager.status(), `${actor.name}: native manager gate`).toBe(actor.status);
    for (const [method, suffix, data] of routes) {
      const denied = await request.fetch(adminPath + suffix, { method, data, headers: {
        ...authHeaders,
        'X-TBOT-Admin-Key': 'browser-forged-untrusted-value',
        'X-Admin-User-Id': '11111111-1111-4111-8111-111111111111',
        'X-Admin-Role': 'super_admin',
        'X-Nest-Authorization': 'Bearer browser-forged-session',
      } });
      expect(denied.status(), `${actor.name}: ${method} ${suffix}`).toBe(actor.status);
      expect(denied.headers()['x-tbot-manager-auth-status']).toBe(String(actor.status));
    }
  }
  const after = await request.get(`${adminPath}/courses/${courseId}`, { headers: allowedHeaders });
  expect(after.status()).toBe(200);
  expect(await after.json()).toEqual(initial);
});

test('nginx overwrites a browser proxy key after the real manager super-admin gate', async ({ request }) => {
  const token = required('BE06_MANAGER_SUPER_TOKEN');
  const courseId = required('BE06_OWNED_COURSE_ID');
  const plain = await request.get(`/nestjs/v1/admin/courses/${courseId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const forged = await request.get(`/nestjs/v1/admin/courses/${courseId}`, { headers: {
    Authorization: `Bearer ${token}`,
    'X-TBOT-Admin-Key': 'browser-forged-untrusted-value',
    'X-Admin-User-Id': '11111111-1111-4111-8111-111111111112',
    'X-Admin-Role': 'viewer',
    'X-Nest-Authorization': 'Bearer browser-forged-session',
  } });
  expect(plain.status()).toBe(200);
  expect(forged.status()).toBe(200);
  expect(forged.headers()['x-tbot-manager-auth-status']).toBe('204');
  expect(await forged.json()).toEqual(await plain.json());
});
