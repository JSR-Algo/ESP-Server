const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

function observeLaunches(mode, cache) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'mjpeg-launch-regression-'));
  try {
    const capture = path.join(temporary, 'launches.jsonl');
    const preload = path.join(temporary, 'preload.cjs');
    fs.writeFileSync(preload, `
      const Module = require('node:module');
      const fs = require('node:fs');
      const original = Module._load;
      Module._load = function(name, ...rest) {
        if (name !== '@playwright/test') return original.call(this, name, ...rest);
        return Object.fromEntries(['chromium', 'webkit'].map(engine => [engine, {
          launch: async options => {
            fs.appendFileSync(process.env.LAUNCH_CAPTURE, JSON.stringify({engine, options}) + '\\n');
            throw new Error('intentional launch boundary stop');
          }
        }]));
      };
    `);
    const env = { ...process.env, LAUNCH_CAPTURE: capture, MJPEG_REPLAY_OUTPUT: temporary };
    delete env.TBOT_MJPEG_REPLAY_CANDIDATE_BROWSER;
    delete env.PLAYWRIGHT_BROWSERS_PATH;
    if (mode !== undefined) env.TBOT_MJPEG_REPLAY_CANDIDATE_BROWSER = mode;
    if (cache !== undefined) env.PLAYWRIGHT_BROWSERS_PATH = cache;
    const result = spawnSync(process.execPath, ['--require', preload,
      path.resolve(__dirname, '../../scripts/check-mjpeg-replay-browser.cjs')],
    { env, encoding: 'utf8', timeout: 10000 });
    assert.equal(result.status, 1, result.stderr);
    return fs.existsSync(capture)
      ? fs.readFileSync(capture, 'utf8').trim().split('\n').map(JSON.parse) : [];
  } finally {
    fs.rmSync(temporary, { recursive: true, force: true });
  }
}

test('candidate replay launches frozen Chromium and WebKit without a host channel', () => {
  assert.deepEqual(observeLaunches('1', '/candidate/frozen-browser-cache'), [
    { engine: 'chromium', options: { headless: true } },
    { engine: 'webkit', options: { headless: true } },
  ]);
});

test('standalone replay retains Chrome and WebKit coverage', () => {
  assert.deepEqual(observeLaunches(undefined, undefined), [
    { engine: 'chromium', options: { headless: true, channel: 'chrome' } },
    { engine: 'webkit', options: { headless: true } },
  ]);
});

for (const [mode, cache] of [['1', undefined], ['1', 'relative-cache'], ['invalid', '/candidate/cache']]) {
  test(`candidate replay rejects invalid browser binding ${mode}/${cache}`, () => {
    assert.deepEqual(observeLaunches(mode, cache), []);
  });
}
