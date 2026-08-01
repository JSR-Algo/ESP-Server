import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const root = new URL('../', import.meta.url);
const helperSource = await readFile(new URL('src/components/lesson/flattened-derivative-status.js', root), 'utf8');
const helper = await import(`data:text/javascript;base64,${Buffer.from(helperSource).toString('base64')}`);
const editorSource = await readFile(new URL('src/views/LessonEditor.vue', root), 'utf8');
const apiSource = await readFile(new URL('src/apis/module/lesson.js', root), 'utf8');
const packageSource = JSON.parse(await readFile(new URL('package.json', root), 'utf8'));

function extractObjectMethod(source, name) {
  const match = new RegExp(`\\n\\s{2,4}${name}\\(`).exec(source);
  assert.ok(match, `${name} method not found`);
  const start = match.index + match[0].lastIndexOf(name);
  const paramsStart = source.indexOf('(', start);
  const paramsEnd = source.indexOf(')', paramsStart);
  const braceStart = source.indexOf('{', paramsEnd);
  let depth = 0;
  for (let index = braceStart; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}' && --depth === 0) {
      return `function ${name}(${source.slice(paramsStart + 1, paramsEnd)}) ${source.slice(braceStart, index + 1)}`;
    }
  }
  throw new Error(`${name} method body not closed`);
}

const lesson = { lessonId: '11111111-1111-4111-8111-111111111111', lessonVersion: 4 };
const readyOutput = {
  url: 'https://cdn.example.test/lessons/derivatives/opening.mp4',
  sha256: 'a'.repeat(64), bytes: 1234, mediaType: 'video/mp4', width: 480, height: 320,
  metadata: { codec: 'mjpeg', width: 480, height: 320, fps: 10, durationMs: 9000, frameCount: 90, hasAudio: false },
};
const phase = (state, overrides = {}) => ({
  phaseId: 'opening', derivativeId: 'b'.repeat(64), sourceRevision: 7, state,
  errorCode: state === 'failed' ? 'ffmpeg_failed' : null,
  ...(state === 'ready' ? { output: readyOutput } : {}),
  ...overrides,
});

for (const state of ['not-requested', 'processing', 'ready', 'failed', 'stale']) {
  const parsed = helper.normalizeFlattenedDerivativeStatus([phase(state)], lesson);
  assert.equal(parsed.lessonId, lesson.lessonId);
  assert.equal(parsed.lessonVersion, lesson.lessonVersion);
  assert.equal(parsed.sourceRevision, 7);
  assert.equal(parsed.phases[0].state, state);
  assert.equal('path' in (parsed.phases[0].output || {}), false, 'local paths must never enter admin state');
}

assert.deepEqual(helper.normalizeFlattenedDerivativeStatus([], lesson), {
  lessonId: lesson.lessonId, lessonVersion: lesson.lessonVersion, sourceRevision: null, phases: [],
});
assert.equal(helper.normalizeFlattenedDerivativeStatus([phase('ready')], lesson).phases[0].output.url, readyOutput.url);
assert.equal(helper.normalizeFlattenedDerivativeStatus([phase('ready', {
  output: { ...readyOutput, url: 'lessons/derivatives/opening.mp4' },
})], lesson).phases[0].output.url, 'lessons/derivatives/opening.mp4', 'backend-relative public URLs must remain valid');
assert.equal(helper.normalizeFlattenedDerivativeStatus([phase('failed')], lesson).phases[0].errorCode, 'ffmpeg_failed');
assert.deepEqual(helper.normalizeFlattenedDerivativeStatusResponse({ data: [phase('ready')] }, lesson),
  helper.normalizeFlattenedDerivativeStatus([phase('ready')], lesson), 'raw backend envelopes must use the production parser');
assert.equal(helper.normalizeFlattenedDerivativeStatusResponse({ data: [phase('ready', { path: '/private/output.mp4' })] }, lesson), null);
assert.equal(helper.normalizeFlattenedDerivativeStatusResponse({ data: [{ ...phase('ready'), extra: true }] }, lesson), null);
assert.equal(helper.normalizeFlattenedDerivativeStatusResponse({ data: [], extra: true }, lesson), null);

const malformed = [
  [phase('queued')],
  [phase('ready', { path: '/private/output.mp4' })],
  [phase('ready', { output: { ...readyOutput, path: '/private/output.mp4' } })],
  [phase('ready', { output: { ...readyOutput, url: 'file:///private/output.mp4' } })],
  [phase('ready', { output: { ...readyOutput, extra: true } })],
  [phase('processing', { output: readyOutput })],
  [phase('failed', { errorCode: '../unsafe' })],
  [phase('ready'), phase('ready', { phaseId: 'teach', sourceRevision: 8 })],
  [{ ...phase('ready'), extra: true }],
];
for (const payload of malformed) assert.equal(helper.normalizeFlattenedDerivativeStatus(payload, lesson), null);

