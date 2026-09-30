/**
 * Mounted harness for the lesson metadata form (CourseLessons.vue).
 *
 * Exists so `scripts/check-course-taxonomy-browser.mjs` can prove, in a real
 * browser, that an unparseable age band surfaces the child-safety warning
 * BEFORE the lesson is saved. The assignment age gate fails open on such a
 * band, so this warning is the only thing standing between an author's typo
 * and a lesson with no age restriction.
 *
 * No backend: Api is stubbed, exactly like tests/browser/lesson-builder-main.js.
 */
import Vue from 'vue';
import ElementUI from 'element-ui';
import VueRouter from 'vue-router';
import CourseLessons from '@/views/CourseLessons.vue';
import Api from '@/apis/api';
import i18n from '@/i18n';

Vue.use(ElementUI);
Vue.use(VueRouter);
Vue.config.productionTip = false;
localStorage.setItem('token', 'course-taxonomy-test-session');

const calls = { created: [], updated: [], errors: [], warnings: [], pendingLessons: [], deferLessons: false };

Object.assign(Api.lesson, {
  listAuthoritativeLessons(courseId, ok) {
    if (calls.deferLessons) { calls.pendingLessons.push({ courseId, ok }); return; }
    ok([
      {
        lessonId: 'lesson-1', lessonKey: 'w01-d01-barn', title: 'Barn',
        status: 'draft', lessonVersion: 1, courseId, locale: 'en-US',
        ageBand: '4-6', topicTags: [], difficultyBand: 'beginner',
        estimatedDurationSec: 240, monitorable: true,
      },
    ]);
  },
  createLesson(courseId, payload, ok) {
    calls.created.push(JSON.parse(JSON.stringify(payload)));
    ok({ ...payload, lessonId: 'lesson-new' });
  },
  updateLesson(lessonId, payload, ok) {
    calls.updated.push(JSON.parse(JSON.stringify(payload)));
    ok({ ...payload, lessonId });
  },
  deleteLesson(lessonId, ok) { ok({}); },
});

CourseLessons.components.HeaderBar = { name: 'HeaderBar', render: (h) => h('header') };

const router = new VueRouter({ routes: [{ path: '/', component: { render: (h) => h('div') } }] });
await router.replace({ path: '/', query: { courseId: 'course-1', courseKey: 'w01-place-words', title: 'Place Words' } });
Vue.prototype.$message = {
  success() {},
  error(message) { calls.errors.push(message); },
  warning(message) { calls.warnings.push(message); },
};
Vue.prototype.$confirm = () => Promise.resolve();

const vm = new Vue({ router, i18n, render: (h) => h(CourseLessons) }).$mount('#app');
const view = vm.$children[0];

window.__COURSE_TAXONOMY_TEST__ = { view, calls };
window.__SWITCH_COURSE__ = async () => {
  calls.deferLessons = true;
  view.fetchList();
  const oldRequest = calls.pendingLessons[0];
  await router.replace({ path: '/', query: { courseId: 'course-2', courseKey: 'second-course', title: 'Second course' } });
  await Vue.nextTick();
  const currentRequest = calls.pendingLessons.find(call => call.courseId === 'course-2');
  if (!currentRequest) return { requested: false };
  const closed = !view.dialogVisible && !view.assignmentDialog.visible;
  currentRequest.ok([{ lessonId: 'second', lessonKey: 'second', title: 'Current course lesson', status: 'draft', topicTags: [] }]);
  oldRequest.ok([{ lessonId: 'stale', lessonKey: 'stale', title: 'Stale course lesson', status: 'draft', topicTags: [] }]);
  await Vue.nextTick();
  return { requested: true, closed, courseId: view.courseId, titles: view.list.map(row => row.title), text: view.$el.textContent };
};
window.__SET_AGE_BAND__ = async (band) => {
  view.form.ageBand = band;
  await Vue.nextTick();
  return {
    severity: view.ageBandSeverity,
    minimum: view.ageBandMinimum,
    unenforcedVisible: Boolean(document.querySelector('[data-testid="lesson-age-band-unenforced"]')),
    customVisible: Boolean(document.querySelector('[data-testid="lesson-age-band-custom"]')),
    warningText: (document.querySelector('[data-testid="lesson-age-band-unenforced"]') || {}).textContent || '',
  };
};
window.__OPEN_CREATE__ = async () => {
  view.openCreate();
  await Vue.nextTick();
  await new Promise((resolve) => setTimeout(resolve, 0));
  return { ageBand: view.form.ageBand, locale: view.form.locale };
};
view.$nextTick(() => { window.__COURSE_TAXONOMY_READY__ = true; });
