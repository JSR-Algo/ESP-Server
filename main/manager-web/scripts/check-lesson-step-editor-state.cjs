const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {
  addChoice,
  buildCreateStepPayload,
  buildSaveStepRequest,
  createLessonStepEditorState,
  createStepDialogState,
  detectStepSaveConflict,
  removeChoice,
  resolveSaveSuccess,
  stepConcurrencyFingerprint,
} = require('../src/components/lesson/lesson-step-editor-state');

const state = createLessonStepEditorState();
assert.deepStrictEqual(state, {
  selectedStepIndex: 0,
  dialogVisible: false,
  form: expectBlankForm(),
  correctChoiceId: '',
  adding: false,
  reordering: false,
  authoringDrafts: {},
  contentDrafts: {},
  assetDrafts: {},
  dirtyKeys: {},
  savingKeys: {},
  draftRevisions: {},
  baselineFingerprints: {},
});

const dialog = createStepDialogState({
  stepTypes: [{ stepType: 'repeat' }],
  lastSubject: 'barn',
});
assert.equal(dialog.visible, true);
assert.equal(dialog.correctChoiceId, 'c1');
assert.equal(dialog.form.stepType, 'repeat');
assert.equal(dialog.form.subject, 'barn');
assert.equal(dialog.form.vocab.word, 'barn');
assert.equal(dialog.form.scene.primaryWord, 'barn');
assert.deepStrictEqual(dialog.form.choices.map((choice) => choice.id), ['c1', 'c2']);

const withChoice = addChoice(dialog.form);
assert.deepStrictEqual(withChoice.choices.map((choice) => choice.id), ['c1', 'c2', 'c3']);
const removed = removeChoice(withChoice, 0, 'c1');
assert.equal(removed.correctChoiceId, 'c2');
assert.deepStrictEqual(removed.form.choices.map((choice) => choice.id), ['c2', 'c3']);
const readded = addChoice(removed.form);
assert.deepStrictEqual(readded.choices.map((choice) => choice.id), ['c2', 'c3', 'c1']);
assert.equal(new Set(readded.choices.map((choice) => choice.id)).size, readded.choices.length);

const assets = [
  { assetKey: 'scene.farm', layer: 'backgroundScene', path: '/farm.jpg', sha256: 'bg' },
  { assetKey: 'object.barn', layer: 'teachingObject', path: '/barn.png', sha256: 'obj' },
  { assetKey: 'robotOverlay.teach', path: '/teach.png', sha256: 'robot' },
];
dialog.form.prompt = 'Say barn';
dialog.form.renderExpression = 'teaching';
dialog.form.scene.backgroundKey = 'scene.farm';
dialog.form.scene.objectKey = 'object.barn';
dialog.form.vocab.ipa = '/bɑːrn/';
const created = buildCreateStepPayload({
  form: dialog.form,
  correctChoiceId: dialog.correctChoiceId,
  assets,
  locale: 'vi',
});
assert.equal(created.ok, true);
assert.equal(created.payload.stepBody.backgroundScene.poster.key, 'scene.farm');
assert.equal(created.payload.stepBody.teachingObject.asset.key, 'object.barn');
assert.equal(created.payload.stepBody.robotOverlay.asset.key, 'robotOverlay.teach');
assert.equal(created.payload.stepBody.vocab.ipa, '/bɑːrn/');
assert.equal(created.payload.stepBody.teachingWord.text, 'BARN');
assert.deepStrictEqual(created.payload.renderOverride, { expression: 'teaching' });

const fillBlank = createStepDialogState({ stepTypes: [{ stepType: 'fillBlank' }] });
fillBlank.form.prompt = 'A __ is red';
fillBlank.form.subject = 'barn';
fillBlank.form.choices[0].label = 'barn';
assert.deepStrictEqual(buildCreateStepPayload({
  form: fillBlank.form,
  correctChoiceId: 'c1',
  assets: [],
  locale: 'vi',
}), { ok: false, reason: 'fillBlankNeedsChoices' });

