const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {
  assetDeletionImpact,
  buildEngagementTrack,
  bindClonedAssetToStep,
  calculateReadiness,
  clampTeachingWord,
  collectAssetReferences,
  TEACHING_WORD_MAX_VISIBLE_CHARS,
  teachingWordLengthIssue,
  visibleGraphemeCount,
  createAuthoringFields,
  createInitialAuthoringFields,
  DURATION_PRESETS,
  NAMED_MOTIONS,
  mergeAuthoringFields,
  nextClonedAssetKey,
  replaceStepAssetReference,
  validSimulationEvidence,
  stepReferencesAssetInLayer,
  courseModeActivityReport,
  isCourseModeAuthority,
  isProjectedCourseModeStep,
} = require('../src/components/lesson/lesson-builder-logic');

const protectedRecallActivity = {
  activityId: 'recall-1',
  targetIds: ['animals.cat'],
  stage: 'RECALL',
  expectedDurationSec: 60,
  answerPolicy: {
    targetTextVisible: true,
    targetAudioBeforeAssessment: false,
    spokenTargetInPrompt: false,
    multipleChoiceContainsTarget: false,
    minElapsedSinceFullModelMs: 20000,
    minInterveningActivityCount: 1,
  },
};
const courseModeContract = {
  contractVersion: 'courseCompanion.v2.contract.v1',
  activities: [
    { activityId: 'discover-1', stage: 'DISCOVER', expectedDurationSec: 425, answerPolicy: {} },
    protectedRecallActivity,
  ],
};
assert.strictEqual(isCourseModeAuthority({ courseModeContract }), true);
assert.strictEqual(isCourseModeAuthority({}, [{ stepBody: { authority: 'courseMode' } }]), true);
assert.strictEqual(isCourseModeAuthority({}, [], { steps: [{ authority: 'courseMode' }] }), true);
assert.strictEqual(isCourseModeAuthority({ manifestVersion: 'teebot-lesson-renderer.v5' }), false);
assert.strictEqual(isProjectedCourseModeStep({ stepBody: { authority: 'courseMode' } }), true);
assert.strictEqual(isProjectedCourseModeStep({ authority: 'courseMode' }), true);
assert.strictEqual(isProjectedCourseModeStep({ stepBody: { authority: 'legacy' } }), false);
assert.deepStrictEqual(courseModeActivityReport(courseModeContract), {
  totalSeconds: 485,
  overDuration: true,
  leakageActivityIds: ['recall-1'],
});
assert.deepStrictEqual(courseModeActivityReport({ activities: [{
  ...protectedRecallActivity,
  answerPolicy: { ...protectedRecallActivity.answerPolicy, targetTextVisible: false },
}] }), { totalSeconds: 60, overDuration: false, leakageActivityIds: [] });

