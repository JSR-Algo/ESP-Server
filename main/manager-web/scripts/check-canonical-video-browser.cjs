const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { createHash } = require('node:crypto');
const { chromium, webkit, expect, devices } = require('@playwright/test');

async function main() {
  const source = fs.readFileSync(path.resolve(__dirname, '../src/views/LessonEditor.vue'), 'utf8');
  const template = source.match(/<video\s+data-testid="canonical-source-video"[\s\S]*?\/>/)[0];
  const video = template.replace(/:poster="[^"]+"/, '').replace(/:src="[^"]+"/, 'src="/source.mp4"').replace('/>', '></video>');
  const bytes = fs.readFileSync(path.resolve(__dirname, '../public/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4'));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), '53d3ac70d166ba83029d5d122493dc48304d2caf933e03c09b0907152531f5f1');
  const rows = [];
  const server = http.createServer((req, res) => {
    if (req.url !== '/source.mp4') {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end(video);
      return;
    }
    const range = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || '');
    const start = range ? Number(range[1]) : 0;
    const end = range?.[2] ? Math.min(Number(range[2]), bytes.length - 1) : bytes.length - 1;
    res.writeHead(range ? 206 : 200, { 'Content-Type': 'video/mp4', 'Accept-Ranges': 'bytes',
      'Content-Length': end - start + 1, ...(range ? { 'Content-Range': `bytes ${start}-${end}/${bytes.length}` } : {}) });
    res.end(bytes.subarray(start, end + 1));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    for (const [name, engine] of [['chromium', chromium], ['webkit', webkit]]) {
      const browser = await engine.launch();
      try {
        for (const mobile of [false, true]) {
          const descriptor = mobile ? (name === 'webkit' ? 'iPhone 13' : 'Pixel 7') : (name === 'webkit' ? 'Desktop Safari' : 'Desktop Chrome');
          const context = await browser.newContext({ ...devices[descriptor] });
          const page = await context.newPage();
          const row = { name: `${name}-${mobile ? 'mobile' : 'desktop'}`, descriptor, failures: [], errors: [] };
          rows.push(row);
          page.on('requestfailed', request => row.failures.push({ url: request.url(), error: request.failure()?.errorText }));
          page.on('pageerror', error => row.errors.push(error.message));
          page.on('console', message => { if (message.type() === 'error') row.errors.push(message.text()); });
          try {
            await page.goto(`http://127.0.0.1:${server.address().port}/`);
            const element = page.getByTestId('canonical-source-video');
            await expect.poll(() => element.evaluate(v => v.networkState === 1 && v.readyState >= 2)).toBe(true);
            // Give native metadata suspension time to surface before the author plays.
            await page.waitForTimeout(500);
            await element.evaluate(v => v.play());
            await expect.poll(() => element.evaluate(v => v.currentTime)).toBeGreaterThan(0.1);
            await element.evaluate(v => v.pause());
            await expect.poll(() => element.evaluate(v => !v.error && v.networkState === 1 && v.buffered.length > 0 && v.buffered.end(v.buffered.length - 1) >= v.duration - 0.05)).toBe(true);
            await page.goto('about:blank');
          } catch (error) { row.errors.push(error.message); }
          await context.close();
        }
      } finally { await browser.close(); }
    }
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
  console.log(JSON.stringify(rows, null, 2));
  assert.ok(rows.every(row => row.errors.length === 0 && row.failures.length === 0), 'canonical source playback and settled navigation must not cancel media');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