const step = {
  stepKey: 's2',
  prompt: 'Old',
  stepBody: { existing: true },
  visualRefs: [
    { slot: 'backgroundScene', assetVersionId: 'scene-v3' },
    { slot: 'teachingObject', assetVersionId: 'asset-v1' },
    { slot: 'robotOverlay', assetVersionId: 'robot-v4' },
  ],
};
const request = buildSaveStepRequest({
  step,
  authoring: { teachingWord: { text: 'BARN' } },
  content: { prompt: 'New' },
  selectedAsset: { versionId: 'asset-v2' },
  savedRevision: 2,
});
assert.equal(request.stepKey, 's2');
assert.equal(request.savedRevision, 2);
assert.deepStrictEqual(request.payload.visualRefs, [
  { slot: 'backgroundScene', assetVersionId: 'scene-v3' },
  { slot: 'teachingObject', assetVersionId: 'asset-v2' },
  { slot: 'robotOverlay', assetVersionId: 'robot-v4' },
]);
assert.deepStrictEqual(request.payload.stepBody, { existing: true, teachingWord: { text: 'BARN' } });

assert.deepStrictEqual(resolveSaveSuccess({ currentRevision: 2, savedRevision: 2 }), { clearDraft: true });
assert.deepStrictEqual(resolveSaveSuccess({ currentRevision: 3, savedRevision: 2 }), { clearDraft: false });

// T4.1 — PATCH /steps/:stepKey carries no version token, so a second editor's
// save used to land as a silent overwrite. The fingerprint is the concurrency
// token the wire lacks.
const serverStep = {
  stepKey: 's2',
  stepType: 'listen',
  prompt: 'Say barn',
  subject: 'barn',
  helperText: '',
  l1TransferHint: '',
  choices: null,
  stepBody: { teachingWord: { text: 'BARN' }, durationPreset: 5 },
  visualRefs: [{ slot: 'teachingObject', assetVersionId: 'asset-v1' }],
};
const baselineFingerprint = stepConcurrencyFingerprint(serverStep);
assert.ok(baselineFingerprint);
// Key order must not matter: two reads of the same jsonb row must agree.
assert.strictEqual(
  stepConcurrencyFingerprint({
    ...serverStep,
    stepBody: { durationPreset: 5, teachingWord: { text: 'BARN' } },
  }),
  baselineFingerprint,
);
// Unrelated response fields must not read as someone else's edit.
assert.strictEqual(
  stepConcurrencyFingerprint({ ...serverStep, phase: 'talk', entrance: 'flyIn' }),
  baselineFingerprint,
);
assert.strictEqual(stepConcurrencyFingerprint(null), '');
assert.strictEqual(stepConcurrencyFingerprint([]), '');

assert.deepStrictEqual(
  detectStepSaveConflict({ baselineFingerprint, serverStep }),
  { conflict: false, reason: 'unchanged', currentFingerprint: baselineFingerprint },
);
// Another editor changed the body.
const rivalBody = { ...serverStep, stepBody: { teachingWord: { text: 'COW' }, durationPreset: 5 } };
const bodyConflict = detectStepSaveConflict({ baselineFingerprint, serverStep: rivalBody });
assert.strictEqual(bodyConflict.conflict, true);
assert.strictEqual(bodyConflict.reason, 'step-changed');
assert.notStrictEqual(bodyConflict.currentFingerprint, baselineFingerprint);
// Another editor rebound the visual.
const rivalRefs = { ...serverStep, visualRefs: [{ slot: 'teachingObject', assetVersionId: 'asset-v9' }] };
assert.strictEqual(detectStepSaveConflict({ baselineFingerprint, serverStep: rivalRefs }).reason, 'step-changed');
// Another editor changed only the prompt.
assert.strictEqual(
  detectStepSaveConflict({ baselineFingerprint, serverStep: { ...serverStep, prompt: 'Say cow' } }).reason,
  'step-changed',
);
// Another editor deleted the step.
assert.deepStrictEqual(
  detectStepSaveConflict({ baselineFingerprint, serverStep: undefined }),
  { conflict: true, reason: 'step-removed', currentFingerprint: '' },
);
// No baseline (step never read) must not block the save.
assert.strictEqual(detectStepSaveConflict({ baselineFingerprint: '', serverStep }).conflict, false);
assert.strictEqual(detectStepSaveConflict().conflict, false);

