const assert = require('node:assert/strict');
const test = require('node:test');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');

// Subprocess doubles exercise preparation control flow, never real service readiness.
function recoveryHarness(mode = 'preserve', fault, seedsRunning = false) {
  const env = {
    LESSON_STUDIO_E2E_STATE_MODE: mode,
    COMPOSE_PROJECT_NAME: 'isolated-recovery',
    TBOT_DOCKER_COMPOSE_EXECUTABLE: '/trusted/compose',
    TBOT_BACKEND_WORKTREE: '/candidate/backend',
    TBOT_FIRMWARE_WORKTREE: '/candidate/firmware',
    TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID: 'sha256:backend',
    TBOT_LESSON_STUDIO_WEB_IMAGE_ID: 'sha256:web',
  };
  const calls = [];
  const spawnSync = (command, args) => {
    calls.push({ command, args });
    if (args.includes('ps')) {
      const service = args.at(-1);
      return { status: 0, stdout: service.startsWith('seed-') && !seedsRunning ? '' : `${service}-container` };
    }
    if (args.includes('inspect')) {
      const service = args.at(-1).replace('-container', '');
      if (args.some(arg => arg.includes('.NetworkSettings.Ports'))) return { status: 0, stdout: JSON.stringify({
        [service === 'backend' ? '3000/tcp' : '8002/tcp']: [{ HostIp: fault === 'port' ? '0.0.0.0' : '127.0.0.1', HostPort: service === 'backend' ? '3100' : '8102' }],
      }) };
      if (args.some(arg => arg.includes('.Mounts'))) return { status: 0, stdout: JSON.stringify([
        ['asset-manifest.json', '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json'],
        ['admin', '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/admin'],
        ['esp-tft', '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/esp-tft'],
        ...['background', 'objects', 'reference', 'robot'].map(directory => [`assets/${directory}`, `/candidate/firmware/lesson/assets/${directory}`]),
      ].map(([suffix, source]) => ({ Type: 'bind', Source: fault === 'mount' ? '/stale' : source, Destination: `/usr/share/nginx/html/tvideo-demo/${suffix}`, RW: false }))) };
      return { status: 0, stdout: `${fault === 'health' ? 'unhealthy' : 'healthy'} sha256:${fault === 'image' ? 'stale' : service}` };
    }
    if (args.includes('exec')) return { status: 0, stdout: '' };
    throw new Error(`unexpected mutating command: ${args.join(' ')}`);
  };
  const module = { exports: {} };
  const sandbox = { module, __dirname, process: { env }, require: id => id === 'node:child_process' ? { spawnSync } : require(id) };
  vm.runInNewContext(readFileSync(require.resolve('./reset-lesson-studio-e2e-state.cjs'), 'utf8'), sandbox);
  return { api: module.exports, calls, env };
}

test('preserve mode checks global setup and each login without mutating throttle or database state', async () => {
  const harness = recoveryHarness();
  const load = relative => {
    const filename = require.resolve(relative);
    const localRequire = require('node:module').createRequire(filename);
    const module = { exports: {} };
    vm.runInNewContext(readFileSync(filename, 'utf8'), { module, process: { env: harness.env }, require: id => id.includes('reset-lesson-studio-e2e-state') ? harness.api : localRequire(id) });
    return module.exports;
  };
  await load('../e2e/lesson-studio/global-setup.cjs')();
  const atLogin = new Error('actual login navigation reached');
  await assert.rejects(load('../e2e/lesson-studio/helpers/session.js').loginAsLessonAuthor({ goto: async url => {
    assert.equal(url, '/login'); throw atLogin;
  } }), error => error === atLogin);
  assert.equal(harness.calls.filter(call => call.args.includes('ps')).length, 10);
  assert.equal(harness.calls.filter(call => call.args.includes('exec')).length, 0);
  assert.ok(harness.calls.every(call => !call.args.some(arg => arg.startsWith('seed-'))));
});

test('invalid state mode fails before any service subprocess', () => {
  const harness = recoveryHarness('perserve');
  assert.throws(() => harness.api.resetLessonStudioE2EState(), /STATE_MODE.*reset.*preserve/);
  assert.equal(harness.calls.length, 0);
});

