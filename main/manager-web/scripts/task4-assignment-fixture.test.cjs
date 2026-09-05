const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { existsSync, readFileSync } = require('node:fs');
const {
  chmod, link, mkdir, mkdtemp, readFile, realpath, rm, stat, symlink, writeFile,
} = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { resolve } = require('node:path');
const test = require('node:test');
const {
  inspectAndPinCandidateImages,
  verifyStartedServiceImages,
  verifyStartedServicePortBindings,
} = require('./task4-image-identity.cjs');
const { validateAssignmentRuntimeCapsule } = require('./task4-assignment-runtime.cjs');

const fixturePath = resolve(__dirname, '../../../docs/docker/task4-admin-assignment/bootstrap.cjs');
const copyHelperPath = resolve(__dirname, '../../../docs/docker/task4-admin-assignment/copy-file.cjs');
const rollbackSpecPath = resolve(__dirname, '../e2e/lesson-studio/assignment-rollback-phase.spec.js');
const playwrightConfigPath = resolve(__dirname, '../playwright.assignment-rollback.config.js');
const orchestratorPath = resolve(__dirname, 'run-task4-assignment-phase.cjs');
const imageIdentityPath = resolve(__dirname, 'task4-image-identity.cjs');
const mediaPrepPath = resolve(__dirname, 'prepare-task4-media-templates.cjs');

async function createAssignmentRuntimeCapsule(t, prefix = 'course-mode-assignment-runtime-') {
  const capsuleRoot = await mkdtemp(resolve(tmpdir(), prefix));
  const runtimeRoot = resolve(capsuleRoot, 'runtime');
  await chmod(capsuleRoot, 0o700);
  await mkdir(runtimeRoot, { mode: 0o700 });
  await chmod(runtimeRoot, 0o700);
  t.after(() => rm(capsuleRoot, { recursive: true, force: true }));
  return { capsuleRoot, runtimeRoot };
}

function capsuleEnvironment(capsuleRoot, runtimeRoot) {
  return {
    TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT: capsuleRoot,
    TASK4_ASSIGNMENT_RUNTIME_ROOT: runtimeRoot,
  };
}

async function createMediaPrepFixture(t) {
  const createdCapsule = await createAssignmentRuntimeCapsule(t);
  const capsule = {
    capsuleRoot: await realpath(createdCapsule.capsuleRoot),
    runtimeRoot: await realpath(createdCapsule.runtimeRoot),
  };
  const protectedRoot = await mkdtemp(resolve(tmpdir(), 'course-mode-media-protected-'));
  const backendRoot = resolve(protectedRoot, 'backend');
  const firmwareRoot = resolve(protectedRoot, 'firmware');
  const toolRoot = resolve(protectedRoot, 'tools');
  const toolMarker = resolve(protectedRoot, 'media-tool.marker');
  await mkdir(backendRoot);
  await mkdir(firmwareRoot);
  await mkdir(toolRoot);
  const ffmpeg = resolve(toolRoot, 'ffmpeg');
  const ffprobe = resolve(toolRoot, 'ffprobe');
  await writeFile(ffmpeg, [
    `#!${process.execPath}`,
    "const { appendFileSync, writeFileSync } = require('node:fs');",
    `appendFileSync(${JSON.stringify(toolMarker)}, 'ffmpeg\\n');`,
    "writeFileSync(process.argv.at(-1), 'stub media');",
  ].join('\n'));
  await writeFile(ffprobe, [
    `#!${process.execPath}`,
    "const { appendFileSync } = require('node:fs');",
    "const { basename } = require('node:path');",
    `appendFileSync(${JSON.stringify(toolMarker)}, 'ffprobe\\n');`,
    "const durationMs = Number(basename(process.argv.at(-1), '.mp4'));",
    "process.stdout.write(JSON.stringify({ streams: [{ codec_name: 'h264', width: 480, height: 320, r_frame_rate: '10/1', nb_frames: durationMs / 100 }], format: { duration: durationMs / 1000 } }));",
  ].join('\n'));
  await chmod(ffmpeg, 0o755);
  await chmod(ffprobe, 0o755);
  t.after(() => rm(protectedRoot, { recursive: true, force: true }));
  return { capsule, backendRoot, firmwareRoot, toolRoot, toolMarker };
}

