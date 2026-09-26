const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { pathToFileURL } = require('node:url');
const { createHash } = require('node:crypto');
const { chromium, webkit, expect } = require('@playwright/test');

async function main() {
  const root = path.resolve(__dirname, '../public/tvideo-demo');
  const output = path.resolve(process.env.REFERENCE_MEDIA_EVIDENCE || 'test-results/reference-media');
  fs.mkdirSync(output, { recursive: true });
  const results = [];
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    const bytes = fs.readFileSync(file);
    const range = /bytes=(\d+)-(\d*)/.exec(req.headers.range || '');
    const start = range ? Number(range[1]) : 0;
    const end = range && range[2] ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
    const type = { '.html': 'text/html', '.png': 'image/png', '.mov': 'video/quicktime', '.webm': 'video/webm' }[path.extname(file)];
    res.writeHead(range ? 206 : 200, { 'Content-Type': type || 'application/octet-stream',
      'Content-Length': end - start + 1, 'Accept-Ranges': 'bytes',
      ...(range ? { 'Content-Range': `bytes ${start}-${end}/${bytes.length}` } : {}) });
    if (!/\.(mov|webm)$/.test(file)) { res.end(bytes.subarray(start, end + 1)); return; }
    // Cold, chunked delivery exposes native tail-index range cancellations.
    let cursor = start;
    const timer = setInterval(() => {
      const next = Math.min(cursor + 16384, end + 1);
      res.write(bytes.subarray(cursor, next)); cursor = next;
      if (cursor > end) { clearInterval(timer); res.end(); }
    }, 10);
    res.on('close', () => clearInterval(timer));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    for (const [engineName, engine] of [['chromium', chromium], ['webkit', webkit]]) {
      const browser = await engine.launch();
      try {
        for (const mobile of [false, true]) {
          const name = `${engineName}-${mobile ? 'mobile' : 'desktop'}`;
          const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: mobile });
          await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
          const page = await context.newPage();
          const row = { name, failures: [], pageErrors: [], httpErrors: [] }; results.push(row);
          page.on('requestfailed', r => row.failures.push({ url: r.url(), error: r.failure()?.errorText }));
          page.on('pageerror', e => row.pageErrors.push(e.message));
          page.on('response', r => { if (r.status() >= 400) row.httpErrors.push({ url: r.url(), status: r.status() }); });
          try {
            await page.goto(`http://127.0.0.1:${server.address().port}/index.html?embed=1`);
            await expect.poll(() => page.evaluate(() => ['flightv', 'greetv', 'walkv'].every(id => document.getElementById(id).readyState >= 2)), { timeout: 20000 }).toBe(true);
            const greetingSource = await page.locator('#greetv').evaluate(v => v.currentSrc);
            await page.evaluate(() => window.postMessage({ type: 'tvideo-params', payload: { motion: 'celebrate' } }, location.origin));
            await expect.poll(() => page.locator('#greetv').evaluate((v, previous) => v.currentSrc !== previous && v.readyState >= 2 && v.duration > 0, greetingSource), { timeout: 20000 }).toBe(true);
            await page.locator('#greetv').evaluate(v => v.play());
            await expect.poll(() => page.locator('#greetv').evaluate(v => v.currentTime)).toBeGreaterThan(0.3);
            await page.evaluate(() => window.postMessage({ type: 'tvideo-params', payload: { motion: 'greet' } }, location.origin));
            await expect.poll(() => page.locator('#greetv').evaluate((v, expected) => v.currentSrc === expected && v.readyState >= 2, greetingSource), { timeout: 20000 }).toBe(true);
            await page.evaluate(() => document.getElementById('replay').click());
            await expect.poll(() => page.locator('#flightv').evaluate(v => v.currentTime), { timeout: 10000 }).toBeGreaterThan(0.3);
            row.media = await page.locator('#flightv, #greetv, #walkv').evaluateAll(vs => vs.map(v => ({ id: v.id, width: v.videoWidth, height: v.videoHeight, duration: v.duration, error: v.error?.code || null })));
            assert.ok(row.media.every(v => v.width > 0 && v.height > 0 && !v.error));
            row.decoded = await page.locator('#flightv, #greetv, #walkv').evaluateAll(async vs => Promise.all(vs.map(async v => {
              const bytes = Uint8Array.from(atob(v.currentSrc.split(',')[1]), c => c.charCodeAt(0));
              const digest = await crypto.subtle.digest('SHA-256', bytes);
              const canvas = document.createElement('canvas'); canvas.width = v.videoWidth; canvas.height = v.videoHeight;
              const ctx = canvas.getContext('2d'); ctx.drawImage(v, 0, 0);
              const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
              let transparent = 0, visible = 0;
              for (let i = 3; i < pixels.length; i += 4) { if (pixels[i] === 0) transparent++; if (pixels[i] > 0) visible++; }
              return { id: v.id, sha256: Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join(''), transparent, visible };
            })));
            for (const sample of row.decoded) {
              const clip = { flightv: 'flight-in', greetv: 'greet-loop', walkv: 'walk-toward' }[sample.id];
              const original = fs.readFileSync(path.join(root, 'assets/robot-alive/flight', `${clip}.${engineName === 'webkit' ? 'mov' : 'webm'}`));
              assert.equal(sample.sha256, createHash('sha256').update(original).digest('hex'));
              assert.ok(sample.transparent > 0 && sample.visible > 0, `${sample.id} must retain decoded alpha and visible content`);
            }
            assert.deepEqual(row.pageErrors, []);
            assert.deepEqual(row.httpErrors, []);
            assert.deepEqual(row.failures, [], 'reference playback must not cancel media requests');
            row.pass = true;
          } catch (e) { row.pass = false; row.error = e.message; }
          finally {
            await page.screenshot({ path: path.join(output, `${name}.png`) });
            await context.tracing.stop({ path: path.join(output, `${name}.zip`) });
            await context.close();
          }
          console.log(JSON.stringify(row));
        }
        const page = await browser.newPage();
        const row = { name: `${engineName}-file`, failures: [], pageErrors: [] }; results.push(row);
        page.on('pageerror', e => row.pageErrors.push(e.message));
        page.on('requestfailed', r => row.failures.push({ url: r.url(), error: r.failure()?.errorText }));
        try {
          await page.goto(pathToFileURL(path.join(root, 'index.html')).href + '?embed=1');
          await expect.poll(() => page.evaluate(() => ['flightv', 'greetv', 'walkv'].every(id => document.getElementById(id).readyState >= 2)), { timeout: 20000 }).toBe(true);
          assert.ok(await page.locator('#flightv').evaluate(v => v.currentSrc.startsWith('file:')));
          await page.evaluate(() => document.getElementById('replay').click());
          await expect.poll(() => page.locator('#flightv').evaluate(v => v.currentTime)).toBeGreaterThan(0.3);
          assert.deepEqual(row.pageErrors, []); assert.deepEqual(row.failures, []);
          row.pass = true;
        } catch (e) { row.pass = false; row.error = e.message; }
        finally { await page.screenshot({ path: path.join(output, `${row.name}.png`) }); await page.close(); }
        console.log(JSON.stringify(row));
      } finally { await browser.close(); }
    }
  } finally {
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  }
  assert.equal(results.filter(r => !r.pass).length, 0);
}
main().catch(e => { console.error(e); process.exitCode = 1; });
