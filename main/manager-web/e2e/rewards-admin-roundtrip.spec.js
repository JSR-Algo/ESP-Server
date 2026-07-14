const { test, expect } = require('@playwright/test');
const { sm2 } = require('sm-crypto');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { mkdir, mkdtemp, readFile, readdir, rm, writeFile } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const path = require('node:path');

const execFileAsync = promisify(execFile);
const lifecycleModulePromise = import('../scripts/_lib/rewards-admin-browser-lifecycle.mjs');

const artifactDir = process.env.REWARDS_ADMIN_ARTIFACT_DIR
  || path.resolve(__dirname, '../output/playwright/rewards-admin-roundtrip');
const canonicalCourseKey = 'w01-place-words';
const cloneCourseKey = 'w01-place-words-browser-e2e';
const shellLogin = { username: 'browser-admin', password: 'BrowserShell-E2E-Only!', captcha: '2468' };
const shellPublicKey = sm2.generateKeyPairHex().publicKey;
const transparentGif = Buffer.from('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==', 'base64');

function requiredFixture() {
  const fixture = {
    email: process.env.REWARDS_ADMIN_EMAIL,
    password: process.env.REWARDS_ADMIN_PASSWORD,
    totp: process.env.REWARDS_ADMIN_TOTP,
  };
  for (const [key, value] of Object.entries(fixture)) {
    if (!value) throw new Error(`Missing browser fixture field: ${key}`);
  }
  return fixture;
}

function adminPath(url) {
  const parsed = new URL(url);
  return parsed.pathname.replace(/^\/nestjs/, '');
}

function routeQuery(url) {
  const parsed = new URL(url);
  const hashQuery = parsed.hash.includes('?') ? parsed.hash.slice(parsed.hash.indexOf('?') + 1) : '';
  return new URLSearchParams(hashQuery || parsed.search);
}

async function responsePayload(response) {
  const body = await response.json();
  return body && typeof body === 'object' && Object.prototype.hasOwnProperty.call(body, 'data')
    ? body.data
    : body;
}

async function waitForAdminResponse(page, { method, pathPattern, capturePayload = true }, action) {
  const { captureResponseDuringAction } = await lifecycleModulePromise;
  const { response, payload } = await captureResponseDuringAction({
    waitForResponse: () => page.waitForResponse((candidate) => (
      candidate.request().method() === method
      && pathPattern.test(adminPath(candidate.url()))
    )),
    action,
    readPayload: capturePayload ? responsePayload : undefined,
  });
  expect(response.status(), `${method} ${adminPath(response.url())}`).toBeGreaterThanOrEqual(200);
  expect(response.status(), `${method} ${adminPath(response.url())}`).toBeLessThan(300);
  return { response, payload };
}

function courseRow(page, courseKey) {
  return page.locator('.el-table__body-wrapper tbody tr')
    .filter({ has: page.getByText(courseKey, { exact: true }) })
    .first();
}

function lessonRow(page, title) {
  return page.locator('.el-table__body-wrapper tbody tr').filter({ hasText: title }).first();
}

async function browserAdminPayload(page, pathName) {
  return page.evaluate(async (path) => {
    const sessionToken = localStorage.getItem('nestjs_session_token');
    if (!sessionToken) throw new Error('Missing authenticated NestJS browser session');
    const response = await fetch(`/nestjs${path}`, {
      headers: { Authorization: `Bearer ${sessionToken}` },
    });
    const body = await response.json();
    if (!response.ok) throw new Error(`Authenticated admin request failed: ${response.status} ${path}`);
    return body && typeof body === 'object' && Object.prototype.hasOwnProperty.call(body, 'data')
      ? body.data
      : body;
  }, pathName);
}

function normalizePins(payload) {
  const assets = payload && Array.isArray(payload.assets) ? payload.assets : [];
  return assets.map((asset) => ({
    profile: asset.profile,
    assetKey: asset.assetKey,
    sha256: asset.sha256,
    bytes: Number(asset.bytes || 0),
  })).sort((left, right) => `${left.profile}:${left.assetKey}`.localeCompare(`${right.profile}:${right.assetKey}`));
}

