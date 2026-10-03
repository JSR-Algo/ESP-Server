import Vue from 'vue';
import ElementUI from 'element-ui';
import VueRouter from 'vue-router';
import CourseInsights from '@/views/CourseInsights.vue';
import CourseLessons from '@/views/CourseLessons.vue';
import Api from '@/apis/api';
import i18n from '@/i18n';
Vue.use(ElementUI);
Vue.use(VueRouter);
Vue.config.productionTip = false;
const calls = {};
for (const method of ['listLearners', 'previewLearnerLessons', 'updateLearnerPersonality', 'getCourseQuality']) {
  Api.courseInsights[method] = (...args) => (calls[method] ||= []).push({ args, ok: args[args.length - 2], fail: args[args.length - 1] });
}
Api.lesson.listAuthoritativeLessons = (...args) => (calls.listAuthoritativeLessons ||= []).push({ args, ok: args[1], fail: args[2] });
for (const view of [CourseInsights, CourseLessons]) view.components.HeaderBar = { render: h => h('header') };
const messages = [];
Vue.prototype.$message = Object.fromEntries(['success', 'error', 'warning'].map(type => [type, message => messages.push({ type, message })]));
const router = new VueRouter({ routes: [{ path: '/', component: CourseInsights }, { path: '/lessons', component: CourseLessons }] });
const root = new Vue({ router, i18n, render: h => h('router-view') }).$mount('#app');
window.__FE01__ = { root, router, calls, messages, tick: () => Vue.nextTick() };
Vue.nextTick(() => { window.__FE01_READY__ = true; });
