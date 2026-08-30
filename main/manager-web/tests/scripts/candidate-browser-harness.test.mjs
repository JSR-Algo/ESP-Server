import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
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

function unopenedSocket() {
  const socket = new EventEmitter();
  socket.readyState = 0;
  socket.send = () => {};
  socket.close = () => {
    socket.readyState = 3;
    queueMicrotask(() => socket.emit('close'));
  };
  socket.terminate = socket.close;
  return socket;
}

function outerWatchdog(operation, timeoutMs = 250) {
  return Promise.race([
    operation,
    new Promise((_, reject) => setTimeout(() => reject(new Error(`outer watchdog fired after ${timeoutMs}ms`)), timeoutMs)),
  ]);
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

test('stalled candidate browser acquisition rejects before the outer watchdog', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  deps.acquireBrowser = ({ signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason || new Error('aborted')), { once: true });
  });
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/profile',
    label: 'test gate',
    operationTimeoutMs: 25,
    ...deps,
  }, async () => {
    assert.fail('callback must not run before browser acquisition completes');
  })), /test gate lifecycle timed out after 25ms/);
  assert.deepEqual(deps.state.spawned, []);
  assert.equal(deps.state.cleanupCalls, 0);
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
  }, async ({ cdp }) => cdp('Page.enable')), /test gate lifecycle timed out after 25ms/);
  assert.equal(deps.state.cleanupCalls, 1);
  assert.deepEqual(deps.state.spawned, ['/candidate/staged/chrome-headless-shell']);
  assert.equal(deps.state.child.signalCode, 'SIGTERM');
  assert.equal(deps.state.socket.readyState, 3);
});

test('stalled candidate browser cleanup rejects before the outer watchdog after local resources close', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  deps.acquireBrowser = async () => ({
    executablePath: '/candidate/staged/chrome-headless-shell',
    cleanup: async () => {
      deps.state.cleanupCalls += 1;
      await new Promise(() => {});
    },
  });
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/profile',
    label: 'test gate',
    operationTimeoutMs: 25,
    ...deps,
  }, async () => {})), /test gate lifecycle timed out after 25ms/);
  assert.equal(deps.state.cleanupCalls, 1);
  assert.equal(deps.state.child.signalCode, 'SIGTERM');
  assert.equal(deps.state.socket.readyState, 3);
});

test('failed candidate cleanup is surfaced synchronously', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  deps.acquireBrowser = async () => ({
    executablePath: '/candidate/staged/chrome-headless-shell',
    cleanup: async () => { deps.state.cleanupCalls += 1; throw new Error('cleanup failed'); },
  });
  await assert.rejects(withCandidateBoundBrowser({
    profileDir: '/tmp/profile', label: 'test gate', operationTimeoutMs: 100,
    ...deps,
  }, async () => {}), /cleanup failed/);
  assert.equal(deps.state.cleanupCalls, 1);
});

test('timed out acquisition aborts and finishes owned cleanup before rejecting', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  let cleanupCalls = 0;
  deps.acquireBrowser = ({ signal }) => new Promise((_, reject) => signal.addEventListener('abort', async () => {
    cleanupCalls += 1;
    await new Promise((resolve) => setTimeout(resolve, 1));
    reject(signal.reason || new Error('aborted'));
  }, { once: true }));
  await assert.rejects(withCandidateBoundBrowser({
    profileDir: '/tmp/profile', label: 'late gate', operationTimeoutMs: 20,
    ...deps,
  }, async () => {}), /late gate lifecycle timed out after 20ms/);
  assert.equal(cleanupCalls, 1);
});

test('visual-library pre-CDP socket factory hang rejects before outer watchdog and cleans lifecycle', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  deps.createDevToolsSocket = () => new Promise(() => {});
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/visual-library-profile',
    label: 'Lesson visual library browser',
    operationTimeoutMs: 25,
    ...deps,
  }, async () => {})), /Lesson visual library browser lifecycle timed out after 25ms/);
  assert.equal(deps.state.cleanupCalls, 1);
  assert.equal(deps.state.child.signalCode, 'SIGTERM');
  assert.equal(deps.state.socket, null);
});

