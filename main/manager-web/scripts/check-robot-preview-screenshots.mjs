import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { dirname, extname, join, normalize } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';

const root = new URL('../', import.meta.url);
const repo = dirname(fileURLToPath(import.meta.url));
const managerRoot = normalize(join(repo, '..'));
const update = process.argv.includes('--update');
const cleanupSelfTest = process.argv.includes('--test-setup-cleanup');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.ico': 'image/x-icon' };

async function waitForFile(path, timeoutMs = 10000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (existsSync(path)) return readFile(path, 'utf8');
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Timed out waiting for ${path}`);
}

async function closeServer(server) {
  if (!server) return;
  server.closeAllConnections?.();
  await new Promise((resolve) => server.close(() => resolve()));
}

async function stopChild(child) {
  if (!child || child.exitCode !== null) return;
  child.kill('SIGTERM');
  await Promise.race([
    new Promise((resolve) => child.once('exit', resolve)),
    new Promise((resolve) => setTimeout(resolve, 2000))
  ]);
  if (child.exitCode === null) {
    const exited = new Promise((resolve) => child.once('exit', resolve));
    child.kill('SIGKILL');
    await exited;
  }
}

async function closeSocket(socket) {
  if (!socket) return;
  if (socket.readyState === WebSocket.CLOSED) return;
  const closed = new Promise((resolve) => socket.once('close', resolve));
  socket.close();
  await Promise.race([closed, new Promise((resolve) => setTimeout(resolve, 1000))]);
  if (socket.readyState !== WebSocket.CLOSED) socket.terminate();
}

async function runHarness({ forceSetupFailure = false, onTemp = () => {} } = {}) {
  let temp = null;
  let server = null;
  let chrome = null;
  let socket = null;
  try {
    temp = await mkdtemp(join(tmpdir(), 'tbot-component-preview-'));
    onTemp(temp);
    const buildDir = join(temp, 'build');
    const profileDir = join(temp, 'chrome-profile');
    await mkdir(new URL('tests/screenshots/', root), { recursive: true });

    const build = spawnSync(process.execPath, [join(managerRoot, 'node_modules/@vue/cli-service/bin/vue-cli-service.js'), 'build', '--dest', buildDir, '--no-clean', join(managerRoot, 'tests/browser/robot-preview-main.js')], { cwd: managerRoot, encoding: 'utf8', timeout: 120000 });
    assert.equal(build.status, 0, `Vue component harness build failed:\n${build.stdout}\n${build.stderr}`);

    server = createServer((request, response) => {
      const path = normalize(join(buildDir, request.url.split('?')[0] === '/' ? 'index.html' : request.url.split('?')[0]));
      if (!path.startsWith(buildDir)) { response.writeHead(403).end(); return; }
      try { response.writeHead(200, { 'content-type': mime[extname(path)] || 'application/octet-stream', 'cache-control': 'no-store' }).end(readFileSync(path)); }
      catch { response.writeHead(404).end(); }
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const port = server.address().port;
    if (forceSetupFailure) throw new Error('forced setup failure after server acquisition');

    const chromeCandidates = [process.env.CHROME_BIN, join(homedir(), 'Library/Caches/ms-playwright/chromium_headless_shell-1223/chrome-headless-shell-mac-arm64/chrome-headless-shell'), '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].filter(Boolean);
    const chromeBin = chromeCandidates.find(existsSync);
    assert.ok(chromeBin, 'Chromium is required for mounted component browser verification');
    chrome = spawn(chromeBin, ['--headless', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`, 'about:blank'], { stdio: 'ignore' });
    const [debugPort] = (await waitForFile(join(profileDir, 'DevToolsActivePort'))).trim().split('\n');
    const target = await fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT' }).then((response) => response.json());
    socket = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject); });

    let commandId = 0;
    const pending = new Map();
    socket.on('message', (raw) => {
      const message = JSON.parse(raw);
      if (message.id && pending.has(message.id)) {
        const handlers = pending.get(message.id); pending.delete(message.id);
        message.error ? handlers.reject(new Error(message.error.message)) : handlers.resolve(message.result);
      }
    });
    const cdp = (method, params = {}) => new Promise((resolve, reject) => { const id = ++commandId; pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })); });
    const evaluate = async (expression) => (await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result.value;
    const waitReady = async () => { for (let i = 0; i < 100; i += 1) { if (await evaluate('Boolean(window.__ROBOT_PREVIEW_READY__)')) return; await new Promise((resolve) => setTimeout(resolve, 50)); } throw new Error('Mounted RobotLessonPreview did not become ready'); };
    const settleVisuals = () => evaluate(`(async()=>{await Promise.all([...document.images].map(img=>img.complete?Promise.resolve():new Promise((resolve,reject)=>{img.addEventListener('load',resolve,{once:true});img.addEventListener('error',reject,{once:true})})));if(document.fonts)await document.fonts.ready;await Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{})));await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return true})()`);

    await cdp('Page.enable'); await cdp('Runtime.enable');
    await cdp('Emulation.setDeviceMetricsOverride', { width: 900, height: 760, deviceScaleFactor: 1, mobile: false });
    const expectedPaths = [
      ['Correct', 'Slave command: celebrate', 'motion-nod'],
      ['Near miss', 'Slave command: encourage', 'motion-nod'],
      ['Incorrect', 'Slave command: gentle-shake', 'motion-shake'],
      ['Silence', 'Slave command: patient-wait', 'motion-breathe'],
      ['STT unavailable', 'Slave command: calm-idle', 'motion-breathe'],
      ['Missing visual', 'Slave command: teach', 'motion-tilt']
    ];
    for (const minutes of [3, 5, 8]) {
      await cdp('Page.navigate', { url: `http://127.0.0.1:${port}/?minutes=${minutes}` }); await waitReady();
      assert.deepEqual(await evaluate(`(() => { const stage=document.querySelector('[data-testid="esp-tft-stage"]'); const r=stage.getBoundingClientRect(); const images=[...stage.querySelectorAll('img')]; return [r.width,r.height,document.querySelectorAll('.preview-toolbar button').length,images.length,images.every(img=>img.src.startsWith('data:image/svg+xml')&&img.complete&&img.naturalWidth>0)]; })()`), [480, 320, 6, 3, true]);
      const states = await evaluate(`(async()=>{const out=[];for(const b of document.querySelectorAll('.preview-toolbar button')){b.click();await new Promise(r=>setTimeout(r,0));const robot=document.querySelector('.layer-robotOverlay');out.push([b.textContent.trim(),b.getAttribute('aria-pressed'),[...document.querySelectorAll('.preview-toolbar button')].filter(x=>x.getAttribute('aria-pressed')==='true').length,document.querySelector('.motion-timeline li:nth-child(2) span').textContent,robot?.className||'missing',robot?.getAnimations().some(animation=>animation.playState==='running')||false]);}return out})()`);
      assert.equal(states.length, expectedPaths.length);
      states.forEach(([label, pressed, pressedCount, timeline, motion, animating], index) => { const expected = expectedPaths[index]; assert.equal(label, expected[0]); assert.equal(pressed, 'true'); assert.equal(pressedCount, 1); assert.equal(timeline, expected[1]); assert.match(motion, new RegExp(`(?:^|\\s)${expected[2]}(?:\\s|$)`)); assert.equal(animating, true); });
      assert.equal(await evaluate(`(async()=>{const checkbox=document.querySelector('.preview-toolbar input');checkbox.click();await new Promise(r=>setTimeout(r,0));return document.querySelectorAll('.safe-zone').length})()`), 4);
      await cdp('Page.navigate', { url: `http://127.0.0.1:${port}/?minutes=${minutes}` }); await waitReady(); await settleVisuals();
      const rect = await evaluate(`(()=>{const r=document.querySelector('[data-testid="esp-tft-stage"]').getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height}})()`);
      const png = Buffer.from((await cdp('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false, clip: { ...rect, scale: 1 } })).data, 'base64');
      const baseline = new URL(`tests/screenshots/robot-preview-${minutes}m.png`, root);
      if (update) await writeFile(baseline, png);
      assert.deepEqual(png, await readFile(baseline), `${minutes}-minute mounted component screenshot drifted`);
    }
    await cdp('Page.navigate', { url: `http://127.0.0.1:${port}/?minutes=5&warning=1` }); await waitReady();
    assert.equal(await evaluate(`document.querySelector('[role="alert"]')?.textContent.includes('Firmware-incompatible preview')`), true);
    console.log('mounted RobotLessonPreview browser behavior and actual PNG baselines 3/5/8 PASS');
  } finally {
    await closeSocket(socket);
    await stopChild(chrome);
    await closeServer(server);
    if (temp) await rm(temp, { recursive: true, force: true });
  }
}

if (cleanupSelfTest) {
  let tempPath = null;
  await assert.rejects(runHarness({ forceSetupFailure: true, onTemp: (path) => { tempPath = path; } }), /forced setup failure/);
  assert.ok(tempPath && !existsSync(tempPath), 'setup-failure cleanup must remove its temp directory');
  console.log('mounted RobotLessonPreview setup-failure cleanup PASS');
} else {
  await runHarness();
}
