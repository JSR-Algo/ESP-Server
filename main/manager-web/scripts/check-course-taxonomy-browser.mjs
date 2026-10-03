/**
 * check-course-taxonomy-browser.mjs
 *
 * Real-browser proof that the lesson metadata form warns an author when the
 * age band they chose leaves the assignment age gate UNENFORCED.
 *
 * The backend gate fails open on a band with no parseable leading integer
 * (`assertChildMeetsAgeBand` -> `if (minAge === null) return;`), so a typo like
 * 'K-2' silently removes the age restriction. check-course-taxonomy.cjs pins
 * the parse + template wiring statically; this drives the mounted component in
 * Chromium and asserts the warning actually RENDERS and clears reactively.
 *
 * Mutations that would turn this red:
 *  - Removing the v-if warning from CourseLessons.vue -> unenforcedVisible false.
 *  - Making ageBandSeverity return 'custom' for unparseable bands -> severity assert fails.
 *  - Reverting the create-dialog default to '6-8'/'en' -> defaults assert fails.
 */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { mkdir, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';
import { withCandidateBoundBrowser } from './_lib/candidate-browser-harness.mjs';

const managerRoot = normalize(join(dirname(fileURLToPath(import.meta.url)), '..'));
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.ico': 'image/x-icon' };
let temp; let server;

try {
  temp = await mkdtemp(join(tmpdir(), 'tbot-course-taxonomy-'));
  const buildDir = join(temp, 'build');
  const profileDir = join(temp, 'chrome');
  await mkdir(profileDir, { recursive: true });

  const build = spawnSync(
    process.execPath,
    [
      join(managerRoot, 'node_modules/@vue/cli-service/bin/vue-cli-service.js'),
      'build', '--dest', buildDir, '--no-clean',
      join(managerRoot, 'tests/browser/course-taxonomy-main.js'),
    ],
    { cwd: managerRoot, encoding: 'utf8', timeout: 180000 },
  );
  assert.equal(build.status, 0, `mounted harness build failed:\n${build.stdout}\n${build.stderr}`);

  server = createServer((request, response) => {
    const requestPath = request.url.split('?')[0];
    const path = normalize(join(buildDir, requestPath === '/' ? 'index.html' : requestPath));
    if (!path.startsWith(buildDir)) { response.writeHead(403).end(); return; }
    try {
      const body = readFileSync(path);
      response.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream' }).end(body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));

  const runtimeErrors = [];
  await withCandidateBoundBrowser({
    profileDir,
    label: 'Course taxonomy browser',
    onMessage: (message) => {
      if (message.method === 'Runtime.exceptionThrown') runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    },
  }, async ({ cdp, evaluate, waitForReadiness }) => {

  await cdp('Page.enable');
  await cdp('Runtime.enable');
  await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  await waitForReadiness('Boolean(window.__COURSE_TAXONOMY_READY__)', 'course taxonomy fixture readiness', 'document.body.innerText');

  // 1. The create dialog must default to values that exist in the seeded content.
  const defaults = await evaluate('window.__OPEN_CREATE__()');
  assert.equal(defaults.ageBand, '4-6', `create dialog must default to the seeded band, got ${defaults.ageBand}`);
  assert.equal(defaults.locale, 'en-US', `create dialog must default to the seeded locale, got ${defaults.locale}`);

  // 2. A canonical band is enforced and shows no warning.
  const canonical = await evaluate("window.__SET_AGE_BAND__('4-6')");
  assert.equal(canonical.severity, 'ok');
  assert.equal(canonical.minimum, 4);
  assert.equal(canonical.unenforcedVisible, false, 'canonical band must not warn');

  // 3. An unparseable band must RENDER the child-safety warning.
  for (const band of ['K-2', 'all ages', 'mẫu giáo', '']) {
    const unenforced = await evaluate(`window.__SET_AGE_BAND__(${JSON.stringify(band)})`);
    assert.equal(unenforced.severity, 'unenforced', `${band} must be classified unenforced`);
    assert.equal(unenforced.minimum, null, `${band} must have no parseable minimum`);
    assert.equal(
      unenforced.unenforcedVisible,
      true,
      `${band} leaves the assignment age gate unenforced and MUST render the warning`,
    );
    assert.match(
      unenforced.warningText,
      /NOT be enforced|KHÔNG được áp dụng/,
      `${band} warning must state the gate will not be enforced`,
    );
  }

  // 4. The warning clears reactively once a valid band is chosen again.
  // '5-7' is parseable but NOT in AGE_BANDS -> 'custom' (an author's own band).
  const recovered = await evaluate("window.__SET_AGE_BAND__('5-7')");
  assert.equal(recovered.severity, 'custom', 'parseable non-canonical band is custom, not unenforced');
  assert.equal(recovered.minimum, 5);
  assert.equal(recovered.unenforcedVisible, false, 'warning must clear when a parseable band is chosen');
  assert.equal(recovered.customVisible, true, 'custom band must explain the minimum it enforces');

  const switched = await evaluate('window.__SWITCH_COURSE__()');
  assert.equal(switched.requested, true, 'reused route must load the newly selected course');
  assert.equal(switched.closed, true, 'old course dialogs must close');
  assert.equal(switched.courseId, 'c0060000-0000-4000-8000-000000000002');
  assert.deepEqual(switched.titles, ['Current course lesson']);
  assert.ok(switched.text.includes('Current course lesson'));
  assert.ok(!switched.text.includes('Stale course lesson'));
  console.log('course route replacement: mounted Vue Router ignores old lesson response (controlled API)');

  assert.deepEqual(runtimeErrors, [], `page runtime errors: ${runtimeErrors.join('\n')}`);
  console.log('check-course-taxonomy-browser: OK');
  });
} finally {
  if (server) await new Promise((resolve) => server.close(resolve));
  if (temp) await rm(temp, { recursive: true, force: true });
}