test('preserve mode remains read-only even when legacy seed jobs remain running', () => {
  const harness = recoveryHarness('preserve', undefined, true);
  assert.doesNotThrow(() => harness.api.resetLessonStudioE2EState());
  assert.equal(harness.calls.filter(call => call.args.includes('exec')).length, 0);
  assert.ok(harness.calls.every(call => !call.args.some(arg => arg.startsWith('seed-'))));
});

for (const mode of ['reset', 'default']) {
  test(`${mode} mode retains all three reset commands after successful preflight`, () => {
    const harness = recoveryHarness('reset', undefined, true);
    if (mode === 'default') delete harness.env.LESSON_STUDIO_E2E_STATE_MODE;
    harness.api.resetLessonStudioE2EState();
    const commands = harness.calls.filter(call => call.args.includes('exec'));
    assert.equal(commands.length, 3);
    assert.deepEqual(commands.map(call => [call.command, ...call.args]),
      Array.from(harness.api.buildResetCommands({ projectName: 'isolated-recovery' }), command => Array.from(command)));
    assert.equal(harness.calls.filter(call => call.args.includes('ps')).length, 7);
    const firstReset = harness.calls.findIndex(call => call.args.includes('exec'));
    assert.ok(harness.calls.slice(firstReset).every(call => call.args.includes('exec')));
  });
}

test('reset mode still requires seed services before any mutation', () => {
  const harness = recoveryHarness('reset');
  assert.throws(() => harness.api.resetLessonStudioE2EState(), /seed-postgres service is not running/);
  assert.ok(harness.calls.every(call => !call.args.includes('exec')));
});

for (const mode of ['preserve', 'reset']) {
 for (const [fault, message] of [['image', /image ID mismatch/], ['mount', /asset mounts mismatch/], ['port', /port binding mismatch/], ['health', /not healthy/]]) {
  test(`${mode} mode still rejects ${fault} mismatch without resetting state`, () => {
    const harness = recoveryHarness(mode, fault, true);
    assert.throws(() => harness.api.resetLessonStudioE2EState(), message);
    assert.ok(harness.calls.every(call => !call.args.includes('exec')));
  });
 }
}

const {
  buildResetCommands,
  composeExecutableFromEnvironment,
  composeEnvironment,
  preflightLessonStudioE2EStack,
  resetOptionsFromEnvironment,
} = require('./reset-lesson-studio-e2e-state.cjs');

test('resets auth throttling through compose service names', () => {
  const commands = buildResetCommands({
    composeFile: '/repo/docs/docker/docker-compose.lesson-studio-e2e.yml',
    composeExecutable: '/trusted/docker-compose',
    projectName: 'tbot-ls-e2e',
  });

  assert.deepEqual(commands, [
    [
      '/trusted/docker-compose', '-p', 'tbot-ls-e2e', '-f',
      '/repo/docs/docker/docker-compose.lesson-studio-e2e.yml',
      'exec', '-T', 'redis', 'redis-cli', 'DEL',
      'rate_limit:ip:127.0.0.1:/user/captcha',
      'rate_limit:ip:127.0.0.1:/user/login',
    ],
    [
      '/trusted/docker-compose', '-p', 'tbot-ls-e2e', '-f',
      '/repo/docs/docker/docker-compose.lesson-studio-e2e.yml',
      'exec', '-T', 'redis', 'redis-cli', 'EVAL',
      "local keys=redis.call('keys','rl:*'); if #keys > 0 then return redis.call('del',unpack(keys)) end return 0",
      '0',
    ],
    [
      '/trusted/docker-compose', '-p', 'tbot-ls-e2e', '-f',
      '/repo/docs/docker/docker-compose.lesson-studio-e2e.yml',
      'exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1',
      '-U', 'tbot', '-d', 'tbot', '-c',
      "DELETE FROM admin_login_attempts WHERE email IN ('lesson-author-e2e@local.invalid','lesson-author-b-e2e@local.invalid','lesson-manager-e2e@local.invalid');",
    ],
  ]);
});

test('uses only an explicit absolute candidate-bound Compose executable in CI', () => {
  assert.equal(composeExecutableFromEnvironment({
    CI: '1',
    TBOT_DOCKER_COMPOSE_EXECUTABLE: '/candidate/tools/docker-compose',
  }), '/candidate/tools/docker-compose');
  assert.throws(
    () => composeExecutableFromEnvironment({ CI: '1' }),
    /TBOT_DOCKER_COMPOSE_EXECUTABLE is required/,
  );
  assert.throws(
    () => composeExecutableFromEnvironment({
      CI: '1', TBOT_DOCKER_COMPOSE_EXECUTABLE: 'docker-compose',
    }),
    /absolute executable path/,
  );
  assert.equal(composeExecutableFromEnvironment({}), 'docker-compose');
  assert.throws(
    () => composeExecutableFromEnvironment({}, { requireExplicit: true }),
    /TBOT_DOCKER_COMPOSE_EXECUTABLE is required/,
  );
});

