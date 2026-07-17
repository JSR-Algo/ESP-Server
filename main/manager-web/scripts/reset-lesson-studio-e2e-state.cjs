const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { lessonStudioWebOrigin } = require('./lesson-studio-e2e-environment.cjs');

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
  // Keep reset targeting aligned with Compose: the suite-specific namespace
  // overrides the standard Compose project, then buildResetCommands defaults.
  const projectName = env.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME
    || env.COMPOSE_PROJECT_NAME;
  return projectName ? { projectName } : {};
}

function composeEnvironment(env = process.env) {
  const webOrigin = lessonStudioWebOrigin(env);
  return {
    ...env,
    JWT_PUBLIC_KEY: env.JWT_PUBLIC_KEY || 'not-used-by-e2e-reset',
    TBOT_DEVICE_MINT_SECRET: env.TBOT_DEVICE_MINT_SECRET || 'not-used-by-e2e-reset',
    LESSON_ASSET_ORIGIN_BASE: env.LESSON_ASSET_ORIGIN_BASE || `${webOrigin}/tvideo-demo`,
    ROBOT_ESP_BASE_URL: env.ROBOT_ESP_BASE_URL || 'not-used-by-e2e-reset',
  };
}

function resetLessonStudioE2EState(options = resetOptionsFromEnvironment()) {
  for (const [command, ...args] of buildResetCommands(options)) {
    const result = spawnSync(command, args, {
      encoding: 'utf8',
      stdio: 'inherit',
      env: composeEnvironment(),
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

module.exports = {
  buildResetCommands,
  composeEnvironment,
  resetLessonStudioE2EState,
  resetOptionsFromEnvironment,
};
