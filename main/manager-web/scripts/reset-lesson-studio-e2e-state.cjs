const { spawnSync } = require('node:child_process');
const path = require('node:path');
const { lessonStudioWebOrigin } = require('./lesson-studio-e2e-environment.cjs');

const DEFAULT_COMPOSE_FILE = path.resolve(
  __dirname,
  '../../../docs/docker/docker-compose.lesson-studio-e2e.yml',
);

function composeExecutableFromEnvironment(env = process.env, { requireExplicit = false } = {}) {
  const executable = env.TBOT_DOCKER_COMPOSE_EXECUTABLE;
  if (executable) {
    if (!path.isAbsolute(executable)) {
      throw new Error('TBOT_DOCKER_COMPOSE_EXECUTABLE must be an absolute executable path');
    }
    return executable;
  }
  if (requireExplicit || env.CI === '1' || env.CI === 'true') {
    throw new Error('TBOT_DOCKER_COMPOSE_EXECUTABLE is required in CI');
  }
  return 'docker-compose';
}

function buildResetCommands({
  composeFile = DEFAULT_COMPOSE_FILE,
  composeExecutable = composeExecutableFromEnvironment(),
  projectName = 'tbot-ls-e2e',
} = {}) {
  const compose = [composeExecutable, '-p', projectName, '-f', composeFile];

  return [
    [
      ...compose,
      'exec', '-T', 'redis', 'redis-cli', 'DEL',
      'rate_limit:ip:127.0.0.1:/user/captcha',
      'rate_limit:ip:127.0.0.1:/user/login',
    ],
    [
      ...compose,
      'exec', '-T', 'redis', 'redis-cli', 'EVAL',
      "local keys=redis.call('keys','rl:*'); if #keys > 0 then return redis.call('del',unpack(keys)) end return 0",
      '0',
    ],
    [
      ...compose,
      'exec', '-T', 'postgres', 'psql', '-v', 'ON_ERROR_STOP=1',
      '-U', 'tbot', '-d', 'tbot', '-c',
      "DELETE FROM admin_login_attempts WHERE email IN ('lesson-author-e2e@local.invalid','lesson-author-b-e2e@local.invalid','lesson-manager-e2e@local.invalid');",
    ],
  ];
}

function resetOptionsFromEnvironment(env = process.env) {
  // Match Compose precedence: its standard project name wins over the
  // suite-specific fallback, then buildResetCommands supplies the default.
  const projectName = env.COMPOSE_PROJECT_NAME
    || env.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME;
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

function preflightLessonStudioE2EStack({ env = process.env, run = (command, args) => {
  const result = spawnSync(command, args, { encoding: 'utf8', env });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(result.stderr || `command failed: ${command}`);
  return result.stdout.trim();
} } = {}) {
  const compose = composeExecutableFromEnvironment(env, { requireExplicit: env.CI === '1' || env.CI === 'true' });
  const project = (env.COMPOSE_PROJECT_NAME || env.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME || 'tbot-ls-e2e');
  const file = DEFAULT_COMPOSE_FILE;
  const services = ['redis', 'postgres', 'mysql', 'backend', 'seed-postgres', 'web', 'seed-mysql'];
  for (const service of services) {
    const container = run(compose, ['-p', project, '-f', file, 'ps', '-q', service]);
    if (!container) throw new Error(`required ${service} service is not running`);
    const inspect = run(env.TBOT_DOCKER_EXECUTABLE || 'docker', [
      'inspect', '--format={{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}} {{.Image}}', container,
    ]);
    const [health, imageId] = inspect.split(/\s+/, 2);
    if (health !== 'healthy') throw new Error(`required ${service} service is not healthy`);
    if (service === 'backend' || service === 'web') {
      const expected = service === 'backend' ? env.TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID : env.TBOT_LESSON_STUDIO_WEB_IMAGE_ID;
      if (!expected || imageId !== expected) throw new Error(`started ${service} container image ID mismatch`);
    }
  }
}

if (require.main === module) {
  resetLessonStudioE2EState();
}

module.exports = {
  buildResetCommands,
  composeExecutableFromEnvironment,
  composeEnvironment,
  resetLessonStudioE2EState,
  preflightLessonStudioE2EStack,
  resetOptionsFromEnvironment,
};
