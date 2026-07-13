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
  /@click="doPreview"[^>]*:disabled="proofActionsDisabled"/m,
  'manifest preview must be disabled for every unsafe or dirty authoring state',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /<lesson-step-prompt-editor[\s\S]*?:disabled="!isDraft\s*\|\|\s*savingStep\s*\|\|\s*rebindingSharedVisual"/m,
  'prompt input must be disabled during its save request',
);

expectContains('src/components/lesson/RobotLessonPreview.vue', 'width: 480px', 'inner stage must match espTft width');
expectContains('src/components/lesson/RobotLessonPreview.vue', 'height: 320px', 'inner stage must match espTft height');
expectContains('src/components/lesson/RobotLessonPreview.vue', 'manifestPreview.preview.profile', 'preview metadata must come from the server');
expectContains('src/components/lesson/RobotLessonPreview.vue', 'manifestPreview.checksum', 'preview checksum must remain visible');
expectContains('src/components/lesson/RobotLessonPreview.vue', 'manifestPreview.etag', 'preview ETag must remain visible');
expectContains('src/components/lesson/LessonSimulationPanel.vue', 'Api.lesson.simulate', 'simulation must use backend manifest truth');
expectContains('src/components/lesson/LessonSimulationPanel.vue', 'terminationReason', 'simulation termination reason must be rendered');
expectContains('src/components/lesson/LessonSimulationPanel.vue', 'event.attempt', 'simulation attempts must be rendered');
expectContains('src/components/lesson/LessonSimulationPanel.vue', 'event.action', 'simulation actions must be rendered in trace order');
expectContains('src/views/LessonEditor.vue', 'invalidatePreview', 'all authoring mutations must invalidate stale preview');
expectContains('src/views/LessonEditor.vue', 'acceptSimulationEvidence', 'parent proof state must reject stale simulation evidence');
expectContains('src/components/lesson/LessonSimulationPanel.vue', 'beforeDestroy', 'destroyed simulation panels must cancel pending callbacks');
expectContains('src/components/lesson/RobotLessonPreview.vue', 'ResizeObserver', 'responsive preview needs a non-container-query resize path');
expectContains('src/views/LessonEditor.vue', 'beforeDestroy', 'destroyed editors must invalidate proof request tokens');
expectRegex('src/views/LessonEditor.vue', /@click="doPublish"[^>]*:disabled="assetMutating"/m, 'publish must lock during asset mutation');
expectContains('src/apis/nestHttp.js', 'status: r.status', 'upload HTTP errors must expose definitive status to mutation callers');
expectContains('src/apis/nestHttp.js', 'transport: true', 'upload transport failures must be marked ambiguous');

const editorSource = read('src/views/LessonEditor.vue');
let publishConfirmCalls = 0;
const guardedPublish = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'doPublish')})`, { Api: { lesson: {} } });
guardedPublish.call({ assetMutating: true, $confirm: () => { publishConfirmCalls += 1; } });
if (publishConfirmCalls !== 0) throw new Error('programmatic publish must reject active asset mutations');
const invalidatePreview = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'invalidatePreview')})`);
const acceptSimulationEvidence = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'acceptSimulationEvidence')})`);
const currentPreview = {
  checksum: 'preview-a', etag: 'etag-a', preview: { profile: 'espTft', width: 480, height: 320 }, manifest: { steps: [] },
};
const validSimulationResult = {
  ...currentPreview,
  simulation: {
    terminated: true,
    terminationReason: 'lesson_completed',
    trace: [{ stepKey: 'lesson', action: 'lesson_completed' }],
  },
};
const simulationProofContext = {
  proofVersion: 4, simulationEvidence: null, previewManifest: currentPreview,
  previewIdentityMatches(result, preview) {
    return Boolean(result && result.checksum === preview.checksum && result.etag === preview.etag
      && result.preview && result.preview.profile === preview.preview.profile
      && result.preview.width === preview.preview.width && result.preview.height === preview.preview.height);
  },
  validSimulationEvidence(result) {
    return Boolean(result && result.simulation && typeof result.simulation.terminated === 'boolean'
      && result.simulation.terminationReason === 'lesson_completed' && Array.isArray(result.simulation.trace));
  },
};
acceptSimulationEvidence.call(simulationProofContext, validSimulationResult, 3);
if (simulationProofContext.simulationEvidence) throw new Error('stale simulation evidence must not repopulate parent proof');
acceptSimulationEvidence.call(simulationProofContext, { checksum: 'preview-a', etag: 'etag-a', simulation: { trace: [] } }, 4);
if (simulationProofContext.simulationEvidence) throw new Error('simulation evidence missing preview identity must be rejected');
acceptSimulationEvidence.call(simulationProofContext, validSimulationResult, 4);
if (simulationProofContext.simulationEvidence.checksum !== 'preview-a') throw new Error('current simulation evidence must be accepted');
acceptSimulationEvidence.call(simulationProofContext, null, 4);
if (simulationProofContext.simulationEvidence) throw new Error('rerunning simulation must clear previous evidence');
let previewRequest;
const doPreview = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'doPreview')})`, {
  Api: { lesson: { manifestPreview: (...args) => { previewRequest = args; } } },
  setPreviewRequest: (args) => { previewRequest = args; },
});
const proofContext = {
  lessonId: 'lesson-1',
  proofVersion: 0,
  previewRequestId: 0,
  previewing: false,
  preview: null,
  previewManifest: null,
  simulationEvidence: null,
  invalidatePreview,
  hasUnsafeProofState: () => false,
  $message: { error() {} },
};
doPreview.call(proofContext);
const stalePreviewSuccess = previewRequest[2];
invalidatePreview.call(proofContext);
stalePreviewSuccess({
  checksum: 'stale-checksum',
  etag: 'stale-etag',
  preview: { profile: 'espTft', width: 480, height: 320 },
  manifest: { steps: [] },
});
if (proofContext.preview || proofContext.previewManifest || proofContext.simulationEvidence) {
  throw new Error('a preview response started before a mutation must not repopulate proof');
}

