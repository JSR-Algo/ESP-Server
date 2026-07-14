import { execFile, spawn } from 'node:child_process';
import { createHmac } from 'node:crypto';
import { access, readdir, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { relative, resolve } from 'node:path';
import { promisify } from 'node:util';

const execFileAsync = promisify(execFile);

export const defaultSecretPatterns = Object.freeze([
  /Bearer\s+[A-Za-z0-9._~+/-]{8,}/gi,
  /["']?nestjs_session_token["']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)/gi,
  /["']?(?:password|totp|mfa_secret|session_token|session_key)["']?\s*[:=]\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)/gi,
]);

function replacePatterns(text, forbiddenPatterns) {
  let sanitized = text;
  for (const pattern of forbiddenPatterns) {
    const copy = new RegExp(pattern.source, pattern.flags);
    sanitized = sanitized.replace(copy, '[REDACTED]');
  }
  return sanitized;
}

export function redactSensitiveText(value, {
  forbiddenValues = [],
  forbiddenPatterns = defaultSecretPatterns,
} = {}) {
  let text = String(value);
  for (const secret of forbiddenValues.filter((item) => typeof item === 'string' && item.length > 0)) {
    text = text.split(secret).join('[REDACTED]');
  }
  return replacePatterns(text, forbiddenPatterns);
}

export function buildBrowserEnvironment({ baseEnv, backendUrl, managerPort }) {
  const environment = {
    ...baseEnv,
    REWARDS_ADMIN_BROWSER_E2E: '1',
    NESTJS_TARGET: backendUrl,
    MANAGER_WEB_PORT: String(managerPort),
    VUE_APP_USE_CDN: 'false',
  };
  delete environment.NESTJS_ADMIN_TOKEN;
  delete environment.VUE_APP_NESTJS_ADMIN_TOKEN;
  return environment;
}

export function buildBackendEnvironment({ baseEnv, databaseUrl }) {
  const environment = {
    ...baseEnv,
    DATABASE_URL: databaseUrl,
    NODE_ENV: 'development',
    PORT: '0',
    SWAGGER_ENABLED: 'false',
  };
  delete environment.ADMIN_AUTH_DISABLED;
  delete environment.NESTJS_ADMIN_TOKEN;
  delete environment.JWT_PRIVATE_KEY;
  return environment;
}

export function extractListeningPort(logText) {
  const port = Number(String(logText).match(/tbot-backend listening on port (\d+)/)?.[1]);
  return Number.isInteger(port) && port > 0 ? port : null;
}

function base32Decode(input) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  const cleaned = String(input).toUpperCase().replace(/=+$/, '').replace(/\s/g, '');
  let bits = '';
  for (const character of cleaned) {
    const index = alphabet.indexOf(character);
    if (index < 0) throw new Error('Invalid base32 secret');
    bits += index.toString(2).padStart(5, '0');
  }
  const bytes = [];
  for (let index = 0; index + 8 <= bits.length; index += 8) {
    bytes.push(Number.parseInt(bits.slice(index, index + 8), 2));
  }
  return Buffer.from(bytes);
}

export function computeTotp(secretBase32, now = new Date()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(now.getTime() / 1000 / 30)));
  const digest = createHmac('sha1', base32Decode(secretBase32)).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary = ((digest[offset] & 0x7f) << 24)
    | ((digest[offset + 1] & 0xff) << 16)
    | ((digest[offset + 2] & 0xff) << 8)
    | (digest[offset + 3] & 0xff);
  return String(binary % 1_000_000).padStart(6, '0');
}

export function findFreePort() {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const address = server.address();
      const port = typeof address === 'object' && address ? address.port : 0;
      server.close((error) => {
        if (error) reject(error);
        else resolvePort(port);
      });
    });
  });
}

export async function captureResponseDuringAction({ waitForResponse, action, readPayload }) {
  const responsePromise = waitForResponse();
  const capturedResponsePromise = responsePromise.then(async (response) => ({
    response,
    payload: readPayload ? await readPayload(response) : undefined,
  }));
  await action();
  return capturedResponsePromise;
}

const seededAssetPaths = new Set([
  '/assets/background/barn-round-field-poster.jpg',
  '/assets/objects/barn.png',
  '/assets/objects/farm.png',
  '/assets/objects/hay.png',
  '/assets/robot/poses/bright-listening.png',
  '/assets/robot/poses/bright-teach.png',
]);

export function isExpectedBrowserHttpFailure({ url, status, traceStarted }) {
  const pathname = new URL(url).pathname;
  if (status === 401 && !traceStarted && pathname.startsWith('/nestjs/v1/admin/')) return true;
  return status === 404 && seededAssetPaths.has(pathname);
}

