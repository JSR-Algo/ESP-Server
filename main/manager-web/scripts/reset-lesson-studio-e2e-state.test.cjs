const assert = require('node:assert/strict');
const test = require('node:test');

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
      { Type: 'bind', Source: '/candidate/firmware/lesson/assets', Destination: '/usr/share/nginx/html/tvideo-demo/assets', RW: false },
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
      { Type: 'bind', Source: '/candidate/firmware/lesson/assets', Destination: '/usr/share/nginx/html/tvideo-demo/assets', RW: false },
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