const hasUnsafeProofState = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'hasUnsafeProofState')})`);
for (const dirtyPatch of [
  { durationPreset: 8 },
  { interaction: { maxAttempts: 4 } },
  { teachingWord: { text: 'SEED' } },
  { storyBeat: { goal: 'Rescue the seed' } },
  { motion: { correct: 'celebrate' } },
  { selectedAssetDrafts: { offTab: { assetKey: 'teachingObject.seed' } } },
]) {
  const unsafe = hasUnsafeProofState.call({
    dirtyStepKeys: { offTab: true }, promptDirty: false, savingStep: false, rebindingSharedVisual: false,
    reordering: false, addingStep: false, renaming: false, assetMutating: false, ...dirtyPatch,
  });
  if (!unsafe) throw new Error('any dirty step, including an off-tab authoring change, must block proof generation');
}
let guardedPreviewCalls = 0;
const guardedPreview = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'doPreview')})`, {
  Api: { lesson: { manifestPreview: () => { guardedPreviewCalls += 1; } } },
});
guardedPreview.call({ hasUnsafeProofState: () => true, proofActionsDisabled: true });
if (guardedPreviewCalls !== 0) throw new Error('programmatic preview calls must reject unsafe dirty state');
const validManifestPreviewResponse = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'validManifestPreviewResponse')})`);
if (validManifestPreviewResponse.call({}, {
  checksum: 'c', etag: 'e', manifest: { steps: [] }, preview: { profile: 'espTft', width: 320, height: 240 },
})) throw new Error('preview proof must reject non-authoritative espTft dimensions');
const malformedPreviewContext = {
  lessonId: 'lesson-1', proofVersion: 0, previewRequestId: 0, previewing: false,
  preview: { checksum: 'valid-checksum' }, previewManifest: currentPreview, simulationEvidence: { checksum: 'valid' },
  hasUnsafeProofState: () => false, validManifestPreviewResponse,
  $message: { error(message) { malformedPreviewContext.errorMessage = message; } },
};
doPreview.call(malformedPreviewContext);
previewRequest[2]({});
if (malformedPreviewContext.previewing || !malformedPreviewContext.preview || !malformedPreviewContext.errorMessage) {
  throw new Error('malformed preview success must clear loading, preserve prior preview, and surface an error');
}
if (malformedPreviewContext.simulationEvidence) throw new Error('preview regeneration must invalidate prior simulation evidence');

for (const methodName of [
  'onPromptInput',
  'selectSharedAsset',
  'saveSelectedStep',
  'moveStep',
  'deleteStep',
  'addStep',
  'doRename',
  'rebindClonedVisual',
]) {
  const method = extractObjectMethod(editorSource, methodName);
  if (!method.includes('invalidatePreview')) {
    throw new Error(`${methodName} must invalidate preview and simulation evidence`);
  }
}

for (const [methodName, apiCall] of [
  ['moveStep', 'Api.lesson.reorderSteps('],
  ['deleteStep', 'Api.lesson.deleteStep('],
  ['addStep', 'Api.lesson.createStep('],
  ['doRename', 'Api.lesson.updateLesson('],
]) {
  const method = extractObjectMethod(editorSource, methodName);
  if (method.indexOf('this.invalidatePreview();') < method.indexOf(apiCall)) {
    throw new Error(`${methodName} must invalidate only inside the server success boundary`);
  }
  if (!method.includes('this.handleUncertainMutationError')) {
    throw new Error(`${methodName} must reconcile ambiguous transport/server failures`);
  }
}

const isUncertainMutationError = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'isUncertainMutationError')})`, {
  isUncertainNestError(error) {
    const status = Number(error && (error.status ?? (error.response && error.response.status)));
    if (error && error.transport === true) return true;
    return !Number.isFinite(status) || status === 0 || status >= 500;
  },
});
if (isUncertainMutationError.call({}, { status: 400 }) || isUncertainMutationError.call({}, { status: 409 })) {
  throw new Error('validated HTTP 4xx responses are definitive rejections');
}
for (const uncertain of [{ status: 0 }, { status: 500 }, { response: { status: 503 } }, { message: 'timeout' }, null]) {
  if (!isUncertainMutationError.call({}, uncertain)) throw new Error('transport and ambiguous 5xx failures must be uncertain');
}
const handleUncertainMutationError = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'handleUncertainMutationError')})`);
let uncertainReconciles = 0;
const uncertainProof = {
  proofVersion: 3, previewRequestId: 4, preview: {}, previewManifest: {}, simulationEvidence: {},
  invalidatePreview, isUncertainMutationError, reconcile() { uncertainReconciles += 1; },
};
handleUncertainMutationError.call(uncertainProof, { status: 0 }, uncertainProof.reconcile);
if (uncertainProof.preview || uncertainReconciles !== 1) {
  throw new Error('uncertain mutation errors must invalidate proof and trigger reconciliation');
}

function persistedProof(overrides = {}) {
  return {
    proofVersion: 8,
    previewRequestId: 12,
    previewing: false,
    preview: { checksum: 'valid-checksum' },
    previewManifest: { checksum: 'valid-checksum' },
    simulationEvidence: { checksum: 'valid-checksum' },
    invalidatePreview,
    hasUnsafeProofState: () => false,
    handleUncertainMutationError,
    isUncertainMutationError,
    $message: { success() {}, error() {}, warning() {} },
    $t: (key) => key,
    ...overrides,
  };
}

let reorderRequest;
const moveStep = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'moveStep')})`, {
  Api: { lesson: { reorderSteps: (...args) => { reorderRequest = args; } } },
});
const failedReorder = persistedProof({
  lessonId: 'lesson-1', reordering: false, steps: [{ stepKey: 's1' }, { stepKey: 's2' }],
});
moveStep.call(failedReorder, 0, 1);
if (!failedReorder.preview || failedReorder.proofVersion !== 8) throw new Error('pending reorder must preserve proof');
reorderRequest[3]('reorder failed', { status: 400 });
if (!failedReorder.preview || failedReorder.proofVersion !== 8) throw new Error('failed reorder must preserve proof');
const expiredReorder = persistedProof({
  lessonId: 'lesson-1', reordering: false, steps: [{ stepKey: 's1' }, { stepKey: 's2' }],
});
moveStep.call(expiredReorder, 0, 1);
reorderRequest[3]('session expired', { status: 401 });
if (expiredReorder.reordering || !expiredReorder.preview) {
  throw new Error('401 reorder rejection must clear loading while preserving trusted proof');
}
const uncertainReorder = persistedProof({
  lessonId: 'lesson-1', reordering: false, steps: [{ stepKey: 's1' }, { stepKey: 's2' }],
  fetchSteps() { this.reconciled = true; },
});
moveStep.call(uncertainReorder, 0, 1);
reorderRequest[3]('timeout', { status: 0 });
if (uncertainReorder.preview || !uncertainReorder.reconciled) throw new Error('uncertain reorder must invalidate and reconcile');

