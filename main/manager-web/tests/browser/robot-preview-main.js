import Vue from 'vue';
import RobotLessonPreview from '../../src/components/lesson/RobotLessonPreview.vue';

function svg(label, colors) {
  const body = `<svg xmlns="http://www.w3.org/2000/svg" width="480" height="320"><defs><linearGradient id="g" x2="1" y2="1"><stop stop-color="${colors[0]}"/><stop offset="1" stop-color="${colors[1]}"/></linearGradient></defs><rect width="480" height="320" fill="url(#g)"/><circle cx="240" cy="148" r="82" fill="${colors[2]}"/><text x="240" y="158" text-anchor="middle" font-family="sans-serif" font-size="34" font-weight="700" fill="#17211b">${label}</text></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(body)}`;
}

const query = new URLSearchParams(window.location.search);
const minutes = Number(query.get('minutes')) || 3;
const target = { 3: ['barn', '#92d4db', '#ddec8c', '#ed613e'], 5: ['apple', '#f8c76b', '#d9ed9c', '#e54e32'], 8: ['turtle', '#72c5c8', '#175c77', '#a8d76c'] }[minutes];
const background = svg(`${minutes} minute lesson`, target.slice(1));
const object = svg(target[0], ['#fff8dc', '#fff8dc', target[3]]);
const robot = svg('TeeBot', ['#edf4ec', '#78a994', '#b9ec45']);

const responsePaths = {
  correct: { prompt: `Great ${target[0]}!`, motionPreset: 'celebrate' },
  nearMiss: { prompt: `Almost. Listen: ${target[0]}.`, motionPreset: 'encourage' },
  incorrect: { prompt: `Let us try ${target[0]} together.`, motionPreset: 'gentle-shake' },
  silence: { prompt: `Take your time with ${target[0]}.`, motionPreset: 'patient-wait' },
  sttUnavailable: { prompt: `Listening is unavailable. Follow ${target[0]}.`, motionPreset: 'calm-idle' },
  missingOptionalVisual: { prompt: `Use the ${target[0]} word card.`, motionPreset: 'teach' }
};

const steps = Array.from({ length: minutes === 8 ? 4 : minutes === 5 ? 3 : 2 }, (_, index) => ({
  id: `step-${index + 1}`,
  prompt: `Learn ${target[0]}.`,
  motionPreset: 'teach',
  responsePaths,
  scene: {
    backgroundScene: { mode: 'poster', poster: { src: background, fit: 'cover' }, video: query.has('warning') ? { src: 'forbidden.mp4' } : null },
    teachingObject: { primaryWord: target[0], asset: { src: object } },
    robotOverlay: { pose: 'teach', anchor: 'bottomLeft', asset: { src: robot } }
  }
}));

new Vue({
  render: (createElement) => createElement(RobotLessonPreview, {
    props: { manifest: { manifestVersion: 'teebot-lesson-renderer.v1', lessonId: `${minutes}m-browser`, lessonVersion: 1, profile: 'espTft', durationMinutes: minutes, steps }, stepIndex: minutes === 8 ? 2 : 0 }
  }),
  mounted() { this.$nextTick(() => { window.__ROBOT_PREVIEW_READY__ = true; }); }
}).$mount('#app');
