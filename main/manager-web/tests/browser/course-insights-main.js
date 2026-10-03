// Component interaction fixture; API callbacks are controlled, not a live backend.
import Vue from 'vue';
import ElementUI from 'element-ui';
import VueRouter from 'vue-router';
import VueI18n from 'vue-i18n';
import en from '../../src/i18n/en';
import CourseInsights from '../../src/views/CourseInsights.vue';
import Api from '../../src/apis/api';

Vue.use(ElementUI);
Vue.use(VueRouter);
Vue.use(VueI18n);
const learner = id => ({
  childId: id, childName: `Learner ${id}`, parentEmail: 'fixture@local.invalid',
  personality: { interests: [id.toLowerCase()], learningStyle: 'visual', parentCareer: '', vocabularyLevel: 'basic', attentionSpanSec: 120 },
  stats: { assignments: 0, completedAssignments: 0, completionRate: 0 },
});
const rows = [learner('A'), learner('B')];
const calls = { previews: [], saves: [], quality: [], errors: [] };
Object.assign(Api.courseInsights, {
  listLearners(params, ok) { ok(rows, { page: params.page, pageSize: params.pageSize, total: rows.length, totalPages: 1 }); },
  previewLearnerLessons(childId, params, ok, fail) { calls.previews.push({ childId, params, ok, fail }); },
  updateLearnerPersonality(childId, payload, ok, fail) { calls.saves.push({ childId, payload, ok, fail }); },
  getCourseQuality(params, ok, fail) { calls.quality.push({ params, ok: rows => ok(rows, { page: params.page, pageSize: params.pageSize, total: rows.length, totalPages: rows.length ? 1 : 0 }), fail }); },
});
CourseInsights.components.HeaderBar = { render: h => h('header') };
Vue.prototype.$message = { success() {}, error(message) { calls.errors.push(message); } };
const router = new VueRouter({ routes: [{ path: '/', component: { render: h => h('div') } }] });
const vm = new Vue({ router, i18n: new VueI18n({ locale: 'en', messages: { en } }), render: h => h(CourseInsights) }).$mount('#app');
window.__INSIGHTS_TEST__ = { view: vm.$children[0], calls, rows, learner };