const successfulReorder = persistedProof({
  lessonId: 'lesson-1', reordering: false, steps: [{ stepKey: 's1' }, { stepKey: 's2' }],
});
doPreview.call(successfulReorder);
const preReorderPreviewSuccess = previewRequest[2];
moveStep.call(successfulReorder, 0, 1);
reorderRequest[2]([{ stepKey: 's2' }, { stepKey: 's1' }]);
if (successfulReorder.preview || successfulReorder.proofVersion !== 10 || successfulReorder.steps[0].stepKey !== 's2') {
  throw new Error('successful reorder must invalidate before applying authoritative order');
}
preReorderPreviewSuccess({
  checksum: 'stale', etag: 'stale', preview: { profile: 'espTft', width: 480, height: 320 }, manifest: { steps: [] },
});
if (successfulReorder.preview) throw new Error('pre-reorder preview callback must stay stale after confirmed reorder');

let deleteStepRequest;
const deleteStep = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'deleteStep')})`, {
  Api: { lesson: { deleteStep: (...args) => { deleteStepRequest = args; } } },
});
function confirmedDeleteContext() {
  return persistedProof({
    lessonId: 'lesson-1', steps: [{ stepKey: 's1' }],
    $confirm: () => ({ then(fn) { fn(); return { catch() {} }; } }),
  });
}
const failedDelete = confirmedDeleteContext();
deleteStep.call(failedDelete, { stepKey: 's1' });
if (!failedDelete.preview || failedDelete.proofVersion !== 8) throw new Error('pending delete must preserve proof');
deleteStepRequest[3]('delete failed', { status: 400 });
if (!failedDelete.preview || failedDelete.proofVersion !== 8) throw new Error('failed delete must preserve proof');
const expiredDelete = confirmedDeleteContext();
deleteStep.call(expiredDelete, { stepKey: 's1' });
deleteStepRequest[3]('session expired', { status: 401 });
if (!expiredDelete.preview) throw new Error('401 delete rejection must settle while preserving proof');
const uncertainDelete = confirmedDeleteContext();
uncertainDelete.fetchSteps = function fetchSteps() { this.reconciled = true; };
deleteStep.call(uncertainDelete, { stepKey: 's1' });
deleteStepRequest[3]('connection lost', { status: 0 });
if (uncertainDelete.preview || !uncertainDelete.reconciled) throw new Error('uncertain delete must invalidate and reconcile');
const successfulDelete = confirmedDeleteContext();
deleteStep.call(successfulDelete, { stepKey: 's1' });
deleteStepRequest[2]([]);
if (successfulDelete.preview || successfulDelete.proofVersion !== 9 || successfulDelete.steps.length !== 0) {
  throw new Error('successful delete must invalidate before applying authoritative rows');
}

let createStepRequest;
const addStep = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'addStep')})`, {
  Api: { lesson: { createStep: (...args) => { createStepRequest = args; } } },
  mergeAuthoringFields: () => ({}),
});
function createStepContext() {
  return persistedProof({
    lessonId: 'lesson-1', addingStep: false, stepDialogVisible: true, lastSubject: '', isChoiceStep: false,
    stepForm: {
      stepType: 'listen', prompt: 'Say seed', subject: 'seed', helperText: '', l1TransferHint: '',
      choices: [], renderExpression: '', scene: { primaryWord: 'seed' },
    },
    buildScene: () => null,
    buildVocab: () => null,
    fetchSteps() { this.fetchAfterCreate = true; },
  });
}
const failedCreate = createStepContext();
addStep.call(failedCreate);
if (!failedCreate.preview || failedCreate.proofVersion !== 8) throw new Error('pending create must preserve proof');
createStepRequest[3]('create failed', { status: 400 });
if (!failedCreate.preview || failedCreate.proofVersion !== 8) throw new Error('failed create must preserve proof');
const expiredCreate = createStepContext();
addStep.call(expiredCreate);
createStepRequest[3]('session expired', { status: 401 });
if (expiredCreate.addingStep || !expiredCreate.preview) throw new Error('401 create rejection must clear loading and allow retry');
const uncertainCreate = createStepContext();
addStep.call(uncertainCreate);
createStepRequest[3]('connection lost', { status: 0 });
if (uncertainCreate.preview || !uncertainCreate.fetchAfterCreate) throw new Error('uncertain create must invalidate and reconcile');
const successfulCreate = createStepContext();
addStep.call(successfulCreate);
createStepRequest[2]({ stepKey: 's2' });
if (successfulCreate.preview || successfulCreate.proofVersion !== 9 || !successfulCreate.fetchAfterCreate) {
  throw new Error('successful create must invalidate before authoritative refetch');
}

