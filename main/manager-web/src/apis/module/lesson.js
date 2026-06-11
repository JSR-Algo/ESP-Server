import { getNestUrl } from '../api';
import {
  nestRequest,
  nestUpload,
  normalizeLesson,
  normalizeStep,
  normalizeStepType,
} from '../nestHttp';

/**
 * Lesson / step / asset / validate / preview / publish CRUD — backed by the
 * NestJS tbot-backend authoring API (/v1/admin/*), reached through the `/nestjs`
 * proxy. Same conventions as course.js (2xx success, {data} envelope unwrap,
 * snake→camel). The render triple (robotState/pose/expression/phase/entrance) is
 * SERVER-DERIVED — never sent on createStep (the controller 400s if present).
 */

const RENDERER_VERSION = 'teebot-lesson-renderer.v1';

export default {
  // GET /v1/admin/courses/:courseId/lessons -> Lesson[]
  listLessons(courseId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses/${courseId}/lessons`,
      method: 'GET',
      onSuccess: (p) =>
        onSuccess((Array.isArray(p) ? p : []).map(normalizeLesson)),
      onError,
    });
  },

  // POST /v1/admin/courses/:courseId/lessons { lessonKey, title, locale, ageBand }
  createLesson(courseId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses/${courseId}/lessons`,
      method: 'POST',
      data,
      // The create row omits course_id; carry it from the path.
      onSuccess: (p) => onSuccess({ ...normalizeLesson(p), courseId }),
      onError,
    });
  },

  // GET /v1/admin/lessons/:lessonId
  getLesson(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}`,
      method: 'GET',
      onSuccess: (p) => onSuccess(normalizeLesson(p)),
      onError,
    });
  },

  // PATCH /v1/admin/lessons/:lessonId { title?, locale?, ageBand? } (draft only)
  updateLesson(lessonId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}`,
      method: 'PATCH',
      data,
      onSuccess: (p) => onSuccess(normalizeLesson(p)),
      onError,
    });
  },

  // DELETE /v1/admin/lessons/:lessonId (draft only)
  deleteLesson(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}`,
      method: 'DELETE',
      onSuccess: () => onSuccess && onSuccess(),
      onError,
    });
  },

  // GET /v1/admin/lessons/:lessonId/steps -> Step[]
  listSteps(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps`,
      method: 'GET',
      onSuccess: (p) =>
        onSuccess((Array.isArray(p) ? p : []).map(normalizeStep)),
      onError,
    });
  },

  // POST /v1/admin/lessons/:lessonId/steps — ONLY author fields (no render triple)
  createStep(lessonId, input, onSuccess, onError) {
    const data = {
      stepType: input.stepType,
      prompt: input.prompt,
      subject: input.subject,
    };
    if (input.helperText) data.helperText = input.helperText;
    if (input.l1TransferHint) data.l1TransferHint = input.l1TransferHint;
    // fillBlank choices (single-correct enforced server-side); only send when present
    if (Array.isArray(input.choices) && input.choices.length) data.choices = input.choices;
    // free-form scene/media body; only send when populated (server defaults to {})
    if (input.stepBody && Object.keys(input.stepBody).length) data.stepBody = input.stepBody;
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps`,
      method: 'POST',
      data,
      onSuccess: (p) => onSuccess(normalizeStep(p)),
      onError,
    });
  },

  // POST /v1/admin/lessons/:lessonId/steps/reorder { order: stepKey[] }
  reorderSteps(lessonId, order, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps/reorder`,
      method: 'POST',
      data: { order },
      onSuccess: (p) =>
        onSuccess((Array.isArray(p) ? p : []).map(normalizeStep)),
      onError,
    });
  },

  // DELETE /v1/admin/lessons/:lessonId/steps/:stepKey -> remaining steps
  deleteStep(lessonId, stepKey, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps/${stepKey}`,
      method: 'DELETE',
      onSuccess: (p) =>
        onSuccess((Array.isArray(p) ? p : []).map(normalizeStep)),
      onError,
    });
  },

  // GET /v1/admin/render/step-types -> authorable step types (builtins + author)
  listStepTypes(onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/render/step-types?rendererVersion=${encodeURIComponent(RENDERER_VERSION)}`,
      method: 'GET',
      onSuccess: (p) =>
        onSuccess((Array.isArray(p) ? p : []).map(normalizeStepType)),
      onError,
    });
  },

  // POST /v1/admin/lessons/:lessonId/assets/upload (multipart; server digests)
  // fields: optional { profile, layer, role, assetKey, critical }
  uploadAsset(lessonId, file, fields, onSuccess, onError) {
    nestUpload(`/lessons/${lessonId}/assets/upload`, file, fields, onSuccess, onError);
  },

  // POST /v1/admin/lessons/:lessonId/validate -> { valid, profiles }
  validate(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/validate`,
      method: 'POST',
      onSuccess,
      onError,
    });
  },

  // GET /v1/admin/lessons/:lessonId/manifest-preview?profile= -> { manifest, checksum, etag }
  manifestPreview(lessonId, profile, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/manifest-preview?profile=${encodeURIComponent(profile || 'espTft')}`,
      method: 'GET',
      onSuccess,
      onError,
    });
  },

  // POST /v1/admin/lessons/:lessonId/publish -> { lessonVersion, checksum, status, ... }
  publish(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/publish`,
      method: 'POST',
      onSuccess,
      onError,
    });
  },
};
