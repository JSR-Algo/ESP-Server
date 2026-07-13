const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {
  buildEngagementTrack,
  bindClonedAssetToStep,
  calculateReadiness,
  collectAssetReferences,
  createAuthoringFields,
  DURATION_PRESETS,
  NAMED_MOTIONS,
  mergeAuthoringFields,
  nextClonedAssetKey,
  replaceStepAssetReference,
  stepReferencesAssetInLayer,
} = require('../src/components/lesson/lesson-builder-logic');

const fields = createAuthoringFields();
assert.strictEqual(fields.durationPreset, 5);
assert.strictEqual(fields.interaction.template, 'safeSpeaking');
assert.strictEqual(fields.interaction.funPattern, 'copyMyMove');
assert.strictEqual(fields.motion.correct, 'celebrate');
assert.deepStrictEqual(DURATION_PRESETS, [3, 5, 8]);
assert.ok(['teach', 'listen', 'celebrate', 'encourage', 'tryAgain'].every((motion) => NAMED_MOTIONS.includes(motion)));
assert.strictEqual(mergeAuthoringFields({}, { durationPreset: 7 }).durationPreset, 5);
assert.strictEqual(mergeAuthoringFields({}, { motion: { correct: 'servo:180' } }).motion.correct, 'celebrate');

const edited = mergeAuthoringFields({}, {
  teachingWord: { text: 'BARN' },
  interaction: { funPattern: 'miniStoryRescue' },
  storyBeat: { goal: 'Help Pip find a home' },
  motion: { listen: 'listen', nearMiss: 'encourage' },
});
assert.strictEqual(edited.teachingWord.text, 'BARN');
assert.strictEqual(edited.interaction.template, 'safeSpeaking');
assert.strictEqual(edited.interaction.funPattern, 'miniStoryRescue');
assert.strictEqual(edited.storyBeat.goal, 'Help Pip find a home');

const steps = [
  { stepKey: 's1', stepType: 'greeting', stepBody: { durationSec: 8 } },
  { stepKey: 's2', stepType: 'repeat', stepBody: { durationSec: 14, interaction: { ...edited.interaction, funPattern: 'copyMyMove' }, motion: edited.motion } },
  { stepKey: 's3', stepType: 'repeat', stepBody: { durationSec: 9, interaction: { template: 'safeSpeaking', funPattern: 'whisperThenLoud' } } },
  { stepKey: 's4', stepType: 'fillBlank', stepBody: { durationSec: 12, interaction: { template: 'safeSpeaking', funPattern: 'sillyChoice' } } },
  { stepKey: 's5', stepType: 'review', stepBody: { durationSec: 15, recall: true } },
  { stepKey: 's6', stepType: 'celebrate', stepBody: { durationSec: 8, ending: true } },
];
const track = buildEngagementTrack(steps);
assert.deepStrictEqual(track.map((item) => item.kind), ['passive', 'motion', 'voice', 'minigame', 'recall', 'ending']);
assert.strictEqual(track[1].hasMotion, true);

const assets = [
  { assetKey: 'scene.farm', sha256: 'a', bytes: 120000, decodedBytes: 460800 },
  { assetKey: 'scene.farm', sha256: 'a', bytes: 120000, decodedBytes: 460800 },
  { assetKey: 'object.barn', sha256: 'b', bytes: 60000, decodedBytes: 160000 },
];
const readiness = calculateReadiness({ steps, assets, manifest: { pathsTerminate: true } });
assert.strictEqual(readiness.downloadBytes, 180000);
assert.strictEqual(readiness.uniqueAssetCount, 2);
assert.strictEqual(readiness.sharedReferenceCount, 1);
assert.strictEqual(readiness.estimatedPeakPsram, 620800);
assert.strictEqual(readiness.offlineReady, true);
assert.strictEqual(readiness.allPathsTerminate, true);

