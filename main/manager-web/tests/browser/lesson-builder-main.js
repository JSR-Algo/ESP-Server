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

const calls = { update: [], visualFilters: [], validate: 0, preview: 0, errors: [], failNextUpdate: false, deferNextUpdate: false, deferNextValidate: false, pendingUpdates: [], pendingValidations: [] };
let steps = [
  { stepKey: 's1', stepType: 'greeting', prompt: 'Meet Pip', subject: 'pet', stepBody: { durationSec: 8 } },
  { stepKey: 's2', stepType: 'repeat', prompt: 'Say barn', subject: 'barn', stepBody: { durationSec: 12 } },
];
const sharedAssets = [{ assetId: '00000000-0000-4000-8000-000000000001', assetKey: 'object.barn', category: 'teachingObject', layer: 'teachingObject', versionId: '00000000-0000-4000-8000-000000000002', version: 2, path: 'sd://shared/barn.png', storagePath: 'sd://shared/barn.png', sha256: 'abc123', bytes: 60000, width: 160, height: 120, usageCount: 4 }];
const validation = { valid: true, profiles: ['espTft'], budgets: { espTft: { errors: [], warnings: [], metrics: { assetCount: 9, uniqueAssetCount: 7, sharedAssetCount: 2, packBytes: 222000, estimatedVisualPeakBytes: 640000, offlineReady: true, allPathsTerminate: true } } } };
const visualStates = ['teach', 'listen', 'thinking', 'correct', 'nearMiss', 'incorrect', 'retry', 'celebrate', 'completion'];
const manifest = {
  manifestVersion: 'teebot-lesson-renderer.v2',
  profile: 'espTft',
  physicalMotionOwner: 'server',
  rendererCapabilities: ['teebot-lesson-renderer.v2'],
  openingEntrance: { template: 'tvideoFlyWalk', preset: 'flyLandWalkGreet', policy: 'oncePerLessonSession', phases: ['hidden', 'flyIn', 'landFar', 'settle', 'walkToward', 'arriveNear', 'greetIdle', 'revealTeachingContent'], fallback: 'staticGreet' },
  pathsTerminate: true,
  steps: [
    { stepKey: 's1', prompt: 'Meet Pip', scene: {}, teachingWord: { text: 'PET' }, entrance: 'none', visualStates: Object.fromEntries(visualStates.map((state) => [state, { prompt: state, motionPreset: state, overlayKey: state }])) },
    { stepKey: 's2', prompt: 'Say barn', scene: {}, teachingWord: { text: 'BARN' }, entrance: 'none', visualStates: Object.fromEntries(visualStates.map((state) => [state, { prompt: state, motionPreset: state, overlayKey: state }])) },
  ]
};

Object.assign(Api.lesson, {
  getRolloutCapabilities(ok) { ok({ sharedVisualAuthoring: true, exactEspTftPreview: true }); },
  getLesson(id, ok) { ok({ lessonId: id, lessonKey: 'farm-1', title: 'Farm friends', status: 'draft', lessonVersion: 1, locale: 'vi' }); },
  listSteps(id, ok) { ok(steps.map((step) => ({ ...step, visualRefs: [...(step.visualRefs || [])] }))); },
  listStepTypes(ok) { ok([{ stepType: 'greeting', completionClass: 'passive' }, { stepType: 'repeat', completionClass: 'interactive' }]); },
  listSharedBackgrounds(ok) { ok([]); },
  listVisualAssets(filters, ok) { calls.visualFilters.push(filters); ok(sharedAssets); },
  updateStep(lessonId, stepKey, payload, ok, fail) {
    calls.update.push({ lessonId, stepKey, payload: JSON.parse(JSON.stringify(payload)) });
    if (calls.deferNextUpdate) { calls.deferNextUpdate = false; calls.pendingUpdates.push({ lessonId, stepKey, payload, ok, fail }); return; }
    if (calls.failNextUpdate) { calls.failNextUpdate = false; fail('forced update failure'); return; }
    const visualRefs = (payload.visualRefs || []).map((ref) => ({ ...ref, assetKey: sharedAssets.find((asset) => asset.versionId === ref.assetVersionId)?.assetKey || '' }));
    steps = steps.map((step) => step.stepKey === stepKey ? { ...step, ...payload, visualRefs } : step);
    ok({ ...payload, stepKey, visualRefs });
  },
  validate(id, ok, fail) { calls.validate += 1; if (calls.deferNextValidate) { calls.deferNextValidate = false; calls.pendingValidations.push({ ok, fail }); return; } ok(validation); },
  manifestPreview(id, profile, ok) { calls.preview += 1; ok({ manifest, checksum: 'checksum-1', etag: 'etag-1' }); },
  reorderSteps() {}, deleteStep() {}, publish() {}, updateLesson() {}, createStep() {},
});

LessonEditor.components.HeaderBar = { name: 'HeaderBar', render: (h) => h('header') };
LessonEditor.components.LessonAssetManager = { name: 'LessonAssetManager', props: ['lessonId'], mounted() { this.$emit('assets-loaded', sharedAssets); }, render: (h) => h('div') };

const router = new VueRouter({ routes: [{ path: '/', component: { render: (h) => h('div') } }] });
await router.replace({ path: '/', query: { lessonId: 'lesson-1' } });
Vue.prototype.$t = (key) => key;
Vue.prototype.$message = { success() {}, error(message) { calls.errors.push(message); }, warning() {} };
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