assert.equal(helper.anyFlattenedDerivativeProcessing({ phases: [phase('processing')] }), true);
assert.equal(helper.anyFlattenedDerivativeProcessing({ phases: [phase('ready')] }), false);
const exactReady = { sourceRevision: 7, phases: [phase('ready'), phase('ready', { phaseId: 'teach' })] };
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v4', exactReady, ['opening', 'teach'], 7), true);
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v4', exactReady, ['opening'], 7), false, 'extra phases fail closed');
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v4', exactReady, ['opening', 'teach', 'review'], 7), false, 'missing phases fail closed');
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v4', { sourceRevision: 7, phases: [phase('ready'), phase('ready')] }, ['opening'], 7), false, 'duplicate phase ids fail closed');
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v4', exactReady, ['opening', 'teach'], 8), false, 'stale revisions fail closed');
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v4', exactReady, ['opening', 'teach'], null), false, 'missing current revision fails closed');
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v4', exactReady, [], 7), false, 'missing required phase identity fails closed');
assert.equal(helper.flattenedDerivativesReadyForPublish('teebot-lesson-renderer.v3', null, [], null), true, 'v1-v3 publish behavior must remain unchanged');
assert.deepEqual(helper.requiredFlattenedDerivativePhaseIds({ cinematicPhases: [{ phaseId: 'opening' }, { phaseId: 'teach' }] }, []), ['opening', 'teach']);
assert.deepEqual(helper.requiredFlattenedDerivativePhaseIds(null, [{ stepBody: { cinematicPhases: [{ phaseId: 'opening' }] } }]), ['opening']);
assert.equal(helper.requiredFlattenedDerivativePhaseIds({ cinematicPhases: [{ phaseId: 'opening' }, { phaseId: 'opening' }] }, []), null);
assert.equal(helper.requiredFlattenedDerivativePhaseIds({}, []), null);
assert.equal(helper.flattenedDerivativeResponseIsCurrent({ requestId: 2, lessonId: lesson.lessonId, sourceEpoch: 4 }, { requestId: 2, lessonId: lesson.lessonId, sourceEpoch: 4 }), true);
assert.equal(helper.flattenedDerivativeResponseIsCurrent({ requestId: 2, lessonId: lesson.lessonId, sourceEpoch: 4 }, { requestId: 1, lessonId: lesson.lessonId, sourceEpoch: 4 }), false);
assert.equal(helper.flattenedDerivativeResponseIsCurrent({ requestId: 2, lessonId: lesson.lessonId, sourceEpoch: 4 }, { requestId: 2, lessonId: lesson.lessonId, sourceEpoch: 5 }), false);

let scheduledDelay = null;
const schedulePoll = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'scheduleFlattenedDerivativeStatusPoll')})`, {
  anyFlattenedDerivativeProcessing: helper.anyFlattenedDerivativeProcessing,
  setTimeout(callback, delay) { scheduledDelay = delay; return { callback }; },
});
const pollContext = {
  editorDestroying: false, lessonId: lesson.lessonId, flattenedDerivativeSourceEpoch: 4,
  flattenedDerivativePollTimer: null, clearFlattenedDerivativeStatusPoll() { this.flattenedDerivativePollTimer = null; },
  loadFlattenedDerivativeStatus() {},
};
assert.equal(schedulePoll.call(pollContext, { phases: [phase('processing')] }), true);
assert.equal(scheduledDelay, 5000);
scheduledDelay = null;
assert.equal(schedulePoll.call(pollContext, { phases: [phase('ready')] }), false);
assert.equal(schedulePoll.call(pollContext, { phases: [phase('failed')] }), false);
assert.equal(scheduledDelay, null, 'terminal phases must not schedule polling');

let statusSuccess; let statusError;
const loadStatus = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'loadFlattenedDerivativeStatus')})`, {
  Api: { lesson: { getFlattenedDerivativeStatus(_id, _version, success, error) { statusSuccess = success; statusError = error; } } },
  flattenedDerivativeResponseIsCurrent: helper.flattenedDerivativeResponseIsCurrent,
});
const loadContext = {
  editorDestroying: false, isTVideoJourney: false, lesson, lessonId: lesson.lessonId,
  flattenedDerivativeRequestId: 0, flattenedDerivativeSourceEpoch: 2,
  flattenedDerivativeStatus: null, flattenedDerivativeLoading: false, flattenedDerivativeError: '',
  flattenedDerivativeReadyPreview: null, flattenedDerivativePreviewError: '',
  clearFlattenedDerivativeStatusPoll() {}, scheduleFlattenedDerivativeStatusPoll() {},
  $t(key) { return key; }, $message: { error() {} },
};
assert.equal(loadStatus.call(loadContext), true);
loadContext.flattenedDerivativeSourceEpoch += 1;
statusSuccess({ ...lesson, sourceRevision: 7, phases: [phase('ready')] });
statusError('late error', { status: 500 });
assert.equal(loadContext.flattenedDerivativeStatus, null, 'late success/error callbacks must be ignored after a source epoch change');
statusSuccess = undefined;
statusError = undefined;
assert.equal(loadStatus.call({ ...loadContext, isTVideoJourney: true }), false, 'TVideo Journey must not call the legacy flattened phase endpoint');
assert.equal(statusSuccess, undefined);
assert.equal(statusError, undefined);
assert.equal(loadStatus.call({
  ...loadContext,
  isTVideoJourney: false,
  lesson: { ...lesson, manifestVersion: 'teebot-lesson-renderer.v4' },
}), true, 'non-TVideo renderer-v4 lessons must retain legacy derivative readiness');

