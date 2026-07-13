import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = process.cwd();

function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8');
}

function expectContains(file, needle, reason) {
  const body = read(file);
  if (!body.includes(needle)) {
    throw new Error(`${file} missing ${needle}: ${reason}`);
  }
}

function expectRegex(file, regex, reason) {
  const body = read(file);
  if (!regex.test(body)) {
    throw new Error(`${file} missing ${regex}: ${reason}`);
  }
}

function expectNotContains(file, needle, reason) {
  const body = read(file);
  if (body.includes(needle)) {
    throw new Error(`${file} unexpectedly contains ${needle}: ${reason}`);
  }
}

function extractObjectMethod(source, name) {
  const methodPattern = new RegExp(`\\n\\s{4}${name}\\(`);
  const match = methodPattern.exec(source);
  if (!match) throw new Error(`${name} method not found`);
  const start = match.index + match[0].lastIndexOf(name);
  const paramsStart = source.indexOf('(', start);
  const paramsEnd = source.indexOf(')', paramsStart);
  const braceStart = source.indexOf('{', paramsEnd);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        const params = source.slice(paramsStart + 1, paramsEnd);
        const body = source.slice(braceStart + 1, i);
        return `function ${name}(${params}) {${body}}`;
      }
    }
  }
  throw new Error(`${name} method body not closed`);
}

expectContains('src/views/LessonEditor.vue', '<lesson-step-prompt-editor', 'selected draft steps need prompt editing');
expectContains('src/views/LessonEditor.vue', 'prompt: this.promptDraft', 'save must persist the edited prompt');
expectContains('src/views/LessonEditor.vue', 'promptDirty', 'prompt edits need explicit dirty tracking');
expectContains('src/views/LessonEditor.vue', 'promptEditRevision', 'prompt edits need revision tracking');
expectContains('src/views/LessonEditor.vue', 'stepEditRevisions', 'all step drafts need revision tracking');
expectContains('src/views/LessonEditor.vue', 'promptSaveRequestId', 'save/refetch callbacks need request gating');
expectContains('src/views/LessonEditor.vue', 'selectedStep.stepKey', 'prompt drafts must reset when the selected step changes');
expectContains(
  'src/views/LessonEditor.vue',
  'this.promptStepKey === this.selectedStep.stepKey',
  'a previous step prompt draft must never enable saving for the next selection',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /findIndex\(\(step\)\s*=>\s*step\.stepKey\s*===\s*selectedKey\)/m,
  'fresh fetches must preserve the latest selected step by key',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /Api\.lesson\.updateStep\([\s\S]*?prompt:\s*this\.promptDraft[\s\S]*?fetchSteps/m,
  'dirty state must clear through a server-confirmed refetch',
);

expectNotContains('src/components/lesson/LessonStepPromptEditor.vue', 'this.$emit', 'Vue 2 template handlers must not reference this');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "$emit('input', $event)", 'editor must emit input with Vue 2 template syntax');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', ':value="value"', 'editor must render its value prop');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', ':disabled="disabled"', 'editor must honor read-only lessons');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', 'maxlength="500"', 'prompt length must be capped at 500 characters');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', 'show-word-limit', 'authors must see the prompt character count');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "lesson.promptEditorLabel", 'prompt editor needs a localized accessible label');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "lesson.promptEditorHint", 'prompt editor needs localized guidance');

expectRegex(
  'src/views/LessonEditor.vue',
  /:disabled="promptDirty\s*\|\|\s*savingStep\s*\|\|\s*rebindingSharedVisual"/m,
  'manifest preview must be disabled while the prompt is unsaved or saving',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /<lesson-step-prompt-editor[\s\S]*?:disabled="!isDraft\s*\|\|\s*savingStep\s*\|\|\s*rebindingSharedVisual"/m,
  'prompt input must be disabled during its save request',
);

