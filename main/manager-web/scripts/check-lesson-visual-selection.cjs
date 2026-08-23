const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.cwd();

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

function assertSourceIncludes(source, needle, message) {
  assert.ok(source.includes(needle), message);
}

function extractObjectMethod(source, name) {
  const methodPattern = new RegExp(`\\n\\s{2,4}${name}\\(`);
  const match = methodPattern.exec(source);
  assert.ok(match, `${name} method must exist`);
  const start = match.index + match[0].lastIndexOf(name);
  const paramsStart = source.indexOf('(', start);
  const paramsEnd = source.indexOf(')', paramsStart);
  const braceStart = source.indexOf('{', paramsEnd);
  let depth = 0;
  for (let index = braceStart; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}' && --depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`${name} method body not closed`);
}

const {
  canonicalLessonVisualPair,
  buildLessonVisualRequest,
  newestPublishedAssetVersions,
} = require('../src/components/lesson/lesson-visual-selection');

const firstStep = {
  visualRefs: [
    { slot: 'backgroundScene', assetVersionId: 'background-version-1', assetKey: 'background.forest' },
    { slot: 'teachingObject', assetVersionId: 'object-version-1', assetKey: 'object.apple' },
  ],
};
const laterStep = {
  visualRefs: [
    { slot: 'backgroundScene', assetVersionId: 'background-version-2', assetKey: 'background.space' },
    { slot: 'teachingObject', assetVersionId: 'object-version-2', assetKey: 'object.rocket' },
  ],
};

assert.deepEqual(canonicalLessonVisualPair([firstStep, laterStep]), {
  backgroundAssetVersionId: 'background-version-1',
  backgroundAssetKey: 'background.forest',
  objectAssetVersionId: 'object-version-1',
  objectAssetKey: 'object.apple',
});
assert.deepEqual(canonicalLessonVisualPair([{ visualRefs: [] }, laterStep]), {
  backgroundAssetVersionId: '',
  backgroundAssetKey: '',
  objectAssetVersionId: '',
  objectAssetKey: '',
});
assert.deepEqual(canonicalLessonVisualPair([]), {
  backgroundAssetVersionId: '',
  backgroundAssetKey: '',
  objectAssetVersionId: '',
  objectAssetKey: '',
});
assert.deepEqual(canonicalLessonVisualPair([{ visualRefs: [
  { slot: 'backgroundScene', asset_version_id: 'background-snake', asset_key: 'background.snake' },
  { slot: 'teachingObject', asset_version_id: 'object-snake', asset_key: 'object.snake' },
] }]), {
  backgroundAssetVersionId: 'background-snake',
  backgroundAssetKey: 'background.snake',
  objectAssetVersionId: 'object-snake',
  objectAssetKey: 'object.snake',
});

const current = {
  backgroundAssetVersionId: 'background-version-1',
  backgroundAssetKey: 'background.forest',
  objectAssetVersionId: 'object-version-1',
  objectAssetKey: 'object.apple',
};
const patch = { objectAssetVersionId: 'object-version-3', objectAssetKey: 'object.ball' };
const currentSnapshot = JSON.stringify(current);
const patchSnapshot = JSON.stringify(patch);
assert.deepEqual(buildLessonVisualRequest(current, patch), {
  backgroundAssetVersionId: 'background-version-1',
  objectAssetVersionId: 'object-version-3',
});
assert.equal(JSON.stringify(current), currentSnapshot);
assert.equal(JSON.stringify(patch), patchSnapshot);
assert.throws(
  () => buildLessonVisualRequest({ backgroundAssetVersionId: 'background-version-1' }, {}),
  /background and object asset version ids are required/i,
);
assert.throws(
  () => buildLessonVisualRequest(current, { objectAssetVersionId: '' }),
  /background and object asset version ids are required/i,
);

assert.deepEqual(newestPublishedAssetVersions([
  { asset_key: 'background.forest', version_id: 'forest-v2', version: 2, publication_state: 'published' },
  { asset_key: 'background.forest', version_id: 'forest-invalid', version: 2.5, publication_state: 'published' },
  { asset_key: 'background.forest', version_id: 'forest-v4', version: 4, publication_state: 'published' },
  { assetKey: 'object.apple', versionId: 'apple-v1', version: 1, publicationState: 'published' },
  { assetKey: 'object.apple', versionId: 'apple-v3', version: 3, publicationState: 'published' },
  { assetKey: 'object.zero', versionId: 'zero-v0', version: 0, publicationState: 'published' },
  { assetKey: 'object.draft', versionId: 'draft-v9', version: 9, publicationState: 'draft' },
]), {
  'background.forest': { versionId: 'forest-v4', version: 4 },
  'object.apple': { versionId: 'apple-v3', version: 3 },
}, 'newest published selection must normalize snake/camel fields, maximize numeric version, and reject invalid records');

function loadLessonApi() {
  const calls = [];
  const source = read('src/apis/module/lesson.js')
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];\n/g, '')
    .replace(/export function /g, 'function ')
    .replace('export default {', 'const lessonApi = {');
  const lessonApi = vm.runInNewContext(`${source}\nlessonApi;`, {
    URLSearchParams,
    Array,
    Date,
    Error,
    JSON,
    Math,
    Number,
    Object,
    Promise,
    RegExp,
    Set,
    String,
    getNestUrl: () => '/nestjs/v1/admin',
    nestRequest: (request) => calls.push(request),
    nestUpload: () => {},
    normalizeLesson: (value) => value,
    normalizeStep: (value) => value,
    normalizeStepType: (value) => value,
  });
  return { lessonApi, calls };
}

const { lessonApi, calls } = loadLessonApi();
const data = {
  backgroundAssetVersionId: 'background-version-1',
  objectAssetVersionId: 'object-version-1',
  robotAssetVersionId: 'robot-version-1',
};
const onSuccess = () => {};
const onError = () => {};
lessonApi.applyLessonVisuals('lesson-7', data, onSuccess, onError);
assert.equal(calls[0].method, 'PUT');
assert.equal(calls[0].url, '/nestjs/v1/admin/lessons/lesson-7/visuals');
assert.strictEqual(calls[0].data, data);
assert.strictEqual(calls[0].onSuccess, onSuccess);
assert.strictEqual(calls[0].onError, onError);

lessonApi.retryLessonAssetGeneration(onSuccess, onError);
assert.equal(calls[1].method, 'POST');
assert.equal(calls[1].url, '/nestjs/v1/admin/lesson-assets/retry');
assert.deepEqual(JSON.parse(JSON.stringify(calls[1].data)), {});
assert.strictEqual(calls[1].onSuccess, onSuccess);
assert.strictEqual(calls[1].onError, onError);