test('reset project precedence is standard Compose, custom fallback, then default', () => {
  assert.deepEqual(resetOptionsFromEnvironment({
    COMPOSE_PROJECT_NAME: 'standard-isolated',
  }), {
    projectName: 'standard-isolated',
  });
  assert.deepEqual(resetOptionsFromEnvironment({
    LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME: 'task14-isolated',
    COMPOSE_PROJECT_NAME: 'standard-isolated',
  }), {
    projectName: 'standard-isolated',
  });
  assert.deepEqual(resetOptionsFromEnvironment({
    LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME: 'task14-isolated',
  }), {
    projectName: 'task14-isolated',
  });
  assert.deepEqual(resetOptionsFromEnvironment({}), {});
});

test('reset and global setup supply parse-only fallbacks without overriding live settings', () => {
  assert.equal(typeof composeEnvironment, 'function');
  assert.deepEqual(composeEnvironment({ KEEP: 'yes' }), {
    KEEP: 'yes',
    JWT_PUBLIC_KEY: 'not-used-by-e2e-reset',
    TBOT_DEVICE_MINT_SECRET: 'not-used-by-e2e-reset',
    LESSON_ASSET_ORIGIN_BASE: 'http://127.0.0.1:8102/tvideo-demo',
    ROBOT_ESP_BASE_URL: 'not-used-by-e2e-reset',
  });
  assert.deepEqual(composeEnvironment({
    LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME: 'task14-isolated',
    LESSON_STUDIO_E2E_BASE_URL: 'http://127.0.0.1:18102',
    LESSON_STUDIO_E2E_RESOURCE_PREFIX: 'task14-isolated',
    LESSON_STUDIO_E2E_BACKEND_HOST_PORT: '13100',
    LESSON_STUDIO_E2E_WEB_HOST_PORT: '18102',
  }), {
    LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME: 'task14-isolated',
    LESSON_STUDIO_E2E_BASE_URL: 'http://127.0.0.1:18102',
    LESSON_STUDIO_E2E_RESOURCE_PREFIX: 'task14-isolated',
    LESSON_STUDIO_E2E_BACKEND_HOST_PORT: '13100',
    LESSON_STUDIO_E2E_WEB_HOST_PORT: '18102',
    JWT_PUBLIC_KEY: 'not-used-by-e2e-reset',
    TBOT_DEVICE_MINT_SECRET: 'not-used-by-e2e-reset',
    LESSON_ASSET_ORIGIN_BASE: 'http://127.0.0.1:18102/tvideo-demo',
    ROBOT_ESP_BASE_URL: 'not-used-by-e2e-reset',
  });
  assert.deepEqual(composeEnvironment({
    JWT_PUBLIC_KEY: 'jwt',
    TBOT_DEVICE_MINT_SECRET: 'mint',
    LESSON_ASSET_ORIGIN_BASE: 'http://192.168.1.25:8180',
    ROBOT_ESP_BASE_URL: 'http://192.168.1.25:8002',
  }), {
    JWT_PUBLIC_KEY: 'jwt',
    TBOT_DEVICE_MINT_SECRET: 'mint',
    LESSON_ASSET_ORIGIN_BASE: 'http://192.168.1.25:8180',
    ROBOT_ESP_BASE_URL: 'http://192.168.1.25:8002',
  });
});

