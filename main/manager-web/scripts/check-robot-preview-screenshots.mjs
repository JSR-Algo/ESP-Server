import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { EventEmitter } from 'node:events';
import { existsSync, readFileSync } from 'node:fs';
import { mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { dirname, extname, join, normalize } from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import WebSocket from 'ws';
import { acquirePinnedRobotPreviewChromium } from './robot-preview-browser.mjs';

const root = new URL('../', import.meta.url);
const repo = dirname(fileURLToPath(import.meta.url));
const managerRoot = normalize(join(repo, '..'));
const update = process.argv.includes('--update');
const cleanupSelfTest = process.argv.includes('--test-setup-cleanup');
const spawnCleanupSelfTest = process.argv.includes('--test-spawn-cleanup');
const childCleanupSelfTest = process.argv.includes('--test-child-cleanup');
const runtimeExitSelfTest = process.argv.includes('--test-runtime-exit-cleanup');
const operationTimeoutSelfTest = process.argv.includes('--test-operation-timeout-cleanup');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.ico': 'image/x-icon' };
const BROWSER_OPERATION_TIMEOUT_MS = 10000;

function assertTrackedTempsRemoved(paths, message) {
  assert.deepEqual(paths.filter((path) => existsSync(path)), [], message);
}

async function acquireSelfTestLease(onLease = () => {}) {
  const leaseRoot = await mkdtemp(join(tmpdir(), 'tbot-robot-preview-browser-'));
  onLease(leaseRoot);
  const executablePath = join(leaseRoot, 'self-test-browser');
  await writeFile(executablePath, 'self-test browser placeholder\n', { mode: 0o500 });
  let active = true;
  return {
    executablePath,
    cleanup: async () => {
      if (!active) return;
      active = false;
      await rm(leaseRoot, { recursive: true, force: true });
    }
  };
}

async function acquireInstalledBrowserSelfTestLease() {
  const metadata = JSON.parse(await readFile(join(managerRoot, 'node_modules/playwright-core/browsers.json'), 'utf8'));
  const revision = metadata.browsers?.find((entry) => entry.name === 'chromium-headless-shell')?.revision;
  assert.match(revision || '', /^[1-9][0-9]*$/, 'Playwright headless-shell revision is required for lifecycle self-test');
  const cacheRoot = process.env.PLAYWRIGHT_BROWSERS_PATH && process.env.PLAYWRIGHT_BROWSERS_PATH !== '0'
    ? process.env.PLAYWRIGHT_BROWSERS_PATH
    : process.platform === 'darwin'
      ? join(homedir(), 'Library/Caches/ms-playwright')
      : join(homedir(), '.cache/ms-playwright');
  const layout = {
    'darwin-arm64': ['chrome-headless-shell-mac-arm64', 'chrome-headless-shell'],
    'darwin-x64': ['chrome-headless-shell-mac-x64', 'chrome-headless-shell'],
    'linux-arm64': ['chrome-linux', 'headless_shell'],
    'linux-x64': ['chrome-headless-shell-linux64', 'chrome-headless-shell']
  }[`${process.platform}-${process.arch}`];
  assert.ok(layout, `Unsupported lifecycle self-test platform: ${process.platform}-${process.arch}`);
  const executablePath = join(cacheRoot, `chromium_headless_shell-${revision}`, ...layout);
  assert.ok(existsSync(executablePath), `Pinned Playwright browser is missing: ${executablePath}`);
  return { executablePath, cleanup: async () => {} };
}

async function waitForFile(path, timeoutMs = 10000, signal = null) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (signal?.aborted) throw signal.reason;
    if (existsSync(path)) return readFile(path, 'utf8');
    await new Promise((resolve, reject) => {
      const onAbort = () => {
        clearTimeout(timer);
        reject(signal.reason);
      };
      const timer = setTimeout(() => {
        signal?.removeEventListener('abort', onAbort);
        resolve();
      }, 50);
      signal?.addEventListener('abort', onAbort, { once: true });
    });
  }
  throw new Error(`Timed out waiting for ${path}`);
}

async function closeServer(server) {
  if (!server) return;
  server.closeAllConnections?.();
  await new Promise((resolve) => server.close(() => resolve()));
}

function childExited(child) {
  return child.exitCode !== null || child.signalCode !== null;
}

async function waitForChildExit(child, timeoutMs) {
  if (childExited(child)) return true;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (exited) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.off('exit', onExit);
      child.off('close', onExit);
      resolve(exited);
    };
    const onExit = () => finish(true);
    const timer = setTimeout(() => finish(childExited(child)), timeoutMs);
    child.once('exit', onExit);
    child.once('close', onExit);
  });
}