const unsafe = calculateReadiness({
  steps: [{ stepKey: 's1', stepType: 'repeat', stepBody: { interaction: { template: 'safeSpeaking' } } }],
  assets: [{ assetKey: 'remote', src: 'https://cdn.invalid/a.png', bytes: 12 }],
  manifest: { pathsTerminate: false },
});
assert.strictEqual(unsafe.offlineReady, false);
assert.strictEqual(unsafe.allPathsTerminate, false);

const referencedAssetKey = 'teachingObject.glowSeed.v1';
const referenceSteps = [
  {
    stepKey: 's1',
    stepBody: { scene: { background: { assetKey: 'scene.forest.v1' } } },
  },
  {
    stepKey: 's2',
    stepBody: { scene: { teachingObject: { assetKey: referencedAssetKey } } },
  },
  {
    stepId: 'step-id-3',
    visual: { foreground: { assetKey: referencedAssetKey } },
  },
  {
    stepKey: 's4',
    body: { overlays: [{ assetKey: 'sparkle.v1' }, { assetKey: referencedAssetKey }] },
  },
  {
    stepKey: 's5',
    stepBody: { scene: null },
  },
];
assert.deepStrictEqual(
  collectAssetReferences(referenceSteps, referencedAssetKey),
  ['s2', 's4'],
);
assert.deepStrictEqual(collectAssetReferences(referenceSteps, 'missing.v1'), []);
assert.deepStrictEqual(collectAssetReferences(null, referencedAssetKey), []);

const clonedAssets = [
  { assetKey: 'teachingObject.glowSeed.v1' },
  { assetKey: 'teachingObject.glowSeed.v2' },
  { assetKey: 'teachingObject.glowSeed.v3' },
  { assetKey: 'character.owl.v7' },
  { assetKey: 'character.owl.v8' },
];
assert.strictEqual(
  nextClonedAssetKey('scene.forest', clonedAssets),
  'scene.forest.v2',
);
assert.strictEqual(
  nextClonedAssetKey('character.owl.v7', clonedAssets),
  'character.owl.v9',
);
assert.strictEqual(
  nextClonedAssetKey('teachingObject.glowSeed.v1', clonedAssets),
  'teachingObject.glowSeed.v4',
);
assert.strictEqual(
  nextClonedAssetKey('scene.forest.v1', [
    { assetKey: 'scene.forest.v1' },
    { assetKey: 'scene.forest.v3' },
    { assetKey: 'scene.desert.v12' },
  ]),
  'scene.forest.v4',
);
assert.strictEqual(
  nextClonedAssetKey('scene.forest.v1', [{ assetKey: 'scene.desert.v12' }]),
  'scene.forest.v2',
);

const cyclicBody = {};
cyclicBody.self = cyclicBody;
assert.throws(
  () => collectAssetReferences([{ stepKey: 'cycle', stepBody: cyclicBody }], referencedAssetKey),
  { name: 'TypeError', message: 'step body must be an acyclic JSON tree' },
);
class CustomBody {
  constructor() {
    this.assetKey = referencedAssetKey;
  }
}
assert.throws(
  () => collectAssetReferences([{ stepKey: 'custom', stepBody: new CustomBody() }], referencedAssetKey),
  { name: 'TypeError', message: 'step body must contain only JSON primitives, arrays, and plain objects' },
);

const originalBody = {
  scene: {
    background: { assetKey: 'scene.forest.v1', opacity: 0.7 },
    layers: [
      { kind: 'teachingObject', assetId: 'asset-old', assetKey: referencedAssetKey, path: '/old.png', sha256: 'old-sha', x: 24 },
      { kind: 'caption', text: 'Glow seed' },
    ],
  },
  fallback: { assetKey: referencedAssetKey, role: 'fallback' },
};
const clonedAsset = {
  assetId: 'asset-clone',
  assetKey: 'teachingObject.glowSeed.v2',
  path: '/clone.png',
  sha256: 'clone-sha',
};
const originalSnapshot = JSON.parse(JSON.stringify(originalBody));
const replacedBody = replaceStepAssetReference(originalBody, referencedAssetKey, clonedAsset);
assert.deepStrictEqual(replacedBody, {
  scene: {
    background: { assetKey: 'scene.forest.v1', opacity: 0.7 },
    layers: [
      { kind: 'teachingObject', assetId: 'asset-clone', assetKey: 'teachingObject.glowSeed.v2', path: '/clone.png', sha256: 'clone-sha', x: 24 },
      { kind: 'caption', text: 'Glow seed' },
    ],
  },
  fallback: { assetId: 'asset-clone', assetKey: 'teachingObject.glowSeed.v2', path: '/clone.png', sha256: 'clone-sha', role: 'fallback' },
});
assert.deepStrictEqual(originalBody, originalSnapshot);
assert.notStrictEqual(replacedBody, originalBody);
assert.notStrictEqual(replacedBody.scene, originalBody.scene);
assert.notStrictEqual(replacedBody.scene.layers, originalBody.scene.layers);

