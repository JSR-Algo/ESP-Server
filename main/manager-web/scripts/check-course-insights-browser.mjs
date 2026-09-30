import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, webkit } from 'playwright-core';

const root = fileURLToPath(new URL('../', import.meta.url));
const temp = await mkdtemp(join(tmpdir(), 'course-insights-'));
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' };
let server;
try {
  const build = spawnSync(process.execPath, ['node_modules/@vue/cli-service/bin/vue-cli-service.js', 'build', '--dest', temp, '--no-clean', 'tests/browser/course-insights-main.js'], { cwd: root, encoding: 'utf8', timeout: 120000 });
  assert.equal(build.status, 0, build.stdout + build.stderr);
  server = createServer(async (req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const path = resolve(temp, pathname === '/' ? 'index.html' : `.${pathname}`);
    if (!path.startsWith(`${temp}/`)) { res.writeHead(403).end(); return; }
    try {
      const body = await readFile(path);
      res.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream' }).end(body);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  for (const [name, engine] of [['chromium', chromium], ['webkit', webkit]]) {
    const browser = await engine.launch({ headless: true });
    try {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.waitForFunction(() => window.__INSIGHTS_TEST__);
      const interests = page.locator('.personality-form .el-input input').first();
      await interests.fill('edited-a');
      await page.locator('.personality-form button.el-button--primary').click();
      await page.locator('.learner-list .el-table__body-wrapper tr').filter({ hasText: 'Learner B' }).click();
      await interests.fill('edited-b');
      await page.evaluate(() => { const t = window.__INSIGHTS_TEST__; t.calls.saves[0].ok({ ...t.rows[0], personality: { ...t.rows[0].personality, interests: ['edited-a'] } }); });
      assert.equal(await page.locator('.detail-head h3').textContent(), 'Learner B', 'late save A must not replace selected B');
      assert.equal(await interests.inputValue(), 'edited-b');
      await page.locator('.personality-form button.el-button--primary').click();
      assert.deepEqual(await page.evaluate(() => { const s = window.__INSIGHTS_TEST__.calls.saves[1]; return { childId: s.childId, interests: s.payload.interests }; }), { childId: 'B', interests: ['edited-b'] });
      console.log(`PASS ${name}: mounted save A/select B/save B keeps target and form aligned (controlled API)`);

      await page.evaluate(() => {
        const t = window.__INSIGHTS_TEST__;
        const lesson = title => ({ title, matchedTopics: [], suitabilityScore: 90 });
        t.calls.previews.filter(c => c.childId === 'B').at(-1).ok({ lessons: [lesson('B recommendation')] });
        t.calls.previews.filter(c => c.childId === 'A')[0].ok({ lessons: [lesson('A stale recommendation')] });
      });
      assert.ok((await page.locator('.preview-table').textContent()).includes('B recommendation'));
      assert.ok(!(await page.locator('.preview-table').textContent()).includes('A stale recommendation'));
      console.log(`PASS ${name}: mounted learner preview ignores late response (controlled API)`);

      await page.evaluate(() => {
        const t = window.__INSIGHTS_TEST__;
        t.view.activeTab = 'quality'; t.view.qualityWindow = 90; t.view.fetchQuality();
        t.view.qualityWindow = 7; t.view.fetchQuality();
        const quality = title => ({ title, qualityScore: 80, issueTags: [], riskLevel: 'healthy' });
        t.calls.quality.at(-1).ok([quality('Current seven days')]);
        t.calls.quality.at(-2).ok([quality('Stale ninety days')]);
      });
      assert.ok((await page.locator('.main-wrapper').textContent()).includes('Current seven days'));
      assert.ok(!(await page.locator('.main-wrapper').textContent()).includes('Stale ninety days'));
      console.log(`PASS ${name}: mounted quality window ignores late response (controlled API)`);
      assert.deepEqual(errors, []);
      assert.deepEqual(await page.evaluate(() => window.__INSIGHTS_TEST__.calls.errors), []);
    } finally { await browser.close(); }
  }
} finally {
  if (server) await new Promise(r => server.close(r));
  await rm(temp, { recursive: true, force: true });
}
