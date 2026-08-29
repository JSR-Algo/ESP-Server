const assert = require('node:assert/strict');
const { existsSync, readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');

const helperPath = resolve(__dirname, 'lesson-studio-e2e-environment.cjs');

test('shared E2E web origin helper exists and follows the configured Playwright origin', () => {
  assert.equal(existsSync(helperPath), true, 'shared E2E web origin helper must exist');
  const { lessonStudioAssetUrl, lessonStudioWebOrigin } = require(helperPath);

  const env = { LESSON_STUDIO_E2E_BASE_URL: 'http://127.0.0.1:18102' };
  assert.equal(lessonStudioWebOrigin(env), 'http://127.0.0.1:18102');
  assert.equal(
    lessonStudioAssetUrl('esp-tft/example.png', env),
    'http://127.0.0.1:18102/tvideo-demo/esp-tft/example.png',
  );
  assert.equal(lessonStudioWebOrigin({
    LESSON_STUDIO_E2E_BASE_URL: 'http://127.0.0.1:18102',
    LESSON_STUDIO_E2E_WEB_ORIGIN: 'http://127.0.0.1:28102',
  }), 'http://127.0.0.1:28102');
  assert.equal(lessonStudioWebOrigin({
    LESSON_STUDIO_E2E_WEB_HOST_PORT: '38102',
  }), 'http://127.0.0.1:38102');
});

test('shared E2E web origin rejects unsafe origins and asset paths', () => {
  assert.equal(existsSync(helperPath), true, 'shared E2E web origin helper must exist');
  const { lessonStudioAssetUrl, lessonStudioWebOrigin } = require(helperPath);

  for (const origin of [
    'file:///tmp/e2e',
    'http://user:pass@127.0.0.1:18102',
    'http://127.0.0.1:18102/base',
    'http://127.0.0.1:18102/?query=1',
    'https://admin.tjbot.vn',
    'https://example.test',
  ]) {
    assert.throws(() => lessonStudioWebOrigin({
      LESSON_STUDIO_E2E_BASE_URL: origin,
    }), /safe HTTP\(S\) origin/);
  }
  for (const port of ['0', '65536', 'not-a-port']) {
    assert.throws(() => lessonStudioWebOrigin({
      LESSON_STUDIO_E2E_WEB_HOST_PORT: port,
    }), /valid TCP port/);
  }
  for (const path of [
    '../secret',
    '/absolute.png',
    'https://attacker.invalid/asset.png',
    'esp-tft/%2e%2e/secret',
    'esp-tft%2fsecret.png',
    'esp-tft\\secret.png',
    'esp-tft/asset.png?token=secret',
  ]) {
    assert.throws(() => lessonStudioAssetUrl(path, {}), /safe relative asset path/);
  }
});

test('canonical roundtrip has no fixed localhost port and uses the shared asset URL helper', () => {
  const spec = readFileSync(
    resolve(__dirname, '../e2e/lesson-studio/canonical-roundtrip.spec.js'),
    'utf8',
  );

  assert.doesNotMatch(spec, /127\.0\.0\.1:8102/);
  assert.match(spec, /lessonStudioAssetUrl\(path\)/);
});

test('Playwright baseURL uses the same validated web origin helper', () => {
  const config = readFileSync(resolve(__dirname, '../playwright.config.js'), 'utf8');
  const orchestrator = readFileSync(resolve(__dirname, 'run-task4-assignment-phase.cjs'), 'utf8');

  assert.match(config, /baseURL: lessonStudioWebOrigin\(\)/);
  assert.match(config, /ignoreHTTPSErrors: true/);
  assert.doesNotMatch(config, /LESSON_STUDIO_E2E_BASE_URL \|\|/);
  assert.match(orchestrator, /const mediaHostname = 'task4-media\.localhost'/);
  assert.doesNotMatch(orchestrator, /admin\.tjbot\.vn/);
});

test('the primary Lesson Studio contract command includes reset isolation tests', () => {
  const packageJson = JSON.parse(readFileSync(resolve(__dirname, '../package.json'), 'utf8'));

  assert.match(
    packageJson.scripts['test:lesson-studio-compose'],
    /reset-lesson-studio-e2e-state\.test\.cjs/,
  );
});

test('legacy Lesson Studio E2E keeps its full suite separate from Course Mode', () => {
  const packageJson = JSON.parse(readFileSync(resolve(__dirname, '../package.json'), 'utf8'));
  const legacyConfigPath = resolve(__dirname, '../playwright.lesson-studio.config.js');

  assert.equal(existsSync(legacyConfigPath), true, 'legacy Playwright config must exist');
  assert.equal(
    packageJson.scripts['test:e2e:lesson-studio'],
    'playwright test --config=playwright.lesson-studio.config.js',
  );
  assert.doesNotMatch(readFileSync(legacyConfigPath, 'utf8'), /testMatch/);
});

test('base Lesson Studio stack enables Course Mode publishing for lifecycle E2E', () => {
  const compose = readFileSync(
    resolve(__dirname, '../../../docs/docker/docker-compose.lesson-studio-e2e.yml'),
    'utf8',
  );

  assert.match(compose, /COURSE_MODE_V2_PUBLISH_ENABLED:\s*"true"/);
  assert.match(compose, /until mysql[\s\S]*SELECT 1 FROM sys_user LIMIT 1[\s\S]*seed-mysql\.sql/);
});

test('Course Mode compiler requires an explicit candidate backend worktree', () => {
  const helper = readFileSync(
    resolve(__dirname, '../e2e/lesson-studio/helpers/admin-api.js'),
    'utf8',
  );

  assert.match(helper, /const root = process\.env\.TBOT_BACKEND_WORKTREE;/);
  assert.doesNotMatch(helper, /\.\.\/\.\.\/\.\.\/\.\.\/\.\.\/\.\.\/tbot-backend|\.\.\/\.\.\/\.\.\/\.\.\/tbot-backend/);
  const compose = readFileSync(
    resolve(__dirname, '../../../docs/docker/docker-compose.lesson-studio-e2e.yml'),
    'utf8',
  );
  assert.match(compose, /TBOT_BACKEND_WORKTREE:\?set the built candidate backend worktree/);
  assert.doesNotMatch(compose, /TBOT_BACKEND_WORKTREE:-/);
  assert.match(compose, /TBOT_LESSON_STUDIO_BACKEND_IMAGE:\?set the candidate backend image/);
  assert.match(compose, /TBOT_LESSON_STUDIO_WEB_IMAGE:\?set the candidate web image/);
});

test('security fixture provisions a distinct valid Nest manager principal', () => {
  const seed = readFileSync(
    resolve(__dirname, '../../../docs/docker/lesson-studio-e2e/seed-postgres.sql'),
    'utf8',
  );

  assert.match(seed, /lesson-manager-e2e@local\.invalid/);
  assert.match(seed, /lesson-author-b-e2e@local\.invalid/);
  assert.match(seed, /00000006-0099-4000-8000-000000000002/);
  assert.match(seed, /'support_agent'/);
  assert.doesNotMatch(seed, /\n\s*'manager',\n/);
});

test('security journey uses the real manager session and exact authorization outcomes', () => {
  const lifecycle = readFileSync(
    resolve(__dirname, '../e2e/lesson-studio/course-mode-lifecycle.spec.js'),
    'utf8',
  );

  assert.match(lifecycle, /lesson-manager-e2e@local\.invalid/);
  assert.match(lifecycle, /lesson-author-b-e2e@local\.invalid/);
  assert.match(lifecycle, /expect\(ownScoped\.status\(\)\)\.toBe\(404\)/);
  assert.match(lifecycle, /00000006-0002-0000-0000-000000000001/);
  assert.match(lifecycle, /expect\(managerOnly\.status\(\)\)\.toBe\(403\)/);
  assert.match(lifecycle, /expect\(idor\.status\(\)\)\.toBe\(403\)/);
  assert.match(lifecycle, /expect\(assignment\.status\(\)\)\.toBe\(403\)/);
  assert.doesNotMatch(lifecycle, /\[403, 404, 409\]/);
});