const realSchemaBody = {
  teachingObject: {
    primaryWord: 'SEED',
    asset: { key: referencedAssetKey, src: '/old.png', sha256: 'old-sha', fit: 'contain' },
  },
  robotOverlay: {
    asset: { key: 'robotOverlay.teach', src: '/robot.png', sha256: 'robot-sha' },
  },
  choiceMetadata: { key: referencedAssetKey, label: 'must not be treated as an asset' },
};
assert.deepStrictEqual(
  collectAssetReferences([{ stepKey: 'real-s2', stepBody: realSchemaBody }], referencedAssetKey),
  ['real-s2'],
);
assert.deepStrictEqual(replaceStepAssetReference(realSchemaBody, referencedAssetKey, clonedAsset), {
  teachingObject: {
    primaryWord: 'SEED',
    asset: { key: clonedAsset.assetKey, src: clonedAsset.path, sha256: clonedAsset.sha256, fit: 'contain', assetId: clonedAsset.assetId },
  },
  robotOverlay: {
    asset: { key: 'robotOverlay.teach', src: '/robot.png', sha256: 'robot-sha' },
  },
  choiceMetadata: { key: referencedAssetKey, label: 'must not be treated as an asset' },
});
const selectedDifferentAsset = bindClonedAssetToStep(realSchemaBody, {
  intent: 'select',
  layer: 'teachingObject',
  boundAssetKey: referencedAssetKey,
}, {
  assetId: 'asset-clone-b',
  assetKey: 'teachingObject.selectedB.v2',
  path: '/selected-b.png',
  sha256: 'selected-b-sha',
});
assert.strictEqual(selectedDifferentAsset.teachingObject.asset.key, 'teachingObject.selectedB.v2');
assert.strictEqual(selectedDifferentAsset.teachingObject.asset.src, '/selected-b.png');
assert.strictEqual(selectedDifferentAsset.choiceMetadata.key, referencedAssetKey);
assert.strictEqual(stepReferencesAssetInLayer(realSchemaBody, referencedAssetKey, 'teachingObject'), true);
assert.strictEqual(stepReferencesAssetInLayer(realSchemaBody, referencedAssetKey, 'backgroundScene'), false);

const backgroundKey = 'backgroundScene.moonGarden.v1';
const backgroundClone = {
  assetId: 'background-clone',
  assetKey: 'backgroundScene.moonGarden.v2',
  path: '/background-clone.png',
  sha256: 'background-clone-sha',
};
const realBackgroundBody = {
  backgroundScene: {
    mode: 'poster',
    poster: { key: backgroundKey, src: '/background.png', fit: 'cover', sha256: 'background-sha' },
    altCaption: 'Moon garden',
  },
  analyticsMetadata: { key: backgroundKey, event: 'background-viewed' },
};
assert.strictEqual(stepReferencesAssetInLayer(realBackgroundBody, backgroundKey, 'backgroundScene'), true);
assert.strictEqual(stepReferencesAssetInLayer(realBackgroundBody, backgroundKey, 'teachingObject'), false);
assert.deepStrictEqual(
  collectAssetReferences([{ stepKey: 'background-s1', stepBody: realBackgroundBody }], backgroundKey),
  ['background-s1'],
);
assert.deepStrictEqual(replaceStepAssetReference(realBackgroundBody, backgroundKey, backgroundClone), {
  backgroundScene: {
    mode: 'poster',
    poster: {
      key: backgroundClone.assetKey,
      src: backgroundClone.path,
      fit: 'cover',
      sha256: backgroundClone.sha256,
      assetId: backgroundClone.assetId,
    },
    altCaption: 'Moon garden',
  },
  analyticsMetadata: { key: backgroundKey, event: 'background-viewed' },
});

