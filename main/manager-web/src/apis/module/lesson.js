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
 * snake→camel). robotState/pose/phase/entrance are SERVER-DERIVED — never sent on
 * createStep (the controller 400s if present). `expression` is the one overridable
 * lever: sent via an explicit `renderOverride` envelope, validated to the
 * firmware-supported set server-side.
 */

const RENDERER_VERSION = 'teebot-lesson-renderer.v1';

function normalizeVisualAsset(raw) {
  const r = raw || {};
  return {
    assetId: r.id ?? r.assetId ?? '', assetKey: r.asset_key ?? r.assetKey ?? '', category: r.category ?? '', title: r.title ?? '',
    versionId: r.version_id ?? r.versionId ?? '', version: Number(r.version ?? 0), profile: r.profile ?? '', storagePath: r.storage_path ?? r.storagePath ?? '',
    sha256: r.sha256 ?? '', mimeType: r.mime_type ?? r.mimeType ?? '', bytes: Number(r.bytes ?? 0), width: Number(r.width ?? 0), height: Number(r.height ?? 0),
    publicationState: r.publication_state ?? r.publicationState ?? 'draft', usageCount: Number(r.usage_count ?? r.usageCount ?? 0),
  };
}

export default {
  listVisualAssets(filters, onSuccess, onError) {
    const query = new URLSearchParams();
    if (filters && filters.category) query.set('category', filters.category);
    if (filters && filters.profile) query.set('profile', filters.profile);
    nestRequest({ url: `${getNestUrl()}/lesson-visual-assets${query.toString() ? `?${query}` : ''}`, method: 'GET', onSuccess: (p) => onSuccess((Array.isArray(p) ? p : []).map(normalizeVisualAsset)), onError });
  },

  getVisualAssetDetail(assetKey, filters, onSuccess, onError) {
    const query = new URLSearchParams();
    if (filters && filters.sourceVersionId) query.set('sourceVersionId', filters.sourceVersionId);
    if (filters && filters.profile) query.set('profile', filters.profile);
    nestRequest({
      url: `${getNestUrl()}/lesson-visual-assets/${encodeURIComponent(assetKey)}${query.toString() ? `?${query}` : ''}`,
      method: 'GET',
      onSuccess: (payload) => {
        const p = payload || {};
        const asset = p.asset || {};
        onSuccess({
          asset: { assetId: asset.id || '', assetKey: asset.asset_key || asset.assetKey || assetKey, category: asset.category || '', title: asset.title || '' },
          sourceVersionId: p.sourceVersionId || p.source_version_id || '',
          versions: (Array.isArray(p.versions) ? p.versions : []).map((row) => normalizeVisualAsset({ ...row, id: asset.id, asset_key: asset.asset_key, category: asset.category, title: asset.title })),
          usages: Array.isArray(p.usages) ? p.usages : [],
        });
      },
      onError,
    });
  },

  visualReplacementImpact(data, onSuccess, onError) {
    nestRequest({ url: `${getNestUrl()}/lesson-visual-assets/replacements/impact`, method: 'POST', data, onSuccess, onError });
  },

  replaceVisualAsset(data, onSuccess, onError) {
    nestRequest({ url: `${getNestUrl()}/lesson-visual-assets/replacements`, method: 'POST', data, onSuccess, onError });
  },
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

  // POST /v1/admin/lessons/:lessonId/new-version -> new DRAFT lesson at v+1
  // "Edit a published lesson": published lessons are immutable, so the edit path is
  // a fresh draft (same lesson_key + course_id, next lesson_version) with the steps
  // + assets deep-copied. Publishing it supersedes the live version. Server 400s if
  // the source is not published or a draft of the next version already exists.
  createNextVersion(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/new-version`,
      method: 'POST',
      onSuccess: (p) => onSuccess(normalizeLesson(p)),
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
    // per-step robot-face override {expression}; server validates against the
    // firmware-supported set and overlays it onto the derived render triple.
    if (input.renderOverride && input.renderOverride.expression) {
      data.renderOverride = { expression: input.renderOverride.expression };
    }
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps`,
      method: 'POST',
      data,
      onSuccess: (p) => onSuccess(normalizeStep(p)),
      onError,
    });
  },

  // PATCH /v1/admin/lessons/:lessonId/steps/:stepKey — draft step authoring fields.
  updateStep(lessonId, stepKey, input, onSuccess, onError) {
    const data = {
      stepType: input.stepType,
      prompt: input.prompt,
      subject: input.subject,
      helperText: input.helperText || undefined,
      l1TransferHint: input.l1TransferHint || undefined,
      choices: input.choices || undefined,
      stepBody: input.stepBody || {},
    };
    if (input.renderOverride && input.renderOverride.expression) data.renderOverride = input.renderOverride;
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps/${encodeURIComponent(stepKey)}`,
      method: 'PATCH',
      data,
      onSuccess: (p) => onSuccess(normalizeStep(p)),
      onError,
    });
  },

  // PUT /v1/admin/lessons/:lessonId/steps/:stepKey/visual-refs/:slot
  setStepVisualRef(lessonId, stepKey, slot, assetVersionId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps/${encodeURIComponent(stepKey)}/visual-refs/${encodeURIComponent(slot)}`,
      method: 'PUT',
      data: { assetVersionId: assetVersionId || null },
      onSuccess,
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
  // fields: optional { profile, layer, role, assetKey, critical }. Also the
  // REPLACE path: re-upload under an existing assetKey upserts in place.
  uploadAsset(lessonId, file, fields, onSuccess, onError) {
    nestUpload(`/lessons/${lessonId}/assets/upload`, file, fields, onSuccess, onError);
  },

  // GET /v1/admin/lessons/:lessonId/assets[?profile] -> { profiles, assets:[...] }
  // Read-only bundle listing across profiles; empty bundle -> { profiles:[], assets:[] }.
  listAssets(lessonId, profile, onSuccess, onError) {
    const q = profile ? `?profile=${encodeURIComponent(profile)}` : '';
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/assets${q}`,
      method: 'GET',
      onSuccess: (p) =>
        onSuccess(p && Array.isArray(p.assets) ? p : { profiles: [], assets: [] }),
      onError,
    });
  },

  // DELETE /v1/admin/lessons/:lessonId/assets/:assetKey?profile= (draft only)
  // Removes the assets row (NOT the content-addressed blob); returns nothing useful.
  deleteAsset(lessonId, assetKey, profile, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/assets/${encodeURIComponent(assetKey)}?profile=${encodeURIComponent(profile || 'espTft')}`,
      method: 'DELETE',
      onSuccess: () => onSuccess && onSuccess(),
      onError,
    });
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
