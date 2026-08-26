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
  const derivativeIndex = readJson(join(pilotRoot, 'derivatives/index.json'));
  assertExact(derivativeIndex.rendererId, EXACT.rendererVersion, 'derivative renderer identity');
  assertOrdered(derivativeIndex.cues?.map(({ cueId }) => cueId), EXACT.authoredActivityIds, 'derivative cue identities');
  const assets = provenance.assets.map((asset) => ({
    assetKey: asset.assetId,
    path: `course-mode/pilot/v1/${asset.generatedPath}`,
    sha256: asset.sha256,
    bytes: asset.bytes,
    width: asset.width,
    height: asset.height,
    ...assetKind(asset.assetId),
  }));
  const steps = pilot.activities.map((activity, index) => seedStep(activity, pilot.turns[index] ?? pilot.turns[0], index));
  const { flattenedPhases, sourceVisuals } = buildFlattenedPhases(derivativeIndex.cues, root);
  steps[0].stepBody.cinematicPhases = flattenedPhases.map((phase) => phase.authored);
  const manifestChecksum = computeManifestChecksum(root, buildManifestIdentity({
    assets, steps, flattenedPhases, contract,
  }));

  return Object.freeze({
    ...EXACT,
    backendRoot: root,
    ids: IDS,
    contract,
    authoredActivities: pilot.activities,
    steps,
    assets,
    flattenedPhases,
    sourceVisuals,
    manifestChecksum,
  });
}