// T4.1 — the detector has to gate the operator's save paths, not just exist.
const editorSource = fs.readFileSync(path.join(__dirname, '..', 'src/views/LessonEditor.vue'), 'utf8');
// Baselines follow server reads, so the editor's own commits (rebind, visual
// pair, reorder — each re-fetches) never read back as another editor's change.
assert.match(editorSource, /this\.rebaselineStepFingerprints\(rows\);/);
assert.match(
  editorSource,
  /rebaselineStepFingerprints\(rows\)\s*\{[\s\S]*?stepConcurrencyFingerprint\(row\)/,
);
assert.match(
  editorSource,
  /guardStepSaveConflict\(stepKey, proceed, onBlocked\)[\s\S]*?Api\.lesson\.listSteps\([\s\S]*?detectStepSaveConflict\(/,
  'the guard must re-read server truth immediately before writing',
);
// No baseline (step never read) must fall through instead of blocking.
assert.match(editorSource, /if \(!baselineFingerprint\)\s*\{\s*proceed\(\);/);
const guardedSaves = editorSource.match(/this\.guardStepSaveConflict\(step\.stepKey,/g) || [];
assert.strictEqual(guardedSaves.length, 2, 'both step-save entry points must be guarded');
assert.match(editorSource, /requestSelectedStepSave\(\)\s*\{[\s\S]*?this\.saveSelectedStep\(promptSnapshot\);/);
assert.match(editorSource, /requestSelectedStepStudioSave\(\)\s*\{[\s\S]*?this\.saveSelectedStepStudio\(\);/);
// The button must call the guarded wrapper, never the raw save.
assert.match(editorSource, /@click="requestSelectedStepSave"/);
assert.ok(
  !/@click="saveSelectedStep"/.test(editorSource),
  'the step save button must not bypass the concurrency guard',
);
// The pre-check must own its own busy flag: the save methods abort on
// savingStep/savingStepKeys, so reusing those would swallow the save.
assert.match(editorSource, /stepConflictChecking: false,/);
assert.match(editorSource, /:disabled="savingStep \|\| stepConflictChecking/);
assert.match(editorSource, /\|\| this\.stepConflictChecking/, 'proof state must treat the pre-check as unsafe');
assert.match(editorSource, /lesson\.stepSaveConflictChanged/);
assert.match(editorSource, /lesson\.stepSaveConflictRemoved/);
// A failed pre-check must report and stop, never fall through to the write.
assert.match(editorSource, /lesson\.stepSaveConflictCheckFailed/);
// Every operator-facing message must exist in both shipped locales.
const englishMessages = fs.readFileSync(path.join(__dirname, '..', 'src/i18n/en.js'), 'utf8');
const vietnameseMessages = fs.readFileSync(path.join(__dirname, '..', 'src/i18n/vi.js'), 'utf8');
for (const key of [
  'lesson.stepSaveConflictChanged',
  'lesson.stepSaveConflictRemoved',
  'lesson.stepSaveConflictCheckFailed',
  'lesson.assetDeleteInUse',
  'lesson.unsavedLeaveTitle',
  'lesson.unsavedLeaveBody',
  'lesson.unsavedLeaveDiscard',
  'lesson.unsavedLeaveStay',
]) {
  assert.ok(englishMessages.includes(`'${key}':`), `en.js must define ${key}`);
  assert.ok(vietnameseMessages.includes(`'${key}':`), `vi.js must define ${key}`);
}
// Conflict copy must name the step and tell the operator what happened to the draft.
assert.match(englishMessages, /'lesson\.stepSaveConflictChanged':[^\n]*\{key\}/);
assert.match(vietnameseMessages, /'lesson\.stepSaveConflictChanged':[^\n]*\{key\}/);
assert.match(englishMessages, /'lesson\.assetDeleteInUse':[^\n]*\{assetKey\}[^\n]*\{steps\}/);
assert.match(vietnameseMessages, /'lesson\.assetDeleteInUse':[^\n]*\{assetKey\}[^\n]*\{steps\}/);

function expectBlankForm() {
  return {
    stepType: '', prompt: '', subject: '', helperText: '', l1TransferHint: '', choices: [],
    renderExpression: '',
    vocab: { word: '', ipa: '', partOfSpeech: '', translationVi: '', definition: '', examples: [] },
    scene: {
      backgroundKey: '', altCaption: '', fit: 'cover', objectKey: '', primaryWord: '',
      placementAnchor: 'center', supportWords: [], activeWindows: [], successUtterance: '',
      missUtterance: '', timeoutSec: 12,
    },
  };
}

// The conflict check inserts an async listSteps hop before the PATCH. promptDraft
// is not keyed by step, so a refetch landing in that window resets it — the save
// then PATCHed the pre-edit prompt back and the operator's edit vanished. The
// prompt must be captured at click time and passed through.
assert.match(
  editorSource,
  /const promptSnapshot = \{ stepKey: step\.stepKey, prompt: this\.promptDraft \};[\s\S]*?this\.saveSelectedStep\(promptSnapshot\);/,
  'requestSelectedStepSave must snapshot promptDraft before the conflict check',
);
assert.match(
  editorSource,
  /saveSelectedStep\(promptSnapshot = null\)/,
  'saveSelectedStep must accept the pre-captured prompt',
);
assert.match(
  editorSource,
  /prompt: promptValue,/,
  'the step PATCH must send the captured prompt, not a possibly-reset promptDraft',
);
assert.ok(
  !/prompt: this\.promptDraft,\n\s*stepBody,/.test(editorSource),
  'the step PATCH must not read promptDraft after the conflict check',
);

// An async step refetch must never discard an unsaved prompt edit for the step
// being edited; only the post-save sync may force server truth over the draft.
assert.match(
  editorSource,
  /resetPromptDraft\(step, \{ force = false \} = \{\}\) \{\s*\n\s*if \(!force && this\.promptDirty && step && step\.stepKey === this\.promptStepKey\)/,
  'resetPromptDraft must protect a dirty draft for the step still being edited',
);
assert.match(
  editorSource,
  /this\.resetPromptDraft\(step, \{ force: true \}\);/,
  'the post-save sync must force the prompt draft to server truth',
);

// Vue coerces an empty string on a Boolean prop to `true`, so a `:disabled`
// chain whose last operand is a string flag (`deletingStepKey` idles at '')
// evaluates to '' and permanently disables the control. This dead-locked
// "Add step", the step dialog's "Save", and "Delete step" — no step could be
// created in the studio at all. Keep such chains behind a Boolean() computed.
const stringValuedFlags = ['deletingStepKey', 'promptStepKey', 'selectedStepKey'];
const booleanPropBindings = /:(?:disabled|loading)="([^"]+)"/g;
for (const [binding, expression] of editorSource.matchAll(booleanPropBindings)) {
  const lastOperand = expression.split('||').pop().trim();
  assert.ok(
    !stringValuedFlags.includes(lastOperand),
    `${binding}: a Boolean prop must not end in the string flag \`${lastOperand}\` `
      + '(Vue coerces \'\' to true). Wrap the chain in a Boolean() computed.',
  );
}
assert.ok(
  /stepMutationBlocked\(\)\s*{\s*return Boolean\(/.test(editorSource),
  'LessonEditor must gate step-mutation controls through the Boolean() stepMutationBlocked computed',
);

console.log('lesson step editor state checks passed');
