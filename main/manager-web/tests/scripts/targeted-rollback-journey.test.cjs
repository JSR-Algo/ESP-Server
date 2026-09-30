const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { expect } = require('@playwright/test');

// Execute the actual journey boundary with a service double; this is not device evidence.
async function rollbackBoundary(overrides = {}, responses = []) {
  const filename = path.resolve(__dirname, '../../e2e/lesson-studio/course-mode-real-journey.spec.js');
  const sourceCode = fs.readFileSync(filename, 'utf8');
  const cancel = "await adminApi(page, 'POST', `/lesson-assignment-operations/${active.assignmentId}/cancel`";
  const start = sourceCode.indexOf('\n', sourceCode.indexOf(cancel));
  const end = sourceCode.indexOf('    const history = await readAssignments();', start);
  assert.ok(start > 0 && end > start);
  const ownedAssignments = [];
  const ownedRetainedRequests = [];
  const calls = [];
  const result = { deviceId: 'unit-device', mode: 'retained', outcome: 'admitted',
    retainedRequestId: 'unit-retained-request', retainedRequestState: 'CONSUMED',
    storeState: 'bound', assignmentId: 'unit-assignment', assignmentState: 'ASSIGNED',
    manifestChecksum: 'a'.repeat(64), error: null, ...overrides };
  const journal = { evidence: { packReadiness: [] } };
  const sandbox = { expect, require, page: { waitForTimeout: async () => {
    if (!responses.length) throw new Error('test preparation did not reach bound');
  } }, source: { id: 'unit-source', lesson_version: 6,
    manifest_checksum: 'a'.repeat(64) }, deviceId: 'unit-device', childId: 'unit-child',
    lessonKey: 'w01-greetings-politeness', ownedAssignments, ownedRetainedRequests,
    journal,
    waitForPublishedPack: async () => { throw new Error('old version is absent from latest-only generation'); },
    assign: async () => { throw new Error('ordinary assignment cannot admit the retained version'); },
    adminApi: async (_page, method, route, body) => {
      calls.push({ method, route, body });
      assert.equal(route, '/lesson-targeted-rollbacks');
      assert.equal(method, 'POST');
      assert.deepEqual(JSON.parse(JSON.stringify(body.deviceIds)), ['unit-device']);
      assert.equal(body.childId, 'unit-child');
      assert.equal(body.lessonId, 'w01-greetings-politeness');
      assert.equal(body.lessonVersion, 6);
      assert.equal(body.profile, 'espTft');
      assert.match(body.idempotencyKey, /^[0-9a-f-]{36}$/);
      return { lessonId: body.lessonId, lessonVersion: 6, profile: 'espTft', results: [{ ...result, ...(responses.shift() || {}) }] };
    } };
  try {
    const rollback = await vm.runInNewContext(`(async () => {${sourceCode.slice(start, end)}; return rollback;})()`, sandbox, { filename });
    return { rollback, calls, ownedAssignments, journal };
  } catch (error) {
    error.ownedAssignments = ownedAssignments;
    error.ownedRetainedRequests = ownedRetainedRequests;
    throw error;
  }
}

test('old publication rollback uses targeted retention instead of the latest-only catalog', async () => {
  const { rollback, calls, ownedAssignments } = await rollbackBoundary();
  assert.equal(calls.length, 1);
  assert.equal(rollback.assignmentId, 'unit-assignment');
  assert.equal(ownedAssignments.length, 1);
});

test('preparing and bind-pending replay the exact batch until the real bound result', async () => {
  const { calls, ownedAssignments, journal } = await rollbackBoundary({}, [
    { outcome: 'preparing', retainedRequestState: 'PREPARING', storeState: 'pending', assignmentId: null },
    { outcome: 'preparing', retainedRequestState: 'CONSUMED', storeState: 'pending' },
    {},
  ]);
  assert.equal(calls.length, 3);
  assert.equal(new Set(calls.map(call => call.body.idempotencyKey)).size, 1);
  assert.equal(ownedAssignments.length, 1);
  assert.equal(journal.evidence.targetedRollbackInput.idempotencyKey, calls[0].body.idempotencyKey);
});

