import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const logic = require('../src/components/lesson/tvideo-template-logic.js');
const layouts = require('../src/components/lesson/tvideo-layout-presets.js');

assert.deepEqual(logic.TEMPLATE_OPTIONS, ['none', 'tvideoFlyWalk']);
assert.deepEqual(logic.templateVisualRefTransition(
  { templateAuthoring: { backgroundAssetVersionId: 'old-background-uuid' } },
  {},
), { shouldSync: true, previousAssetVersionId: 'old-background-uuid', nextAssetVersionId: null });
assert.deepEqual(logic.templateVisualRefTransition(
  { templateAuthoring: { backgroundAssetVersionId: 'old-background-uuid' } },
  { templateAuthoring: { backgroundAssetVersionId: 'new-background-uuid' } },
), { shouldSync: true, previousAssetVersionId: 'old-background-uuid', nextAssetVersionId: 'new-background-uuid' });
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
  left: 234, top: 150, width: 112, height: 56,
});

const previewSource = fs.readFileSync(new URL('../src/components/lesson/TvideoJourneyPreview.vue', import.meta.url), 'utf8');
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
for (const marker of ['Reuse shared background', 'Clone for this lesson', 'Upload a new shared background version']) {
  assert.ok(panelSource.includes(marker), `author panel missing background flow ${marker}`);
}
const batchPanelSource = fs.readFileSync(new URL('../src/components/lesson/TvideoVariantBatchPanel.vue', import.meta.url), 'utf8');
for (const marker of [
  'Generate variants', 'Run batch readiness', 'Ready subset', 'vocabularySetId', 'backgroundVersionId', 'layoutPreset',
  'Vocabulary', 'Background', 'Pack bytes', 'Peak PSRAM', 'Offline', 'Terminates',
  'duplicateReason', 'Recall', 'Spiral review', 'Assessment',
]) {
  assert.ok(batchPanelSource.includes(marker), `batch panel missing ${marker}`);
}
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
for (const marker of ['templateVisualRefTransition', 'restoreTemplateVisualRef', 'resetStepDraftAfterFailedSave', 'Partial save:', 'assetVersionId,']) {
  assert.ok(editorSource.includes(marker), `lesson editor visual-ref compensation missing ${marker}`);
}

console.log('tvideo template contracts OK');
