#!/usr/bin/env node

import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const EXACT = Object.freeze({
  deviceId: '14:c1:9f:d1:ac:20',
  lessonKey: 'course-mode-pilot-cat-ball',
  lessonVersion: 1,
  packageVersion: 1,
  rendererVersion: 'teebot-lesson-renderer.v4',
  contractChecksum: 'cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264',
  layoutChecksum: 'e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c',
  profile: 'espTft',
  targetIds: ['animals.cat', 'toys.ball'],
  contractActivityIds: [
    'cat-discover-center-01', 'cat-meaning-left-right-01', 'cat-recall-visual-02',
    'cat-transfer-scene-01', 'cat-delayed-recall-01', 'ball-discover-center-01',
  ],
  authoredActivityIds: [
    'cat-discover', 'cat-meaning', 'cat-joint-speech', 'cat-recall',
    'cat-transfer', 'ball-discover', 'ball-meaning', 'cat-delayed',
  ],
});

const IDS = Object.freeze({
  owner: '70000000-0000-4000-8000-000000000001',
  household: '70000000-0000-4000-8000-000000000002',
  course: '70000000-0000-4000-8000-000000000003',
  lesson: '70000000-0000-4000-8000-000000000004',
  device: '70000000-0000-4000-8000-000000000005',
  assignment: '70000000-0000-4000-8000-000000000006',
  adultLearner: '70000000-0000-4000-8000-000000000007',
  bundle: '70000000-0000-4000-8000-000000000008',
});

export function buildSeedPlan({ deviceId, backendRoot = process.env.TBOT_BACKEND_WORKTREE } = {}) {
  if (deviceId !== EXACT.deviceId) throw new Error(`device identity drift: expected ${EXACT.deviceId}`);
  const root = resolveBackendRoot(backendRoot);
  const fixtureRoot = join(root, 'src/lessons/fixtures/course-mode');
  const contract = readJson(join(fixtureRoot, 'course-mode-pilot-cat-ball.json'));
  const pilotRoot = join(fixtureRoot, 'pilot/v1');
  const pilot = readJson(join(pilotRoot, 'pilot.json'));

  rejectForbiddenFields(contract);
  rejectForbiddenFields(pilot);
  assertExact(contract.fixtureId, EXACT.lessonKey, 'fixture identity');
  assertExact(contract.lesson?.lessonId, EXACT.lessonKey, 'lesson identity');
  assertExact(contract.lesson?.lessonVersion, EXACT.lessonVersion, 'lesson version');
  assertExact(contract.renderer?.rendererId, EXACT.rendererVersion, 'renderer identity');
  assertExact(contract.contractChecksum, EXACT.contractChecksum, 'contract checksum');
  assertExact(pilot.identity?.fixtureId, EXACT.lessonKey, 'package fixture identity');
  assertExact(pilot.identity?.lessonVersion, EXACT.lessonVersion, 'package lesson version');
  assertExact(pilot.identity?.packageVersion, EXACT.packageVersion, 'package version');
  assertExact(pilot.identity?.rendererId, EXACT.rendererVersion, 'package renderer identity');
  assertExact(pilot.identity?.semanticChecksum, EXACT.contractChecksum, 'semantic checksum');
  assertExact(pilot.identity?.layoutChecksum, EXACT.layoutChecksum, 'layout checksum');
  assertOrdered(pilot.targetIds, EXACT.targetIds, 'target identities');
  assertOrdered(contract.activities?.map(({ activityId }) => activityId), EXACT.contractActivityIds, 'contract activity identities');
  assertOrdered(pilot.activities?.map(({ activityId }) => activityId), EXACT.authoredActivityIds, 'authored activity identities');

  const provenance = readJson(join(pilotRoot, 'assets/provenance.json'));
  const assets = provenance.assets.map((asset) => ({
    assetKey: asset.assetId,
    path: `course-mode/pilot/v1/${asset.generatedPath}`,
    sha256: asset.sha256,
    bytes: asset.bytes,
    width: asset.width,
    height: asset.height,
    ...assetKind(asset.assetId),
  }));

  return Object.freeze({
    ...EXACT,
    backendRoot: root,
    ids: IDS,
    contract,
    authoredActivities: pilot.activities,
    turns: pilot.turns,
    assets,
  });
}