async function redactDirectory(directory, forbiddenValues) {
  const { sanitizeArtifactBuffer } = await lifecycleModulePromise;
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const target = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      await redactDirectory(target, forbiddenValues);
      continue;
    }
    if (!entry.isFile()) continue;
    const buffer = await readFile(target);
    const sanitized = sanitizeArtifactBuffer(buffer, forbiddenValues);
    if (sanitized !== buffer) await writeFile(target, sanitized);
  }
}

async function sanitizeTraceArchive(rawTracePath, deliverableTracePath, forbiddenValues) {
  const directory = await mkdtemp(path.join(tmpdir(), 'rewards-admin-trace-'));
  try {
    await execFileAsync('unzip', ['-qq', rawTracePath, '-d', directory]);
    await redactDirectory(directory, forbiddenValues);
    await rm(deliverableTracePath, { force: true });
    await execFileAsync('zip', ['-qr', deliverableTracePath, '.'], { cwd: directory });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function loginWithRealAdminSession(page, fixture) {
  await page.route('**/tbot/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/tbot/user/captcha') {
      return route.fulfill({ status: 200, contentType: 'image/gif', body: transparentGif });
    }
    const responses = {
      '/tbot/user/pub-config': { sm2PublicKey: shellPublicKey, enableMobileRegister: false, allowUserRegister: false },
      '/tbot/user/login': { token: 'shell-session-route-gate-only' },
      '/tbot/user/info': { username: shellLogin.username, superAdmin: true },
      '/tbot/agent/list': { data: [] },
    };
    if (!Object.prototype.hasOwnProperty.call(responses, url.pathname)) {
      return route.fulfill({
        status: 501,
        contentType: 'application/json',
        body: JSON.stringify({ code: 1, msg: `Unexpected legacy mock endpoint: ${url.pathname}` }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, data: responses[url.pathname] }),
    });
  });
  await page.goto('/login');
  await page.getByPlaceholder('Please enter username').fill(shellLogin.username);
  await page.getByPlaceholder('Please enter password').fill(shellLogin.password);
  await page.getByPlaceholder('Please enter verification code').fill(shellLogin.captcha);
  await page.locator('.login-btn').click();
  await expect(page).toHaveURL(/\/home/);
  await expect(page.getByText(shellLogin.username, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: /More/ }).click();
  await page.getByText('Courses', { exact: true }).click();
  await expect(page).toHaveURL(/\/course-management/);

  const dialog = page.getByRole('dialog', { name: 'Sign in as author (NestJS)' });
  await expect(dialog).toBeVisible();
  await dialog.locator('input').nth(0).fill(fixture.email);
  await dialog.locator('input').nth(1).fill(fixture.password);
  const login = await waitForAdminResponse(page, {
    method: 'POST',
    pathPattern: /\/v1\/admin\/auth\/login$/,
  }, () => dialog.getByRole('button', { name: 'Author sign-in' }).click());
  expect(login.payload).toMatchObject({ mfa_required: true });
  expect(login.payload.mfa_token).toEqual(expect.any(String));

  const totp = process.env.REWARDS_ADMIN_FAILURE_INJECTION === 'mfa' ? '000000' : fixture.totp;
  await dialog.getByPlaceholder('6-digit code').fill(totp);
  const mfa = await waitForAdminResponse(page, {
    method: 'POST',
    pathPattern: /\/v1\/admin\/auth\/mfa-verify$/,
    capturePayload: false,
  }, () => dialog.getByRole('button', { name: 'Verify' }).click());
  expect(mfa.payload).toBeUndefined();
  await expect(dialog).toBeHidden();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('nestjs_session_token'))).not.toBeNull();
  await expect(page).toHaveURL(/\/home/);
  const courses = await waitForAdminResponse(page, {
    method: 'GET',
    pathPattern: /\/v1\/admin\/courses$/,
  }, async () => {
    await page.getByRole('button', { name: /More/ }).click();
    await page.getByText('Courses', { exact: true }).click();
  });
  expect(courses.payload).toEqual(expect.any(Array));
  await expect(courseRow(page, canonicalCourseKey)).toBeVisible();
}

