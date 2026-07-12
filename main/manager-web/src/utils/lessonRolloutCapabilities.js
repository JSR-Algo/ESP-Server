import Api from '@/apis/api';
import {
  createLessonRolloutCapabilitiesLoader,
  getLessonRolloutSessionGeneration,
} from './lessonRolloutCapabilitiesCore.mjs';

export { NO_LESSON_ROLLOUT_CAPABILITIES } from './lessonRolloutCapabilitiesCore.mjs';

const loader = createLessonRolloutCapabilitiesLoader({
  getSessionKey: getLessonRolloutSessionKey,
  load: () => new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error('lesson rollout capabilities timed out')), 5000);
    const finish = (callback) => (value) => {
      clearTimeout(timeout);
      callback(value);
    };
    try {
      Api.lesson.getRolloutCapabilities(finish(resolve), finish(reject));
    } catch (error) {
      clearTimeout(timeout);
      reject(error);
    }
  }),
});

export function loadLessonRolloutCapabilities() {
  return loader.load();
}

export function resetLessonRolloutCapabilities() {
  loader.reset();
}

export function getLessonRolloutSessionKey() {
  const token = localStorage.getItem('token');
  return token ? `${getLessonRolloutSessionGeneration()}:${token}` : '';
}