const simulationIdentity = {
  checksum: 'checksum-a',
  etag: 'etag-a',
  preview: { profile: 'espTft', width: 480, height: 320 },
  manifest: {
    steps: [
      { id: 's1', type: 'greeting', completionClass: 'passive', timeoutSec: 10 },
      { id: 's2', type: 'listen', completionClass: 'interactive', timeoutSec: 20 },
    ],
  },
};
const simulationAuthoringSteps = [{ stepKey: 's2', stepBody: { interaction: { maxAttempts: 3 } } }];
const validSimulation = {
  ...simulationIdentity,
  simulation: {
    terminated: true,
    terminationReason: 'lesson_completed',
    trace: [
      { stepKey: 's1', stepType: 'greeting', completionClass: 'passive', timeoutSec: 10, action: 'auto_advance' },
      { stepKey: 's2', stepType: 'listen', completionClass: 'interactive', timeoutSec: 20, outcome: 'retry', attempt: 1, action: 'retry' },
      { stepKey: 's2', stepType: 'listen', completionClass: 'interactive', timeoutSec: 20, outcome: 'correct', attempt: 2, action: 'advance' },
      { stepKey: 'lesson', action: 'lesson_completed' },
    ],
  },
};
assert.strictEqual(validSimulationEvidence(validSimulation, simulationIdentity, simulationAuthoringSteps), true);
const passiveTrace = { stepKey: 's1', stepType: 'greeting', completionClass: 'passive', timeoutSec: 10, action: 'auto_advance' };
const completionTrace = { stepKey: 'lesson', action: 'lesson_completed' };
const interactiveTrace = (outcome, attempt, action) => ({
  stepKey: 's2', stepType: 'listen', completionClass: 'interactive', timeoutSec: 20,
  ...(outcome === undefined ? {} : { outcome }), attempt, action,
});
const legitimatePresetTraces = [
  [interactiveTrace('correct', 1, 'advance')],
  [interactiveTrace('near_miss', 1, 'advance')],
  [interactiveTrace('brave_try', 1, 'advance')],
  [interactiveTrace('incorrect', 1, 'retry'), interactiveTrace('incorrect', 2, 'retry'), interactiveTrace('incorrect', 3, 'fallback_advance')],
  [interactiveTrace('retry', 1, 'retry'), interactiveTrace('correct', 2, 'advance')],
  [interactiveTrace('timeout', 1, 'fallback_advance')],
  [interactiveTrace(undefined, 1, 'fallback_advance')],
];
legitimatePresetTraces.forEach((interactive, index) => {
  assert.strictEqual(validSimulationEvidence({
    ...simulationIdentity,
    simulation: { terminated: true, terminationReason: 'lesson_completed', trace: [passiveTrace, ...interactive, completionTrace] },
  }, simulationIdentity, simulationAuthoringSteps), true, `legitimate preset trace ${index} must be accepted`);
});
const impossibleBranchTraces = [
  [interactiveTrace('correct', 1, 'retry')],
  [interactiveTrace('timeout', 1, 'advance')],
  [interactiveTrace(undefined, 1, 'retry')],
  [interactiveTrace('incorrect', 1, 'fallback_advance')],
  [interactiveTrace('retry', 1, 'fallback_advance')],
  [interactiveTrace('incorrect', 1, 'retry'), interactiveTrace('incorrect', 2, 'retry'), interactiveTrace('incorrect', 3, 'retry')],
  [interactiveTrace('correct', 1, 'fallback_advance')],
];
impossibleBranchTraces.forEach((interactive, index) => {
  assert.strictEqual(validSimulationEvidence({
    ...simulationIdentity,
    simulation: { terminated: true, terminationReason: 'lesson_completed', trace: [passiveTrace, ...interactive, completionTrace] },
  }, simulationIdentity, simulationAuthoringSteps), false, `impossible outcome/action trace ${index} must be rejected`);
});
const loopingIdentity = {
  ...simulationIdentity,
  manifest: { steps: [{ id: 's2', type: 'listen', completionClass: 'interactive', timeoutSec: 20 }] },
};
const loopingAuthoringSteps = [{ stepKey: 's2', stepBody: { interaction: { maxAttempts: 200 } } }];
const loopingTraceFor = (length) => Array.from({ length }, (_, index) => interactiveTrace('retry', index + 1, 'retry'));
const loopingTrace = loopingTraceFor(100);
assert.strictEqual(validSimulationEvidence({
  ...loopingIdentity,
  simulation: { terminated: false, terminationReason: 'max_transitions', trace: loopingTrace },
}, loopingIdentity, loopingAuthoringSteps), true, 'max_transitions requires the exact fixed 100-event trace');
for (const length of [0, 99, 101]) {
  assert.strictEqual(validSimulationEvidence({
    ...loopingIdentity,
    simulation: { terminated: false, terminationReason: 'max_transitions', trace: loopingTraceFor(length) },
  }, loopingIdentity, loopingAuthoringSteps), false, `max_transitions trace length ${length} must be rejected`);
}
const malformedSimulations = [
  { ...validSimulation, simulation: { ...validSimulation.simulation, terminated: 'true' } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, terminationReason: null } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, terminationReason: 'complete' } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: {} } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: [{ stepKey: '', action: 'advance', attempt: 1 }] } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: [{ stepKey: 's2', action: 'advance', attempt: '1' }] } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: [{ stepKey: 's2', action: 'teleport', attempt: 1 }] } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: [{ stepKey: 's2', action: 'advance' }] } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: [{ stepKey: 'lesson', action: 'lesson_completed' }, { stepKey: 's2', action: 'advance', attempt: 1 }] } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: [{ stepKey: 's2', action: 'retry', attempt: 2 }, { stepKey: 's2', action: 'advance', attempt: 3 }, { stepKey: 'lesson', action: 'lesson_completed' }] } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, terminated: false, terminationReason: 'lesson_completed' } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, terminated: true, terminationReason: 'max_transitions' } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: validSimulation.simulation.trace.slice(0, -1) } },
  { ...validSimulation, simulation: { ...validSimulation.simulation, trace: [validSimulation.simulation.trace[1], validSimulation.simulation.trace[0], validSimulation.simulation.trace[2], completionTrace] } },
  { ...validSimulation, preview: { profile: 'espTft', width: 480, height: 320 }, etag: 'different' },
];
malformedSimulations.forEach((candidate, index) => {
  assert.strictEqual(validSimulationEvidence(candidate, simulationIdentity, simulationAuthoringSteps), false, `malformed simulation ${index} must be rejected`);
});

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
  .replace(/import \{[\s\S]*?\} from '@\/components\/lesson\/flattened-derivative-status';/, '')
  .replace(/^export function /gm, 'function ')
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
  normalizeFlattenedDerivativeStatusResponse: (value) => value,
};
vm.runInNewContext(executableLessonApiSource, apiSandbox, { filename: 'lesson.js' });
const lessonApi = apiSandbox.module.exports;
const onSuccess = () => {};
const onError = () => {};