const invalidateStatus = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'invalidateFlattenedDerivativeStatus')})`);
const invalidationContext = {
  flattenedDerivativeRequestId: 3, flattenedDerivativeSourceEpoch: 5,
  flattenedDerivativeLoading: true, flattenedDerivativeError: 'old', flattenedDerivativePreviewError: 'cors',
  flattenedDerivativeStatus: { ...lesson, sourceRevision: 7, phases: [phase('ready')] },
  clearFlattenedDerivativeStatusPoll() {},
};
invalidateStatus.call(invalidationContext);
assert.equal(invalidationContext.flattenedDerivativeRequestId, 4);
assert.equal(invalidationContext.flattenedDerivativeSourceEpoch, 6);
assert.equal(invalidationContext.flattenedDerivativeStatus.phases[0].state, 'stale');
assert.equal('output' in invalidationContext.flattenedDerivativeStatus.phases[0], false);
assert.equal(invalidationContext.flattenedDerivativeCurrentSourceRevision, null);

const canPublish = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'canPublishCurrentProof')})`, {
  flattenedDerivativesReadyForPublish: helper.flattenedDerivativesReadyForPublish,
});
const publishContext = {
  editorDestroying: false, isDraft: true, publishing: false, publishPreparing: false,
  publishUncertainState: null, publishReconciling: false, readinessReady: true,
  hasUnsafeProofState: () => false, proofVersion: 4,
  validationResult: { valid: true }, validationProofVersion: 4,
  previewManifest: { checksum: 'proof' }, previewProofVersion: 4,
  simulationEvidence: {}, simulationProofVersion: 4, validSimulationEvidence: () => true,
};
assert.equal(canPublish.call({ ...publishContext, flattenedDerivativeManifestVersion: 'teebot-lesson-renderer.v3' }), true);
assert.equal(canPublish.call({ ...publishContext, flattenedDerivativeManifestVersion: 'teebot-lesson-renderer.v4', flattenedDerivativeStatus: { sourceRevision: 7, phases: [phase('failed')] }, flattenedDerivativeRequiredPhaseIds: ['opening'], flattenedDerivativeCurrentSourceRevision: 7 }), false);
assert.equal(canPublish.call({ ...publishContext, flattenedDerivativeManifestVersion: 'teebot-lesson-renderer.v4', flattenedDerivativeStatus: { sourceRevision: 7, phases: [phase('ready')] }, flattenedDerivativeRequiredPhaseIds: ['opening'], flattenedDerivativeCurrentSourceRevision: 7 }), true);

const previewFailure = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'onFlattenedDerivativePreviewError')})`);
const previewContext = { flattenedDerivativeStatus: { phases: [phase('ready')] }, flattenedDerivativePreviewError: '', $t: (key) => key };
previewFailure.call(previewContext);
assert.equal(previewContext.flattenedDerivativePreviewError, 'lesson.flattenedDerivativePreviewFailed');
assert.equal(previewContext.flattenedDerivativeStatus.phases[0].state, 'ready', 'browser media errors must not change server readiness');

const stepChangesSource = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'flattenedStepMutationChangesSource')})`);
assert.equal(stepChangesSource.call({ flattenedDerivativeManifestVersion: 'teebot-lesson-renderer.v4' }, { prompt: 'text only', stepBody: { durationSec: 8 } }), false);
assert.equal(stepChangesSource.call({ flattenedDerivativeManifestVersion: 'teebot-lesson-renderer.v4' }, { stepBody: { cinematicPhases: [] } }), true);
assert.equal(stepChangesSource.call({ flattenedDerivativeManifestVersion: 'teebot-lesson-renderer.v3' }, { stepBody: { cinematicPhases: [] } }), false);

