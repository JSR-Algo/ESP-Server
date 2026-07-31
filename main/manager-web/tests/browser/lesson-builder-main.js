import Vue from 'vue';
import ElementUI from 'element-ui';
import VueRouter from 'vue-router';
import LessonEditor from '@/views/LessonEditor.vue';
import Api from '@/apis/api';
import { resetLessonRolloutCapabilities } from '@/utils/lessonRolloutCapabilities';
import applicationRouter from '@/router';
import applicationStore from '@/store';
import { normalizeFlattenedDerivativeStatusResponse } from '@/components/lesson/flattened-derivative-status';
import { createFarmJourneyDraft, requiredCueIds } from '@/components/lesson/tvideo-journey';

Vue.use(ElementUI);
Vue.use(VueRouter);
Vue.config.productionTip = false;
localStorage.setItem('token', 'lesson-builder-test-session');

const calls = { update: [], visualFilters: [], visualRefSets: [], lessonVisualSets: [], journeyLoads: [], journeySaves: [], validate: 0, preview: 0, errors: [], warnings: [], failNextUpdate: false, failNextJourneySave: false, deferNextUpdate: false, deferNextValidate: false, deferNextJourneySave: false, pendingUpdates: [], pendingValidations: [], pendingJourneySaves: [] };
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
  { assetId: 'scene-asset', assetKey: 'scene.farm', category: 'scene', layer: 'backgroundScene', versionId: 'scene-v3', versionIdentity: 'scene-v3', version: 3, url: '/fixtures/scene-farm-v3.mp4', mimeType: 'video/mp4', sha256: 'scene-sha', bytes: 180000, width: 480, height: 320, usageCount: 2, compatibilityMetadata: { codec: 'mjpeg', fps: 15, durationMs: 3200, frameCount: 48, rect: { x: 0, y: 0, width: 480, height: 320 }, chromaKey: null } },
  { assetId: 'scene-asset', assetKey: 'scene.farm', category: 'scene', layer: 'backgroundScene', versionId: 'scene-v4', versionIdentity: 'scene-v4', version: 4, url: '/fixtures/scene-farm-v4.mp4', mimeType: 'video/mp4', sha256: 'scene-sha-v4', bytes: 181000, width: 480, height: 320, usageCount: 0, compatibilityMetadata: { codec: 'mjpeg', fps: 15, durationMs: 3200, frameCount: 48, rect: { x: 0, y: 0, width: 480, height: 320 }, chromaKey: null } },
  { assetId: 'teach-asset', assetKey: 'object.barn', category: 'teachingObject', layer: 'teachingObject', versionId: 'teach-v2', versionIdentity: 'teach-v2', version: 2, url: '/fixtures/object-barn-v2.mp4', mimeType: 'video/mp4', sha256: 'abc123', bytes: 60000, width: 160, height: 120, usageCount: 4, compatibilityMetadata: { codec: 'mjpeg', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 180, y: 100, width: 160, height: 120 }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 24, feather: 8 } } },
  { assetId: 'teach-asset', assetKey: 'object.barn', category: 'teachingObject', layer: 'teachingObject', versionId: 'teach-v3', versionIdentity: 'teach-v3', version: 3, url: '/fixtures/object-barn-v3.mp4', mimeType: 'video/mp4', sha256: 'abc124', bytes: 61000, width: 160, height: 120, usageCount: 0, compatibilityMetadata: { codec: 'mjpeg', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 180, y: 100, width: 160, height: 120 }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 24, feather: 8 } } },
  { assetId: 'robot-asset', assetKey: 'robot.teach', category: 'robotPose', layer: 'robotOverlay', versionId: 'robot-v4', versionIdentity: 'robot-v4', version: 4, url: '/fixtures/robot-teach-v4.mp4', mimeType: 'video/mp4', sha256: 'robot-sha', bytes: 72000, width: 200, height: 220, usageCount: 1, compatibilityMetadata: { codec: 'mjpeg', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 8, y: 92, width: 200, height: 220 }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 24, feather: 8 } } },
  { assetId: 'robot-asset', assetKey: 'robot.teach', category: 'robotPose', layer: 'robotOverlay', versionId: 'robot-v5', versionIdentity: 'robot-v5', version: 5, url: '/fixtures/robot-teach-v5.mp4', mimeType: 'video/mp4', sha256: 'robot-sha-v5', bytes: 74000, width: 200, height: 220, usageCount: 0, compatibilityMetadata: { codec: 'mjpeg', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 8, y: 92, width: 200, height: 220 }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 24, feather: 8 } } },
  { assetId: 'journey-bg', assetKey: 'journey.farm', category: 'scene', layer: 'backgroundScene', versionId: '10000000-0000-4000-8000-000000000001', version: 1, url: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4', mimeType: 'video/mp4', sha256: '1'.repeat(64), bytes: 1000, width: 480, height: 320, usageCount: 0 },
  { assetId: 'journey-bg', assetKey: 'journey.farm.alt', category: 'scene', layer: 'backgroundScene', versionId: '10000000-0000-4000-8000-000000000002', version: 2, url: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4', mimeType: 'video/mp4', sha256: '8'.repeat(64), bytes: 1000, width: 480, height: 320, usageCount: 0 },
  { assetId: 'journey-barn', assetKey: 'journey.barn', category: 'teachingObject', layer: 'teachingObject', versionId: '30000000-0000-4000-8000-000000000001', version: 1, url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL8WQAAAABJRU5ErkJggg==', mimeType: 'image/png', sha256: '6'.repeat(64), bytes: 68, width: 192, height: 192, usageCount: 0 },
  { assetId: 'journey-hay', assetKey: 'journey.hay', category: 'teachingObject', layer: 'teachingObject', versionId: '30000000-0000-4000-8000-000000000002', version: 1, url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL8WQAAAABJRU5ErkJggg==', mimeType: 'image/png', sha256: '7'.repeat(64), bytes: 68, width: 192, height: 192, usageCount: 0 },
  ...['flight', 'walking', 'greeting-teaching', 'celebration'].map((role, index) => ({ assetId: `journey-${role}`, assetKey: `journey.robot.${role}`, category: 'robotPose', layer: 'robotOverlay', role, versionId: `20000000-0000-4000-8000-00000000000${index + 1}`, version: 1, url: `/fixtures/${role}.webm`, mimeType: 'video/webm', sha256: String(index + 2).repeat(64), bytes: 1000, width: 200, height: 220, usageCount: 0 })),
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
  'legacy-v1': 'teebot-lesson-renderer.v1',
  'legacy-v2': 'teebot-lesson-renderer.v2',
  'legacy-v3': 'teebot-lesson-renderer.v3',
};

Object.assign(Api.lesson, {
  getRolloutCapabilities(ok) { ok({ sharedVisualAuthoring: true, exactEspTftPreview: true }); },
  getLesson(id, ok) { ok({ lessonId: id, lessonKey: 'farm-1', title: 'Farm friends', status: 'draft', lessonVersion: 1, locale: 'vi', manifestVersion: lessonManifestVersions[id] || 'teebot-lesson-renderer.v2' }); },
  getTVideoJourneyPreset(ok) { ok(journeyPreset); },
  getTVideoJourney(id, ok) { calls.journeyLoads.push(id); ok({ state: 'not-configured', lessonId: id, cinematicSourceRevision: 0, set: { state: 'invalid', issues: [{ code: 'MISSING_CUES', cueIds: [] }] }, statuses: [], publishReady: false }); },
  saveTVideoJourney(id, payload, ok, fail) {
    calls.journeySaves.push(JSON.parse(JSON.stringify(payload)));
    if (calls.deferNextJourneySave) { calls.deferNextJourneySave = false; calls.pendingJourneySaves.push({ id, payload, ok, fail }); return; }
    if (calls.failNextJourneySave) { calls.failNextJourneySave = false; fail('journey save failed', { status: 422, data: { error: { code: 'ASSET_PIN_INVALID', details: { role: 'flight' } } } }); return; }
    ok({ state: 'configured', lessonId: id, lessonVersion: 2, sourceRevision: 2, cinematicSourceRevision: 2, journey: payload, set: { state: 'invalid', issues: [{ code: 'QUEUED_CUES', cueIds: requiredCueIds(payload.steps) }] }, statuses: [], publishReady: false });
  },
  getFlattenedDerivativeStatus(lessonId, lessonVersion, ok) {
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
const testI18n = Vue.observable({ locale: 'keys' });
const journeyTranslations = {
  en: {
    'lesson.tvideoJourney.field.x': 'Horizontal position', 'lesson.tvideoJourney.field.y': 'Vertical position',
    'lesson.tvideoJourney.field.width': 'Safe width', 'lesson.tvideoJourney.field.height': 'Safe height',
    'lesson.tvideoJourney.field.stepKey': 'Step key', 'lesson.tvideoJourney.pronunciation.segments': 'Approved segments',
    'lesson.tvideoJourney.pronunciation.phonemes': 'Approved phonemes',
    'lesson.tvideoJourney.branch.target': 'Target word', 'lesson.tvideoJourney.branch.meaning_vi': 'Vietnamese meaning',
    'lesson.tvideoJourney.branch.related': 'Related concept', 'lesson.tvideoJourney.branch.silence': 'Silence',
    'lesson.tvideoJourney.branch.uncertain': 'Uncertain contribution', 'lesson.tvideoJourney.branch.retry_level_1': 'Retry coaching · level 1',
    'lesson.tvideoJourney.branch.retry_level_2': 'Retry coaching · level 2', 'lesson.tvideoJourney.branch.retry_level_3': 'Retry coaching · level 3',
  },
  vi: {
    'lesson.tvideoJourney.field.x': 'Vị trí ngang', 'lesson.tvideoJourney.field.y': 'Vị trí dọc',
    'lesson.tvideoJourney.field.width': 'Chiều rộng an toàn', 'lesson.tvideoJourney.field.height': 'Chiều cao an toàn',
    'lesson.tvideoJourney.field.stepKey': 'Khóa bước', 'lesson.tvideoJourney.pronunciation.segments': 'Âm đoạn đã duyệt',
    'lesson.tvideoJourney.pronunciation.phonemes': 'Âm vị đã duyệt',
    'lesson.tvideoJourney.branch.target': 'Từ mục tiêu', 'lesson.tvideoJourney.branch.meaning_vi': 'Nghĩa tiếng Việt',
    'lesson.tvideoJourney.branch.related': 'Khái niệm liên quan', 'lesson.tvideoJourney.branch.silence': 'Im lặng',
    'lesson.tvideoJourney.branch.uncertain': 'Đóng góp chưa chắc chắn', 'lesson.tvideoJourney.branch.retry_level_1': 'Hướng dẫn thử lại · mức 1',
    'lesson.tvideoJourney.branch.retry_level_2': 'Hướng dẫn thử lại · mức 2', 'lesson.tvideoJourney.branch.retry_level_3': 'Hướng dẫn thử lại · mức 3',
  },
};
Vue.prototype.$t = function translate(key, params) {
  const template = (journeyTranslations[testI18n.locale] && journeyTranslations[testI18n.locale][key]) || key;
  return Object.entries(params || {}).reduce((value, [name, replacement]) => value.replace(`{${name}}`, replacement), template);
};
Vue.prototype.$message = { success() {}, error(message) { calls.errors.push(message); }, warning(message) { calls.warnings.push(message); } };
Vue.prototype.$confirm = () => Promise.resolve();
let vm = new Vue({ router, render: (h) => h(LessonEditor) }).$mount('#app');

const editor = vm.$children[0];
window.__LESSON_BUILDER_TEST__ = { editor, calls, sharedAssets, validation, manifest };
window.__SET_TEST_LOCALE__ = async (locale) => { testI18n.locale = locale; await Vue.nextTick(); };
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
  vm = new Vue({ router, render: (h) => h(LessonEditor) }).$mount(root);
  const disabledEditor = vm.$children[0];
  await new Promise((resolve) => disabledEditor.$nextTick(resolve));
  await new Promise((resolve) => setTimeout(resolve, 0));
  disabledEditor.doPreview();
  await disabledEditor.$nextTick();
  return {
    visualCalls: calls.visualFilters.length - visualBefore,
    previewCalls: calls.preview - previewBefore,
    sharedPickerVisible: Boolean(vm.$el.querySelector('.asset-picker')),
    previewButtonVisible: [...vm.$el.querySelectorAll('button')].some((button) => button.textContent.includes('lesson.previewManifest')),
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