export function isUnexpectedBrowserConsole({ type, text }) {
  if (String(text).startsWith('Failed to load resource:')) return false;
  if (type === 'error') return true;
  return /\b(?:uncaught|unhandled|exception|(?:type|reference|range|syntax)error)\b/i.test(String(text));
}

export function createProcessLifecycle({ cleanupContainer, cleanupTimeoutMs = 5_000 }) {
  const children = new Map();
  let cleanupPromise;
  let abortSignal;

  function spawnTracked(command, args, options = {}) {
    if (abortSignal) throw new Error(`Workflow aborted by ${abortSignal}`);
    const child = spawn(command, args, {
      ...options,
      detached: process.platform !== 'win32',
    });
    const tracked = { child, pid: child.pid, retainUntilSettled: false };
    children.set(child, tracked);
    const release = () => {
      if (!tracked.retainUntilSettled) children.delete(child);
    };
    child.once('error', release);
    child.once('exit', release);
    return child;
  }

  function signalProcessGroup(tracked, signal) {
    if (!tracked?.pid) return;
    try {
      if (process.platform === 'win32') tracked.child.kill(signal);
      else process.kill(-tracked.pid, signal);
    } catch (error) {
      if (error?.code !== 'ESRCH') throw error;
    }
  }

  function processGroupExists(tracked) {
    if (!tracked?.pid) return false;
    if (process.platform === 'win32') {
      return tracked.child.exitCode === null && tracked.child.signalCode === null;
    }
    try {
      process.kill(-tracked.pid, 0);
      return true;
    } catch (error) {
      if (error?.code === 'ESRCH') return false;
      throw error;
    }
  }

  async function waitForProcessGroupExit(tracked) {
    const deadline = Date.now() + cleanupTimeoutMs;
    while (Date.now() < deadline) {
      if (!processGroupExists(tracked)) return true;
      await new Promise((resolveWait) => setTimeout(resolveWait, 20));
    }
    return !processGroupExists(tracked);
  }

  async function terminateProcessGroup(tracked) {
    if (!processGroupExists(tracked)) return;
    signalProcessGroup(tracked, 'SIGTERM');
    if (await waitForProcessGroupExit(tracked)) return;
    signalProcessGroup(tracked, 'SIGKILL');
    await waitForProcessGroupExit(tracked);
  }

  function runTrackedCommand(command, args, options = {}) {
    return new Promise((resolveRun, reject) => {
      const {
        captureOutput = false,
        failureOutputTailChars = 4_000,
        forbiddenPatterns = defaultSecretPatterns,
        forbiddenValues = [],
        timeout,
        ...spawnOptions
      } = options;
      const child = spawnTracked(command, args, spawnOptions);
      const tracked = children.get(child);
      tracked.retainUntilSettled = true;
      let stdout = '';
      let stderr = '';
      if (captureOutput) {
        child.stdout?.setEncoding('utf8');
        child.stderr?.setEncoding('utf8');
        child.stdout?.on('data', (chunk) => { stdout += chunk; });
        child.stderr?.on('data', (chunk) => { stderr += chunk; });
      }
      let settled = false;
      let timedOut = false;
      let timer;

      const settle = (callback) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        children.delete(child);
        callback();
      };
      child.once('error', (error) => {
        if (!timedOut) settle(() => reject(new Error(redactSensitiveText(error, { forbiddenValues, forbiddenPatterns }))));
      });
      child.once('exit', (code, signal) => {
        if (timedOut) return;
        settle(() => {
          if (code === 0) resolveRun({ child, code, signal, stdout, stderr });
          else {
            const rawTail = `${stdout}${stdout && stderr ? '\n' : ''}${stderr}`.slice(-failureOutputTailChars);
            const sanitizedTail = redactSensitiveText(rawTail, { forbiddenValues, forbiddenPatterns }).trim();
            const status = code ?? signal ?? 'unknown status';
            reject(new Error(`command failed with exit status ${status}${sanitizedTail ? `\nSanitized output tail:\n${sanitizedTail}` : ''}`));
          }
        });
      });
      if (Number.isFinite(timeout) && timeout > 0) {
        timer = setTimeout(async () => {
          if (settled) return;
          timedOut = true;
          try {
            await terminateProcessGroup(tracked);
            settle(() => reject(new Error(`${command} timed out after ${timeout}ms`)));
          } catch (error) {
            settle(() => reject(error));
          }
        }, timeout);
        timer.unref();
      }
    });
  }

  function cleanup() {
    if (cleanupPromise) return cleanupPromise;
    cleanupPromise = (async () => {
      for (const tracked of [...children.values()].reverse()) {
        await terminateProcessGroup(tracked);
        children.delete(tracked.child);
      }
      await cleanupContainer();
    })();
    return cleanupPromise;
  }

  function abort(signal) {
    abortSignal ??= signal;
    return cleanup();
  }

  async function forceKill() {
    for (const tracked of [...children.values()].reverse()) signalProcessGroup(tracked, 'SIGKILL');
  }

  return {
    abort,
    cleanup,
    forceKill,
    runTrackedCommand,
    spawnTracked,
    trackedCount: () => children.size,
  };
}

