const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
require('./real-service-media.test.cjs');
require('./reference-background.test.cjs');
require('./targeted-rollback-journey.test.cjs');

const helperPath = path.resolve(__dirname, '../../e2e/lesson-studio/helpers/admin-api.js');

function curriculumPage({ draft = true, courseKey = 'english-6month-4-6' } = {}) {
  const calls = [];
  const curriculum = { courseId: 'canonical-course', courseKey: 'english-6month-4-6',
    lessonKey: 'w01-greetings-politeness', sourceLessonId: 'published-w1' };
  const lesson = { id: 'draft-w1', course_id: curriculum.courseId, lesson_key: curriculum.lessonKey,
    lesson_version: 3, status: 'draft', manifest_version: 'teebot-lesson-renderer.v5' };
  return { calls, curriculum, page: {
    evaluate: async () => ({ manager: 'test-manager', nest: 'test-author' }),
    request: { fetch: async (url, options) => {
      calls.push({ url, method: options.method });
      assert.notEqual(url, '/nestjs/v1/admin/courses', 'curriculum fixture must not create a default demo');
      let data;
      if (url.endsWith('/courses/canonical-course')) data = { id: curriculum.courseId, course_key: courseKey };
      else if (url.endsWith('/courses/canonical-course/lessons')) data = [
        { id: 'published-w1', lesson_key: curriculum.lessonKey, lesson_version: 2, status: 'published' },
        ...(draft ? [lesson] : []),
      ];
      else if (url.endsWith('/lessons/published-w1/new-version')) data = lesson;
      else if (url.endsWith('/lessons/draft-w1')) data = lesson;
      else if (url.endsWith('/lessons/draft-w1/course-mode')) data = { contract: { activities: [{ activityId: 'retained-activity' }] } };
      else throw new Error(`unexpected request ${url}`);
      return { ok: () => true, status: () => 200, text: async () => '', json: async () => ({ data }) };
    } },
  } };
}

test('canonical curriculum fixture reuses its actual draft without creating or relabeling a demo', async () => {
  const fixture = curriculumPage();
  const result = await require(helperPath).createCourseModeDraft(fixture.page, { curriculum: fixture.curriculum });
  assert.equal(result.lesson.id, 'draft-w1');
  assert.equal(result.contract.activities[0].activityId, 'retained-activity');
  assert.equal(fixture.calls.some(call => call.method !== 'GET'), false);
});

test('canonical curriculum fixture branches the published source only when no draft exists', async () => {
  const fixture = curriculumPage({ draft: false });
  await require(helperPath).createCourseModeDraft(fixture.page, { curriculum: fixture.curriculum });
  assert.deepEqual(fixture.calls.filter(call => call.method === 'POST'), [
    { url: '/nestjs/v1/admin/lessons/published-w1/new-version', method: 'POST' },
  ]);
});

test('canonical curriculum fixture rejects a mismatched course identity before writing', async () => {
  const fixture = curriculumPage({ courseKey: 'unrelated-demo' });
  await assert.rejects(require(helperPath).createCourseModeDraft(fixture.page, { curriculum: fixture.curriculum }),
    /canonical curriculum course/);
  assert.equal(fixture.calls.some(call => call.method !== 'GET'), false);
});