test('preflight rejects a healthy stack running a non-candidate image', () => {
  const env = {
    CI: '1',
    TBOT_DOCKER_EXECUTABLE: '/trusted/docker',
    TBOT_DOCKER_COMPOSE_EXECUTABLE: '/trusted/docker-compose',
    TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID: `sha256:${'1'.repeat(64)}`,
    TBOT_LESSON_STUDIO_WEB_IMAGE_ID: `sha256:${'2'.repeat(64)}`,
  };
  const containers = Object.fromEntries(
    ['redis', 'postgres', 'mysql', 'backend', 'seed-postgres', 'web', 'seed-mysql']
      .map((service) => [service, `${service}-container`]),
  );
  const run = (_command, args) => {
    const service = args.at(-1);
    if (args.includes('ps')) return containers[service];
    const container = args.at(-1);
    if (container === containers.backend) return `healthy ${'sha256:' + '9'.repeat(64)}`;
    if (container === containers.web) return `healthy ${env.TBOT_LESSON_STUDIO_WEB_IMAGE_ID}`;
    return 'healthy sha256:fixture';
  };

  assert.throws(
    () => preflightLessonStudioE2EStack({ env, run }),
    /started backend container image ID mismatch/,
  );
});

test('preflight accepts only a healthy candidate-bound stack', () => {
  const env = {
    CI: '1',
    TBOT_DOCKER_EXECUTABLE: '/trusted/docker',
    TBOT_DOCKER_COMPOSE_EXECUTABLE: '/trusted/docker-compose',
    TBOT_BACKEND_WORKTREE: '/candidate/backend',
    TBOT_FIRMWARE_WORKTREE: '/candidate/firmware',
    TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT: '/candidate/backend',
    TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT: '/candidate/firmware',
    LESSON_STUDIO_E2E_BACKEND_HOST_PORT: '13133',
    LESSON_STUDIO_E2E_WEB_HOST_PORT: '18133',
    TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID: `sha256:${'1'.repeat(64)}`,
    TBOT_LESSON_STUDIO_WEB_IMAGE_ID: `sha256:${'2'.repeat(64)}`,
  };
  const containers = Object.fromEntries(
    ['redis', 'postgres', 'mysql', 'backend', 'seed-postgres', 'web', 'seed-mysql']
      .map((service) => [service, `${service}-container`]),
  );
  const run = (_command, args) => {
    if (args.some((arg) => arg.includes('.NetworkSettings.Ports'))) {
      const container = args.at(-1);
      if (container === containers.backend) return JSON.stringify({
        '3000/tcp': [{ HostIp: '127.0.0.1', HostPort: '13133' }],
      });
      if (container === containers.web) return JSON.stringify({
        '8002/tcp': [{ HostIp: '127.0.0.1', HostPort: '18133' }],
      });
    }
    if (args.some((arg) => arg.includes('.Mounts'))) return JSON.stringify([
      { Type: 'bind', Source: '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json', Destination: '/usr/share/nginx/html/tvideo-demo/asset-manifest.json', RW: false },
      { Type: 'bind', Source: '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/admin', Destination: '/usr/share/nginx/html/tvideo-demo/admin', RW: false },
      { Type: 'bind', Source: '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/esp-tft', Destination: '/usr/share/nginx/html/tvideo-demo/esp-tft', RW: false },
      ...['background', 'objects', 'reference', 'robot'].map(directory => ({ Type: 'bind', Source: `/candidate/firmware/lesson/assets/${directory}`, Destination: `/usr/share/nginx/html/tvideo-demo/assets/${directory}`, RW: false })),
    ]);
    const service = args.at(-1);
    if (args.includes('ps')) return containers[service];
    const container = args.at(-1);
    if (container === containers.backend) return `healthy ${env.TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID}`;
    if (container === containers.web) return `healthy ${env.TBOT_LESSON_STUDIO_WEB_IMAGE_ID}`;
    return 'healthy sha256:fixture';
  };

  assert.doesNotThrow(() => preflightLessonStudioE2EStack({ env, run }));
});