function runMediaPrep(fixture, overrides = {}) {
  const mediaRoot = resolve(fixture.capsule.runtimeRoot, 'media');
  return execFileSync(process.execPath, [mediaPrepPath], {
    env: {
      ...process.env,
      ...capsuleEnvironment(fixture.capsule.capsuleRoot, fixture.capsule.runtimeRoot),
      TASK4_ASSIGNMENT_MEDIA_ROOT: mediaRoot,
      TBOT_BACKEND_WORKTREE: fixture.backendRoot,
      TBOT_FIRMWARE_WORKTREE: fixture.firmwareRoot,
      PATH: `${fixture.toolRoot}:${process.env.PATH || ''}`,
      ...overrides,
    },
    stdio: 'pipe',
  });
}

test('assignment media prep accepts a valid gate-owned system-temp capsule', async (t) => {
  const fixture = await createMediaPrepFixture(t);

  runMediaPrep(fixture);

  const templateRoot = resolve(fixture.capsule.runtimeRoot, 'media/templates');
  for (const durationMs of [600, 1100, 1200, 1300, 1400, 1600, 2600, 3000, 9500]) {
    assert.equal((await stat(resolve(templateRoot, `${durationMs}.mp4`))).isFile(), true);
  }
  assert.equal((await readFile(fixture.toolMarker, 'utf8')).trim().split('\n').length, 18);
});

test('assignment media prep rejects a mismatched media root before side effects', async (t) => {
  const fixture = await createMediaPrepFixture(t);
  const mismatchedRoot = resolve(fixture.capsule.runtimeRoot, 'other-media');

  assert.throws(
    () => runMediaPrep(fixture, { TASK4_ASSIGNMENT_MEDIA_ROOT: mismatchedRoot }),
    /TASK4_ASSIGNMENT_MEDIA_ROOT/,
  );
  assert.equal(existsSync(resolve(mismatchedRoot, 'templates')), false);
  assert.equal(existsSync(fixture.toolMarker), false);
});

test('assignment media prep rejects an invalid capsule before side effects', async (t) => {
  const fixture = await createMediaPrepFixture(t);
  await chmod(fixture.capsule.capsuleRoot, 0o755);
  const templateRoot = resolve(fixture.capsule.runtimeRoot, 'media/templates');

  assert.throws(() => runMediaPrep(fixture), /0700/);
  assert.equal(existsSync(templateRoot), false);
  assert.equal(existsSync(fixture.toolMarker), false);
});

test('assignment media prep rejects a missing capsule owner before side effects', async (t) => {
  const fixture = await createMediaPrepFixture(t);
  const templateRoot = resolve(fixture.capsule.runtimeRoot, 'media/templates');

  assert.throws(
    () => runMediaPrep(fixture, { TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT: '' }),
    /TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT/,
  );
  assert.equal(existsSync(templateRoot), false);
  assert.equal(existsSync(fixture.toolMarker), false);
});

test('assignment media prep validates the capsule before filesystem or media side effects', () => {
  const source = readFileSync(mediaPrepPath, 'utf8');

  for (const required of [
    'TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT', 'TASK4_ASSIGNMENT_RUNTIME_ROOT',
    'TASK4_ASSIGNMENT_MEDIA_ROOT', 'TBOT_BACKEND_WORKTREE', 'TBOT_FIRMWARE_WORKTREE',
  ]) assert.match(source, new RegExp(required));
  assert.match(source, /validateAssignmentRuntimeCapsule/);
  assert.doesNotMatch(source, /manager-web\/output/);
  const validationIndex = source.indexOf('validateAssignmentRuntimeCapsule(process.env, [');
  assert.ok(validationIndex >= 0);
  assert.ok(validationIndex < source.indexOf('mkdirSync('));
  assert.ok(validationIndex < source.indexOf("execFileSync('ffmpeg'"));
  assert.ok(validationIndex < source.indexOf("execFileSync('ffprobe'"));
});

test('assignment runtime capsule accepts an exact private owner and direct runtime child', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  const expected = {
    capsuleRoot: await realpath(capsule.capsuleRoot),
    runtimeRoot: await realpath(capsule.runtimeRoot),
  };

  const result = validateAssignmentRuntimeCapsule(
    capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot),
    [],
  );

  assert.deepEqual(result, expected);
  assert.equal(Object.isFrozen(result), true);
});