for (const token of [
  'flattened-cinematic-derivatives',
  'normalizeFlattenedDerivativeStatus',
  'normalizeAuthoringLesson',
  'manifest_version ?? value.manifestVersion',
  'getFlattenedDerivativeStatus(lessonId, lessonVersion',
]) assert.ok(apiSource.includes(token), `lesson API must include ${token}`);
assert.doesNotMatch(apiSource, /flattened[^\n]{0,80}(?:path|filesystem)/i, 'API must not expose local derivative paths');

for (const token of [
  'data-testid="flattened-derivative-readiness"',
  'flattenedDerivativeStatus',
  'flattenedDerivativePreviewError',
  'loadFlattenedDerivativeStatus',
  'invalidateFlattenedDerivativeStatus',
  'scheduleFlattenedDerivativeStatusPoll',
  'clearFlattenedDerivativeStatusPoll',
  'flattenedDerivativeSourceEpoch',
  'flattenedDerivativeResponseIsCurrent',
  "this.$t('lesson.flattenedDerivativeServerFailed'",
  "this.$t('lesson.flattenedDerivativePreviewFailed'",
]) assert.ok(editorSource.includes(token), `LessonEditor.vue must include ${token}`);
assert.match(editorSource, /<section v-if="!isTVideoJourney" class="preview-surface derivative-readiness"/, 'TVideo Journey must hide the legacy flattened phase panel');
assert.match(editorSource, /beforeDestroy\(\)[\s\S]*flattenedDerivativePollTimer[\s\S]*clearTimeout/, 'destroy must stop derivative polling');
assert.match(editorSource, /'\$route\.query\.lessonId'[\s\S]*resetFlattenedDerivativeStatus/, 'route changes must reset polling and epochs');
assert.match(editorSource, /scheduleFlattenedDerivativeStatusPoll[\s\S]*setTimeout[\s\S]*5000/, 'processing polling must use a bounded five-second interval');
assert.match(editorSource, /applyLessonVisualSelection[\s\S]*invalidateFlattenedDerivativeStatus/, 'cinematic composition saves must invalidate derivative status');
assert.match(editorSource, /selectCinematicLayer[\s\S]*invalidateFlattenedDerivativeStatus/, 'cinematic source saves must invalidate derivative status');
for (const method of ['rebindClonedVisual', 'moveStep', 'deleteStep', 'addStep', 'saveSelectedStep', 'saveSelectedStepStudio']) {
  const source = extractObjectMethod(editorSource, method);
  const invalidateAt = source.indexOf('invalidateFlattenedDerivativeStatus');
  const reloadAt = source.indexOf('loadFlattenedDerivativeStatus');
  assert.ok(invalidateAt >= 0 && reloadAt > invalidateAt, `${method} must invalidate then reload authoritative status`);
}
assert.match(editorSource, /canPublishCurrentProof\(\)[\s\S]*flattenedDerivativesReadyForPublish/, 'publish gate must use exact renderer-v4 phase/revision readiness');
assert.doesNotMatch(editorSource, /flattenedDerivative(?:Status|Loading|Error|PollTimer|SourceEpoch|RequestId)[\s\S]{0,100}lessonUpdateSafety\.setDirty/, 'polling must not dirty the lesson');

assert.equal(packageSource.scripts['test:flattened-derivative-status'], 'node scripts/check-flattened-derivative-status.mjs');
const lessonStudioChain = packageSource.scripts['test:lesson-studio'];
assert.ok(lessonStudioChain.includes('npm run test:flattened-derivative-status'), 'lesson studio aggregate must run the derivative readiness gate');
assert.ok(
  lessonStudioChain.indexOf('npm run test:flattened-derivative-status') < lessonStudioChain.indexOf('npm run test:flattened-cinematic-preview'),
  'derivative readiness gate must run before downstream cinematic preview checks',
);
for (const locale of ['en', 'vi']) {
  const source = await readFile(new URL(`src/i18n/${locale}.js`, root), 'utf8');
  for (const key of ['lesson.flattenedDerivativeTitle', 'lesson.flattenedDerivativeServerFailed', 'lesson.flattenedDerivativePreviewFailed']) {
    assert.ok(source.includes(`'${key}'`), `${locale}.js must translate ${key}`);
  }
}

console.log('flattened derivative status contract checks passed');
