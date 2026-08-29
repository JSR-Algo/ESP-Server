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

function canonicalCourseModeContract(weekNumber = 1) {
  const root = canonicalBackendRoot();
  const source = [
    "const compiler=require('./dist/lessons/course-mode/curriculum-course-mode.js')",
    "const curriculum=require('./dist/lessons/course-mode/curriculum-6month.js')",
    `const week=curriculum.CURRICULUM[${Number(weekNumber) - 1}]`,
    "if(!week) throw new Error('unknown curriculum week')",
    "const versions=new Map([['scene.playground-park','11111111-1111-4111-8111-111111111111'],['object.no','22222222-2222-4222-8222-222222222222']])",
    'process.stdout.write(JSON.stringify(compiler.compileCurriculumCourseMode(week,compiler.buildAssetCatalog(versions))))',
  ].join(';');
  return JSON.parse(execFileSync(process.execPath, ['-e', source], {
    cwd: root,
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

async function createCourseModeDraft(page, { weekNumber = 1, runId } = {}) {
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
  const contract = canonicalCourseModeContract(weekNumber);
  await adminApi(page, 'PUT', `/lessons/${lesson.id}/course-mode`, { expectedChecksum: null, contract });
  const sourceLessons = ['00000006-0002-0000-0000-000000000001'];
  const sourceAssets = (await Promise.all(sourceLessons.map((sourceLessonId) => (
    adminApi(page, 'GET', `/lessons/${sourceLessonId}/assets?profile=espTft`)
  )))).flatMap((result) => result.assets);
  for (const assetKey of [
    'robotOverlay.teach',
    'robotOverlay.listening',
    'robotOverlay.celebrate',
  ]) {
    const sourceAssetId = sourceAssets.find((asset) => asset.assetKey === assetKey)?.assetId;
    expect(sourceAssetId, `seeded source asset must exist for ${assetKey}`).toBeTruthy();
    await adminApi(page, 'POST', `/lessons/${lesson.id}/assets`, {
      profile: 'espTft',
      sourceAssetId,
    });
  }
  return { course, lesson: await adminApi(page, 'GET', `/lessons/${lesson.id}`), contract, runId: suffix };
}

async function createVisualTriple(page, lessonId, runId, { bind = true } = {}) {
  const definitions = [
    ['backgroundScene', 'scene', 'scene.playground-park'],
    ['teachingObject', 'teachingObject', 'object.no'],
    ['robotOverlay', 'robotPose', `robot.e2e.${runId}`],
  ];
  const versions = {};
  const sourceBySlot = {
    backgroundScene: 'assets/t54-layered/background-farm.jpg',
    teachingObject: 'assets/objects/barn.png',
    robotOverlay: 'assets/t54-layered/robot-teach.mp4',
  };
  const compatibilityBySlot = {
    backgroundScene: {
      mediaKind: 'image', mediaType: 'image/jpeg', width: 480, height: 320,
      rect: { x: 0, y: 0, width: 480, height: 320 }, fit: 'cover',
    },
    teachingObject: {
      mediaKind: 'image', mediaType: 'image/png', width: 95, height: 95,
      rect: { x: 20, y: 168, width: 95, height: 95 }, fit: 'contain',
    },
    robotOverlay: {
      mediaKind: 'video', mediaType: 'video/mp4', codec: 'mjpeg', hasAudio: false,
      width: 240, height: 240, fps: 10, durationMs: 1000, frameCount: 10,
      rect: { x: 118, y: 160, width: 150, height: 150 },
      chromaKey: { keyColor: '#00ff00', tolerance: 20, featherPx: 1 },
    },
  };
  for (const [slot, category, assetKey] of definitions) {
    versions[slot] = await adminApi(page, 'POST', `/lesson-visual-assets/${encodeURIComponent(assetKey)}/versions`, {
      category,
      title: `E2E ${slot} ${runId}`,
      profile: 'espTft',
      storagePath: sourceBySlot[slot],
      sha256: slot === 'backgroundScene'
        ? 'd4abb6087dc3122e0a00feb5e6a86b03dc7db550eb59d25e92f54d0fd09e4fc0'
        : slot === 'teachingObject'
          ? 'eac30a7ddf3f14df79f27c3eb39f2114f3a780d5670bb11ef62446f5fa5dcbb9'
          : 'f2d496b5e750e895f7e086aec827d7b99d0bb322d73ea660a2e84ff484b602c4',
      mimeType: slot === 'backgroundScene' ? 'image/jpeg' : slot === 'robotOverlay' ? 'video/mp4' : 'image/png',
      bytes: slot === 'backgroundScene' ? 43599 : slot === 'teachingObject' ? 200618 : 223033,
      width: slot === 'backgroundScene' ? 480 : slot === 'robotOverlay' ? 240 : 95,
      height: slot === 'backgroundScene' ? 320 : slot === 'robotOverlay' ? 240 : 95,
      publicationState: 'published',
      compatibilityMetadata: compatibilityBySlot[slot],
    });
  }
  if (bind) {
    await adminApi(page, 'PUT', `/lessons/${lessonId}/visuals`, {
      backgroundAssetVersionId: versions.backgroundScene.id,
      objectAssetVersionId: versions.teachingObject.id,
      robotAssetVersionId: versions.robotOverlay.id,
    });
  }
  return versions;
}

async function createPublishableCourseModeVisuals(page, lessonId) {
  const source = await adminApi(
    page,
    'GET',
    '/lessons/00000006-0002-0000-0000-000000000001/assets?profile=espTft',
  );
  for (const assetKey of ['backgroundScene.poster', 'teachingObject.barn']) {
    const sourceAssetId = source.assets.find((asset) => asset.assetKey === assetKey)?.assetId;
    expect(sourceAssetId, `seeded source asset must exist for ${assetKey}`).toBeTruthy();
    await adminApi(page, 'POST', `/lessons/${lessonId}/assets`, {
      profile: 'espTft',
      sourceAssetId,
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