async function listFiles(directory, root = directory) {
  let entries;
  try {
    entries = await readdir(directory, { withFileTypes: true });
  } catch (error) {
    if (error?.code === 'ENOENT' && directory === root) return [];
    throw error;
  }
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...await listFiles(path, root));
    else if (entry.isFile()) files.push({ path, name: relative(root, path) });
  }
  return files;
}

async function privacyBuffers(file) {
  const buffers = [await readFile(file.path)];
  if (!file.name.toLowerCase().endsWith('.zip')) return buffers;
  const { stdout } = await execFileAsync('unzip', ['-Z1', file.path], { encoding: 'utf8' });
  const entries = stdout.split(/\r?\n/).filter((entry) => entry && !entry.endsWith('/'));
  for (const entry of entries) {
    const extracted = await execFileAsync('unzip', ['-p', file.path, entry], {
      encoding: 'buffer',
      maxBuffer: 100 * 1024 * 1024,
    });
    buffers.push(extracted.stdout);
  }
  return buffers;
}

export async function scanArtifactPrivacy(directory, { forbiddenValues = [], forbiddenPatterns = [] } = {}) {
  const flagged = [];
  for (const file of await listFiles(directory)) {
    try {
      const buffers = await privacyBuffers(file);
      const hasForbiddenValue = forbiddenValues
        .filter((value) => typeof value === 'string' && value.length > 0)
        .some((value) => buffers.some((buffer) => buffer.includes(Buffer.from(value))));
      const hasForbiddenPattern = forbiddenPatterns.some((pattern) => buffers.some((buffer) => {
        pattern.lastIndex = 0;
        return pattern.test(buffer.toString('utf8'));
      }));
      if (hasForbiddenValue || hasForbiddenPattern) flagged.push(file.name);
    } catch {
      flagged.push(file.name);
    }
  }
  flagged.sort();
  return { pass: flagged.length === 0, files: flagged };
}

export function sanitizeArtifactBuffer(buffer, forbiddenValues = []) {
  let text = buffer.toString('utf8');
  let changed = false;
  for (const value of forbiddenValues.filter((item) => typeof item === 'string' && item.length > 0)) {
    if (!text.includes(value)) continue;
    text = text.split(value).join('[REDACTED]');
    changed = true;
  }
  const replacements = [
    [/Bearer\s+[A-Za-z0-9._-]{8,}/gi, 'Bearer [REDACTED]'],
    [/nestjs_session_token/gi, 'redacted_session_key'],
    [/"(?:parent|household)_id"\s*:/gi, '"redacted_identifier":'],
    [
      /"(?:transcript|score|reward_timestamp|password|mfa_secret)"\s*:\s*(?:"(?:\\.|[^"\\])*"|[^,}\]]+)/gi,
      '"redacted_field":"[REDACTED]"',
    ],
  ];
  for (const [pattern, replacement] of replacements) {
    pattern.lastIndex = 0;
    if (!pattern.test(text)) continue;
    pattern.lastIndex = 0;
    text = text.replace(pattern, replacement);
    changed = true;
  }
  return changed ? Buffer.from(text) : buffer;
}

function redactError(error, forbiddenValues, forbiddenPatterns) {
  return redactSensitiveText(error instanceof Error ? error.message : error, { forbiddenValues, forbiddenPatterns });
}

export async function executeWithArtifactFinalization({
  runWorkflow,
  finalizeArtifacts,
  forbiddenValues = [],
  forbiddenPatterns = defaultSecretPatterns,
}) {
  let result;
  let workflowError;
  let artifactError;
  try {
    result = await runWorkflow();
  } catch (error) {
    workflowError = error;
  }
  try {
    await finalizeArtifacts({ workflowSucceeded: !workflowError });
  } catch (error) {
    artifactError = error;
  }
  if (workflowError || artifactError) {
    const messages = [];
    if (workflowError) messages.push(`Workflow failed: ${redactError(workflowError, forbiddenValues, forbiddenPatterns)}`);
    if (artifactError) messages.push(`Artifact finalization failed: ${redactError(artifactError, forbiddenValues, forbiddenPatterns)}`);
    throw new Error(messages.join('\n'));
  }
  return result;
}