const cyclicArray = [];
cyclicArray.push(cyclicArray);
assert.throws(
  () => replaceStepAssetReference(cyclicArray, referencedAssetKey, clonedAsset),
  { name: 'TypeError', message: 'step body must be an acyclic JSON tree' },
);
assert.throws(
  () => replaceStepAssetReference(new CustomBody(), referencedAssetKey, clonedAsset),
  { name: 'TypeError', message: 'step body must contain only JSON primitives, arrays, and plain objects' },
);
assert.throws(
  () => replaceStepAssetReference(originalBody, referencedAssetKey, new CustomBody()),
  { name: 'TypeError', message: 'cloned asset must contain only JSON primitives, arrays, and plain objects' },
);

const lessonApiSource = fs.readFileSync(
  path.join(__dirname, '../src/apis/module/lesson.js'),
  'utf8',
);
const executableLessonApiSource = lessonApiSource
  .replace(/^import \{ getNestUrl \} from '\.\.\/api';$/m, '')
  .replace(/import \{[\s\S]*?\} from '\.\.\/nestHttp';/, '')
  .replace('export default {', 'const lessonApi = {')
  .concat('\nmodule.exports = lessonApi;\n');
const apiRequests = [];
const apiSandbox = {
  module: { exports: {} },
  exports: {},
  getNestUrl: () => '/v1/admin',
  nestRequest: (request) => apiRequests.push(request),
  nestUpload: () => {},
  normalizeLesson: (value) => value,
  normalizeStep: (value) => value,
  normalizeStepType: (value) => value,
};
vm.runInNewContext(executableLessonApiSource, apiSandbox, { filename: 'lesson.js' });
const lessonApi = apiSandbox.module.exports;
const onSuccess = () => {};
const onError = () => {};

assert.strictEqual(typeof lessonApi.reviewSharedVisualImpact, 'function');
lessonApi.reviewSharedVisualImpact('asset-7', onSuccess, onError);
assert.strictEqual(apiRequests[0].url, '/v1/admin/assets/asset-7/impact');
assert.strictEqual(apiRequests[0].method, 'GET');
assert.strictEqual(apiRequests[0].onSuccess, onSuccess);
assert.strictEqual(apiRequests[0].onError, onError);

const clonePayload = { profile: 'espTft', assetKey: 'teachingObject.glowSeed.v2' };
assert.strictEqual(typeof lessonApi.cloneSharedVisual, 'function');
lessonApi.cloneSharedVisual('lesson-3', 'asset-7', clonePayload, onSuccess, onError);
assert.strictEqual(apiRequests[1].url, '/v1/admin/lessons/lesson-3/assets/asset-7/clone');
assert.strictEqual(apiRequests[1].method, 'POST');
assert.strictEqual(apiRequests[1].data, clonePayload);
assert.strictEqual(apiRequests[1].onSuccess, onSuccess);
assert.strictEqual(apiRequests[1].onError, onError);

const simulationPayload = { startStepKey: 's2', answers: ['wrong', 'correct'] };
assert.strictEqual(typeof lessonApi.simulate, 'function');
lessonApi.simulate('lesson-3', simulationPayload, onSuccess, onError);
assert.strictEqual(apiRequests[2].url, '/v1/admin/lessons/lesson-3/simulate?profile=espTft');
assert.strictEqual(apiRequests[2].method, 'POST');
assert.strictEqual(apiRequests[2].data, simulationPayload);
assert.strictEqual(apiRequests[2].onSuccess, onSuccess);
assert.strictEqual(apiRequests[2].onError, onError);
assert.strictEqual(apiRequests.length, 3);

console.log('lesson builder logic checks passed');
