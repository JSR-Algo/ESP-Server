import Vue from 'vue';
import ElementUI from 'element-ui';
import VueI18n from 'vue-i18n';
import en from '../../src/i18n/en';
import TVideoRobotPreview from '../../src/components/lesson/TVideoRobotPreview.vue';
import { normalizeScenePath } from '../../src/components/lesson/tvideo-journey';

Vue.use(ElementUI);
Vue.use(VueI18n);
const urls = {
  background: '/tvideo-demo/assets/scenes/deep-barn-farm-background-6s.mp4',
  robot: '/tvideo-demo/assets/robot-alive/flight/flight-in.webm',
  walking: '/tvideo-demo/assets/robot-alive/flight/walk-toward.webm',
  object: '/tvideo-demo/assets/objects/barn.png',
};
new Vue({
  i18n: new VueI18n({ locale: 'en', messages: { en } }),
  data: () => ({ urls: { ...urls } }),
  render(h) {
    return h(TVideoRobotPreview, { ref: 'preview', props: {
      mediaUrl: (id) => this.urls[id],
      preset: { presetId: 'TEST_ONLY', presetVersion: 1, effects: {} },
      journey: {
        assets: { background: { assetVersionId: 'background' }, robotClips: ['flight', 'walking', 'greeting-teaching', 'celebration'].map(role => ({ role, assetVersionId: role === 'walking' ? 'walking' : 'robot' })) },
        steps: [{ stepKey: 'barn', targetWord: 'barn', teachingObject: { assetVersionId: 'object' } }],
        scenePath: normalizeScenePath({}),
      },
    } });
  },
  mounted() { window.__MEDIA_TEST__ = { root: this, preview: this.$refs.preview, urls }; },
}).$mount('#app');
