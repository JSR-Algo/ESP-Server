import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import WebSocket from 'ws';

import { startPinnedRobotPreviewChromiumAcquisition } from '../robot-preview-browser.mjs';

const DEFAULT_OPERATION_TIMEOUT_MS = 60000;
const DEFAULT_READINESS_TIMEOUT_MS = 10000;

function delay(timeoutMs) {
  return new Promise((resolve) => setTimeout(resolve, timeoutMs));
}

function waitForEventOrTimeout(emitter, event, timeoutMs, action = () => {}, isComplete = () => false) {
  return new Promise((resolve) => {
    let timer;
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      emitter.off(event, onEvent);
      resolve(value);
    };
    const onEvent = () => finish(true);
    emitter.once(event, onEvent);
    timer = setTimeout(() => finish(false), timeoutMs);
    action();
    if (isComplete()) finish(true);
  });
}

async function waitForChildExit(child, timeoutMs) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;
  return waitForEventOrTimeout(child, 'exit', timeoutMs);
}

async function stopChild(child, remainingMs, label) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;
  child.kill('SIGTERM');
  const termBudgetMs = Math.max(0, Math.floor(remainingMs() / 2));
  if (!await waitForChildExit(child, termBudgetMs)) {
    child.kill('SIGKILL');
    if (!await waitForChildExit(child, Math.max(0, remainingMs()))) {
      const error = new Error(`${label} lease retained because Chromium child was not reaped before the lifecycle deadline`);
      error.leaseOwner = label;
      error.childPid = child.pid;
      throw error;
    }
  }
  return true;
}

async function closeSocket(socket, timeoutMs = 500) {
  if (!socket || socket.readyState === 3) return;
  const deadline = Date.now() + Math.max(0, timeoutMs);
  const ignoreClosingError = () => {};
  socket.on('error', ignoreClosingError);
  try {
    const closed = await waitForEventOrTimeout(
      socket, 'close', Math.max(0, Math.floor(timeoutMs / 2)), () => socket.close(), () => socket.readyState === 3
    );
    if (!closed) {
      socket.terminate();
      const terminated = socket.readyState === 3 || await waitForEventOrTimeout(
        socket, 'close', Math.max(0, deadline - Date.now()), () => {}, () => socket.readyState === 3
      );
      if (!terminated) throw new Error('DevTools socket was not reaped before the lifecycle deadline');
    }
  } finally {
    socket.off('error', ignoreClosingError);
  }
}

