import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile, mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve, join, extname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, webkit } from 'playwright-core';

const root = fileURLToPath(new URL('../', import.meta.url));
const temp = await mkdtemp(join(tmpdir(), 'tvideo-media-errors-'));
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.mp4': 'video/mp4', '.webm': 'video/webm', '.png': 'image/png' };
let server;
let releaseDelayed;
let delayedRequest;
try {
  const build = spawnSync(process.execPath, ['node_modules/@vue/cli-service/bin/vue-cli-service.js', 'build', '--dest', temp, '--no-clean', 'tests/browser/tvideo-media-errors-main.js'], { cwd: root, encoding: 'utf8', timeout: 120000 });
  assert.equal(build.status, 0, build.stdout + build.stderr);
  server = createServer(async (req, res) => {
    let pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname.startsWith('/delayed/')) {
      delayedRequest = true;
      await new Promise(resolve => { releaseDelayed = resolve; });
      pathname = pathname.slice('/delayed'.length);
    }
    if (pathname.startsWith('/missing/')) { res.writeHead(404).end(); return; }
    if (pathname.startsWith('/broken/')) { res.writeHead(200, { 'content-type': mime[extname(pathname)] }); res.end('NOT_VALID_MEDIA'); return; }
    const path = resolve(temp, pathname === '/' ? 'index.html' : `.${pathname}`);
    if (!path.startsWith(`${temp}/`)) { res.writeHead(403).end(); return; }
    try { const body = await readFile(path); res.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream', 'content-length': body.length }); res.end(body); }
    catch { res.writeHead(404).end(); }
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  for (const name of (process.env.TBOT_MEDIA_TEST_ENGINES || 'chromium,webkit').split(',')) {
    assert.ok(['chromium', 'webkit'].includes(name), `Unknown engine ${name}`);
    const engine = name === 'webkit' ? webkit : chromium;
    const browser = await engine.launch({ headless: true });
    try {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(`http://127.0.0.1:${server.address().port}`);
      await page.waitForFunction(() => window.__MEDIA_TEST__);
      console.log(`Browser ${name}: ${browser.version()}`);
      await page.waitForFunction(() => window.__MEDIA_TEST__.preview.$refs.robot.readyState >= 2 && window.__MEDIA_TEST__.preview.alphaSupported !== null);
      const originalHasAlpha = await page.evaluate(() => {
        const video = window.__MEDIA_TEST__.preview.$refs.robot;
        const canvas = document.createElement('canvas'); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        const ctx = canvas.getContext('2d'); ctx.drawImage(video, 0, 0);
        return ctx.getImageData(0, 0, canvas.width, canvas.height).data.some((value, index) => index % 4 === 3 && value < 255);
      });
      assert.equal(await page.evaluate(() => window.__MEDIA_TEST__.preview.alphaSupported), originalHasAlpha, 'canary verdict must match this transparent original');
      if (!originalHasAlpha) {
        assert.equal(await page.locator('[role="alert"]').isVisible(), true, 'opaque VP9 decode must report unsupported alpha');
        assert.ok((await page.locator('[role="alert"]').textContent()).includes('VP9 transparency'));
        assert.equal(await page.locator('canvas').isVisible(), false);
        assert.equal(await page.evaluate(() => window.__MEDIA_TEST__.preview.playing), false);
        assert.deepEqual(errors, []);
        console.log(`PASS ${name}: native opaque VP9 decode is rejected visibly; no original-alpha compatibility claim`);
        continue;
      }
      for (const failure of ['broken', 'missing']) {
      for (const role of ['background', 'robot', 'object']) {
        await page.waitForFunction(() => { const refs = window.__MEDIA_TEST__.preview.$refs; return refs.background.readyState >= 2 && refs.robot.readyState >= 2 && refs.object.naturalWidth > 0; });
        await page.evaluate(() => window.__MEDIA_TEST__.preview.toggle());
        await page.waitForFunction(() => window.__MEDIA_TEST__.preview.clockMs >= 100);
        await page.evaluate(({ role, failure }) => { const t = window.__MEDIA_TEST__; t.root.urls[role] = `/${failure}/${role}.${role === 'object' ? 'png' : role === 'robot' ? 'webm' : 'mp4'}`; }, { role, failure });
        await page.waitForFunction(role => { const media = window.__MEDIA_TEST__.preview.$refs[role]; return role === 'object' ? media.complete && media.naturalWidth === 0 : Boolean(media.error); }, role);
        const result = await page.evaluate(() => { const p = window.__MEDIA_TEST__.preview; return { alert: p.$el.querySelector('[role="alert"]')?.textContent, playing: p.playing, timer: p.timer, clockMs: p.clockMs }; });
        assert.ok(result.alert?.includes('could not'), `${name}/${role}: actual browser decode failure must be visible`);
        assert.equal(result.playing, false);
        assert.equal(result.timer, null);
        assert.equal(await page.locator('[role="alert"]').isVisible(), true);
        assert.equal(await page.locator('canvas').isVisible(), false);
        await page.waitForTimeout(250);
        assert.equal(await page.evaluate(() => window.__MEDIA_TEST__.preview.clockMs), result.clockMs, 'failed preview must stop its active clock');
        await page.evaluate(role => { const t = window.__MEDIA_TEST__; t.root.urls[role] = t.urls[role]; }, role);
        await page.waitForFunction(role => { const media = window.__MEDIA_TEST__.preview.$refs[role]; return role === 'object' ? media.naturalWidth > 0 : media.readyState >= 2; }, role);
        await page.waitForFunction(() => !window.__MEDIA_TEST__.preview.$el.querySelector('[role="alert"]'));
        assert.equal(await page.locator('canvas').isVisible(), true);
        await page.evaluate(() => window.__MEDIA_TEST__.preview.replay());
        console.log(`PASS ${name}/${role}/${failure}: real HTTP failure, visible alert, hidden canvas, active clock stopped, original-source recovery`);
      }
      }
      for (const role of ['background', 'robot', 'object']) {
        await page.evaluate(() => window.__MEDIA_TEST__.preview.toggle());
        await page.waitForFunction(() => window.__MEDIA_TEST__.preview.playing);
        await page.evaluate(role => { window.__MEDIA_TEST__.root.urls[role] = ''; }, role);
        await page.waitForFunction(() => !window.__MEDIA_TEST__.preview.playing);
        assert.equal(await page.locator('[role="alert"]').isVisible(), true, `${role}: absent source must be visible`);
        assert.equal(await page.locator('canvas').isVisible(), false);
        await page.evaluate(() => window.__MEDIA_TEST__.preview.toggle());
        assert.equal(await page.evaluate(() => window.__MEDIA_TEST__.preview.playing), false);

        delayedRequest = false;
        await page.evaluate(role => { const t = window.__MEDIA_TEST__; t.root.urls[role] = `/delayed${t.urls[role]}`; }, role);
        await page.waitForFunction(() => Boolean(document.querySelector('[role="status"]')));
        assert.equal(await page.locator('canvas').isVisible(), false, `${role}: loading source must hide incomplete composite`);
        await page.evaluate(() => window.__MEDIA_TEST__.preview.toggle());
        assert.equal(await page.evaluate(() => window.__MEDIA_TEST__.preview.playing), false);
        assert.equal(await page.evaluate(() => window.__MEDIA_TEST__.preview.timer), null);
        assert.ok(delayedRequest, `${role}: real HTTP request must be pending`);
        releaseDelayed();
        releaseDelayed = null;
        await page.waitForFunction(() => !document.querySelector('[role="status"], [role="alert"]'));
        assert.equal(await page.locator('canvas').isVisible(), true);
        await page.evaluate(role => { const t = window.__MEDIA_TEST__; t.root.urls[role] = t.urls[role]; }, role);
        await page.waitForFunction(() => !document.querySelector('[role="status"], [role="alert"]'));
        console.log(`PASS ${name}/${role}: missing URL and delayed HTTP block playback and placeholder; original-source recovery`);
      }
      await page.evaluate(() => {
        const t = window.__MEDIA_TEST__;
        t.root.urls.walking = `/delayed${t.urls.walking}`;
        t.preview.clockMs = 3600;
        t.preview.toggle();
      });
      await page.waitForFunction(() => window.__MEDIA_TEST__.preview.robotRole === 'walking' && !window.__MEDIA_TEST__.preview.playing);
      assert.equal(await page.locator('canvas').isVisible(), false, 'phase clip replacement must wait for actual bytes');
      await page.waitForFunction(() => Boolean(document.querySelector('[role="status"]')));
      assert.ok(releaseDelayed, 'walking clip HTTP request must be pending');
      releaseDelayed(); releaseDelayed = null;
      await page.waitForFunction(() => window.__MEDIA_TEST__.preview.mediaReadyForPreview);
      assert.equal(await page.locator('canvas').isVisible(), true);
      await page.evaluate(() => window.__MEDIA_TEST__.preview.toggle());
      await page.waitForFunction(() => window.__MEDIA_TEST__.preview.clockMs > 3700);
      await page.evaluate(() => window.__MEDIA_TEST__.preview.toggle());
      console.log(`PASS ${name}: actual flight-to-walking clip replacement pauses, loads and resumes`);
      assert.deepEqual(errors, []);
    } finally { await browser.close(); }
  }
} finally {
  if (releaseDelayed) releaseDelayed();
  if (server) await new Promise(r => server.close(r));
  await rm(temp, { recursive: true, force: true });
}
