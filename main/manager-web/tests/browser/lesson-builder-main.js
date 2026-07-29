import Vue from 'vue';
import ElementUI from 'element-ui';
import VueRouter from 'vue-router';
import LessonEditor from '@/views/LessonEditor.vue';
import Api from '@/apis/api';
import { resetLessonRolloutCapabilities } from '@/utils/lessonRolloutCapabilities';
import applicationRouter from '@/router';
import applicationStore from '@/store';

Vue.use(ElementUI);
Vue.use(VueRouter);
Vue.config.productionTip = false;
localStorage.setItem('token', 'lesson-builder-test-session');

const calls = { update: [], visualFilters: [], visualRefSets: [], validate: 0, preview: 0, errors: [], warnings: [], failNextUpdate: false, deferNextUpdate: false, deferNextValidate: false, pendingUpdates: [], pendingValidations: [] };
let steps = [
  { stepKey: 's1', stepType: 'greeting', prompt: 'Meet Pip', subject: 'pet', stepBody: { durationSec: 8 } },
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
  { assetId: 'teach-asset', assetKey: 'object.barn', category: 'teachingObject', layer: 'teachingObject', versionId: 'teach-v2', versionIdentity: 'teach-v2', version: 2, url: '/fixtures/object-barn-v2.mp4', mimeType: 'video/mp4', sha256: 'abc123', bytes: 60000, width: 160, height: 120, usageCount: 4, compatibilityMetadata: { codec: 'mjpeg', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 180, y: 100, width: 160, height: 120 }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 24, feather: 8 } } },
  { assetId: 'robot-asset', assetKey: 'robot.teach', category: 'robotPose', layer: 'robotOverlay', versionId: 'robot-v4', versionIdentity: 'robot-v4', version: 4, url: '/fixtures/robot-teach-v4.mp4', mimeType: 'video/mp4', sha256: 'robot-sha', bytes: 72000, width: 200, height: 220, usageCount: 1, compatibilityMetadata: { codec: 'mjpeg', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 8, y: 92, width: 200, height: 220 }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 24, feather: 8 } } },
  { assetId: 'robot-asset', assetKey: 'robot.teach', category: 'robotPose', layer: 'robotOverlay', versionId: 'robot-v5', versionIdentity: 'robot-v5', version: 5, url: '/fixtures/robot-teach-v5.mp4', mimeType: 'video/mp4', sha256: 'robot-sha-v5', bytes: 74000, width: 200, height: 220, usageCount: 0, compatibilityMetadata: { codec: 'mjpeg', fps: 10, durationMs: 3000, frameCount: 30, rect: { x: 8, y: 92, width: 200, height: 220 }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 24, feather: 8 } } },
];
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

Object.assign(Api.lesson, {
  getRolloutCapabilities(ok) { ok({ sharedVisualAuthoring: true, exactEspTftPreview: true }); },
  getLesson(id, ok) { ok({ lessonId: id, lessonKey: 'farm-1', title: 'Farm friends', status: 'draft', lessonVersion: 1, locale: 'vi' }); },
  listSteps(id, ok) { ok(steps.map((step) => ({ ...step, visualRefs: [...(step.visualRefs || [])] }))); },
  listStepTypes(ok) { ok([{ stepType: 'greeting', completionClass: 'passive' }, { stepType: 'repeat', completionClass: 'interactive' }]); },
  listSharedBackgrounds(ok) { ok([]); },
  listVisualAssets(filters, ok) { calls.visualFilters.push(filters); ok(sharedAssets.filter((asset) => asset.category === filters.category)); },
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
Vue.prototype.$t = (key) => key;
Vue.prototype.$message = { success() {}, error(message) { calls.errors.push(message); }, warning(message) { calls.warnings.push(message); } };
Vue.prototype.$confirm = () => Promise.resolve();
let vm = new Vue({ router, render: (h) => h(LessonEditor) }).$mount('#app');

const editor = vm.$children[0];
window.__LESSON_BUILDER_TEST__ = { editor, calls, sharedAssets, validation, manifest };
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
