import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { acquirePinnedRobotPreviewChromium } from '../robot-preview-browser.mjs';

const DEFAULT_OPERATION_TIMEOUT_MS = 10000;
const DEFAULT_READINESS_TIMEOUT_MS = 10000;

function delay(timeoutMs) {
  return new Promise((resolve) => setTimeout(resolve, timeoutMs));
}

async function waitForChildExit(child, timeoutMs) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;
  return Promise.race([
    new Promise((resolve) => child.once('exit', () => resolve(true))),
    delay(timeoutMs).then(() => false),
  ]);
}

async function stopChild(child) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  if (!await waitForChildExit(child, 1500)) {
    child.kill('SIGKILL');
    await waitForChildExit(child, 500);
  }
}

async function closeSocket(socket) {
  if (!socket || socket.readyState === 3) return;
  const ignoreClosingError = () => {};
  socket.on('error', ignoreClosingError);
  try {
    const closed = new Promise((resolve) => socket.once('close', resolve));
    socket.close();
    if (!await Promise.race([closed.then(() => true), delay(500).then(() => false)])) socket.terminate();
  } finally {
    socket.off('error', ignoreClosingError);
  }
}

async function defaultWaitForDevToolsPort(path, timeoutMs, signal) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (signal?.aborted) throw signal.reason || new Error('DevTools readiness aborted');
    if (existsSync(path)) return readFileSync(path, 'utf8');
    await delay(50);
  }
  throw new Error(`DevToolsActivePort timed out after ${timeoutMs}ms`);
}

export async function withCandidateBoundBrowser({
  profileDir,
  label,
  acquireBrowser = acquirePinnedRobotPreviewChromium,
  spawnBrowser = spawn,
  waitForDevToolsPort = defaultWaitForDevToolsPort,
  fetchDevToolsTarget = (debugPort, signal) => fetch(
    `http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT', signal }
  ).then((response) => response.json()),
  createDevToolsSocket = async (url) => new (await import('ws')).default(url),
  operationTimeoutMs = DEFAULT_OPERATION_TIMEOUT_MS,
  readinessTimeoutMs = DEFAULT_READINESS_TIMEOUT_MS,
  readinessPollMs = 50,
  onMessage = () => {},
}, run) {
  let lease;
  let child;
  let socket;
  const pending = new Map();
  let commandId = 0;
  let rejectLifecycle;
  const lifecycleFailure = new Promise((_, reject) => { rejectLifecycle = reject; });
  lifecycleFailure.catch(() => {});

  const failLifecycle = (error) => {
    rejectLifecycle(error);
    for (const callbacks of pending.values()) callbacks.reject(error);
    pending.clear();
  };
  const lifecycleBounded = async (operation, operationLabel, timeoutMs = operationTimeoutMs, onTimeout = () => {}) => {
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        onTimeout();
        reject(new Error(`${operationLabel} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
    });
    try {
      return await Promise.race([operation, timeout]);
    } finally {
      clearTimeout(timer);
    }
  };
  const bounded = (operation, operationLabel, timeoutMs = operationTimeoutMs, onTimeout = () => {}) => lifecycleBounded(
    Promise.race([operation, lifecycleFailure]), operationLabel, timeoutMs, onTimeout
  );

  try {
    const acquisition = Promise.resolve().then(() => acquireBrowser());
    try {
      lease = await lifecycleBounded(acquisition, `${label} candidate browser acquisition`);
    } catch (error) {
      acquisition.then((lateLease) => lifecycleBounded(
        Promise.resolve().then(() => lateLease?.cleanup()),
        `${label} late candidate browser cleanup`,
      ).catch(() => {}), () => {});
      throw error;
    }
    child = spawnBrowser(lease.executablePath, [
      '--headless', '--disable-gpu', '--remote-debugging-port=0',
      `--user-data-dir=${profileDir}`, 'about:blank',
    ], { stdio: 'ignore' });
    child.once('error', (error) => failLifecycle(new Error(`${label} Chromium spawn failed: ${error.message}`)));
    child.once('exit', (code, signal) => failLifecycle(new Error(`${label} Chromium exited before completion (${signal || code})`)));

    const readinessController = new AbortController();
    let portFile;
    try {
      portFile = await bounded(
        waitForDevToolsPort(join(profileDir, 'DevToolsActivePort'), operationTimeoutMs, readinessController.signal),
        `${label} DevTools readiness`, operationTimeoutMs, () => readinessController.abort(),
      );
    } finally {
      readinessController.abort();
    }
    const [debugPort] = portFile.trim().split('\n');
    const fetchController = new AbortController();
    let target;
    try {
      target = await bounded(
        fetchDevToolsTarget(debugPort, fetchController.signal),
        `${label} DevTools target request`, operationTimeoutMs, () => fetchController.abort(),
      );
    } finally {
      fetchController.abort();
    }
    const socketCreation = Promise.resolve(createDevToolsSocket(target.webSocketDebuggerUrl));
    try {
      socket = await bounded(socketCreation, `${label} DevTools socket creation`);
    } catch (error) {
      socketCreation.then((createdSocket) => closeSocket(createdSocket).catch(() => {}), () => {});
      throw error;
    }
    socket.on('error', (error) => failLifecycle(new Error(`${label} DevTools socket failed: ${error.message}`)));
    socket.on('close', () => failLifecycle(new Error(`${label} DevTools socket closed`)));
    socket.on('message', (raw) => {
      const message = JSON.parse(raw);
      onMessage(message);
      if (message.id && pending.has(message.id)) {
        const callbacks = pending.get(message.id);
        pending.delete(message.id);
        message.error ? callbacks.reject(new Error(message.error.message)) : callbacks.resolve(message.result);
      }
    });
    await bounded(new Promise((resolve) => socket.once('open', resolve)), `${label} DevTools socket open`);

    const cdp = (method, params = {}) => {
      const id = ++commandId;
      const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      socket.send(JSON.stringify({ id, method, params }));
      return bounded(response, `${label} CDP ${method}`).finally(() => pending.delete(id));
    };
    const evaluate = async (expression) => {
      const result = await cdp('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    };
    const waitForReadiness = async (expression, readinessLabel, detailExpression = null) => {
      const deadline = Date.now() + readinessTimeoutMs;
      while (Date.now() < deadline) {
        if (await evaluate(expression)) return;
        await bounded(delay(readinessPollMs), `${label} ${readinessLabel} poll`, readinessPollMs + operationTimeoutMs);
      }
      const detail = detailExpression ? `: ${await evaluate(detailExpression)}` : '';
      throw new Error(`${readinessLabel} timed out after ${readinessTimeoutMs}ms${detail}`);
    };

    return await run({ cdp, evaluate, waitForReadiness, browserExecutablePath: lease.executablePath });
  } finally {
    await closeSocket(socket).catch(() => {});
    await stopChild(child).catch(() => {});
    if (lease) {
      await lifecycleBounded(
        Promise.resolve().then(() => lease.cleanup()),
        `${label} candidate browser cleanup`,
      );
    }
  }
}