async function openCanonicalLesson(page) {
  const row = courseRow(page, canonicalCourseKey);
  await row.getByRole('button', { name: 'Lessons' }).click();
  await expect(page).toHaveURL(/\/course-lessons/);
  const rowLesson = lessonRow(page, 'This Is a Barn');
  await expect(rowLesson).toBeVisible();
  await page.getByRole('button', { name: 'Edit steps', exact: true }).click();
  await expect(page).toHaveURL(/\/lesson-editor/);
  await expect(page.getByRole('heading', { name: /This Is a Barn/ })).toBeVisible();
  const lessonId = routeQuery(page.url()).get('lessonId');
  expect(lessonId).toEqual(expect.any(String));
  const assets = await browserAdminPayload(page, `/v1/admin/lessons/${lessonId}/assets`);
  const preview = await waitForAdminResponse(page, {
    method: 'GET',
    pathPattern: /\/v1\/admin\/lessons\/[^/]+\/manifest-preview$/,
  }, () => page.getByRole('button', { name: 'Preview', exact: true }).click());
  expect(preview.payload.preview).toEqual(expect.objectContaining({ profile: 'espTft', width: 480, height: 320 }));
  await expect(page.locator('.server-size')).toHaveText(/espTft\s+480x320/);
  return { lessonId, editorUrl: page.url(), checksum: preview.payload.checksum, pins: normalizePins(assets) };
}

async function cloneCanonicalLesson(page) {
  await waitForAdminResponse(page, {
    method: 'GET',
    pathPattern: /\/v1\/admin\/courses$/,
  }, async () => {
    await page.getByRole('button', { name: /More/ }).click();
    await page.getByText('Courses', { exact: true }).click();
  });
  const row = courseRow(page, canonicalCourseKey);
  await expect(row).toBeVisible();
  await page.getByRole('button', { name: 'Clone', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: 'Clone → customize' });
  await expect(dialog).toBeVisible();
  const inputs = dialog.locator('input');
  await inputs.nth(0).fill(cloneCourseKey);
  await inputs.nth(1).fill('Browser Roundtrip Barn');
  const clone = await waitForAdminResponse(page, {
    method: 'POST',
    pathPattern: /\/v1\/admin\/courses\/[^/]+\/clone$/,
  }, () => dialog.getByRole('button', { name: 'Clone' }).click());
  expect(clone.payload).toEqual(expect.objectContaining({ course_key: cloneCourseKey, status: 'draft' }));
  await expect(page).toHaveURL(/\/course-lessons/);
  const rowLesson = lessonRow(page, 'This Is a Barn');
  await expect(rowLesson).toBeVisible();
  await page.getByRole('button', { name: 'Edit steps', exact: true }).click();
  await expect(page).toHaveURL(/\/lesson-editor/);
  await expect(page.getByRole('heading', { name: /This Is a Barn/ })).toBeVisible();
}

async function editRequiredFields(page) {
  await page.getByRole('button', { name: /^5 listen\b/ }).click();
  const prompt = page.locator('.lesson-step-prompt-editor textarea');
  await prompt.fill('Your turn: say "barn" and help TeeBot find the glowing door.');
  await page.locator('.el-form-item').filter({ hasText: 'English teaching word' }).locator('input').fill('BARN');
  await page.locator('.el-form-item').filter({ hasText: 'Goal' }).locator('input').fill('Help TeeBot identify the barn door.');
  await page.locator('label.el-radio-button').filter({ hasText: '5 min' }).click();
  const save = await waitForAdminResponse(page, {
    method: 'PATCH',
    pathPattern: /\/v1\/admin\/lessons\/[^/]+\/steps\/s5$/,
  }, () => page.getByRole('button', { name: 'Save step' }).click());
  expect(save.payload).toBeTruthy();
  await expect(page.getByRole('button', { name: 'Publish' })).toBeDisabled();
  await expect(page.locator('.robot-preview')).toHaveCount(0);
}

