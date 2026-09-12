const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { createServer } = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { chromium, webkit } = require('@playwright/test');
const { visitPersistedPhases } = require('../e2e/lesson-studio/helpers/persisted-phases');
const manifest = require('../tests/fixtures/persisted-multi-activity-v5.json');

async function main() {
  const root = path.resolve(__dirname, '..');
  const output = process.env.LESSON_STUDIO_E2E_OUTPUT_ROOT
    || fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'tbot-persisted-phases-'));
  const buildDir = path.join(output, 'phase-selection-build');
  fs.mkdirSync(buildDir, { recursive: true });
  const build = spawnSync(process.execPath, [path.join(root, 'node_modules/@vue/cli-service/bin/vue-cli-service.js'), 'build', '--dest', buildDir, '--no-clean', 'tests/browser/persisted-phase-selection-main.js'], { cwd: root, encoding: 'utf8', timeout: 120000, env: { ...process.env, VUE_APP_USE_CDN: 'false' } });
  fs.writeFileSync(path.join(output, 'mounted-build.log'), `${build.stdout}\n${build.stderr}`);
  assert.equal(build.status, 0, 'mounted selection harness must build');
  const server = createServer((req, res) => {
    const file = path.join(buildDir, new URL(req.url, 'http://localhost').pathname === '/' ? 'index.html' : new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(buildDir + path.sep) || !fs.existsSync(file)) return res.writeHead(404).end();
    const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime }); res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const evidence = [];
  try {
    const projects = require('../playwright.config').projects;
    for (const project of projects) {
      const engine = project.use.defaultBrowserType || project.use.browserName;
      const { defaultBrowserType, browserName, ...device } = project.use;
      const dir = path.join(output, project.name); fs.mkdirSync(dir);
      const row = { project: project.name, viewport: device.viewport, cases: [], pageErrors: [], blockedMediaRequests: [] };
      let browser;
      let context;
      try {
        browser = await ({ chromium, webkit })[engine].launch();
        row.browserVersion = browser.version();
        context = await browser.newContext({ ...device, serviceWorkers: 'block' });
        await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
        const page = await context.newPage(); page.setDefaultTimeout(2500);
        page.on('pageerror', e => row.pageErrors.push(e.message));
        // Explicit isolation: selection/projection proof does not fetch broken shared media or qualify pixels.
        await page.route('**/*', route => {
          if (new URL(route.request().url()).origin === `http://127.0.0.1:${server.address().port}`) return route.continue();
          row.blockedMediaRequests.push(route.request().url()); return route.abort('blockedbyclient');
        });
        try {
          await page.goto(`http://127.0.0.1:${server.address().port}`);
          assert.ok(manifest.steps.length > 1);
          await visitPersistedPhases(page, manifest, async ({ phase, activityId, stepIndex }) => {
            row.cases.push({ activityId, stepIndex, phaseId: phase.phaseId, layers: phase.layers.map(l => ({ slot: l.slot, assetVersionId: l.assetVersionId, sha256: l.sha256 })) });
          });
          // Persisted records can belong to several activities: 19 records expand to 43 visits.
          assert.equal(row.cases.length, 43);
          assert.equal(new Set(row.cases.map(c => `${c.activityId}/${c.phaseId}`)).size, 43);
          assert.deepEqual([...new Set(row.cases.map(c => c.phaseId))].sort(), ['flyIn', 'walk', 'teach', 'listen', 'thinking', 'celebrate', 'exit'].sort());
          assert.equal(row.cases.find(c => c.phaseId === 'exit').activityId, manifest.steps.at(-1).id);
          assert.deepEqual(row.pageErrors, []);
          row.status = 'PASS_SELECTION_ONLY';
        } finally {
          try { await page.screenshot({ path: path.join(dir, 'selection.png'), fullPage: true }); }
          finally { await context.tracing.stop({ path: path.join(dir, 'trace.zip') }); }
        }
      } catch (e) { row.status = 'FAIL'; row.error = e.message; }
      finally {
        try { if (context) await context.close(); }
        finally { if (browser) await browser.close(); }
      }
      evidence.push(row);
    }
  } finally { await new Promise(resolve => server.close(resolve)); }
  fs.writeFileSync(path.join(output, 'phase-selection-results.json'), JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence.map(r => ({ project: r.project, status: r.status, cases: r.cases.length, error: r.error })), null, 2));
  assert.equal(evidence.filter(r => r.status === 'FAIL').length, 0, 'every activity-scoped phase must be reachable');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