let renameRequest;
const doRename = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'doRename')})`, {
  Api: { lesson: { updateLesson: (...args) => { renameRequest = args; } } },
});
function renameContext() {
  return persistedProof({ lessonId: 'lesson-1', titleDraft: 'New title', renaming: false, renameVisible: true, lesson: { title: 'Old' } });
}
const failedRename = renameContext();
doRename.call(failedRename);
if (!failedRename.preview || failedRename.proofVersion !== 8) throw new Error('pending rename must preserve proof');
renameRequest[3]('rename failed', { status: 400 });
if (!failedRename.preview || failedRename.proofVersion !== 8) throw new Error('failed rename must preserve proof');
const expiredRename = renameContext();
doRename.call(expiredRename);
renameRequest[3]('session expired', { status: 401 });
if (expiredRename.renaming || !expiredRename.preview) throw new Error('401 rename rejection must clear loading and allow retry');
const uncertainRename = renameContext();
uncertainRename.fetchAll = function fetchAll() { this.reconciled = true; };
doRename.call(uncertainRename);
renameRequest[3]('server unavailable', { status: 503 });
if (uncertainRename.preview || !uncertainRename.reconciled) throw new Error('uncertain rename must invalidate and reconcile');
const successfulRename = renameContext();
doRename.call(successfulRename);
renameRequest[2]({ title: 'New title' });
if (successfulRename.preview || successfulRename.proofVersion !== 9 || successfulRename.lesson.title !== 'New title') {
  throw new Error('successful rename must invalidate before applying authoritative lesson');
}

const selectedAuthoringSetter = editorSource.slice(
  editorSource.indexOf('selectedAuthoring:'),
  editorSource.indexOf('selectedObjectKey()', editorSource.indexOf('selectedAuthoring:')),
);
if (!selectedAuthoringSetter.includes('invalidatePreview')) {
  throw new Error('duration, teaching word, story, fun pattern, and motion edits must invalidate proof');
}

const simulationSource = read('src/components/lesson/LessonSimulationPanel.vue');
const previewIdentity = vm.runInNewContext(`(${extractObjectMethod(simulationSource, 'previewIdentity')})`);
const samePreviewIdentity = vm.runInNewContext(`(${extractObjectMethod(simulationSource, 'samePreviewIdentity')})`);
const identityA = previewIdentity.call({}, currentPreview);
if (!samePreviewIdentity.call({}, identityA, previewIdentity.call({}, currentPreview))) {
  throw new Error('matching preview identity must permit simulation evidence');
}
if (samePreviewIdentity.call({}, identityA, previewIdentity.call({}, { ...currentPreview, checksum: 'preview-b' }))) {
  throw new Error('simulation A must be rejected after preview B replaces it');
}
if (previewIdentity.call({}, { checksum: 'preview-a', etag: 'etag-a' })) {
  throw new Error('preview identity requires server profile and dimensions');
}
const buildSimulationPayload = vm.runInNewContext(`(${extractObjectMethod(simulationSource, 'buildSimulationPayload')})`, {
  BRANCH_ACTIONS: {
    correct: 'advance', near_miss: 'advance', brave_try: 'advance',
    incorrect: 'retry', retry: 'retry', timeout: 'fallback',
  },
});
const maxAttemptsFor = vm.runInNewContext(`(${extractObjectMethod(simulationSource, 'maxAttemptsFor')})`);
const outcomesFor = vm.runInNewContext(`(${extractObjectMethod(simulationSource, 'outcomesFor')})`);
const simulationContext = {
  manifestSteps: [
    { id: 'passive', completionClass: 'passive' },
    { id: 'voice', completionClass: 'interactive', interaction: { maxAttempts: 3 } },
  ],
  activePreset: 'retry-then-correct',
  steps: [{ stepKey: 'voice', stepBody: { interaction: { maxAttempts: 3 } } }],
  maxAttemptsFor,
  outcomesFor,
};
const simulationPayload = buildSimulationPayload.call(simulationContext);
if (JSON.stringify(simulationPayload.outcomes) !== JSON.stringify({ voice: ['retry', 'correct'] })) {
  throw new Error('retry-then-correct must send the deterministic backend outcome sequence');
}
if (simulationPayload.projection.steps.voice.maxAttempts !== 3
  || simulationPayload.projection.steps.voice.on.timeout !== 'fallback') {
  throw new Error('simulation payload must include the complete safe-speaking branch projection');
}
let simulationRequest;
const runSimulation = vm.runInNewContext(`(${extractObjectMethod(simulationSource, 'runSimulation')})`, {
  Api: { lesson: { simulate: (...args) => { simulationRequest = args; } } },
});
let guardedSimulationCalls = 0;
const guardedSimulation = vm.runInNewContext(`(${extractObjectMethod(simulationSource, 'runSimulation')})`, {
  Api: { lesson: { simulate: () => { guardedSimulationCalls += 1; } } },
});
guardedSimulation.call({ disabled: true, running: false });
if (guardedSimulationCalls !== 0) throw new Error('programmatic simulation calls must reject unsafe dirty state');
function simulationRunContext() {
  const events = [];
  return {
    lessonId: 'lesson-1', disabled: false, running: false, requestId: 0, proofVersion: 4,
    manifestPreview: currentPreview, errorMessage: '', previewIdentity, samePreviewIdentity,
    validSimulationEvidence(result) {
      return Boolean(result && result.preview && result.simulation && typeof result.simulation.terminated === 'boolean');
    },
    buildSimulationPayload: () => simulationPayload,
    $emit: (...args) => events.push(args), events,
  };
}
const previewRaceSimulation = simulationRunContext();
runSimulation.call(previewRaceSimulation);
previewRaceSimulation.manifestPreview = { ...currentPreview, checksum: 'preview-b', etag: 'etag-b' };
simulationRequest[2](validSimulationResult);
if (previewRaceSimulation.events.some(([event, value]) => event === 'evidence' && value)) {
  throw new Error('simulation A arriving after preview B must not become evidence');
}
const malformedSimulation = simulationRunContext();
runSimulation.call(malformedSimulation);
simulationRequest[2]({ checksum: 'preview-a', etag: 'etag-a', simulation: { terminated: true, trace: [] } });
if (malformedSimulation.events.some(([event, value]) => event === 'evidence' && value) || !malformedSimulation.errorMessage) {
  throw new Error('simulation response missing preview identity must be rejected safely');
}

const robotPreviewSource = read('src/components/lesson/RobotLessonPreview.vue');
const previewScaleForWidth = vm.runInNewContext(`(${extractObjectMethod(robotPreviewSource, 'previewScaleForWidth')})`);
const narrowScale = previewScaleForWidth.call({}, 320);
if (Math.abs(narrowScale - (2 / 3)) > 0.0001) {
  throw new Error('320px preview container must fully scale the 480px stage to approximately 2/3');
}
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
expectContains('src/components/LessonAssetManager.vue', "this.$emit('asset-mutated'", 'successful asset writes must invalidate proof independently of reload');
expectContains('src/components/LessonAssetManager.vue', "this.$emit('asset-mutation-uncertain'", 'ambiguous asset writes must invalidate proof and reconcile');
expectContains('src/views/LessonEditor.vue', '@asset-mutated="onAssetMutated"', 'the editor must subscribe to committed asset mutations');
expectContains('src/views/LessonEditor.vue', '@asset-mutation-uncertain="onAssetMutationUncertain"', 'the editor must subscribe to ambiguous asset mutations');
expectContains('src/views/LessonEditor.vue', '@asset-mutation-detached="onAssetMutationDetached"', 'the editor must reconcile active mutations detached during unmount');
expectContains('src/views/LessonEditor.vue', ':mutation-settler="settleAssetMutation"', 'asset request settlement must remain parent-owned after child teardown');
expectContains('src/views/LessonEditor.vue', ':disabled="savingStep || rebindingSharedVisual || assetMutating"', 'asset manager must lock while any asset mutation token is active');
expectRegex(
  'src/views/LessonEditor.vue',
  /intent\.intent\s*===\s*'select'[\s\S]*?bindClonedAssetToStep\(step\.stepBody\s*\|\|\s*\{\}/m,
  'picker clone must bind the selected clone rather than search for the clicked source key',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /invalidatePreview\(\)[\s\S]*?Api\.lesson\.updateStep\([\s\S]*?step\.stepKey[\s\S]*?fetchSteps[\s\S]*?refreshSharedVisualTruth/m,
  'clone rebind must invalidate proof before mutation, then refetch authoritative truth after confirmation',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /refreshSharedVisualTruth\([\s\S]*?reloadAssets\(done, fail\)[\s\S]*?doValidate\(done, fail\)[\s\S]*?doPreview\(done, fail,/m,
  'server-confirmed clone rebind must refetch validation and manifest preview',
);
const cloneRebindSource = extractObjectMethod(editorSource, 'rebindClonedVisual');
const cloneRebindOrder = [
  'this.invalidatePreview();',
  'Api.lesson.updateStep(',
  'this.fetchSteps({',
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
  simulationEvidence: {},
  proofVersion: 0,
  previewRequestId: 0,
  invalidatePreview,
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

const assetManagerSource = read('src/components/LessonAssetManager.vue');
expectContains('src/components/LessonAssetManager.vue', 'nextAssetMutationId', 'asset mutation ids must be unique across component remounts');
expectContains('src/components/LessonAssetManager.vue', "this.$emit('asset-mutation-detached'", 'unmounting an active mutation must notify the parent');
let testMutationSequence = 0;
const beginAssetMutation = vm.runInNewContext(`(${extractObjectMethod(assetManagerSource, 'beginMutation')})`, {
  nextAssetMutationId: () => `asset-test-${testMutationSequence += 1}`,
});
const finishAssetMutation = vm.runInNewContext(`(${extractObjectMethod(assetManagerSource, 'finishMutation')})`);
const mutationEvents = [];
const assetSingleFlight = {
  disabled: false, mutationPending: false, mutationSequence: 0, activeMutationId: null,
  $emit: (...args) => mutationEvents.push(args),
};
const firstMutationId = beginAssetMutation.call(assetSingleFlight);
if (!firstMutationId || beginAssetMutation.call(assetSingleFlight) !== null) {
  throw new Error('asset manager must reject overlapping mutation attempts');
}
if (finishAssetMutation.call(assetSingleFlight, 'stale-id') || !assetSingleFlight.mutationPending) {
  throw new Error('stale asset completion must not clear a newer pending mutation');
}
if (!finishAssetMutation.call(assetSingleFlight, firstMutationId) || assetSingleFlight.mutationPending) {
  throw new Error('matching asset completion must release single-flight state');
}

let globalMutationSequence = 0;
const globallyUniqueBegin = vm.runInNewContext(`(${extractObjectMethod(assetManagerSource, 'beginMutation')})`, {
  nextAssetMutationId: () => `asset-global-${globalMutationSequence += 1}`,
});
const remountedA = { disabled: false, mutationPending: false, activeMutationId: null, $emit() {} };
const remountedB = { disabled: false, mutationPending: false, activeMutationId: null, $emit() {} };
const remountIdA = globallyUniqueBegin.call(remountedA);
const remountIdB = globallyUniqueBegin.call(remountedB);
if (remountIdA === remountIdB) throw new Error('asset mutation ids must not collide after remount');

const detachAssetMutation = vm.runInNewContext(`(${extractObjectMethod(assetManagerSource, 'detachActiveMutation')})`);
const createMutationSettlement = vm.runInNewContext(`(${extractObjectMethod(assetManagerSource, 'createMutationSettlement')})`);
const detachedEvents = [];
const detachingChild = {
  mutationPending: true, activeMutationId: remountIdA, uploading: true,
  $emit: (...args) => detachedEvents.push(args),
};
detachAssetMutation.call(detachingChild);
if (detachingChild.mutationPending || detachingChild.activeMutationId
  || !detachedEvents.some(([event, payload]) => event === 'asset-mutation-detached' && payload.id === remountIdA)) {
  throw new Error('active mutation teardown must detach its exact token as uncertain');
}
if (finishAssetMutation.call(detachingChild, remountIdA)) {
  throw new Error('late completion from a detached instance must be ignored');
}
const parentSettlements = [];
const settlementOwner = { mutationSettler: (payload) => parentSettlements.push(payload) };
const settleDetachedRequest = createMutationSettlement.call(settlementOwner, remountIdA);
settleDetachedRequest('success', { assetKey: 'teachingObject.seed' });
settleDetachedRequest('success', { assetKey: 'duplicate' });
if (parentSettlements.length !== 1 || parentSettlements[0].id !== remountIdA) {
  throw new Error('detached request callback must settle its parent token exactly once');
}

const onAssetMutationState = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'onAssetMutationState')})`);
const parentMutationTokens = {
  assetMutationTokens: {},
  $set(target, key, value) { target[key] = value; },
  $delete(target, key) { delete target[key]; },
};
onAssetMutationState.call(parentMutationTokens, { id: 'old', active: true });
onAssetMutationState.call(parentMutationTokens, { id: 'new', active: true });
onAssetMutationState.call(parentMutationTokens, { id: 'old', active: false });
if (!parentMutationTokens.assetMutationTokens.new || Object.keys(parentMutationTokens.assetMutationTokens).length !== 1) {
  throw new Error('older asset completion must not clear a newer parent mutation token');
}
onAssetMutationState.call(parentMutationTokens, { id: 'new', active: false });
if (Object.keys(parentMutationTokens.assetMutationTokens).length) {
  throw new Error('preview and simulation may unlock only after every mutation token completes');
}