assert.strictEqual(typeof lessonApi.getCourseModeContract, 'function');
lessonApi.getCourseModeContract('lesson-course-mode', onSuccess, onError);
assert.strictEqual(apiRequests[0].url, '/v1/admin/lessons/lesson-course-mode/course-mode');
assert.strictEqual(apiRequests[0].method, 'GET');
assert.strictEqual(typeof lessonApi.saveCourseModeContract, 'function');
lessonApi.saveCourseModeContract('lesson-course-mode', courseModeContract, onSuccess, onError);
assert.strictEqual(apiRequests[1].url, '/v1/admin/lessons/lesson-course-mode/course-mode');
assert.strictEqual(apiRequests[1].method, 'PUT');
assert.strictEqual(JSON.stringify(apiRequests[1].data), JSON.stringify({ contract: courseModeContract }));
apiRequests.length = 0;

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

const unknown = calculateReadiness({ steps, assets });
assert.strictEqual(unknown.estimateOnly, true);
assert.strictEqual(unknown.offlineReady, false);
assert.strictEqual(unknown.allPathsTerminate, false);
const dimensionEstimate = calculateReadiness({ assets: [{ assetKey: 'rgba', width: 100, height: 50, hasAlpha: true }] });
assert.strictEqual(dimensionEstimate.estimatedPeakPsram, 20000);

// T4.1 — deleting a bundle asset a step still binds leaves the manifest pointing
// at a row the server no longer has. The picker must name the blocking steps.
const deletionSteps = [
  { stepKey: 's1', stepBody: { teachingObject: { asset: { key: 'teachingObject.barn' } } } },
  { stepKey: 's2', stepBody: { backgroundScene: { poster: { key: 'backgroundScene.poster' } } } },
  { stepKey: 's3', stepBody: { teachingObject: { asset: { key: 'teachingObject.barn' } } } },
];
const boundImpact = assetDeletionImpact(deletionSteps, 'teachingObject.barn');
assert.deepStrictEqual(boundImpact, {
  assetKey: 'teachingObject.barn',
  stepKeys: ['s1', 's3'],
  blocked: true,
});
assert.deepStrictEqual(assetDeletionImpact(deletionSteps, 'backgroundScene.poster').stepKeys, ['s2']);
assert.deepStrictEqual(assetDeletionImpact(deletionSteps, 'teachingObject.unused'), {
  assetKey: 'teachingObject.unused',
  stepKeys: [],
  blocked: false,
});
assert.deepStrictEqual(assetDeletionImpact(deletionSteps, ''), { assetKey: '', stepKeys: [], blocked: false });
assert.deepStrictEqual(assetDeletionImpact(null, 'teachingObject.barn'), {
  assetKey: 'teachingObject.barn',
  stepKeys: [],
  blocked: false,
});

// T4.1 — the backend budgets teachingWord by visible characters. Counting UTF-16
// units instead truncates Vietnamese words carrying combining diacritics.
assert.strictEqual(TEACHING_WORD_MAX_VISIBLE_CHARS, 12);
const decomposedTruong = 'TRƯỜNG'.normalize('NFD');
assert.ok(decomposedTruong.length > 6, 'fixture must actually be decomposed');
assert.strictEqual(visibleGraphemeCount(decomposedTruong), 6);
assert.strictEqual(visibleGraphemeCount('TRƯỜNG'.normalize('NFC')), 6);
assert.strictEqual(teachingWordLengthIssue(decomposedTruong), null);
assert.strictEqual(clampTeachingWord(decomposedTruong), decomposedTruong);
const twelveVisible = 'NGHIÊNGNGẢ'.normalize('NFD');
assert.strictEqual(visibleGraphemeCount(twelveVisible), 10);
assert.strictEqual(teachingWordLengthIssue(twelveVisible), null);
const thirteenVisible = 'ĐƯỜNGSẮTBẮCNAM'.normalize('NFD');
assert.strictEqual(visibleGraphemeCount(thirteenVisible), 14);
assert.deepStrictEqual(teachingWordLengthIssue(thirteenVisible), {
  code: 'teaching-word-too-long', visible: 14, max: 12,
});
// Clamping keeps whole graphemes — never a bare combining mark.
const clamped = clampTeachingWord(thirteenVisible);
assert.strictEqual(visibleGraphemeCount(clamped), 12);
assert.strictEqual(clamped, clampTeachingWord(clamped));
assert.strictEqual(clampTeachingWord(''), '');
assert.strictEqual(clampTeachingWord(null), '');
assert.strictEqual(visibleGraphemeCount(null), 0);

