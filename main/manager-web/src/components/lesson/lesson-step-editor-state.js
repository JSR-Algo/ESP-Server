const { createInitialAuthoringFields } = require('./lesson-builder-logic');

const EXPRESSION_BY_TYPE = Object.freeze({
  greeting: 'teaching', review: 'teaching', focus: 'teaching', model: 'teaching',
  listen: 'listening', repeat: 'listening', fillBlank: 'thinking', feedback: 'teaching',
  celebrate: 'celebrating',
});

const POSE_BY_EXPRESSION = Object.freeze({
  teaching: 'teach', listening: 'listening', thinking: 'thinking', celebrating: 'celebrate',
});

function createBlankStepForm() {
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

function createLessonStepEditorState() {
  return {
    selectedStepIndex: 0,
    dialogVisible: false,
    form: createBlankStepForm(),
    correctChoiceId: '',
    adding: false,
    reordering: false,
    authoringDrafts: {},
    contentDrafts: {},
    assetDrafts: {},
    dirtyKeys: {},
    savingKeys: {},
    draftRevisions: {},
  };
}

function createStepDialogState({ stepTypes = [], lastSubject = '' } = {}) {
  const form = createBlankStepForm();
  form.stepType = stepTypes.length ? stepTypes[0].stepType : '';
  form.subject = lastSubject;
  form.choices = [{ id: 'c1', label: '' }, { id: 'c2', label: '' }];
  form.vocab.word = lastSubject;
  form.scene.primaryWord = lastSubject;
  return { visible: true, form, correctChoiceId: 'c1' };
}

function addChoice(form) {
  const usedIds = new Set(form.choices.map((choice) => choice.id));
  let index = 1;
  while (usedIds.has(`c${index}`)) index += 1;
  return { ...form, choices: [...form.choices, { id: `c${index}`, label: '' }] };
}

function removeChoice(form, index, correctChoiceId) {
  const choices = form.choices.slice();
  const [removed] = choices.splice(index, 1);
  return {
    form: { ...form, choices },
    correctChoiceId: removed && removed.id === correctChoiceId
      ? (choices.length ? choices[0].id : '')
      : correctChoiceId,
  };
}

function assetByKey(assets, key) {
  return assets.find((asset) => asset.assetKey === key) || null;
}

function buildVocab(form, locale) {
  const subject = form.subject;
  const vocab = form.vocab;
  const word = (vocab.word || subject || '').trim();
  const out = {};
  if (word) out.word = word;
  if ((vocab.ipa || '').trim()) out.ipa = vocab.ipa.trim();
  if (vocab.partOfSpeech) out.partOfSpeech = vocab.partOfSpeech;
  const translation = (vocab.translationVi || '').trim();
  if (translation) out.translation = { [locale || 'vi']: translation };
  if ((vocab.definition || '').trim()) out.definition = vocab.definition.trim();
  const examples = (vocab.examples || [])
    .filter((example) => (example.text || '').trim())
    .map((example) => {
      const row = { text: example.text.trim() };
      if ((example.translation || '').trim()) row.translation = example.translation.trim();
      return row;
    });
  if (examples.length) out.examples = examples;
  return Object.keys(out).length ? out : null;
}

function buildScene(form, assets) {
  const subject = form.subject;
  const source = form.scene;
  const scene = {};
  const background = assetByKey(assets, source.backgroundKey);
  if (background) {
    scene.backgroundScene = {
      mode: 'poster',
      poster: { key: background.assetKey, src: background.path || background.url, fit: source.fit || 'cover', sha256: background.sha256 },
      video: null,
      altCaption: (source.altCaption || '').trim(),
    };
  }
  const object = assetByKey(assets, source.objectKey);
  if (object) {
    const teachingObject = {
      primaryWord: (source.primaryWord || subject || '').trim(),
      supportWords: Array.isArray(source.supportWords) ? source.supportWords.filter(Boolean) : [],
      placement: { anchor: source.placementAnchor || 'center', paddingTopPercent: 8 },
      asset: { key: object.assetKey, src: object.path || object.url, sha256: object.sha256 },
    };
    if (form.stepType === 'model' && source.activeWindows.length) {
      const clamp = (number) => Math.min(1, Math.max(0, Number(number) || 0));
      const windows = source.activeWindows.map((window) => ({
        tStart: Math.max(0, Number(window.tStart) || 0),
        tEnd: Math.max(0, Number(window.tEnd) || 0),
        x: clamp(window.x), y: clamp(window.y), w: clamp(window.w), h: clamp(window.h),
      })).filter((window) => window.tStart < window.tEnd);
      if (windows.length) {
        teachingObject.focusTarget = {
          activeWindows: windows,
          successUtterance: (source.successUtterance || '').trim(),
          missUtterance: (source.missUtterance || '').trim(),
        };
      }
    }
    scene.teachingObject = teachingObject;
  }
  if (!Object.keys(scene).length) return null;
  const expression = form.renderExpression || EXPRESSION_BY_TYPE[form.stepType] || 'teaching';
  const pose = POSE_BY_EXPRESSION[expression] || 'teach';
  const overlay = assetByKey(assets, `robotOverlay.${pose}`);
  const overlaySrc = overlay && (overlay.path || overlay.url);
  if (overlaySrc) {
    scene.robotOverlay = {
      robotState: pose === 'celebrate' ? 'celebrating' : (pose === 'listening' || pose === 'thinking' ? pose : 'talking'),
      pose,
      expression,
      anchor: 'bottomLeft',
      pivot: { x: 0.5, y: 1.0 },
      asset: { key: overlay.assetKey, src: overlaySrc, sha256: overlay.sha256 },
      atlas: { image: overlaySrc, cell: 0 },
      pointerEvents: 'none',
    };
  }
  scene.audio = { via: 'tts' };
  scene.timeoutSec = Number(source.timeoutSec) || 12;
  return scene;
}

function buildCreateStepPayload({ form, correctChoiceId, assets = [], locale = 'vi' }) {
  if (!form.stepType || !form.prompt || !form.subject) return { ok: false, reason: 'stepRequired' };
  const payload = {
    stepType: form.stepType,
    prompt: form.prompt,
    subject: form.subject,
    helperText: form.helperText,
    l1TransferHint: form.l1TransferHint,
  };
  if (form.stepType === 'fillBlank') {
    const choices = form.choices.filter((choice) => (choice.label || '').trim());
    if (choices.length < 2 || !choices.some((choice) => choice.id === correctChoiceId)) {
      return { ok: false, reason: 'fillBlankNeedsChoices' };
    }
    payload.choices = choices.map((choice, index) => ({
      id: choice.id || `c${index + 1}`,
      label: choice.label.trim(),
      isCorrect: choice.id === correctChoiceId,
    }));
  }
  const stepBody = {};
  const scene = buildScene(form, assets);
  if (scene) Object.assign(stepBody, scene);
  const vocab = buildVocab(form, locale);
  if (vocab) stepBody.vocab = vocab;
  Object.assign(stepBody, createInitialAuthoringFields({
    teachingWord: form.scene.primaryWord,
    prompt: form.prompt,
    subject: form.subject,
  }));
  if (Object.keys(stepBody).length) payload.stepBody = stepBody;
  if (form.renderExpression) payload.renderOverride = { expression: form.renderExpression };
  return { ok: true, payload };
}

function buildSaveStepRequest({ step, authoring, content, selectedAsset, savedRevision }) {
  const refsBySlot = new Map((Array.isArray(step.visualRefs) ? step.visualRefs : [])
    .filter((ref) => ref && ref.slot && (ref.assetVersionId || ref.asset_version_id))
    .map((ref) => [ref.slot, { slot: ref.slot, assetVersionId: ref.assetVersionId || ref.asset_version_id }]));
  if (selectedAsset && selectedAsset.versionId) {
    refsBySlot.set('teachingObject', { slot: 'teachingObject', assetVersionId: selectedAsset.versionId });
  }
  const slotOrder = ['backgroundScene', 'teachingObject', 'robotOverlay'];
  const visualRefs = [...refsBySlot.values()].sort((left, right) => slotOrder.indexOf(left.slot) - slotOrder.indexOf(right.slot));
  return {
    stepKey: step.stepKey,
    savedRevision,
    payload: {
      ...step,
      ...content,
      stepBody: { ...(step.stepBody || {}), ...authoring },
      visualRefs,
    },
  };
}

function resolveSaveSuccess({ currentRevision, savedRevision }) {
  return { clearDraft: Number(currentRevision || 0) === Number(savedRevision || 0) };
}

module.exports = {
  addChoice,
  buildCreateStepPayload,
  buildSaveStepRequest,
  createBlankStepForm,
  createLessonStepEditorState,
  createStepDialogState,
  removeChoice,
  resolveSaveSuccess,
};
