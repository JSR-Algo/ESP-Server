import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import test from 'node:test';

import * as lifecycle from '../../scripts/_lib/rewards-admin-browser-lifecycle.mjs';

import {
  buildBrowserEnvironment,
  buildBackendEnvironment,
  computeTotp,
  createProcessLifecycle,
  extractListeningPort,
  findFreePort,
  isExpectedBrowserHttpFailure,
  scanArtifactPrivacy,
} from '../../scripts/_lib/rewards-admin-browser-lifecycle.mjs';

test('browser environment removes every shared Nest admin-token fallback', () => {
  const environment = buildBrowserEnvironment({
    baseEnv: {
      NESTJS_ADMIN_TOKEN: 'must-not-survive',
      VUE_APP_NESTJS_ADMIN_TOKEN: 'must-not-survive-either',
      PRESERVED: 'yes',
    },
    backendUrl: 'http://127.0.0.1:41237',
    managerPort: 43111,
  });

  assert.equal(environment.NESTJS_ADMIN_TOKEN, undefined);
  assert.equal(environment.VUE_APP_NESTJS_ADMIN_TOKEN, undefined);
  assert.equal(environment.REWARDS_ADMIN_BROWSER_E2E, '1');
  assert.equal(environment.NESTJS_TARGET, 'http://127.0.0.1:41237');
  assert.equal(environment.MANAGER_WEB_PORT, '43111');
  assert.equal(environment.PRESERVED, 'yes');
});

test('backend environment uses development key fallback when a private fixture file is absent', () => {
  const environment = buildBackendEnvironment({
    baseEnv: { ADMIN_AUTH_DISABLED: 'true', NESTJS_ADMIN_TOKEN: 'shared-token' },
    databaseUrl: 'postgresql://example',
  });

  assert.equal(environment.DATABASE_URL, 'postgresql://example');
  assert.equal(environment.PORT, '0');
  assert.equal(environment.NODE_ENV, 'development');
  assert.equal(environment.JWT_PRIVATE_KEY, undefined);
  assert.equal(environment.ADMIN_AUTH_DISABLED, undefined);
  assert.equal(environment.NESTJS_ADMIN_TOKEN, undefined);
});

test('backend readiness accepts only the real nonzero listening-port handshake', () => {
  assert.equal(extractListeningPort('tbot-backend listening on port 40123'), 40123);
  assert.equal(extractListeningPort('tbot-backend listening on port 0'), null);
  assert.equal(extractListeningPort('Nest application successfully started'), null);
});

test('TOTP generation matches the RFC 6238 SHA-1 fixture without exposing the secret', () => {
  assert.equal(
    computeTotp('GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ', new Date(59_000)),
    '287082',
  );
});

test('free-port reservation returns a connectable-range loopback port', async () => {
  const port = await findFreePort();
  assert.equal(Number.isInteger(port), true);
  assert.equal(port > 0 && port <= 65535, true);
});

test('response payload is captured before a reload-triggering action settles', async () => {
  assert.equal(typeof lifecycle.captureResponseDuringAction, 'function');

  let bodyAvailable = true;
  const response = {
    json: async () => {
      if (!bodyAvailable) throw new Error('response body invalidated by reload');
      return { data: { session_token: 'captured-before-reload' } };
    },
  };
  const waitForResponse = async () => response;
  const action = async () => {
    await Promise.resolve();
    bodyAvailable = false;
  };

  const result = await lifecycle.captureResponseDuringAction({
    waitForResponse,
    action,
    readPayload: (matchedResponse) => matchedResponse.json(),
  });

  assert.equal(result.response, response);
  assert.deepEqual(result.payload, { data: { session_token: 'captured-before-reload' } });
});

test('response capture can verify a reload response without reading its invalidated body', async () => {
  const response = { status: () => 200 };
  const result = await lifecycle.captureResponseDuringAction({
    waitForResponse: async () => response,
    action: async () => undefined,
  });

  assert.equal(result.response, response);
  assert.equal(result.payload, undefined);
});

test('browser HTTP failure classification allows only pre-auth admin 401s and known seeded asset gaps', () => {
  assert.equal(isExpectedBrowserHttpFailure({
    url: 'http://127.0.0.1:4000/nestjs/v1/admin/courses', status: 401, traceStarted: false,
  }), true);
  assert.equal(isExpectedBrowserHttpFailure({
    url: 'http://127.0.0.1:4000/nestjs/v1/admin/courses', status: 401, traceStarted: true,
  }), false);
  assert.equal(isExpectedBrowserHttpFailure({
    url: 'http://127.0.0.1:4000/assets/objects/barn.png', status: 404, traceStarted: true,
  }), true);
  assert.equal(isExpectedBrowserHttpFailure({
    url: 'http://127.0.0.1:4000/assets/objects/unexpected.png', status: 404, traceStarted: true,
  }), false);
  assert.equal(isExpectedBrowserHttpFailure({
    url: 'http://127.0.0.1:4000/nestjs/v1/admin/courses', status: 500, traceStarted: true,
  }), false);
});

test('lifecycle cleanup terminates a detached process group and runs container cleanup once', async () => {
  let cleanupCalls = 0;
  const lifecycle = createProcessLifecycle({
    cleanupContainer: async () => { cleanupCalls += 1; },
    cleanupTimeoutMs: 500,
  });
  const child = lifecycle.spawnTracked(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
    stdio: 'ignore',
  });

  await lifecycle.cleanup();
  await lifecycle.cleanup();
  await sleep(20);

  assert.notEqual(child.exitCode ?? child.signalCode, null);
  assert.equal(cleanupCalls, 1);
});

test('artifact privacy scan reports seeded credentials and session-like values', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-artifacts-'));
  const secret = 'browser-secret-value';
  await writeFile(resolve(directory, 'summary.json'), JSON.stringify({ message: secret }));

  try {
    const result = await scanArtifactPrivacy(directory, {
      forbiddenValues: [secret],
      forbiddenPatterns: [/Bearer\s+[A-Za-z0-9._-]+/i],
    });
    assert.equal(result.pass, false);
    assert.deepEqual(result.files, ['summary.json']);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('artifact privacy scan accepts sanitized deterministic summaries', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-artifacts-'));
  await writeFile(resolve(directory, 'summary.json'), JSON.stringify({ path: '/v1/admin/courses', status: 200 }));

  try {
    const result = await scanArtifactPrivacy(directory, {
      forbiddenValues: ['not-present'],
      forbiddenPatterns: [/Bearer\s+[A-Za-z0-9._-]+/i],
    });
    assert.equal(result.pass, true);
    assert.deepEqual(result.files, []);
    assert.match(await readFile(resolve(directory, 'summary.json'), 'utf8'), /courses/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
