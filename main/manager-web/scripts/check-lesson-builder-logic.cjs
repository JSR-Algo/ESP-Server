const assert = require('assert');
const {
  buildEngagementTrack,
  calculateReadiness,
  createAuthoringFields,
  createInitialAuthoringFields,
  DURATION_PRESETS,
  NAMED_MOTIONS,
  mergeAuthoringFields,
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

const initial = createInitialAuthoringFields({
  teachingWord: ' barn ',
  prompt: ' Help Pip find the barn. ',
  subject: ' barn ',
});
assert.strictEqual(initial.teachingWord.text, 'BARN');
assert.deepStrictEqual(initial.storyBeat, {
  goal: 'Help Pip find the barn.',
  successReaction: 'Celebrate learning barn.',
  nextTease: 'What will we discover about barn next?',
});

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
const readiness = calculateReadiness({ steps, assets, validation: { budgets: { espTft: { errors: [], warnings: [], metrics: { assetCount: 3, uniqueAssetCount: 2, sharedAssetCount: 1, packBytes: 180000, estimatedVisualPeakBytes: 620800, offlineReady: true, allPathsTerminate: true } } } } });
assert.strictEqual(readiness.downloadBytes, 180000);
assert.strictEqual(readiness.uniqueAssetCount, 2);
assert.strictEqual(readiness.sharedReferenceCount, 1);
assert.strictEqual(readiness.estimatedPeakPsram, 620800);
assert.strictEqual(readiness.offlineReady, true);
assert.strictEqual(readiness.allPathsTerminate, true);

const unsafe = calculateReadiness({
  steps: [{ stepKey: 's1', stepType: 'repeat', stepBody: { interaction: { template: 'safeSpeaking' } } }],
  assets: [{ assetKey: 'remote', src: 'https://cdn.invalid/a.png', bytes: 12 }],
  validation: { budgets: { espTft: { errors: [{ code: 'branch-termination' }], warnings: [], metrics: { assetCount: 1, packBytes: 12, estimatedVisualPeakBytes: 100, offlineReady: false } } } },
});
assert.strictEqual(unsafe.offlineReady, false);
assert.strictEqual(unsafe.allPathsTerminate, false);
const unknown = calculateReadiness({ steps, assets });
assert.strictEqual(unknown.estimateOnly, true);
assert.strictEqual(unknown.offlineReady, false);
assert.strictEqual(unknown.allPathsTerminate, false);
const dimensionEstimate = calculateReadiness({ assets: [{ assetKey: 'rgba', width: 100, height: 50, hasAlpha: true }] });
assert.strictEqual(dimensionEstimate.estimatedPeakPsram, 20000);

console.log('lesson builder logic checks passed');
