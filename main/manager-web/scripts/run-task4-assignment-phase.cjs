'use strict';

const { execFileSync } = require('node:child_process');
const { existsSync, mkdirSync, realpathSync } = require('node:fs');
const { isAbsolute, resolve } = require('node:path');
const {
  inspectAndPinCandidateImages,
  verifyStartedServiceImages,
  verifyStartedServicePortBindings,
} = require('./task4-image-identity.cjs');
const { validateAssignmentRuntimeCapsule } = require('./task4-assignment-runtime.cjs');
const { composeExecutableFromEnvironment } = require('./reset-lesson-studio-e2e-state.cjs');

const phase = process.argv[2];
if (!['new', 'rollback'].includes(phase)) throw new Error('usage: run-task4-assignment-phase.cjs new|rollback');

const required = [
  'TBOT_BACKEND_WORKTREE', 'TBOT_LESSON_STUDIO_BACKEND_IMAGE',
  'TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID', 'TBOT_LESSON_STUDIO_WEB_IMAGE',
  'TBOT_LESSON_STUDIO_WEB_IMAGE_ID', 'LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME',
  'LESSON_STUDIO_E2E_RESOURCE_PREFIX', 'TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT',
  'TASK4_ASSIGNMENT_RUNTIME_ROOT',
  'JWT_PUBLIC_KEY', 'TBOT_DEVICE_MINT_SECRET', 'LESSON_ASSET_ORIGIN_BASE',
  'ROBOT_ESP_BASE_URL', 'TBOT_FIRMWARE_WORKTREE',
];
for (const name of required) {
  if (!process.env[name]) throw new Error(`${name} is required for candidate-bound Task4 orchestration`);
}
const project = process.env.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME;
const prefix = process.env.LESSON_STUDIO_E2E_RESOURCE_PREFIX;
if (!/^tbot-task4-[a-z0-9-]+$/.test(project) || prefix !== project) {
  throw new Error('Task4 compose project/resource prefix must match an explicit tbot-task4-* namespace');
}

const repoRoot = resolve(__dirname, '../../..');
const backendRoot = realpathSync(process.env.TBOT_BACKEND_WORKTREE);
const firmwareRoot = realpathSync(process.env.TBOT_FIRMWARE_WORKTREE);
const { runtimeRoot } = validateAssignmentRuntimeCapsule(process.env, [
  repoRoot, backendRoot, firmwareRoot,
]);
if (!existsSync(resolve(backendRoot, 'dist/lessons/course-mode/curriculum-course-mode.js'))) {
  throw new Error('TBOT_BACKEND_WORKTREE must be the built candidate backend worktree');
}
for (const asset of [
  'lesson/assets/background/barn-round-field-poster.jpg',
  'lesson/assets/robot/poses/bright-teach.png',
  'lesson/assets/robot/poses/bright-listening.png',
  'lesson/assets/robot/poses/bright-celebrate.png',
]) {
  if (!existsSync(resolve(firmwareRoot, asset))) {
    throw new Error(`TBOT_FIRMWARE_WORKTREE lacks required candidate asset: ${asset}`);
  }
}
const mediaRoot = resolve(runtimeRoot, 'media');
const tlsRoot = resolve(runtimeRoot, 'tls');
mkdirSync(mediaRoot, { recursive: true });
mkdirSync(tlsRoot, { recursive: true });

const hostPort = process.env.TASK4_ASSIGNMENT_MEDIA_HOST_PORT || '18443';
const mediaHostname = 'task4-media.localhost';
const environment = {
  ...process.env,
  TBOT_BACKEND_WORKTREE: backendRoot,
  LESSON_STUDIO_E2E_BACKEND_HOST_PORT: process.env.LESSON_STUDIO_E2E_BACKEND_HOST_PORT || '3100',
  LESSON_STUDIO_E2E_WEB_HOST_PORT: process.env.LESSON_STUDIO_E2E_WEB_HOST_PORT || '8102',
  TASK4_ASSIGNMENT_MEDIA_ROOT: mediaRoot,
  TASK4_ASSIGNMENT_TLS_ROOT: tlsRoot,
  TASK4_ASSIGNMENT_MEDIA_HOST_PORT: hostPort,
  TASK4_ASSIGNMENT_MEDIA_ORIGIN: `https://${mediaHostname}:${hostPort}`,
  TASK4_ASSIGNMENT_PHASE: phase,
};
const dockerExecutable = environment.TBOT_DOCKER_EXECUTABLE;
if (!dockerExecutable || !isAbsolute(dockerExecutable)) {
  throw new Error('TBOT_DOCKER_EXECUTABLE must be an absolute candidate-bound executable');
}
const composeExecutable = composeExecutableFromEnvironment(environment, { requireExplicit: true });
const compose = [
  '-p', environment.LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME,
  '-f', resolve(repoRoot, 'docs/docker/docker-compose.lesson-studio-e2e.yml'),
  '-f', resolve(repoRoot, `docs/docker/task4-admin-assignment/docker-compose.${phase}.yml`),
];
const run = (command, args, options = {}) => execFileSync(command, args, {
  cwd: repoRoot, env: environment, stdio: 'inherit', ...options,
});
const composeRun = (...args) => run(composeExecutable, [...compose, ...args]);