function signalExitCode(signal) {
  return signal === 'SIGINT' ? 130 : 143;
}

export async function executeWithSignalFinalization({
  processTarget = process,
  runWorkflow,
  finalizeArtifacts,
  abortWorkflow,
  escalateAbort,
  cleanup,
  cleanupRawArtifacts = async () => undefined,
  forbiddenValues = [],
  forbiddenPatterns = defaultSecretPatterns,
}) {
  let receivedSignal;
  let executionError;
  let result;
  let rejectAbort;
  let firstAbortAction;
  let workflowSettled = false;
  const signalActions = new Set();
  const abortPromise = new Promise((resolveAbort, reject) => { rejectAbort = reject; });

  const trackSignalAction = (action) => {
    const promise = Promise.resolve().then(action);
    signalActions.add(promise);
    promise.catch(() => undefined).finally(() => signalActions.delete(promise));
    return promise;
  };
  const handlers = new Map(['SIGINT', 'SIGTERM'].map((signal) => [signal, () => {
    if (!receivedSignal) {
      receivedSignal = signal;
      firstAbortAction = trackSignalAction(() => abortWorkflow?.(signal));
      if (!workflowSettled) rejectAbort(new Error(`Workflow interrupted by ${signal}`));
      return;
    }
    trackSignalAction(() => escalateAbort?.(signal));
  }]));
  for (const [signal, handler] of handlers) processTarget.on(signal, handler);

  try {
    result = await executeWithArtifactFinalization({
      runWorkflow: async () => {
        try {
          return await Promise.race([runWorkflow(), abortPromise]);
        } catch (error) {
          if (firstAbortAction) await firstAbortAction;
          throw error;
        } finally {
          workflowSettled = true;
        }
      },
      finalizeArtifacts,
      forbiddenValues,
      forbiddenPatterns,
    });
  } catch (error) {
    executionError = error;
  } finally {
    const cleanupErrors = [];
    try {
      await cleanup();
    } catch (error) {
      cleanupErrors.push(`Cleanup failed: ${redactError(error, forbiddenValues, forbiddenPatterns)}`);
    } finally {
      try {
        await cleanupRawArtifacts();
      } catch (error) {
        cleanupErrors.push(`Raw artifact cleanup failed: ${redactError(error, forbiddenValues, forbiddenPatterns)}`);
      }
    }
    if (cleanupErrors.length) {
      const messages = [];
      if (executionError) messages.push(redactError(executionError, forbiddenValues, forbiddenPatterns));
      messages.push(...cleanupErrors);
      executionError = new Error(messages.join('\n'));
    }
    await Promise.allSettled([...signalActions]);
    for (const [signal, handler] of handlers) processTarget.off(signal, handler);
  }

  if (receivedSignal) processTarget.exitCode = signalExitCode(receivedSignal);
  return { error: executionError, interrupted: Boolean(receivedSignal), result, signal: receivedSignal };
}

export async function finalizeArtifactPrivacy(directory, {
  workflowSucceeded,
  scanPrivacy,
  allowedSuccessImages = ['final-original-immutable.png'],
}) {
  let files = [];
  try {
    files = await listFiles(directory);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }
  const allowedImages = new Set(allowedSuccessImages);
  for (const file of files) {
    const lowerName = file.name.toLowerCase();
    const unsafeBinary = lowerName.endsWith('.zip') || lowerName.endsWith('.png');
    const allowedSuccessImage = workflowSucceeded && allowedImages.has(file.name);
    if (unsafeBinary && !allowedSuccessImage && !(workflowSucceeded && lowerName.endsWith('.zip'))) {
      await rm(file.path, { force: true });
    }
  }
  try {
    const result = await scanPrivacy();
    if (!result.pass) throw new Error(`Artifact privacy scan failed: ${result.files.join(', ')}`);
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

export async function sanitizeTraceToDeliverable({ rawTrace, deliverableTrace, sanitize }) {
  try {
    await sanitize(rawTrace, deliverableTrace);
    await access(deliverableTrace);
  } catch (error) {
    await rm(deliverableTrace, { force: true });
    throw error;
  } finally {
    await rm(rawTrace, { force: true });
  }
}
