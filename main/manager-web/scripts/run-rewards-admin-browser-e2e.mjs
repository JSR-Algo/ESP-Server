import { mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  buildBrowserEnvironment,
  buildBackendEnvironment,
  computeTotp,
  createProcessLifecycle,
  defaultSecretPatterns,
  executeWithSignalFinalization,
  finalizeArtifactPrivacy,
  findFreePort,
  redactSensitiveText,
  scanArtifactPrivacy,
} from './_lib/rewards-admin-browser-lifecycle.mjs';

const managerRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const backendRoot = resolve(
  process.env.TBOT_BACKEND_WORKTREE
    ?? resolve(managerRoot, '../../../tbot-backend-rewards-final'),
);
const artifactDir = resolve(managerRoot, 'output/playwright/rewards-admin-roundtrip');
const rawTraceDir = resolve(tmpdir(), `tbot-rewards-admin-raw-trace-${process.pid}`);
const containerName = `tbot-rewards-admin-browser-${process.pid}`;
const postgresImage = process.env.TBOT_REWARDS_POSTGRES_IMAGE ?? 'postgres:16-alpine';
const adminId = '00000008-0001-0000-0000-000000000001';
const adminEmail = 'rewards-admin-browser@invalid.test';
const adminPassword = 'RewardsAdminBrowser-E2E-Only-93!';
const adminMfaSecret = 'JBSWY3DPEHPK3PXP';
const childLogs = new Map();
let browserTotp = '';

async function removeContainer() {
  try {
    await outputCommand('docker', ['rm', '-f', containerName], { timeout: 5_000 });
  } catch {
    // The container may not have been created yet, or --rm may have removed it.
  }
}

const lifecycle = createProcessLifecycle({ cleanupContainer: removeContainer });

function captureLogs(child, label) {
  const chunks = [];
  childLogs.set(label, chunks);
  for (const stream of [child.stdout, child.stderr]) {
    if (!stream) continue;
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      chunks.push(chunk);
      if (chunks.length > 100) chunks.shift();
    });
  }
  return chunks;
}

function runCommand(command, args, options = {}) {
  return lifecycle.runTrackedCommand(command, args, {
    ...options,
    env: options.env ?? process.env,
    stdio: options.stdio ?? 'inherit',
    timeout: options.timeout ?? 180_000,
  });
}

async function outputCommand(command, args, options = {}) {
  const result = await lifecycle.runTrackedCommand(command, args, {
    cwd: options.cwd,
    env: options.env ?? process.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    captureOutput: true,
    timeout: options.timeout ?? 30_000,
  });
  return result.stdout.trim();
}

async function waitForDatabase() {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      await outputCommand('docker', [
        'exec', '-e', 'PGCONNECT_TIMEOUT=1', containerName,
        'psql', '-X', '-U', 'tbot', '-d', 'tbot', '-Atqc', 'SELECT 1',
      ], { timeout: 1_500 });
      return;
    } catch {
      await new Promise((resolveWait) => setTimeout(resolveWait, 400));
    }
  }
  throw new Error('Disposable PostgreSQL did not become queryable within 30 seconds');
}

async function waitForHttp(url, child, label) {
  const deadline = Date.now() + 45_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null || child.signalCode !== null) {
      throw new Error(`${label} exited before readiness\n${(childLogs.get(label) || []).join('')}`);
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(700) });
      if (response.status >= 200 && response.status < 500) return;
    } catch {
      // The socket is expected to reject while the service boots.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 300));
  }
  throw new Error(`${label} readiness timed out\n${(childLogs.get(label) || []).join('')}`);
}