const editorSource = read('src/views/LessonEditor.vue');
const lessonApiSourceForAuthoring = read('src/apis/module/lesson.js');
assertSourceIncludes(lessonApiSourceForAuthoring, 'courseModeContract', 'lesson API must carry persisted Course Mode authority state');
assertSourceIncludes(
  editorSource,
  "import { canonicalLessonVisualPair, buildLessonVisualRequest } from '@/components/lesson/lesson-visual-selection';",
  'LessonEditor must import the lesson-wide visual helpers through named CommonJS interop',
);
assertSourceIncludes(editorSource, 'data-testid="lesson-background-selector"', 'background selector needs a stable lesson-level test id');
assertSourceIncludes(editorSource, 'data-testid="lesson-object-selector"', 'object selector needs a stable lesson-level test id');
assertSourceIncludes(editorSource, 'data-testid="lesson-robot-selector"', 'robot selector needs a stable lesson-level test id');
assertSourceIncludes(editorSource, 'lessonVisualPair()', 'LessonEditor must derive one canonical pair for the lesson');
assertSourceIncludes(editorSource, 'canonicalLessonVisualPair(this.steps)', 'the canonical pair must come from authoritative steps');
assertSourceIncludes(editorSource, 'isCourseModeV5()', 'Course Mode v5 must be the scoped boundary for robot triple authoring');
assertSourceIncludes(editorSource, 'hasLoadedCourseModeAuthority()', 'Course Mode v5 authoring must require persisted Course Mode authority');
assertSourceIncludes(editorSource, 'filterRobotVideoAssets', 'robot picker must fail closed to real MJPEG MP4 robot videos');
assertSourceIncludes(editorSource, 'applyLessonVisualSelection(patch)', 'both selectors must share one lesson-level save path');
assertSourceIncludes(editorSource, 'buildLessonVisualRequest(this.lessonVisualPair, patch)', 'every save must merge and validate both visual ids');
assertSourceIncludes(editorSource, 'Api.lesson.applyLessonVisuals(', 'visual selection must use the lesson-level API');
assertSourceIncludes(editorSource, 'savingLessonVisuals: false', 'visual selection needs an explicit saving state');
assertSourceIncludes(editorSource, 'pendingLessonVisualPair: null', 'incomplete local pairs need explicit pending state');
assertSourceIncludes(editorSource, 'rawCinematicLibraries:', 'cinematic assets must be stored raw so Course Mode authority can filter on read');
assert.ok(!editorSource.includes('selectedBackgroundKey: \'\''), 'selectedBackgroundKey must not be mutable component data');
assert.ok(!editorSource.includes('pickedObjectKey: \'\''), 'pickedObjectKey must not be mutable component data');
assert.ok(!editorSource.includes('<SharedAssetPicker'), 'the editor must not expose a conflicting per-step teaching-object picker');
assertSourceIncludes(
  editorSource,
  ':disabled="!isDraft || lessonVisualSelectionDisabled"',
  'published lessons must disable the background and teaching-object selectors',
);
assertSourceIncludes(
  editorSource,
  "$t(isCourseModeV5 ? 'lesson.visualTripleRequired' : 'lesson.visualPairRequired')",
  'the inline missing-visual notice must use the Course Mode v5 triple copy when a robot video is also required',
);
assertSourceIncludes(
  editorSource,
  "$t('lesson.visualSelectionReadOnly')",
  'published lesson visual notice must use read-only/create-editable-version copy',
);
assert.ok(
  !editorSource.includes('Background and teaching object update this lesson immediately.'),
  'published lesson visual notice must not claim selectors update published lessons immediately',
);