export async function executeSeedPlan(plan, client) {
  await client.query('BEGIN');
  try {
    await client.query(
      `INSERT INTO parent_accounts (id,email,password_hash,coppa_verified,email_verified)
       VALUES ($1,$2,$3,true,true)
       ON CONFLICT (id) DO UPDATE SET email=EXCLUDED.email`,
      [plan.ids.owner, 'course-mode-pilot-adult@local.invalid', 'disabled-local-synthetic-owner'],
    );
    await client.query(
      `INSERT INTO households (id,name,owner_id) VALUES ($1,$2,$3)
       ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,owner_id=EXCLUDED.owner_id`,
      [plan.ids.household, 'Disposable Course Mode physical TFT household', plan.ids.owner],
    );
    await client.query(
      `INSERT INTO courses (id,course_key,title,locale,age_band,status,created_by)
       VALUES ($1,$2,$3,'en-US','18+','draft',$4)
       ON CONFLICT (course_key) DO UPDATE SET title=EXCLUDED.title,status='draft',created_by=EXCLUDED.created_by`,
      [plan.ids.course, 'local-course-mode-physical-tft', 'Local Course Mode physical TFT', plan.ids.owner],
    );
    await client.query(
      `INSERT INTO lessons
         (id,course_id,lesson_key,lesson_version,manifest_version,title,locale,age_band,manifest_checksum,status,published_at,created_by)
       VALUES ($1,$2,$3,$4,$5,$6,'en-US','18+',$7,'published',NOW(),$8)
       ON CONFLICT (lesson_key,lesson_version) DO UPDATE SET
         course_id=EXCLUDED.course_id,manifest_version=EXCLUDED.manifest_version,title=EXCLUDED.title,
         manifest_checksum=EXCLUDED.manifest_checksum,status='published',published_at=COALESCE(lessons.published_at,NOW()),created_by=EXCLUDED.created_by`,
      [plan.ids.lesson, plan.ids.course, plan.lessonKey, plan.lessonVersion, plan.rendererVersion,
        'Course Mode Pilot: Cat and Ball', plan.contractChecksum, plan.ids.owner],
    );
    await client.query(
      `INSERT INTO lesson_course_mode_contracts
         (lesson_id,preset_id,preset_version,contract,contract_checksum)
       VALUES ($1,'courseCompanion',2,$2::jsonb,$3)
       ON CONFLICT (lesson_id) DO UPDATE SET contract=EXCLUDED.contract,contract_checksum=EXCLUDED.contract_checksum,updated_at=NOW()`,
      [plan.ids.lesson, JSON.stringify(plan.contract), plan.contractChecksum],
    );
    for (const [index, activity] of plan.authoredActivities.entries()) {
      const turn = plan.turns[index] ?? plan.turns[0];
      await client.query(
        `INSERT INTO lesson_steps
           (id,lesson_id,step_key,step_index,step_type,entrance,robot_state,pose,expression,phase,prompt,subject,step_body)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
         ON CONFLICT (lesson_id,step_key) DO UPDATE SET
           step_index=EXCLUDED.step_index,step_type=EXCLUDED.step_type,entrance=EXCLUDED.entrance,
           robot_state=EXCLUDED.robot_state,pose=EXCLUDED.pose,expression=EXCLUDED.expression,
           phase=EXCLUDED.phase,prompt=EXCLUDED.prompt,subject=EXCLUDED.subject,step_body=EXCLUDED.step_body`,
        [stepUuid(index), plan.ids.lesson, activity.activityId, index, 'model', index === 0 ? 'flyIn' : 'none',
          activity.assessed ? 'listening' : 'modeling', 'teach', 'teaching', activity.stage.toLowerCase(),
          turn.text, activity.targetId, JSON.stringify({ courseModeActivity: activity })],
      );
    }
    await client.query(
      `INSERT INTO asset_bundles (id,lesson_id,lesson_version,profile,manifest_checksum)
       VALUES ($1,$2,$3,$4,$5)
       ON CONFLICT (lesson_id,lesson_version,profile) DO UPDATE SET manifest_checksum=EXCLUDED.manifest_checksum`,
      [plan.ids.bundle, plan.ids.lesson, plan.lessonVersion, plan.profile, plan.contractChecksum],
    );
    for (const [index, asset] of plan.assets.entries()) {
      await client.query(
        `INSERT INTO assets
           (id,bundle_id,asset_key,layer,role,path,sha256,is_critical,media_type,bytes,width,height)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'image/png',$9,$10,$11)
         ON CONFLICT (bundle_id,asset_key) DO UPDATE SET
           layer=EXCLUDED.layer,role=EXCLUDED.role,path=EXCLUDED.path,sha256=EXCLUDED.sha256,
           is_critical=EXCLUDED.is_critical,media_type=EXCLUDED.media_type,bytes=EXCLUDED.bytes,width=EXCLUDED.width,height=EXCLUDED.height`,
        [assetUuid(index), plan.ids.bundle, asset.assetKey, asset.layer, asset.role, asset.path, asset.sha256,
          asset.isCritical, asset.bytes, asset.width, asset.height],
      );
    }
    await client.query(
      `INSERT INTO devices
         (id,serial_number,current_household_id,state,firmware_version,hardware_revision,claimed_by,lifecycle_state,status,mac_address,display_name)
       VALUES ($1,$2,$3,'ACTIVE','local-course-mode-pilot','esp32-tft-local',$4,'assigned','active',$5,$6)
       ON CONFLICT (id) DO UPDATE SET
         current_household_id=EXCLUDED.current_household_id,state='ACTIVE',claimed_by=EXCLUDED.claimed_by,
         lifecycle_state='assigned',status='active',mac_address=EXCLUDED.mac_address,display_name=EXCLUDED.display_name`,
      [plan.ids.device, 'LOCAL-COURSE-MODE-PHYSICAL-TFT', plan.ids.household, plan.ids.owner, plan.deviceId, 'Local Course Mode TFT'],
    );
    await client.query(
      `INSERT INTO lesson_assignments
         (id,device_id,child_id,household_id,lesson_id,lesson_version,profile,state,assignment_version,idempotency_key)
       VALUES ($1,$2,$3,$4,$5,$6,$7,'ASSIGNED',1,$8)
       ON CONFLICT (id) DO UPDATE SET
         device_id=EXCLUDED.device_id,child_id=EXCLUDED.child_id,household_id=EXCLUDED.household_id,
         lesson_id=EXCLUDED.lesson_id,lesson_version=EXCLUDED.lesson_version,profile=EXCLUDED.profile,
         state='ASSIGNED',assignment_version=1,idempotency_key=EXCLUDED.idempotency_key,updated_at=NOW()`,
      [plan.ids.assignment, plan.ids.device, plan.ids.adultLearner, plan.ids.household, plan.ids.lesson,
        plan.lessonVersion, plan.profile, 'local-course-mode-physical-tft-v1'],
    );
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  }
}