test('late socket factory resolution is closed after creation timeout and lifecycle cleanup', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  let resolveLateSocketClosed;
  const lateSocketClosed = new Promise((resolve) => { resolveLateSocketClosed = resolve; });
  deps.createDevToolsSocket = () => new Promise((resolve) => {
    setTimeout(() => {
      deps.state.socket = unopenedSocket();
      const close = deps.state.socket.close;
      deps.state.socket.close = () => {
        queueMicrotask(() => deps.state.socket.emit('error', new Error('closed before connection established')));
        close();
        resolveLateSocketClosed();
      };
      deps.state.socket.terminate = deps.state.socket.close;
      resolve(deps.state.socket);
    }, 50);
  });
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/visual-library-profile',
    label: 'Lesson visual library browser',
    operationTimeoutMs: 20,
    ...deps,
  }, async () => {
    assert.fail('callback must not run after socket creation times out');
  })), /Lesson visual library browser lifecycle timed out after 20ms/);
  assert.equal(deps.state.cleanupCalls, 1);
  assert.equal(deps.state.child.signalCode, 'SIGTERM');
  await outerWatchdog(lateSocketClosed);
  assert.equal(deps.state.socket.readyState, 3);
});

test('visual-library pre-CDP socket-open hang rejects before outer watchdog and cleans lifecycle', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  deps.createDevToolsSocket = () => {
    deps.state.socket = unopenedSocket();
    return deps.state.socket;
  };
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/visual-library-profile',
    label: 'Lesson visual library browser',
    operationTimeoutMs: 25,
    ...deps,
  }, async () => {
    assert.fail('pre-CDP callback must not run before the socket opens');
  })), /Lesson visual library browser lifecycle timed out after 25ms/);
  assert.equal(deps.state.cleanupCalls, 1);
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

test('stalled readiness evaluation uses the remaining readiness deadline', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies({ onCommand: () => {} });
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/profile',
    label: 'test gate',
    operationTimeoutMs: 100,
    readinessTimeoutMs: 20,
    ...deps,
  }, async ({ waitForReadiness }) => waitForReadiness(
    'Boolean(window.__READY__)', 'fixture readiness', 'document.body?.innerText'
  )), 75), /fixture readiness timed out after 20ms/);
  assert.equal(deps.state.cleanupCalls, 1);
  assert.equal(deps.state.child.signalCode, 'SIGTERM');
  assert.equal(deps.state.socket.readyState, 3);
});

test('completed child and socket cleanup do not leave deadline timers keeping Node alive', () => {
  const harnessUrl = pathToFileURL(join(managerRoot, 'scripts/_lib/candidate-browser-harness.mjs')).href;
  const source = `
    import { EventEmitter } from 'node:events';
    import { withCandidateBoundBrowser } from ${JSON.stringify(harnessUrl)};
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.kill = (signal) => {
      queueMicrotask(() => {
        child.signalCode = signal;
        child.emit('exit', null, signal);
      });
    };
    const socket = new EventEmitter();
    socket.readyState = 0;
    socket.send = (payload) => {
      const message = JSON.parse(payload);
      queueMicrotask(() => socket.emit('message', JSON.stringify({ id: message.id, result: {} })));
    };
    socket.close = () => {
      socket.readyState = 3;
      queueMicrotask(() => socket.emit('close'));
    };
    socket.terminate = socket.close;
    setTimeout(() => { socket.readyState = 1; socket.emit('open'); }, 0);
    await withCandidateBoundBrowser({
      profileDir: '/tmp/profile',
      label: 'timer test',
      acquireBrowser: async () => ({ executablePath: '/candidate/chrome', cleanup: async () => {} }),
      spawnBrowser: () => child,
      waitForDevToolsPort: async () => '9222\\n',
      fetchDevToolsTarget: async () => ({ webSocketDebuggerUrl: 'ws://candidate-bound' }),
      createDevToolsSocket: () => socket,
    }, async () => {});
  `;
  const result = spawnSync(process.execPath, ['--unhandled-rejections=strict', '--input-type=module', '-e', source], {
    timeout: 750,
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.error?.message || result.stderr);
});

test('cumulative browser phases share one absolute lifecycle deadline', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  deps.acquireBrowser = async () => {
    await new Promise((resolve) => setTimeout(resolve, 7));
    return { executablePath: '/candidate/staged/chrome-headless-shell', cleanup: async () => { deps.state.cleanupCalls += 1; } };
  };
  deps.waitForDevToolsPort = async () => { await new Promise((resolve) => setTimeout(resolve, 7)); return '9222\n'; };
  deps.fetchDevToolsTarget = async () => { await new Promise((resolve) => setTimeout(resolve, 7)); return { webSocketDebuggerUrl: 'ws://candidate-bound' }; };
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/profile', label: 'global gate', operationTimeoutMs: 20, ...deps,
  }, async () => assert.fail('callback must not outlive the global deadline')), 75), /global gate lifecycle timed out after 20ms/);
});

