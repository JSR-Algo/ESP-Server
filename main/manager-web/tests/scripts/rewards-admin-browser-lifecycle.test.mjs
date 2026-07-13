import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { access, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import test from 'node:test';
import { promisify } from 'node:util';

import * as lifecycle from '../../scripts/_lib/rewards-admin-browser-lifecycle.mjs';

import {
  buildBrowserEnvironment,
  buildBackendEnvironment,
  computeTotp,
  createProcessLifecycle,
  executeWithArtifactFinalization,
  extractListeningPort,
  finalizeArtifactPrivacy,
  findFreePort,
  isExpectedBrowserHttpFailure,
  sanitizeArtifactBuffer,
  sanitizeTraceToDeliverable,
  scanArtifactPrivacy,
} from '../../scripts/_lib/rewards-admin-browser-lifecycle.mjs';

const execFileAsync = promisify(execFile);

async function pathExists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function processExists(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    if (error?.code === 'ESRCH') return false;
    throw error;
  }
}

async function waitForProcessExit(pid, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!processExists(pid)) return;
    await sleep(20);
  }
  assert.fail(`process ${pid} survived cleanup`);
}

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

test('owned command direct exit settles once and leaves no tracked process', async () => {
  const lifecycle = createProcessLifecycle({ cleanupContainer: async () => undefined });
  const result = await lifecycle.runTrackedCommand(process.execPath, ['-e', 'process.exit(0)'], {
    stdio: 'ignore',
    timeout: 1_000,
  });

  assert.equal(result.code, 0);
  assert.equal(lifecycle.trackedCount(), 0);
  await lifecycle.cleanup();
});

