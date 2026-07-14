import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
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
  executeWithSignalFinalization,
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

async function waitForPath(path, timeoutMs = 2_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await pathExists(path)) return;
    await sleep(20);
  }
  assert.fail(`path ${path} was not created`);
}

async function runSignalFixture({ signal, repeated = false, privacyFailure = false }) {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-signal-'));
  const fixture = resolve(import.meta.dirname, 'fixtures/rewards-admin-signal-fixture.mjs');
  const ready = resolve(directory, 'ready');
  const finalized = resolve(directory, 'finalized');
  const servicePidFile = resolve(directory, 'service.pid');
  const child = spawn(process.execPath, [fixture, directory], {
    env: { ...process.env, SIGNAL_FIXTURE_PRIVACY_FAILURE: privacyFailure ? '1' : '' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', (chunk) => { stderr += chunk; });
  const exitPromise = new Promise((resolveExit, reject) => {
    child.once('error', reject);
    child.once('exit', (exitCode, receivedSignal) => resolveExit([exitCode, receivedSignal]));
  });
  const boundedExit = async () => {
    let timer;
    try {
      return await Promise.race([
        exitPromise,
        new Promise((resolveTimeout, reject) => {
          timer = setTimeout(() => reject(new Error('signal fixture did not exit within 5 seconds')), 5_000);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  };

  try {
    await waitForPath(ready);
    child.kill(signal);
    if (repeated) {
      await sleep(30);
      child.kill(signal);
    }
    const [code, exitSignal] = await boundedExit();
    const servicePid = Number(await readFile(servicePidFile, 'utf8'));
    await waitForProcessExit(servicePid);
    return { code, directory, exitSignal, finalized, stderr };
  } catch (error) {
    child.kill('SIGKILL');
    await Promise.race([exitPromise, sleep(1_000)]);
    if (await pathExists(servicePidFile)) {
      const servicePid = Number(await readFile(servicePidFile, 'utf8'));
      try {
        process.kill(process.platform === 'win32' ? servicePid : -servicePid, 'SIGKILL');
      } catch (killError) {
        if (killError?.code !== 'ESRCH') throw killError;
      }
      await waitForProcessExit(servicePid);
    }
    await rm(directory, { recursive: true, force: true });
    throw new Error(`${error.message}\nFixture stderr:\n${stderr}`);
  }
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

test('Vue E2E proxy preserves the runner target over a conflicting local dotenv override', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-vue-config-'));
  const configPath = resolve(import.meta.dirname, '../../vue.config.js');
  await writeFile(resolve(directory, '.env.development.local'), 'NESTJS_TARGET=http://127.0.0.1:39999\n');

  try {
    const { stdout } = await execFileAsync(process.execPath, ['-e', [
      `const config = require(${JSON.stringify(configPath)})`,
      "process.stdout.write(config.devServer.proxy['/nestjs'].target)",
    ].join(';')], {
      cwd: directory,
      env: {
        ...process.env,
        REWARDS_ADMIN_BROWSER_E2E: '1',
        NESTJS_TARGET: 'http://127.0.0.1:41237',
      },
    });
    assert.equal(stdout, 'http://127.0.0.1:41237');
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test('Vue normal development proxy still accepts the local dotenv override', async () => {
  const directory = await mkdtemp(resolve(tmpdir(), 'rewards-admin-vue-config-'));
  const configPath = resolve(import.meta.dirname, '../../vue.config.js');
  await writeFile(resolve(directory, '.env.development.local'), 'NESTJS_TARGET=http://127.0.0.1:39999\n');

  try {
    const { stdout } = await execFileAsync(process.execPath, ['-e', [
      `const config = require(${JSON.stringify(configPath)})`,
      "process.stdout.write(config.devServer.proxy['/nestjs'].target)",
    ].join(';')], {
      cwd: directory,
      env: { ...process.env, REWARDS_ADMIN_BROWSER_E2E: '', NESTJS_TARGET: 'http://127.0.0.1:41237' },
    });
    assert.equal(stdout, 'http://127.0.0.1:39999');
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
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

test('owned command buffers and redacts split Playwright output before reporting a bounded failure', async () => {
  const lifecycle = createProcessLifecycle({ cleanupContainer: async () => undefined });
  const secrets = {
    email: 'browser-admin-secret@invalid.test',
    password: 'Browser-Password-Secret-91!',
    totp: '654321',
    session: '"nestjs_session_token":"raw-session-secret-value"',
    bearer: 'Bearer raw.bearer.secret-value',
  };
  const script = [
    `const values = ${JSON.stringify(Object.values(secrets))}`,
    "process.stdout.write(values[0].slice(0, 12))",
    "setTimeout(() => process.stdout.write(values[0].slice(12) + '\\n' + values[1] + '\\n'), 5)",
    "setTimeout(() => process.stderr.write(values.slice(2).join('\\n') + '\\npassword=structured-secret'), 10)",
    'setTimeout(() => process.exit(1), 20)',
  ].join(';');

  try {
    await assert.rejects(
      lifecycle.runTrackedCommand(process.execPath, ['-e', script], {
        stdio: ['ignore', 'pipe', 'pipe'],
        captureOutput: true,
        forbiddenValues: [secrets.email, secrets.password, secrets.totp, 'raw-session-secret-value'],
        forbiddenPatterns: lifecycle.defaultSecretPatterns,
        failureOutputTailChars: 220,
        timeout: 1_000,
      }),
      (error) => {
        assert.match(error.message, /command failed/);
        assert.equal(error.message.length < 500, true);
        for (const secret of Object.values(secrets)) assert.equal(error.message.includes(secret), false);
        assert.doesNotMatch(error.message, /structured-secret|raw\.bearer\.secret-value/i);
        return true;
      },
    );
  } finally {
    await lifecycle.cleanup();
  }
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

test('SIGTERM after raw trace and screenshot unwinds through privacy finalization before exit 143', async () => {
  const result = await runSignalFixture({ signal: 'SIGTERM' });
  try {
    assert.equal(result.code, 143);
    assert.equal(result.exitSignal, null);
    assert.equal(await pathExists(result.finalized), true);
    assert.deepEqual(await readdir(resolve(result.directory, 'artifacts')), ['summary.json']);
    assert.equal(await pathExists(resolve(result.directory, 'raw')), false);
    assert.doesNotMatch(result.stderr, /fixture-secret/);
  } finally {
    await rm(result.directory, { recursive: true, force: true });
  }
});

test('SIGINT privacy failure fails closed but preserves signal exit 130 after finalization', async () => {
  const result = await runSignalFixture({ signal: 'SIGINT', privacyFailure: true });
  try {
    assert.equal(result.code, 130);
    assert.equal(result.exitSignal, null);
    assert.equal(await pathExists(result.finalized), true);
    assert.equal(await pathExists(resolve(result.directory, 'artifacts')), false);
    assert.equal(await pathExists(resolve(result.directory, 'raw')), false);
    assert.doesNotMatch(result.stderr, /fixture-secret/);
  } finally {
    await rm(result.directory, { recursive: true, force: true });
  }
});

test('repeated SIGTERM escalates child termination without bypassing artifact cleanup', async () => {
  const result = await runSignalFixture({ signal: 'SIGTERM', repeated: true });
  try {
    assert.equal(result.code, 143);
    assert.equal(result.exitSignal, null);
    assert.equal(await pathExists(result.finalized), true);
    assert.deepEqual(await readdir(resolve(result.directory, 'artifacts')), ['summary.json']);
    assert.equal(await pathExists(resolve(result.directory, 'raw')), false);
    assert.doesNotMatch(result.stderr, /fixture-secret/);
  } finally {
    await rm(result.directory, { recursive: true, force: true });
  }
});

test('signal during artifact finalization completes privacy cleanup before setting exit code', async () => {
  const processTarget = new EventEmitter();
  processTarget.exitCode = 0;
  let aborted = 0;
  let cleaned = 0;
  let finalized = 0;
  let rawCleaned = 0;

  const outcome = await executeWithSignalFinalization({
    processTarget,
    runWorkflow: async () => 'complete',
    finalizeArtifacts: async () => {
      processTarget.emit('SIGTERM');
      await sleep(20);
      finalized += 1;
    },
    abortWorkflow: async () => { aborted += 1; },
    escalateAbort: async () => undefined,
    cleanup: async () => { cleaned += 1; },
    cleanupRawArtifacts: async () => { rawCleaned += 1; },
  });

  assert.equal(outcome.error, undefined);
  assert.equal(outcome.interrupted, true);
  assert.equal(outcome.signal, 'SIGTERM');
  assert.equal(processTarget.exitCode, 143);
  assert.equal(aborted, 1);
  assert.equal(finalized, 1);
  assert.equal(cleaned, 1);
  assert.equal(rawCleaned, 1);
});

test('cleanup and raw-cleanup failures are both redacted with exact values and structured patterns', async () => {
  const secret = 'cleanup-exact-secret';
  const outcome = await executeWithSignalFinalization({
    processTarget: new EventEmitter(),
    runWorkflow: async () => 'complete',
    finalizeArtifacts: async () => undefined,
    abortWorkflow: async () => undefined,
    escalateAbort: async () => undefined,
    cleanup: async () => { throw new Error(`cleanup password=${secret}`); },
    cleanupRawArtifacts: async () => { throw new Error('raw Bearer raw.cleanup.bearer-secret'); },
    forbiddenValues: [secret],
    forbiddenPatterns: lifecycle.defaultSecretPatterns,
  });

  assert.match(outcome.error.message, /Cleanup failed/);
  assert.match(outcome.error.message, /Raw artifact cleanup failed/);
  assert.doesNotMatch(outcome.error.message, /cleanup-exact-secret|raw\.cleanup\.bearer-secret/i);
});