test('assignment runtime capsule defaults ownership checks to the effective UID', { concurrency: false }, async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  const actualUid = (await stat(capsule.capsuleRoot)).uid;
  const originalGetuid = process.getuid;
  const originalGeteuid = process.geteuid;

  try {
    process.getuid = () => actualUid + 1;
    process.geteuid = () => actualUid;
    assert.doesNotThrow(() => validateAssignmentRuntimeCapsule(
      capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot),
      [],
    ));
  } finally {
    process.getuid = originalGetuid;
    process.geteuid = originalGeteuid;
  }
});

test('assignment runtime capsule rejects a real-UID owner with a different effective UID', { concurrency: false }, async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  const actualUid = (await stat(capsule.capsuleRoot)).uid;
  const originalGetuid = process.getuid;
  const originalGeteuid = process.geteuid;

  try {
    process.getuid = () => actualUid;
    process.geteuid = () => actualUid + 1;
    assert.throws(
      () => validateAssignmentRuntimeCapsule(
        capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot),
        [],
      ),
      /owner UID.*runtime UID/,
    );
  } finally {
    process.getuid = originalGetuid;
    process.geteuid = originalGeteuid;
  }
});

test('assignment runtime capsule rejects a missing owner variable', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  assert.throws(
    () => validateAssignmentRuntimeCapsule({ TASK4_ASSIGNMENT_RUNTIME_ROOT: capsule.runtimeRoot }, []),
    /TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT/,
  );
});

test('assignment runtime capsule rejects the obsolete owner variable name', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  assert.throws(
    () => validateAssignmentRuntimeCapsule({
      TASK4_ASSIGNMENT_CAPSULE_ROOT: capsule.capsuleRoot,
      TASK4_ASSIGNMENT_RUNTIME_ROOT: capsule.runtimeRoot,
    }, []),
    /TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT/,
  );
});

test('assignment runtime capsule rejects a runtime that is not the exact direct child', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  const nestedRuntime = resolve(capsule.runtimeRoot, 'nested');
  await mkdir(nestedRuntime, { mode: 0o700 });

  assert.throws(
    () => validateAssignmentRuntimeCapsule(capsuleEnvironment(capsule.capsuleRoot, nestedRuntime), []),
    /runtime/,
  );
});

test('assignment runtime capsule rejects an owner symlink', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  const linkRoot = `${capsule.capsuleRoot}-link`;
  await symlink(capsule.capsuleRoot, linkRoot, 'dir');
  t.after(() => rm(linkRoot, { force: true }));

  assert.throws(
    () => validateAssignmentRuntimeCapsule(capsuleEnvironment(linkRoot, resolve(linkRoot, 'runtime')), []),
    /symlink/,
  );
});

test('assignment runtime capsule rejects a runtime symlink', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  const realRuntime = resolve(capsule.capsuleRoot, 'real-runtime');
  await mkdir(realRuntime, { mode: 0o700 });
  await rm(capsule.runtimeRoot, { recursive: true });
  await symlink(realRuntime, capsule.runtimeRoot, 'dir');

  assert.throws(
    () => validateAssignmentRuntimeCapsule(capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot), []),
    /symlink/,
  );
});

for (const [target, mode] of [['owner', 0o755], ['runtime', 0o755]]) {
  test(`assignment runtime capsule rejects ${target} mode 0755`, async (t) => {
    const capsule = await createAssignmentRuntimeCapsule(t);
    await chmod(target === 'owner' ? capsule.capsuleRoot : capsule.runtimeRoot, mode);
    assert.throws(
      () => validateAssignmentRuntimeCapsule(capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot), []),
      /0700/,
    );
  });
}

for (const target of ['owner', 'runtime']) {
  test(`assignment runtime capsule rejects sticky-bit ${target} mode 01700`, async (t) => {
    const capsule = await createAssignmentRuntimeCapsule(t);
    const targetPath = target === 'owner' ? capsule.capsuleRoot : capsule.runtimeRoot;
    await chmod(targetPath, 0o1700);
    const observedMode = (await stat(targetPath)).mode & 0o7777;
    assert.equal(observedMode, 0o1700, 'filesystem must preserve the sticky mode bit for this test');

    assert.throws(
      () => validateAssignmentRuntimeCapsule(capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot), []),
      /0700/,
    );
  });
}