const onAssetMutationDetached = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'onAssetMutationDetached')})`);
const detachedParent = {
  lessonId: 'lesson-1', assetMutationTokens: { [remountIdA]: true, [remountIdB]: true },
  proofVersion: 1, previewRequestId: 1, preview: {}, previewManifest: {}, simulationEvidence: {},
  $set(target, key, value) { target[key] = value; }, $delete(target, key) { delete target[key]; },
  $message: { error() {} },
};
onAssetMutationDetached.call(detachedParent, { id: remountIdA });
if (!detachedParent.preview || !detachedParent.assetMutationTokens[remountIdA] || !detachedParent.assetMutationTokens[remountIdB]) {
  throw new Error('detach must keep proof locked by the token without a premature reconciliation read');
}

let terminalAssetRead;
const settleAssetMutation = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'settleAssetMutation')})`, {
  Api: { lesson: { listAssets: (...args) => { terminalAssetRead = args; } } },
});
detachedParent.invalidatePreview = invalidatePreview;
detachedParent.onAssetsLoaded = function onAssetsLoaded() { this.assetsReconciled = true; };
settleAssetMutation.call(detachedParent, { id: remountIdA, outcome: 'success' });
if (detachedParent.preview || !detachedParent.assetMutationTokens[remountIdA] || !terminalAssetRead) {
  throw new Error('terminal success must invalidate proof and reconcile before unlocking its token');
}
terminalAssetRead[2]({ assets: [] });
if (detachedParent.assetMutationTokens[remountIdA] || !detachedParent.assetMutationTokens[remountIdB] || !detachedParent.assetsReconciled) {
  throw new Error('post-terminal reconciliation must clear only its matching token');
}
onAssetMutationState.call(detachedParent, { id: remountIdB, active: false });
if (Object.keys(detachedParent.assetMutationTokens).length) {
  throw new Error('parent must not remain permanently disabled after detached reconciliation and newer completion');
}
const rejectedDetachedParent = {
  ...detachedParent, assetMutationTokens: { rejected: true }, preview: { checksum: 'still-valid' },
};
terminalAssetRead = null;
settleAssetMutation.call(rejectedDetachedParent, { id: 'rejected', outcome: 'rejected', error: { status: 401 } });
if (Object.keys(rejectedDetachedParent.assetMutationTokens).length || !rejectedDetachedParent.preview || terminalAssetRead) {
  throw new Error('definitive terminal rejection must unlock without invalidating proof or reading assets');
}
const invalidSettlementParent = {
  ...detachedParent, assetMutationTokens: { invalid: true }, preview: { checksum: 'still-valid' },
};
terminalAssetRead = null;
if (settleAssetMutation.call(invalidSettlementParent, { id: 'invalid', outcome: 'unknown' })
  || invalidSettlementParent.assetMutationTokens.invalid !== true
  || !invalidSettlementParent.preview
  || terminalAssetRead) {
  throw new Error('invalid asset outcomes must not strand, unlock, or reconcile the active token');
}
const timeoutDetachedParent = {
  ...detachedParent, assetMutationTokens: { timeout: true }, preview: { checksum: 'unsafe' }, assetsReconciled: false,
};
settleAssetMutation.call(timeoutDetachedParent, { id: 'timeout', outcome: 'uncertain', error: { status: 0 } });
if (timeoutDetachedParent.preview || !timeoutDetachedParent.assetMutationTokens.timeout || !terminalAssetRead) {
  throw new Error('terminal timeout must invalidate and keep locked through reconciliation');
}
terminalAssetRead[3]('reload failed');
if (Object.keys(timeoutDetachedParent.assetMutationTokens).length) {
  throw new Error('terminal timeout reconciliation failure must still release only its token');
}
const handleAssetMutationError = vm.runInNewContext(`(${extractObjectMethod(assetManagerSource, 'handleMutationError')})`, {
  isUncertainNestError(error) {
    const status = Number(error && (error.status ?? (error.response && error.response.status)));
    if (error && error.transport === true) return true;
    return !Number.isFinite(status) || status === 0 || status >= 500;
  },
});
const uploadAssetSource = extractObjectMethod(assetManagerSource, 'uploadAsset');
const uploadMutationEventIndex = uploadAssetSource.indexOf("this.$emit('asset-mutated'");
const uploadSettlementIndex = uploadAssetSource.indexOf("settleMutation('success'");
if (uploadMutationEventIndex === -1 || uploadSettlementIndex === -1 || uploadSettlementIndex > uploadMutationEventIndex) {
  throw new Error('successful upload/replace must settle the parent token before notifying consumers');
}
if (uploadAssetSource.includes('this.reload()')) throw new Error('upload/replace must leave authoritative reload to parent settlement');
if (!uploadAssetSource.includes('handleMutationError')) throw new Error('upload/replace errors must classify uncertain commits');
if (!uploadAssetSource.includes('beginMutation')) throw new Error('upload/replace must enter child single-flight state');
if (!extractObjectMethod(assetManagerSource, 'onDelete').includes('handleMutationError')) {
  throw new Error('delete errors must classify uncertain commits');
}
if (!extractObjectMethod(assetManagerSource, 'onDelete').includes('beginMutation')) {
  throw new Error('delete must enter child single-flight state');
}
const onAssetMutated = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'onAssetMutated')})`);
const assetMutationParent = {
  proofVersion: 2,
  previewRequestId: 4,
  previewing: false,
  preview: { checksum: 'old' },
  previewManifest: { checksum: 'old' },
  simulationEvidence: { checksum: 'old' },
  invalidatePreview,
};
const mutationSettlements = [];
let deleteRequest;
const deleteAsset = vm.runInNewContext(`(${extractObjectMethod(assetManagerSource, 'onDelete')})`, {
  Api: { lesson: { deleteAsset: (...args) => { deleteRequest = args; } } },
  isUncertainNestError(error) {
    const status = Number(error && (error.status ?? (error.response && error.response.status)));
    return !Number.isFinite(status) || status === 0 || status >= 500;
  },
});
const assetMutationChild = {
  lessonId: 'lesson-1',
  disabled: false,
  mutationPending: false,
  mutationSequence: 0,
  activeMutationId: null,
  beginMutation: beginAssetMutation,
  finishMutation: finishAssetMutation,
  createMutationSettlement,
  mutationSettler(payload) {
    mutationSettlements.push(payload);
    if (payload.outcome === 'success' || payload.outcome === 'uncertain') onAssetMutated.call(assetMutationParent);
  },
  $t: (key) => key,
  $message: { success() {}, error() {} },
  handleMutationError: handleAssetMutationError,
  $emit(event, payload) {
    if (event === 'asset-mutated') onAssetMutated.call(assetMutationParent, payload);
  },
};
const detachedCallbackSettlements = [];
let detachedCallbackEvents = 0;
let detachedCallbackMessages = 0;
const detachedDeleteChild = {
  ...assetMutationChild,
  mutationPending: false,
  activeMutationId: null,
  detachActiveMutation: detachAssetMutation,
  mutationSettler(payload) { detachedCallbackSettlements.push(payload); },
  $emit(event) { if (event === 'asset-mutated') detachedCallbackEvents += 1; },
  $message: { success() { detachedCallbackMessages += 1; }, error() {} },
};
deleteAsset.call(detachedDeleteChild, { assetKey: 'teachingObject.seed', profile: 'espTft' });
detachAssetMutation.call(detachedDeleteChild);
deleteRequest[3]();
if (detachedCallbackSettlements.length !== 1 || detachedCallbackSettlements[0].outcome !== 'success') {
  throw new Error('detached delete completion must still settle its parent token');
}
if (detachedCallbackEvents || detachedCallbackMessages) {
  throw new Error('detached delete completion must not update its stale child instance');
}
deleteAsset.call(assetMutationChild, { assetKey: 'teachingObject.seed', profile: 'espTft' });
deleteRequest[3]();
if (assetMutationParent.preview || assetMutationParent.previewManifest || assetMutationParent.simulationEvidence) {
  throw new Error('successful delete settlement must invalidate proof');
}
if (mutationSettlements.at(-1)?.outcome !== 'success') throw new Error('successful delete must settle its parent token');

const failedMutationParent = { ...assetMutationParent, preview: { checksum: 'current' } };
const failedMutationChild = {
  ...assetMutationChild,
  mutationSettler(payload) { mutationSettlements.push(payload); },
  $emit(event, payload) {
    if (event === 'asset-mutated') onAssetMutated.call(failedMutationParent, payload);
  },
};
deleteAsset.call(failedMutationChild, { assetKey: 'teachingObject.seed', profile: 'espTft' });
deleteRequest[4]('delete failed', { status: 400 });
if (!failedMutationParent.preview) throw new Error('failed asset mutation must not invalidate server proof');
if (mutationSettlements.at(-1)?.outcome !== 'rejected') throw new Error('definitive asset failure must reject its parent token');

const expiredAssetDelete = { ...assetMutationChild, mutationPending: false, activeMutationId: null };
deleteAsset.call(expiredAssetDelete, { assetKey: 'teachingObject.seed', profile: 'espTft' });
if (!expiredAssetDelete.mutationPending) throw new Error('asset delete must latch pending state before request');
deleteRequest[4]('session expired', { status: 401 });
if (expiredAssetDelete.mutationPending || expiredAssetDelete.activeMutationId) {
  throw new Error('401 asset delete rejection must release single-flight state for retry');
}

let uploadRequest;
const uploadAsset = vm.runInNewContext(`(${uploadAssetSource})`, {
  Api: { lesson: { uploadAsset: (...args) => { uploadRequest = args; } } },
  ROLE_BY_LAYER: { teachingObject: 'primarySubject' },
  isUncertainNestError(error) {
    const status = Number(error && (error.status ?? (error.response && error.response.status)));
    return !Number.isFinite(status) || status === 0 || status >= 500;
  },
});
const expiredAssetUpload = {
  ...assetMutationChild,
  mutationPending: false,
  activeMutationId: null,
  pickedFile: { name: 'seed.png' },
  uploading: false,
  layer: 'teachingObject', role: 'primarySubject', assetKey: 'teachingObject.seed', critical: true, replaceMode: false,
  $refs: {},
};
uploadAsset.call(expiredAssetUpload);
if (!expiredAssetUpload.uploading || !expiredAssetUpload.mutationPending) {
  throw new Error('asset upload must latch loading and mutation state before request');
}
uploadRequest[4]('session expired', { status: 401 });
if (expiredAssetUpload.uploading || expiredAssetUpload.mutationPending) {
  throw new Error('401 asset upload rejection must release loading and allow retry');
}
expiredAssetUpload.pickedFile = { name: 'seed.png' };
uploadAsset.call(expiredAssetUpload);
if (!expiredAssetUpload.mutationPending) throw new Error('asset upload must be retryable after re-auth rejection settles');

let uncertainAssetEvents = 0;
const uncertainMutationChild = {
  ...assetMutationChild,
  mutationSettler(payload) {
    mutationSettlements.push(payload);
    if (payload.outcome === 'uncertain') onAssetMutated.call(failedMutationParent);
  },
  $emit(event) {
    if (event === 'asset-mutation-uncertain') {
      uncertainAssetEvents += 1;
      onAssetMutated.call(failedMutationParent);
    }
  },
};
deleteAsset.call(uncertainMutationChild, { assetKey: 'teachingObject.seed', profile: 'espTft' });
deleteRequest[4]('network timeout', { status: 0 });
if (uncertainAssetEvents !== 1 || mutationSettlements.at(-1)?.outcome !== 'uncertain' || failedMutationParent.preview) {
  throw new Error('uncertain asset delete must invalidate proof through parent settlement');
}

const assetProofFingerprint = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'buildAssetProofFingerprint')})`);
const baseAsset = {
  assetId: 'asset-1', profile: 'espTft', assetKey: 'teachingObject.seed', sha256: 'same-sha',
  layer: 'teachingObject', role: 'primarySubject', critical: true, mediaType: 'image/png',
  bytes: 12, width: 4, height: 3, url: '/seed.png',
};
const baseFingerprint = assetProofFingerprint.call({}, [baseAsset]);
for (const changed of [
  { ...baseAsset, role: 'supportSubject' },
  { ...baseAsset, critical: false },
  { ...baseAsset, layer: 'backgroundScene' },
]) {
  if (assetProofFingerprint.call({}, [changed]) === baseFingerprint) {
    throw new Error('manifest-relevant asset metadata changes must invalidate the fingerprint');
  }
}
if (assetProofFingerprint.call({}, [baseAsset, { ...baseAsset, assetId: 'asset-2', assetKey: 'robotOverlay.teach' }])
  !== assetProofFingerprint.call({}, [{ ...baseAsset, assetId: 'asset-2', assetKey: 'robotOverlay.teach' }, baseAsset])) {
  throw new Error('asset proof fingerprint must be deterministic across response ordering');
}
const onAssetsLoaded = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'onAssetsLoaded')})`);
let metadataInvalidations = 0;
const metadataContext = {
  assetProofFingerprint: baseFingerprint,
  assetRefreshIsProofRecovery: false,
  bundleAssets: [baseAsset],
  buildAssetProofFingerprint: assetProofFingerprint,
  invalidatePreview() { metadataInvalidations += 1; },
};
onAssetsLoaded.call(metadataContext, [{ ...baseAsset, role: 'supportSubject' }]);
if (metadataInvalidations !== 1) {
  throw new Error('same-key/same-sha metadata changes must invalidate existing proof');
}

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
