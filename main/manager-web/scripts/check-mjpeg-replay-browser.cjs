const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const crypto = require('node:crypto');
const { chromium, webkit } = require('@playwright/test');

const root = path.resolve(__dirname, '..');
const identity = require('../tests/fixtures/mjpeg/current-flyIn.json');
const media = fs.readFileSync(path.join(root, 'tests/fixtures/mjpeg/current-flyIn.mp4'));
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
assert.equal(media.length, identity.bytes);
assert.equal(digest(media), identity.sha256);
const background = '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="320"><rect width="480" height="320" fill="#ead4a6"/></svg>';
function manifest(suffix) {
  const layers = [
    { layer: 'background', slot: 'backgroundScene', assetVersionId: 'background', assetKey: 'background',
      version: 1, sha256: digest(background), bytes: Buffer.byteLength(background),
      metadata: { mediaKind: 'image', mediaType: 'image/svg+xml', width: 480, height: 320,
        rect: { x: 0, y: 0, width: 480, height: 320 }, fit: 'cover' } },
    { layer: 'robotOverlay', slot: 'robotOverlay', assetVersionId: 'diagnostic-' + suffix,
      assetKey: 'diagnostic-' + suffix, version: 1, sha256: identity.sha256,
      bytes: identity.bytes, metadata: identity.metadata }
  ];
  return { manifestVersion: 'teebot-lesson-renderer.v5', profile: 'espTft', lessonId: 'diagnostic-' + suffix,
    steps: [{ id: 'activity', activityId: 'activity' }],
    assets: layers.map(layer => ({ ...layer, mediaType: layer.metadata.mediaType,
      url: layer.slot === 'robotOverlay' ? '/fixture-' + suffix + '.mp4' : '/background.svg' })),
    cinematicPhases: [{ templateId: 'layeredCinematic', phaseId: 'teach', activityIds: ['activity'],
      playbackMode: 'once', timing: { durationMs: identity.metadata.durationMs }, layers }] };
}
function componentModule(file) {
  const source = fs.readFileSync(file, 'utf8');
  if (!file.endsWith('.vue')) return source;
  const template = source.match(/<template>([\s\S]*?)<\/template>\s*<script>/)[1];
  const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace('export default', 'const component =');
  const styles = [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map(match => match[1]).join('\n');
  return script + '\ncomponent.template=' + JSON.stringify(template) + ';\n'
    + 'const style=document.createElement("style");style.textContent=' + JSON.stringify(styles)
    + ';document.head.append(style);export default component;';
}
async function main() {
  const output = process.env.MJPEG_REPLAY_OUTPUT || fs.mkdtempSync(path.join(os.tmpdir(), 'tbot-mjpeg-replay-'));
  fs.mkdirSync(output, { recursive: true });
  const server = http.createServer((request, response) => {
    try {
      const url = new URL(request.url, 'http://localhost').pathname;
      let body, type = 'text/javascript';
      if (/^\/fixture-[12]\.mp4$/.test(url)) { body = media; type = 'video/mp4'; }
      else if (url === '/background.svg') { body = background; type = 'image/svg+xml'; }
      else if (url === '/vue.js') body = fs.readFileSync(path.join(root, 'node_modules/vue/dist/vue.js'));
      else if (url.startsWith('/component/')) {
        const name = url.slice('/component/'.length);
        assert.match(name, /^[A-Za-z0-9.-]+$/);
        const file = path.join(root, 'src/components/lesson', path.extname(name) ? name : name + '.js');
        body = componentModule(file);
      } else if (url === '/') {
        type = 'text/html';
        body = '<!doctype html><title>MJPEG replay regression</title><script src="/vue.js"></script><div id="app"></div>'
          + '<script type="module">import Preview from "/component/RobotEspTftProjectionPreview.vue";'
          + 'window.mountFixture=async manifest=>{if(window.host)window.host.$destroy();document.body.innerHTML="<div id=app></div>";'
          + 'window.host=new Vue({data:{manifest},render(h){return h(Preview,{ref:"preview",props:{manifest:this.manifest}})}}).$mount("#app");'
          + 'window.preview=window.host.$refs.preview;await Vue.nextTick();};window.fixtureReady=true;</script>';
      } else { response.writeHead(404); response.end(); return; }
      response.writeHead(200, { 'Content-Type': type, 'Content-Length': Buffer.byteLength(body) }); response.end(body);
    } catch (error) { response.writeHead(500); response.end(String(error)); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  const results = [];
  try {
    for (const [name, engine, options] of [['Chrome', chromium, { channel: 'chrome' }], ['WebKit', webkit, {}]]) {
      let browser;
      const row = { name, scope: 'Actual local mounted Vue controls and JPEG decode; unregistered fixtures', pageErrors: [] };
      try {
        browser = await engine.launch({ headless: true, ...options }); row.version = browser.version();
        const page = await browser.newPage(); page.on('pageerror', error => row.pageErrors.push(error.message));
        await page.goto('http://127.0.0.1:' + server.address().port); await page.waitForFunction(() => window.fixtureReady);
        await page.addScriptTag({ path: path.join(root, 'tests/browser/mjpeg-replay-checks.js') });
        row.cases = await page.evaluate(async ([first, next]) => window.verifyMjpegReplay(first, next), [manifest('1'), manifest('2')]);
        row.failures = row.cases.filter(item => !item.pass).length;
      } catch (error) { row.error = String(error); row.failures = 1; }
      finally { if (browser) await browser.close(); results.push(row); }
    }
  } finally { await new Promise(resolve => server.close(resolve)); }
  fs.writeFileSync(path.join(output, 'replay-results.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify(results.map(row => ({ browser: row.name, version: row.version,
    cases: row.cases && row.cases.length, failures: row.failures, pageErrors: row.pageErrors })), null, 2));
  assert.equal(results.reduce((count, row) => count + row.failures + row.pageErrors.length, 0), 0, 'Mounted replay regressions');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