// T4.1 — the delete guard has to be wired, not merely exported.
const assetManagerSource = fs.readFileSync(
  path.join(__dirname, '..', 'src/components/LessonAssetManager.vue'),
  'utf8',
);
assert.match(assetManagerSource, /deletionGuard:\s*\{\s*type:\s*Function/);
assert.match(
  assetManagerSource,
  /onDelete\(a\)\s*\{[\s\S]*?this\.deletionGuard\(a\)[\s\S]*?impact\.blocked[\s\S]*?lesson\.assetDeleteInUse[\s\S]*?return;/,
  'onDelete must consult the deletion guard before calling the delete API',
);
const editorSourceForDeletion = fs.readFileSync(
  path.join(__dirname, '..', 'src/views/LessonEditor.vue'),
  'utf8',
);
const courseModeTimelineSource = fs.readFileSync(
  path.join(__dirname, '..', 'src/components/lesson/CourseModeActivityTimeline.vue'),
  'utf8',
);
assert.match(editorSourceForDeletion, /<CourseModeActivityTimeline/);
assert.match(editorSourceForDeletion, /Api\.lesson\.getCourseModeContract/);
assert.match(editorSourceForDeletion, /Api\.lesson\.saveCourseModeContract/);
assert.match(editorSourceForDeletion, /v-if="[^"]*!isCourseModeAuthority[^"]*"[^>]*class="add-row"/);
assert.match(editorSourceForDeletion, /v-if="isDraft && !isCourseModeAuthority"[^>]*:label="\$t\('lesson\.colActions'\)"/);
assert.match(courseModeTimelineSource, /data-testid="course-mode-duration-meter"/);
assert.match(courseModeTimelineSource, /data-testid="course-mode-answer-leakage-warning"/);
assert.match(courseModeTimelineSource, /expectedDurationSec/);
assert.match(courseModeTimelineSource, /backgroundAssetKey/);
assert.match(courseModeTimelineSource, /objectAssetKey/);
assert.match(courseModeTimelineSource, /fallback/);
assert.match(editorSourceForDeletion, /:deletion-guard="assetDeletionGuard"/);
// studioSteps, not steps: an unsaved draft binding the asset must block too.
assert.match(
  editorSourceForDeletion,
  /assetDeletionGuard\(asset\)\s*\{\s*return assetDeletionImpact\(this\.studioSteps,/,
);

// T4.1 — unsaved step drafts are component state only; leaving must prompt.
assert.match(editorSourceForDeletion, /beforeRouteLeave\(to, from, next\)/);
assert.match(
  editorSourceForDeletion,
  /beforeRouteLeave[\s\S]*?if \(!this\.hasPendingAuthoringChanges\)[\s\S]*?next\(\);/,
);
assert.match(editorSourceForDeletion, /next\(false\)/, 'cancelling the prompt must stay on the page');
assert.match(editorSourceForDeletion, /addEventListener\('beforeunload', this\._unsavedChangesHandler\)/);
assert.match(editorSourceForDeletion, /removeEventListener\('beforeunload', this\._unsavedChangesHandler\)/);

// T4.1 — the panel must count graphemes, not UTF-16 units.
const interactionPanelSource = fs.readFileSync(
  path.join(__dirname, '..', 'src/components/lesson/LessonInteractionPanel.vue'),
  'utf8',
);
assert.ok(
  !/maxlength="12"/.test(interactionPanelSource),
  'teachingWord must not use an HTML maxlength (UTF-16 units, not visible characters)',
);
assert.match(interactionPanelSource, /setTeachingWord\(\$event\)/);
assert.match(interactionPanelSource, /clampTeachingWord\(String\(value\)\.toUpperCase\(\)\)/);

console.log('lesson builder logic checks passed');
