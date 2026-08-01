import Vue from 'vue';
import ElementUI from 'element-ui';
import VueRouter from 'vue-router';
import LessonEditor from '@/views/LessonEditor.vue';
import Api from '@/apis/api';
import i18n from '@/i18n';
import { resetLessonRolloutCapabilities } from '@/utils/lessonRolloutCapabilities';
import applicationRouter from '@/router';
import applicationStore from '@/store';
import { normalizeFlattenedDerivativeStatusResponse } from '@/components/lesson/flattened-derivative-status';
import { createFarmJourneyDraft, requiredCueIds } from '@/components/lesson/tvideo-journey';

Vue.use(ElementUI);
Vue.use(VueRouter);
Vue.config.productionTip = false;
localStorage.setItem('token', 'lesson-builder-test-session');

const calls = { update: [], visualFilters: [], visualRefSets: [], lessonVisualSets: [], journeyLoads: [], journeySaves: [], flattenedDerivativeLoads: [], validate: 0, preview: 0, errors: [], warnings: [], failNextUpdate: false, failNextJourneySave: false, deferNextUpdate: false, deferNextValidate: false, deferNextJourneySave: false, pendingUpdates: [], pendingValidations: [], pendingJourneySaves: [] };
let steps = [
  {
    stepKey: 's1', stepType: 'greeting', prompt: 'Meet Pip', subject: 'pet', stepBody: { durationSec: 8 },
    visualRefs: [
      { slot: 'backgroundScene', assetVersionId: 'scene-v3', assetKey: 'scene.farm' },
      { slot: 'teachingObject', assetVersionId: 'teach-v2', assetKey: 'object.barn' },
    ],
  },
  {
    stepKey: 's2', stepType: 'repeat', prompt: 'Say barn', subject: 'barn', stepBody: { durationSec: 12 },
    visualRefs: [
      { slot: 'backgroundScene', assetVersionId: 'scene-v3', assetKey: 'scene.farm' },
      { slot: 'teachingObject', assetVersionId: 'teach-v2', assetKey: 'object.barn' },
      { slot: 'robotOverlay', assetVersionId: 'robot-v4', assetKey: 'robot.teach' },
    ],
  },
];
const sharedAssets = [
  { assetId: 'scene-asset', assetKey: 'scene.farm', category: 'scene', layer: 'backgroundScene', versionId: 'scene-v3', versionIdentity: 'scene-v3', version: 3, url: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4', mimeType: 'video/mp4', sha256: 'scene-sha', bytes: 180000, width: 480, height: 320, usageCount: 2, compatibilityMetadata: { codec: 'mjpeg', fps: 15, durationMs: 3200, frameCount: 48, rect: { x: 0, y: 0, width: 480, height: 320 }, chromaKey: null } },
  { assetId: 'scene-asset', assetKey: 'scene.farm', category: 'scene', layer: 'backgroundScene', versionId: 'scene-v4', versionIdentity: 'scene-v4', version: 4, url: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4', mimeType: 'video/mp4', sha256: 'scene-sha-v4', bytes: 181000, width: 480, height: 320, usageCount: 0, compatibilityMetadata: { codec: 'mjpeg', fps: 15, durationMs: 3200, frameCount: 48, rect: { x: 0, y: 0, width: 480, height: 320 }, chromaKey: null } },
  { assetId: 'teach-asset', assetKey: 'object.barn', category: 'teachingObject', layer: 'teachingObject', versionId: 'teach-v2', versionIdentity: 'teach-v2', version: 2, url: '/tvideo-demo/assets/objects/barn.png', mimeType: 'image/png', sha256: 'abc123', bytes: 60000, width: 160, height: 120, usageCount: 4, compatibilityMetadata: { codec: 'png', fps: 0, durationMs: 0, frameCount: 1, rect: { x: 180, y: 100, width: 160, height: 120 }, chromaKey: null } },
  { assetId: 'teach-asset', assetKey: 'object.barn', category: 'teachingObject', layer: 'teachingObject', versionId: 'teach-v3', versionIdentity: 'teach-v3', version: 3, url: '/tvideo-demo/assets/objects/barn.png', mimeType: 'image/png', sha256: 'abc124', bytes: 61000, width: 160, height: 120, usageCount: 0, compatibilityMetadata: { codec: 'png', fps: 0, durationMs: 0, frameCount: 1, rect: { x: 180, y: 100, width: 160, height: 120 }, chromaKey: null } },
  { assetId: 'robot-asset', assetKey: 'robot.teach', category: 'robotPose', layer: 'robotOverlay', versionId: 'robot-v4', versionIdentity: 'robot-v4', version: 4, url: '/tvideo-demo/assets/robot-alive/flight/greet-loop.webm', mimeType: 'video/webm', sha256: 'robot-sha', bytes: 72000, width: 200, height: 220, usageCount: 1, compatibilityMetadata: { codec: 'vp9', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 8, y: 92, width: 200, height: 220 }, chromaKey: null } },
  { assetId: 'robot-asset', assetKey: 'robot.teach', category: 'robotPose', layer: 'robotOverlay', versionId: 'robot-v5', versionIdentity: 'robot-v5', version: 5, url: '/tvideo-demo/assets/robot-alive/flight/greet-loop.webm', mimeType: 'video/webm', sha256: 'robot-sha-v5', bytes: 74000, width: 200, height: 220, usageCount: 0, compatibilityMetadata: { codec: 'vp9', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 8, y: 92, width: 200, height: 220 }, chromaKey: null } },
  { assetId: 'journey-bg', assetKey: 'journey.farm', category: 'scene', layer: 'backgroundScene', versionId: '10000000-0000-4000-8000-000000000001', version: 1, url: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4', mimeType: 'video/mp4', sha256: '53d3ac70d166ba83029d5d122493dc48304d2caf933e03c09b0907152531f5f1', bytes: 1000, width: 480, height: 320, usageCount: 0 },
  { assetId: 'journey-bg', assetKey: 'journey.farm.alt', category: 'scene', layer: 'backgroundScene', versionId: '10000000-0000-4000-8000-000000000002', version: 2, url: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4', mimeType: 'video/mp4', sha256: '53d3ac70d166ba83029d5d122493dc48304d2caf933e03c09b0907152531f5f1', bytes: 1000, width: 480, height: 320, usageCount: 0 },
  { assetId: 'journey-barn', assetKey: 'journey.barn', category: 'teachingObject', layer: 'teachingObject', versionId: '30000000-0000-4000-8000-000000000001', version: 1, url: '/tvideo-demo/assets/objects/barn.png', mimeType: 'image/png', sha256: 'eac30a7ddf3f14df79f27c3eb39f2114f3a780d5670bb11ef62446f5fa5dcbb9', bytes: 68, width: 192, height: 192, usageCount: 0 },
  { assetId: 'journey-hay', assetKey: 'journey.hay', category: 'teachingObject', layer: 'teachingObject', versionId: '30000000-0000-4000-8000-000000000002', version: 1, url: '/tvideo-demo/assets/objects/hay.png', mimeType: 'image/png', sha256: 'f74c34f44459495062091d4d91bb8fef0a2501ff3fab78beddaf0f70f0bf2e11', bytes: 68, width: 192, height: 192, usageCount: 0 },
  ...[
    ['flight', 'flight-in', '52091cdbfe5712e4afed800ce35e4a743e923c65b4034dd4c0a3c85d0f6c345c'],
    ['walking', 'walk-toward', '46a3058664949eaebf057f99309ee317bd36257178c187e83329fdbaf030cf5a'],
    ['greeting-teaching', 'greet-loop', '249a86cea2456b41ee5344445b0b83777a0ee7217ce435e7e9294ed7f39b12e0'],
    ['celebration', 'celebrate', '708d982dc9f2257a170ad1811c1afc230b5249a4139f975133a703ebdf39e105'],
  ].map(([role, file, sha256], index) => ({ assetId: `journey-${role}`, assetKey: `journey.robot.${role}`, category: 'robotPose', layer: 'robotOverlay', role, versionId: `20000000-0000-4000-8000-00000000000${index + 1}`, version: 1, url: `/tvideo-demo/assets/robot-alive/flight/${file}.webm`, mimeType: 'video/webm', sha256, bytes: 1000, width: 200, height: 220, usageCount: 0 })),
];
const journeyPreset = { presetId: 'tvideoJourney', presetVersion: 1, locked: true, width: 480, height: 320, fps: 10, confettiSeed: 0x54424f54, confettiPieces: 64, rendererBuildSha256: 'a'.repeat(64), effects: { opening: {}, greet: {}, teach: {}, listen: {}, thinking: {}, correct: {}, 'retry-level-1': {}, 'retry-level-2': {}, 'retry-level-3': {}, celebrate: {}, 'word-transition': {} } };
const validation = { valid: true, profiles: ['espTft'], budgets: { espTft: { errors: [], warnings: [], metrics: { assetCount: 9, uniqueAssetCount: 7, sharedAssetCount: 2, packBytes: 222000, estimatedVisualPeakBytes: 640000, offlineReady: true, allPathsTerminate: true } } } };
const visualStates = ['teach', 'listen', 'thinking', 'correct', 'nearMiss', 'incorrect', 'retry', 'celebrate', 'completion'];
const stateMotions = { teach: 'presentLeft', listen: 'listen', thinking: 'thinking', correct: 'celebrate', nearMiss: 'encourage', incorrect: 'gentle-shake', retry: 'tryAgain', celebrate: 'celebrate', completion: 'celebrate' };
const robotAsset = { src: 'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs=' };
const templateProjection = {
  templateId: 'tvideoFlyWalk', templateVersion: 1, layoutPreset: 'centerRoad', geometryVersion: 1,
  phases: [
    { name: 'hidden', durationMs: 100 }, { name: 'flyIn', durationMs: 1200 },
    { name: 'landFar', durationMs: 700 }, { name: 'settle', durationMs: 350 },
    { name: 'walkToward', durationMs: 1800 }, { name: 'arriveNear', durationMs: 250 },
    { name: 'greetIdle', durationMs: 650 }, { name: 'revealTeachingContent', durationMs: 100 }
  ]
};
const manifest = {
  manifestVersion: 'teebot-lesson-renderer.v2',
  profile: 'espTft',
  openingEntrance: { template: 'tvideoFlyWalk', preset: 'flyLandWalkGreet', policy: 'oncePerLessonSession', phases: ['hidden', 'flyIn', 'landFar', 'settle', 'walkToward', 'arriveNear', 'greetIdle', 'revealTeachingContent'], fallback: 'staticGreet' },
  pathsTerminate: true,
  steps: [
    { stepKey: 's1', prompt: 'Meet Pip', scene: { robotOverlay: { asset: robotAsset } }, teachingWord: { text: 'PET' }, entrance: 'none', templateProjection, visualStates: Object.fromEntries(visualStates.map((state) => [state, { prompt: state, motionPreset: stateMotions[state], overlayKey: state }])) },
    { stepKey: 's2', prompt: 'Say barn', scene: { robotOverlay: { asset: robotAsset } }, teachingWord: { text: 'BARN' }, entrance: 'none', templateProjection, visualStates: Object.fromEntries(visualStates.map((state) => [state, { prompt: state, motionPreset: stateMotions[state], overlayKey: state }])) },
  ]
};
const lessonManifestVersions = {
  'lesson-1': 'teebot-lesson-renderer.v2',
  'journey-v4': 'teebot-lesson-renderer.v4',
  'legacy-v4': 'teebot-lesson-renderer.v4',
  'legacy-v1': 'teebot-lesson-renderer.v1',
  'legacy-v2': 'teebot-lesson-renderer.v2',
  'legacy-v3': 'teebot-lesson-renderer.v3',
};

Object.assign(Api.lesson, {
  getRolloutCapabilities(ok) { ok({ sharedVisualAuthoring: true, exactEspTftPreview: true }); },
  getLesson(id, ok) { ok({ lessonId: id, lessonKey: 'farm-1', title: 'Farm friends', status: 'draft', lessonVersion: 1, locale: 'vi', manifestVersion: lessonManifestVersions[id] || 'teebot-lesson-renderer.v2' }); },
  getTVideoJourneyPreset(ok) { ok(journeyPreset); },
  getTVideoJourney(id, ok) {
    calls.journeyLoads.push(id);
    if (id === 'lesson-1' || id === 'journey-v4') {
      ok({ state: 'configured', lessonId: id, lessonVersion: 1, sourceRevision: 1, cinematicSourceRevision: 1, journey: createFarmJourneyDraft(), set: { state: 'ready', issues: [] }, statuses: [], publishReady: false });
      return;
    }
    ok({ state: 'not-configured', lessonId: id, cinematicSourceRevision: 0, set: { state: 'invalid', issues: [{ code: 'MISSING_CUES', cueIds: [] }] }, statuses: [], publishReady: false });
  },
  saveTVideoJourney(id, payload, ok, fail) {
    calls.journeySaves.push(JSON.parse(JSON.stringify(payload)));
    if (calls.deferNextJourneySave) { calls.deferNextJourneySave = false; calls.pendingJourneySaves.push({ id, payload, ok, fail }); return; }
    if (calls.failNextJourneySave) { calls.failNextJourneySave = false; fail('journey save failed', { status: 422, data: { error: { code: 'ASSET_PIN_INVALID', details: { role: 'flight' } } } }); return; }
    ok({ state: 'configured', lessonId: id, lessonVersion: 2, sourceRevision: 2, cinematicSourceRevision: 2, journey: payload, set: { state: 'invalid', issues: [{ code: 'QUEUED_CUES', cueIds: requiredCueIds(payload.steps) }] }, statuses: [], publishReady: false });
  },
  getFlattenedDerivativeStatus(lessonId, lessonVersion, ok) {
    calls.flattenedDerivativeLoads.push({ lessonId, lessonVersion });
    ok(normalizeFlattenedDerivativeStatusResponse({ data: [] }, { lessonId, lessonVersion }));
  },
  listSteps(id, ok) { ok(steps.map((step) => ({ ...step, visualRefs: [...(step.visualRefs || [])] }))); },
  listStepTypes(ok) { ok([{ stepType: 'greeting', completionClass: 'passive' }, { stepType: 'repeat', completionClass: 'interactive' }]); },
  listSharedBackgrounds(ok) { ok([]); },
  listVisualAssets(filters, ok) { calls.visualFilters.push(filters); ok(filters.category ? sharedAssets.slice(0, 6).filter((asset) => asset.category === filters.category) : sharedAssets); },
  setVisualRef(lessonId, stepKey, slot, assetVersionId, ok) {
    calls.visualRefSets.push({ lessonId, stepKey, slot, assetVersionId });
    const asset = sharedAssets.find((row) => row.versionId === assetVersionId);
    steps = steps.map((step) => {
      if (step.stepKey !== stepKey) return step;
      const visualRefs = (step.visualRefs || []).filter((ref) => ref.slot !== slot);
      visualRefs.push({ slot, assetVersionId, assetKey: asset ? asset.assetKey : '' });
      return { ...step, visualRefs };
    });
    ok({ slot, assetVersionId });
  },
  applyLessonVisuals(lessonId, payload, ok) {
    calls.lessonVisualSets.push({ lessonId, payload: { ...payload } });
    const slots = [
      ['backgroundScene', payload.backgroundAssetVersionId],
      ['teachingObject', payload.objectAssetVersionId],
    ];
    steps = steps.map((step) => {
      const visualRefs = (step.visualRefs || []).filter((ref) => !slots.some(([slot]) => ref.slot === slot));
      slots.forEach(([slot, assetVersionId]) => {
        const asset = sharedAssets.find((row) => row.versionId === assetVersionId);
        visualRefs.push({ slot, assetVersionId, assetKey: asset ? asset.assetKey : '' });
      });
      return { ...step, visualRefs };
    });
    ok({ lessonId, ...payload });
  },
  updateStep(lessonId, stepKey, payload, ok, fail) {
    calls.update.push({ lessonId, stepKey, payload: JSON.parse(JSON.stringify(payload)) });
    if (calls.deferNextUpdate) { calls.deferNextUpdate = false; calls.pendingUpdates.push({ lessonId, stepKey, payload, ok, fail }); return; }
    if (calls.failNextUpdate) { calls.failNextUpdate = false; fail('forced update failure'); return; }
    const visualRefs = (payload.visualRefs || []).map((ref) => ({ ...ref, assetKey: sharedAssets.find((asset) => asset.versionId === ref.assetVersionId)?.assetKey || '' }));
    steps = steps.map((step) => step.stepKey === stepKey ? { ...step, ...payload, visualRefs } : step);
    ok({ ...payload, stepKey, visualRefs });
  },
  validate(id, ok, fail) { calls.validate += 1; if (calls.deferNextValidate) { calls.deferNextValidate = false; calls.pendingValidations.push({ ok, fail }); return; } ok(validation); },
  manifestPreview(id, profile, ok) { calls.preview += 1; ok({ manifest, checksum: 'checksum-1', etag: 'etag-1', features: { renderer: ['teebot-lesson-renderer.v1', 'teebot-lesson-renderer.v2'], lessonRendererV2: { openingEntrance: true, visualStateEvents: true, physicalMotionOwner: 'server', singleSpriteEntrance: true } } }); },
  reorderSteps() {}, deleteStep() {}, publish() {}, updateLesson() {}, createStep() {},
});

LessonEditor.components.HeaderBar = { name: 'HeaderBar', render: (h) => h('header') };
LessonEditor.components.LessonAssetManager = { name: 'LessonAssetManager', props: ['lessonId'], mounted() { this.$emit('assets-loaded', sharedAssets); }, render: (h) => h('div') };

const router = new VueRouter({ routes: [{ path: '/', component: { render: (h) => h('div') } }] });
await router.replace({ path: '/', query: { lessonId: 'lesson-1' } });
Vue.prototype.$message = { success() {}, error(message) { calls.errors.push(message); }, warning(message) { calls.warnings.push(message); } };
Vue.prototype.$confirm = () => Promise.resolve();
let vm = new Vue({ router, i18n, render: (h) => h(LessonEditor) }).$mount('#app');

const editor = vm.$children[0];
window.__LESSON_BUILDER_TEST__ = { editor, calls, sharedAssets, validation, manifest };
window.__SET_TEST_LOCALE__ = async (locale) => { i18n.locale = locale; await Vue.nextTick(); };
window.__MOUNT_DISABLED_LESSON_EDITOR__ = async () => {
  vm.$destroy();
  vm.$el.remove();
  await new Promise((resolve) => setTimeout(resolve, 0));
  const root = document.createElement('div');
  root.id = 'disabled-app';
  document.body.appendChild(root);
  Api.lesson.getRolloutCapabilities = (ok) => ok({ sharedVisualAuthoring: false, exactEspTftPreview: false });
  resetLessonRolloutCapabilities();
  const visualBefore = calls.visualFilters.length;
  const previewBefore = calls.preview;
  vm = new Vue({ router, i18n, render: (h) => h(LessonEditor) }).$mount(root);
  const disabledEditor = vm.$children[0];
  await new Promise((resolve) => disabledEditor.$nextTick(resolve));
  await new Promise((resolve) => setTimeout(resolve, 0));
  disabledEditor.doPreview();
  await disabledEditor.$nextTick();
  return {
    visualCalls: calls.visualFilters.length - visualBefore,
    previewCalls: calls.preview - previewBefore,
    sharedPickerVisible: Boolean(vm.$el.querySelector('.asset-picker')),
    previewButtonVisible: [...vm.$el.querySelectorAll('button')].some((button) => button.textContent.includes('Preview')),
  };
};
window.__TEST_CAPABILITY_ROUTE_LOGOUT_RACE__ = async () => {
  localStorage.setItem('token', 'route-session-a');
  localStorage.setItem('userInfo', JSON.stringify({ superAdmin: true }));
  let resolveCapabilities;
  Api.lesson.getRolloutCapabilities = (ok) => { resolveCapabilities = ok; };
  resetLessonRolloutCapabilities();
  const redirectedToLogin = new Promise((resolve) => {
    const removeHook = applicationRouter.afterEach((to) => {
      if (to.name !== 'login') return;
      removeHook();
      resolve();
    });
  });
  const navigation = applicationRouter.push({ name: 'LessonVisualLibrary' });
  for (let i = 0; i < 20 && !resolveCapabilities; i += 1) await Promise.resolve();
  applicationStore.commit('clearAuth');
  resolveCapabilities({ sharedVisualAuthoring: true, exactEspTftPreview: true });
  try {
    await navigation;
  } catch (error) {
    // Vue Router rejects the original push when the guard intentionally redirects.
    if (!VueRouter.isNavigationFailure(error, VueRouter.NavigationFailureType.redirected)) throw error;
  }
  await redirectedToLogin;
  return {
    route: applicationRouter.currentRoute.name,
    visualLibraryLoaded: applicationRouter.currentRoute.name === 'LessonVisualLibrary',
  };
};
editor.$nextTick(() => { window.__LESSON_BUILDER_READY__ = true; });
