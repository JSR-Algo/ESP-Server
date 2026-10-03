const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { createHash } = require('node:crypto');
const { writeFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { resetLessonStudioE2EState } = require('../../scripts/reset-lesson-studio-e2e-state.cjs');

const admin = '/nestjs/v1/admin';
const childId = 'be060000-0000-4000-8000-000000000003';
// No credential-bearing traces: tests attach only status/readback receipts.
test.use({ trace: 'off', video: 'off' });
test.setTimeout(120000);

function sql(service, statement) {
  const project = process.env.COMPOSE_PROJECT_NAME;
  if (project !== 'tbot-be06-20261002') throw new Error('BE06 requires its independently owned Compose project');
  const container = `${project}-${service === 'postgres' ? 'pg' : service}`;
  const owner = execFileSync('docker', ['inspect', '--format={{index .Config.Labels "com.docker.compose.project"}}', container], { encoding: 'utf8', timeout: 15000 }).trim();
  if (owner !== project) throw new Error('BE06 resource ownership mismatch');
  const command = service === 'postgres'
    ? ['psql', '-v', 'ON_ERROR_STOP=1', '-U', 'tbot', '-d', 'tbot', '-A', '-t']
    : ['env', 'MYSQL_PWD=123456', 'mysql', '-u', 'root', 'tbot_esp32_server', '-N'];
  return execFileSync('docker', ['exec', '-i', container, ...command], {
    input: statement, encoding: 'utf8', timeout: 15000, maxBuffer: 8 * 1024 * 1024,
  }).trim();
}

function prepareActors() {
  for (const [id, username, role] of [
    [9000601, 'be06_ordinary', 0], [9000602, 'be06_expired', 1], [9000603, 'be06_revoked', 1],
  ]) sql('mysql', `INSERT INTO sys_user(id,username,password,super_admin,status,create_date,update_date)
    SELECT ${id},'${username}',password,${role},1,NOW(),NOW() FROM sys_user WHERE id=9000001
    ON DUPLICATE KEY UPDATE password=VALUES(password),super_admin=VALUES(super_admin),status=1;`);
  sql('postgres', `BEGIN;
    INSERT INTO parent_accounts(id,email,password_hash) VALUES('be060000-0000-4000-8000-000000000001','be06-browser@local.invalid','test-only') ON CONFLICT DO NOTHING;
    INSERT INTO households(id,name,owner_id) VALUES('be060000-0000-4000-8000-000000000002','Synthetic BE06','be060000-0000-4000-8000-000000000001') ON CONFLICT DO NOTHING;
    INSERT INTO child_profiles(id,household_id,display_name,birth_year) VALUES('${childId}','be060000-0000-4000-8000-000000000002','Synthetic BE06',2020) ON CONFLICT DO NOTHING;
    COMMIT;`);
}

async function login(page, username = 'lesson_admin_e2e') {
  resetLessonStudioE2EState();
  await page.goto('/login');
  await expect(page.getByRole('img', { name: 'Verification code' })).toHaveAttribute('src', /^blob:/);
  await expect.poll(() => page.evaluate(() => Boolean(JSON.parse(localStorage.getItem('pubConfig') || '{}').sm2PublicKey))).toBe(true);
  await page.getByTestId('manager-login-username').fill(username);
  await page.getByTestId('manager-login-password').fill('TbotE2E!2026');
  await page.getByTestId('manager-login-captcha').fill('E2E42');
  const response = page.waitForResponse(r => r.url().includes('/tbot/user/login') && r.request().method() === 'POST');
  await page.getByTestId('manager-login-submit').click();
  expect((await (await response).json()).code).toBe(0);
  await page.waitForURL(/#\/home$/);
  const token = await page.evaluate(() => JSON.parse(localStorage.getItem('token')).token);
  expect(Boolean(token), 'real manager issued a session').toBe(true);
  return token;
}

function headers(token) { return token ? { Authorization: `Bearer ${token}` } : {}; }
async function call(request, token, method, route, data, extra = {}) {
  return request.fetch(admin + route, { method, data, headers: { ...headers(token), ...extra } });
}
async function fixture(request, token) {
  const suffix = `be06-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const course = await call(request, token, 'POST', '/courses', { courseKey: suffix, title: suffix, locale: 'en-US', ageBand: '4-6' });
  expect(course.status()).toBe(201);
  const data = (await course.json()).data;
  const lesson = await call(request, token, 'POST', `/courses/${data.id}/lessons`, { lessonKey: suffix, title: suffix, locale: 'en-US', ageBand: '4-6' });
  expect(lesson.status()).toBe(201);
  return { course: data, lesson: (await lesson.json()).data };
}
function readback() {
  const tables = ['courses', 'lessons', 'lesson_steps', 'asset_bundles', 'assets', 'shared_visual_assets',
    'shared_visual_asset_versions', 'lesson_visual_refs', 'child_profiles', 'lesson_assignments', 'admin_audit_log', 'lesson_asset_generation_rebuilds'];
  return Object.fromEntries(tables.map(table => {
    const rows = sql('postgres', `SELECT COALESCE(jsonb_agg(row ORDER BY row::text),'[]'::jsonb) FROM (SELECT to_jsonb(t) row FROM ${table} t) s;`);
    return [table, createHash('sha256').update(rows).digest('hex')];
  }));
}
async function attach(testInfo, name, receipt) {
  await testInfo.attach(name, { body: JSON.stringify(receipt, null, 2), contentType: 'application/json' });
  writeFileSync(testInfo.outputPath(`${name}.json`), JSON.stringify(receipt, null, 2));
}

test.beforeEach(() => prepareActors());

test('real ingress denies anonymous, ordinary, expired and revoked roles across sensitive routes with unchanged native readbacks', async ({ browser, request }, testInfo) => {
  const pages = [];
  const signIn = async username => { const page = await browser.newPage(); pages.push(page); return login(page, username); };
  const superToken = await signIn('lesson_admin_e2e');
  const ordinary = await signIn('be06_ordinary');
  const expired = await signIn('be06_expired');
  const revoked = await signIn('be06_revoked');
  for (const token of [expired, revoked]) expect((await request.get('/tbot/user/proxy-auth', { headers: headers(token) })).status()).toBe(204);
  sql('mysql', 'UPDATE sys_user_token SET expire_date=NOW()-INTERVAL 1 MINUTE WHERE user_id=9000602;');
  const revoke = await request.put('/tbot/user/change-password', { headers: headers(revoked), data: { password: 'TbotE2E!2026', newPassword: 'TbotBE06Revoked!2026' } });
  expect(revoke.status()).toBe(200); expect((await revoke.json()).code).toBe(0);
  const f = await fixture(request, superToken);
  const routes = [
    ['GET', '/courses'], ['GET', `/courses/${f.course.id}`], ['PATCH', `/courses/${f.course.id}`, { title: 'denied' }],
    ['GET', '/courses?page=1&pageSize=1'],
    ['GET', `/courses/${f.course.id}/lessons?page=1&pageSize=1`],
    ['GET', `/courses/${f.course.id}/lessons/authoritative?page=1&pageSize=1`],
    ['GET', '/course-insights/learners?page=1&pageSize=1'],
    ['GET', '/course-insights/course-quality?page=1&pageSize=1'],
    ['GET', '/course-insights/learners'], ['PATCH', `/course-insights/learners/${childId}/personality`, { learningStyle: 'audio' }],
    ['GET', `/lessons/${f.lesson.id}/assets`], ['POST', '/assets/upload', {}], ['GET', '/lesson-visual-assets'],
    ['POST', `/lessons/${f.lesson.id}/publish`, {}],
    ['POST', '/lesson-assignments', { lessonId: f.lesson.id, lessonVersion: 1, childId }],
    ['GET', '/lesson-assignments/eligible-devices'], ['GET', '/lesson-rollout-capabilities'],
  ];
  const before = readback(); const receipts = [];
  for (const [actor, token, status] of [['anonymous', '', 401], ['ordinary', ordinary, 403], ['expired', expired, 401], ['revoked', revoked, 401]]) {
    expect((await request.get('/tbot/user/proxy-auth', { headers: headers(token) })).status()).toBe(status);
    for (const [method, route, data] of routes) {
      const response = await call(request, token, method, route, data, {
        'X-TBOT-Admin-Key': 'browser-forged-untrusted-value', 'X-Admin-User-Id': '11111111-1111-4111-8111-111111111111',
        'X-Admin-Role': 'super_admin', 'X-Nest-Authorization': 'Bearer browser-forged-session',
      });
      expect(response.status(), `${actor} ${method} ${route}`).toBe(status);
      expect(response.headers()['x-tbot-manager-auth-status']).toBe(String(status));
      receipts.push({ actor, method, route, status, managerAuthStatus: status });
    }
  }
  const backendPort = process.env.LESSON_STUDIO_E2E_BACKEND_HOST_PORT;
  if (backendPort !== '13606') throw new Error('BE06 owned backend port required');
  for (const extra of [{}, { 'X-TBOT-Admin-Key': 'browser-forged-untrusted-value', 'X-Admin-Role': 'super_admin' }]) {
    const direct = await request.get(`http://127.0.0.1:${backendPort}/v1/admin/courses`, { headers: extra });
    expect(direct.status()).toBe(401);
    receipts.push({ actor: 'direct untrusted', method: 'GET', route: '/v1/admin/courses', status: 401 });
  }
  const after = readback(); expect(after).toEqual(before);
  await attach(testInfo, 'role-denials-and-readback', { receipts, before, after, nativeReadback: 'unchanged' });
  for (const page of pages) await page.close();
});