async function startBackend(databaseUrl, backendPort) {
  const environment = buildBackendEnvironment({
    baseEnv: process.env,
    databaseUrl,
    backendPort,
    rolloutAdminId: adminId,
  });

  const backend = lifecycle.spawnTracked('npm', ['start'], {
    cwd: backendRoot,
    env: environment,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  captureLogs(backend, 'backend');
  await waitForHttp(`http://127.0.0.1:${backendPort}/v1/health`, backend, 'backend');
  return { backend, url: `http://127.0.0.1:${backendPort}` };
}

async function seedAdmin(databaseUrl) {
  const seedScript = `
    const pg = await import('pg');
    const argon2 = await import('argon2');
    const pool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
    const passwordHash = await argon2.hash(process.env.E2E_ADMIN_PASSWORD);
    const client = await pool.connect();
    try {
      await client.query('BEGIN');
      await client.query(
        \`INSERT INTO admin_users (id, email, password_hash, role, status, mfa_enabled, mfa_secret, can_author_lessons)
         VALUES ($1, $2, $3, 'super_admin', 'active', true, $4, true)\`,
        [process.env.E2E_ADMIN_ID, process.env.E2E_ADMIN_EMAIL, passwordHash, process.env.E2E_ADMIN_MFA_SECRET],
      );
      await client.query(
        \`INSERT INTO admin_role_assignments
           (admin_user_id, role, status, granted_by_admin_id, reason)
         VALUES ($1, 'super_admin', 'active', $1, 'disposable browser proof')\`,
        [process.env.E2E_ADMIN_ID],
      );
      const canonical = await client.query(
        \`SELECT l.id FROM lessons l JOIN courses c ON c.id = l.course_id
          WHERE c.course_key = 'w01-place-words' AND l.lesson_key = 'w01-d01-barn-say-it'\`,
      );
      if (canonical.rowCount !== 1) throw new Error('canonical lesson seed missing');
      const canonicalLessonId = canonical.rows[0].id;
      await client.query(
        \`UPDATE lesson_steps
            SET step_body = jsonb_set(COALESCE(step_body, '{}'::jsonb), '{durationSec}', '5'::jsonb, true)
          WHERE lesson_id = $1\`,
        [canonicalLessonId],
      );
      await client.query(
        \`UPDATE lesson_steps
            SET step_body = COALESCE(step_body, '{}'::jsonb) || '{"teachingWord":{"text":"FARM"}}'::jsonb
          WHERE lesson_id = $1 AND step_index = 1\`,
        [canonicalLessonId],
      );
      await client.query(
        \`UPDATE lesson_steps
            SET step_body = jsonb_set(COALESCE(step_body, '{}'::jsonb), '{terminal}', 'true'::jsonb, true)
          WHERE lesson_id = $1
            AND step_index = (SELECT MAX(step_index) FROM lesson_steps WHERE lesson_id = $1)\`,
        [canonicalLessonId],
      );
      await client.query('COMMIT');
    } catch (error) {
      await client.query('ROLLBACK');
      throw error;
    } finally {
      client.release();
      await pool.end();
    }
  `;
  await runCommand(process.execPath, ['--input-type=module', '-e', seedScript], {
    cwd: backendRoot,
    env: {
      ...process.env,
      DATABASE_URL: databaseUrl,
      E2E_ADMIN_ID: adminId,
      E2E_ADMIN_EMAIL: adminEmail,
      E2E_ADMIN_PASSWORD: adminPassword,
      E2E_ADMIN_MFA_SECRET: adminMfaSecret,
    },
    stdio: ['ignore', 'ignore', 'inherit'],
    timeout: 30_000,
  });
}

async function startManager(backendUrl, port) {
  const environment = buildBrowserEnvironment({ baseEnv: process.env, backendUrl, managerPort: port });
  const manager = lifecycle.spawnTracked('npm', ['run', 'serve', '--', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: managerRoot,
    env: environment,
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  captureLogs(manager, 'manager-web');
  await waitForHttp(`http://127.0.0.1:${port}/login`, manager, 'manager-web');
  return manager;
}

async function runPrivacyScan() {
  if (process.env.REWARDS_ADMIN_FAILURE_INJECTION === 'privacy-scanner') {
    throw new Error('Injected artifact privacy scanner failure');
  }
  const result = await scanArtifactPrivacy(artifactDir, {
    forbiddenValues: [adminEmail, adminPassword, adminMfaSecret, browserTotp],
    forbiddenPatterns: [
      /Bearer\s+[A-Za-z0-9._-]{16,}/i,
      /nestjs_session_token/i,
      /"(?:parent|household)_id"\s*:/i,
      /"(?:transcript|score|reward_timestamp|password|mfa_secret)"\s*:/i,
    ],
  });
  if (!result.pass) throw new Error(`Artifact privacy scan failed: ${result.files.join(', ')}`);
  return result;
}

const redactText = (value) => redactSensitiveText(value, {
    forbiddenValues: [adminEmail, adminPassword, adminMfaSecret, browserTotp],
    forbiddenPatterns: defaultSecretPatterns,
  });

const forbiddenArtifactValues = [adminEmail, adminPassword, adminMfaSecret];

const outcome = await executeWithSignalFinalization({
  processTarget: process,
  abortWorkflow: (signal) => lifecycle.abort(signal),
  escalateAbort: () => lifecycle.forceKill(),
  runWorkflow: async () => {
    await rm(artifactDir, { recursive: true, force: true });
    await rm(rawTraceDir, { recursive: true, force: true });
    await mkdir(rawTraceDir, { recursive: true });
    await outputCommand('docker', [
      'run', '--rm', '-d', '--name', containerName,
      '-e', 'POSTGRES_USER=tbot',
      '-e', 'POSTGRES_PASSWORD=tbot',
      '-e', 'POSTGRES_DB=tbot',
      '-p', '127.0.0.1::5432',
      postgresImage,
    ], { timeout: 30_000 });
    await waitForDatabase();
    const portOutput = await outputCommand('docker', ['port', containerName, '5432/tcp']);
    const postgresPort = portOutput.match(/:(\d+)$/)?.[1];
    if (!postgresPort) throw new Error(`Unable to parse disposable PostgreSQL port: ${portOutput}`);
    const databaseUrl = `postgresql://tbot:tbot@127.0.0.1:${postgresPort}/tbot`;

    await runCommand('npm', ['run', 'migrate'], {
      cwd: backendRoot,
      env: { ...process.env, DATABASE_URL: databaseUrl },
      timeout: 180_000,
    });
    await seedAdmin(databaseUrl);
    await runCommand('npm', ['run', 'build'], { cwd: backendRoot, timeout: 180_000 });
    const backendPort = await findFreePort();
    const backend = await startBackend(databaseUrl, backendPort);
    const managerPort = await findFreePort();
    await startManager(backend.url, managerPort);
    browserTotp = computeTotp(adminMfaSecret);
    forbiddenArtifactValues.push(browserTotp);

    await runCommand('npm', ['exec', '--', 'playwright', 'test', '--config=playwright.rewards.config.js'], {
      cwd: managerRoot,
      env: {
        ...buildBrowserEnvironment({ baseEnv: process.env, backendUrl: backend.url, managerPort }),
        REWARDS_ADMIN_BASE_URL: `http://127.0.0.1:${managerPort}`,
        REWARDS_ADMIN_ARTIFACT_DIR: artifactDir,
        REWARDS_ADMIN_RAW_TRACE_DIR: rawTraceDir,
        REWARDS_ADMIN_EMAIL: adminEmail,
        REWARDS_ADMIN_PASSWORD: adminPassword,
        REWARDS_ADMIN_TOTP: browserTotp,
        REWARDS_ADMIN_FAILURE_INJECTION: process.env.REWARDS_ADMIN_FAILURE_INJECTION ?? '',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      captureOutput: true,
      forbiddenValues: forbiddenArtifactValues,
      forbiddenPatterns: defaultSecretPatterns,
      failureOutputTailChars: 8_000,
      timeout: 240_000,
    });
  },
  finalizeArtifacts: ({ workflowSucceeded }) => finalizeArtifactPrivacy(artifactDir, {
    workflowSucceeded,
    scanPrivacy: runPrivacyScan,
  }),
  cleanup: () => lifecycle.cleanup(),
  cleanupRawArtifacts: () => rm(rawTraceDir, { recursive: true, force: true }),
  forbiddenValues: forbiddenArtifactValues,
  forbiddenPatterns: defaultSecretPatterns,
});

try {
  if (outcome.error) throw outcome.error;
  console.info('Authenticated rewards admin browser round-trip passed with sanitized artifacts.');
} catch (error) {
  for (const [label, logs] of childLogs) {
    if (logs.length) process.stderr.write(`\n${label} log tail:\n${redactText(logs.join('').slice(-8_000))}`);
  }
  if (!outcome.interrupted) throw error;
  process.stderr.write(`${redactText(error instanceof Error ? error.message : error)}\n`);
}