test('author login registers its header challenge before manager login can trigger it', async () => {
  const filename = path.resolve(__dirname, '../../e2e/lesson-studio/helpers/session.js');
  const registered = [];
  const req = createRequire(filename);
  const expectStub = () => ({ toHaveAttribute: async () => {} });
  expectStub.poll = () => ({ toBe: async () => {} });
  const sandbox = { module: { exports: {} }, exports: {}, process,
    require: name => {
      if (name === '@playwright/test') return { expect: expectStub };
      if (name.endsWith('reset-lesson-studio-e2e-state.cjs')) return { resetLessonStudioE2EState() {} };
      if (name === './real-service-evidence') return { expectObservedFault: (_page, method, url, status) => registered.push([method, url, status]) };
      return req(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), sandbox, { filename });
  const page = {
    goto: async () => {}, getByRole: () => ({}), waitForResponse: () => new Promise(() => {}),
    getByTestId: id => ({ fill: async () => {}, click: async () => {
      assert.equal(id, 'manager-login-submit');
      assert.deepEqual(registered, [['GET', '/nestjs/v1/admin/lesson-rollout-capabilities', 401]]);
      throw new Error('end-of-prelogin-proof');
    } }),
  };
  await assert.rejects(sandbox.module.exports.loginAsLessonAuthor(page), /end-of-prelogin-proof/);
});

test('second-author fixture keeps ownership writes separate from the rollout asset reader', async () => {
  const calls = [];
  const req = createRequire(helperPath);
  const sandbox = { module: { exports: {} }, exports: {}, console,
    process: { env: { TBOT_BACKEND_WORKTREE: '/unit/backend' }, execPath: process.execPath },
    require: name => {
      if (name === 'node:fs') return { existsSync: () => true };
      if (name === 'node:child_process') return { execFileSync: () => JSON.stringify({ activities: [] }) };
      return req(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(helperPath, 'utf8'), sandbox, { filename: helperPath });
  function page(actor) {
    return {
      evaluate: async () => ({ manager: 'unit-manager', nest: actor }),
      request: { fetch: async (url, options) => {
        calls.push({ actor, url, ...options });
        if (url.includes('/lesson-visual-assets') || url.includes('/assets')) {
          assert.equal(actor, 'rollout-admin', 'only the admitted actor may read source media');
        } else {
          assert.equal(actor, 'owner', 'draft creation and contract writes retain the second author');
        }
        let data = { id: 'draft', visualChecksum: 'initial' };
        if (url.includes('/lesson-visual-assets')) data = [{ publicationState: 'published', assetKey: 'real-key' }];
        else if (url.includes('/assets')) data = { assets: [{ assetId: 'existing' }] };
        return { ok: () => true, status: () => 200, text: async () => '', json: async () => ({ data }) };
      } },
    };
  }
  await sandbox.module.exports.createCourseModeDraft(page('owner'), { visualPage: page('rollout-admin'), runId: 'unit' });
  assert.ok(calls.some(call => call.method === 'PUT' && call.actor === 'owner'));
  assert.ok(calls.some(call => call.url.includes('/lesson-visual-assets') && call.actor === 'rollout-admin'));
});

test('v5 fixture binding sends both tokens from one saved snapshot and all seven phases', async () => {
  const phases = ['flyIn', 'walk', 'teach', 'listen', 'thinking', 'celebrate', 'exit'];
  const selection = {
    ids: { background: 'background-pin', object: 'object-pin', ...Object.fromEntries(phases.map(p => [p, `${p}-pin`])) },
    robotAssetVersionIds: Object.fromEntries(phases.map(p => [p, `${p}-pin`])),
  };
  const calls = [];
  const req = createRequire(helperPath);
  const sandbox = { module: { exports: {} }, exports: {}, process, console,
    require: name => name === './s07-session' ? { publishedCourseModeSelection: async () => selection } : req(name),
  };
  vm.runInNewContext(fs.readFileSync(helperPath, 'utf8'), sandbox, { filename: helperPath });
  const page = {
    evaluate: async () => ({ manager: 'unit-only-manager', nest: 'unit-only-author' }),
    request: { fetch: async (url, options) => {
      calls.push({ url, ...options });
      const data = options.method === 'GET'
        ? { checksum: 'learning-snapshot', visualChecksum: 'visual-snapshot' }
        : { id: 'old-fixture-version' };
      return { ok: () => true, status: () => 200, text: async () => '', json: async () => ({ data }) };
    } },
  };
  await sandbox.module.exports.createVisualTriple(page, 'lesson', 'unit');
  const write = calls.find(call => call.method === 'PUT');
  assert.equal(write.data.expectedChecksum, 'learning-snapshot');
  assert.equal(write.data.expectedVisualChecksum, 'visual-snapshot');
  assert.deepEqual(JSON.parse(JSON.stringify(write.data.robotAssetVersionIds)), selection.robotAssetVersionIds);
  assert.equal(write.data.robotAssetVersionId, undefined);
  assert.equal(calls.some(call => call.method === 'POST'), false, 'binding cannot fabricate asset metadata');
});

test('both qualification suites collect both browser engines at the required viewports', () => {
  for (const file of ['playwright.config.js', 'playwright.lesson-studio.config.js']) {
    const config = require(path.resolve(__dirname, '../..', file));
    assert.deepEqual((config.projects || []).map(p => [p.use.browserName || p.use.defaultBrowserType, p.use.viewport]).sort(), [
      ['chromium', { width: 1440, height: 900 }], ['webkit', { width: 1440, height: 900 }],
      ['chromium', { width: 390, height: 844 }], ['webkit', { width: 390, height: 844 }],
    ].sort());
  }
  const course = require('../../playwright.config.js');
  const studio = require('../../playwright.lesson-studio.config.js');
  assert.notEqual(course.outputDir, studio.outputDir);
  assert.notEqual(course.reporter.find(r => r[0] === 'html')[1].outputFolder,
    studio.reporter.find(r => r[0] === 'html')[1].outputFolder);
});

test('real-service navigation setup never fulfills successful media or synthetic readiness', async () => {
  const { installCinematicTestRoutes } = require('../../e2e/lesson-studio/helpers/navigation');
  const routes = [];
  if (installCinematicTestRoutes) await installCinematicTestRoutes({ route: async (pattern) => routes.push(String(pattern)) });
  assert.deepEqual(routes, []);
});

test('direct reset cannot mutate services before candidate preflight and bounds every command', () => {
  const filename = path.resolve(__dirname, '../../scripts/reset-lesson-studio-e2e-state.cjs');
  const calls = [];
  const sandbox = { module: { exports: {} }, exports: {}, __dirname: path.dirname(filename), process: { env: {} },
    require: name => name === 'node:child_process' ? { spawnSync: (command, args, options) => {
      calls.push({ command, args, options });
      return { status: 1, stderr: 'preflight unavailable' };
    } } : createRequire(filename)(name),
  };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), sandbox, { filename });
  assert.throws(() => sandbox.module.exports.resetLessonStudioE2EState({ composeExecutable: '/isolated/compose', projectName: 'isolated-test', composeFile: '/isolated/compose.yml' }), /preflight unavailable/);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, '/isolated/compose');
  assert.ok(calls[0].args.includes('isolated-test'));
  assert.ok(calls[0].args.includes('/isolated/compose.yml'));
  assert.ok(calls[0].args.includes('ps'), 'read-only preflight must precede redis/SQL writes');
  assert.ok(calls[0].options.timeout > 0 && calls[0].options.timeout <= 15000);
});

