'use strict';

process.env.FLATTENED_CINEMATIC_RENDERER_BUILD_SHA256 ||= 'a'.repeat(64);
process.env.TVIDEO_FONT_BUNDLE_SHA256 ||= 'b'.repeat(64);

const { createHash } = require('node:crypto');
const { createReadStream, readFileSync } = require('node:fs');
const { mkdir, open, rm, stat } = require('node:fs/promises');
const { get: httpsGet } = require('node:https');
const { dirname, join } = require('node:path');
const { Pool } = require('/app/node_modules/pg');
const { replaceWithCopy } = require('/task4-fixture/copy-file.cjs');
const { LessonAuthoringService } = require('/app/dist/lessons/authoring/lesson-authoring.service.js');
const {
  FARM_V7_BOOTSTRAP_ASSETS,
  FARM_V7_BOOTSTRAP_JOURNEY,
} = require('/app/dist/lessons/tvideo-journey/farm-v7-bootstrap.js');
const { deriveTVideoJourneyCuePlan } = require('/app/dist/lessons/tvideo-journey/tvideo-journey.cues.js');
const { LessonAssetGenerationRepository } = require('/app/dist/lessons/lesson-asset-generation.repository.js');
const { buildCanonicalGenerationIndex } = require('/app/dist/lessons/lesson-asset-generation.contract.js');
const {
  TBOT_RGB565_FRAME_BYTES,
  validateTbotRgb565File,
  writeTbotRgb565File,
} = require('/app/dist/lessons/derivatives/tbot-rgb565.js');
const {
  FARM_V9_ROLLOUT,
  runFarmV9GeometryRollout,
} = require('/app/dist/lessons/tvideo-journey/farm-v9-geometry-rollout.js');

const ADMIN_ID = '11111111-1111-4111-8111-111111111111';
const PARENT_ID = '22222222-2222-4222-8222-222222222222';
const HOUSEHOLD_ID = '33333333-3333-4333-8333-333333333333';
const CHILD_ID = '44444444-4444-4444-8444-444444444444';
const DEVICE_ID = FARM_V9_ROLLOUT.deviceId;
const COURSE_ID = '66666666-6666-4666-8666-666666666666';
const LESSON_ID = FARM_V9_ROLLOUT.sourceLessonId;
const LESSON_KEY = FARM_V9_ROLLOUT.lessonKey;
const DEVICE_MEDIA_TYPE = 'application/vnd.tbot.rgb565-indexed';
const MEDIA_ROOT = process.env.TASK4_ASSIGNMENT_MEDIA_ROOT;
const MEDIA_ORIGIN = process.env.TASK4_ASSIGNMENT_MEDIA_ORIGIN;
if (!MEDIA_ROOT) throw new Error('TASK4_ASSIGNMENT_MEDIA_ROOT is required');
if (!MEDIA_ORIGIN || new URL(MEDIA_ORIGIN).protocol !== 'https:') {
  throw new Error('TASK4_ASSIGNMENT_MEDIA_ORIGIN must be an HTTPS origin');
}
const EXPECTED = Object.freeze({
  sharedVisualAssets: 7,
  lessonSteps: 2,
  bundleAssets: 8,
  derivatives: 19,
});
const BUNDLE_ASSETS = Object.freeze([
  ['backgroundScene.poster', 'backgroundScene', 'poster', 'd5cdaba9f9086ef56a5f41c5fddf2e32b91ecfe141cc346f3221c7b221a3a357', true, 'image/jpeg', 18482, 320, 180],
  ['teachingObject.barn', 'teachingObject', 'primarySubject', 'bf3d88d17867f02872e3e6aff31b5d4d0a94977a5efa4214b7e831122938511b', true, 'image/png', 42107, 192, 192],
  ['teachingObject.farm', 'teachingObject', 'supportSubject', 'e84ac5ae133e6bfa5cdcca88fe2c6d91e30b6a7f952adcc9bbf29c60c5ab8138', false, 'image/png', 38451, 192, 192],
  ['teachingObject.hay', 'teachingObject', 'supportSubject', '66d4bdfe70783f389e30d488b1184dd9b48182b7f29406a196a2ec616fedbb7c', false, 'image/png', 24369, 192, 192],
  ['robotOverlay.teach', 'robotOverlay', 'pose', '576d86a75686f6eab606295529593da14b01554e21e0601c8f29aedbc1ba4965', false, 'image/png', 45408, 186, 192],
  ['robotOverlay.listening', 'robotOverlay', 'pose', '572a61f140eca17968a85f61704967d03a1a3311222335e32b94b1ab370e2419', false, 'image/png', 43615, 169, 192],
  ['robotOverlay.thinking', 'robotOverlay', 'pose', '3d3d3c3a7c6993ad2346e11cfee72a15750d0b5f35642b8d949902a8745352c8', false, 'image/png', 38733, 130, 192],
  ['robotOverlay.celebrate', 'robotOverlay', 'pose', '8392fb31c53030147d27fbd96c5b2dd1a4e5c33efd35f8727bee6dabda62605d', false, 'image/png', 44193, 192, 190],
]);
const admin = Object.freeze({
  adminUserId: ADMIN_ID,
  role: 'super_admin',
  roles: ['super_admin'],
  canAuthorLessons: true,
});