const selectBackgroundSource = extractObjectMethod(editorSource, 'selectBackground');
assert.match(selectBackgroundSource, /applyLessonVisualSelection\(\{[\s\S]*backgroundAssetVersionId:\s*bg\.versionId[\s\S]*backgroundAssetKey:\s*bg\.assetKey/m);
assert.ok(!selectBackgroundSource.includes('setVisualRef'), 'background selector must not save a per-step visual ref');
const selectTeachObjectSource = extractObjectMethod(editorSource, 'selectTeachObject');
assert.match(selectTeachObjectSource, /applyLessonVisualSelection\(\{[\s\S]*objectAssetVersionId:\s*obj\.versionId[\s\S]*objectAssetKey:\s*obj\.assetKey/m);
assert.ok(!selectTeachObjectSource.includes('setVisualRef'), 'object selector must not save a per-step visual ref');
const selectedVisualVersionIdSource = extractObjectMethod(editorSource, 'selectedVisualVersionId');
assert.match(selectedVisualVersionIdSource, /slot === 'robotOverlay'[\s\S]*this\.isCourseModeV5[\s\S]*this\.lessonVisualPair\.robotAssetVersionId/m, 'Course Mode v5 robot selector must read the lesson-wide robot pin');
const selectCinematicLayerSource = extractObjectMethod(editorSource, 'selectCinematicLayer');
assert.match(selectCinematicLayerSource, /selection\.slot === 'robotOverlay'[\s\S]*this\.isCourseModeV5[\s\S]*applyLessonVisualSelection\(\{[\s\S]*robotAssetVersionId:\s*selection\.assetVersionId[\s\S]*robotAssetKey:\s*asset\.assetKey/m, 'Course Mode v5 robot selector must use the atomic lesson visual triple save');
assert.match(selectCinematicLayerSource, /if \(!this\.isCourseModeV5\)[\s\S]*Api\.lesson\.setVisualRef\(/m, 'legacy non-v5 robot authoring must keep the old per-step visual ref path');

const isCourseModeV5Source = extractObjectMethod(editorSource, 'isCourseModeV5');
const isCourseModeV5 = vm.runInNewContext(`(${isCourseModeV5Source.replace(/^isCourseModeV5/, 'function isCourseModeV5')})`);
const hasLoadedCourseModeAuthoritySource = extractObjectMethod(editorSource, 'hasLoadedCourseModeAuthority');
const hasLoadedCourseModeAuthority = vm.runInNewContext(`(${hasLoadedCourseModeAuthoritySource.replace(/^hasLoadedCourseModeAuthority/, 'function hasLoadedCourseModeAuthority')})`);
assert.equal(hasLoadedCourseModeAuthority.call({
  lesson: { courseModeContract: { version: 2 } },
  previewManifest: null,
}), true, 'lesson-level Course Mode contract marks loaded persisted authority');
assert.equal(hasLoadedCourseModeAuthority.call({
  lesson: {},
  previewManifest: { manifest: { courseModeContract: { version: 2 } } },
}), true, 'preview Course Mode contract marks loaded persisted authority');
assert.equal(hasLoadedCourseModeAuthority.call({
  lesson: {},
  previewManifest: { manifest: {} },
}), false, 'missing Course Mode contract must fail closed');
assert.equal(isCourseModeV5.call({
  lessonId: 'course-v5',
  lesson: { lessonId: 'course-v5', manifestVersion: 'teebot-lesson-renderer.v5', courseModeContract: { version: 2 } },
  previewManifest: null,
  hasLoadedCourseModeAuthority: true,
}), true, 'renderer v5 with loaded persisted Course Mode authority is Course Mode v5');
assert.equal(isCourseModeV5.call({
  lessonId: 'non-course-v5',
  lesson: { lessonId: 'non-course-v5', manifestVersion: 'teebot-lesson-renderer.v5' },
  previewManifest: null,
  hasLoadedCourseModeAuthority: false,
}), false, 'renderer v5 alone must not enable Course Mode visual triples');
assert.equal(isCourseModeV5.call({
  lessonId: 'preview-course-v5',
  lesson: { lessonId: 'preview-course-v5', manifestVersion: 'teebot-lesson-renderer.v5' },
  previewManifest: { manifest: { courseModeContract: { version: 2 } } },
  hasLoadedCourseModeAuthority: true,
}), true, 'loaded preview Course Mode authority may enable Course Mode v5 triples');

const nonCourseRobotCalls = [];
const selectCinematicLayer = vm.runInNewContext(`(${selectCinematicLayerSource.replace(/^selectCinematicLayer/, 'function selectCinematicLayer')})`, {
  Api: { lesson: { setVisualRef: (...args) => nonCourseRobotCalls.push(args) } },
});
const nonCourseRobotContext = {
  isCourseModeV5: false,
  selectedStep: { stepKey: 's1' },
  isDraft: true,
  cinematicRefSaving: false,
  lessonId: 'non-course-v5',
  invalidateFlattenedDerivativeStatus() {},
  fetchSteps() {},
  $message: { warning() {}, error() {}, success() {} },
};
selectCinematicLayer.call(nonCourseRobotContext, {
  slot: 'robotOverlay',
  assetVersionId: 'robot-v1',
  asset: { assetKey: 'robot.valid', title: 'Robot valid' },
});
assert.equal(nonCourseRobotCalls.length, 1, 'non-Course renderer v5 robot selection must keep per-step setVisualRef');

const saveSelectedStepSource = extractObjectMethod(editorSource, 'saveSelectedStep');
const rebindClonedVisualSource = extractObjectMethod(editorSource, 'rebindClonedVisual');
assert.ok(!saveSelectedStepSource.includes('setVisualRef'), 'step metadata save must not pin a background visual ref');
assert.ok(!saveSelectedStepSource.includes('templateVisualRefTransition'), 'step metadata save must not derive a per-step background transition');
assertSourceIncludes(saveSelectedStepSource, 'this.stepPayloadWithoutVisualRefs(', 'ordinary step saves must strip stale lesson visual refs');
assertSourceIncludes(rebindClonedVisualSource, 'this.stepPayloadWithoutVisualRefs(', 'cloned visual rebinds must strip stale lesson visual refs');
assert.ok(!editorSource.includes('restoreTemplateVisualRef('), 'obsolete per-step background rollback must be removed');
assert.ok(!editorSource.includes('templateVisualRefTransition'), 'obsolete template background transition import must be removed');

const stripVisualRefsMethod = extractObjectMethod(editorSource, 'stepPayloadWithoutVisualRefs');
const stripVisualRefs = vm.runInNewContext(`(${stripVisualRefsMethod.replace(
  /^stepPayloadWithoutVisualRefs/,
  'function stepPayloadWithoutVisualRefs',
)})`);
const staleStepPayload = { prompt: 'hello', visualRefs: [{ slot: 'backgroundScene', assetVersionId: 'stale' }] };
const staleStepPayloadSnapshot = JSON.stringify(staleStepPayload);
assert.deepEqual(JSON.parse(JSON.stringify(stripVisualRefs(staleStepPayload))), { prompt: 'hello' });
assert.equal(JSON.stringify(staleStepPayload), staleStepPayloadSnapshot, 'stripping visual refs must not mutate the loaded step');

const applyLessonVisualSelectionSource = extractObjectMethod(editorSource, 'applyLessonVisualSelection');
const visualSaveGuard = applyLessonVisualSelectionSource.slice(0, applyLessonVisualSelectionSource.indexOf('const nextPair'));
assertSourceIncludes(visualSaveGuard, '!this.isDraft', 'published lessons must not dispatch lesson visual PUTs');
assertSourceIncludes(visualSaveGuard, 'this.savingStep', 'lesson visual saves must wait for step saves');
assertSourceIncludes(visualSaveGuard, 'this.rebindingSharedVisual', 'lesson visual saves must wait for shared visual rebinds');
assertSourceIncludes(visualSaveGuard, 'this.assetMutating', 'lesson visual saves must wait for active visual asset mutations');
assertSourceIncludes(visualSaveGuard, 'this.addingStep', 'lesson visual saves must wait for step creation');
assertSourceIncludes(visualSaveGuard, 'this.reordering', 'lesson visual saves must wait for step reorder');
assertSourceIncludes(visualSaveGuard, 'this.deletingStepKey', 'lesson visual saves must wait for step deletion');
assertSourceIncludes(
  applyLessonVisualSelectionSource,
  'const confirmedPair = this.lessonVisualReconciliationRequired ? this.pendingLessonVisualPair : null;',
  'a reconciliation retry must preserve the pair already confirmed by the backend',
);
assertSourceIncludes(
  applyLessonVisualSelectionSource,
  'this.pendingLessonVisualPair = confirmedPair;',
  'a failed reconciliation retry must restore the previously confirmed pair',
);
assert.match(saveSelectedStepSource.slice(0, saveSelectedStepSource.indexOf('const authored')), /this\.lessonVisualStepMutationBlocked/, 'step saves must wait for lesson visual state reconciliation');
const saveSelectedStepStudioSource = extractObjectMethod(editorSource, 'saveSelectedStepStudio');
assert.match(saveSelectedStepStudioSource.slice(0, saveSelectedStepStudioSource.indexOf('const savedRevision')), /this\.lessonVisualStepMutationBlocked/, 'studio step saves must wait for lesson visual state reconciliation');
assertSourceIncludes(saveSelectedStepStudioSource, 'this.stepPayloadWithoutVisualRefs(', 'studio step saves must also strip stale lesson visual refs');
assertSourceIncludes(saveSelectedStepStudioSource, 'const lessonId = this.lessonId;', 'studio step saves must capture the lesson identity');
assertSourceIncludes(saveSelectedStepStudioSource, 'const lessonLoadRequestId = this.lessonLoadRequestId;', 'studio step saves must capture the lesson navigation epoch');
assert.ok(
  (saveSelectedStepStudioSource.match(/this\.editorDestroying\s*\|\|\s*lessonId\s*!==\s*this\.lessonId/g) || []).length >= 2,
  'studio step save success and error callbacks must reject stale lesson responses',
);
assert.ok(
  (saveSelectedStepStudioSource.match(/lessonLoadRequestId\s*!==\s*this\.lessonLoadRequestId/g) || []).length >= 2,
  'studio step save callbacks must reject leave-and-return responses for the same lesson id',
);
const studioSaveCalls = [];
const guardedStudioSave = vm.runInNewContext(`(${saveSelectedStepStudioSource.replace(/^saveSelectedStepStudio/, 'function saveSelectedStepStudio')})`, {
  Api: { lesson: { updateStep: (...args) => studioSaveCalls.push(args) } },
  buildSaveStepRequest: ({ step, savedRevision }) => ({ stepKey: step.stepKey, savedRevision, payload: { prompt: 'saved' } }),
  resolveSaveSuccess: () => { throw new Error('stale studio save callback reached draft reconciliation'); },
});
const staleStudioContext = {
  selectedStep: { stepKey: 'shared-step' },
  isDraft: true,
  savingStep: false,
  lessonVisualStepMutationBlocked: false,
  rebindingSharedVisual: false,
  savingSelectedStep: false,
  stepDraftRevisions: { 'shared-step': 3 },
  selectedAuthoring: {},
  selectedContent: {},
  selectedAssetDrafts: { 'shared-step': { assetKey: 'old-asset' } },
  selectedStepDrafts: { 'shared-step': { prompt: 'old draft' } },
  selectedContentDrafts: { 'shared-step': { prompt: 'old content' } },
  dirtyStepKeys: { 'shared-step': true },
  savingStepKeys: {},
  lessonId: 'lesson-1',
  lessonLoadRequestId: 11,
  editorDestroying: false,
  studioRevision: 0,
  validationResult: { valid: true },
  previewManifest: { manifest: true },
  stepPayloadWithoutVisualRefs: (payload) => payload,
  flattenedStepMutationChangesSource: () => false,
  $set(target, key, value) { target[key] = value; },
  $delete(target, key) { delete target[key]; },
  fetchCount: 0,
  fetchSteps() { this.fetchCount += 1; },
  messages: [],
  $message: {
    success(message) { staleStudioContext.messages.push(['success', message]); },
    error(message) { staleStudioContext.messages.push(['error', message]); },
  },
};
guardedStudioSave.call(staleStudioContext);
assert.equal(studioSaveCalls.length, 1, 'studio save must dispatch exactly once');
assert.equal(studioSaveCalls[0][0], 'lesson-1', 'studio save must dispatch against the captured lesson id');
staleStudioContext.lessonLoadRequestId = 12;
staleStudioContext.savingStepKeys = { 'shared-step': 'new-save-token' };
staleStudioContext.selectedStepDrafts = { 'shared-step': { prompt: 'new draft' } };
staleStudioContext.selectedContentDrafts = { 'shared-step': { prompt: 'new content' } };
staleStudioContext.selectedAssetDrafts = { 'shared-step': { assetKey: 'new-asset' } };
staleStudioContext.dirtyStepKeys = { 'shared-step': 'new-dirty-token' };
staleStudioContext.validationResult = { valid: 'new lesson' };
staleStudioContext.previewManifest = { manifest: 'new lesson' };
const staleStudioSnapshot = JSON.stringify({
  savingStepKeys: staleStudioContext.savingStepKeys,
  selectedStepDrafts: staleStudioContext.selectedStepDrafts,
  selectedContentDrafts: staleStudioContext.selectedContentDrafts,
  selectedAssetDrafts: staleStudioContext.selectedAssetDrafts,
  dirtyStepKeys: staleStudioContext.dirtyStepKeys,
  validationResult: staleStudioContext.validationResult,
  previewManifest: staleStudioContext.previewManifest,
});
studioSaveCalls[0][3]({});
studioSaveCalls[0][4]('old save failed');
assert.equal(JSON.stringify({
  savingStepKeys: staleStudioContext.savingStepKeys,
  selectedStepDrafts: staleStudioContext.selectedStepDrafts,
  selectedContentDrafts: staleStudioContext.selectedContentDrafts,
  selectedAssetDrafts: staleStudioContext.selectedAssetDrafts,
  dirtyStepKeys: staleStudioContext.dirtyStepKeys,
  validationResult: staleStudioContext.validationResult,
  previewManifest: staleStudioContext.previewManifest,
}), staleStudioSnapshot, 'stale studio save callbacks must not mutate the newly loaded lesson state');
assert.equal(staleStudioContext.fetchCount, 0, 'stale studio save success must not fetch the newly loaded lesson');
assert.deepEqual(staleStudioContext.messages, [], 'stale studio save callbacks must not show messages from the old request');
const updateStepSources = [rebindClonedVisualSource, saveSelectedStepSource, saveSelectedStepStudioSource];
assert.equal((editorSource.match(/Api\.lesson\.updateStep\(/g) || []).length, updateStepSources.length, 'every updateStep call must be covered by the visual-ref sanitizer contract');
updateStepSources.forEach((methodSource) => {
  assertSourceIncludes(methodSource, 'this.stepPayloadWithoutVisualRefs(', 'every updateStep payload path must remove lesson visual refs');
  assert.ok(!/Api\.lesson\.updateStep\([\s\S]*?\{\s*\.\.\.step[,}]/m.test(methodSource), 'updateStep must never receive a direct spread of the loaded step');
});
assertSourceIncludes(editorSource, 'lessonVisualStepMutationBlocked()', 'step mutation locking needs one consistent computed state');
const lessonVisualStepMutationBlockedSource = extractObjectMethod(editorSource, 'lessonVisualStepMutationBlocked');
assertSourceIncludes(lessonVisualStepMutationBlockedSource, 'this.pendingLessonVisualPair', 'pending lesson visual pairs must block step mutations');
assertSourceIncludes(lessonVisualStepMutationBlockedSource, 'this.lessonVisualReconciliationRequired', 'visual reconciliation must block step mutations');
assertSourceIncludes(saveSelectedStepSource, 'this.lessonVisualStepMutationBlocked', 'ordinary step saves must respect pending visual reconciliation');
assertSourceIncludes(saveSelectedStepStudioSource, 'this.lessonVisualStepMutationBlocked', 'studio step saves must respect pending visual reconciliation');
assertSourceIncludes(
  editorSource,
  ':disabled="!isDraft || savingStep || lessonVisualStepMutationBlocked || rebindingSharedVisual"',
  'step authoring controls must lock during lesson visual saves',
);
assert.match(
  editorSource,
  /<LessonAssetManager\b[\s\S]*?:disabled="(?=[^"]*\bsavingStep\b)(?=[^"]*\blessonVisualStepMutationBlocked\b)(?=[^"]*\brebindingSharedVisual\b)(?=[^"]*\bassetMutating\b)[^"]*"/m,
  'asset manager must lock during step saves, lesson visual reconciliation, clone rebinds, and asset mutations',
);
assert.ok(!applyLessonVisualSelectionSource.includes('this.doPreview('), 'authoritative fetch must remain the only automatic preview trigger');

const publishedVisualSaveCalls = [];
const applyLessonVisualSelection = vm.runInNewContext(`(${applyLessonVisualSelectionSource.replace(
  /^applyLessonVisualSelection/,
  'function applyLessonVisualSelection',
)})`, {
  Api: { lesson: { applyLessonVisuals: (...args) => publishedVisualSaveCalls.push(args) } },
  Object,
  buildLessonVisualRequest,
});
const publishedVisualSaveContext = {
  lessonId: 'published-lesson',
  lessonLoadRequestId: 1,
  lessonVisualSaveRequestId: 0,
  editorDestroying: false,
  isDraft: false,
  isCourseModeV5: true,
  savingLessonVisuals: false,
  savingStep: false,
  rebindingSharedVisual: false,
  assetMutating: false,
  sharedImpactReconciling: false,
  savingStepKeys: {},
  addingStep: false,
  reordering: false,
  deletingStepKey: '',
  steps: [{ stepKey: 'step-a' }],
  lessonVisualPair: {
    backgroundAssetVersionId: 'background-v1',
    backgroundAssetKey: 'background.one',
    objectAssetVersionId: 'object-v1',
    objectAssetKey: 'object.one',
    robotAssetVersionId: 'robot-v1',
    robotAssetKey: 'robot.one',
  },
  pendingLessonVisualPair: null,
  lessonVisualReconciliationRequired: false,
  $nextTick(callback) { callback(); },
  pushCinematicStep() {},
  $t(key) { return key; },
  $message: { warning() {}, error() {}, success() {} },
};
assert.equal(
  applyLessonVisualSelection.call(publishedVisualSaveContext, {
    backgroundAssetVersionId: 'background-v2',
    backgroundAssetKey: 'background.two',
  }),
  false,
  'published lesson visual selection must return false',
);
assert.equal(publishedVisualSaveCalls.length, 0, 'published lesson visual selection must not send a visual PUT');

const authoritativeReloadFailure = applyLessonVisualSelectionSource.match(
  /this\.fetchSteps\(\{[\s\S]*?onError:\s*\(\)\s*=>\s*\{([\s\S]*?)\n\s{12}\},\n\s{10}\}\);/m,
);
assert.ok(authoritativeReloadFailure, 'lesson visual save must handle authoritative step reload failure');
assert.doesNotMatch(authoritativeReloadFailure[1], /this\.pendingLessonVisualPair\s*=\s*null;/, 'reload failure must retain the backend-confirmed pair during reconciliation');
assert.match(authoritativeReloadFailure[1], /this\.savingLessonVisuals\s*=\s*false;/, 'reload failure must stop the visual save state');
assert.match(authoritativeReloadFailure[1], /this\.syncCinematicSoon\(\);/, 'reload failure must resynchronize the cinematic from loaded authoritative steps');
assert.match(authoritativeReloadFailure[1], /this\.lessonVisualReconciliationRequired\s*=\s*true;/, 'reload failure must enter explicit reconciliation state');
assert.match(authoritativeReloadFailure[1], /this\.\$message\.warning\(this\.\$t\('lesson\.visualPairReloadFailed'\)\);/, 'reload failure must explain that the save succeeded but refresh failed');
assertSourceIncludes(editorSource, 'lessonVisualReconciliationRequired: false', 'reconciliation needs explicit component state');
assertSourceIncludes(editorSource, 'deletingStepKey: \'\'', 'step deletion needs explicit in-flight state');
assertSourceIncludes(editorSource, 'v-loading="savingLessonVisuals"', 'lesson visual panel needs an Element UI loading overlay');
assertSourceIncludes(editorSource, ':aria-busy="savingLessonVisuals ? \'true\' : \'false\'"', 'lesson visual loading overlay must retain accessible busy state');

const openStepDialogSource = extractObjectMethod(editorSource, 'openStepDialog');
assert.match(openStepDialogSource, /this\.lessonVisualStepMutationBlocked/, 'add-step dialog must not open during pending lesson visual state');
const addStepSource = extractObjectMethod(editorSource, 'addStep');
assert.match(addStepSource.slice(0, addStepSource.indexOf('const f')), /this\.lessonVisualStepMutationBlocked/, 'step creation must not start during pending lesson visual state');
const moveStepSource = extractObjectMethod(editorSource, 'moveStep');
assert.match(moveStepSource.slice(0, moveStepSource.indexOf('const target')), /this\.lessonVisualStepMutationBlocked/, 'step reorder must not start during pending lesson visual state');
const deleteStepSource = extractObjectMethod(editorSource, 'deleteStep');
assert.ok((deleteStepSource.match(/this\.lessonVisualStepMutationBlocked/g) || []).length >= 2, 'step deletion must guard both before and after confirmation');
assert.ok(
  deleteStepSource.indexOf('const lessonId = this.lessonId;') < deleteStepSource.indexOf('this.$confirm'),
  'step deletion must capture lesson identity before opening confirmation',
);
assert.ok(
  (deleteStepSource.match(/this\.editorDestroying\s*\|\|\s*lessonId\s*!==\s*this\.lessonId/g) || []).length >= 3,
  'step deletion must reject navigation after confirmation and in both API callbacks',
);
assert.ok((deleteStepSource.match(/this\.deletingStepKey\s*=\s*'';/g) || []).length >= 2, 'step deletion state must reset on success and error');
assertSourceIncludes(editorSource, ':loading="deletingStepKey === scope.row.stepKey"', 'active step deletion needs a row loading state');
assertSourceIncludes(
  editorSource,
  ':disabled="stepMutationBlocked"',
  'add-step control must lock during conflicting mutations',
);
// The chain must stay behind a Boolean() computed: inlining it ends the
// expression on `deletingStepKey`, whose idle '' Vue coerces to true, which
// permanently disabled Add step / the step dialog's Save / Delete step.
assertSourceIncludes(
  editorSource,
  'stepMutationBlocked() {\n      return Boolean(',
  'step-mutation locking must be a Boolean() computed, not an inline string chain',
);
[addStepSource, moveStepSource, deleteStepSource].forEach((mutationSource) => {
  assertSourceIncludes(mutationSource, 'const lessonId = this.lessonId;', 'step mutation must capture its lesson identity');
  assertSourceIncludes(mutationSource, 'const lessonLoadRequestId = this.lessonLoadRequestId;', 'step mutation must capture the lesson navigation epoch');
  assert.ok(
    (mutationSource.match(/this\.editorDestroying\s*\|\|\s*lessonId\s*!==\s*this\.lessonId/g) || []).length >= 2,
    'step mutation success and error callbacks must reject stale lesson responses',
  );
  assert.ok(
    (mutationSource.match(/lessonLoadRequestId\s*!==\s*this\.lessonLoadRequestId/g) || []).length >= 2,
    'step mutation callbacks must reject leave-and-return responses for the same lesson id',
  );
});
assertSourceIncludes(editorSource, 'this.addingStep = false;', 'lesson navigation must reset add-step in-flight state');
assertSourceIncludes(editorSource, 'this.reordering = false;', 'lesson navigation must reset reorder in-flight state');
assertSourceIncludes(read('src/i18n/en.js'), "'lesson.visualPairReloadFailed':", 'English reload reconciliation warning is required');
assertSourceIncludes(read('src/i18n/en.js'), "'lesson.visualSelectionReadOnly':", 'English published visual read-only notice is required');
assertSourceIncludes(read('src/i18n/en.js'), 'create an editable version', 'published visual read-only notice must direct authors to create an editable version');
assertSourceIncludes(read('src/i18n/vi.js'), "'lesson.visualPairReloadFailed':", 'Vietnamese reload reconciliation warning is required');
assertSourceIncludes(read('src/i18n/vi.js'), "'lesson.visualSelectionReadOnly':", 'Vietnamese published visual read-only notice is required');
assertSourceIncludes(read('src/i18n/vi.js'), "'lesson.visualTripleTitle':", 'Vietnamese Course Mode v5 visual triple title is required');
assertSourceIncludes(read('src/i18n/vi.js'), "'lesson.visualTripleWholeLesson':", 'Vietnamese Course Mode v5 visual triple description is required');
assertSourceIncludes(read('src/i18n/vi.js'), "'lesson.visualTripleRequired':", 'Vietnamese Course Mode v5 visual triple validation warning is required');

function verifyDirectCinematicLibraryContract() {
  const loadCinematicLibrariesSource = extractObjectMethod(editorSource, 'loadCinematicLibraries');
  const cinematicLibrariesSource = extractObjectMethod(editorSource, 'cinematicLibraries');
  const requests = [];
  const loadCinematicLibraries = vm.runInNewContext(`(${loadCinematicLibrariesSource.replace(
    /^loadCinematicLibraries/,
    'function loadCinematicLibraries',
  )})`, {
    Api: { lesson: { listVisualAssets: (...args) => requests.push(args) } },
    CINEMATIC_SLOT_CATEGORY: {
      backgroundScene: 'scene', teachingObject: 'teachingObject', robotOverlay: 'robotPose',
    },
    Object,
  });
  const cinematicLibraries = vm.runInNewContext(`(${cinematicLibrariesSource.replace(
    /^cinematicLibraries/,
    'function cinematicLibraries',
  )})`, {
    JSON,
  });
  const filterRobotVideoAssets = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'filterRobotVideoAssets').replace(
    /^filterRobotVideoAssets/,
    'function filterRobotVideoAssets',
  )})`, { Number, String, Array });
  const context = {
    rawCinematicLibraries: { backgroundScene: [], teachingObject: [], robotOverlay: [] },
    cinematicLibraryLoading: { backgroundScene: false, teachingObject: false, robotOverlay: false },
    cinematicLibraryErrors: { backgroundScene: '', teachingObject: '', robotOverlay: '' },
    isCourseModeV5: true,
    filterRobotVideoAssets,
    $set(target, key, value) { target[key] = value; },
  };
  loadCinematicLibraries.call(context);
  assert.deepEqual(JSON.parse(JSON.stringify(requests.map(([filters]) => filters))), [
    { category: 'scene', profile: 'espTft' },
    { category: 'teachingObject', profile: 'espTft' },
    { category: 'robotPose', profile: 'espTft' },
  ], 'direct cinematic libraries must query every backend MP4 category');
  const sceneRows = [{ assetKey: 'scene.farm', versionId: 'scene-v4', url: '/scene-v4.mp4', mimeType: 'video/mp4' }];
  const objectRows = [{ assetKey: 'object.apple', versionId: 'apple-v3', url: '/apple-v3.mp4', mimeType: 'video/mp4' }];
  const validRobot = {
    assetKey: 'robot.teacher',
    versionId: 'robot-valid',
    category: 'robotPose',
    profile: 'espTft',
    publicationState: 'published',
    url: '/robot-valid.mp4',
    mimeType: 'video/mp4',
    codec: 'mjpeg',
    fps: 10,
    compatibilityMetadata: {
      codec: 'mjpeg',
      fps: 10,
      hasAudio: false,
      chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 32, feather: 2 },
    },
  };
  const layeredRobot = {
    assetKey: 'robot.layered',
    versionId: 'robot-layered',
    category: 'robotPose',
    profile: 'espTft',
    publicationState: 'published',
    url: '/robot-layered.mp4',
    mimeType: 'video/mp4',
    codec: 'motion-jpeg',
    fps: 15,
    compatibilityMetadata: {
      hasAudio: false,
      chromaKey: { keyColor: '#00ff00', tolerance: 32, featherPx: 2 },
    },
  };
  const invalidRobots = [
    { ...validRobot, assetKey: 'robot.draft', versionId: 'robot-draft', publicationState: 'draft' },
    { ...validRobot, assetKey: 'robot.png', versionId: 'robot-png', mimeType: 'image/png' },
    { ...validRobot, assetKey: 'robot.h264', versionId: 'robot-h264', codec: 'h264', compatibilityMetadata: { ...validRobot.compatibilityMetadata, codec: 'h264' } },
    { ...validRobot, assetKey: 'robot.audio', versionId: 'robot-audio', compatibilityMetadata: { ...validRobot.compatibilityMetadata, hasAudio: true } },
    { ...validRobot, assetKey: 'robot.bad-fps', versionId: 'robot-fps', fps: 12, compatibilityMetadata: { ...validRobot.compatibilityMetadata, fps: 12 } },
    { ...validRobot, assetKey: 'robot.no-chroma', versionId: 'robot-chroma', compatibilityMetadata: { codec: 'mjpeg', fps: 10, hasAudio: false } },
    { ...layeredRobot, assetKey: 'robot.magenta-key', versionId: 'robot-magenta-key', compatibilityMetadata: { ...layeredRobot.compatibilityMetadata, chromaKey: { keyColor: '#ff00ff', tolerance: 32, featherPx: 2 } } },
    { ...layeredRobot, assetKey: 'robot.bad-key-color', versionId: 'robot-key-color', compatibilityMetadata: { ...layeredRobot.compatibilityMetadata, chromaKey: { keyColor: '00ff00', tolerance: 32, featherPx: 2 } } },
    { ...layeredRobot, assetKey: 'robot.bad-layered-tolerance', versionId: 'robot-layered-tolerance', compatibilityMetadata: { ...layeredRobot.compatibilityMetadata, chromaKey: { keyColor: '#00ff00', tolerance: 256, featherPx: 2 } } },
    { ...layeredRobot, assetKey: 'robot.bad-layered-feather', versionId: 'robot-layered-feather', compatibilityMetadata: { ...layeredRobot.compatibilityMetadata, chromaKey: { keyColor: '#00ff00', tolerance: 32, featherPx: 5 } } },
    { ...validRobot, assetKey: 'robot.bad-legacy-tolerance', versionId: 'robot-legacy-tolerance', compatibilityMetadata: { ...validRobot.compatibilityMetadata, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: -1, feather: 2 } } },
    { ...validRobot, assetKey: 'robot.bad-legacy-feather', versionId: 'robot-legacy-feather', compatibilityMetadata: { ...validRobot.compatibilityMetadata, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 32, feather: 5 } } },
    { ...validRobot, assetKey: 'robot.bad-legacy-color', versionId: 'robot-legacy-color', compatibilityMetadata: { ...validRobot.compatibilityMetadata, chromaKey: { color: { r: 0, g: 300, b: 0 }, tolerance: 32, feather: 2 } } },
  ];
  requests[0][1](sceneRows);
  requests[1][1](objectRows);
  requests[2][1]([validRobot, layeredRobot, ...invalidRobots]);
  assert.strictEqual(context.rawCinematicLibraries.backgroundScene, sceneRows);
  assert.strictEqual(context.rawCinematicLibraries.teachingObject, objectRows);
  assert.deepEqual(JSON.parse(JSON.stringify(context.rawCinematicLibraries.robotOverlay.map((row) => row.versionId))), [
    'robot-valid',
    'robot-layered',
    'robot-draft',
    'robot-png',
    'robot-h264',
    'robot-audio',
    'robot-fps',
    'robot-chroma',
    'robot-magenta-key',
    'robot-key-color',
    'robot-layered-tolerance',
    'robot-layered-feather',
    'robot-legacy-tolerance',
    'robot-legacy-feather',
    'robot-legacy-color',
  ], 'robot library load must keep raw backend rows until Course Mode authority is known');
  assert.deepEqual(JSON.parse(JSON.stringify(cinematicLibraries.call(context).robotOverlay.map((row) => row.versionId))), ['robot-valid', 'robot-layered']);
  assert.equal(context.cinematicLibraryErrors.robotOverlay, '');
  assert.equal(Object.values(context.cinematicLibraryLoading).some(Boolean), false);

  const delayedAuthorityContext = {
    rawCinematicLibraries: { backgroundScene: [], teachingObject: [], robotOverlay: [validRobot, ...invalidRobots] },
    isCourseModeV5: false,
    filterRobotVideoAssets,
  };
  assert.deepEqual(
    JSON.parse(JSON.stringify(cinematicLibraries.call(delayedAuthorityContext).robotOverlay.map((row) => row.versionId))),
    delayedAuthorityContext.rawCinematicLibraries.robotOverlay.map((row) => row.versionId),
    'before Course Mode authority arrives, the robot library remains raw for legacy renderer-v5 authoring',
  );
  delayedAuthorityContext.isCourseModeV5 = true;
  assert.deepEqual(
    JSON.parse(JSON.stringify(cinematicLibraries.call(delayedAuthorityContext).robotOverlay.map((row) => row.versionId))),
    ['robot-valid'],
    'when Course Mode authority arrives after assets, the robot picker must fail closed by filtering on read',
  );
}

function verifyOrderedStepRefreshContract() {
  const fetchStepsSource = extractObjectMethod(editorSource, 'fetchSteps');
  const calls = [];
  const fetchSteps = vm.runInNewContext(`(${fetchStepsSource.replace(/^fetchSteps/, 'function fetchSteps')})`, {
    Api: { lesson: { listSteps: (...args) => calls.push(args) } },
    Math,
  });
  const messages = [];
  const context = {
    lessonId: 'lesson-a',
    lessonLoadRequestId: 7,
    lessonStepsRequestId: 0,
    editorDestroying: false,
    selectedStepKey: 'step-new',
    selectedStepIndex: 0,
    steps: [{ stepKey: 'initial' }],
    pendingLessonVisualPair: { backgroundAssetVersionId: 'confirmed' },
    lessonVisualReconciliationRequired: true,
    promptStepKey: '',
    promptDraft: '',
    cinematicDemoUrl: '',
    lessonCapabilities: {},
    previewManifest: null,
    previewing: false,
    resetPromptDraft() {},
    baselinedRows: [],
    rebaselineStepFingerprints(rows) { this.baselinedRows = rows; },
    $message: { error(message) { messages.push(message); } },
  };

  fetchSteps.call(context);
  fetchSteps.call(context);
  assert.equal(calls.length, 2, 'overlapping step refreshes must both dispatch');

  calls[1][1]([{ stepKey: 'step-new', prompt: 'newest' }]);
  context.pendingLessonVisualPair = { backgroundAssetVersionId: 'new-confirmed' };
  context.lessonVisualReconciliationRequired = true;
  calls[0][1]([{ stepKey: 'step-old', prompt: 'oldest' }]);
  calls[0][2]('stale failure');

  assert.deepEqual(JSON.parse(JSON.stringify(context.steps)), [{ stepKey: 'step-new', prompt: 'newest' }], 'an older listSteps response must not replace newer steps');
  // T4.1 — the concurrency baseline must track the newest read only; an older
  // response rebaselining would make the next save read as a false conflict.
  assert.deepEqual(JSON.parse(JSON.stringify(context.baselinedRows)), [{ stepKey: 'step-new', prompt: 'newest' }], 'an older listSteps response must not rebaseline step fingerprints');
  assert.deepEqual(JSON.parse(JSON.stringify(context.pendingLessonVisualPair)), { backgroundAssetVersionId: 'new-confirmed' }, 'an older listSteps response must not clear a newer confirmed visual pair');
  assert.equal(context.lessonVisualReconciliationRequired, true, 'an older listSteps response must not clear newer reconciliation state');
  assert.deepEqual(messages, [], 'an older listSteps error must not surface after a newer refresh');
}

function verifyVisualSaveNavigationEpochContract() {
  const calls = [];
  const applyLessonVisualSelection = vm.runInNewContext(`(${applyLessonVisualSelectionSource.replace(
    /^applyLessonVisualSelection/,
    'function applyLessonVisualSelection',
  )})`, {
    Api: { lesson: { applyLessonVisuals: (...args) => calls.push(args) } },
    Object,
    buildLessonVisualRequest,
  });
  const messages = [];
  const context = {
    lessonId: 'lesson-a',
    lessonLoadRequestId: 10,
    lessonVisualSaveRequestId: 0,
    editorDestroying: false,
    isDraft: true,
    isCourseModeV5: true,
    savingLessonVisuals: false,
    savingStep: false,
    rebindingSharedVisual: false,
    assetMutating: false,
    sharedImpactReconciling: false,
    savingStepKeys: {},
    addingStep: false,
    reordering: false,
    deletingStepKey: '',
    steps: [{ stepKey: 'step-a' }],
    lessonVisualPair: {
      backgroundAssetVersionId: 'background-v1',
      backgroundAssetKey: 'background.one',
      objectAssetVersionId: 'object-v1',
      objectAssetKey: 'object.one',
      robotAssetVersionId: 'robot-v1',
      robotAssetKey: 'robot.one',
    },
    pendingLessonVisualPair: null,
    lessonVisualReconciliationRequired: false,
    $nextTick(callback) { callback(); },
    pushCinematicStep() {},
    syncCinematicSoon() { throw new Error('stale callback reached cinematic sync'); },
    invalidatePreview() { throw new Error('stale callback invalidated the reopened lesson'); },
    fetchSteps() { throw new Error('stale callback refreshed the reopened lesson'); },
    loadLessonAssetGenerationStatus() { throw new Error('stale callback refreshed SD status'); },
    $t(key) { return key; },
    $message: {
      success(message) { messages.push(['success', message]); },
      warning(message) { messages.push(['warning', message]); },
      error(message) { messages.push(['error', message]); },
    },
  };

  applyLessonVisualSelection.call(context, {
    objectAssetVersionId: 'object-v2',
    objectAssetKey: 'object.two',
  });
  assert.equal(calls.length, 1, 'visual save must dispatch once');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])), {
    backgroundAssetVersionId: 'background-v1',
    objectAssetVersionId: 'object-v2',
    robotAssetVersionId: 'robot-v1',
  }, 'Course Mode v5 visual saves must send background/object/robot UUIDs atomically');

  context.lessonLoadRequestId = 12;
  context.lessonVisualSaveRequestId += 1;
  context.savingLessonVisuals = false;
  context.pendingLessonVisualPair = { backgroundAssetVersionId: 'reopened-session' };
  context.lessonVisualReconciliationRequired = true;
  const reopenedSnapshot = JSON.stringify({
    savingLessonVisuals: context.savingLessonVisuals,
    pendingLessonVisualPair: context.pendingLessonVisualPair,
    lessonVisualReconciliationRequired: context.lessonVisualReconciliationRequired,
  });

  calls[0][2]({});
  calls[0][3]('stale save failed');

  assert.equal(JSON.stringify({
    savingLessonVisuals: context.savingLessonVisuals,
    pendingLessonVisualPair: context.pendingLessonVisualPair,
    lessonVisualReconciliationRequired: context.lessonVisualReconciliationRequired,
  }), reopenedSnapshot, 'A to B to A stale visual callbacks must not mutate the reopened lesson session');
  assert.deepEqual(messages, [], 'A to B to A stale visual callbacks must not show messages');

  let authoritativeReloadOptions = null;
  let invalidations = 0;
  let flattenedInvalidations = 0;
  let flattenedReloads = 0;
  context.lessonLoadRequestId = 20;
  context.lessonVisualSaveRequestId += 1;
  context.savingLessonVisuals = false;
  context.pendingLessonVisualPair = null;
  context.lessonVisualReconciliationRequired = false;
  context.invalidatePreview = () => { invalidations += 1; };
  context.invalidateFlattenedDerivativeStatus = () => { flattenedInvalidations += 1; };
  context.loadFlattenedDerivativeStatus = () => { flattenedReloads += 1; };
  context.fetchSteps = (options) => { authoritativeReloadOptions = options; };
  applyLessonVisualSelection.call(context, {
    objectAssetVersionId: 'object-v3',
    objectAssetKey: 'object.three',
  });
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1][1])), {
    backgroundAssetVersionId: 'background-v1',
    objectAssetVersionId: 'object-v3',
    robotAssetVersionId: 'robot-v1',
  }, 'subsequent visual saves must retain the current robot UUID');
  calls[1][2]({});
  assert.equal(invalidations, 1, 'a current visual PUT success must invalidate preview before authoritative reload');
  assert.equal(flattenedInvalidations, 1, 'a current visual PUT success must invalidate flattened derivative status');
  assert.equal(flattenedReloads, 0, 'a current visual PUT success must wait for authoritative reload before refreshing flattened derivative status');
  assert.ok(authoritativeReloadOptions, 'a current visual PUT success must request authoritative steps');

  context.lessonLoadRequestId = 22;
  context.lessonVisualSaveRequestId += 1;
  context.savingLessonVisuals = false;
  context.pendingLessonVisualPair = { backgroundAssetVersionId: 'second-reopened-session' };
  context.lessonVisualReconciliationRequired = true;
  const secondReopenedSnapshot = JSON.stringify({
    savingLessonVisuals: context.savingLessonVisuals,
    pendingLessonVisualPair: context.pendingLessonVisualPair,
    lessonVisualReconciliationRequired: context.lessonVisualReconciliationRequired,
  });
  authoritativeReloadOptions.onSuccess([]);
  authoritativeReloadOptions.onError('stale reload failed');
  assert.equal(JSON.stringify({
    savingLessonVisuals: context.savingLessonVisuals,
    pendingLessonVisualPair: context.pendingLessonVisualPair,
    lessonVisualReconciliationRequired: context.lessonVisualReconciliationRequired,
  }), secondReopenedSnapshot, 'stale authoritative reload callbacks must not mutate a reopened lesson session');
  assert.deepEqual(messages, [], 'stale authoritative reload callbacks must not show messages');
}

verifyVisualSaveNavigationEpochContract();
verifyOrderedStepRefreshContract();

function verifyPreviewAuthorityClearsOnLessonFetchContract() {
  const fetchAllSource = extractObjectMethod(editorSource, 'fetchAll');
  const clearPreviewProofStateSource = extractObjectMethod(editorSource, 'clearPreviewProofState');
  const isCourseModeV5Source = extractObjectMethod(editorSource, 'isCourseModeV5');
  const hasLoadedCourseModeAuthoritySource = extractObjectMethod(editorSource, 'hasLoadedCourseModeAuthority');
  const calls = [];
  const fetchAll = vm.runInNewContext(`(${fetchAllSource.replace(/^fetchAll/, 'function fetchAll')})`, {
    Api: {
      lesson: {
        getLesson: (...args) => calls.push(['getLesson', args]),
        listStepTypes: (...args) => calls.push(['listStepTypes', args]),
        listSharedBackgrounds: (...args) => calls.push(['listSharedBackgrounds', args]),
      },
    },
  });
  const isCourseModeV5 = vm.runInNewContext(`(${isCourseModeV5Source.replace(/^isCourseModeV5/, 'function isCourseModeV5')})`);
  const hasLoadedCourseModeAuthority = vm.runInNewContext(`(${hasLoadedCourseModeAuthoritySource.replace(/^hasLoadedCourseModeAuthority/, 'function hasLoadedCourseModeAuthority')})`);
  const clearPreviewProofState = vm.runInNewContext(`(${clearPreviewProofStateSource.replace(/^clearPreviewProofState/, 'function clearPreviewProofState')})`);
  const context = {
    lessonId: 'non-course-v5',
    lessonLoadRequestId: 4,
    editorDestroying: false,
    loading: false,
    lesson: { lessonId: 'course-v5', manifestVersion: 'teebot-lesson-renderer.v5', courseModeContract: { version: 2 } },
    previewManifest: { lessonId: 'course-v5', manifest: { manifestVersion: 'teebot-lesson-renderer.v5', courseModeContract: { version: 2 }, steps: [] }, checksum: 'old', etag: 'old' },
    preview: { checksum: 'old', etag: 'old' },
    previewProofVersion: 9,
    simulationEvidence: { checksum: 'old', etag: 'old' },
    simulationProofVersion: 9,
    stepTypes: [],
    stepForm: { stepType: '' },
    sharedBackgrounds: [],
    clearPreviewProofState,
    resetLessonAssetGenerationStatus() {},
    resetTVideoJourneyState() {},
    loadLessonAssetGenerationStatus() {},
    fetchSteps() {},
    loadTVideoJourney() {},
    loadFlattenedDerivativeStatus() {},
    $message: { error() {} },
  };
  fetchAll.call(context);
  assert.equal(context.previewManifest, null, 'starting a new lesson fetch must clear stale preview Course Mode authority');
  assert.equal(context.preview, null, 'starting a new lesson fetch must clear stale preview checksum state');
  assert.equal(context.previewProofVersion, -1, 'starting a new lesson fetch must reset preview proof version');
  assert.equal(context.simulationEvidence, null, 'starting a new lesson fetch must clear stale simulation evidence');
  assert.equal(context.simulationProofVersion, -1, 'starting a new lesson fetch must reset simulation proof version');
  assert.equal(calls[0][0], 'getLesson', 'fetchAll must request the new lesson');
  calls[0][1][1]({ lessonId: 'non-course-v5', manifestVersion: 'teebot-lesson-renderer.v5', status: 'draft' });
  context.hasLoadedCourseModeAuthority = hasLoadedCourseModeAuthority.call(context);
  assert.equal(context.hasLoadedCourseModeAuthority, false, 'non-Course renderer v5 must not inherit stale preview Course Mode authority');
  assert.equal(isCourseModeV5.call(context), false, 'non-Course renderer v5 must remain on the legacy visual UI path after route/load');
}

function verifyLatePreviewCannotRestoreStaleCourseAuthorityContract() {
  const doPreviewSource = extractObjectMethod(editorSource, 'doPreview');
  const isCourseModeV5Source = extractObjectMethod(editorSource, 'isCourseModeV5');
  const hasLoadedCourseModeAuthoritySource = extractObjectMethod(editorSource, 'hasLoadedCourseModeAuthority');
  const requests = [];
  const doPreview = vm.runInNewContext(`(${doPreviewSource.replace(/^doPreview/, 'function doPreview')})`, {
    Api: { lesson: { manifestPreview: (...args) => requests.push(args) } },
  });
  const isCourseModeV5 = vm.runInNewContext(`(${isCourseModeV5Source.replace(/^isCourseModeV5/, 'function isCourseModeV5')})`);
  const hasLoadedCourseModeAuthority = vm.runInNewContext(`(${hasLoadedCourseModeAuthoritySource.replace(/^hasLoadedCourseModeAuthority/, 'function hasLoadedCourseModeAuthority')})`);
  const context = {
    lessonId: 'course-v5',
    lessonLoadRequestId: 10,
    previewRequestId: 0,
    proofVersion: 5,
    validationProofVersion: -1,
    publishReviewRequestId: 0,
    publishReviewVisible: true,
    publishReviewSnapshot: { stale: true },
    publishResult: { stale: true },
    previewing: false,
    preview: null,
    previewManifest: null,
    previewProofVersion: -1,
    simulationEvidence: null,
    simulationProofVersion: -1,
    editorDestroying: false,
    lessonCapabilities: { exactEspTftPreview: true },
    hasUnsafeProofState: false,
    validManifestPreviewResponse: (result) => Boolean(result && result.checksum && result.etag && result.manifest && Array.isArray(result.manifest.steps)),
    $message: { error() {} },
  };
  assert.equal(doPreview.call(context, null, null, { allowUnsafe: true }), true, 'Course Mode preview request must dispatch');
  assert.equal(requests.length, 1, 'preview request must be captured for async race simulation');
  context.lessonId = 'non-course-v5';
  context.lessonLoadRequestId += 1;
  context.lesson = { lessonId: 'non-course-v5', manifestVersion: 'teebot-lesson-renderer.v5', status: 'draft' };
  context.preview = null;
  context.previewManifest = null;
  context.previewProofVersion = -1;
  context.simulationEvidence = null;
  context.simulationProofVersion = -1;
  requests[0][2]({
    checksum: 'course-preview-checksum',
    etag: 'course-preview-etag',
    preview: { profile: 'espTft', width: 480, height: 320 },
    manifest: {
      manifestVersion: 'teebot-lesson-renderer.v5',
      profile: 'espTft',
      courseModeContract: { version: 2 },
      steps: [],
    },
  });
  assert.equal(context.previewManifest, null, 'late Course Mode preview success must not store on the navigated lesson');
  assert.equal(context.preview, null, 'late Course Mode preview success must not restore stale preview checksum state');
  context.hasLoadedCourseModeAuthority = hasLoadedCourseModeAuthority.call(context);
  assert.equal(context.hasLoadedCourseModeAuthority, false, 'late Course Mode preview must not restore Course authority on non-Course renderer v5');
  assert.equal(isCourseModeV5.call(context), false, 'late Course Mode preview must leave the non-Course renderer v5 on the legacy UI path');
}

verifyDirectCinematicLibraryContract();
verifyPreviewAuthorityClearsOnLessonFetchContract();
verifyLatePreviewCannotRestoreStaleCourseAuthorityContract();
console.log('lesson visual selection contract: OK');
