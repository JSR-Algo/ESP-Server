const { spawnSync } = require('node:child_process');
const path = require('node:path');

const DEFAULT_COMPOSE_FILE = path.resolve(
  __dirname,
  '../../../docs/docker/docker-compose.lesson-studio-e2e.yml',
);

function buildResetCommands({ composeFile = DEFAULT_COMPOSE_FILE, projectName = 'tbot-ls-e2e' } = {}) {
  const compose = ['docker', 'compose', '-p', projectName, '-f', composeFile];

  return [
    [
      ...compose,
      'exec', '-T', 'redis', 'redis-cli', 'DEL',
      'rate_limit:ip:127.0.0.1:/user/captcha',
      'rate_limit:ip:127.0.0.1:/user/login',
    ],
    [
      ...compose,
      'exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1',
      '-U', 'tbot', '-d', 'tbot', '-c',
      "DELETE FROM admin_login_attempts WHERE email='lesson-author-e2e@local.invalid';",
    ],
  ];
}

function resetOptionsFromEnvironment(env = process.env) {
  return env.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME
    ? { projectName: env.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME }
    : {};
}

function resetLessonStudioE2EState(options = resetOptionsFromEnvironment()) {
  for (const [command, ...args] of buildResetCommands(options)) {
    const result = spawnSync(command, args, {
      encoding: 'utf8',
      stdio: 'inherit',
      env: {
        ...process.env,
        JWT_PUBLIC_KEY: process.env.JWT_PUBLIC_KEY || 'not-used-by-e2e-reset',
      },
    });

    if (result.error) throw result.error;
    if (result.status !== 0) {
      throw new Error(`E2E state reset failed with exit code ${result.status}: ${command} ${args.join(' ')}`);
    }
  }
}

if (require.main === module) {
  resetLessonStudioE2EState();
}

module.exports = { buildResetCommands, resetLessonStudioE2EState, resetOptionsFromEnvironment };
