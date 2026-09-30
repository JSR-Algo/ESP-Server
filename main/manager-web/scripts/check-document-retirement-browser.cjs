const assert = require('node:assert/strict');
const http = require('node:http');
const { chromium, webkit } = require('@playwright/test');
const { observeJourney } = require('../e2e/lesson-studio/helpers/real-service-evidence');

async function main() {
  const server = http.createServer((req, res) => {
    if (req.url.startsWith('/held.png')) {
      res.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': '1000000', 'Cache-Control': 'no-store' });
      res.write(Buffer.from('89504e470d0a1a0a', 'hex'));
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html', 'Cache-Control': 'no-store' });
    res.end(req.url === '/child' ? '<iframe src="/image"></iframe>'
      : req.url === '/image' ? '<img src="/held.png">' : '<p>Replacement document</p>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  try {
    for (const [name, engine] of [['chromium', chromium], ['webkit', webkit]]) {
      const browser = await engine.launch();
      try {
        for (const frameKind of ['root', 'child']) {
          for (const registered of [false, true]) {
            const page = await browser.newPage();
            const journal = observeJourney(page);
            const request = page.waitForRequest(r => r.url() === `${origin}/held.png`);
            await page.goto(`${origin}/${frameKind === 'child' ? 'child' : 'image'}`, { waitUntil: 'domcontentloaded' });
            await request;
            const failed = page.waitForEvent('requestfailed', r => r.url() === `${origin}/held.png`);
            const action = async () => {
              await page.goto(`${origin}/replacement`);
              await failed;
            };
            if (registered) await journal.retireDocumentForNavigation(action);
            else await action();
            assert.equal(journal.evidence.requestFailures.length, 1);
            assert.equal(journal.evidence.mediaRetirements.length, registered ? 1 : 0,
              JSON.stringify({ name, frameKind, evidence: journal.evidence }));
            if (registered) journal.assertHappyPath();
            else assert.throws(() => journal.assertHappyPath());
            console.log(JSON.stringify({ engine: name, frameKind, registered, ...journal.evidence }));
            if (registered) {
              const next = page.waitForRequest(r => r.url() === `${origin}/held.png`);
              await page.goto(`${origin}/image`, { waitUntil: 'domcontentloaded' });
              await next;
              const failedAgain = page.waitForEvent('requestfailed', r => r.url() === `${origin}/held.png`);
              await page.goto(`${origin}/replacement`);
              await failedAgain;
              assert.equal(journal.evidence.requestFailures.length, 2);
              assert.equal(journal.evidence.mediaRetirements.length, 1);
              assert.throws(() => journal.assertHappyPath());
              console.log(JSON.stringify({ engine: name, frameKind, variant: 'new-document-identical-url-remains-failure', pass: true }));
            }
            await page.close();
          }
        }
        const page = await browser.newPage();
        const journal = observeJourney(page);
        const request = page.waitForRequest(r => r.url() === `${origin}/held.png`);
        await page.goto(`${origin}/child`, { waitUntil: 'domcontentloaded' });
        await request;
        const failed = page.waitForEvent('requestfailed', r => r.url() === `${origin}/held.png`);
        await journal.retireDocumentForNavigation(async () => {
          await page.evaluate(() => document.querySelector('iframe').remove());
          await failed;
        });
        assert.equal(journal.evidence.requestFailures.length, 1);
        assert.equal(journal.evidence.mediaRetirements.length, 0);
        assert.throws(() => journal.assertHappyPath());
        console.log(JSON.stringify({ engine: name, variant: 'iframe-removal-without-navigation-remains-failure', pass: true }));
        await page.close();
      } finally { await browser.close(); }
    }
  } finally {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