async function stopChild(child, timeoutMs = 2000) {
  // Failed asynchronous spawns have no pid and may emit `error` without ever emitting `exit`.
  if (!child || childExited(child) || child.pid == null) return;
  child.kill('SIGTERM');
  if (await waitForChildExit(child, timeoutMs)) return;
  const killAccepted = child.kill('SIGKILL');
  if (killAccepted && await waitForChildExit(child, timeoutMs)) return;
  throw new Error('Robot preview Chromium did not exit after SIGTERM/SIGKILL');
}

async function withDeadline(operation, label, timeoutMs, onTimeout = () => {}) {
  let timer;
  try {
    return await Promise.race([
      operation,
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          onTimeout();
          reject(new Error(`${label} timed out after ${timeoutMs}ms`));
        }, timeoutMs);
      })
    ]);
  } finally {
    clearTimeout(timer);
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

async function runHarness({
  forceSetupFailure = false,
  onTemp = () => {},
  acquireBrowser = acquirePinnedRobotPreviewChromium,
  spawnBrowser = spawn,
  afterSocketOpen = async () => {},
  sendCdp = (socket, payload) => socket.send(payload),
  operationTimeoutMs = BROWSER_OPERATION_TIMEOUT_MS,
  childStopTimeoutMs = 2000
} = {}) {
  let temp = null;
  let server = null;
  let chrome = null;
  let browserLease = null;
  let socket = null;
  const pending = new Map();
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

    browserLease = await acquireBrowser();
    chrome = spawnBrowser(browserLease.executablePath, ['--headless', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1', '--remote-debugging-port=0', `--user-data-dir=${profileDir}`, 'about:blank'], { stdio: 'ignore' });
    let rejectLifecycle;
    let lifecycleError = null;
    const lifecycleFailure = new Promise((_, reject) => { rejectLifecycle = reject; });
    lifecycleFailure.catch(() => {});
    const failLifecycle = (error) => {
      if (lifecycleError) return;
      lifecycleError = error;
      for (const handlers of pending.values()) handlers.reject(error);
      pending.clear();
      rejectLifecycle(error);
    };
    const raceLifecycle = (operation) => Promise.race([operation, lifecycleFailure]);
    const boundedLifecycle = (operation, label, onTimeout) => withDeadline(
      raceLifecycle(operation), label, operationTimeoutMs, onTimeout
    );
    chrome.once('error', (error) => failLifecycle(new Error(`Robot preview Chromium failed to spawn: ${error.message}`)));
    chrome.once('exit', (code, signal) => failLifecycle(new Error(`Robot preview Chromium exited during preview: code=${code} signal=${signal}`)));
    const startupController = new AbortController();
    let portFile;
    try {
      portFile = await raceLifecycle(waitForFile(join(profileDir, 'DevToolsActivePort'), 10000, startupController.signal));
    } finally {
      startupController.abort(new Error('Chromium startup wait completed'));
    }
    const [debugPort] = portFile.trim().split('\n');
    const fetchController = new AbortController();
    let target;
    try {
      target = await boundedLifecycle(
        fetch(`http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT', signal: fetchController.signal }).then((response) => response.json()),
        'Robot preview DevTools target request',
        () => fetchController.abort()
      );
    } finally {
      fetchController.abort();
    }
    socket = new WebSocket(target.webSocketDebuggerUrl);
    socket.on('error', (error) => failLifecycle(new Error(`Robot preview DevTools socket failed: ${error.message}`)));
    socket.on('close', () => failLifecycle(new Error('Robot preview DevTools socket closed')));
    await boundedLifecycle(new Promise((resolve) => socket.once('open', resolve)), 'Robot preview DevTools socket open');
    await boundedLifecycle(afterSocketOpen({ chrome, socket }), 'Robot preview post-socket setup');

    let commandId = 0;
    socket.on('message', (raw) => {
      const message = JSON.parse(raw);
      if (message.id && pending.has(message.id)) {
        const handlers = pending.get(message.id); pending.delete(message.id);
        message.error ? handlers.reject(new Error(message.error.message)) : handlers.resolve(message.result);
      }
    });
    const cdp = (method, params = {}) => {
      const id = ++commandId;
      const response = new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        sendCdp(socket, JSON.stringify({ id, method, params }));
      });
      return boundedLifecycle(response, `Robot preview CDP ${method}`).finally(() => pending.delete(id));
    };
    const evaluate = async (expression) => (await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result.value;
    const waitReady = async () => { for (let i = 0; i < 100; i += 1) { if (await evaluate('Boolean(window.__ROBOT_PREVIEW_READY__)')) return; await new Promise((resolve) => setTimeout(resolve, 50)); } throw new Error('Mounted RobotLessonPreview did not become ready'); };
    const settleVisuals = () => evaluate(`(async()=>{await Promise.all([...document.images].map(img=>img.complete?Promise.resolve():new Promise((resolve,reject)=>{img.addEventListener('load',resolve,{once:true});img.addEventListener('error',reject,{once:true})})));if(document.fonts)await document.fonts.ready;await Promise.all(document.getAnimations().map(animation=>animation.finished.catch(()=>{})));await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));return true})()`);

    await cdp('Page.enable'); await cdp('Runtime.enable');
    await cdp('Emulation.setDeviceMetricsOverride', { width: 900, height: 760, deviceScaleFactor: 1, mobile: false });
    const expectedPaths = [
      ['Correct', 'Slave command: celebrate', 'motion-nod'],
      ['Near miss', 'Slave command: encourage', 'motion-nod'],
      ['Incorrect', 'Slave command: gentle-shake', 'motion-shake'],
      ['Retry', 'Slave command: tryAgain', 'motion-shake'],
      ['Timeout', 'Slave command: encourage', 'motion-nod'],
      ['Brave try', 'Slave command: encourage', 'motion-nod'],
      ['Completion', 'Slave command: celebrate', 'motion-nod'],
      ['Silence', 'Slave command: patient-wait', 'motion-breathe'],
      ['STT unavailable', 'Slave command: calm-idle', 'motion-breathe'],
      ['Missing visual', 'Slave command: teach', 'motion-tilt']
    ];
    for (const minutes of [3, 5, 8]) {
      await cdp('Page.navigate', { url: `http://127.0.0.1:${port}/?minutes=${minutes}` }); await waitReady();
      assert.deepEqual(await evaluate(`(() => { const stage=document.querySelector('[data-testid="esp-tft-stage"]'); const r=stage.getBoundingClientRect(); const images=[...stage.querySelectorAll('img')]; return [r.width,r.height,document.querySelectorAll('.preview-toolbar button').length,images.length,images.every(img=>img.src.startsWith('data:image/svg+xml')&&img.complete&&img.naturalWidth>0)]; })()`), [480, 320, 10, 3, true]);
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
    let cleanupError = null;
    let childStopped = true;
    for (const operation of [() => closeSocket(socket), () => closeServer(server)]) {
      try { await operation(); } catch (error) { cleanupError ||= error; }
    }
    try { await stopChild(chrome, childStopTimeoutMs); } catch (error) { childStopped = false; cleanupError ||= error; }
    if (childStopped) {
      for (const operation of [
        () => browserLease?.cleanup(),
        () => temp ? rm(temp, { recursive: true, force: true }) : Promise.resolve()
      ]) {
        try { await operation(); } catch (error) { cleanupError ||= error; }
      }
    }
    if (cleanupError) throw cleanupError;
  }
}

if (childCleanupSelfTest) {
  const controller = new AbortController();
  const cancelledWait = waitForFile(join(tmpdir(), 'tbot-file-that-must-not-exist'), 10000, controller.signal);
  controller.abort(new Error('waitForFile cancelled'));
  await Promise.race([
    assert.rejects(cancelledWait, /waitForFile cancelled/),
    new Promise((_, reject) => setTimeout(() => reject(new Error('waitForFile did not cancel promptly')), 500))
  ]);
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  child.pid = 1234;
  let killCount = 0;
  child.kill = (signal) => {
    killCount += 1;
    if (killCount > 1) return false;
    queueMicrotask(() => { child.signalCode = signal; child.emit('exit', null, signal); });
    return true;
  };
  await withDeadline(stopChild(child), 'stopChild signal-exit self-test', 3000);
  const stubbornChild = new EventEmitter();
  stubbornChild.exitCode = null;
  stubbornChild.signalCode = null;
  stubbornChild.pid = 1234;
  const directSignals = [];
  stubbornChild.kill = (signal) => { directSignals.push(signal); return true; };
  await withDeadline(
    assert.rejects(stopChild(stubbornChild, 20), /did not exit after SIGTERM\/SIGKILL/),
    'stubborn child bounded cleanup self-test',
    200
  );
  assert.deepEqual(directSignals, ['SIGTERM', 'SIGKILL']);
  const harnessTemps = [];
  const leaseTemps = [];
  const harnessSignals = [];
  let markStubbornSpawned;
  const stubbornSpawned = new Promise((resolve) => { markStubbornSpawned = resolve; });
  const stubbornSpawn = () => {
    markStubbornSpawned();
    const spawned = new EventEmitter();
    spawned.exitCode = null;
    spawned.signalCode = null;
    spawned.pid = 1234;
    spawned.kill = (signal) => { harnessSignals.push(signal); return true; };
    queueMicrotask(() => spawned.emit('error', new Error('simulated stubborn browser')));
    return spawned;
  };
  const stubbornHarness = assert.rejects(runHarness({
    acquireBrowser: () => acquireSelfTestLease((path) => leaseTemps.push(path)),
    spawnBrowser: stubbornSpawn,
    onTemp: (path) => harnessTemps.push(path),
    operationTimeoutMs: 20,
    childStopTimeoutMs: 20
  }), /did not exit after SIGTERM\/SIGKILL/);
  await stubbornSpawned;
  await withDeadline(
    stubbornHarness,
    'stubborn harness bounded cleanup self-test',
    200
  );
  assert.deepEqual(harnessSignals, ['SIGTERM', 'SIGKILL']);
  assert.equal(harnessTemps.every((path) => existsSync(path)), true, 'live child must retain its harness temp');
  assert.equal(leaseTemps.every((path) => existsSync(path)), true, 'live child must retain its browser lease');
  for (const path of [...harnessTemps, ...leaseTemps]) await rm(path, { recursive: true, force: true });
  console.log('mounted RobotLessonPreview child cleanup PASS');
} else if (runtimeExitSelfTest) {
  const harnessTemps = [];
  await assert.rejects(runHarness({
    acquireBrowser: acquireInstalledBrowserSelfTestLease,
    onTemp: (path) => harnessTemps.push(path),
    afterSocketOpen: async ({ chrome }) => { chrome.kill('SIGKILL'); }
  }), /(?:Chromium exited during preview|DevTools socket closed)/);
  assertTrackedTempsRemoved(harnessTemps, 'runtime browser exit must clean harness temp directories');
  console.log('mounted RobotLessonPreview runtime-exit cleanup PASS');
} else if (operationTimeoutSelfTest) {
  const harnessTemps = [];
  await assert.rejects(runHarness({
    acquireBrowser: acquireInstalledBrowserSelfTestLease,
    onTemp: (path) => harnessTemps.push(path),
    sendCdp: () => {},
    operationTimeoutMs: 100
  }), /Robot preview CDP Page\.enable timed out after 100ms/);
  assertTrackedTempsRemoved(harnessTemps, 'CDP timeout must clean harness temp directories');
  console.log('mounted RobotLessonPreview operation-timeout cleanup PASS');
} else if (spawnCleanupSelfTest) {
  const harnessTemps = [];
  const leaseTemps = [];
  let acquireCalls = 0;
  let spawnCalls = 0;
  let cleanupCalls = 0;
  const acquireBrowser = async () => {
    acquireCalls += 1;
    const lease = await acquireSelfTestLease((path) => leaseTemps.push(path));
    return {
      ...lease,
      cleanup: async () => { cleanupCalls += 1; await lease.cleanup(); }
    };
  };
  for (const code of ['ENOENT', 'EACCES']) {
    const failingSpawn = () => {
      spawnCalls += 1;
      const child = new EventEmitter();
      child.exitCode = null;
      child.signalCode = null;
      child.pid = undefined;
      child.kill = () => true;
      queueMicrotask(() => {
        child.emit('error', Object.assign(new Error(`simulated ${code}`), { code }));
      });
      return child;
    };
    await assert.rejects(runHarness({
      acquireBrowser,
      spawnBrowser: failingSpawn,
      onTemp: (path) => harnessTemps.push(path)
    }), new RegExp(`failed to spawn: simulated ${code}`));
  }
  const earlyExitSpawn = () => {
    spawnCalls += 1;
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.pid = 1234;
    child.kill = () => true;
    queueMicrotask(() => {
      child.signalCode = 'SIGKILL';
      child.emit('exit', null, child.signalCode);
    });
    return child;
  };
  await assert.rejects(runHarness({
    acquireBrowser,
    spawnBrowser: earlyExitSpawn,
    onTemp: (path) => harnessTemps.push(path)
  }), /exited during preview: code=null signal=SIGKILL/);
  assert.equal(acquireCalls, 3, 'spawn lifecycle self-test must acquire one lease per injected failure');
  assert.equal(spawnCalls, 3, 'spawn lifecycle self-test must invoke every injected spawn hook');
  assert.equal(cleanupCalls, 3, 'spawn lifecycle self-test must clean every injected browser lease');
  assertTrackedTempsRemoved(harnessTemps, 'spawn failure must clean harness temp directories');
  assertTrackedTempsRemoved(leaseTemps, 'spawn failure must clean browser lease temp directories');
  console.log('mounted RobotLessonPreview spawn-failure cleanup PASS');
} else if (cleanupSelfTest) {
  let tempPath = null;
  await assert.rejects(runHarness({ forceSetupFailure: true, onTemp: (path) => { tempPath = path; } }), /forced setup failure/);
  assert.ok(tempPath && !existsSync(tempPath), 'setup-failure cleanup must remove its temp directory');
  console.log('mounted RobotLessonPreview setup-failure cleanup PASS');
} else {
  await runHarness();
}
