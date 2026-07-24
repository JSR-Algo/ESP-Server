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
const SD_SYNC_STATES = new Set(['complete', 'syncing', 'offline_pending', 'failed']);

function normalizeSdSyncState(value) {
  if (typeof value !== 'string') return '';
  const normalized = value.trim().replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`).replace(/[\s-]+/g, '_').toLowerCase();
  if (normalized === 'in_progress' || normalized === 'pending' || normalized === 'syncing') return 'syncing';
  if (normalized === 'offline' || normalized === 'offline_pending') return 'offline_pending';
  if (normalized === 'complete' || normalized === 'failed') return normalized;
  return '';
}

function validSafeCount(value) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function validNullableTimestamp(value) {
  return value === null || value === undefined || (typeof value === 'string' && value.trim() && !Number.isNaN(Date.parse(value)));
}

function normalizeNullableTimestamp(value) {
  return value === undefined ? null : value;
}

function validNullableChecksum(value) {
  return value === null || value === undefined || value === '' || (typeof value === 'string' && /^[a-f0-9]{64}$/i.test(value));
}

function normalizeSdSyncDevice(raw) {
  if (!raw || Array.isArray(raw) || typeof raw !== 'object') return null;
  const deviceId = raw.deviceId ?? raw.device_id ?? raw.id;
  const state = normalizeSdSyncState(raw.state ?? raw.syncState ?? raw.sync_state);
  const version = raw.version ?? raw.lessonVersion ?? raw.lesson_version ?? null;
  const checksum = raw.checksum ?? raw.manifestChecksum ?? raw.manifest_checksum ?? null;
  const lastSuccessAt = normalizeNullableTimestamp(raw.lastSuccessAt ?? raw.last_success_at);
  const lastErrorAt = normalizeNullableTimestamp(raw.lastErrorAt ?? raw.last_error_at);
  const error = raw.error ?? raw.lastError ?? raw.last_error ?? '';
  if (typeof deviceId !== 'string' || !deviceId.trim() || !SD_SYNC_STATES.has(state)) return null;
  if (version !== null && version !== undefined && !validSafeCount(version)) return null;
  if (!validNullableChecksum(checksum) || !validNullableTimestamp(lastSuccessAt) || !validNullableTimestamp(lastErrorAt)) return null;
  if (error !== null && error !== undefined && typeof error !== 'string') return null;
  return {
    deviceId: deviceId.trim(),
    state,
    version,
    checksum: checksum || '',
    lastSuccessAt,
    lastErrorAt,
    error: error || '',
  };
}

export function normalizeLessonSdSyncStatus(payload) {
  if (!payload || Array.isArray(payload) || typeof payload !== 'object') return null;
  const state = normalizeSdSyncState(payload.state ?? payload.syncState ?? payload.sync_state);
  const total = payload.total;
  const complete = payload.complete;
  const syncing = payload.syncing ?? payload.inProgress ?? payload.in_progress;
  const offlinePending = payload.offlinePending ?? payload.offline_pending;
  const failed = payload.failed;
  const version = payload.version ?? payload.lessonVersion ?? payload.lesson_version ?? null;
  const checksum = payload.checksum ?? payload.manifestChecksum ?? payload.manifest_checksum ?? null;
  const lastSuccessAt = normalizeNullableTimestamp(payload.lastSuccessAt ?? payload.last_success_at);
  const lastErrorAt = normalizeNullableTimestamp(payload.lastErrorAt ?? payload.last_error_at);
  const devices = Array.isArray(payload.devices) ? payload.devices.map(normalizeSdSyncDevice) : null;
  if (!SD_SYNC_STATES.has(state) || !devices || devices.some((device) => !device)) return null;
  if (![total, complete, syncing, offlinePending, failed].every(validSafeCount)) return null;
  if (complete + syncing + offlinePending + failed !== total) return null;
  if (complete > total || syncing > total || offlinePending > total || failed > total) return null;
  if (devices.length !== total) return null;
  const deviceCounts = devices.reduce((counts, device) => {
    const key = device.state === 'offline_pending' ? 'offlinePending' : device.state;
    counts[key] += 1;
    return counts;
  }, { complete: 0, syncing: 0, offlinePending: 0, failed: 0 });
  if (deviceCounts.complete !== complete || deviceCounts.syncing !== syncing
    || deviceCounts.offlinePending !== offlinePending || deviceCounts.failed !== failed) return null;
  if (state === 'complete' && !(total > 0 && complete === total)) return null;
  if (state === 'failed' && failed < 1) return null;
  if (state === 'offline_pending' && offlinePending < 1) return null;
  if (state === 'syncing' && total > 0 && syncing < 1 && offlinePending < 1) return null;
  if (version !== null && version !== undefined && !validSafeCount(version)) return null;
  if (!validNullableChecksum(checksum) || !validNullableTimestamp(lastSuccessAt) || !validNullableTimestamp(lastErrorAt)) return null;
  return {
    state,
    total,
    complete,
    syncing,
    offlinePending,
    failed,
    version,
    checksum: checksum || '',
    lastSuccessAt,
    lastErrorAt,
    devices,
  };
}

export function validateAssetListResponse(payload) {
  if (!payload || Array.isArray(payload) || typeof payload !== 'object'
    || !Array.isArray(payload.profiles) || !Array.isArray(payload.assets)) return null;
  if (payload.profiles.some((profile) => typeof profile !== 'string' || !profile.trim())
    || new Set(payload.profiles).size !== payload.profiles.length) return null;
  if ((!payload.profiles.length && payload.assets.length) || (payload.profiles.length && !payload.assets.length)) return null;
  const identities = new Set();
  for (const asset of payload.assets) {
    if (!asset || Array.isArray(asset) || typeof asset !== 'object') return null;
    if (![asset.profile, asset.assetKey, asset.layer, asset.role, asset.mediaType, asset.sha256, asset.url]
      .every((value) => typeof value === 'string' && value.trim())) return null;
    if (!payload.profiles.includes(asset.profile) || !/^[a-f0-9]{64}$/i.test(asset.sha256)
      || typeof asset.bytes !== 'number' || !Number.isSafeInteger(asset.bytes) || asset.bytes < 0
      || (asset.width !== null && (typeof asset.width !== 'number' || !Number.isFinite(asset.width) || asset.width < 0))
      || (asset.height !== null && (typeof asset.height !== 'number' || !Number.isFinite(asset.height) || asset.height < 0))
      || typeof asset.critical !== 'boolean') return null;
    const identity = `${asset.profile}\u0000${asset.assetKey}`;
    if (identities.has(identity)) return null;
    identities.add(identity);
  }
  const assetProfiles = [...new Set(payload.assets.map((asset) => asset.profile))].sort();
  if (assetProfiles.join('|') !== payload.profiles.slice().sort().join('|')) return null;
  return payload;
}

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
  getRolloutCapabilities(onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lesson-rollout-capabilities`,
      method: 'GET',
      onSuccess,
      onError,
    });
  },

  listVisualAssets(filters, onSuccess, onError) {
    const query = new URLSearchParams();
    if (filters && filters.category) query.set('category', filters.category);
    if (filters && filters.profile) query.set('profile', filters.profile);
    // Force authoring refreshes past intermediary/browser validation caches.
    query.set('_', String(Date.now()));
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
      prompt: input.prompt,
      subject: input.subject,
      helperText: input.helperText || undefined,
      l1TransferHint: input.l1TransferHint || undefined,
      choices: input.choices || undefined,
      stepBody: input.stepBody || {},
      visualRefs: Array.isArray(input.visualRefs) ? input.visualRefs : undefined,
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
      onSuccess: (payload) => {
        const validated = validateAssetListResponse(payload);
        if (validated) {
          if (onSuccess) onSuccess(validated);
          return;
        }
        if (onError) onError('Asset list response violated the backend contract.', {
          status: 200,
          contract: true,
          code: 'INVALID_ASSET_LIST_RESPONSE',
        });
      },
      onError,
    });
  },

  getSdSyncStatus(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/sd-sync`,
      method: 'GET',
      onSuccess: (payload) => {
        const normalized = normalizeLessonSdSyncStatus(payload);
        if (normalized) {
          if (onSuccess) onSuccess(normalized);
          return;
        }
        if (onError) onError('Lesson SD sync status response violated the backend contract.', {
          status: 200,
          contract: true,
          code: 'INVALID_SD_SYNC_STATUS_RESPONSE',
        });
      },
      onError,
    });
  },

  retrySdSync(lessonId, deviceIds, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/sd-sync/retry`,
      method: 'POST',
      data: { deviceIds: Array.isArray(deviceIds) ? deviceIds : undefined },
      onSuccess,
      onError,
    });
  },

  // GET /v1/admin/assets/:assetId/impact -> authoritative shared usage details
  reviewSharedVisualImpact(assetId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/assets/${assetId}/impact`,
      method: 'GET',
      onSuccess,
      onError,
    });
  },

  // POST /v1/admin/lessons/:lessonId/assets/:assetId/clone
  cloneSharedVisual(lessonId, assetId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/assets/${assetId}/clone`,
      method: 'POST',
      data,
      onSuccess,
      onError,
    });
  },

  // POST /v1/admin/lessons/:lessonId/simulate?profile=espTft
  simulate(lessonId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/simulate?profile=espTft`,
      method: 'POST',
      data,
      onSuccess,
      onError,
    });
  },

  listSharedBackgrounds(onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lesson-visual-assets?category=scene&profile=espTft`,
      method: 'GET',
      onSuccess: (rows) => onSuccess(Array.isArray(rows) ? rows : []),
      onError,
    });
  },

  setVisualRef(lessonId, stepKey, slot, assetVersionId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/steps/${encodeURIComponent(stepKey)}/visual-refs/${encodeURIComponent(slot)}`,
      method: 'PUT',
      data: { assetVersionId },
      onSuccess,
      onError,
    });
  },

  generateVariants(lessonId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/variants`,
      method: 'POST',
      data,
      onSuccess,
      onError,
    });
  },

  assessBatchReadiness(lessonIds, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/batch-readiness`,
      method: 'POST',
      data: { lessonIds },
      onSuccess,
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