test('assignment runtime capsule rejects an owner prefix mismatch', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t, 'task4-assignment-runtime-');
  assert.throws(
    () => validateAssignmentRuntimeCapsule(capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot), []),
    /course-mode-assignment-runtime-/,
  );
});

for (const target of ['owner', 'runtime']) {
  test(`assignment runtime capsule rejects ${target} UID mismatch`, async (t) => {
    const capsule = await createAssignmentRuntimeCapsule(t);
    const actualUid = (await stat(capsule.capsuleRoot)).uid;
    assert.throws(
      () => validateAssignmentRuntimeCapsule(
        capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot),
        [],
        actualUid + 1,
      ),
      new RegExp(`${target} UID`),
    );
  });
}

for (const protectedName of ['admin', 'backend', 'firmware']) {
  test(`assignment runtime capsule rejects overlap with the protected ${protectedName} root`, async (t) => {
    const capsule = await createAssignmentRuntimeCapsule(t);
    const protectedRoot = protectedName === 'admin'
      ? capsule.capsuleRoot
      : protectedName === 'backend'
        ? capsule.runtimeRoot
        : resolve(capsule.runtimeRoot, 'firmware');
    if (protectedName === 'firmware') await mkdir(protectedRoot, { mode: 0o700 });

    assert.throws(
      () => validateAssignmentRuntimeCapsule(
        capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot),
        [protectedRoot],
      ),
      /protected root/,
    );
  });
}

test('assignment runtime capsule rejects the filesystem root as a protected ancestor', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  assert.throws(
    () => validateAssignmentRuntimeCapsule(
      capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot),
      ['/'],
    ),
    /protected root/,
  );
});

test('assignment runtime capsule accepts a similarly prefixed protected sibling', async (t) => {
  const capsule = await createAssignmentRuntimeCapsule(t);
  const protectedSibling = `${capsule.capsuleRoot}-protected`;
  await mkdir(protectedSibling, { mode: 0o700 });
  t.after(() => rm(protectedSibling, { recursive: true, force: true }));

  assert.doesNotThrow(() => validateAssignmentRuntimeCapsule(
    capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot),
    [protectedSibling],
  ));
});

for (const [label, value] of [
  ['empty', ''],
  ['relative', 'relative/runtime'],
  ['NUL-containing', '/tmp/runtime\0suffix'],
]) {
  test(`assignment runtime capsule rejects ${label} paths`, async (t) => {
    const capsule = await createAssignmentRuntimeCapsule(t);
    for (const key of ['TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT', 'TASK4_ASSIGNMENT_RUNTIME_ROOT']) {
      const environment = capsuleEnvironment(capsule.capsuleRoot, capsule.runtimeRoot);
      environment[key] = value;
      assert.throws(() => validateAssignmentRuntimeCapsule(environment, []), /absolute|NUL|nonempty/);
    }
  });
}

test('Task 4 pins inspected image IDs and rejects retag or started-container drift', () => {
  const candidate = {
    backendReference: 'local/backend:candidate', backendId: `sha256:${'1'.repeat(64)}`,
    webReference: 'local/web:candidate', webId: `sha256:${'2'.repeat(64)}`,
  };
  const pinned = inspectAndPinCandidateImages(candidate, (reference) => ({
    'local/backend:candidate': candidate.backendId,
    'local/web:candidate': candidate.webId,
  })[reference]);
  assert.deepEqual(pinned, { backendImage: candidate.backendId, webImage: candidate.webId });
  assert.throws(
    () => inspectAndPinCandidateImages(candidate, () => `sha256:${'3'.repeat(64)}`),
    /candidate backend image ID mismatch/,
  );
  assert.doesNotThrow(() => verifyStartedServiceImages({
    backend: candidate.backendId, web: candidate.webId, 'derivative-media': candidate.backendId,
  }, (service) => (service === 'web' ? candidate.webId : candidate.backendId)));
  assert.throws(() => verifyStartedServiceImages(
    { backend: candidate.backendId }, () => `sha256:${'4'.repeat(64)}`,
  ), /started backend container image ID mismatch/);
  assert.doesNotThrow(() => verifyStartedServicePortBindings({
    'derivative-media': { containerPort: '8443/tcp', hostPort: '28434' },
  }, () => ({ '8443/tcp': [{ HostIp: '127.0.0.1', HostPort: '28434' }] })));
  assert.throws(() => verifyStartedServicePortBindings({
    'derivative-media': { containerPort: '8443/tcp', hostPort: '28434' },
  }, () => ({ '8443/tcp': [{ HostIp: '0.0.0.0', HostPort: '28434' }] })),
  /started derivative-media container host port binding mismatch/);
});