test('journey rejects transport failures that produce no HTTP response', () => {
  const { EventEmitter } = require('node:events');
  const { observeJourney } = require('../../e2e/lesson-studio/helpers/real-service-evidence');
  const page = new EventEmitter();
  const journal = observeJourney(page);
  page.emit('requestfailed', { method: () => 'GET', resourceType: () => 'media', url: () => 'http://localhost/media.mp4', failure: () => ({ errorText: 'net::ERR_CONNECTION_REFUSED' }) });
  assert.equal(journal.evidence.requestFailures.length, 1);
  assert.equal(journal.evidence.requestFailures[0].resourceType, 'media');
  assert.throws(() => journal.assertHappyPath());
});

test('expected HTTP faults are exact, single-use and must actually occur', () => {
  const { EventEmitter } = require('node:events');
  const { observeJourney } = require('../../e2e/lesson-studio/helpers/real-service-evidence');
  const response = (status, pathname) => ({ status: () => status, request: () => ({ method: () => 'GET' }), url: () => `http://localhost${pathname}` });
  const page = new EventEmitter();
  const journal = observeJourney(page);
  journal.expectFault('GET', '/assets', 503, 'injected offline fault');
  assert.throws(() => journal.assertHappyPath(), 'unused allowance must fail');
  page.emit('response', response(503, '/assets'));
  journal.assertHappyPath();
  page.emit('response', response(503, '/assets'));
  assert.throws(() => journal.assertHappyPath(), 'a second failure is not covered');
  const otherPage = new EventEmitter();
  const other = observeJourney(otherPage);
  other.expectFault('GET', '/assets', 503, 'injected offline fault');
  otherPage.emit('response', response(503, '/lessons'));
  assert.throws(() => other.assertHappyPath(), 'unrelated paths must fail');
});

test('legacy monitor no longer suppresses unrelated 401 or validation/media failures', () => {
  const { EventEmitter } = require('node:events');
  const { monitorUnexpectedPageErrors } = require('../../e2e/lesson-studio/helpers/page-errors');
  for (const status of [400, 401, 422, 503]) {
    const page = new EventEmitter();
    const check = monitorUnexpectedPageErrors(page);
    page.emit('response', { status: () => status, request: () => ({ method: () => 'GET' }), url: () => 'http://localhost/nestjs/v1/admin/lessons/other/manifest-preview' });
    assert.throws(check, `unregistered ${status} must fail`);
  }
});
