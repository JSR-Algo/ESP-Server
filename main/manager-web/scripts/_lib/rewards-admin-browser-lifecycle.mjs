import { spawn } from 'node:child_process';
import { createHmac } from 'node:crypto';
import { once } from 'node:events';
import { readdir, readFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { relative, resolve } from 'node:path';

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

export function createProcessLifecycle({ cleanupContainer, cleanupTimeoutMs = 5_000 }) {
  const children = new Set();
  let cleanupPromise;

  function spawnTracked(command, args, options = {}) {
    const child = spawn(command, args, {
      ...options,
      detached: process.platform !== 'win32',
    });
    children.add(child);
    const release = () => children.delete(child);
    child.once('error', release);
    child.once('exit', release);
    return child;
  }

  function signalProcessGroup(child, signal) {
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    try {
      if (process.platform === 'win32') child.kill(signal);
      else process.kill(-child.pid, signal);
    } catch (error) {
      if (error?.code !== 'ESRCH') throw error;
    }
  }

  async function terminate(child) {
    if (!child || child.exitCode !== null || child.signalCode !== null) return;
    const gracefulExit = once(child, 'exit').catch(() => undefined);
    signalProcessGroup(child, 'SIGTERM');
    await Promise.race([
      gracefulExit,
      new Promise((resolveWait) => setTimeout(resolveWait, cleanupTimeoutMs)),
    ]);
    if (child.exitCode === null && child.signalCode === null) {
      const forcedExit = once(child, 'exit').catch(() => undefined);
      signalProcessGroup(child, 'SIGKILL');
      await Promise.race([
        forcedExit,
        new Promise((resolveWait) => setTimeout(resolveWait, cleanupTimeoutMs)),
      ]);
    }
  }

  function cleanup() {
    if (cleanupPromise) return cleanupPromise;
    cleanupPromise = (async () => {
      for (const child of [...children].reverse()) await terminate(child);
      await cleanupContainer();
    })();
    return cleanupPromise;
  }

  return { cleanup, spawnTracked };
}

async function listFiles(directory, root = directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...await listFiles(path, root));
    else if (entry.isFile()) files.push({ path, name: relative(root, path) });
  }
  return files;
}

export async function scanArtifactPrivacy(directory, { forbiddenValues = [], forbiddenPatterns = [] } = {}) {
  const flagged = [];
  for (const file of await listFiles(directory)) {
    const buffer = await readFile(file.path);
    const text = buffer.toString('utf8');
    const hasForbiddenValue = forbiddenValues
      .filter((value) => typeof value === 'string' && value.length > 0)
      .some((value) => buffer.includes(Buffer.from(value)));
    const hasForbiddenPattern = forbiddenPatterns.some((pattern) => {
      pattern.lastIndex = 0;
      return pattern.test(text);
    });
    if (hasForbiddenValue || hasForbiddenPattern) flagged.push(file.name);
  }
  flagged.sort();
  return { pass: flagged.length === 0, files: flagged };
}
