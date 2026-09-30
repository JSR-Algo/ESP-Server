const { expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const { existsSync } = require('node:fs');
const { resolve } = require('node:path');

const apiRoot = '/nestjs/v1/admin';

/**
 * Both credentials the `/nestjs/` proxy needs:
 *  - `Authorization` is the manager bearer nginx validates with auth_request
 *    (`/tbot/user/proxy-auth`). Without it every request is 401 at the edge and
 *    never reaches NestJS.
 *  - `X-Nest-Authorization` is the Nest author session; the proxy promotes it to
 *    `Authorization` upstream (a browser-set `Authorization` would be overwritten).
 *
 * `page.request` shares cookies with the page but not localStorage, so both
 * tokens have to be read out of the page and passed explicitly.
 */
async function adminAuthHeaders(page) {
  const tokens = await page.evaluate(() => ({
    manager: (JSON.parse(localStorage.getItem('token') || 'null') || {}).token || '',
    nest: localStorage.getItem('nestjs_session_token') || '',
  }));
  expect(tokens.manager, 'manager session token must exist after UI login').toBeTruthy();
  expect(tokens.nest, 'real Nest Author session token must exist after UI login').toBeTruthy();
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${tokens.manager}`,
    'X-Nest-Authorization': `Bearer ${tokens.nest}`,
  };
}

/** Authenticated admin API call through the same-origin `/nestjs/` proxy. */
async function adminApi(page, method, path, data) {
  const headers = await adminAuthHeaders(page);
  const response = await page.request.fetch(`${apiRoot}${path}`, { method, data, headers });
  expect(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`).toBe(true);
  const body = await response.json();
  return body.data;
}

/** Authenticated call that leaves status assertions to the journey. */
async function adminApiResponse(page, method, path, data, headers = {}) {
  const auth = await adminAuthHeaders(page);
  return page.request.fetch(`${apiRoot}${path}`, {
    method,
    data,
    headers: { ...auth, ...headers },
  });
}

function canonicalBackendRoot() {
  const root = process.env.TBOT_BACKEND_WORKTREE;
  if (!root || !existsSync(resolve(root, 'dist/lessons/course-mode/curriculum-course-mode.js'))) {
    throw new Error('Set TBOT_BACKEND_WORKTREE to the built candidate backend worktree');
  }
  return root;
}

function canonicalCourseModeContract(weekNumber = 1, publishedAssetKeys = []) {
  const root = canonicalBackendRoot();
  const source = [
    "const compiler=require('./dist/lessons/course-mode/curriculum-course-mode.js')",
    "const curriculum=require('./dist/lessons/course-mode/curriculum-6month.js')",
    `const week=curriculum.CURRICULUM[${Number(weekNumber) - 1}]`,
    "if(!week) throw new Error('unknown curriculum week')",
    "const keys=JSON.parse(require('node:fs').readFileSync(0,'utf8'))",
    "const versions=new Map(keys.map(key=>[key,key]))",
    'process.stdout.write(JSON.stringify(compiler.compileCurriculumCourseMode(week,compiler.buildAssetCatalog(versions))))',
  ].join(';');
  return JSON.parse(execFileSync(process.execPath, ['-e', source], {
    cwd: root,
    input: JSON.stringify(publishedAssetKeys),
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
  }));
}

function withCanonicalCourseModeChecksum(contract) {
  const root = canonicalBackendRoot();
  const source = [
    "const fs=require('node:fs')",
    "const compiler=require('./dist/lessons/course-mode/course-mode.contract.js')",
    "const contract=JSON.parse(fs.readFileSync(0,'utf8'))",
    'delete contract.contractChecksum',
    'contract.contractChecksum=compiler.computeCourseModeChecksum(contract)',
    'process.stdout.write(JSON.stringify(contract))',
  ].join(';');
  return JSON.parse(execFileSync(process.execPath, ['-e', source], {
    cwd: root,
    input: JSON.stringify(contract),
    encoding: 'utf8',
    maxBuffer: 1024 * 1024,
  }));
}

async function createCurriculumDraft(page, curriculum, runId) {
  expect(curriculum.courseKey, 'canonical curriculum course is required').toBe('english-6month-4-6');
  expect(curriculum.lessonKey, 'selected canonical W1 is required').toBe('w01-greetings-politeness');
  expect(curriculum.courseId).toBeTruthy();
  expect(curriculum.sourceLessonId).toBeTruthy();
  const course = await adminApi(page, 'GET', `/courses/${curriculum.courseId}`);
  expect(course.course_key, 'canonical curriculum course identity must match').toBe(curriculum.courseKey);
  const lessons = await adminApi(page, 'GET', `/courses/${course.id}/lessons`);
  const versions = lessons.filter(lesson => lesson.lesson_key === curriculum.lessonKey);
  expect(versions.some(lesson => lesson.id === curriculum.sourceLessonId && lesson.status === 'published'),
    'the observed published W1 source must remain available').toBe(true);
  const drafts = versions.filter(lesson => lesson.status === 'draft');
  expect(drafts.length, 'ambiguous canonical W1 drafts').toBeLessThanOrEqual(1);
  const published = versions.filter(lesson => lesson.status === 'published')
    .sort((a, b) => Number(b.lesson_version) - Number(a.lesson_version))[0];
  const selected = drafts[0] || await adminApi(page, 'POST', `/lessons/${published.id}/new-version`, {});
  const lesson = await adminApi(page, 'GET', `/lessons/${selected.id}`);
  expect(lesson.course_id).toBe(course.id);
  expect(lesson.lesson_key).toBe(curriculum.lessonKey);
  expect(lesson.status).toBe('draft');
  expect(lesson.manifest_version).toBe('teebot-lesson-renderer.v5');
  const { contract } = await adminApi(page, 'GET', `/lessons/${lesson.id}/course-mode`);
  expect(contract.activities.length).toBeGreaterThan(0);
  return { course, lesson, contract, runId: runId || `curriculum-${Date.now().toString(36)}` };
}