async function reviewAndCloneSharedVisual(page) {
  const asset = page.locator('.asset-tile').filter({ hasText: 'teachingObject.barn' }).first();
  const impactPromise = page.waitForResponse((response) => (
    response.request().method() === 'GET'
    && /\/v1\/admin\/assets\/[^/]+\/impact$/.test(adminPath(response.url()))
  ));
  await asset.click();
  const impactResponse = await impactPromise;
  expect(impactResponse.status()).toBe(200);
  const impact = await responsePayload(impactResponse);
  expect(impact).toBeTruthy();

  const dialog = page.getByRole('dialog', { name: 'Review shared visual impact' });
  await expect(dialog).toBeVisible();
  const cloneKey = dialog.getByRole('textbox');
  await expect(cloneKey).toHaveValue('teachingObject.barn.v2');
  const clone = await waitForAdminResponse(page, {
    method: 'POST',
    pathPattern: /\/v1\/admin\/lessons\/[^/]+\/assets\/[^/]+\/clone$/,
  }, () => dialog.getByRole('button', { name: 'Clone for this draft' }).click());
  expect(clone.payload).toEqual(expect.objectContaining({ assetKey: 'teachingObject.barn.v2' }));
  await expect(dialog).toBeHidden({ timeout: 30_000 });
  await expect(page.locator('.asset-tile').filter({ hasText: 'teachingObject.barn.v2' })).toBeVisible();
}

async function assertExactPreview(page) {
  const validation = await waitForAdminResponse(page, {
    method: 'POST',
    pathPattern: /\/v1\/admin\/lessons\/[^/]+\/validate$/,
  }, () => page.getByRole('button', { name: 'Validate' }).click());
  expect(validation.payload).toEqual(expect.objectContaining({ valid: true }));
  expect(validation.payload.profiles).toContain('espTft');
  await expect(page.getByTestId('validation-result')).toContainText('PASS');

  const preview = await waitForAdminResponse(page, {
    method: 'GET',
    pathPattern: /\/v1\/admin\/lessons\/[^/]+\/manifest-preview$/,
  }, () => page.getByRole('button', { name: 'Preview', exact: true }).click());
  expect(preview.payload.preview).toEqual(expect.objectContaining({ profile: 'espTft', width: 480, height: 320 }));
  expect(preview.payload.checksum).toMatch(/^[a-f0-9]{64}$/i);
  const size = await page.locator('.robot-preview .stage').evaluate((element) => {
    const style = getComputedStyle(element);
    return { width: style.width, height: style.height };
  });
  expect(size).toEqual({ width: '480px', height: '320px' });
  await expect(page.locator('.server-size')).toHaveText(/espTft\s+480x320/);
  return preview.payload;
}

function expectedSimulationTrace(manifest, projection, preset) {
  const interactiveEvents = {
    Correct: (maxAttempts) => [{ outcome: 'correct', attempt: 1, action: 'advance' }],
    'Near miss': (maxAttempts) => [{ outcome: 'near_miss', attempt: 1, action: 'advance' }],
    'Brave try': (maxAttempts) => [{ outcome: 'brave_try', attempt: 1, action: 'advance' }],
    'Incorrect to fallback': (maxAttempts) => Array.from({ length: maxAttempts }, (_, index) => ({
      outcome: 'incorrect', attempt: index + 1, action: index + 1 < maxAttempts ? 'retry' : 'fallback_advance',
    })),
    'Retry then correct': (maxAttempts) => [
      { outcome: 'retry', attempt: 1, action: 'retry' },
      { outcome: 'correct', attempt: 2, action: 'advance' },
    ],
    Timeout: (maxAttempts) => [{ outcome: 'timeout', attempt: 1, action: 'fallback_advance' }],
    Completion: (maxAttempts) => [{ attempt: 1, action: 'fallback_advance' }],
  };
  const trace = [];
  for (const step of manifest.steps) {
    if (step.completionClass === 'passive') {
      trace.push({ stepKey: step.id, action: 'auto_advance' });
      continue;
    }
    const maxAttempts = projection.steps[step.id].maxAttempts;
    for (const event of interactiveEvents[preset](maxAttempts)) trace.push({ stepKey: step.id, ...event });
  }
  trace.push({ stepKey: 'lesson', action: 'lesson_completed' });
  return trace;
}

