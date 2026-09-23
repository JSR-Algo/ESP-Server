const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const crypto = require('node:crypto');
const { chromium, webkit } = require('@playwright/test');

const root = path.resolve(__dirname, '../..');
const identity = require('../fixtures/mjpeg/current-flyIn.json');
const media = fs.readFileSync(path.join(root, 'tests/fixtures/mjpeg/current-flyIn.mp4'));
assert.equal(media.length, identity.bytes);
assert.equal(crypto.createHash('sha256').update(media).digest('hex'), identity.sha256);

// Mount the real Vue component and decoder; only the unrelated input widget is stubbed.
function componentModule(file) {
  const source = fs.readFileSync(file, 'utf8');
  if (!file.endsWith('.vue')) return source;
  const template = source.match(/<template>([\s\S]*?)<\/template>\s*<script>/)[1];
  const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace('export default', 'const component =');
  const style = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n');
  return script + '\ncomponent.template=' + JSON.stringify(template) + ';\n'
    + 'const style=document.createElement("style");style.textContent=' + JSON.stringify(style)
    + ';document.head.append(style);export default component;';
}

for (const [name, engine] of [['Chromium', chromium], ['WebKit', webkit]]) {
  test(`${name}: picker decodes verified MJPEG bytes and rejects a wrong hash`, { timeout: 30000 }, async () => {
    const server = http.createServer((request, response) => {
      try {
        const url = new URL(request.url, 'http://localhost').pathname;
        let body, type = 'text/javascript';
        if (url === '/fixture.mp4') { body = media; type = 'video/mp4'; }
        else if (url === '/vue.js') body = fs.readFileSync(path.join(root, 'node_modules/vue/dist/vue.js'));
        else if (url.startsWith('/component/')) {
          const name = url.slice('/component/'.length);
          assert.match(name, /^[A-Za-z0-9.-]+$/);
          body = componentModule(path.join(root, 'src/components/lesson', path.extname(name) ? name : name + '.js'));
        } else if (url === '/') {
          type = 'text/html';
          body = '<!doctype html><script src="/vue.js"></script><div id="app"></div>'
            + '<script type="module">import Picker from "/component/SharedAssetPicker.vue";'
            + 'Vue.prototype.$t=k=>k;Vue.component("el-input",{render:h=>h("input")});'
            + 'window.mountAsset=async asset=>{if(window.host)window.host.$destroy();document.body.innerHTML="<div id=app></div>";'
            + 'window.host=new Vue({data:{asset},render(h){return h(Picker,{props:{assets:[this.asset],title:"Source preview"}})}}).$mount("#app");await Vue.nextTick();};window.ready=true;</script>';
        } else { response.writeHead(404).end(); return; }
        response.writeHead(200, { 'Content-Type': type, 'Content-Length': Buffer.byteLength(body) }).end(body);
      } catch (error) { response.writeHead(500).end(String(error)); }
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
      browser = await engine.launch({ headless: true });
      const page = await browser.newPage();
      const failures = [], pageErrors = [], mediaRequests = [];
      page.on('request', request => { if (new URL(request.url()).pathname === '/fixture.mp4') mediaRequests.push(request); });
      page.on('requestfailed', request => failures.push(request.failure()?.errorText));
      page.on('pageerror', error => pageErrors.push(error.message));
      await page.goto('http://127.0.0.1:' + server.address().port);
      await page.waitForFunction(() => window.ready);
      const asset = { versionId: 'original', assetKey: 'robot.flyIn', version: 1,
        url: '/fixture.mp4', mimeType: 'video/mp4', bytes: identity.bytes, sha256: identity.sha256,
        width: identity.metadata.width, height: identity.metadata.height, compatibilityMetadata: identity.metadata };
      await page.evaluate(asset => window.mountAsset(asset), asset);
      await page.waitForFunction(() => {
        const video = document.querySelector('video');
        const canvas = document.querySelector('canvas');
        return video?.error || video?.readyState >= 2 || (canvas && canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some((v, i) => i % 4 === 3 && v > 0));
      }, null, { timeout: 10000 });
      const state = await page.evaluate(() => ({ videos: document.querySelectorAll('video').length,
        error: document.querySelector('video')?.error?.message || null, canvases: document.querySelectorAll('canvas').length }));
      assert.equal(state.error, null, 'MJPEG must not enter an unsupported native decoder');
      assert.equal(state.videos, 0);
      assert.equal(state.canvases, 1);
      assert.deepEqual(failures, []);
      assert.deepEqual(pageErrors, []);
      await page.evaluate(async () => {
        window.host.$children[0].query = 'robot';
        await Vue.nextTick();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      assert.equal(mediaRequests.length, 1, 'retained tiles do not reload when the filter rerenders');
      await page.evaluate(async () => {
        window.host.asset = JSON.parse(JSON.stringify(window.host.asset));
        await Vue.nextTick();
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      assert.equal(mediaRequests.length, 1, 'identical catalog readback must retain the verified decoder');
      await page.evaluate(() => {
        window.host.asset = { ...window.host.asset, sha256: '0'.repeat(64) };
      });
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /SHA-256 mismatch/,
        'changed media identity must retire the old decoder and revalidate bytes');
      await page.evaluate(asset => window.mountAsset(asset), { ...asset, sha256: '0'.repeat(64) });
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /SHA-256 mismatch/);
      await page.evaluate(asset => window.mountAsset(asset), { ...asset, bytes: asset.bytes + 1 });
      await page.getByRole('alert').waitFor();
      assert.match(await page.getByRole('alert').textContent(), /length mismatch/);
      await page.evaluate(asset => window.mountAsset(asset), { ...asset, compatibilityMetadata: { ...identity.metadata, codec: 'h264' } });
      assert.equal(await page.locator('video').count(), 1, 'ordinary codecs retain the existing native preview');
    } finally {
      if (browser) await browser.close();
      await new Promise(resolve => server.close(resolve));
    }
  });
}
