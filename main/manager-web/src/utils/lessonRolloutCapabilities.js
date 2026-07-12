import Api from '@/apis/api';
import {
  loadLessonRolloutCapabilitiesWith,
  NO_LESSON_ROLLOUT_CAPABILITIES,
} from './lessonRolloutCapabilitiesCore.mjs';

export { NO_LESSON_ROLLOUT_CAPABILITIES } from './lessonRolloutCapabilitiesCore.mjs';

export function loadLessonRolloutCapabilities() {
  return loadLessonRolloutCapabilitiesWith(Api.lesson);
}