test('preflight rejects a candidate image backed by stale lesson asset mounts', () => {
  const env = {
    CI: '1',
    TBOT_DOCKER_EXECUTABLE: '/trusted/docker',
    TBOT_DOCKER_COMPOSE_EXECUTABLE: '/trusted/docker-compose',
    TBOT_BACKEND_WORKTREE: '/candidate/backend',
    TBOT_FIRMWARE_WORKTREE: '/candidate/firmware',
    TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT: '/candidate/backend',
    TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT: '/candidate/firmware',
    LESSON_STUDIO_E2E_BACKEND_HOST_PORT: '13133',
    LESSON_STUDIO_E2E_WEB_HOST_PORT: '18133',
    TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID: `sha256:${'1'.repeat(64)}`,
    TBOT_LESSON_STUDIO_WEB_IMAGE_ID: `sha256:${'2'.repeat(64)}`,
  };
  const containers = Object.fromEntries(
    ['redis', 'postgres', 'mysql', 'backend', 'seed-postgres', 'web', 'seed-mysql']
      .map((service) => [service, `${service}-container`]),
  );
  const run = (_command, args) => {
    if (args.some((arg) => arg.includes('.NetworkSettings.Ports'))) {
      const container = args.at(-1);
      if (container === containers.backend) return JSON.stringify({
        '3000/tcp': [{ HostIp: '127.0.0.1', HostPort: '13133' }],
      });
      if (container === containers.web) return JSON.stringify({
        '8002/tcp': [{ HostIp: '127.0.0.1', HostPort: '18133' }],
      });
    }
    if (args.some((arg) => arg.includes('.Mounts'))) return JSON.stringify([
      { Type: 'bind', Source: '/stale/backend/src/lessons/fixtures/tvideo-raw-code/assets/admin', Destination: '/usr/share/nginx/html/tvideo-demo/admin', RW: false },
    ]);
    const service = args.at(-1);
    if (args.includes('ps')) return containers[service];
    const container = args.at(-1);
    if (container === containers.backend) return `healthy ${env.TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID}`;
    if (container === containers.web) return `healthy ${env.TBOT_LESSON_STUDIO_WEB_IMAGE_ID}`;
    return 'healthy sha256:fixture';
  };

  assert.throws(
    () => preflightLessonStudioE2EStack({ env, run }),
    /started web container lesson asset mounts mismatch/,
  );
});

test('preflight rejects candidate services exposed beyond loopback', () => {
  const env = {
    CI: '1',
    TBOT_DOCKER_EXECUTABLE: '/trusted/docker',
    TBOT_DOCKER_COMPOSE_EXECUTABLE: '/trusted/docker-compose',
    TBOT_BACKEND_WORKTREE: '/candidate/backend',
    TBOT_FIRMWARE_WORKTREE: '/candidate/firmware',
    TBOT_LESSON_STUDIO_BACKEND_MOUNT_ROOT: '/candidate/backend',
    TBOT_LESSON_STUDIO_FIRMWARE_MOUNT_ROOT: '/candidate/firmware',
    TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID: `sha256:${'1'.repeat(64)}`,
    TBOT_LESSON_STUDIO_WEB_IMAGE_ID: `sha256:${'2'.repeat(64)}`,
    LESSON_STUDIO_E2E_BACKEND_HOST_PORT: '13133',
    LESSON_STUDIO_E2E_WEB_HOST_PORT: '18133',
  };
  const containers = Object.fromEntries(
    ['redis', 'postgres', 'mysql', 'backend', 'seed-postgres', 'web', 'seed-mysql']
      .map((service) => [service, `${service}-container`]),
  );
  const run = (_command, args) => {
    if (args.some((arg) => arg.includes('.NetworkSettings.Ports'))) return JSON.stringify({
      '3000/tcp': [{ HostIp: '0.0.0.0', HostPort: '13133' }],
    });
    if (args.some((arg) => arg.includes('.Mounts'))) return JSON.stringify([
      { Type: 'bind', Source: '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/asset-manifest.json', Destination: '/usr/share/nginx/html/tvideo-demo/asset-manifest.json', RW: false },
      { Type: 'bind', Source: '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/admin', Destination: '/usr/share/nginx/html/tvideo-demo/admin', RW: false },
      { Type: 'bind', Source: '/candidate/backend/src/lessons/fixtures/tvideo-raw-code/assets/esp-tft', Destination: '/usr/share/nginx/html/tvideo-demo/esp-tft', RW: false },
      ...['background', 'objects', 'reference', 'robot'].map(directory => ({ Type: 'bind', Source: `/candidate/firmware/lesson/assets/${directory}`, Destination: `/usr/share/nginx/html/tvideo-demo/assets/${directory}`, RW: false })),
    ]);
    const service = args.at(-1);
    if (args.includes('ps')) return containers[service];
    const container = args.at(-1);
    if (container === containers.backend) return `healthy ${env.TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID}`;
    if (container === containers.web) return `healthy ${env.TBOT_LESSON_STUDIO_WEB_IMAGE_ID}`;
    return 'healthy sha256:fixture';
  };

  assert.throws(
    () => preflightLessonStudioE2EStack({ env, run }),
    /started backend container host port binding mismatch/,
  );
});
