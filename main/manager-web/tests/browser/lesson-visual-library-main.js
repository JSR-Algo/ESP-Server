import Vue from 'vue';
import ElementUI from 'element-ui';
import Api from '../../src/apis/api';
import AssetImpactDialog from '../../src/components/lesson/AssetImpactDialog.vue';
import LessonVisualAssetDetail from '../../src/views/LessonVisualAssetDetail.vue';
import LessonVisualLibrary from '../../src/views/LessonVisualLibrary.vue';

window.addEventListener('error', (event) => { window.__LESSON_VISUAL_ERROR__ = event.error?.stack || event.message; });
window.addEventListener('unhandledrejection', (event) => { window.__LESSON_VISUAL_ERROR__ = event.reason?.stack || String(event.reason); });

Vue.use(ElementUI);
Vue.prototype.$t = (key, values) => values ? `${key}:${JSON.stringify(values)}` : key;
Vue.prototype.$message = { error() {}, warning() {}, success() {} };

const HeaderStub = { name: 'HeaderBar', template: '<header data-testid="header-stub" />' };
LessonVisualLibrary.components.HeaderBar = HeaderStub;
LessonVisualAssetDetail.components.HeaderBar = HeaderStub;
LessonVisualAssetDetail.computed.assetKey = () => 'object.apple';

const versions = [
  { assetId: 'asset-1', assetKey: 'object.apple', category: 'teachingObject', title: 'Apple master', versionId: '00000000-0000-4000-8000-000000000002', version: 2, profile: 'mobile', width: 640, height: 480, bytes: 9200, sha256: 'b'.repeat(64), publicationState: 'published', usageCount: 2 },
  { assetId: 'asset-1', assetKey: 'object.apple', category: 'teachingObject', title: 'Apple robot', versionId: '00000000-0000-4000-8000-000000000001', version: 1, profile: 'espTft', width: 160, height: 120, bytes: 1200, sha256: 'a'.repeat(64), publicationState: 'published', usageCount: 3 },
  { assetId: 'asset-2', assetKey: 'scene.park', category: 'scene', title: 'Park', versionId: '00000000-0000-4000-8000-000000000003', version: 1, profile: 'espTft', width: 480, height: 320, bytes: 4200, sha256: 'c'.repeat(64), publicationState: 'published', usageCount: 1 },
];
const usages = [
  { courseId: 'course-a', courseKey: 'english-a1', lessonId: '00000000-0000-4000-8000-000000000011', lessonKey: 'apple-draft', lessonVersion: 2, lessonStatus: 'draft', stepKey: 'teach', slot: 'teachingObject', activeAssignmentCount: 0 },
  { courseId: 'course-a', courseKey: 'english-a1', lessonId: '00000000-0000-4000-8000-000000000012', lessonKey: 'apple-live', lessonVersion: 1, lessonStatus: 'published', stepKey: 'teach', slot: 'teachingObject', activeAssignmentCount: 4 },
];
const calls = { list: [], detail: [], impact: [], replace: [] };
Api.lesson.listVisualAssets = (filters, success) => { calls.list.push(filters); success(versions); };
Api.lesson.getVisualAssetDetail = (assetKey, filters, success) => { calls.detail.push({ assetKey, filters }); success({ asset: { assetKey, category: 'teachingObject', title: 'Apple' }, sourceVersionId: filters.sourceVersionId || versions[0].versionId, versions: versions.slice(0, 2), usages }); };
Api.lesson.visualReplacementImpact = (payload, success) => { calls.impact.push(structuredClone(payload)); success({ courses: 1, lessons: payload.mode === 'global' ? 2 : payload.lessonIds.length, publishedVersions: 1, activeAssignments: 4 }); };
Api.lesson.replaceVisualAsset = (payload, success) => { calls.replace.push(structuredClone(payload)); success({ targetVersionId: payload.targetVersionId, clonedAssetKey: payload.mode === 'cloneForLesson' ? 'clone.private' : undefined }); };

const library = new Vue({ render: (h) => h(LessonVisualLibrary) }).$mount('#library');
const detail = new Vue({ render: (h) => h(LessonVisualAssetDetail) }).$mount('#detail');
const impact = new Vue({ render: (h) => h(AssetImpactDialog, { props: { visible: true, mode: 'selectedLessons', impact: { courses: 1, lessons: 1, publishedVersions: 1, activeAssignments: 4 } } }) }).$mount('#impact');

Vue.nextTick(() => {
  window.__LESSON_VISUAL_TEST__ = { library: library.$children[0], detail: detail.$children[0], impact: impact.$children[0], calls, versions, usages, Api };
  window.__LESSON_VISUAL_READY__ = true;
});