test('owned command timeout kills detached shell parent and TERM-resistant grandchild before untracking', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-timeout-'));
  const parentPidFile = resolve(directory, 'parent.pid');
  const grandchildPidFile = resolve(directory, 'grandchild.pid');
  const lifecycle = createProcessLifecycle({
    cleanupContainer: async () => undefined,
    cleanupTimeoutMs: 150,
  });
  const grandchildScript = [
    "const fs = require('node:fs')",
    `fs.writeFileSync(${JSON.stringify(grandchildPidFile)}, String(process.pid))`,
    "process.on('SIGTERM', () => {})",
    'setInterval(() => {}, 1000)',
  ].join(';');
  const shellScript = [
    `echo $$ > ${JSON.stringify(parentPidFile)}`,
    `${JSON.stringify(process.execPath)} -e ${JSON.stringify(grandchildScript)} &`,
    'wait',
  ].join('\n');

  try {
    await assert.rejects(
      lifecycle.runTrackedCommand('sh', ['-c', shellScript], {
        stdio: 'ignore',
        timeout: 300,
      }),
      /timed out after 300ms/,
    );
    const parentPid = Number(await readFile(parentPidFile, 'utf8'));
    const grandchildPid = Number(await readFile(grandchildPidFile, 'utf8'));
    await waitForProcessExit(parentPid);
    await waitForProcessExit(grandchildPid);
    assert.equal(lifecycle.trackedCount(), 0);
  } finally {
    await lifecycle.cleanup();
    await rm(directory, { recursive: true, force: true });
  }
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

test('artifact privacy scan accepts an absent artifact directory', async () => {
  const directory = resolve(tmpdir(), `rewards-admin-absent-${process.pid}-${Date.now()}`);
  const result = await scanArtifactPrivacy(directory, { forbiddenValues: ['not-present'] });
  assert.deepEqual(result, { pass: true, files: [] });
});

test('artifact privacy scan inspects compressed trace entries', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-artifacts-'));
  const traceContents = resolve(directory, 'trace-contents');
  const trace = resolve(directory, 'trace.zip');
  const secret = 'compressed-session-secret';
  await mkdir(traceContents);
  await writeFile(resolve(traceContents, 'trace.network'), `Authorization: Bearer ${secret}`);
  await execFileAsync('zip', ['-qr', trace, '.'], { cwd: traceContents });
  await rm(traceContents, { recursive: true, force: true });

  try {
    const result = await scanArtifactPrivacy(directory, { forbiddenValues: [secret] });
    assert.deepEqual(result, { pass: false, files: ['trace.zip'] });
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('artifact sanitizer redacts exact credentials and structured privacy fields without breaking JSON keys', () => {
  const secret = 'session-secret';
  const source = Buffer.from(JSON.stringify({
    authorization: `Bearer ${secret}`,
    nestjs_session_token: secret,
    score: 0.87,
    transcript: 'child speech',
    password: 'fixture-password',
    safe: 'kept',
  }));
  const sanitized = sanitizeArtifactBuffer(source, [secret, 'fixture-password']).toString('utf8');

  assert.doesNotMatch(sanitized, /session-secret|fixture-password|Bearer\s+[A-Za-z0-9._-]{8,}/i);
  assert.doesNotMatch(sanitized, /nestjs_session_token|"(?:transcript|score|password)"\s*:/i);
  assert.match(sanitized, /"safe":"kept"/);
  assert.doesNotThrow(() => JSON.parse(sanitized));
});

test('Playwright nonzero removes unchecked screenshots and traces before reporting the original error', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-artifacts-'));
  const token = 'raw-session-token-that-must-not-remain';
  await writeFile(resolve(directory, 'last-state.png'), `pixels:${token}`);
  await writeFile(resolve(directory, 'trace.zip'), `trace:${token}`);
  await writeFile(resolve(directory, 'request-summary.json'), JSON.stringify({ failures: ['mfa failed'] }));

  try {
    await assert.rejects(
      executeWithArtifactFinalization({
        runWorkflow: async () => { throw new Error(`playwright exited with 1: ${token}`); },
        finalizeArtifacts: ({ workflowSucceeded }) => finalizeArtifactPrivacy(directory, {
          workflowSucceeded,
          scanPrivacy: () => scanArtifactPrivacy(directory, { forbiddenValues: [token] }),
        }),
        forbiddenValues: [token],
      }),
      (error) => {
        assert.match(error.message, /playwright exited with 1/);
        assert.doesNotMatch(error.message, new RegExp(token));
        return true;
      },
    );
    assert.deepEqual(await readdir(directory), ['request-summary.json']);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('MFA failure removes every credential-bearing screenshot and trace', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-artifacts-'));
  const credentials = [
    'rewards-admin-browser@invalid.test',
    'RewardsAdminBrowser-E2E-Only-93!',
    '123456',
  ];
  await writeFile(resolve(directory, 'last-state.png'), credentials.join(':'));
  await writeFile(resolve(directory, 'trace.zip'), credentials.join(':'));

  try {
    await assert.rejects(
      executeWithArtifactFinalization({
        runWorkflow: async () => { throw new Error(`MFA rejected ${credentials.join(' ')}`); },
        finalizeArtifacts: ({ workflowSucceeded }) => finalizeArtifactPrivacy(directory, {
          workflowSucceeded,
          scanPrivacy: () => scanArtifactPrivacy(directory, { forbiddenValues: credentials }),
        }),
        forbiddenValues: credentials,
      }),
      (error) => {
        assert.match(error.message, /MFA rejected/);
        for (const credential of credentials) assert.doesNotMatch(error.message, new RegExp(credential.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
        return true;
      },
    );
    assert.deepEqual(await readdir(directory), []);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('trace sanitizer failure deletes raw and deliverable traces', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-trace-'));
  const rawTrace = resolve(directory, 'raw', 'trace.zip');
  const deliverableTrace = resolve(directory, 'output', 'trace.zip');
  await mkdir(resolve(directory, 'raw'), { recursive: true });
  await mkdir(resolve(directory, 'output'), { recursive: true });
  await writeFile(rawTrace, 'raw-session-token');
  await writeFile(deliverableTrace, 'partial-session-token');

  try {
    await assert.rejects(
      sanitizeTraceToDeliverable({
        rawTrace,
        deliverableTrace,
        sanitize: async () => { throw new Error('zip sanitizer failed'); },
      }),
      /zip sanitizer failed/,
    );
    assert.equal(await pathExists(rawTrace), false);
    assert.equal(await pathExists(deliverableTrace), false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('privacy scanner failure removes deliverables and reports original plus privacy errors safely', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-artifacts-'));
  const secret = 'credential-that-must-be-redacted';
  await writeFile(resolve(directory, 'summary.json'), JSON.stringify({ message: secret }));

  await assert.rejects(
    executeWithArtifactFinalization({
      runWorkflow: async () => { throw new Error(`playwright original failure ${secret}`); },
      finalizeArtifacts: ({ workflowSucceeded }) => finalizeArtifactPrivacy(directory, {
        workflowSucceeded,
        scanPrivacy: async () => { throw new Error(`privacy scanner failed ${secret}`); },
      }),
      forbiddenValues: [secret],
    }),
    (error) => {
      assert.match(error.message, /playwright original failure/);
      assert.match(error.message, /privacy scanner failed/);
      assert.doesNotMatch(error.message, new RegExp(secret));
      return true;
    },
  );
  assert.equal(await pathExists(directory), false);
});
