const assert = require('node:assert/strict');
const test = require('node:test');

const {
  buildResetCommands,
  composeEnvironment,
  resetOptionsFromEnvironment,
} = require('./reset-lesson-studio-e2e-state.cjs');

test('resets auth throttling through compose service names', () => {
  const commands = buildResetCommands({
    composeFile: '/repo/docs/docker/docker-compose.lesson-studio-e2e.yml',
    projectName: 'tbot-ls-e2e',
  });

  assert.deepEqual(commands, [
    [
      'docker', 'compose', '-p', 'tbot-ls-e2e', '-f',
      '/repo/docs/docker/docker-compose.lesson-studio-e2e.yml',
      'exec', '-T', 'redis', 'redis-cli', 'DEL',
      'rate_limit:ip:127.0.0.1:/user/captcha',
      'rate_limit:ip:127.0.0.1:/user/login',
    ],
    [
      'docker', 'compose', '-p', 'tbot-ls-e2e', '-f',
      '/repo/docs/docker/docker-compose.lesson-studio-e2e.yml',
      'exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1',
      '-U', 'tbot', '-d', 'tbot', '-c',
      "DELETE FROM admin_login_attempts WHERE email='lesson-author-e2e@local.invalid';",
    ],
  ]);
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