async function main() {
  const mode = process.argv[2];
  if (!['seed-v8', 'rollout-v9', 'cancel-v9-assignment', 'rollback-v9', 'verify', 'verify-new', 'verify-rollback'].includes(mode)) {
    throw new Error('usage: bootstrap.cjs seed-v8|rollout-v9|cancel-v9-assignment|rollback-v9|verify|verify-new|verify-rollback');
  }
  if (mode === 'rollout-v9') {
    await resetLocalV9Drafts();
    const result = await runFarmV9GeometryRollout({ mode: 'apply', api: await authenticatedApi() });
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }
  if (mode === 'rollback-v9') {
    const result = await runFarmV9GeometryRollout({ mode: 'rollback', api: await authenticatedApi() });
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }
  if (mode === 'cancel-v9-assignment') {
    const api = await authenticatedApi();
    const assignments = await api.listAssignments(DEVICE_ID, 20);
    const active = assignments.assignments.find((row) => row.lessonKey === LESSON_KEY
      && row.lessonVersion === 9
      && ['ASSIGNED', 'PRELOADING', 'READY', 'RUNNING', 'PAUSED'].includes(row.state));
    if (!active) throw new Error('active v9 assignment missing after rollout bootstrap');
    await api.cancelAssignment(active.assignmentId, {
      expectedAssignmentVersion: active.assignmentVersion,
      reason: 'CONTENT_REPLACED',
    });
    process.stdout.write(`${JSON.stringify({ cancelled: true, lessonVersion: 9 })}\n`);
    return;
  }

  const pool = new Pool({ connectionString: process.env.DATABASE_URL });
  try {
    if (mode === 'seed-v8') await seedV8(pool);
    const evidence = await verifyFixture(pool, mode !== 'seed-v8');
    if (mode === 'verify-new' || mode === 'verify-rollback') {
      await verifyAssignmentState(pool, mode === 'verify-new' ? 'new' : 'rollback');
    }
    process.stdout.write(`${JSON.stringify(evidence)}\n`);
  } finally {
    await pool.end();
  }
}

async function resetLocalV9Drafts() {
  const pool = new Pool({ connectionString: process.env.DATABASE_URL });
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    await client.query(
      `DELETE FROM admin_login_attempts WHERE email='lesson-author-e2e@local.invalid'`,
    );
    const found = await client.query(
      `SELECT id FROM lessons WHERE course_id=$1::uuid AND lesson_key=$2 AND lesson_version>=9`,
      [COURSE_ID, LESSON_KEY],
    );
    for (const { id } of found.rows) {
      await client.query(`DELETE FROM lesson_assignments WHERE lesson_id=$1::uuid`, [id]);
      for (const table of [
        'lesson_tvideo_pronunciation_units', 'lesson_tvideo_step_values', 'lesson_tvideo_steps',
        'lesson_tvideo_walk_keyframes', 'lesson_tvideo_robot_clips', 'lesson_tvideo_journeys',
        'flattened_cinematic_derivatives', 'lesson_visual_refs', 'lesson_steps',
      ]) await client.query(`DELETE FROM ${table} WHERE lesson_id=$1::uuid`, [id]);
      await client.query(`DELETE FROM assets WHERE bundle_id IN (SELECT id FROM asset_bundles WHERE lesson_id=$1::uuid)`, [id]);
      await client.query(`DELETE FROM asset_bundles WHERE lesson_id=$1::uuid`, [id]);
      await client.query(`DELETE FROM lessons WHERE id=$1::uuid`, [id]);
    }
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
    await pool.end();
  }
}