export async function executeSeedPlan(plan, client) {
  await client.query('BEGIN');
  try {
    // Migration 099's legacy image-only check conflicts with the later cinematic-source contract.
    await client.query(
      'ALTER TABLE shared_visual_asset_versions DROP CONSTRAINT IF EXISTS shared_visual_asset_versions_tvideo_compatibility_check',
    );
    await client.query(
      `INSERT INTO parent_accounts (id,email,password_hash,coppa_verified)
       VALUES ($1,$2,$3,true)
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
        'Course Mode Pilot: Cat and Ball', plan.manifestChecksum, plan.ids.owner],
    );
    await client.query(
      `INSERT INTO lesson_course_mode_contracts
         (lesson_id,preset_id,preset_version,contract,contract_checksum)
       VALUES ($1,'courseCompanion',2,$2::jsonb,$3)
       ON CONFLICT (lesson_id) DO UPDATE SET contract=EXCLUDED.contract,contract_checksum=EXCLUDED.contract_checksum,updated_at=NOW()`,
      [plan.ids.lesson, JSON.stringify(plan.contract), plan.contractChecksum],
    );
    for (const [index, step] of plan.steps.entries()) {
      await client.query(
        `INSERT INTO lesson_steps
           (id,lesson_id,step_key,step_index,step_type,entrance,robot_state,pose,expression,phase,prompt,subject,step_body)
         VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb)
         ON CONFLICT (lesson_id,step_key) DO UPDATE SET
           step_index=EXCLUDED.step_index,step_type=EXCLUDED.step_type,entrance=EXCLUDED.entrance,
           robot_state=EXCLUDED.robot_state,pose=EXCLUDED.pose,expression=EXCLUDED.expression,
           phase=EXCLUDED.phase,prompt=EXCLUDED.prompt,subject=EXCLUDED.subject,step_body=EXCLUDED.step_body`,
        [stepUuid(index), plan.ids.lesson, step.stepKey, index, step.stepType, step.entrance,
          step.robotState, step.pose, step.expression, step.phase, step.prompt, step.subject, JSON.stringify(step.stepBody)],
      );
    }
    await client.query(
      `INSERT INTO asset_bundles (id,lesson_id,lesson_version,profile,manifest_checksum)
       VALUES ($1,$2,$3,$4,$5)
       ON CONFLICT (lesson_id,lesson_version,profile) DO UPDATE SET manifest_checksum=EXCLUDED.manifest_checksum`,
      [plan.ids.bundle, plan.ids.lesson, plan.lessonVersion, plan.profile, plan.manifestChecksum],
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
    for (const source of plan.sourceVisuals) {
      await client.query(
        `INSERT INTO shared_visual_assets (id,asset_key,category,title)
         VALUES ($1,$2,$3,$4) ON CONFLICT (asset_key) DO NOTHING`,
        [source.assetId, source.assetKey, source.category, source.title],
      );
      await client.query(
        `INSERT INTO shared_visual_asset_versions
           (id,asset_id,version,profile,storage_path,sha256,mime_type,bytes,width,height,
            publication_state,published_at,compatibility_metadata)
         VALUES ($1,$2,1,'espTft',$3,$4,'video/mp4',$5,480,320,'published',NOW(),$6::jsonb)
         ON CONFLICT (asset_id,version) DO NOTHING`,
        [source.versionId, source.assetId, source.storagePath, source.sha256, source.bytes,
          JSON.stringify(source.compatibilityMetadata)],
      );
    }
    for (const phase of plan.flattenedPhases) {
      for (const ref of phase.refs) {
        await client.query(
          `INSERT INTO lesson_visual_refs (id,lesson_id,step_key,slot,asset_version_id)
           VALUES ($1,$2,$3,$4,$5)
           ON CONFLICT (lesson_id,step_key,slot) DO UPDATE SET asset_version_id=EXCLUDED.asset_version_id`,
          [ref.id, plan.ids.lesson, plan.steps[0].stepKey, ref.slot, ref.assetVersionId],
        );
      }
    }
    for (const phase of plan.flattenedPhases) {
      await client.query(
        `INSERT INTO flattened_cinematic_derivatives
           (derivative_id,lesson_id,lesson_version,phase_id,source_revision,is_current,status,
            output_path,output_url,output_sha256,output_bytes,output_metadata,completed_at)
         VALUES ($1,$2,$3,$4,1,true,'ready',$5,$6,$7,$8,$9::jsonb,NOW())
         ON CONFLICT (derivative_id) DO UPDATE SET
           is_current=true,status='ready',output_path=EXCLUDED.output_path,output_url=EXCLUDED.output_url,
           output_sha256=EXCLUDED.output_sha256,output_bytes=EXCLUDED.output_bytes,
           output_metadata=EXCLUDED.output_metadata,completed_at=NOW(),updated_at=NOW()`,
        [phase.derivativeId, plan.ids.lesson, plan.lessonVersion, phase.phaseId, phase.path, phase.url,
          phase.sha256, phase.bytes, JSON.stringify(phase.metadata)],
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

function seedStep(activity, turn, index) {
  return {
    stepKey: activity.activityId,
    stepType: 'model',
    entrance: index === 0 ? 'flyIn' : 'none',
    robotState: activity.assessed ? 'listening' : 'modeling',
    pose: 'teach',
    expression: 'teaching',
    phase: activity.stage.toLowerCase(),
    prompt: turn.text,
    subject: activity.targetId,
    stepBody: { courseModeActivity: activity },
  };
}

function buildFlattenedPhases(cues, backendRoot) {
  const require = createRequire(join(backendRoot, 'package.json'));
  const { buildFlattenedCinematicSourceDescriptor } = require(join(
    backendRoot, 'dist/lessons/derivatives/flattened-cinematic-source.js',
  ));
  const phaseIds = ['opening', 'greet', 'teach', 'listen', 'thinking', 'correct', 'retry', 'celebrate'];
  const sourceVisuals = [
    sourceVisual(cues[0], 0, 'scene', 'local.course-mode.scene'),
    sourceVisual(cues[1], 1, 'teachingObject', 'local.course-mode.object'),
    sourceVisual(cues[2], 2, 'robotPose', 'local.course-mode.robot'),
  ];
  const flattenedPhases = cues.map((cue, index) => {
    const phaseId = phaseIds[index];
    const authoredLayers = {
      background: `${sourceVisuals[0].assetKey}@v1`,
      teachingObject: `${sourceVisuals[1].assetKey}@v1`,
      robotOverlay: `${sourceVisuals[2].assetKey}@v1`,
    };
    const refs = sourceVisuals.map((source, layerIndex) => ({
      id: visualRefUuid(index, layerIndex),
      assetVersionId: source.versionId,
      slot: `${['backgroundScene', 'teachingObject', 'robotOverlay'][layerIndex]}.${phaseId}`,
      asset_version_id: source.versionId,
      step_key: EXACT.authoredActivityIds[0],
      category: source.category,
      asset_key: source.assetKey,
      version: 1,
      profile: 'espTft',
      publication_state: 'published',
      storage_path: source.storagePath,
      sha256: source.sha256,
      mime_type: 'video/mp4',
      bytes: String(source.bytes),
      width: 480,
      height: 320,
      compatibility_metadata: source.compatibilityMetadata,
    }));
    const descriptor = buildFlattenedCinematicSourceDescriptor({
      lessonId: IDS.lesson,
      lessonVersion: EXACT.lessonVersion,
      sourceRevision: 1,
      phaseId,
      durationMs: cue.durationMs,
      authoredLayers,
      refs,
    });
    const path = `lessons/derivatives/${descriptor.derivativeId}/${phaseId}.mp4`;
    return {
      phaseId,
      derivativeId: descriptor.derivativeId,
      path,
      url: `https://course-mode-assets.local.invalid/${path}`,
      sha256: cue.sha256,
      bytes: cue.bytes,
      metadata: { codec: 'mjpeg', fps: 10, durationMs: cue.durationMs, frameCount: cue.frameCount, hasAudio: false },
      authored: { phaseId, timing: { durationMs: cue.durationMs }, layers: authoredLayers },
      refs,
    };
  });
  return { flattenedPhases, sourceVisuals };
}