test('nginx overwrites forged browser credentials and attributes admitted writes to its trusted proxy principal', async ({ page, request }, testInfo) => {
  const token = await login(page);
  const runtime = process.env.BE06_RUNTIME_ROOT;
  if (runtime !== '/Users/manhhodinh/.tbot-operator/be06-live-20261002') throw new Error('BE06 private runtime ownership required');
  writeFileSync(resolve(runtime, 'super-token'), token, { mode: 0o600 });
  const f = await fixture(request, token);
  const plain = await call(request, token, 'GET', `/courses/${f.course.id}`);
  const forged = await call(request, token, 'GET', `/courses/${f.course.id}`, undefined, {
    'X-TBOT-Admin-Key': 'browser-forged-untrusted-value', 'X-Admin-User-Id': '11111111-1111-4111-8111-111111111112',
    'X-Admin-Role': 'viewer', 'X-Nest-Authorization': 'Bearer browser-forged-session',
  });
  expect(plain.status()).toBe(200); expect(forged.status()).toBe(200);
  expect(forged.headers()['x-tbot-manager-auth-status']).toBe('204');
  expect(await forged.json()).toEqual(await plain.json());
  const pagedReadReceipts = [];
  for (const route of ['/courses?page=1&pageSize=1', `/courses/${f.course.id}/lessons?page=1&pageSize=1`,
    `/courses/${f.course.id}/lessons/authoritative?page=1&pageSize=1`,
    '/course-insights/learners?page=1&pageSize=1', '/course-insights/course-quality?page=1&pageSize=1']) {
    const response = await call(request, token, 'GET', route);
    expect(response.status()).toBe(200);
    const data = (await response.json()).data;
    expect(data.pagination).toMatchObject({ page: 1, pageSize: 1 });
    pagedReadReceipts.push({ method: 'GET', route, status: 200, pagination: data.pagination });
  }
  const patch = await call(request, token, 'PATCH', `/course-insights/learners/${childId}/personality`, { learningStyle: 'visual' }, { 'X-Admin-User-Id': '11111111-1111-4111-8111-111111111112' });
  expect(patch.status()).toBe(200);
  const audit = JSON.parse(sql('postgres', "SELECT to_jsonb(t) FROM (SELECT admin_user_id,metadata FROM admin_audit_log WHERE action='learner.personality.update' ORDER BY occurred_at DESC LIMIT 1) t;"));
  expect(audit.metadata.actorType).toBe('admin_proxy');
  expect(audit.admin_user_id).not.toBe('11111111-1111-4111-8111-111111111112');
  await attach(testInfo, 'trusted-proxy-readback', { plainStatus: 200, forgedStatus: 200, mutationStatus: 200, pagedReadReceipts, actorType: audit.metadata.actorType, actorAnchor: audit.admin_user_id });
});

