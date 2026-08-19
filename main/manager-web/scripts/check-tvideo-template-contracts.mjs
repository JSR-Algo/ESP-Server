import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const logic = require('../src/components/lesson/tvideo-template-logic.js');
const layouts = require('../src/components/lesson/tvideo-layout-presets.js');
const Vue = require('vue');

function loadComponentOptions(source, imports) {
  const script = /<script>([\s\S]*?)<\/script>/.exec(source)?.[1];
  assert.ok(script, 'component script block is missing');
  const transformed = script
    .replace(/^import .*;$/gm, '')
    .replace('export default', 'componentOptions =');
  const context = vm.createContext({ componentOptions: null, ...imports });
  vm.runInContext(transformed, context);
  return context.componentOptions;
}

async function tick() {
  await new Promise((resolve) => Vue.nextTick(resolve));
}

function extractObjectMethod(source, name) {
  const match = new RegExp(`\\n\\s{2,4}${name}\\(`).exec(source);
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

assert.deepEqual(logic.TEMPLATE_OPTIONS, ['none', 'tvideoFlyWalk']);
assert.deepEqual(logic.mergeStepBodyForSave(
  { interaction: { type: 'repeat' }, templateAuthoring: { backgroundAssetVersionId: 'old-background-uuid' } },
  { interaction: { type: 'repeat' } },
), { interaction: { type: 'repeat' } });
assert.equal(logic.readinessVocabularySummary({ vocabularySetId: 'animals-a1', repeatedWords: ['CAT', 'DOG'] }), 'animals-a1 · 2 repeated');
assert.equal(logic.readinessVocabularySummary({ vocabulary: { unique: ['CAT', 'DOG', 'OWL'], repeated: ['CAT'] } }), '3 unique · 1 repeated');
assert.deepEqual(logic.sharedBackgroundOption({
  asset_key: 'forest-road', version: 3, version_id: 'visual-version-uuid', title: 'Forest Road',
  compatibility_metadata: { supportedLayoutPresets: ['centerRoad'], geometryVersion: 1 },
}), {
  assetKey: 'forest-road@v3',
  assetVersionId: 'visual-version-uuid',
  name: 'Forest Road · v3',
  layer: 'backgroundScene',
  compatibility: { supportedLayoutPresets: ['centerRoad'], geometryVersion: 1 },
});
assert.deepEqual(logic.compatibleLayouts({ supportedLayoutPresets: ['centerRoad', 'rightApproach'], geometryVersion: 1 }), ['centerRoad', 'rightApproach']);
assert.deepEqual(logic.compatibleLayouts({ supportedLayoutPresets: ['centerRoad'], geometryVersion: 2 }), []);

const payload = logic.buildTemplateAuthoring({
  templateId: 'tvideoFlyWalk', layoutPreset: 'centerRoad', backgroundVersionId: 'bg-v1',
  arrivedPoseVersionId: 'pose-v1', atlasVersionId: 'atlas-v1', vocabularySetId: 'animals-v1',
  backgroundCompatibility: { supportedLayoutPresets: ['centerRoad'], geometryVersion: 1 },
});
assert.deepEqual(payload, {
  templateId: 'tvideoFlyWalk', layoutPreset: 'centerRoad', backgroundVersionId: 'bg-v1',
  arrivedPoseVersionId: 'pose-v1', atlasVersionId: 'atlas-v1', vocabularySetId: 'animals-v1',
});
assert.equal(JSON.stringify(payload).match(/\"(?:x|y|w|h|servo|motor|chassis|timeline|easing)\"/i), null);

const batch = logic.assessVariantReadiness([
  { lessonKey: 'one', vocabularySetId: 'animals', words: ['CAT'], backgroundVersionId: 'bg', layoutPreset: 'centerRoad' },
  { lessonKey: 'two', vocabularySetId: 'actions', words: ['RUN'], backgroundVersionId: 'bg', layoutPreset: 'centerRoad' },
], { bg: { supportedLayoutPresets: ['centerRoad'], geometryVersion: 1 } });
assert.equal(batch[0].backgroundUsage, 'shared');
assert.equal(batch[1].ready, true);

const variantRequest = logic.buildVariantGenerationRequest([
  {
    lessonKey: 'animals-a1', title: 'Animals A1', vocabularySetId: 'vocab-animals-a1',
    words: ['CAT'], backgroundVersionId: 'bg-shared-v1', layoutPreset: 'centerRoad',
    arrivedPoseVersionId: 'pose-v1', atlasVersionId: 'atlas-v1',
  },
  {
    lessonKey: 'actions-a1', title: 'Actions A1', vocabularySetId: 'vocab-actions-a1',
    words: ['RUN'], backgroundVersionId: 'bg-actions-v2', layoutPreset: 'leftApproach',
    arrivedPoseVersionId: 'pose-v1',
  },
]);
assert.deepEqual(variantRequest, {
  variants: [
    {
      lessonKey: 'animals-a1', title: 'Animals A1', vocabularySetId: 'vocab-animals-a1',
      words: ['CAT'],
      templateAuthoring: {
        templateId: 'tvideoFlyWalk', layoutPreset: 'centerRoad', backgroundVersionId: 'bg-shared-v1',
        arrivedPoseVersionId: 'pose-v1', atlasVersionId: 'atlas-v1',
      },
    },
    {
      lessonKey: 'actions-a1', title: 'Actions A1', vocabularySetId: 'vocab-actions-a1',
      words: ['RUN'],
      templateAuthoring: {
        templateId: 'tvideoFlyWalk', layoutPreset: 'leftApproach', backgroundVersionId: 'bg-actions-v2',
        arrivedPoseVersionId: 'pose-v1',
      },
    },
  ],
});
assert.notEqual(variantRequest.variants[0].vocabularySetId, variantRequest.variants[1].vocabularySetId);
assert.equal(JSON.stringify(variantRequest).match(/\"(?:x|y|w|h|servo|motor|chassis|command|timeline|easing)\"/i), null);

const readiness = logic.normalizeBatchReadiness([
  { lessonId: 'lesson-ready', ready: true, vocabulary: { unique: ['CAT'], repeated: [] }, backgroundUsage: 'shared', issues: [] },
  { lessonId: 'lesson-blocked', ready: false, vocabulary: { unique: [], repeated: ['CAT'] }, backgroundUsage: 'unique', issues: ['layout-incompatible'] },
]);
assert.deepEqual(readiness.readyLessonIds, ['lesson-ready']);
assert.equal(readiness.readyCount, 1);
assert.equal(readiness.blockedCount, 1);
assert.equal(readiness.lessons[1].issues[0], 'layout-incompatible');
const wrappedReadiness = logic.normalizeBatchReadiness({
  lessons: readiness.lessons,
  readyLessonIds: ['lesson-ready'],
});
assert.deepEqual(wrappedReadiness.readyLessonIds, ['lesson-ready']);

assert.equal(layouts.SCREEN.width, 480);
assert.equal(layouts.SCREEN.height, 320);
assert.deepEqual(layouts.PHASES.map((phase) => phase.name), ['hidden', 'flyIn', 'landFar', 'settle', 'walkToward', 'arriveNear', 'greetIdle', 'revealTeachingContent']);
for (const id of ['centerRoad', 'leftApproach', 'rightApproach']) assert.ok(layouts.LAYOUT_PRESETS[id]);
assert.deepEqual(layouts.LAYOUT_PRESETS.centerRoad.teachingObject, { left: 20, top: 168, width: 95, height: 95 });
assert.deepEqual(layouts.LAYOUT_PRESETS.centerRoad.arrive, { left: 118, top: 160, width: 150, height: 150 });
for (const layout of Object.values(layouts.LAYOUT_PRESETS)) {
  assert.equal(layouts.rectOverlaps(layout.arrive, layout.teachingObject), false);
  assert.equal(layouts.rectOverlaps(layout.arrive, layout.wordPill), false);
  assert.equal(layouts.rectOverlaps(layout.walkCorridor, layout.teachingObject), false);
}
assert.equal(layouts.validateProjection({
  templateId: 'tvideoFlyWalk', templateVersion: 1, layoutPreset: 'centerRoad', geometryVersion: 1,
  phases: layouts.PHASES, revealPhase: 'revealTeachingContent', fallbackPolicy: 'snapToArriveNearAndReveal',
}), true);
assert.deepEqual(layouts.phaseRobotRect(layouts.LAYOUT_PRESETS.centerRoad, 'walkToward', 900, 1800), {
  left: 201, top: 138, width: 150, height: 150,
});
assert.equal(layouts.isTeachingContentVisible('arriveNear', 'revealTeachingContent', true), true);
assert.equal(layouts.isTeachingContentVisible('arriveNear', 'revealTeachingContent', false), false);
assert.equal(layouts.isTeachingContentVisible('revealTeachingContent', 'revealTeachingContent', false), true);
for (const requestedPhase of ['hidden', 'flyIn', 'greetIdle', 'revealTeachingContent']) {
  assert.equal(layouts.effectivePreviewPhaseName(requestedPhase, true), 'arriveNear');
}
assert.equal(layouts.effectivePreviewPhaseName('greetIdle', false), 'greetIdle');

const previewSource = fs.readFileSync(new URL('../src/components/lesson/TvideoJourneyPreview.vue', import.meta.url), 'utf8');
assert.ok(previewSource.includes('this.paused = this.fallback;'), 'static fallback must remain on the arrived frame');
for (const marker of [
  '480', '320', 'Replay', 'Pause', 'Arrived frame', 'selectPhase', 'phase-selector',
  'safe-zone', 'walk-corridor', 'teachingObjectSrc', 'missingAtlas', 'missingOverlay', 'phaseTimeout', 'reducedMotion',
]) {
  assert.ok(previewSource.includes(marker), `preview missing ${marker}`);
}
const panelSource = fs.readFileSync(new URL('../src/components/lesson/TvideoTemplatePanel.vue', import.meta.url), 'utf8');
for (const forbidden of ['servoCommand', 'motorCommand', 'chassisCommand', 'timelineEditor', 'coordinateInput']) {
  assert.equal(panelSource.includes(forbidden), false, `author panel exposes ${forbidden}`);
}
for (const forbidden of ['Background workflow', 'Reuse shared background', 'Clone for this lesson', 'Upload a new shared background version', 'Compatible background']) {
  assert.equal(panelSource.includes(forbidden), false, `TVideo must not expose a second background authority: ${forbidden}`);
}
assert.ok(panelSource.includes('lesson-wide background selector'), 'TVideo must explain that its background is derived from the lesson-wide selector');
assert.ok(panelSource.includes('background: { type: Object'), 'TVideo template must receive the authoritative selected background');
const templateOptions = loadComponentOptions(panelSource, {
  buildTemplateAuthoring: logic.buildTemplateAuthoring,
  compatibleLayouts: logic.compatibleLayouts,
});
const TemplatePanel = Vue.extend({ ...templateOptions, render(h) { return h('div'); } });
const templatePanel = new TemplatePanel({
  propsData: {
    value: {
      templateId: 'tvideoFlyWalk', layoutPreset: 'centerRoad', vocabularySetId: 'animals',
      backgroundVersionId: 'scene.old@v1', backgroundAssetVersionId: 'old-version-id', arrivedPoseVersionId: 'pose-v1',
    },
    assets: [],
    background: null,
  },
});
let templateInputAttempts = 0;
let mutationBlocked = false;
templatePanel.$on('input', () => { templateInputAttempts += 1; if (mutationBlocked) throw new Error('blocked parent setter must never receive a background-derived input'); });
templatePanel.$mount();
await tick();
assert.equal(templateInputAttempts, 0, 'mounting before the background library loads must not dirty the step');
templatePanel.background = { assetKey: 'scene.farm@v2', assetVersionId: 'farm-version-id', compatibility: { geometryVersion: 1, supportedLayoutPresets: ['centerRoad'] } };
await tick();
assert.equal(templateInputAttempts, 0, 'async background library load must not emit through v-model');
assert.equal(templatePanel.draft.backgroundVersionId, 'scene.old@v1', 'async library load must not mutate authored step metadata');
mutationBlocked = true;
templatePanel.background = { assetKey: 'scene.town@v3', assetVersionId: 'town-version-id', compatibility: { geometryVersion: 1, supportedLayoutPresets: ['leftApproach'] } };
await tick();
assert.equal(templateInputAttempts, 0, 'background changes during a pending lesson visual save must not attempt a suppressed step mutation');
assert.equal(templatePanel.draft.backgroundVersionId, 'scene.old@v1', 'background changes must remain read-only in the template editor');
const batchPanelSource = fs.readFileSync(new URL('../src/components/lesson/TvideoVariantBatchPanel.vue', import.meta.url), 'utf8');
for (const marker of [
  'Generate variants', 'Run batch readiness', 'Ready subset', 'vocabularySetId', 'layoutPreset',
  'Vocabulary', 'Background', 'Pack bytes', 'Peak PSRAM', 'Offline', 'Terminates',
  'duplicateReason', 'Recall', 'Spiral review', 'Assessment',
]) {
  assert.ok(batchPanelSource.includes(marker), `batch panel missing ${marker}`);
}
assert.equal(batchPanelSource.includes('v-model="variant.backgroundVersionId"'), false, 'variants must not select a background independently');
assert.equal(batchPanelSource.includes('props: {\n    backgrounds:'), false, 'batch panel must derive background from template authoring');
const batchOptions = loadComponentOptions(batchPanelSource, {
  buildVariantGenerationRequest: logic.buildVariantGenerationRequest,
  compatibleLayouts: logic.compatibleLayouts,
  readinessVocabularySummary: logic.readinessVocabularySummary,
});
const BatchPanel = Vue.extend({ ...batchOptions, render(h) { return h('div'); } });
const batchPanel = new BatchPanel({
  propsData: {
    background: { assetKey: 'scene.farm@v2', assetVersionId: 'farm-version-id', compatibility: { geometryVersion: 1, supportedLayoutPresets: ['centerRoad'] } },
    templateAuthoring: { templateId: 'tvideoFlyWalk', layoutPreset: 'centerRoad', backgroundVersionId: 'scene.old@v1', backgroundAssetVersionId: 'old-version-id', arrivedPoseVersionId: 'pose-v1' },
  },
});
batchPanel.$message = { error() {} };
let generatedPayload;
batchPanel.$on('generate', (payloadValue) => { generatedPayload = payloadValue; });
batchPanel.$mount();
batchPanel.variants[0] = { ...batchPanel.variants[0], lessonKey: 'animals-new', vocabularySetId: 'animals', wordsText: 'CAT' };
batchPanel.generate();
assert.equal(generatedPayload.variants[0].templateAuthoring.backgroundVersionId, 'scene.farm@v2', 'variant request must inject the authoritative background key');
assert.equal(generatedPayload.variants[0].templateAuthoring.backgroundAssetVersionId, 'farm-version-id', 'variant request must inject the authoritative background version id');
generatedPayload = undefined;
batchPanel.background = null;
await tick();
batchPanel.generate();
assert.equal(generatedPayload, undefined, 'an unavailable authoritative background must clear stale template metadata and block generation');
for (const forbidden of ['servoCommand', 'motorCommand', 'chassisCommand', 'timelineEditor', 'coordinateInput']) {
  assert.equal(batchPanelSource.includes(forbidden), false, `batch panel exposes ${forbidden}`);
}

const apiSource = fs.readFileSync(new URL('../src/apis/module/lesson.js', import.meta.url), 'utf8');
assert.ok(apiSource.includes('/variants`'));
assert.ok(apiSource.includes('/batch-readiness'));
assert.ok(apiSource.includes('generateVariants('));
assert.ok(apiSource.includes('assessBatchReadiness('));

const editorSource = fs.readFileSync(new URL('../src/views/LessonEditor.vue', import.meta.url), 'utf8');
for (const marker of [
  'TvideoVariantBatchPanel', '@generate="generateTvideoVariants"', '@readiness="runTvideoBatchReadiness"',
  'Api.lesson.generateVariants(', 'Api.lesson.assessBatchReadiness(', 'normalizeBatchReadiness',
]) {
  assert.ok(editorSource.includes(marker), `lesson editor batch wiring missing ${marker}`);
}
assert.ok(editorSource.includes('<TvideoTemplatePanel'), 'template content editing must remain mounted in LessonEditor');
assert.ok(editorSource.includes('v-model="selectedTemplateAuthoring"'), 'template content edits must remain part of the step draft');
assert.ok(editorSource.includes(':background="selectedTemplateBackground"'), 'LessonEditor must derive TVideo background from its authoritative lesson visual pair');
assert.equal(editorSource.includes(':backgrounds="templateAssets.filter'), false, 'LessonEditor must not wire a second TVideo background picker');

const saveSelectedStepSource = extractObjectMethod(editorSource, 'saveSelectedStep');
assert.equal(saveSelectedStepSource.includes('setVisualRef'), false, 'template step saves must not write per-step visual refs');
assert.equal(saveSelectedStepSource.includes('visualRefs:'), false, 'template step saves must not submit background/object visual refs');
assert.ok(saveSelectedStepSource.includes('this.stepPayloadWithoutVisualRefs('), 'template step saves must strip loaded background/object refs');
assert.equal(saveSelectedStepSource.includes('backgroundScene'), false, 'template step saves must not construct a backgroundScene visual ref');
const stripVisualRefsSource = extractObjectMethod(editorSource, 'stepPayloadWithoutVisualRefs');
assert.ok(stripVisualRefsSource.includes('delete sanitized.visualRefs'), 'step payloads must remove all lesson-owned visual refs');

for (const selector of ['selectBackground', 'selectTeachObject']) {
  const selectorSource = extractObjectMethod(editorSource, selector);
  assert.ok(selectorSource.includes('this.applyLessonVisualSelection({'), `${selector} must delegate to the lesson-wide selector`);
  assert.equal(selectorSource.includes('setVisualRef'), false, `${selector} must not write a per-step visual ref`);
}
const lessonVisualSaveSource = extractObjectMethod(editorSource, 'applyLessonVisualSelection');
assert.ok(lessonVisualSaveSource.includes('Api.lesson.applyLessonVisuals('), 'lesson visual changes must use only the lesson-level endpoint');
assert.equal(panelSource.includes('setVisualRef'), false, 'the TVideo panel must remain a content/body editor, not a visual-ref writer');

const robotPreviewSource = fs.readFileSync(new URL('../src/components/lesson/RobotManifestServerPreview.vue', import.meta.url), 'utf8');
const primaryWordMatch = /primaryWord\(\)\s*\{\s*return\s+([\s\S]*?);\s*\},/.exec(robotPreviewSource);
assert.ok(primaryWordMatch, 'RobotManifestServerPreview primaryWord computed expression is missing');
const resolvePrimaryWord = Function(`return function primaryWord() { return (${primaryWordMatch[1]}); }`)();
const primaryWordCases = [
  {
    body: { teachingWord: { displayText: 'BaRn', text: 'BARN' }, primaryWord: 'body-barn' },
    scene: { primaryWord: 'scene-barn' }, currentStep: { subject: 'barn' }, expected: 'BaRn',
  },
  {
    body: { teachingWord: { text: 'COW' } }, scene: {}, currentStep: { subject: 'cow' }, expected: 'COW',
  },
  {
    body: {}, scene: { primaryWord: 'Hen' }, currentStep: { subject: 'hen' }, expected: 'Hen',
  },
  {
    body: { primaryWord: 'Corn' }, scene: {}, currentStep: { subject: 'corn' }, expected: 'Corn',
  },
  {
    body: {}, scene: {}, currentStep: { subject: 'barn' }, expected: 'barn',
  },
];
for (const testCase of primaryWordCases) {
  assert.equal(resolvePrimaryWord.call(testCase), testCase.expected);
}

console.log('tvideo template contracts OK');