test('Task 4 assignment fixture uses canonical backend authoring and rollout code', () => {
  assert.equal(existsSync(fixturePath), true, 'assignment bootstrap must exist');
  const source = readFileSync(fixturePath, 'utf8');

  assert.match(source, /LessonAuthoringService/);
  assert.match(source, /runFarmV9GeometryRollout/);
  assert.match(source, /mode: 'rollback'/);
  assert.match(source, /FARM_V7_BOOTSTRAP_ASSETS/);
  assert.match(source, /FARM_V7_BOOTSTRAP_JOURNEY/);
  assert.match(source, /manifestPreview/);
  assert.match(source, /const token = loginBody\.session_token;/);
  assert.doesNotMatch(source, /accessToken|loginBody\.data\?\.token/);
  assert.doesNotMatch(source, /repeat\(['"](?:8|9)['"],\s*64\)/);
  assert.doesNotMatch(source, /270ee576f1224503e79b0332c2a0f4606213902af750bd4d9945e3e55ff35530/);
  assert.doesNotMatch(source, /INSERT INTO lesson_asset_generations/);
  assert.match(source, /requestAndLease/);
  assert.match(source, /loadLatestPublishedPacks/);
  assert.match(source, /buildCanonicalGenerationIndex/);
  assert.match(source, /commitGeneration/);
  assert.match(source, /Test-only atomic repository lifecycle/);
  assert.match(source, /not the public\/global \/lesson-assets\/rebuild operation/);
  assert.doesNotMatch(source, /request\(['"]POST['"],\s*['"]\/lesson-assets\/rebuild/);
});

test('Task 4 assignment fixture declares exact graph and READY derivative invariants', () => {
  const source = readFileSync(fixturePath, 'utf8');
  const copyHelperSource = readFileSync(copyHelperPath, 'utf8');

  assert.match(source, /sharedVisualAssets:\s*7/);
  assert.match(source, /lessonSteps:\s*2/);
  assert.match(source, /bundleAssets:\s*8/);
  assert.match(source, /derivatives:\s*19/);
  assert.match(source, /status\s*=\s*'ready'/);
  assert.match(source, /application\/vnd\.tbot\.rgb565-indexed/);
  assert.match(source, /hook:\s*true/);
  assert.match(source, /recall:\s*true/);
  assert.match(source, /motion:\s*\{ present: 'teach' \}/);
  assert.match(source, /funPattern:\s*'copyMyMove'/);
  assert.match(source, /funPattern:\s*'soundGuess'/);
  assert.match(source, /terminal:\s*true/);
  assert.match(source, /timeoutSec:\s*index === 0 \? 8 : 12/);
  assert.match(source, /writeTbotRgb565File/);
  assert.match(source, /validateTbotRgb565File/);
  assert.match(source, /createHash\('sha256'\)/);
  assert.match(source, /const \{ replaceWithCopy \} = require\('\/task4-fixture\/copy-file\.cjs'\);/);
  assert.doesNotMatch(source, /\blink\b|replaceWithLink/);
  assert.equal((source.match(/await replaceWithCopy\(/g) || []).length, 2);
  assert.match(copyHelperSource, /const \{ copyFile, rm, stat \} = require\('node:fs\/promises'\);/);
  assert.doesNotMatch(copyHelperSource, /\blink\b|replaceWithLink/);
  assert.match(copyHelperSource, /async function replaceWithCopy\(source, destination[\s\S]*await stat\(source\);[\s\S]*await rm\(destination, \{ force: true \}\);[\s\S]*await copy\(source, destination\);[\s\S]*\}/);
  assert.doesNotMatch(source, /fixture\.local/);
  assert.doesNotMatch(source, /output_bytes=1/);
  assert.doesNotMatch(source, /preview:\$\{row\.derivative_id\}/);
});

test('replaceWithCopy creates independent bytes that cannot mutate the source', async (t) => {
  const root = await mkdtemp(resolve(tmpdir(), 'task4-copy-file-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = resolve(root, 'source.bin');
  const destination = resolve(root, 'destination.bin');
  const expected = Buffer.from('canonical derivative bytes');
  await writeFile(source, expected);

  const { replaceWithCopy } = require(copyHelperPath);
  await replaceWithCopy(source, destination);

  assert.deepEqual(await readFile(destination), expected);
  assert.notEqual((await stat(destination)).ino, (await stat(source)).ino);
  await writeFile(destination, 'mutated destination');
  assert.deepEqual(await readFile(source), expected);
  await rm(destination);
  assert.deepEqual(await readFile(source), expected);
});

test('replaceWithCopy replaces hard-linked and stale destinations on rerun', async (t) => {
  const root = await mkdtemp(resolve(tmpdir(), 'task4-copy-file-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = resolve(root, 'source.bin');
  const destination = resolve(root, 'destination.bin');
  await writeFile(source, 'current derivative');
  await link(source, destination);
  assert.equal((await stat(destination)).ino, (await stat(source)).ino);

  const { replaceWithCopy } = require(copyHelperPath);
  await replaceWithCopy(source, destination);

  assert.equal(await readFile(destination, 'utf8'), 'current derivative');
  assert.notEqual((await stat(destination)).ino, (await stat(source)).ino);
  await writeFile(destination, 'stale derivative');
  await replaceWithCopy(source, destination);
  assert.equal(await readFile(destination, 'utf8'), 'current derivative');
  assert.notEqual((await stat(destination)).ino, (await stat(source)).ino);
});

test('replaceWithCopy leaves copy failures recoverable by the next rerun', async (t) => {
  const root = await mkdtemp(resolve(tmpdir(), 'task4-copy-file-'));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = resolve(root, 'source.bin');
  const destination = resolve(root, 'destination.bin');
  await writeFile(source, 'recoverable source');
  await writeFile(destination, 'stale destination');

  const { replaceWithCopy } = require(copyHelperPath);
  await assert.rejects(
    replaceWithCopy(source, destination, async () => { throw new Error('injected copy failure'); }),
    /injected copy failure/,
  );
  assert.equal(existsSync(destination), false);
  assert.equal(await readFile(source, 'utf8'), 'recoverable source');

  await replaceWithCopy(source, destination);
  assert.equal(await readFile(destination, 'utf8'), 'recoverable source');
  assert.notEqual((await stat(destination)).ino, (await stat(source)).ino);
});

test('Task 4 assignment phases preserve PostgreSQL state while refreshing candidate-bound services', () => {
  for (const phase of ['new', 'rollback']) {
    const compose = readFileSync(
      resolve(__dirname, `../../../docs/docker/task4-admin-assignment/docker-compose.${phase}.yml`),
      'utf8',
    );
    assert.match(compose, /LESSON_ROLLOUT_DEVICE_ALLOWLIST:\s*91deb5af-c1c0-416b-956d-266d510eac5e/);
    assert.match(compose, /derivative-media:/);
    assert.match(compose, /TASK4_ASSIGNMENT_MEDIA_ROOT/);
    assert.match(
      compose,
      /"127\.0\.0\.1:\$\{TASK4_ASSIGNMENT_MEDIA_HOST_PORT:-18443\}:8443"/,
    );
    assert.match(compose, /aliases:\s*\[task4-media\.localhost\]/);
    assert.match(compose, /\.\/task4-admin-assignment\/copy-file\.cjs:\/task4-fixture\/copy-file\.cjs:ro/);
  }
});

test('Task 4 assignment browser phase uses WebKit and verifies row-scoped Monitoring state', () => {
  const config = readFileSync(playwrightConfigPath, 'utf8');
  const spec = readFileSync(rollbackSpecPath, 'utf8');

  assert.match(config, /course-mode-webkit-desktop/);
  assert.doesNotMatch(config, /course-mode-chromium-desktop/);
  assert.doesNotMatch(config, /ignoreHTTPSErrors/);
  assert.match(spec, /gotoAppRoute\(page, `#\/lesson-monitoring\?keyword=\$\{DEVICE_ID\}`\)/);
  assert.match(spec, /getByTestId\('monitoring-lesson-version'\)/);
  assert.match(spec, /monitoringRow\(page, 9, 'ASSIGNED'\)\.first\(\).*toBeVisible\(\)/s);
  assert.match(spec, /monitoringRow\(page, 9, 'CANCELLED'\)\.first\(\).*toBeVisible\(\)/s);
  assert.match(spec, /monitoringRow\(page, 8, 'ASSIGNED'\)\.first\(\).*toBeVisible\(\)/s);
  assert.match(spec, /const lessonByVersion = new Map/);
  assert.match(spec, /row\.lessonId === lessonByVersion\.get\(9\)\.id/);
  assert.match(spec, /assignmentId: current\.assignmentId, lessonId: lessonByVersion\.get\(9\)\.id/);
  assert.match(spec, /assignmentId: rollbackAssignment\.assignmentId, lessonId: lessonByVersion\.get\(8\)\.id/);
  assert.match(spec, /#\/course-lessons\?courseId=\$\{COURSE_ID\}/);
  assert.doesNotMatch(spec, /ignoreHTTPSErrors|rejectUnauthorized:\s*false/);
  assert.match(spec, /getByRole\('button', \{ name: \/assign to child\/i \}\)/);
  assert.match(spec, /getByRole\('dialog', \{ name: \/assign lesson to child\/i \}\)/);
  assert.match(spec, /waitForResponse\(.*lesson-assignments/s);
  assert.equal((spec.match(/adminApiResponse\(page, 'POST', '\/lesson-assignments'/g) || []).length, 1);
  assert.ok(spec.indexOf("adminApiResponse(page, 'POST', '/lesson-assignments'") > spec.indexOf('} else {'));
});

test('Task 4 release commands run candidate-bound NEW and ROLLBACK orchestration', () => {
  assert.equal(existsSync(orchestratorPath), true, 'assignment phase orchestrator must exist');
  const source = readFileSync(orchestratorPath, 'utf8');
  const imageIdentitySource = readFileSync(imageIdentityPath, 'utf8');
  const pkg = JSON.parse(readFileSync(resolve(__dirname, '../package.json'), 'utf8'));

  for (const required of [
    'TBOT_BACKEND_WORKTREE', 'TBOT_FIRMWARE_WORKTREE',
    'TBOT_LESSON_STUDIO_BACKEND_IMAGE', 'TBOT_LESSON_STUDIO_BACKEND_IMAGE_ID',
    'TBOT_LESSON_STUDIO_WEB_IMAGE', 'TBOT_LESSON_STUDIO_WEB_IMAGE_ID',
    'LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME',
    'LESSON_STUDIO_E2E_RESOURCE_PREFIX', 'TASK4_ASSIGNMENT_RUNTIME_CAPSULE_ROOT',
    'TASK4_ASSIGNMENT_RUNTIME_ROOT',
  ]) assert.match(source, new RegExp(required));
  assert.match(source, /const \{ validateAssignmentRuntimeCapsule \} = require\('\.\/task4-assignment-runtime\.cjs'\);/);
  assert.match(source, /const \{ runtimeRoot \} = validateAssignmentRuntimeCapsule\(process\.env, \[/);
  assert.doesNotMatch(source, /manager-web\/output|must be isolated under manager-web\/output/);
  const validationIndex = source.indexOf('validateAssignmentRuntimeCapsule(process.env, [');
  for (const sideEffect of ['mkdirSync(', 'inspectAndPinCandidateImages({', "run('openssl'", 'composeRun(']) {
    assert.ok(validationIndex < source.indexOf(sideEffect), `capsule validation must precede ${sideEffect}`);
  }
  assert.match(source, /composeExecutableFromEnvironment/);
  assert.match(source, /TBOT_DOCKER_EXECUTABLE/);
  assert.doesNotMatch(source, /run\('docker', \[\.\.\.compose/);
  assert.match(source, /\['new', 'rollback'\]/);
  assert.match(source, /docker-compose\.\$\{phase\}\.yml/);
  assert.match(source, /const baseCompose = \[[\s\S]*docker-compose\.lesson-studio-e2e\.yml[\s\S]*\];/);
  assert.match(source, /const baseComposeRun = \(\.\.\.args\) => run\(composeExecutable, \[\.\.\.baseCompose, \.\.\.args\]\);/);
  assert.match(
    source,
    /composeRun\('up', '-d', '--wait', '--no-deps', '--force-recreate', 'backend', 'web'\)/,
    'ROLLBACK must wait for refreshed candidate-bound services to become healthy',
  );
  const finalRollbackVerifyIndex = source.indexOf(
    "composeRun('exec', '-T', 'backend', '/nodejs/bin/node', '/task4-fixture/bootstrap.cjs', phase === 'new' ? 'verify-new' : 'verify-rollback');",
  );
  const baseRestoreCall = "baseComposeRun('up', '-d', '--wait', '--no-deps', '--force-recreate', 'backend', 'web');";
  const baseRestoreIndex = source.indexOf(baseRestoreCall);
  assert.ok(finalRollbackVerifyIndex >= 0, 'final bootstrap readback must verify rollback');
  assert.ok(baseRestoreIndex > finalRollbackVerifyIndex, 'base stack restore must follow final rollback verification');
  assert.ok(
    source.includes(`if (phase === 'rollback') {\n  ${baseRestoreCall}\n}`),
    'base stack restore must run only after ROLLBACK',
  );
  assert.match(source, /bootstrap\.cjs/);
  assert.match(source, /playwright\.assignment-rollback\.config\.js/);
  assert.match(source, /const mediaHostname = 'task4-media\.localhost'/);
  assert.match(source, /LESSON_STUDIO_E2E_BACKEND_HOST_PORT: process\.env\.LESSON_STUDIO_E2E_BACKEND_HOST_PORT \|\| '3100'/);
  assert.match(source, /LESSON_STUDIO_E2E_WEB_HOST_PORT: process\.env\.LESSON_STUDIO_E2E_WEB_HOST_PORT \|\| '8102'/);
  assert.match(source, /TBOT_FIRMWARE_WORKTREE lacks required candidate asset/);
  assert.match(source, /docker[\s\S]*image[\s\S]*inspect/);
  assert.match(imageIdentitySource, /candidate backend image ID mismatch/);
  assert.match(imageIdentitySource, /candidate web image ID mismatch/);
  assert.match(source, /verifyStartedServiceImages/);
  assert.match(source, /lesson\/assets\/robot\/poses\/bright-teach\.png/);
  assert.match(source, /TASK4_ASSIGNMENT_MEDIA_ORIGIN:\s*`https:\/\/\$\{mediaHostname\}:\$\{hostPort\}`/);
  assert.match(source, /subjectAltName=DNS:\$\{mediaHostname\}/);
  assert.match(source, /basicConstraints=critical,CA:TRUE/);
  assert.match(source, /if \(!existsSync\(tlsKey\) \|\| !existsSync\(tlsCert\)\)/);
  assert.doesNotMatch(source, /TASK4_ASSIGNMENT_MEDIA_ORIGIN:\s*`https:\/\/127\.0\.0\.1:/);
  const fixture = readFileSync(fixturePath, 'utf8');
  const session = readFileSync(resolve(__dirname, '../e2e/lesson-studio/helpers/session.js'), 'utf8');
  assert.match(fixture, /reachable\.port = '8443'/);
  assert.doesNotMatch(fixture, /reachable\.hostname = 'host\.docker\.internal'/);
  assert.match(session, /page\.route\(\/\^https:\\\/\\\/task4-media\\\.localhost/);
  assert.match(session, /ca,\s*servername: 'task4-media\.localhost'/s);
  assert.doesNotMatch(session, /ignoreHTTPSErrors|rejectUnauthorized:\s*false/);
  assert.match(fixture, /reachable\.hostname === 'task4-media\.localhost'/);
  assert.match(fixture, /ca: readFileSync\('\/task4-tls\/cert\.pem'\)/);
  assert.doesNotMatch(fixture, /rejectUnauthorized:\s*false/);
  assert.equal(pkg.scripts['test:e2e:course-mode:assignment:new'], 'node scripts/run-task4-assignment-phase.cjs new');
  assert.equal(pkg.scripts['test:e2e:course-mode:assignment:rollback'], 'node scripts/run-task4-assignment-phase.cjs rollback');
});
