const assert = require('node:assert/strict');
const { existsSync, readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const test = require('node:test');
const {
  inspectAndPinCandidateImages,
  verifyStartedServiceImages,
} = require('./task4-image-identity.cjs');

const fixturePath = resolve(__dirname, '../../../docs/docker/task4-admin-assignment/bootstrap.cjs');
const rollbackSpecPath = resolve(__dirname, '../e2e/lesson-studio/assignment-rollback-phase.spec.js');
const playwrightConfigPath = resolve(__dirname, '../playwright.assignment-rollback.config.js');
const orchestratorPath = resolve(__dirname, 'run-task4-assignment-phase.cjs');
const imageIdentityPath = resolve(__dirname, 'task4-image-identity.cjs');

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
  assert.doesNotMatch(source, /fixture\.local/);
  assert.doesNotMatch(source, /output_bytes=1/);
  assert.doesNotMatch(source, /preview:\$\{row\.derivative_id\}/);
});

test('Task 4 assignment phases recreate only the backend and preserve PostgreSQL state', () => {
  for (const phase of ['new', 'rollback']) {
    const compose = readFileSync(
      resolve(__dirname, `../../../docs/docker/task4-admin-assignment/docker-compose.${phase}.yml`),
      'utf8',
    );
    assert.match(compose, /LESSON_ROLLOUT_DEVICE_ALLOWLIST:\s*91deb5af-c1c0-416b-956d-266d510eac5e/);
    assert.match(compose, /derivative-media:/);
    assert.match(compose, /TASK4_ASSIGNMENT_MEDIA_ROOT/);
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
    'LESSON_STUDIO_E2E_RESOURCE_PREFIX', 'TASK4_ASSIGNMENT_RUNTIME_ROOT',
  ]) assert.match(source, new RegExp(required));
  assert.match(source, /docker[\s\S]*compose/);
  assert.match(source, /\['new', 'rollback'\]/);
  assert.match(source, /docker-compose\.\$\{phase\}\.yml/);
  assert.match(source, /bootstrap\.cjs/);
  assert.match(source, /playwright\.assignment-rollback\.config\.js/);
  assert.match(source, /const mediaHostname = 'task4-media\.localhost'/);
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
  assert.match(session, /page\.route\(\/\^https:\\\/\\\/task4-media\\\.localhost/);
  assert.match(session, /ca,\s*servername: 'task4-media\.localhost'/s);
  assert.doesNotMatch(session, /ignoreHTTPSErrors|rejectUnauthorized:\s*false/);
  assert.match(fixture, /reachable\.hostname === 'task4-media\.localhost'/);
  assert.match(fixture, /ca: readFileSync\('\/task4-tls\/cert\.pem'\)/);
  assert.doesNotMatch(fixture, /rejectUnauthorized:\s*false/);
  assert.equal(pkg.scripts['test:e2e:course-mode:assignment:new'], 'node scripts/run-task4-assignment-phase.cjs new');
  assert.equal(pkg.scripts['test:e2e:course-mode:assignment:rollback'], 'node scripts/run-task4-assignment-phase.cjs rollback');
});