async function seedV8(pool) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    // Remove only the previously documented Task 4 placeholder identities.
    await client.query(`DELETE FROM lessons WHERE id IN (
      '77777777-7777-4777-8777-777777777778'::uuid,
      '77777777-7777-4777-8777-777777777779'::uuid
    )`);
    await client.query(
      `DELETE FROM lesson_asset_generations
        WHERE index_checksum=$1 OR pack_index @> $2::jsonb
          OR EXISTS (SELECT 1 FROM jsonb_array_elements(pack_index) pack WHERE pack->>'lessonId'=$3)`,
      ['a'.repeat(64), JSON.stringify([{ lessonId: LESSON_KEY, manifestChecksum: '9'.repeat(64) }]), LESSON_KEY],
    );
    await client.query(`DELETE FROM lesson_assignments WHERE device_id IN (
      '55555555-5555-4555-8555-555555555555'::uuid,
      $1::uuid
    )`, [DEVICE_ID]);
    await client.query(`DELETE FROM devices WHERE id='55555555-5555-4555-8555-555555555555'::uuid`);
    await client.query(`DELETE FROM courses WHERE id='66666666-6666-4666-8666-666666666666'::uuid`);
    for (const table of [
      'lesson_tvideo_pronunciation_units', 'lesson_tvideo_step_values', 'lesson_tvideo_steps',
      'lesson_tvideo_walk_keyframes', 'lesson_tvideo_robot_clips', 'lesson_tvideo_journeys',
      'flattened_cinematic_derivatives', 'lesson_visual_refs', 'lesson_steps',
    ]) {
      await client.query(`DELETE FROM ${table} WHERE lesson_id=$1::uuid`, [LESSON_ID]);
    }
    await client.query(`DELETE FROM assets WHERE bundle_id IN (
      SELECT id FROM asset_bundles WHERE lesson_id=$1::uuid
    )`, [LESSON_ID]);
    await client.query(`DELETE FROM asset_bundles WHERE lesson_id=$1::uuid`, [LESSON_ID]);
    await client.query(`DELETE FROM lessons WHERE id=$1::uuid OR (lesson_key=$2 AND lesson_version IN (8,9))`, [LESSON_ID, LESSON_KEY]);

    await seedFamily(client);
    await seedVisualSources(client);
    await client.query(
      `INSERT INTO courses(id,course_key,title,locale,age_band,status,created_by,asset_sync_scope)
       VALUES($1::uuid,$2,'Task 4 Farm Rollback','en-US','4-6','published',$3::uuid,'demo')`,
      [COURSE_ID, FARM_V9_ROLLOUT.courseKey, ADMIN_ID],
    );
    await client.query(
      `INSERT INTO lessons(
         id,course_id,lesson_key,lesson_version,manifest_version,title,locale,age_band,
         manifest_checksum,status,created_by,lesson_type,topic_tags,estimated_duration_sec,
         difficulty_band,cinematic_source_revision
       ) VALUES($1::uuid,$2::uuid,$3,8,$4,'Farm historical v8','en-US','4-6','pending-publish','draft',
         $5::uuid,'lesson',ARRAY['farm'],180,'beginner',1)`,
      [LESSON_ID, COURSE_ID, LESSON_KEY, FARM_V9_ROLLOUT.rendererVersion, ADMIN_ID],
    );
    await seedLessonStepsAndBundle(client);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }

  const authoring = new LessonAuthoringService(pool);
  await authoring.saveTVideoJourney(LESSON_ID, FARM_V7_BOOTSTRAP_JOURNEY, admin, '127.0.0.1');
  await materializeReadyDerivatives(pool, LESSON_ID, 8);
  const preview = await authoring.manifestPreview(LESSON_ID, 'espTft');
  await pool.query(
    `UPDATE asset_bundles SET manifest_checksum=$2 WHERE lesson_id=$1::uuid AND lesson_version=8 AND profile='espTft'`,
    [LESSON_ID, preview.checksum],
  );
  await pool.query(
    `UPDATE lessons SET manifest_checksum=$2,status='published',published_at=NOW() WHERE id=$1::uuid`,
    [LESSON_ID, preview.checksum],
  );
  await materializeFarmTestGeneration(pool, 8);
}

