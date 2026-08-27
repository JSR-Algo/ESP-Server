#!/usr/bin/env node

import { createRequire } from 'node:module';

const DEVICE_MAC = '14:c1:9f:d1:ac:20';
const IDEMPOTENCY_KEY = 'local-course-mode-v5-ac20-v1';
const ACTIVE_ASSIGNMENT_STATES = new Set(['ASSIGNED', 'PRELOADING', 'READY', 'RUNNING', 'PAUSED']);

export const COURSE_MODE_V5_LOCAL_IDS = Object.freeze({
  operator: '75000000-0000-4000-8000-000000000001',
  lesson: '75000000-0000-4000-8000-000000000003',
  household: '76000000-0000-4000-8000-000000000001',
  child: '76000000-0000-4000-8000-000000000002',
  device: '76000000-0000-4000-8000-000000000003',
  assignment: '76000000-0000-4000-8000-000000000004',
});

export async function executeCourseModeV5LocalProvisioning(client) {
  await client.query('BEGIN ISOLATION LEVEL SERIALIZABLE');
  try {
    await client.query('SELECT pg_advisory_xact_lock(760000000004)');
    const lesson = await client.query(
      `SELECT id FROM lessons
       WHERE id=$1::uuid AND lesson_key='course-mode-v5-farm-candidate'
         AND lesson_version=2 AND manifest_version='teebot-lesson-renderer.v5'
         AND status='published'`,
      [COURSE_MODE_V5_LOCAL_IDS.lesson],
    );
    if (lesson.rowCount !== 1) {
      throw new Error('canonical Course Mode v5 lesson is not materialized');
    }

    await client.query(
      `INSERT INTO households(id,name,owner_id) VALUES($1::uuid,$2,$3::uuid)
       ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name,owner_id=EXCLUDED.owner_id,updated_at=NOW()`,
      [COURSE_MODE_V5_LOCAL_IDS.household, 'Local Course Mode v5 AC:20', COURSE_MODE_V5_LOCAL_IDS.operator],
    );
    await client.query(
      `INSERT INTO household_memberships(parent_id,household_id,role)
       VALUES($1::uuid,$2::uuid,'owner')
       ON CONFLICT (parent_id,household_id) DO UPDATE SET role='owner'`,
      [COURSE_MODE_V5_LOCAL_IDS.operator, COURSE_MODE_V5_LOCAL_IDS.household],
    );
    await client.query(
      `INSERT INTO child_profiles(id,household_id,display_name,birth_year,age_gate_passed)
       VALUES($1::uuid,$2::uuid,$3,2020,TRUE)
       ON CONFLICT (id) DO UPDATE SET household_id=EXCLUDED.household_id,
         display_name=EXCLUDED.display_name,birth_year=EXCLUDED.birth_year,
         age_gate_passed=TRUE,updated_at=NOW()`,
      [COURSE_MODE_V5_LOCAL_IDS.child, COURSE_MODE_V5_LOCAL_IDS.household, 'Local Course Mode Learner'],
    );

    const existing = await client.query(
      'SELECT id FROM devices WHERE lower(mac_address)=lower($1) FOR UPDATE',
      [DEVICE_MAC],
    );
    const deviceId = existing.rows[0]?.id ?? COURSE_MODE_V5_LOCAL_IDS.device;
    if (existing.rowCount === 0) {
      await client.query(
        `INSERT INTO devices(id,serial_number,current_household_id,assigned_child_profile_id,
           state,firmware_version,hardware_revision,claimed_by,lifecycle_state,status,mac_address,display_name)
         VALUES($1::uuid,$2,$3::uuid,$4::uuid,'ACTIVE','local-course-mode-v5',
           'esp32-tft-local',$5::uuid,'assigned','active',$6,$7)`,
        [deviceId, 'LOCAL-COURSE-MODE-V5-AC20', COURSE_MODE_V5_LOCAL_IDS.household,
          COURSE_MODE_V5_LOCAL_IDS.child, COURSE_MODE_V5_LOCAL_IDS.operator, DEVICE_MAC,
          'Local Course Mode v5 AC:20'],
      );
    } else {
      await client.query(
        `UPDATE devices SET current_household_id=$2::uuid,assigned_child_profile_id=$3::uuid,
           state='ACTIVE',claimed_by=$4::uuid,lifecycle_state='assigned',status='active',
           display_name=$5,updated_at=NOW()
         WHERE id=$1::uuid AND lower(mac_address)=lower($6)`,
        [deviceId, COURSE_MODE_V5_LOCAL_IDS.household, COURSE_MODE_V5_LOCAL_IDS.child,
          COURSE_MODE_V5_LOCAL_IDS.operator, 'Local Course Mode v5 AC:20', DEVICE_MAC],
      );
    }

    const assignment = await client.query(
      `SELECT id,device_id,child_id,household_id,lesson_id,lesson_version,profile,state,
              assignment_version,idempotency_key
         FROM lesson_assignments WHERE id=$1::uuid FOR UPDATE`,
      [COURSE_MODE_V5_LOCAL_IDS.assignment],
    );
    const current = assignment.rows[0];
    const exactActiveAssignment = current
      && current.device_id === deviceId
      && current.child_id === COURSE_MODE_V5_LOCAL_IDS.child
      && current.household_id === COURSE_MODE_V5_LOCAL_IDS.household
      && current.lesson_id === COURSE_MODE_V5_LOCAL_IDS.lesson
      && Number(current.lesson_version) === 2
      && current.profile === 'espTft'
      && ACTIVE_ASSIGNMENT_STATES.has(current.state)
      && current.idempotency_key === IDEMPOTENCY_KEY;

    if (!exactActiveAssignment) {
      await client.query(
        `UPDATE lesson_assignments SET state='CANCELLED',updated_at=NOW()
         WHERE device_id=$1::uuid AND id<>$2::uuid
           AND state IN ('ASSIGNED','PRELOADING','READY','RUNNING','PAUSED')`,
        [deviceId, COURSE_MODE_V5_LOCAL_IDS.assignment],
      );
      await client.query(
        `DELETE FROM lesson_assignments
         WHERE device_id=$1::uuid AND idempotency_key=$2 AND id<>$3::uuid`,
        [deviceId, IDEMPOTENCY_KEY, COURSE_MODE_V5_LOCAL_IDS.assignment],
      );
      await client.query(
        `INSERT INTO lesson_assignments
           (id,device_id,child_id,household_id,lesson_id,lesson_version,profile,state,assignment_version,idempotency_key)
         VALUES($1::uuid,$2::uuid,$3::uuid,$4::uuid,$5::uuid,2,'espTft','ASSIGNED',1,$6)
         ON CONFLICT (id) DO UPDATE SET device_id=EXCLUDED.device_id,child_id=EXCLUDED.child_id,
           household_id=EXCLUDED.household_id,lesson_id=EXCLUDED.lesson_id,lesson_version=2,
           profile='espTft',state='ASSIGNED',assignment_version=lesson_assignments.assignment_version+1,
           idempotency_key=EXCLUDED.idempotency_key,updated_at=NOW()`,
        [COURSE_MODE_V5_LOCAL_IDS.assignment, deviceId, COURSE_MODE_V5_LOCAL_IDS.child,
          COURSE_MODE_V5_LOCAL_IDS.household, COURSE_MODE_V5_LOCAL_IDS.lesson, IDEMPOTENCY_KEY],
      );
    }
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  }
}

function assertLocalDatabaseUrl(databaseUrl) {
  if (databaseUrl !== 'postgresql://tbot:tbot@postgres:5432/tbot') {
    throw new Error('isolated physical-TFT PostgreSQL DATABASE_URL is required');
  }
}

async function main() {
  const databaseUrl = process.env.DATABASE_URL;
  assertLocalDatabaseUrl(databaseUrl);
  const require = createRequire('/app/package.json');
  const { Pool } = require('pg');
  const pool = new Pool({ connectionString: databaseUrl, max: 1 });
  const client = await pool.connect();
  try {
    await executeCourseModeV5LocalProvisioning(client);
  } finally {
    client.release();
    await pool.end();
  }
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.stack : String(error)}\n`);
    process.exitCode = 1;
  });
}
