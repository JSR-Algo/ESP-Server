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
assert.ok(!saveSelectedStepSource.includes('setVisualRef'), 'step metadata save must not pin a background visual ref');
assert.ok(!saveSelectedStepSource.includes('templateVisualRefTransition'), 'step metadata save must not derive a per-step background transition');
assert.ok(!editorSource.includes('restoreTemplateVisualRef('), 'obsolete per-step background rollback must be removed');
assert.ok(!editorSource.includes('templateVisualRefTransition'), 'obsolete template background transition import must be removed');

const applyLessonVisualSelectionSource = extractObjectMethod(editorSource, 'applyLessonVisualSelection');
const visualSaveGuard = applyLessonVisualSelectionSource.slice(0, applyLessonVisualSelectionSource.indexOf('const nextPair'));
assertSourceIncludes(visualSaveGuard, 'this.savingStep', 'lesson visual saves must wait for step saves');
assertSourceIncludes(visualSaveGuard, 'this.rebindingSharedVisual', 'lesson visual saves must wait for shared visual rebinds');
assertSourceIncludes(visualSaveGuard, 'this.assetMutating', 'lesson visual saves must wait for active visual asset mutations');
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
assert.match(saveSelectedStepSource.slice(0, saveSelectedStepSource.indexOf('const authored')), /this\.savingLessonVisuals/, 'step saves must wait for lesson visual saves');
const saveSelectedStepStudioSource = extractObjectMethod(editorSource, 'saveSelectedStepStudio');
assert.match(saveSelectedStepStudioSource.slice(0, saveSelectedStepStudioSource.indexOf('const savedRevision')), /this\.savingLessonVisuals/, 'studio step saves must wait for lesson visual saves');
assertSourceIncludes(
  editorSource,
  ':disabled="!isDraft || savingStep || savingLessonVisuals || rebindingSharedVisual"',
  'step authoring controls must lock during lesson visual saves',
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
assertSourceIncludes(read('src/i18n/en.js'), "'lesson.visualPairReloadFailed':", 'English reload reconciliation warning is required');
assertSourceIncludes(read('src/i18n/vi.js'), "'lesson.visualPairReloadFailed':", 'Vietnamese reload reconciliation warning is required');

console.log('lesson visual selection contract: OK');