function ownDevToolsSocket(socket) {
  let started = false;
  let resolveCompletion;
  let rejectCompletion;
  const completion = new Promise((resolve, reject) => {
    resolveCompletion = resolve;
    rejectCompletion = reject;
  });
  completion.catch(() => {});
  return {
    socket,
    completion,
    cancel(timeoutMs) {
      if (started) return;
      started = true;
      closeSocket(socket, timeoutMs).then(resolveCompletion, rejectCompletion);
    },
  };
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
  startBrowserAcquisition = startPinnedRobotPreviewChromiumAcquisition,
  spawnBrowser = spawn,
  waitForDevToolsPort = defaultWaitForDevToolsPort,
  fetchDevToolsTarget = (debugPort, signal) => fetch(
    `http://127.0.0.1:${debugPort}/json/new?about:blank`, { method: 'PUT', signal }
  ).then((response) => response.json()),
  startDevToolsSocket = (url) => {
    return ownDevToolsSocket(new WebSocket(url));
  },
  operationTimeoutMs = DEFAULT_OPERATION_TIMEOUT_MS,
  readinessTimeoutMs = DEFAULT_READINESS_TIMEOUT_MS,
  readinessPollMs = 50,
  onMessage = () => {},
}, run) {
  let lease;
  let acquisition;
  let acquisitionOwned = false;
  let child;
  let socket;
  let socketHandle;
  const pending = new Map();
  let commandId = 0;
  let rejectLifecycle;
  const lifecycleDeadline = Date.now() + operationTimeoutMs;
  const cleanupReserveMs = Math.min(1000, Math.max(5, Math.floor(operationTimeoutMs / 5)));
  const childReapReserveMs = Math.max(1, Math.floor(cleanupReserveMs / 2));
  const socketCloseReserveMs = Math.max(1, Math.floor(cleanupReserveMs / 5));
  const runtimeDeadline = lifecycleDeadline - cleanupReserveMs;
  const remainingMs = () => Math.max(0, lifecycleDeadline - Date.now());
  const runtimeRemainingMs = () => Math.max(0, runtimeDeadline - Date.now());
  const addOwnership = (error) => {
    const retainedLeasePath = lease?.leasePath || acquisition?.retainedLeasePath
      || (lease?.executablePath ? dirname(lease.executablePath) : undefined);
    if (retainedLeasePath) error.retainedLeasePath ||= retainedLeasePath;
    error.leaseOwner ||= lease?.leaseOwner || acquisition?.leaseOwner || label;
    const workerPid = lease?.workerPid || acquisition?.workerPid;
    if (error.workerPid === undefined) {
      error.workerPid = Number.isSafeInteger(workerPid) && workerPid > 0 ? workerPid : null;
    }
    if (error.childPid === undefined) {
      error.childPid = Number.isSafeInteger(child?.pid) && child.pid > 0 ? child.pid : null;
    }
    return error;
  };
  const lifecycleTimeout = () => addOwnership(new Error(`${label} lifecycle timed out after ${operationTimeoutMs}ms`));
  const lifecycleFailure = new Promise((_, reject) => { rejectLifecycle = reject; });
  lifecycleFailure.catch(() => {});

  const failLifecycle = (error) => {
    const ownedError = addOwnership(error);
    rejectLifecycle(ownedError);
    for (const callbacks of pending.values()) callbacks.reject(ownedError);
    pending.clear();
  };
  const lifecycleBounded = async (
    operation, operationLabel, timeoutMs = operationTimeoutMs, onTimeout = () => {}, budgetRemainingMs = runtimeRemainingMs
  ) => {
    const lifecycleRemainingMs = budgetRemainingMs();
    if (lifecycleRemainingMs <= 0) {
      Promise.resolve(operation).catch(() => {});
      onTimeout();
      throw lifecycleTimeout();
    }
    const effectiveTimeoutMs = Math.min(timeoutMs, lifecycleRemainingMs);
    let timer;
    const timeout = new Promise((_, reject) => {
      timer = setTimeout(() => {
        onTimeout();
        reject(effectiveTimeoutMs === lifecycleRemainingMs
          ? lifecycleTimeout()
          : new Error(`${operationLabel} timed out after ${effectiveTimeoutMs}ms`));
      }, effectiveTimeoutMs);
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
    try {
      try {
        acquisition = startBrowserAcquisition({ deadline: runtimeDeadline, cleanupDeadline: lifecycleDeadline });
        if (!acquisition || typeof acquisition !== 'object' || typeof acquisition.then === 'function'
          || typeof acquisition.cancel !== 'function' || typeof acquisition.completion?.then !== 'function') {
          throw new Error(`${label} candidate browser acquisition starter must synchronously return an owned handle`);
        }
        acquisitionOwned = true;
        lease = await lifecycleBounded(
          acquisition.completion, `${label} candidate browser acquisition`, operationTimeoutMs
        );
      } catch (error) {
        if (acquisitionOwned) {
          try {
            const cancellation = acquisition.cancel(error);
            if (typeof cancellation?.then === 'function') {
              throw new Error(`${label} candidate browser acquisition cancel must synchronously start owned cleanup`);
            }
            lease = await lifecycleBounded(
              acquisition.completion, `${label} candidate browser acquisition cancellation`,
              operationTimeoutMs, () => {}, remainingMs,
            );
          } catch (acquisitionError) {
            throw acquisitionError;
          }
        }
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
    const startedSocket = startDevToolsSocket(target.webSocketDebuggerUrl);
    socket = startedSocket?.socket;
    if (!startedSocket || typeof startedSocket !== 'object' || typeof startedSocket.then === 'function'
      || !socket || typeof startedSocket.cancel !== 'function'
      || typeof startedSocket.completion?.then !== 'function') {
      if (socket) socketHandle = ownDevToolsSocket(socket);
      throw new Error(`${label} DevTools socket starter must synchronously return an owned handle`);
    }
    socketHandle = startedSocket;
    socket.on('error', (error) => failLifecycle(new Error(`${label} DevTools socket failed: ${error.message}`)));
    socket.on('close', () => failLifecycle(new Error(`${label} DevTools socket closed`)));
    socket.on('message', (raw) => {
      try {
        const message = JSON.parse(raw);
        onMessage(message);
        if (message.id && pending.has(message.id)) {
          const callbacks = pending.get(message.id);
          pending.delete(message.id);
          message.error ? callbacks.reject(new Error(message.error.message)) : callbacks.resolve(message.result);
        }
      } catch (error) {
        failLifecycle(new Error(`${label} DevTools message failed: ${error.message}`, { cause: error }));
      }
    });
    await bounded(new Promise((resolve) => socket.once('open', resolve)), `${label} DevTools socket open`);

    const cdp = (method, params = {}, timeoutMs = operationTimeoutMs, operationLabel = `${label} CDP ${method}`) => {
      const id = ++commandId;
      const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
      socket.send(JSON.stringify({ id, method, params }));
      return bounded(response, operationLabel, timeoutMs).finally(() => pending.delete(id));
    };
    const evaluate = async (
      expression, timeoutMs = operationTimeoutMs, operationLabel = `${label} CDP Runtime.evaluate`
    ) => {
      const result = await cdp(
        'Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, timeoutMs, operationLabel
      );
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    };
    const waitForReadiness = async (expression, readinessLabel, detailExpression = null) => {
      const deadline = Date.now() + readinessTimeoutMs;
      const timedOut = (detail = '') => new Error(
        `${readinessLabel} timed out after ${readinessTimeoutMs}ms${detail}`
      );
      const readinessEvaluate = async (value, evaluationLabel) => {
        const remainingMs = deadline - Date.now();
        if (remainingMs <= 0) throw timedOut();
        const operationLabel = `${label} ${readinessLabel} ${evaluationLabel}`;
        try {
          return await evaluate(value, remainingMs, operationLabel);
        } catch (error) {
          if (Date.now() >= deadline || error.message === `${operationLabel} timed out after ${remainingMs}ms`) {
            throw timedOut();
          }
          throw error;
        }
      };
      while (Date.now() < deadline) {
        if (await readinessEvaluate(expression, 'evaluation')) return;
        const remainingMs = deadline - Date.now();
        if (remainingMs <= 0) break;
        if (detailExpression && remainingMs <= readinessPollMs) {
          const detail = await readinessEvaluate(detailExpression, 'diagnostic');
          throw timedOut(`: ${detail}`);
        }
        const pollMs = Math.min(readinessPollMs, remainingMs);
        await bounded(delay(pollMs), `${label} ${readinessLabel} poll`, pollMs);
      }
      throw timedOut();
    };

      return await bounded(
        Promise.resolve().then(() => run({ cdp, evaluate, waitForReadiness, browserExecutablePath: lease.executablePath })),
        `${label} callback`,
      );
    } catch (error) {
      throw addOwnership(error);
    }
  } finally {
    const socketDeadline = Date.now() + Math.min(socketCloseReserveMs, remainingMs());
    let socketReapError;
    if (socketHandle) {
      try {
        const cancelTimeoutMs = Math.max(0, Math.min(remainingMs(), socketDeadline - Date.now()));
        const cancellation = socketHandle.cancel(cancelTimeoutMs);
        if (typeof cancellation?.then === 'function') {
          throw new Error(`${label} DevTools socket cancel must synchronously start owned cleanup`);
        }
        await lifecycleBounded(
          socketHandle.completion,
          `${label} DevTools socket cleanup`, operationTimeoutMs, () => {},
          () => Math.max(0, Math.min(remainingMs(), socketDeadline - Date.now())),
        );
      } catch (error) {
        socketReapError = addOwnership(error);
      }
    }
    const childDeadline = Date.now() + Math.min(childReapReserveMs, remainingMs());
    let childReapError;
    try {
      await stopChild(child, () => Math.max(0, Math.min(remainingMs(), childDeadline - Date.now())), label);
    } catch (error) {
      childReapError = error;
      if (lease) {
        error.retainedLeasePath = lease.leasePath || dirname(lease.executablePath);
        error.leaseOwner = lease.leaseOwner || label;
        error.workerPid ||= lease.workerPid;
        error.childPid ||= child?.pid;
      }
    }
    if (childReapError) throw childReapError;
    if (socketReapError) throw socketReapError;
    if (lease) {
      try {
        await lifecycleBounded(
          Promise.resolve().then(() => lease.cleanup()),
          `${label} candidate browser cleanup`,
          operationTimeoutMs,
          () => {},
          remainingMs,
        );
      } catch (error) {
        error.retainedLeasePath ||= lease.leasePath || dirname(lease.executablePath);
        error.leaseOwner ||= lease.leaseOwner || label;
        error.workerPid ||= lease.workerPid;
        error.childPid ||= child?.pid;
        throw error;
      }
    }
  }
}