async function createCourseModeDraft(page, { weekNumber = 1, runId, visualPage = page, curriculum } = {}) {
  if (curriculum) return createCurriculumDraft(page, curriculum, runId);
  const suffix = runId || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  const course = await adminApi(page, 'POST', '/courses', {
    courseKey: `e2e-course-mode-${suffix}`,
    title: `Course Mode E2E ${suffix}`,
    locale: 'en-US',
    ageBand: '4-6',
  });
  const lesson = await adminApi(page, 'POST', `/courses/${course.id}/lessons`, {
    lessonKey: `e2e-course-mode-w${String(weekNumber).padStart(2, '0')}-${suffix}`,
    title: `Course Mode week ${weekNumber} ${suffix}`,
    locale: 'en-US',
    ageBand: '4-6',
    rendererVersion: 'teebot-lesson-renderer.v5',
    estimatedDurationSec: 480,
    durationPreset: 8,
  });
  const catalog = await adminApi(visualPage, 'GET', '/lesson-visual-assets?profile=espTft');
  const contract = canonicalCourseModeContract(weekNumber, catalog
    .filter(asset => (asset.publication_state || asset.publicationState) === 'published')
    .map(asset => asset.asset_key || asset.assetKey));
  const initialVisuals = await adminApi(page, 'GET', `/lessons/${lesson.id}/visuals`);
  await adminApi(page, 'PUT', `/lessons/${lesson.id}/course-mode`, { expectedChecksum: null, expectedVisualChecksum: initialVisuals.visualChecksum, contract });
  await createPublishableCourseModeVisuals(visualPage, lesson.id);
  return { course, lesson: await adminApi(page, 'GET', `/lessons/${lesson.id}`), contract, runId: suffix };
}

async function createVisualTriple(page, lessonId, _runId, { bind = true } = {}) {
  const selection = await require('./s07-session').publishedCourseModeSelection(page, lessonId);
  if (bind) {
    const snapshot = await adminApi(page, 'GET', `/lessons/${lessonId}/visuals`);
    await adminApi(page, 'PUT', `/lessons/${lessonId}/visuals`, {
      expectedChecksum: snapshot.checksum,
      expectedVisualChecksum: snapshot.visualChecksum,
      backgroundAssetVersionId: selection.ids.background,
      ...(selection.ids.object ? { objectAssetVersionId: selection.ids.object } : {}),
      robotAssetVersionIds: selection.robotAssetVersionIds,
    });
  }
  return selection;
}

async function createPublishableCourseModeVisuals(page, lessonId) {
  const sourceId = process.env.LESSON_STUDIO_E2E_VISUAL_SOURCE_LESSON_ID
    || '00000006-0002-0000-0000-000000000001';
  const source = await adminApi(page, 'GET', `/lessons/${sourceId}/assets?profile=espTft`);
  expect(source.assets.length, 'selected source must have an actual asset bundle').toBeGreaterThan(0);
  const current = await adminApi(page, 'GET', `/lessons/${lessonId}/assets?profile=espTft`);
  const existing = new Set(current.assets.map(asset => asset.assetId));
  for (const asset of source.assets) {
    if (existing.has(asset.assetId)) continue;
    await adminApi(page, 'POST', `/lessons/${lessonId}/assets`, {
      profile: 'espTft', sourceAssetId: asset.assetId,
    });
  }
}

/**
 * Same call, issued from inside the page (so a spec can assert on what the SPA's
 * own origin sees). Both tokens are read in-page; a bare `X-Nest-Authorization`
 * fetch is rejected by nginx's auth_request before it reaches NestJS.
 */
async function nestFetch(page, path, { method = 'GET', body } = {}) {
  return page.evaluate(async ({ path: p, method: m, body: b, root }) => {
    const stored = JSON.parse(localStorage.getItem('token') || 'null') || {};
    const nest = localStorage.getItem('nestjs_session_token');
    const headers = {};
    if (stored.token) headers.Authorization = `Bearer ${stored.token}`;
    if (nest) headers['X-Nest-Authorization'] = `Bearer ${nest}`;
    let requestBody;
    if (b !== undefined) {
      headers['Content-Type'] = 'application/json';
      requestBody = JSON.stringify(b);
    }
    const response = await fetch(`${root}${p}`, { method: m, headers, body: requestBody });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new Error(`${m} ${p} failed (${response.status}): ${JSON.stringify(payload)}`);
    return payload && Object.prototype.hasOwnProperty.call(payload, 'data') ? payload.data : payload;
  }, { path, method, body, root: apiRoot });
}

module.exports = {
  adminApi,
  adminApiResponse,
  adminAuthHeaders,
  apiRoot,
  canonicalCourseModeContract,
  createCourseModeDraft,
  createPublishableCourseModeVisuals,
  createVisualTriple,
  nestFetch,
  withCanonicalCourseModeChecksum,
};