function simulationTraceContract(trace) {
  return trace.map(({ stepKey, outcome, attempt, action }) => ({
    stepKey,
    ...(outcome === undefined ? {} : { outcome }),
    ...(attempt === undefined ? {} : { attempt }),
    action,
  }));
}

async function runRequiredSimulations(page, manifest) {
  const presets = ['Correct', 'Near miss', 'Brave try', 'Incorrect to fallback', 'Retry then correct', 'Timeout', 'Completion'];
  const payloadSignatures = new Set();
  const responseSignatures = new Set();
  let finalPayload;
  for (const preset of presets) {
    await page.getByRole('radio', { name: preset, exact: true }).first().click();
    const simulation = await waitForAdminResponse(page, {
      method: 'POST',
      pathPattern: /\/v1\/admin\/lessons\/[^/]+\/simulate$/,
    }, () => page.getByRole('button', { name: 'Simulate' }).click());
    const requestPayload = simulation.response.request().postDataJSON();
    const expectedTrace = expectedSimulationTrace(manifest, requestPayload.projection, preset);
    expect(simulation.payload.simulation).toEqual(expect.objectContaining({
      terminated: true,
      terminationReason: 'lesson_completed',
    }));
    expect(simulationTraceContract(simulation.payload.simulation.trace)).toEqual(expectedTrace);
    payloadSignatures.add(JSON.stringify(requestPayload.outcomes));
    responseSignatures.add(JSON.stringify(simulationTraceContract(simulation.payload.simulation.trace)));
    finalPayload = simulation.payload;
  }
  expect(payloadSignatures.size, 'all seven presets must submit distinct outcome mappings').toBe(presets.length);
  expect(responseSignatures.size, 'all seven presets must prove distinct backend branch traces').toBe(presets.length);
  return finalPayload;
}