async function seedFamily(client) {
  await client.query(
    `INSERT INTO parent_accounts(id,email,password_hash,name,coppa_verified)
     VALUES($1::uuid,'task4-parent@local.invalid','unused','Task 4 Parent',TRUE)
     ON CONFLICT(id) DO NOTHING`, [PARENT_ID],
  );
  await client.query(
    `INSERT INTO households(id,name,owner_id) VALUES($1::uuid,'Task 4 Household',$2::uuid)
     ON CONFLICT(id) DO NOTHING`, [HOUSEHOLD_ID, PARENT_ID],
  );
  await client.query(
    `INSERT INTO household_memberships(parent_id,household_id,role) VALUES($1::uuid,$2::uuid,'owner')
     ON CONFLICT(parent_id,household_id) DO NOTHING`, [PARENT_ID, HOUSEHOLD_ID],
  );
  await client.query(
    `INSERT INTO child_profiles(id,household_id,display_name,birth_year,age_gate_passed)
     VALUES($1::uuid,$2::uuid,'Mai',2020,TRUE) ON CONFLICT(id) DO NOTHING`, [CHILD_ID, HOUSEHOLD_ID],
  );
  await client.query(
    `INSERT INTO devices(id,serial_number,hardware_revision,mac_address,current_household_id,
       assigned_child_profile_id,state,lifecycle_state,status,display_name)
     VALUES($1::uuid,'TASK4-E2E-0001','lcdwiki-es3c35p','14:c1:9f:d1:a8:49',$2::uuid,$3::uuid,
       'ACTIVE','assigned','active','Task 4 Robot')
     ON CONFLICT(id) DO UPDATE SET current_household_id=EXCLUDED.current_household_id,
       assigned_child_profile_id=EXCLUDED.assigned_child_profile_id,lifecycle_state='assigned',status='active',updated_at=NOW()`,
    [DEVICE_ID, HOUSEHOLD_ID, CHILD_ID],
  );
  await client.query(`DELETE FROM lesson_assignments WHERE device_id=$1::uuid`, [DEVICE_ID]);
}

async function seedVisualSources(client) {
  for (const asset of FARM_V7_BOOTSTRAP_ASSETS) {
    await client.query(
      `INSERT INTO shared_visual_assets(id,asset_key,category,title) VALUES($1::uuid,$2,$3,$4)
       ON CONFLICT(id) DO UPDATE SET asset_key=EXCLUDED.asset_key,category=EXCLUDED.category,title=EXCLUDED.title`,
      [asset.assetId, asset.assetKey, asset.category, asset.title],
    );
    await client.query(
      `INSERT INTO shared_visual_asset_versions(id,asset_id,version,profile,storage_path,sha256,mime_type,
         bytes,width,height,compatibility_metadata,publication_state,published_at)
       VALUES($1::uuid,$2::uuid,1,'espTft',$3,$4,$5,$6,$7,$8,$9::jsonb,'published',NOW())
       ON CONFLICT(id) DO UPDATE SET storage_path=EXCLUDED.storage_path,sha256=EXCLUDED.sha256,
         mime_type=EXCLUDED.mime_type,bytes=EXCLUDED.bytes,width=EXCLUDED.width,height=EXCLUDED.height,
         compatibility_metadata=EXCLUDED.compatibility_metadata,publication_state='published'`,
      [asset.versionId, asset.assetId, asset.storagePath, asset.sha256, asset.mimeType,
        asset.bytes, asset.width, asset.height, JSON.stringify(asset.compatibilityMetadata)],
    );
  }
}