test('failed unconsumed preparation remains owned for cancellation even without an assignment', async () => {
  await assert.rejects(rollbackBoundary({ outcome: 'preparing', retainedRequestState: 'PREPARING',
    storeState: 'failed', assignmentId: null, assignmentState: null, manifestChecksum: null }), error => {
    assert.equal(error.ownedAssignments.length, 0);
    assert.deepEqual(error.ownedRetainedRequests, ['unit-retained-request']);
    return true;
  });
});

for (const [name, overrides] of [
  ['unbound device selection', { storeState: 'pending' }],
  ['preparation only', { outcome: 'preparing', retainedRequestState: 'PREPARING' }],
  ['wrong device', { deviceId: 'other-device' }],
  ['wrong manifest', { manifestChecksum: 'b'.repeat(64) }],
]) test(`rollback refuses ${name} and retains its created assignment for cleanup`, async () => {
  await assert.rejects(rollbackBoundary(overrides), error => {
    assert.equal(error.ownedAssignments.length, 1, 'partial admission still requires cleanup');
    return true;
  });
});

test('uncertain HTTP outcome replays its saved batch before cleaning up owned resources', async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../../e2e/lesson-studio/course-mode-real-journey.spec.js'), 'utf8');
  const start = source.indexOf('  } finally {') + '  } finally {'.length;
  const end = source.indexOf('    for (const assignment of ownedAssignments)', start);
  const input = { deviceIds: ['unit-device'], idempotencyKey: 'saved-batch-key' };
  const ownedAssignments = [], ownedRetainedRequests = [], calls = [];
  const journal = { evidence: { targetedRollbackInput: input, cleanup: [] } };
  await vm.runInNewContext(`(async () => {${source.slice(start, end)}})()`, {
    page: {}, expect, journal, ownedAssignments, ownedRetainedRequests,
    assignmentFixture: { deviceId: 'unit-device' },
    adminApi: async (_page, method, route, body) => {
      if (method === 'GET') return { deviceId: 'unit-device', assignmentId: 'recovered-assignment' };
      calls.push({ method, route, body });
      return { results: [{ deviceId: 'unit-device', retainedRequestId: 'recovered-request', assignmentId: 'recovered-assignment' }] };
    },
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body, input);
  assert.equal(calls[0].route, '/lesson-targeted-rollbacks');
  assert.deepEqual(ownedRetainedRequests, ['recovered-request']);
  assert.equal(ownedAssignments[0].assignmentId, 'recovered-assignment');
});

test('cleanup discovers an assignment consumed after the last preparing response', async () => {
  const source = fs.readFileSync(path.resolve(__dirname, '../../e2e/lesson-studio/course-mode-real-journey.spec.js'), 'utf8');
  const start = source.indexOf('  } finally {') + '  } finally {'.length;
  const end = source.indexOf('    for (const assignment of ownedAssignments)', start);
  const ownedAssignments = [], ownedRetainedRequests = ['known-preparation'];
  const journal = { evidence: { cleanup: [] } };
  await vm.runInNewContext(`(async () => {${source.slice(start, end)}})()`, {
    page: {}, expect, journal, ownedAssignments, ownedRetainedRequests,
    assignmentFixture: { deviceId: 'unit-device' },
    adminApi: async (_page, method, route) => {
      assert.equal(method, 'GET');
      assert.equal(route, '/lesson-retained-requests/known-preparation');
      return { deviceId: 'unit-device', state: 'CONSUMED', assignmentId: 'late-assignment' };
    },
  });
  assert.equal(ownedAssignments.length, 1);
  assert.equal(ownedAssignments[0].assignmentId, 'late-assignment');
});
