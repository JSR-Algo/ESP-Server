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
  temp = await mkdtemp(join(tmpdir(), 'tbot-fe01-'));
  const buildDir = join(temp, 'build');
  const profileDir = join(temp, 'chrome');
  await mkdir(profileDir, { recursive: true });

  const build = spawnSync(
    process.execPath,
    [
      join(managerRoot, 'node_modules/@vue/cli-service/bin/vue-cli-service.js'),
      'build', '--dest', buildDir, '--no-clean',
      join(managerRoot, 'tests/browser/course-insights-state-main.js'),
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
    label: 'FE01 state browser',
    onMessage: (message) => {
      if (message.method === 'Runtime.exceptionThrown') runtimeErrors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text);
    },
  }, async ({ cdp, evaluate, waitForReadiness }) => {

  await cdp('Page.enable');
  await cdp('Runtime.enable');
  await cdp('Page.navigate', { url: `http://127.0.0.1:${server.address().port}/` });
  await waitForReadiness('Boolean(window.__FE01_READY__)', 'course taxonomy fixture readiness', 'document.body.innerText');

  const result = await evaluate(`(async () => {
    const { root, router, calls: p, tick, messages } = window.__FE01__;
    const s = root.$children[0];
    const child = id => ({ childId: id, childName: 'Child '+id, personality: { interests: [] }, stats: {} });
    p.listLearners[0].ok([child('A'),child('B')]); p.getCourseQuality[0].ok([]); await tick();
    s.savePersonality();
    const rows = document.querySelectorAll('.learner-list .el-table__body-wrapper tbody tr');
    rows[1].click(); await tick();
    const input = document.querySelector('.personality-form input');
    input.value = 'B draft'; input.dispatchEvent(new Event('input',{bubbles:true})); await tick();
    p.previewLearnerLessons[1].ok({ lessons: [{ lessonId:'B',title:'B recommendation',matchedTopics:[] }] });
    p.previewLearnerLessons[0].ok({ lessons: [{ lessonId:'A',title:'A recommendation',matchedTopics:[] }] });
    p.updateLearnerPersonality[0].ok(child('A')); await tick();
    const selection = document.querySelector('.detail-head h3').textContent;
    const draft = input.value;
    const preview = document.querySelector('.preview-table').textContent;
    s.fetchLearners(); p.listLearners[1].ok([child('A'),child('B')]); await tick();
    const draftAfterRefresh = document.querySelector('.personality-form input').value;
    s.activeTab = 'quality'; s.fetchQuality(); s.fetchQuality();
    p.getCourseQuality[1].fail('obsolete'); await tick();
    const loading = s.qualityLoading;
    p.getCourseQuality[2].fail('503'); await tick();
    const unavailable = document.querySelector('.main-wrapper').textContent;
    const metric = document.querySelector('.quality-stats strong').textContent;
    s.fetchPreview();
    const oldPreview = p.previewLearnerLessons[p.previewLearnerLessons.length-1];
    await router.push('/lessons?courseId=A'); await tick();
    oldPreview.ok({lessons:[{title:'destroyed'}]}); oldPreview.fail('destroyed');
    const oldRows = p.listAuthoritativeLessons[0];
    await router.push('/lessons?courseId=B'); await tick();
    oldRows.ok([{lessonId:'A',title:'A lesson'}]);
    p.listAuthoritativeLessons[1].ok([{lessonId:'B',lessonKey:'B',title:'B lesson',topicTags:[]}]); await tick();
    return { selection, draft, preview, draftAfterRefresh, loading, unavailable, metric, messages,
      lessons: document.querySelector('.main-wrapper').textContent, destroyed: s.requestsDisposed };
  })()`);
  assert.equal(result.selection, 'Child B');
  assert.equal(result.draft, 'B draft');
  assert.equal(result.draftAfterRefresh, 'B draft');
  assert.match(result.preview, /B recommendation/);
  assert.doesNotMatch(result.preview, /A recommendation/);
  assert.equal(result.loading, true);
  assert.match(result.unavailable, /Failed to load course quality/);
  assert.equal(result.metric, '-');
  assert.equal(result.destroyed, true);
  assert.match(result.lessons, /B lesson/);
  assert.doesNotMatch(result.lessons, /A lesson/);
  assert.equal(result.messages.length, 2);
  assert.match(result.messages[0].message, /Child A/);
  assert.deepEqual(runtimeErrors, [], `page runtime errors: ${runtimeErrors.join('\n')}`);
  console.log('check-course-insights-state-browser: OK (14 assertions, controlled callbacks; no live API)');
  });
} finally {
  if (server) await new Promise((resolve) => server.close(resolve));
  if (temp) await rm(temp, { recursive: true, force: true });
}
