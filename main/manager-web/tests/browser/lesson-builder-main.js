import Vue from 'vue';
import ElementUI from 'element-ui';
import VueRouter from 'vue-router';
import LessonEditor from '@/views/LessonEditor.vue';
import Api from '@/apis/api';

Vue.use(ElementUI);
Vue.use(VueRouter);
Vue.config.productionTip = false;

const calls = { update: [], visualRefs: [], visualFilters: [], validate: 0, preview: 0 };
const steps = [
  { stepKey: 's1', stepType: 'greeting', prompt: 'Meet Pip', subject: 'pet', stepBody: { durationSec: 8 } },
  { stepKey: 's2', stepType: 'repeat', prompt: 'Say barn', subject: 'barn', stepBody: { durationSec: 12 } },
];
const sharedAssets = [{ assetKey: 'object.barn', category: 'teachingObject', versionId: '00000000-0000-4000-8000-000000000002', version: 2, storagePath: 'sd://shared/barn.png', sha256: 'abc123', bytes: 60000, width: 160, height: 120, usageCount: 4 }];
const validation = { valid: true, profiles: ['espTft'], budgets: { espTft: { errors: [], warnings: [], metrics: { assetCount: 9, uniqueAssetCount: 7, sharedAssetCount: 2, packBytes: 222000, estimatedVisualPeakBytes: 640000, offlineReady: true, allPathsTerminate: true } } } };
const manifest = { profile: 'espTft', pathsTerminate: true, steps: [{ stepKey: 's1', prompt: 'Meet Pip', scene: {}, teachingWord: { text: 'PET' } }, { stepKey: 's2', prompt: 'Say barn', scene: {}, teachingWord: { text: 'BARN' } }] };

Object.assign(Api.lesson, {
  getLesson(id, ok) { ok({ lessonId: id, lessonKey: 'farm-1', title: 'Farm friends', status: 'draft', lessonVersion: 1, locale: 'vi' }); },
  listSteps(id, ok) { ok(steps.map((step) => ({ ...step }))); },
  listStepTypes(ok) { ok([{ stepType: 'greeting', completionClass: 'passive' }, { stepType: 'repeat', completionClass: 'interactive' }]); },
  listVisualAssets(filters, ok) { calls.visualFilters.push(filters); ok(sharedAssets); },
  setStepVisualRef(lessonId, stepKey, slot, assetVersionId, ok) { calls.visualRefs.push({ lessonId, stepKey, slot, body: { assetVersionId } }); ok({}); },
  updateStep(lessonId, stepKey, payload, ok) { calls.update.push({ lessonId, stepKey, payload: JSON.parse(JSON.stringify(payload)) }); ok({ ...payload, stepKey }); },
  validate(id, ok) { calls.validate += 1; ok(validation); },
  manifestPreview(id, profile, ok) { calls.preview += 1; ok({ manifest, checksum: 'checksum-1', etag: 'etag-1' }); },
  reorderSteps() {}, deleteStep() {}, publish() {}, updateLesson() {}, createStep() {},
});

LessonEditor.components.HeaderBar = { name: 'HeaderBar', render: (h) => h('header') };
LessonEditor.components.LessonAssetManager = { name: 'LessonAssetManager', props: ['lessonId'], render: (h) => h('div') };

const router = new VueRouter({ routes: [{ path: '/', component: { render: (h) => h('div') } }] });
router.replace({ path: '/', query: { lessonId: 'lesson-1' } });
Vue.prototype.$t = (key) => key;
Vue.prototype.$message = { success() {}, error(message) { throw new Error(message); }, warning() {} };
Vue.prototype.$confirm = () => Promise.resolve();
const vm = new Vue({ router, render: (h) => h(LessonEditor) }).$mount('#app');

const editor = vm.$children[0];
window.__LESSON_BUILDER_TEST__ = { editor, calls, sharedAssets, validation, manifest };
editor.$nextTick(() => { window.__LESSON_BUILDER_READY__ = true; });
