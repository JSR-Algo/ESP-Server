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

  assert.match(config, /baseURL: lessonStudioWebOrigin\(\)/);
  assert.doesNotMatch(config, /LESSON_STUDIO_E2E_BASE_URL \|\|/);
});

test('the primary Lesson Studio contract command includes reset isolation tests', () => {
  const packageJson = JSON.parse(readFileSync(resolve(__dirname, '../package.json'), 'utf8'));

  assert.match(
    packageJson.scripts['test:lesson-studio-compose'],
    /reset-lesson-studio-e2e-state\.test\.cjs/,
  );
});
