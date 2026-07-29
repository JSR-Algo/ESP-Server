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
};
const onSuccess = () => {};
const onError = () => {};
lessonApi.applyLessonVisuals('lesson-7', data, onSuccess, onError);
assert.equal(calls[0].method, 'PUT');
assert.equal(calls[0].url, '/nestjs/v1/admin/lessons/lesson-7/visuals');
assert.strictEqual(calls[0].data, data);
assert.strictEqual(calls[0].onSuccess, onSuccess);
assert.strictEqual(calls[0].onError, onError);

lessonApi.retrySdSync('lesson-7', onSuccess, onError);
assert.equal(calls[1].method, 'POST');
assert.equal(calls[1].url, '/nestjs/v1/admin/lessons/lesson-7/sd-sync/retry');
assert.deepEqual(JSON.parse(JSON.stringify(calls[1].data)), {});
assert.strictEqual(calls[1].onSuccess, onSuccess);
assert.strictEqual(calls[1].onError, onError);

const editorSource = read('src/views/LessonEditor.vue');
assertSourceIncludes(
  editorSource,
  "import { canonicalLessonVisualPair, buildLessonVisualRequest } from '@/components/lesson/lesson-visual-selection';",
  'LessonEditor must import the lesson-wide visual helpers through named CommonJS interop',
);
assertSourceIncludes(editorSource, 'data-testid="lesson-background-selector"', 'background selector needs a stable lesson-level test id');
assertSourceIncludes(editorSource, 'data-testid="lesson-object-selector"', 'object selector needs a stable lesson-level test id');
assertSourceIncludes(editorSource, 'lessonVisualPair()', 'LessonEditor must derive one canonical pair for the lesson');
assertSourceIncludes(editorSource, 'canonicalLessonVisualPair(this.steps)', 'the canonical pair must come from authoritative steps');
assertSourceIncludes(editorSource, 'applyLessonVisualSelection(patch)', 'both selectors must share one lesson-level save path');
assertSourceIncludes(editorSource, 'buildLessonVisualRequest(this.lessonVisualPair, patch)', 'every save must merge and validate both visual ids');
assertSourceIncludes(editorSource, 'Api.lesson.applyLessonVisuals(', 'visual selection must use the lesson-level API');
assertSourceIncludes(editorSource, 'savingLessonVisuals: false', 'visual selection needs an explicit saving state');
assertSourceIncludes(editorSource, 'pendingLessonVisualPair: null', 'incomplete local pairs need explicit pending state');
assert.ok(!editorSource.includes('selectedBackgroundKey: \'\''), 'selectedBackgroundKey must not be mutable component data');
assert.ok(!editorSource.includes('pickedObjectKey: \'\''), 'pickedObjectKey must not be mutable component data');
assert.ok(!editorSource.includes('<SharedAssetPicker'), 'the editor must not expose a conflicting per-step teaching-object picker');

const selectBackgroundSource = extractObjectMethod(editorSource, 'selectBackground');
assert.match(selectBackgroundSource, /applyLessonVisualSelection\(\{[\s\S]*backgroundAssetVersionId:\s*bg\.versionId[\s\S]*backgroundAssetKey:\s*bg\.assetKey/m);
assert.ok(!selectBackgroundSource.includes('setVisualRef'), 'background selector must not save a per-step visual ref');
const selectTeachObjectSource = extractObjectMethod(editorSource, 'selectTeachObject');
assert.match(selectTeachObjectSource, /applyLessonVisualSelection\(\{[\s\S]*objectAssetVersionId:\s*obj\.versionId[\s\S]*objectAssetKey:\s*obj\.assetKey/m);
assert.ok(!selectTeachObjectSource.includes('setVisualRef'), 'object selector must not save a per-step visual ref');

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
  ':disabled="lessonVisualStepMutationBlocked || addingStep || reordering || deletingStepKey"',
  'add-step control must lock during conflicting mutations',
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
assertSourceIncludes(read('src/i18n/vi.js'), "'lesson.visualPairReloadFailed':", 'Vietnamese reload reconciliation warning is required');

async function verifyPublishedObjectLibraryContract() {
  const loadObjectLibrarySource = extractObjectMethod(editorSource, 'loadObjectLibrary');
  const objectRows = [
    { assetKey: 'object.apple', versionId: 'apple-draft-v3', publicationState: 'draft' },
    { assetKey: 'object.apple', versionId: 'apple-retired-v2', publicationState: 'retired' },
    { assetKey: 'object.apple', versionId: 'apple-published-v1', publicationState: 'published' },
    { assetKey: 'object.unpublished', versionId: 'unpublished-draft-v2', publicationState: 'draft' },
    { assetKey: 'object.unpublished', versionId: 'unpublished-retired-v1', publicationState: 'retired' },
  ];
  const manifest = {
    objects: [
      { assetKey: 'object.apple', title: 'Apple', posterUrl: '/apple.png', anim: '/apple.mp4' },
      { assetKey: 'object.unpublished', title: 'Hidden', posterUrl: '/hidden.png' },
    ],
  };
  const context = { objectLibrary: [] };
  const loadObjectLibrary = vm.runInNewContext(`(${loadObjectLibrarySource.replace(
    /^loadObjectLibrary/,
    'function loadObjectLibrary',
  )})`, {
    Api: {
      lesson: {
        listVisualAssets: (_filters, onSuccess) => onSuccess(objectRows),
      },
    },
    Array,
    fetch: async () => ({ ok: true, json: async () => manifest }),
  });

  loadObjectLibrary.call(context);
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(JSON.parse(JSON.stringify(context.objectLibrary)), [{
    assetKey: 'object.apple',
    title: 'Apple',
    posterUrl: '/apple.png',
    anim: '/apple.mp4',
    versionId: 'apple-published-v1',
  }], 'the visible object library must use published versions and omit assets without one');

  const selectedRequest = buildLessonVisualRequest(current, {
    objectAssetVersionId: context.objectLibrary[0].versionId,
    objectAssetKey: context.objectLibrary[0].assetKey,
  });
  assert.equal(selectedRequest.objectAssetVersionId, 'apple-published-v1', 'selector requests must never contain an unpublished object version');
}

verifyPublishedObjectLibraryContract()
  .then(() => console.log('lesson visual selection contract: OK'))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