const editorSource = read('src/views/LessonEditor.vue');
const shouldApplySavedStepState = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'shouldApplySavedStepState')})`);
const matching = {
  promptSaveRequestId: 7,
  promptStepKey: 's2',
  promptEditRevision: 4,
  stepEditRevisions: { s2: 9 },
};
const guard = { requestId: 7, stepKey: 's2', promptRevision: 4, stepRevision: 9 };
if (!shouldApplySavedStepState.call(matching, guard)) {
  throw new Error('matching save state must be eligible for reset after refetch');
}
for (const changed of [
  { ...matching, promptSaveRequestId: 8 },
  { ...matching, promptStepKey: 's3' },
  { ...matching, promptEditRevision: 5 },
  { ...matching, stepEditRevisions: { s2: 10 } },
]) {
  if (shouldApplySavedStepState.call(changed, guard)) {
    throw new Error('stale save/refetch state must not reset a newer or differently selected draft');
  }
}

const clearSavedStepDraft = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'clearSavedStepDraft')})`);
const saveCleanupContext = {
  promptSaveRequestId: 12,
  stepEditRevisions: { A: 4, B: 9 },
  selectedStepDrafts: { A: { durationPreset: 5 }, B: { durationPreset: 8 } },
  selectedAssetDrafts: { A: { assetKey: 'asset-a' }, B: { assetKey: 'asset-b' } },
  dirtyStepKeys: { A: true, B: true },
  $delete(target, key) { delete target[key]; },
};
clearSavedStepDraft.call(saveCleanupContext, { requestId: 12, stepKey: 'A', stepRevision: 4 });
if (saveCleanupContext.selectedStepDrafts.A || saveCleanupContext.selectedAssetDrafts.A || saveCleanupContext.dirtyStepKeys.A) {
  throw new Error('server-confirmed save must clean step A even after navigation');
}
if (!saveCleanupContext.selectedStepDrafts.B || !saveCleanupContext.selectedAssetDrafts.B || !saveCleanupContext.dirtyStepKeys.B) {
  throw new Error('step A save cleanup must preserve selected step B drafts');
}
const staleCleanupContext = {
  ...saveCleanupContext,
  selectedStepDrafts: { A: { durationPreset: 3 }, B: { durationPreset: 8 } },
  dirtyStepKeys: { A: true, B: true },
  stepEditRevisions: { A: 5, B: 9 },
};
clearSavedStepDraft.call(staleCleanupContext, { requestId: 12, stepKey: 'A', stepRevision: 4 });
if (!staleCleanupContext.selectedStepDrafts.A || !staleCleanupContext.dirtyStepKeys.A) {
  throw new Error('stale save callback must not clear a newer step A draft');
}

for (const locale of ['src/i18n/en.js', 'src/i18n/vi.js']) {
  expectContains(locale, "'lesson.promptEditorLabel'", 'prompt editor label must be localized');
  expectContains(locale, "'lesson.promptEditorHint'", 'prompt editor hint must be localized');
  expectContains(locale, "'lesson.stepSaved'", 'save confirmation must be localized');
}