test('stubborn child retains the lease when it cannot be reaped before the lifecycle deadline', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies({ spawnBrowser: () => {
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.kill = () => true;
    deps.state.child = child;
    return child;
  } });
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/profile', label: 'stubborn gate', operationTimeoutMs: 25, ...deps,
  }, async () => { throw new Error('trigger cleanup'); }), 75), /lease retained.*child.*not reaped/i);
  assert.equal(deps.state.cleanupCalls, 0);
  assert.equal(deps.state.socket.readyState, 3);
});

test('SIGKILL reap completes before candidate lease cleanup', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const events = [];
  const deps = dependencies({ spawnBrowser: () => {
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.kill = (signal) => {
      events.push(signal);
      if (signal === 'SIGKILL') queueMicrotask(() => {
        child.signalCode = signal;
        child.emit('exit', null, signal);
      });
      return true;
    };
    deps.state.child = child;
    return child;
  } });
  deps.acquireBrowser = async () => ({
    executablePath: '/candidate/staged/chrome-headless-shell',
    cleanup: async () => { events.push('cleanup'); deps.state.cleanupCalls += 1; },
  });
  await assert.rejects(withCandidateBoundBrowser({
    profileDir: '/tmp/profile', label: 'reap gate', operationTimeoutMs: 100, ...deps,
  }, async () => { throw new Error('trigger cleanup'); }), /trigger cleanup/);
  assert.deepEqual(events, ['SIGTERM', 'SIGKILL', 'cleanup']);
});

test('silent socket shutdown cannot starve child reap or candidate cleanup', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const events = [];
  const deps = dependencies({ spawnBrowser: () => {
    const child = new EventEmitter();
    child.exitCode = null;
    child.signalCode = null;
    child.kill = (signal) => {
      events.push(signal);
      if (signal === 'SIGTERM') setTimeout(() => {
        child.signalCode = signal;
        child.emit('exit', null, signal);
      }, 1);
      return true;
    };
    deps.state.child = child;
    return child;
  } });
  deps.createDevToolsSocket = () => {
    const socket = fakeSocket();
    socket.close = () => {};
    socket.terminate = () => { events.push('terminate'); socket.readyState = 3; };
    deps.state.socket = socket;
    return socket;
  };
  deps.acquireBrowser = async () => ({
    executablePath: '/candidate/staged/chrome-headless-shell',
    cleanup: async () => { events.push('cleanup'); deps.state.cleanupCalls += 1; },
  });
  await withCandidateBoundBrowser({
    profileDir: '/tmp/profile', label: 'silent socket gate', operationTimeoutMs: 100, ...deps,
  }, async () => {});
  assert.deepEqual(events, ['SIGTERM', 'terminate', 'cleanup']);
  assert.equal(deps.state.cleanupCalls, 1);
});

test('reserve exhaustion waits for acquisition-owned cleanup terminal error with explicit ownership', async () => {
  const { withCandidateBoundBrowser } = await importHarness();
  const deps = dependencies();
  let cleanupActive = false;
  deps.acquireBrowser = ({ signal, deadline }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => {
      cleanupActive = true;
      setTimeout(() => {
        cleanupActive = false;
        const error = new Error('owned cleanup could not finish');
        error.retainedLeasePath = '/tmp/auditable-retained-browser-lease';
        error.leaseOwner = 'test acquisition';
        reject(error);
      }, Math.max(0, deadline - Date.now()));
    }, { once: true });
  });
  await assert.rejects(outerWatchdog(withCandidateBoundBrowser({
    profileDir: '/tmp/profile', label: 'owned gate', operationTimeoutMs: 20, ...deps,
  }, async () => {}), 75), (error) => {
    assert.equal(error.retainedLeasePath, '/tmp/auditable-retained-browser-lease');
    assert.equal(error.leaseOwner, 'test acquisition');
    return true;
  });
  assert.equal(cleanupActive, false);
});
