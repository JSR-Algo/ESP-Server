import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { buildSeedPlan, executeSeedPlan } from './seed-course-mode-pilot.mjs';

const DEVICE_ID = '14:c1:9f:d1:ac:20';
const BACKEND_ROOT = '/Users/manhhodinh/Documents/TBOT/tbot-backend';

test('builds the exact canonical local pilot plan', () => {
  const plan = buildSeedPlan({ deviceId: DEVICE_ID, backendRoot: BACKEND_ROOT });

  assert.equal(plan.lessonKey, 'course-mode-pilot-cat-ball');
  assert.equal(plan.lessonVersion, 1);
  assert.equal(plan.rendererVersion, 'teebot-lesson-renderer.v4');
  assert.equal(plan.contractChecksum, 'cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264');
  assert.equal(plan.layoutChecksum, 'e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c');
  assert.equal(plan.profile, 'espTft');
  assert.equal(plan.packageVersion, 1);
  assert.equal(plan.deviceId, DEVICE_ID);
  assert.deepEqual(plan.targetIds, ['animals.cat', 'toys.ball']);
  assert.deepEqual(plan.contractActivityIds, [
    'cat-discover-center-01', 'cat-meaning-left-right-01', 'cat-recall-visual-02',
    'cat-transfer-scene-01', 'cat-delayed-recall-01', 'ball-discover-center-01',
  ]);
  assert.deepEqual(plan.authoredActivityIds, [
    'cat-discover', 'cat-meaning', 'cat-joint-speech', 'cat-recall',
    'cat-transfer', 'ball-discover', 'ball-meaning', 'cat-delayed',
  ]);
  assert.equal(plan.childData, undefined);
});

test('rejects canonical identity drift', () => {
  const root = fixtureRoot((contract) => { contract.renderer.rendererId = 'teebot-lesson-renderer.v5'; });
  assert.throws(
    () => buildSeedPlan({ deviceId: DEVICE_ID, backendRoot: root }),
    /renderer identity drift/,
  );
});

test('rejects forbidden privacy and physical-control fields anywhere in fixtures', () => {
  for (const key of ['childName', 'transcript', 'utterance', 'audioUrl', 'pronunciationScore', 'servoAngle', 'freeFormStory']) {
    const root = fixtureRoot((_contract, pilot) => { pilot.turns[0][key] = 'forbidden'; });
    assert.throws(
      () => buildSeedPlan({ deviceId: DEVICE_ID, backendRoot: root }),
      new RegExp(`forbidden field.*${key}`, 'i'),
    );
  }
});

test('executes deterministic SQL in one transaction', async () => {
  const plan = buildSeedPlan({ deviceId: DEVICE_ID, backendRoot: BACKEND_ROOT });
  const calls = [];
  const client = { query: async (sql, params = []) => { calls.push([sql, params]); return { rows: [], rowCount: 1 }; } };

  await executeSeedPlan(plan, client);

  assert.equal(calls[0][0], 'BEGIN');
  assert.equal(calls.at(-1)[0], 'COMMIT');
  assert.equal(calls.filter(([sql]) => sql === 'BEGIN').length, 1);
  assert.equal(calls.filter(([sql]) => sql === 'COMMIT').length, 1);
  assert.match(calls.map(([sql]) => sql).join('\n'), /INSERT INTO lesson_course_mode_contracts/);
  assert.match(calls.map(([sql]) => sql).join('\n'), /INSERT INTO lesson_steps/);
  assert.match(calls.map(([sql]) => sql).join('\n'), /INSERT INTO lesson_assignments/);
  assert.ok(calls.flatMap(([, params]) => params).includes(DEVICE_ID));
  assert.ok(calls.flatMap(([, params]) => params).includes(plan.contractChecksum));
  assert.equal(JSON.stringify(calls).includes('production'), false);
});

function fixtureRoot(mutate) {
  const root = mkdtempSync(join(tmpdir(), 'course-mode-seed-'));
  const fixtureDir = join(root, 'src/lessons/fixtures/course-mode');
  const pilotDir = join(fixtureDir, 'pilot/v1');
  mkdirSync(pilotDir, { recursive: true });
  const contract = JSON.parse(readFileSync(join(BACKEND_ROOT, 'src/lessons/fixtures/course-mode/course-mode-pilot-cat-ball.json'), 'utf8'));
  const pilot = JSON.parse(readFileSync(join(BACKEND_ROOT, 'src/lessons/fixtures/course-mode/pilot/v1/pilot.json'), 'utf8'));
  mutate(contract, pilot);
  writeFileSync(join(fixtureDir, 'course-mode-pilot-cat-ball.json'), JSON.stringify(contract));
  writeFileSync(join(pilotDir, 'pilot.json'), JSON.stringify(pilot));
  return root;
}