const tlsKey = resolve(tlsRoot, 'key.pem');
const tlsCert = resolve(tlsRoot, 'cert.pem');
const tlsCertificateIsUsable = () => {
  if (!existsSync(tlsKey) || !existsSync(tlsCert)) return false;
  try {
    execFileSync('openssl', ['x509', '-checkend', '3600', '-noout', '-in', tlsCert], {
      cwd: repoRoot, env: environment, stdio: 'ignore',
    });
    return execFileSync('openssl', ['x509', '-text', '-noout', '-in', tlsCert], {
      cwd: repoRoot, env: environment, encoding: 'utf8',
    }).includes('CA:TRUE');
  } catch {
    return false;
  }
};
if (!tlsCertificateIsUsable()) {
  run('openssl', [
    'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
    `-subj`, `/CN=${mediaHostname}`,
    '-addext', `subjectAltName=DNS:${mediaHostname}`,
    '-addext', 'basicConstraints=critical,CA:TRUE',
    '-addext', 'keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign',
    '-keyout', tlsKey, '-out', tlsCert,
  ]);
}
run(process.execPath, [resolve(__dirname, 'prepare-task4-media-templates.cjs')], {
  env: { ...environment, TASK4_ASSIGNMENT_MEDIA_ROOT: mediaRoot },
});
const pinnedImages = inspectAndPinCandidateImages({
  backendReference: environment.TBOT_LESSON_STUDIO_BACKEND_IMAGE,
  backendId: environment.TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID,
  webReference: environment.TBOT_LESSON_STUDIO_WEB_IMAGE,
  webId: environment.TBOT_LESSON_STUDIO_WEB_IMAGE_ID,
}, (reference) => execFileSync(dockerExecutable, ['image', 'inspect', '--format={{.Id}}', reference], {
    cwd: repoRoot, env: environment, encoding: 'utf8',
  }).trim());
environment.TBOT_LESSON_STUDIO_BACKEND_IMAGE = pinnedImages.backendImage;
environment.TBOT_LESSON_STUDIO_WEB_IMAGE = pinnedImages.webImage;

if (phase === 'new') {
  // NEW owns a fresh isolated stack. ROLLBACK intentionally preserves this PostgreSQL volume.
  composeRun('down', '--volumes', '--remove-orphans');
  composeRun('up', '-d', '--wait');
  composeRun('exec', '-T', 'backend', '/nodejs/bin/node', '/task4-fixture/bootstrap.cjs', 'seed-v8');
  composeRun('exec', '-T', 'backend', '/nodejs/bin/node', '/task4-fixture/bootstrap.cjs', 'rollout-v9');
  composeRun('exec', '-T', 'backend', '/nodejs/bin/node', '/task4-fixture/bootstrap.cjs', 'cancel-v9-assignment');
} else {
  composeRun('up', '-d', '--no-deps', '--force-recreate', 'backend');
  composeRun('up', '-d', '--no-deps', 'derivative-media');
  composeRun('exec', '-T', 'backend', '/nodejs/bin/node', '/task4-fixture/bootstrap.cjs', 'verify-new');
}

verifyStartedServiceImages({
  backend: pinnedImages.backendImage,
  web: pinnedImages.webImage,
  'derivative-media': pinnedImages.backendImage,
}, (service) => {
  const container = execFileSync(composeExecutable, [...compose, 'ps', '-q', service], {
    cwd: repoRoot, env: environment, encoding: 'utf8',
  }).trim();
  if (!container) return '';
  return execFileSync(dockerExecutable, ['inspect', '--format={{.Image}}', container], {
    cwd: repoRoot, env: environment, encoding: 'utf8',
  }).trim();
});
verifyStartedServicePortBindings({
  backend: { containerPort: '3000/tcp', hostPort: environment.LESSON_STUDIO_E2E_BACKEND_HOST_PORT },
  web: { containerPort: '8002/tcp', hostPort: environment.LESSON_STUDIO_E2E_WEB_HOST_PORT },
  'derivative-media': { containerPort: '8443/tcp', hostPort },
}, (service) => {
  const container = execFileSync(composeExecutable, [...compose, 'ps', '-q', service], {
    cwd: repoRoot, env: environment, encoding: 'utf8',
  }).trim();
  if (!container) return null;
  return JSON.parse(execFileSync(dockerExecutable, [
    'inspect', '--format={{json .NetworkSettings.Ports}}', container,
  ], { cwd: repoRoot, env: environment, encoding: 'utf8' }));
});

run(process.execPath, [
  resolve(repoRoot, 'main/manager-web/node_modules/@playwright/test/cli.js'),
  'test', '--config=playwright.assignment-rollback.config.js',
], { cwd: resolve(repoRoot, 'main/manager-web') });
composeRun('exec', '-T', 'backend', '/nodejs/bin/node', '/task4-fixture/bootstrap.cjs', phase === 'new' ? 'verify-new' : 'verify-rollback');
