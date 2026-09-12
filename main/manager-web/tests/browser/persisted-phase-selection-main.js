import Vue from 'vue';
import LessonStepNavigator from '../../src/components/lesson/LessonStepNavigator.vue';
import RobotLessonPreview from '../../src/components/lesson/RobotLessonPreview.vue';
import manifest from '../fixtures/persisted-multi-activity-v5.json';

Vue.config.productionTip = false;
new Vue({
  data: { selectedStepIndex: 0 },
  render(h) {
    return h('main', [
      h(LessonStepNavigator, {
        props: { value: this.selectedStepIndex, editable: false, steps: manifest.steps.map(step => ({
          stepKey: step.id, stepType: step.type, prompt: step.prompt,
        })) },
        on: { input: index => { this.selectedStepIndex = index; } },
      }),
      h(RobotLessonPreview, { props: { manifest, stepIndex: this.selectedStepIndex } }),
    ]);
  },
}).$mount('#app');