async function publishAndAssertOriginalImmutable(page) {
  await expect(page.getByRole('button', { name: 'Publish' })).toBeEnabled();
  await page.getByRole('button', { name: 'Publish' }).click();
  const dialog = page.getByRole('dialog', { name: 'Immutable version review' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('480×320');
  await dialog.getByTestId('immutable-ack').check();
  const published = await waitForAdminResponse(page, {
    method: 'POST',
    pathPattern: /\/v1\/admin\/lessons\/[^/]+\/publish$/,
  }, () => dialog.getByRole('button', { name: 'Publish reviewed version' }).click());
  expect(published.payload).toEqual(expect.objectContaining({ status: 'published', lessonVersion: 1 }));
  expect(published.payload.checksum).toMatch(/^[a-f0-9]{64}$/i);
  await expect(dialog).toContainText('Reviewed v1 draft bytes preserved: PASS', { timeout: 30_000 });
  await expect(dialog).toContainText('Published target identity: PASS');
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(dialog).toBeHidden();
  return published.payload;
}

test('admin customizes the canonical lesson without mutating v1', async ({ page, context }) => {
  const fixture = requiredFixture();
  const { isExpectedBrowserHttpFailure } = await lifecycleModulePromise;
  const requests = [];
  const consoleEntries = [];
  const failures = [];
  let traceStarted = false;
  let originalBefore;
  let originalAfter;
  let preview;
  let simulation;
  let published;
  let sessionToken;

  page.on('console', (message) => {
    const entry = { type: message.type(), text: message.text().slice(0, 500) };
    consoleEntries.push(entry);
    const resourceError = entry.text.startsWith('Failed to load resource:');
    if (message.type() === 'error' && !resourceError) failures.push(`console: ${entry.text}`);
  });
  page.on('requestfailed', (request) => {
    if (request.url().includes('/tbot/') && request.failure()?.errorText === 'net::ERR_ABORTED') return;
    failures.push(`requestfailed: ${request.method()} ${new URL(request.url()).pathname} ${request.failure()?.errorText || ''}`);
  });
  page.on('response', (response) => {
    const isAdmin = response.url().includes('/nestjs/v1/admin/');
    const pathName = new URL(response.url()).pathname;
    const entry = {
      method: response.request().method(),
      path: isAdmin ? adminPath(response.url()) : pathName,
      status: response.status(),
    };
    if (isAdmin) requests.push(entry);
    if (response.status() >= 400 && !isExpectedBrowserHttpFailure({
      url: response.url(), status: response.status(), traceStarted,
    })) failures.push(`http: ${entry.method} ${entry.path} ${entry.status}`);
  });

  await mkdir(artifactDir, { recursive: true });
  try {
    await loginWithRealAdminSession(page, fixture);
    sessionToken = await page.evaluate(() => localStorage.getItem('nestjs_session_token'));
    expect(sessionToken).toEqual(expect.any(String));
    await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
    traceStarted = true;

    originalBefore = await openCanonicalLesson(page);
    await cloneCanonicalLesson(page);
    await editRequiredFields(page);
    await reviewAndCloneSharedVisual(page);
    preview = await assertExactPreview(page);
    simulation = await runRequiredSimulations(page, preview.manifest);
    published = await publishAndAssertOriginalImmutable(page);

    await waitForAdminResponse(page, {
      method: 'GET',
      pathPattern: /\/v1\/admin\/courses$/,
    }, async () => {
      await page.getByRole('button', { name: /More/ }).click();
      await page.getByText('Courses', { exact: true }).click();
    });
    const canonicalAfter = await openCanonicalLesson(page);
    originalAfter = { checksum: canonicalAfter.checksum, pins: canonicalAfter.pins };
    expect(originalAfter).toEqual({ checksum: originalBefore.checksum, pins: originalBefore.pins });
    expect(failures, 'unexpected browser/network failures').toEqual([]);

    await page.screenshot({ path: path.join(artifactDir, 'final-original-immutable.png'), fullPage: true });
  } finally {
    if (traceStarted) {
      const { sanitizeTraceToDeliverable } = await lifecycleModulePromise;
      const rawTraceDirectory = process.env.REWARDS_ADMIN_RAW_TRACE_DIR;
      if (!rawTraceDirectory) throw new Error('Missing runner-owned raw trace directory');
      await mkdir(rawTraceDirectory, { recursive: true });
      const rawTracePath = path.join(rawTraceDirectory, 'trace.zip');
      const deliverableTracePath = path.join(artifactDir, 'trace.zip');
      try {
        await context.tracing.stop({ path: rawTracePath });
        await sanitizeTraceToDeliverable({
          rawTrace: rawTracePath,
          deliverableTrace: deliverableTracePath,
          sanitize: async (rawTrace, deliverableTrace) => {
            if (process.env.REWARDS_ADMIN_FAILURE_INJECTION === 'trace-sanitizer') {
              throw new Error('Injected trace sanitizer failure');
            }
            await sanitizeTraceArchive(rawTrace, deliverableTrace, [
              sessionToken,
              fixture.email,
              fixture.password,
              fixture.totp,
              'nestjs_session_token',
            ]);
          },
        });
      } finally {
        await rm(rawTracePath, { force: true });
      }
    }
    await writeFile(path.join(artifactDir, 'request-summary.json'), JSON.stringify({ requests, failures }, null, 2));
    await writeFile(path.join(artifactDir, 'console-summary.json'), JSON.stringify({ entries: consoleEntries }, null, 2));
    await writeFile(path.join(artifactDir, 'checksums.json'), JSON.stringify({
      canonicalV1Before: originalBefore && originalBefore.checksum,
      customizedV1: preview && preview.checksum,
      publishedV1: published && published.checksum,
      simulationChecksum: simulation && simulation.checksum,
    }, null, 2));
    await writeFile(path.join(artifactDir, 'all-profile-pinned-asset-comparison.json'), JSON.stringify({
      pass: Boolean(originalBefore && originalAfter
        && originalBefore.checksum === originalAfter.checksum
        && JSON.stringify(originalBefore.pins) === JSON.stringify(originalAfter.pins)),
      before: originalBefore && originalBefore.pins,
      after: originalAfter && originalAfter.pins,
    }, null, 2));
  }
});