function sourceVisual(cue, index, category, assetKey) {
  return {
    assetId: `73000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
    versionId: `74000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`,
    assetKey,
    category,
    title: `Local Course Mode ${category}`,
    storagePath: `course-mode/pilot/v1/${cue.path}`,
    sha256: cue.sha256,
    bytes: cue.bytes,
    compatibilityMetadata: {
      codec: 'mjpeg', width: 480, height: 320, durationMs: cue.durationMs,
      frameCount: cue.frameCount, fpsNumerator: 10, fpsDenominator: 1, hasAudio: false,
      rect: { x: 0, y: 0, width: 480, height: 320 },
      chromaKey: category === 'scene' ? null : { color: { r: 0, g: 255, b: 0 }, tolerance: 20, feather: 4 },
    },
  };
}

function buildManifestIdentity({ assets, steps, flattenedPhases, contract }) {
  return {
    manifestVersion: EXACT.rendererVersion,
    courseId: 'local-course-mode-physical-tft',
    lessonId: EXACT.lessonKey,
    lessonVersion: EXACT.lessonVersion,
    locale: 'en-US',
    ageBand: '18+',
    profile: EXACT.profile,
    assets: [...assets].sort((a, b) => layerRank(a.layer) - layerRank(b.layer) || a.assetKey.localeCompare(b.assetKey)).map((asset) => ({
      id: asset.assetKey, layer: asset.layer, role: asset.role, mediaType: 'image/png', path: asset.path,
      sha256: asset.sha256, bytes: asset.bytes, dimensions: { width: asset.width, height: asset.height }, critical: asset.isCritical,
    })),
    steps: steps.map((step) => ({
      id: step.stepKey, type: step.stepType, prompt: step.prompt, robotState: step.robotState,
      pose: step.pose, expression: step.expression, phase: step.phase, entrance: step.entrance, subject: step.subject,
    })),
    protocolVersion: EXACT.rendererVersion,
    cinematicPhases: flattenedPhases.map((phase) => ({
      templateId: 'flattenedMjpegCinematic', templateVersion: 1, phaseId: phase.phaseId,
      timing: { durationMs: phase.metadata.durationMs },
      asset: { derivativeId: phase.derivativeId, path: phase.path, url: phase.path, sha256: phase.sha256,
        bytes: phase.bytes, mediaType: 'video/mp4', width: 480, height: 320, metadata: phase.metadata },
    })),
    features: { lessonRendererV4: { flattenedMjpegCinematic: true, assetSource: 'publishedFlattenedDerivative' } },
    courseModeContract: contract,
  };
}

function computeManifestChecksum(backendRoot, identity) {
  const require = createRequire(join(backendRoot, 'package.json'));
  const { computeManifestChecksum: compute } = require(join(
    backendRoot, 'dist/lessons/lesson-manifest.canonical.cjs',
  ));
  return compute(identity);
}

function layerRank(layer) {
  return { backgroundScene: 0, teachingObject: 1, robotOverlay: 2 }[layer] ?? 99;
}

function stepUuid(index) {
  return `71000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`;
}

function assetUuid(index) {
  return `72000000-0000-4000-8000-${String(index + 1).padStart(12, '0')}`;
}

function visualRefUuid(phaseIndex, layerIndex) {
  return `75000000-0000-4000-8000-${String(phaseIndex * 3 + layerIndex + 1).padStart(12, '0')}`;
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