async function seedLessonStepsAndBundle(client) {
  const bundle = await client.query(
    `INSERT INTO asset_bundles(lesson_id,lesson_version,profile) VALUES($1::uuid,8,'espTft') RETURNING id`,
    [LESSON_ID],
  );
  for (const [assetKey, layer, role, checksum, critical, mediaType, bytes, width, height] of BUNDLE_ASSETS) {
    await client.query(
      `INSERT INTO assets(bundle_id,asset_key,layer,role,path,sha256,is_critical,media_type,bytes,width,height)
       VALUES($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
      [bundle.rows[0].id, assetKey, layer, role, `lesson-assets/${checksum}`, checksum,
        critical, mediaType, bytes, width, height],
    );
  }
  for (const [index, step] of FARM_V7_BOOTSTRAP_JOURNEY.steps.entries()) {
    await client.query(
      `INSERT INTO lesson_steps(lesson_id,step_key,step_index,step_type,entrance,robot_state,pose,
         expression,phase,prompt,subject,helper_text,l1_transfer_hint,choices,step_body)
       VALUES($1::uuid,$2,$3,'listen',$4,'listening','listening','listening','listen',$5,$6,$7,$8,NULL,$9::jsonb)`,
      [LESSON_ID, step.stepKey, index, index === 0 ? 'flyIn' : 'none', step.teachingCopy.prompt,
        step.targetWord, step.teachingCopy.explanation, step.pronunciation.vietnameseL1Guidance.join(' '),
        JSON.stringify({
          ...(index === 0
            ? {
                hook: true,
                recall: true,
                interaction: { template: 'safeSpeaking', funPattern: 'copyMyMove', maxAttempts: 3, listenTimeoutSec: 8 },
                motion: { present: 'teach' },
              }
            : {
                terminal: true,
                interaction: { template: 'safeSpeaking', funPattern: 'soundGuess', maxAttempts: 3, listenTimeoutSec: 12 },
              }),
          teachingWord: { text: step.targetWord, displayText: step.targetWord.toLocaleUpperCase('en-US') },
          teachingObject: {
            primaryWord: step.targetWord,
            asset: {
              key: `teachingObject.${step.stepKey}`,
              src: `lesson-assets/${step.stepKey === 'barn'
                ? 'bf3d88d17867f02872e3e6aff31b5d4d0a94977a5efa4214b7e831122938511b'
                : '66d4bdfe70783f389e30d488b1184dd9b48182b7f29406a196a2ec616fedbb7c'}`,
              sha256: step.stepKey === 'barn'
                ? 'bf3d88d17867f02872e3e6aff31b5d4d0a94977a5efa4214b7e831122938511b'
                : '66d4bdfe70783f389e30d488b1184dd9b48182b7f29406a196a2ec616fedbb7c',
            },
          },
          audio: { via: 'tts' },
          timeoutSec: index === 0 ? 8 : 12,
        })],
    );
  }
}

async function materializeReadyDerivatives(pool, lessonId, lessonVersion) {
  const cues = deriveTVideoJourneyCuePlan(FARM_V7_BOOTSTRAP_JOURNEY);
  const rows = await pool.query(
    `SELECT derivative_id,cue_id FROM flattened_cinematic_derivatives
      WHERE lesson_id=$1::uuid AND lesson_version=$2 AND identity_version=2 AND is_current=TRUE`,
    [lessonId, lessonVersion],
  );
  if (rows.rows.length !== EXPECTED.derivatives) throw new Error('expected 19 generated derivative identities');
  if (DEVICE_MEDIA_TYPE !== 'application/vnd.tbot.rgb565-indexed') throw new Error('unexpected device media type');
  const durationByCue = new Map(cues.map((cue) => [cue.cueId, cue.durationMs]));
  for (const row of rows.rows) {
    const durationMs = durationByCue.get(row.cue_id);
    if (!durationMs) throw new Error(`unknown cue ${row.cue_id}`);
    const frames = durationMs / 100;
    const relativeRoot = `lessons/derivatives/${row.derivative_id}`;
    const previewRelative = `${relativeRoot}/${row.cue_id}.mp4`;
    const deviceRelative = `${relativeRoot}/${row.cue_id}.trgb`;
    const previewPath = join(MEDIA_ROOT, previewRelative);
    const devicePath = join(MEDIA_ROOT, deviceRelative);
    await mkdir(dirname(previewPath), { recursive: true });
    await replaceWithCopy(join(MEDIA_ROOT, 'templates', `${durationMs}.mp4`), previewPath);
    const trgbTemplate = await ensureTbotTemplate(durationMs, frames);
    await replaceWithCopy(trgbTemplate, devicePath);
    const parsed = await validateTbotRgb565File(devicePath, frames);
    const [previewInfo, deviceInfo, previewSha, deviceSha] = await Promise.all([
      stat(previewPath), stat(devicePath), fileSha256(previewPath), fileSha256(devicePath),
    ]);
    if (parsed.metadata.durationMs !== durationMs || parsed.metadata.fileBytes !== deviceInfo.size) {
      throw new Error(`TRGB metadata mismatch for ${row.cue_id}`);
    }
    await pool.query(
      `UPDATE flattened_cinematic_derivatives SET status='ready',attempt_count=1,next_attempt_at=NOW(),
         output_path=$2,output_url=$3,output_sha256=$4,output_bytes=$5,
         output_metadata=$6::jsonb,device_output_path=$7,device_output_url=$8,
         device_output_sha256=$9,device_output_bytes=$10,device_output_metadata=$11::jsonb,
         completed_at=NOW(),updated_at=NOW(),preview_encoder='tvideo-h264-browser-preview-v1'
       WHERE derivative_id=$1`,
      [row.derivative_id,
        previewRelative,
        `${MEDIA_ORIGIN}/${previewRelative}`, previewSha, previewInfo.size,
        JSON.stringify({ codec: 'h264', width: 480, height: 320, fps: 10, durationMs, frameCount: frames, hasAudio: false }),
        deviceRelative,
        `${MEDIA_ORIGIN}/${deviceRelative}`, deviceSha, deviceInfo.size,
        JSON.stringify({ codec: 'rgb565le', containerVersion: 1, width: 480, height: 320,
          storedWidth: 320, storedHeight: 480, orientation: 'panelNativeClockwise', fps: 10,
          durationMs, frameCount: frames, frameBytes: 307200, hasAudio: false })],
    );
  }
}

async function ensureTbotTemplate(durationMs, frameCount) {
  const template = join(MEDIA_ROOT, 'templates', `${durationMs}.trgb`);
  await mkdir(dirname(template), { recursive: true });
  try {
    await validateTbotRgb565File(template, frameCount);
    return template;
  } catch {
    // Rebuild incomplete or stale runtime output deterministically.
  }
  const raw = `${template}.raw`;
  const file = await open(raw, 'w');
  const frame = Buffer.alloc(TBOT_RGB565_FRAME_BYTES);
  for (let pixel = 0; pixel < frame.length; pixel += 2) {
    const value = ((pixel / 2 + durationMs) & 0xffff);
    frame.writeUInt16LE(value, pixel);
  }
  try {
    for (let index = 0; index < frameCount; index += 1) await file.write(frame);
    await file.sync();
  } finally {
    await file.close();
  }
  await writeTbotRgb565File(raw, template, frameCount);
  await rm(raw, { force: true });
  await validateTbotRgb565File(template, frameCount);
  return template;
}

async function fileSha256(path) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) hash.update(chunk);
  return hash.digest('hex');
}

async function verifyServedBytes(url, expectedSha, expectedBytes) {
  await new Promise((resolvePromise, reject) => {
    const hash = createHash('sha256');
    let bytes = 0;
    const reachable = new URL(url);
    if (reachable.hostname === 'task4-media.localhost') reachable.port = '8443';
    const request = httpsGet(reachable, {
      ca: readFileSync('/task4-tls/cert.pem'),
      servername: 'task4-media.localhost',
    }, (response) => {
      if (response.statusCode !== 200) {
        response.resume();
        reject(new Error(`fixture media URL returned ${response.statusCode}`));
        return;
      }
      response.on('data', (chunk) => { bytes += chunk.length; hash.update(chunk); });
      response.on('end', () => {
        if (bytes !== expectedBytes || hash.digest('hex') !== expectedSha) {
          reject(new Error('fixture media URL bytes differ from READY metadata'));
          return;
        }
        resolvePromise();
      });
    });
    request.on('error', reject);
  });
}

async function authenticatedApi() {
  const root = 'http://127.0.0.1:3000/v1/admin';
  const login = await fetch(`${root}/auth/login`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: 'lesson-author-e2e@local.invalid', password: 'TbotAuthorE2E!2026' }),
  });
  if (!login.ok) throw new Error(`fixture admin login failed: ${login.status}`);
  const loginBody = await login.json();
  const token = loginBody.session_token;
  if (!token) throw new Error('fixture admin login returned no token');
  const request = async (method, path, body) => {
    const response = await fetch(`${root}${path}`, {
      method, headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const payload = await response.json();
    if (!response.ok) throw Object.assign(new Error(`fixture API ${method} ${path} failed`), { status: response.status, code: payload.code });
    return payload.data;
  };
  return {
    getLesson: (id) => request('GET', `/lessons/${id}`),
    listCourseLessons: (id) => request('GET', `/courses/${id}/lessons`),
    getTVideoJourney: (id) => request('GET', `/lessons/authoring/${id}/tvideo-journey`),
    saveTVideoJourney: async (id, journey) => {
      const result = await request('PUT', `/lessons/authoring/${id}/tvideo-journey`, journey);
      const pool = new Pool({ connectionString: process.env.DATABASE_URL });
      try { await materializeReadyDerivatives(pool, id, 9); } finally { await pool.end(); }
      return request('GET', `/lessons/authoring/${id}/tvideo-journey`);
    },
    getManifestPreview: (id, profile) => request('GET', `/lessons/${id}/manifest-preview?profile=${profile}`),
    createNextVersion: (id) => request('POST', `/lessons/${id}/new-version`, {}),
    publishLesson: (id) => request('POST', `/lessons/${id}/publish`, {}),
    rebuildLessonAssets: () => materializeFarmTestGeneration(undefined, 9),
    getLatestLessonAssets: () => fetch('http://127.0.0.1:3000/v1/public/lesson-assets/latest').then((r) => r.json()).then((r) => r.data),
    listAssignments: (deviceId, limit = 20) => request('GET', `/lesson-monitoring/assignments?deviceId=${deviceId}&limit=${limit}`),
    cancelAssignment: (id, body) => request('POST', `/lesson-assignment-operations/${id}/cancel`, body),
    assignLesson: (body) => request('POST', '/lesson-assignments', body),
  };
}

async function materializeFarmTestGeneration(existingPool, expectedVersion) {
  // Test-only atomic repository lifecycle. This validates generation persistence,
  // not the public/global /lesson-assets/rebuild operation.
  const pool = existingPool ?? new Pool({ connectionString: process.env.DATABASE_URL });
  const ownsPool = !existingPool;
  const repository = new LessonAssetGenerationRepository(pool);
  let rebuild;
  let client = await pool.connect();
  try {
    await client.query('BEGIN');
    rebuild = await repository.requestAndLease(client, {
      reason: 'lesson.assignment.bootstrap',
      sourceLessonId: LESSON_ID,
    });
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }

  client = await pool.connect();
  try {
    await client.query('BEGIN');
    const built = buildCanonicalGenerationIndex(await repository.loadLatestPublishedPacks(client));
    const farm = built.index.find((pack) => pack.lessonId === LESSON_KEY && pack.lessonVersion === expectedVersion);
    if (!farm || farm.cacheKey !== `${LESSON_KEY}/v${expectedVersion}-${farm.manifestChecksum}`) {
      throw new Error(`test fixture generation lacks the exact Farm v${expectedVersion} pack`);
    }
    const generation = await repository.commitGeneration(client, rebuild, built);
    await client.query('COMMIT');
    return {
      generation: generation.generation,
      indexChecksum: generation.indexChecksum,
      curriculumLessonCount: generation.curriculumLessonCount,
      packCount: generation.packCount,
      testFixtureOnly: true,
    };
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
    if (ownsPool) await pool.end();
  }
}

async function verifyFixture(pool, requireV9) {
  const counts = await pool.query(
    `SELECT
      (SELECT count(*)::int FROM shared_visual_assets WHERE id=ANY($1::uuid[])) AS shared_visual_assets,
      (SELECT count(*)::int FROM lesson_steps WHERE lesson_id=$2::uuid) AS lesson_steps,
      (SELECT count(*)::int FROM assets a JOIN asset_bundles b ON b.id=a.bundle_id WHERE b.lesson_id=$2::uuid AND b.lesson_version=8) AS bundle_assets,
      (SELECT count(*)::int FROM flattened_cinematic_derivatives WHERE lesson_id=$2::uuid AND lesson_version=8 AND is_current=TRUE AND status='ready') AS derivatives`,
    [FARM_V7_BOOTSTRAP_ASSETS.map((asset) => asset.assetId), LESSON_ID],
  );
  const actual = counts.rows[0];
  if (actual.shared_visual_assets !== EXPECTED.sharedVisualAssets || actual.lesson_steps !== EXPECTED.lessonSteps
      || actual.bundle_assets !== EXPECTED.bundleAssets || actual.derivatives !== EXPECTED.derivatives) {
    throw new Error(`fixture count mismatch: ${JSON.stringify(actual)}`);
  }
  const authoring = new LessonAuthoringService(pool);
  const preview = await authoring.manifestPreview(LESSON_ID, 'espTft');
  const lesson = await pool.query(`SELECT manifest_checksum FROM lessons WHERE id=$1::uuid`, [LESSON_ID]);
  if (preview.checksum !== lesson.rows[0]?.manifest_checksum) throw new Error('stored v8 checksum differs from real manifest preview');
  if (requireV9) {
    const v9 = await pool.query(`SELECT count(*)::int AS count FROM lessons WHERE lesson_key=$1 AND lesson_version=9 AND status='published'`, [LESSON_KEY]);
    if (v9.rows[0].count !== 1) throw new Error('valid published v9 fixture missing');
  }
  const derivatives = await pool.query(
    `SELECT cue_id,output_path,output_url,output_sha256,output_bytes,device_output_path,
            device_output_url,device_output_sha256,device_output_bytes,device_output_metadata
       FROM flattened_cinematic_derivatives
      WHERE lesson_id=$1::uuid AND is_current=TRUE AND status='ready'`,
    [LESSON_ID],
  );
  for (const row of derivatives.rows) {
    const previewPath = join(MEDIA_ROOT, row.output_path);
    const devicePath = join(MEDIA_ROOT, row.device_output_path);
    const [previewInfo, deviceInfo, previewSha, deviceSha] = await Promise.all([
      stat(previewPath), stat(devicePath), fileSha256(previewPath), fileSha256(devicePath),
    ]);
    if (previewInfo.size !== Number(row.output_bytes) || previewSha !== row.output_sha256
        || deviceInfo.size !== Number(row.device_output_bytes) || deviceSha !== row.device_output_sha256) {
      throw new Error(`served derivative bytes differ from READY metadata for ${row.cue_id}`);
    }
    await validateTbotRgb565File(devicePath, Number(row.device_output_metadata.frameCount));
  }
  if (derivatives.rows[0]) {
    await verifyServedBytes(
      derivatives.rows[0].output_url,
      derivatives.rows[0].output_sha256,
      Number(derivatives.rows[0].output_bytes),
    );
    await verifyServedBytes(
      derivatives.rows[0].device_output_url,
      derivatives.rows[0].device_output_sha256,
      Number(derivatives.rows[0].device_output_bytes),
    );
  }
  return { ok: true, lessonId: LESSON_ID, checksum: preview.checksum, counts: actual };
}

async function verifyAssignmentState(pool, phase) {
  const rows = await pool.query(
    `SELECT l.lesson_version,a.state,a.assignment_version
       FROM lesson_assignments a JOIN lessons l ON l.id=a.lesson_id
      WHERE a.device_id=$1::uuid AND l.lesson_key=$2
      ORDER BY a.created_at DESC`,
    [DEVICE_ID, LESSON_KEY],
  );
  const v9 = rows.rows.find((row) => row.lesson_version === 9);
  if (phase === 'new') {
    if (!v9 || v9.state !== 'ASSIGNED') throw new Error('NEW phase must end with v9 ASSIGNED');
    return;
  }
  const v8 = rows.rows.find((row) => row.lesson_version === 8);
  if (!v9 || v9.state !== 'CANCELLED' || !v8 || v8.state !== 'ASSIGNED') {
    throw new Error('ROLLBACK phase must end with v9 CANCELLED and v8 ASSIGNED');
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
