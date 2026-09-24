const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium, webkit } = require('@playwright/test');

async function main() {
  const root = path.resolve(__dirname, '../public/tvideo-demo');
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + new URL(req.url, 'http://localhost').pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404); res.end(); return; }
    const type = { '.html': 'text/html', '.png': 'image/png', '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime' }[path.extname(file)];
    res.writeHead(200, { 'Content-Type': type || 'application/octet-stream' });
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  const videoPath = '/assets/scenes/deep-barn-farm-background-6s.mp4';
  try {
    for (const [name, engine] of [['chromium', chromium], ['webkit', webkit]]) {
      const browser = await engine.launch();
      try {
        for (const embed of [true, false]) {
          const page = await browser.newPage();
          const videoRequests = [];
          page.on('request', req => { if (req.url() === origin + videoPath) videoRequests.push(req); });
          await page.goto(`${origin}/index.html${embed ? '?embed=1' : ''}`, { waitUntil: 'domcontentloaded' });
          if (embed) {
            assert.equal(await page.locator('#bgv').getAttribute('src'), null, 'embedded preview must wait for authored background');
            await page.evaluate(() => window.postMessage({ type: 'tvideo-params', payload: { bg: 'assets/scenes/scene-07-farm.png' } }, location.origin));
            await page.waitForFunction(() => { const img = document.getElementById('bgi'); return img.complete && img.naturalWidth > 0; });
            assert.equal(videoRequests.length, 0, 'authored image must not download the unused default video');
            const selected = page.waitForRequest(req => req.url() === origin + videoPath);
            await page.evaluate(bg => window.postMessage({ type: 'tvideo-params', payload: { bg } }, location.origin), 'assets/scenes/deep-barn-farm-background-6s.mp4');
            await selected;
          }
          assert.equal(await page.locator('#bgv').getAttribute('src'), 'assets/scenes/deep-barn-farm-background-6s.mp4');
          assert.ok(videoRequests.length > 0);
          console.log(JSON.stringify({ engine: name, embed, originalVideoRequestedWhenSelected: true, pass: true }));
          await page.close();
        }
      } finally { await browser.close(); }
    }
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
