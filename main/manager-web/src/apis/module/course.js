import { getNestUrl } from '../api';
import { nestRequest, normalizeCourse } from '../nestHttp';

/**
 * Course customization CRUD — backed by the NestJS tbot-backend authoring API
 * (/v1/admin/courses), reached through the dev-server `/nestjs` proxy. This is a
 * DIFFERENT backend from manager-api (which owns device/agent/model/voice): the
 * course/lesson domain lives only in NestJS, and the robot reads published
 * lessons from there. Shared 2xx/envelope/normalize helpers live in ../nestHttp.
 */

export default {
  // GET /v1/admin/courses -> Course[]
  getCourseList(onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses`,
      method: 'GET',
      onSuccess: (payload) =>
        onSuccess((Array.isArray(payload) ? payload : []).map(normalizeCourse)),
      onError,
    });
  },

  // POST /v1/admin/courses { courseKey, title, locale, ageBand }
  createCourse(data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses`,
      method: 'POST',
      data,
      onSuccess: (p) => onSuccess(normalizeCourse(p)),
      onError,
    });
  },

  // PATCH /v1/admin/courses/:id { title?, locale?, ageBand? }
  updateCourse(courseId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses/${courseId}`,
      method: 'PATCH',
      data,
      onSuccess: (p) => onSuccess(normalizeCourse(p)),
      onError,
    });
  },

  // DELETE /v1/admin/courses/:id
  deleteCourse(courseId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses/${courseId}`,
      method: 'DELETE',
      onSuccess: () => onSuccess && onSuccess(),
      onError,
    });
  },

  // PATCH /v1/admin/courses/:id/template { isTemplate } — mark/unmark a shared template
  setTemplate(courseId, isTemplate, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses/${courseId}/template`,
      method: 'PATCH',
      data: { isTemplate },
      onSuccess: (p) => onSuccess(normalizeCourse(p)),
      onError,
    });
  },

  // POST /v1/admin/courses/:id/clone { courseKey, title? } — deep-clone a template
  // into a new custom draft course (lessons/steps/assets copied).
  cloneCourse(courseId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses/${courseId}/clone`,
      method: 'POST',
      data,
      onSuccess: (p) => onSuccess(normalizeCourse(p)),
      onError,
    });
  },
};
