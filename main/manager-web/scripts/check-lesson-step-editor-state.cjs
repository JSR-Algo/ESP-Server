const assert = require('assert');
const {
  addChoice,
  buildCreateStepPayload,
  buildSaveStepRequest,
  createLessonStepEditorState,
  createStepDialogState,
  removeChoice,
  resolveSaveSuccess,
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

const step = { stepKey: 's2', prompt: 'Old', stepBody: { existing: true } };
const request = buildSaveStepRequest({
  step,
  authoring: { teachingWord: { text: 'BARN' } },
  content: { prompt: 'New' },
  selectedAsset: { versionId: 'asset-v2' },
  savedRevision: 2,
});
assert.equal(request.stepKey, 's2');
assert.equal(request.savedRevision, 2);
assert.deepStrictEqual(request.payload.visualRefs, [{ slot: 'teachingObject', assetVersionId: 'asset-v2' }]);
assert.deepStrictEqual(request.payload.stepBody, { existing: true, teachingWord: { text: 'BARN' } });

assert.deepStrictEqual(resolveSaveSuccess({ currentRevision: 2, savedRevision: 2 }), { clearDraft: true });
assert.deepStrictEqual(resolveSaveSuccess({ currentRevision: 3, savedRevision: 2 }), { clearDraft: false });

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

console.log('lesson step editor state checks passed');
