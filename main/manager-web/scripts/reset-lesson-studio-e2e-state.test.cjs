const assert = require('node:assert/strict');
const test = require('node:test');

const { buildResetCommands } = require('./reset-lesson-studio-e2e-state.cjs');

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