function resolveBackendRoot(value) {
  if (!value) throw new Error('TBOT_BACKEND_WORKTREE or backendRoot is required');
  return resolve(value);
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf8'));
}

function assertExact(actual, expected, label) {
  if (actual !== expected) throw new Error(`${label} drift: expected ${expected}, received ${actual}`);
}

function assertOrdered(actual, expected, label) {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) throw new Error(`${label} drift`);
}

function rejectForbiddenFields(value, path = '$') {
  if (Array.isArray(value)) return value.forEach((item, index) => rejectForbiddenFields(item, `${path}[${index}]`));
  if (!value || typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value)) {
    const normalized = key.replace(/[-_]/g, '').toLowerCase();
    const forbidden = normalized.includes('child') || normalized.includes('transcript') || normalized.includes('utterance')
      || (normalized.includes('audio') && normalized !== 'targetaudiobeforeassessment')
      || normalized.includes('pronunciation') || normalized.includes('servo') || normalized.includes('freeformstory');
    if (forbidden) throw new Error(`forbidden field ${path}.${key}`);
    rejectForbiddenFields(child, `${path}.${key}`);
  }
}

function assetKind(assetId) {
  if (assetId.startsWith('background.')) return { layer: 'backgroundScene', role: 'poster', isCritical: true };
  if (assetId.startsWith('object.')) return { layer: 'teachingObject', role: 'primarySubject', isCritical: true };
  return { layer: 'robotOverlay', role: 'pose', isCritical: false };
}

function stepUuid(index) {
  return `71000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`;
}

function assetUuid(index) {
  return `72000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`;
}

function assertLocalDatabaseUrl(databaseUrl) {
  if (!databaseUrl) throw new Error('DATABASE_URL must be explicitly supplied');
  const parsed = new URL(databaseUrl);
  if (parsed.protocol !== 'postgres:' && parsed.protocol !== 'postgresql:') throw new Error('DATABASE_URL must use PostgreSQL');
  if (!['localhost', '127.0.0.1', '::1'].includes(parsed.hostname)) throw new Error('DATABASE_URL must point to a local PostgreSQL host');
  if (/prod(uction)?/i.test(databaseUrl)) throw new Error('DATABASE_URL must not reference production');
}

async function main() {
  const backendRoot = process.env.TBOT_BACKEND_WORKTREE;
  const databaseUrl = process.env.DATABASE_URL;
  assertLocalDatabaseUrl(databaseUrl);
  const plan = buildSeedPlan({ deviceId: EXACT.deviceId, backendRoot });
  const require = createRequire(join(resolveBackendRoot(backendRoot), 'package.json'));
  const { Pool } = require('pg');
  const pool = new Pool({ connectionString: databaseUrl, max: 1 });
  const client = await pool.connect();
  try {
    await executeSeedPlan(plan, client);
    process.stdout.write(`Seeded ${plan.lessonKey}@${plan.lessonVersion} for ${plan.deviceId}\n`);
  } finally {
    client.release();
    await pool.end();
  }
}

const isCli = process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isCli) main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