expectContains('src/components/lesson/SharedAssetPicker.vue', "this.$emit('select-intent'", 'selection must review impact first');
expectContains('src/components/lesson/SharedAssetPicker.vue', ':disabled="disabled"', 'selection must lock during save or clone rebind');
expectNotContains('src/components/lesson/SharedAssetPicker.vue', "$emit('select', asset)", 'shared selection must not mutate a draft before review');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'reviewSharedVisualImpact', 'dialog must load backend usage truth');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'cloneSharedVisual', 'dialog must clone without mutating source pins');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', "profile: 'espTft'", 'clone payload must target the firmware profile');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'scope.row.lessonKey', 'every backend lesson usage must be rendered');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'scope.row.lessonVersion', 'every backend lesson version must be rendered');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'localAffectedStepKeys', 'the current draft step references must be visible');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'cloneKey', 'the collision-free clone key must be visible');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'authoritativeAsset.assetKey', 'source key must come from the impact response');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'authoritativeAsset.sha256', 'source checksum must come from the impact response');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', '!impactLoaded', 'actions must remain gated until authoritative impact succeeds');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'retryRebind', 'a committed clone must retry rebind without cloning again');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'rebindError', 'partial clone failures need an actionable recovery state');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'requiresCurrentReference', 'global replacement must prove the selected step is affected');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'currentStepReferencesSource', 'clone eligibility must use layer-specific current-step analysis');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'handleClose', 'dialog close attempts need an in-flight operation guard');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'validCloneResponse', 'clone callbacks must be validated before recovery state is emitted');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'cloneUncertain', 'malformed success must latch a possibly committed state');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'retryDiscovery', 'uncertain clone recovery must retry discovery, not cloning');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'submittedCloneKey', 'clone dispatch must snapshot the immutable requested key');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', ':disabled="cloning || cloneUncertain || reconciling || rebindPending', 'clone key input must lock during async clone/recovery');
expectContains('src/views/LessonEditor.vue', 'discoverUncertainClone', 'parent must reconcile uncertain clone commits through authoritative assets');
expectNotContains('src/components/lesson/SharedVisualImpactDialog.vue', 'result.asset || result.clone', 'unsupported clone response wrappers must be rejected');
expectRegex(
  'src/components/lesson/SharedVisualImpactDialog.vue',
  /keepShared\(\)\s*\{[\s\S]*?if\s*\(!this\.impactLoaded\s*\|\|[\s\S]*?\)\s*return/m,
  'keep-shared must not bypass a failed impact request',
);
expectRegex(
  'src/components/lesson/SharedVisualImpactDialog.vue',
  /confirmClone\(\)\s*\{[\s\S]*?!this\.canClone/m,
  'clone must not bypass a failed impact request',
);
expectContains('src/components/LessonAssetManager.vue', "this.$emit('impact-review-request'", 'shared replacement must request review first');
expectContains('src/components/LessonAssetManager.vue', 'confirmReplace', 'replacement mode needs an explicit parent confirmation gate');
expectRegex(
  'src/views/LessonEditor.vue',
  /intent\.intent\s*===\s*'select'[\s\S]*?bindClonedAssetToStep\(step\.stepBody\s*\|\|\s*\{\}/m,
  'picker clone must bind the selected clone rather than search for the clicked source key',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /Api\.lesson\.updateStep\([\s\S]*?step\.stepKey[\s\S]*?fetchSteps[\s\S]*?preview\s*=\s*null[\s\S]*?previewManifest\s*=\s*null[\s\S]*?refreshSharedVisualTruth/m,
  'clone rebind must wait for server confirmation before refetch and preview invalidation',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /refreshSharedVisualTruth\([\s\S]*?reloadAssets\(done, fail\)[\s\S]*?doValidate\(done, fail\)[\s\S]*?doPreview\(done, fail\)/m,
  'server-confirmed clone rebind must refetch validation and manifest preview',
);
const cloneRebindSource = extractObjectMethod(editorSource, 'rebindClonedVisual');
const cloneRebindOrder = [
  'Api.lesson.updateStep(',
  'this.fetchSteps({',
  'this.preview = null;',
  'this.previewManifest = null;',
  'this.refreshSharedVisualTruth(',
].map((needle) => {
  const index = cloneRebindSource.indexOf(needle);
  if (index === -1) throw new Error(`applyClonedVisual missing ${needle}`);
  return index;
});
if (!cloneRebindOrder.every((index, position) => position === 0 || index > cloneRebindOrder[position - 1])) {
  throw new Error('applyClonedVisual must update one step, then refetch steps/assets and authoritative validation/preview in order');
}

const saveSelectedStepSource = extractObjectMethod(editorSource, 'saveSelectedStep');
if (!/this\.rebindingSharedVisual/.test(saveSelectedStepSource)) {
  throw new Error('saveSelectedStep must reject overlap with clone rebind');
}
const rebindClonedVisualSource = extractObjectMethod(editorSource, 'rebindClonedVisual');
if (!/this\.savingStep/.test(rebindClonedVisualSource)) {
  throw new Error('rebindClonedVisual must reject overlap with prompt save');
}
const updateCalls = [];
const guardedApi = { lesson: { updateStep: (...args) => updateCalls.push(args) } };
const guardedSave = vm.runInNewContext(`(${saveSelectedStepSource})`, { Api: guardedApi });
guardedSave.call({
  selectedStep: { stepKey: 's1' }, isDraft: true, savingStep: false, rebindingSharedVisual: true,
});
const guardedRebind = vm.runInNewContext(`(${rebindClonedVisualSource})`, {
  Api: guardedApi,
  bindClonedAssetToStep: () => ({}),
  replaceStepAssetReference: () => ({}),
  collectAssetReferences: () => ['s1'],
});
guardedRebind.call({ savingStep: true, rebindingSharedVisual: false });
if (updateCalls.length !== 0) throw new Error('overlapping save/rebind guards must prevent updateStep dispatch');

let rebindAttempts = 0;
let successfulClose = 0;
const recoveryApi = { lesson: { updateStep: (lessonId, stepKey, payload, success, error) => {
  rebindAttempts += 1;
  if (rebindAttempts === 1) error('write failed');
  else success();
} } };
const recoveryRebind = vm.runInNewContext(`(${rebindClonedVisualSource})`, {
  Api: recoveryApi,
  bindClonedAssetToStep: () => ({ teachingObject: { asset: { key: 'clone-b' } } }),
  replaceStepAssetReference: () => ({}),
  collectAssetReferences: () => ['s1'],
});
const recoveryContext = {
  savingStep: false,
  rebindingSharedVisual: false,
  lessonId: 'lesson-1',
  sharedImpactIntent: { intent: 'select', stepKey: 's1', layer: 'teachingObject', boundAssetKey: 'asset-a', asset: { assetKey: 'asset-b' } },
  steps: [{ stepKey: 's1', stepBody: { teachingObject: { asset: { key: 'asset-a' } } } }],
  failSharedVisualRebind() { this.rebindingSharedVisual = false; },
  fetchSteps({ onSuccess }) { onSuccess(); },
  refreshSharedVisualTruth(onSuccess) { onSuccess(); },
  $delete() {},
  selectedStepDrafts: {},
  promptDirty: false,
  dirtyStepKeys: {},
  selectedAssetDrafts: {},
  closeSharedImpact() { successfulClose += 1; },
  $nextTick(fn) { fn(); },
  $refs: {},
  $message: { success() {} },
  $t: (key) => key,
  preview: {},
  previewManifest: {},
};
const committedClone = { assetId: 'clone-b', assetKey: 'clone-b', path: '/clone-b.png', sha256: 'clone-b-sha' };
recoveryRebind.call(recoveryContext, committedClone);
if (successfulClose !== 0) throw new Error('failed updateStep must keep recovery context open');
recoveryRebind.call(recoveryContext, committedClone);
if (rebindAttempts !== 2 || successfulClose !== 1) throw new Error('retry must rebind the existing clone and close only after refresh success');

const dialogSource = read('src/components/lesson/SharedVisualImpactDialog.vue');
const confirmCloneSource = extractObjectMethod(dialogSource, 'confirmClone');
if (confirmCloneSource.includes("this.$emit('close')")) {
  throw new Error('dialog must remain open until parent confirms rebind and refresh success');
}
const retryRebindSource = extractObjectMethod(dialogSource, 'retryRebind');
if (retryRebindSource.includes('cloneSharedVisual')) {
  throw new Error('retrying a rebind must reuse the committed clone');
}
const handleCloseSource = extractObjectMethod(dialogSource, 'handleClose');
const keepSharedSource = extractObjectMethod(dialogSource, 'keepShared');
const validCloneResponseSource = extractObjectMethod(dialogSource, 'validCloneResponse');
const retryDiscoverySource = extractObjectMethod(dialogSource, 'retryDiscovery');
if (retryDiscoverySource.includes('cloneSharedVisual')) throw new Error('uncertain recovery must never reissue clone');
let cloneRequests = 0;
const dialogEvents = [];
let deferredCloneSuccess;
const confirmClone = vm.runInNewContext(`(${confirmCloneSource})`, {
  Api: { lesson: { cloneSharedVisual: (lessonId, assetId, payload, success) => {
    cloneRequests += 1;
    deferredCloneSuccess = success;
  } } },
});
const validCloneResponse = vm.runInNewContext(`(${validCloneResponseSource})`);
const dialogContext = {
  impactLoaded: true,
  asset: { assetId: 'source-b' },
  cloneKey: 'teachingObject.b.v2',
  cloning: false,
  lessonId: 'lesson-1',
  clonedAsset: null,
  rebindPending: false,
  requiresCurrentReference: false,
  currentStepReferencesSource: true,
  canClone: true,
  cloneError: '',
  validCloneResponse,
  $t: (key) => key,
  $emit: (...args) => dialogEvents.push(args),
};
confirmClone.call(dialogContext);
const handleClose = vm.runInNewContext(`(${handleCloseSource})`);
const keepShared = vm.runInNewContext(`(${keepSharedSource})`);
handleClose.call(dialogContext);
keepShared.call(dialogContext);
if (dialogEvents.some(([event]) => event === 'close')) throw new Error('clone-in-flight close attempts must preserve dialog context');
deferredCloneSuccess({ assetId: 'clone-b', assetKey: 'teachingObject.b.v2', path: '/clone-b.png', sha256: 'clone-b-sha' });
dialogContext.clonedAsset = { assetId: 'clone-b', assetKey: 'teachingObject.b.v2' };
const retryRebind = vm.runInNewContext(`(${retryRebindSource})`);
retryRebind.call(dialogContext);
if (cloneRequests !== 1) throw new Error('rebind retry must not create a second clone');
if (!dialogEvents.some(([event]) => event === 'retry-rebind')) throw new Error('failed rebind must expose a retry event');

let raceCloneRequests = 0;
let raceSuccess;
const raceEvents = [];
const raceConfirm = vm.runInNewContext(`(${confirmCloneSource})`, {
  Api: { lesson: { cloneSharedVisual: (lessonId, assetId, payload, success) => {
    raceCloneRequests += 1;
    raceSuccess = success;
  } } },
});
const raceContext = {
  ...dialogContext,
  cloning: false,
  cloneUncertain: false,
  clonedAsset: null,
  cloneKey: 'teachingObject.submittedA.v2',
  submittedCloneKey: '',
  canClone: true,
  cloneError: '',
  $emit: (...args) => raceEvents.push(args),
};
raceConfirm.call(raceContext);
raceContext.cloneKey = 'teachingObject.editedB.v9';
raceSuccess(undefined);
const raceUncertain = raceEvents.find(([event]) => event === 'clone-uncertain');
if (raceCloneRequests !== 1 || !raceUncertain || raceUncertain[1].assetKey !== 'teachingObject.submittedA.v2') {
  throw new Error('deferred malformed success must reconcile the immutable submitted key with one clone request');
}

let blockedCloneRequests = 0;
const blockedConfirm = vm.runInNewContext(`(${confirmCloneSource})`, {
  Api: { lesson: { cloneSharedVisual: () => { blockedCloneRequests += 1; } } },
});
const currentStepReferenceSource = extractObjectMethod(dialogSource, 'currentStepReferencesSource');
const currentStepReference = vm.runInNewContext(`(${currentStepReferenceSource})`, {
  stepReferencesAssetInLayer: (body, key) => body.teachingObject.asset.key === key,
});
const canCloneSource = extractObjectMethod(dialogSource, 'canClone');
const canClone = vm.runInNewContext(`(${canCloneSource})`);
const blockedContext = {
  ...dialogContext,
  cloning: false,
  clonedAsset: null,
  intentType: 'replace',
  requiresCurrentReference: true,
  currentStep: { stepBody: { teachingObject: { asset: { key: 'asset-a' } } } },
  authoritativeAsset: { assetKey: 'asset-b' },
  layer: 'teachingObject',
};
blockedContext.currentStepReferencesSource = currentStepReference.call(blockedContext);
blockedContext.canClone = canClone.call(blockedContext);
blockedConfirm.call(blockedContext);
if (blockedContext.currentStepReferencesSource || blockedContext.canClone) {
  throw new Error('replacement clone must be ineligible when only another step references the source');
}
if (blockedCloneRequests !== 0) throw new Error('asset used only by another step must not create an orphan clone');

for (const malformed of [undefined, { assetId: 'clone-b' }, { asset: { assetId: 'clone-b', assetKey: 'b', path: '/b', sha256: 'sha' } }]) {
  const events = [];
  const malformedConfirm = vm.runInNewContext(`(${confirmCloneSource})`, {
    Api: { lesson: { cloneSharedVisual: (lessonId, assetId, payload, success) => success(malformed) } },
    malformed,
  });
  const context = { ...dialogContext, cloning: false, cloneUncertain: false, clonedAsset: null, cloneError: '', $emit: (...args) => events.push(args) };
  malformedConfirm.call(context);
  handleClose.call(context);
  if (context.cloning || !context.cloneUncertain || !context.cloneError
    || events.some(([event]) => event === 'cloned') || events.some(([event]) => event === 'close')
    || !events.some(([event]) => event === 'clone-uncertain')) {
    throw new Error('malformed clone success must reconcile a possibly committed clone without allowing close');
  }
}

const discoverSource = extractObjectMethod(editorSource, 'discoverUncertainClone');
if (discoverSource.includes('cloneSharedVisual')) throw new Error('parent discovery retry must never reissue clone');
const discover = vm.runInNewContext(`(${discoverSource})`);
let discoveryReloads = 0;
const discovered = [];
const discoveryContext = {
  sharedImpactUncertainCloneKey: 'teachingObject.b.v2',
  sharedImpactReconciling: false,
  sharedImpactRebindError: '',
  reloadAssets(success) {
    discoveryReloads += 1;
    success([{ assetId: 'clone-b', assetKey: 'teachingObject.b.v2', path: '/clone-b.png', sha256: 'clone-b-sha' }]);
  },
  validClonedAsset: (asset) => Boolean(asset),
  applyClonedVisual: (asset) => discovered.push(asset),
  $t: (key) => key,
};
discover.call(discoveryContext);
if (discoveryReloads !== 1 || discovered.length !== 1) throw new Error('malformed success must discover and rebind the committed clone');

let missingReloads = 0;
const missingContext = {
  ...discoveryContext,
  sharedImpactUncertainCloneKey: 'teachingObject.b.v2',
  sharedImpactReconciling: false,
  reloadAssets(success) { missingReloads += 1; success([]); },
  applyClonedVisual: () => { throw new Error('missing clone must not rebind'); },
};
discover.call(missingContext);
discover.call(missingContext);
if (missingReloads !== 2 || !missingContext.sharedImpactRebindError) {
  throw new Error('failed discovery retry must only reload assets and keep actionable uncertainty');
}
if (cloneRequests !== 1) throw new Error('reconciliation and discovery retries must not create another clone');

for (const locale of ['src/i18n/en.js', 'src/i18n/vi.js']) {
  for (const key of [
    'lesson.sharedImpactTitle',
    'lesson.sharedImpactKeep',
    'lesson.sharedImpactClone',
    'lesson.sharedImpactUsages',
    'lesson.sharedImpactLocalSteps',
    'lesson.sharedImpactCloneKey',
    'lesson.sharedImpactRetryRebind',
    'lesson.sharedImpactRebindFailed',
    'lesson.sharedImpactCloneUncertain',
    'lesson.sharedImpactRetryDiscovery',
  ]) expectContains(locale, `'${key}'`, 'shared visual review must be localized');
}

console.log('lesson editor UI contracts OK');
