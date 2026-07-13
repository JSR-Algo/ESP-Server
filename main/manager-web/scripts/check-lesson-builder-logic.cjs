const assert = require('assert');
const {
  buildEngagementTrack,
  calculateReadiness,
  collectAssetReferences,
  createAuthoringFields,
  DURATION_PRESETS,
  NAMED_MOTIONS,
  mergeAuthoringFields,
  nextClonedAssetKey,
  replaceStepAssetReference,
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
    stepBody: { overlays: [{ assetKey: 'sparkle.v1' }, { assetKey: referencedAssetKey }] },
  },
  {
    stepKey: 's5',
    stepBody: { scene: null },
  },
];
assert.deepStrictEqual(
  collectAssetReferences(referenceSteps, referencedAssetKey),
  ['s2', 'step-id-3', 's4'],
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

console.log('lesson builder logic checks passed');
