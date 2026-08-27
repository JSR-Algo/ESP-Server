import assert from 'node:assert/strict';
import test from 'node:test';

import {
  COURSE_MODE_V5_LOCAL_IDS,
  executeCourseModeV5LocalProvisioning,
} from './provision-course-mode-v5-assignment.mjs';

test('provisions only the local AC:20 assignment for the canonical renderer-v5 lesson', async () => {
  const calls = [];
  const client = {
    query: async (sql, params = []) => {
      calls.push([sql, params]);
      if (/SELECT id FROM lessons/.test(sql)) {
        return { rows: [{ id: COURSE_MODE_V5_LOCAL_IDS.lesson }], rowCount: 1 };
      }
      if (/SELECT id FROM devices/.test(sql)) return { rows: [], rowCount: 0 };
      return { rows: [], rowCount: 1 };
    },
  };

  await executeCourseModeV5LocalProvisioning(client);

  assert.equal(calls[0][0], 'BEGIN ISOLATION LEVEL SERIALIZABLE');
  assert.equal(calls.at(-1)[0], 'COMMIT');
  const sql = calls.map(([statement]) => statement).join('\n');
  const params = calls.flatMap(([, values]) => values);
  assert.match(sql, /teebot-lesson-renderer\.v5/);
  assert.match(sql, /course-mode-v5-farm-candidate/);
  assert.match(sql, /lower\(mac_address\)=lower\(\$1\)/);
  assert.match(sql, /INSERT INTO devices/);
  assert.doesNotMatch(sql, /renderer\.v4|admin-w1/);
  assert.ok(params.includes('14:c1:9f:d1:ac:20'));
  assert.ok(params.includes(COURSE_MODE_V5_LOCAL_IDS.lesson));
  assert.ok(params.includes(COURSE_MODE_V5_LOCAL_IDS.assignment));
});

test('repairs a stale AC:20 row without creating a second device identity', async () => {
  const staleDeviceId = '77000000-0000-4000-8000-000000000001';
  const calls = [];
  const client = {
    query: async (sql, params = []) => {
      calls.push([sql, params]);
      if (/SELECT id FROM lessons/.test(sql)) {
        return { rows: [{ id: COURSE_MODE_V5_LOCAL_IDS.lesson }], rowCount: 1 };
      }
      if (/SELECT id FROM devices/.test(sql)) {
        return { rows: [{ id: staleDeviceId }], rowCount: 1 };
      }
      return { rows: [], rowCount: 1 };
    },
  };

  await executeCourseModeV5LocalProvisioning(client);

  const sql = calls.map(([statement]) => statement).join('\n');
  assert.doesNotMatch(sql, /INSERT INTO devices/);
  assert.match(sql, /UPDATE devices SET current_household_id/);
  assert.match(sql, /UPDATE lesson_assignments SET state='CANCELLED'/);
  assert.ok(calls.flatMap(([, values]) => values).includes(staleDeviceId));
  assert.equal(calls.at(-1)[0], 'COMMIT');
});

test('rolls back and fails closed when canonical v5 materialization is absent', async () => {
  const calls = [];
  const client = {
    query: async (sql, params = []) => {
      calls.push([sql, params]);
      if (/SELECT id FROM lessons/.test(sql)) return { rows: [], rowCount: 0 };
      return { rows: [], rowCount: 1 };
    },
  };

  await assert.rejects(
    executeCourseModeV5LocalProvisioning(client),
    /canonical Course Mode v5 lesson is not materialized/,
  );
  assert.equal(calls.at(-1)[0], 'ROLLBACK');
});

test('preserves an exact READY canonical assignment without changing state or version', async () => {
  const calls = [];
  const client = {
    query: async (sql, params = []) => {
      calls.push([sql, params]);
      if (/SELECT id FROM lessons/.test(sql)) {
        return { rows: [{ id: COURSE_MODE_V5_LOCAL_IDS.lesson }], rowCount: 1 };
      }
      if (/SELECT id FROM devices/.test(sql)) {
        return { rows: [{ id: COURSE_MODE_V5_LOCAL_IDS.device }], rowCount: 1 };
      }
      if (/FROM lesson_assignments/.test(sql) && /FOR UPDATE/.test(sql)) {
        return {
          rows: [{
            id: COURSE_MODE_V5_LOCAL_IDS.assignment,
            device_id: COURSE_MODE_V5_LOCAL_IDS.device,
            child_id: COURSE_MODE_V5_LOCAL_IDS.child,
            household_id: COURSE_MODE_V5_LOCAL_IDS.household,
            lesson_id: COURSE_MODE_V5_LOCAL_IDS.lesson,
            lesson_version: 2,
            profile: 'espTft',
            state: 'READY',
            assignment_version: 7,
            idempotency_key: 'local-course-mode-v5-ac20-v1',
          }],
          rowCount: 1,
        };
      }
      return { rows: [], rowCount: 1 };
    },
  };

  await executeCourseModeV5LocalProvisioning(client);

  const assignmentWrites = calls.filter(([sql]) => (
    /UPDATE lesson_assignments/.test(sql)
    || /DELETE FROM lesson_assignments/.test(sql)
    || /INSERT INTO lesson_assignments/.test(sql)
  ));
  assert.deepEqual(assignmentWrites, []);
  assert.equal(calls.at(-1)[0], 'COMMIT');
});
