import Vue from 'vue';
import RobotLessonPreview from '../../src/components/lesson/RobotLessonPreview.vue';

// Qualification supplies an unchanged persisted response and records its hash.
fetch('/candidate.json').then(response => response.json()).then(candidate => {
  const root = new Vue({
    data: () => ({ candidate, stepIndex: 0, visible: true }),
    render(h) {
      return h('main', [h('p', 'Recorded persisted manifest: component/media verification; origin and full-scene failures remain visible.'),
        this.visible ? h(RobotLessonPreview, { ref: 'preview', props: { manifestPreview: this.candidate, stepIndex: this.stepIndex } }) : h('p', 'Preview unmounted')]);
    }
  }).$mount('#app');
  window.__V5_CANDIDATE__ = root;
});
