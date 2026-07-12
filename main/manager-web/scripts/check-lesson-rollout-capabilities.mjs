import assert from 'node:assert/strict';
import {
  advanceLessonRolloutSessionGeneration,
  createLessonRolloutCapabilitiesLoader,
  getLessonRolloutSessionGeneration,
  isLessonCapabilityNavigationCurrent,
  loadLessonRolloutCapabilitiesWith,
  normalizeLessonRolloutCapabilities,
} from '../src/utils/lessonRolloutCapabilitiesCore.mjs';

const disabled = { sharedVisualAuthoring: false, exactEspTftPreview: false };
const enabled = { sharedVisualAuthoring: true, exactEspTftPreview: true };

const initialGeneration = getLessonRolloutSessionGeneration();
advanceLessonRolloutSessionGeneration();
assert.equal(getLessonRolloutSessionGeneration(), initialGeneration + 1, 'auth reset must advance the session generation');

assert.deepEqual(normalizeLessonRolloutCapabilities({ sharedVisualAuthoring: true, exactEspTftPreview: 'true' }), {
  sharedVisualAuthoring: true,
  exactEspTftPreview: false,
});
assert.deepEqual(await loadLessonRolloutCapabilitiesWith({ getRolloutCapabilities(ok) { ok(enabled); } }), enabled);
for (const apiClient of [
  { getRolloutCapabilities(ok) { ok(null); } },
  { getRolloutCapabilities(ok, fail) { fail('network'); } },
  { getRolloutCapabilities() { throw new Error('broken client'); } },
]) assert.deepEqual(await loadLessonRolloutCapabilitiesWith(apiClient), disabled);
assert.deepEqual(await loadLessonRolloutCapabilitiesWith({ getRolloutCapabilities() {} }, 0), disabled);

let sessionKey = 'session-a';
let calls = 0;
let pending;
const loader = createLessonRolloutCapabilitiesLoader({
  getSessionKey: () => sessionKey,
  load: () => {
    calls += 1;
    return new Promise((resolve) => { pending = resolve; });
  },
});

const concurrentA = loader.load();
const concurrentB = loader.load();
await Promise.resolve();
assert.equal(calls, 1, 'concurrent callers must share one request');
pending(enabled);
assert.deepEqual(await concurrentA, enabled);
assert.deepEqual(await concurrentB, enabled);
assert.deepEqual(await loader.load(), enabled);
assert.equal(calls, 1, 'successful capabilities must be cached for the active session');

loader.reset();
const afterReset = loader.load();
await Promise.resolve();
assert.equal(calls, 2, 'reset must invalidate the cached result');
pending(enabled);
assert.deepEqual(await afterReset, enabled);

loader.reset();
const staleRequest = loader.load();
await Promise.resolve();
sessionKey = 'session-b';
pending(enabled);
assert.deepEqual(await staleRequest, disabled, 'a response from an old session must fail closed');
const currentRequest = loader.load();
await Promise.resolve();
assert.equal(calls, 4, 'a stale response must not populate the next session cache');
pending(enabled);
assert.deepEqual(await currentRequest, enabled);

loader.reset();
const requestBeforeLogout = loader.load();
await Promise.resolve();
loader.reset();
pending(enabled);
assert.deepEqual(await requestBeforeLogout, disabled, 'logout/reset must invalidate an in-flight response');
assert.deepEqual(loader.peek(), disabled, 'logout/reset must synchronously expose disabled capabilities');

let errorCalls = 0;
const errorLoader = createLessonRolloutCapabilitiesLoader({
  getSessionKey: () => 'session-c',
  load: async () => {
    errorCalls += 1;
    throw new Error('offline');
  },
});
assert.deepEqual(await errorLoader.load(), disabled);
assert.deepEqual(errorLoader.peek(), disabled);
assert.deepEqual(await errorLoader.load(), disabled);
assert.equal(errorCalls, 2, 'errors must reset state rather than cache a prior/failed result');

sessionKey = 'old-session';
const raceRequests = [];
const rejectionRaceLoader = createLessonRolloutCapabilitiesLoader({
  getSessionKey: () => sessionKey,
  load: () => new Promise((resolve, reject) => raceRequests.push({ resolve, reject })),
});
const oldSessionRequest = rejectionRaceLoader.load();
await Promise.resolve();
sessionKey = 'new-session';
const newSessionRequest = rejectionRaceLoader.load();
await Promise.resolve();
raceRequests[1].resolve(enabled);
assert.deepEqual(await newSessionRequest, enabled);
raceRequests[0].reject(new Error('old session failed late'));
assert.deepEqual(await oldSessionRequest, disabled);
assert.deepEqual(rejectionRaceLoader.peek(), enabled, 'an old-session error must not clear the current session cache');

assert.equal(isLessonCapabilityNavigationCurrent('token-a', 'token-a', true), true);
assert.equal(isLessonCapabilityNavigationCurrent('token-a', 'token-b', true), false);
assert.equal(isLessonCapabilityNavigationCurrent('token-a', '', true), false);
assert.equal(isLessonCapabilityNavigationCurrent('token-a', 'token-a', false), false);

console.log('lesson rollout capabilities cache, dedup, reset, and session-race behavior PASS');