for (const transition of ['expiry', 'revocation']) test(`denies a real course form submission after ${transition} while the authenticated UI stays open`, async ({ page, request }, testInfo) => {
  const username = transition === 'expiry' ? 'be06_expired' : 'be06_revoked';
  const token = await login(page, username);
  await page.goto('/login#/course-management');
  await expect(page.getByRole('heading', { name: 'Courses' })).toBeVisible();
  await page.getByRole('button', { name: 'Create course', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Create course' });
  await expect(dialog).toBeVisible();
  await dialog.locator('input').nth(0).fill(`be06-denied-${Date.now()}`);
  await dialog.locator('input').nth(1).fill('BE06 denied UI mutation');
  expect((await request.get('/tbot/user/proxy-auth', { headers: headers(token) })).status()).toBe(204);
  if (transition === 'expiry') sql('mysql', 'UPDATE sys_user_token SET expire_date=NOW()-INTERVAL 1 MINUTE WHERE user_id=9000602;');
  else {
    const response = await request.put('/tbot/user/change-password', { headers: headers(token), data: { password: 'TbotE2E!2026', newPassword: 'TbotBE06Revoked!2026' } });
    expect((await response.json()).code).toBe(0);
  }
  const before = readback();
  const mutation = page.waitForResponse(r => new URL(r.url()).pathname === `${admin}/courses` && r.request().method() === 'POST');
  await dialog.getByTestId('course-submit').click();
  const response = await mutation; expect(response.status()).toBe(401);
  expect(response.headers()['x-tbot-manager-auth-status']).toBe('401');
  const after = readback(); expect(after).toEqual(before);
  await attach(testInfo, `open-ui-${transition}`, { transition, admittedBefore: 204, mutationStatus: 401, before, after, nativeReadback: 'unchanged' });
});
