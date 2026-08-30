import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { readFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const managerRoot = join(dirname(fileURLToPath(import.meta.url)), '../..');
const scripts = [
  'check-lesson-visual-library-browser.mjs',
  'check-lesson-builder-browser.mjs',
  'check-course-taxonomy-browser.mjs',
  'check-tvideo-journey-browser.mjs',
];

async function importHarness() {
  return import(pathToFileURL(join(managerRoot, 'scripts/_lib/candidate-browser-harness.mjs')));
}

function fakeChild() {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  child.kill = (signal) => {
    queueMicrotask(() => {
      child.signalCode = signal;
      child.emit('exit', null, signal);
    });
    return true;
  };
  return child;
}

function fakeSocket(onCommand = (_message, respond) => respond({})) {
  const socket = new EventEmitter();
  socket.readyState = 0;
  socket.send = (payload) => {
    const message = JSON.parse(payload);
    onCommand(message, (result) => queueMicrotask(() => socket.emit('message', JSON.stringify({ id: message.id, result }))));
  };
  socket.close = () => {
    socket.readyState = 3;
    queueMicrotask(() => socket.emit('close'));
  };
  socket.terminate = socket.close;
  setTimeout(() => {
    socket.readyState = 1;
    socket.emit('open');
  }, 0);
  return socket;
}

function dependencies({ onCommand, spawnBrowser } = {}) {
  const state = { cleanupCalls: 0, spawned: [], child: null, socket: null };
  return {
    state,
    acquireBrowser: async () => ({
      executablePath: '/candidate/staged/chrome-headless-shell',
      cleanup: async () => { state.cleanupCalls += 1; },
    }),
    spawnBrowser: spawnBrowser || ((executablePath) => {
      state.spawned.push(executablePath);
      state.child = fakeChild();
      return state.child;
    }),
    waitForDevToolsPort: async () => '9222\n',
    fetchDevToolsTarget: async () => ({ webSocketDebuggerUrl: 'ws://candidate-bound' }),
    createDevToolsSocket: () => {
      state.socket = fakeSocket(onCommand);
      return state.socket;
    },
  };
}

test('all remaining browser gates use only the candidate-bound lifecycle helper', async () => {
  for (const script of scripts) {
    const source = await readFile(join(managerRoot, 'scripts', script), 'utf8');
    assert.match(source, /candidate-browser-harness\.mjs/);
    assert.doesNotMatch(source, /CHROME_BIN|homedir\(|Google Chrome\.app|ms-playwright\/chromium/);
    assert.doesNotMatch(source, /new WebSocket|spawn\(chromeBin/);
  }
});

test('lesson studio aggregate runs the candidate browser lifecycle regressions', async () => {
  const packageJson = JSON.parse(await readFile(join(managerRoot, 'package.json'), 'utf8'));
  assert.match(packageJson.scripts['test:lesson-studio'], /npm run test:candidate-browser-harness/);
});

test('TVideo keeps its H.264 capability assertion on the leased candidate', async () => {
  const source = await readFile(join(managerRoot, 'scripts/check-tvideo-journey-browser.mjs'), 'utf8');
  assert.match(source, /canPlayType\('video\/mp4; codecs=/);
  assert.match(source, /cannot decode the H\.264 path background/);
  assert.match(source, /browserExecutablePath/);
  assert.doesNotMatch(source, /Set CHROME_BIN|Google Chrome/);
});

test('spawn failure cleans the candidate browser lease', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies({ spawnBrowser: () => { throw new Error('simulated spawn failure'); } });
  await assert.rejects(withCandidateBoundBrowser({
    profileDir: '/tmp/profile',
    label: 'test gate',
    ...deps,
  }, async () => {}), /simulated spawn failure/);
  assert.equal(deps.state.cleanupCalls, 1);
});

test('hostile ambient browser variables cannot redirect the staged candidate executable', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  const previous = {
    CHROME_BIN: process.env.CHROME_BIN,
    HOME: process.env.HOME,
    PLAYWRIGHT_BROWSERS_PATH: process.env.PLAYWRIGHT_BROWSERS_PATH,
  };
  Object.assign(process.env, {
    CHROME_BIN: '/hostile/chrome',
    HOME: '/hostile/home',
    PLAYWRIGHT_BROWSERS_PATH: '/hostile/cache',
  });
  try {
    await withCandidateBoundBrowser({
      profileDir: '/tmp/profile',
      label: 'test gate',
      ...deps,
    }, async ({ browserExecutablePath }) => {
      assert.equal(browserExecutablePath, '/candidate/staged/chrome-headless-shell');
    });
    assert.deepEqual(deps.state.spawned, ['/candidate/staged/chrome-headless-shell']);
    assert.equal(deps.state.cleanupCalls, 1);
  } finally {
    for (const [key, value] of Object.entries(previous)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
});

test('stalled CDP command times out and cleans child, socket, and lease', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies({ onCommand: () => {} });
  await assert.rejects(withCandidateBoundBrowser({
    profileDir: '/tmp/profile',
    label: 'test gate',
    operationTimeoutMs: 25,
    ...deps,
  }, async ({ cdp }) => cdp('Page.enable')), /test gate CDP Page\.enable timed out after 25ms/);
  assert.equal(deps.state.cleanupCalls, 1);
  assert.deepEqual(deps.state.spawned, ['/candidate/staged/chrome-headless-shell']);
  assert.equal(deps.state.child.signalCode, 'SIGTERM');
  assert.equal(deps.state.socket.readyState, 3);
});

test('stalled readiness polling is bounded and cleans the candidate lifecycle', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies({
    onCommand: (message, respond) => respond(message.method === 'Runtime.evaluate'
      ? { result: { value: false } }
      : {}),
  });
  await assert.rejects(withCandidateBoundBrowser({
    profileDir: '/tmp/profile',
    label: 'test gate',
    readinessTimeoutMs: 30,
    readinessPollMs: 5,
    ...deps,
  }, async ({ waitForReadiness }) => waitForReadiness('Boolean(window.__READY__)', 'fixture readiness')), /fixture readiness timed out after 30ms/);
  assert.equal(deps.state.cleanupCalls, 1);
  assert.equal(deps.state.child.signalCode, 'SIGTERM');
  assert.equal(deps.state.socket.readyState, 3);
});
