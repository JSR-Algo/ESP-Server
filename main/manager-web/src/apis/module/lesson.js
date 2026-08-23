import { getNestUrl } from '../api';
import {
  nestRequest,
  nestUpload,
  normalizeLesson,
  normalizeStep,
  normalizeStepType,
} from '../nestHttp';
import { normalizeFlattenedDerivativeStatusResponse } from '@/components/lesson/flattened-derivative-status';

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
const BUILD_STATES = new Set(['idle', 'pending', 'building', 'failed']);
const MATERIALIZATION_STATES = new Set(['empty', 'polling', 'materializing', 'retry_wait', 'ready']);
const CHECKSUM_PATTERN = /^[a-f0-9]{64}$/;
const ERROR_CODE_PATTERN = /^[a-z0-9][a-z0-9_]{0,63}$/;
const TIMESTAMP_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?Z$/;

export function normalizeAuthoringLesson(raw) {
  const normalized = normalizeLesson(raw);
  const value = raw || {};
  return {
    ...normalized,
    manifestVersion: value.manifest_version ?? value.manifestVersion ?? '',
    courseModeContract: value.course_mode_contract ?? value.courseModeContract ?? null,
  };
}

export function normalizeTVideoJourneyResponse(raw) {
  const value = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  return {
    ...value,
    state: value.state === 'configured' ? 'configured' : 'not-configured',
    lessonId: value.lessonId ?? value.lesson_id ?? '',
    lessonVersion: Number(value.lessonVersion ?? value.lesson_version ?? 0),
    sourceRevision: Number(value.sourceRevision ?? value.source_revision ?? value.cinematicSourceRevision ?? value.cinematic_source_revision ?? 0),
    cinematicSourceRevision: Number(value.cinematicSourceRevision ?? value.cinematic_source_revision ?? 0),
    journey: value.journey || null,
    set: value.set && typeof value.set === 'object' ? value.set : { state: 'invalid', issues: [] },
    statuses: Array.isArray(value.statuses) ? value.statuses : [],
    publishReady: value.publishReady === true || value.publish_ready === true,
  };
}

function validSafeCount(value) {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

function validPositiveSafeInteger(value) {
  return validSafeCount(value) && value > 0;
}

function validNullableTimestamp(value) {
  if (value === null) return true;
  if (typeof value !== 'string') return false;
  const match = TIMESTAMP_PATTERN.exec(value);
  const parsed = Date.parse(value);
  if (!match || Number.isNaN(parsed)) return false;
  const date = new Date(parsed);
  return Number(match[1]) > 0
    && date.getUTCFullYear() === Number(match[1])
    && date.getUTCMonth() + 1 === Number(match[2])
    && date.getUTCDate() === Number(match[3])
    && date.getUTCHours() === Number(match[4])
    && date.getUTCMinutes() === Number(match[5])
    && date.getUTCSeconds() === Number(match[6]);
}

function validNullableErrorCode(value) {
  return value === null || (typeof value === 'string' && ERROR_CODE_PATTERN.test(value));
}

function validNullableChecksum(value) {
  return value === null || (typeof value === 'string' && CHECKSUM_PATTERN.test(value));
}

const DEVICE_AVAILABILITY = new Set(['available', 'already_assigned', 'busy']);
const ACTIVE_ASSIGNMENT_STATES = new Set(['ASSIGNED', 'PRELOADING', 'READY', 'RUNNING', 'PAUSED']);

function normalizeEligibleDevice(raw) {
  const r = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : null;
  if (!r || typeof r.deviceId !== 'string' || !r.deviceId
    || !DEVICE_AVAILABILITY.has(r.availability)) {
    throw new Error('INVALID_ELIGIBLE_DEVICES_RESPONSE');
  }
  const current = r.currentAssignment;
  let currentAssignment = null;
  if (current !== null && current !== undefined) {
    if (!current || typeof current !== 'object' || Array.isArray(current)
      || typeof current.assignmentId !== 'string' || !current.assignmentId
      || typeof current.childId !== 'string' || !current.childId
      || typeof current.lessonId !== 'string' || !current.lessonId
      || !validPositiveSafeInteger(Number(current.lessonVersion))
      || !ACTIVE_ASSIGNMENT_STATES.has(current.state)
      || !validPositiveSafeInteger(Number(current.assignmentVersion))) {
      throw new Error('INVALID_ELIGIBLE_DEVICES_RESPONSE');
    }
    currentAssignment = {
      assignmentId: current.assignmentId,
      childId: current.childId,
      lessonId: current.lessonId,
      lessonTitle: typeof current.lessonTitle === 'string' ? current.lessonTitle : '',
      lessonVersion: Number(current.lessonVersion),
      state: current.state,
      assignmentVersion: Number(current.assignmentVersion),
      createdAt: current.createdAt || null,
    };
  }
  if (r.availability === 'available' && currentAssignment) {
    throw new Error('INVALID_ELIGIBLE_DEVICES_RESPONSE');
  }
  if (r.availability !== 'available' && !currentAssignment) {
    throw new Error('INVALID_ELIGIBLE_DEVICES_RESPONSE');
  }
  return {
    deviceId: r.deviceId,
    serialNumber: r.serialNumber || '',
    displayName: r.displayName || r.serialNumber || r.deviceId,
    firmwareVersion: r.firmwareVersion || '',
    availability: r.availability,
    currentAssignment,
  };
}

export function normalizeEligibleDevicesResponse(raw) {
  const payload = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  if (!Array.isArray(payload.devices)) {
    throw new Error('INVALID_ELIGIBLE_DEVICES_RESPONSE');
  }
  return payload.devices.map(normalizeEligibleDevice);
}

export function normalizeAssignmentResponse(raw) {
  const payload = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
  const assignment = payload.assignment;
  if (!assignment || typeof assignment !== 'object' || Array.isArray(assignment)
    || typeof assignment.assignmentId !== 'string' || !assignment.assignmentId
    || !validPositiveSafeInteger(Number(assignment.assignmentVersion))
    || typeof assignment.deviceId !== 'string' || !assignment.deviceId
    || typeof assignment.childId !== 'string' || !assignment.childId
    || typeof assignment.lessonId !== 'string' || !assignment.lessonId
    || !validPositiveSafeInteger(Number(assignment.lessonVersion))
    || assignment.profile !== 'espTft'
    || !ACTIVE_ASSIGNMENT_STATES.has(assignment.state)
    || !validNullableTimestamp(assignment.createdAt ?? null)
    || typeof payload.created !== 'boolean') {
    throw new Error('INVALID_ASSIGNMENT_RESPONSE');
  }
  return {
    assignment: {
      assignmentId: assignment.assignmentId,
      assignmentVersion: Number(assignment.assignmentVersion || 0),
      deviceId: assignment.deviceId,
      childId: assignment.childId,
      lessonId: assignment.lessonId,
      lessonTitle: assignment.lessonTitle || '',
      lessonVersion: Number(assignment.lessonVersion),
      profile: assignment.profile,
      state: assignment.state || '',
      createdAt: assignment.createdAt || null,
    },
    created: payload.created,
  };
}

export function normalizeLessonAssetGenerationStatus(buildPayload, cmsPayload, espPayload) {
  const build = buildPayload && buildPayload.data ? buildPayload.data : buildPayload;
  const cms = cmsPayload && cmsPayload.data ? cmsPayload.data : cmsPayload;
  const esp = espPayload;
  if (![build, cms, esp].every((value) => value && !Array.isArray(value) && typeof value === 'object')) return null;

  const buildState = build.state;
  const pendingCount = build.pendingCount;
  const lastBuildAt = build.updatedAt;
  const buildErrorCode = build.lastErrorCode;
  if (!BUILD_STATES.has(buildState) || !validSafeCount(pendingCount)
    || !validNullableTimestamp(lastBuildAt) || !validNullableErrorCode(buildErrorCode)) return null;

  const generation = cms.generation;
  const curriculumLessonCount = cms.curriculumLessonCount;
  const packCount = cms.packCount;
  const indexChecksum = cms.indexChecksum;
  if (!validPositiveSafeInteger(generation) || !validSafeCount(curriculumLessonCount)
    || !validSafeCount(packCount) || typeof indexChecksum !== 'string'
    || !CHECKSUM_PATTERN.test(indexChecksum) || !validNullableTimestamp(cms.publishedAt)) return null;
  if (cms.publishedAt === null) return null;
  if (cms.index !== undefined) {
    if (!Array.isArray(cms.index) || cms.index.length !== packCount) return null;
    const curriculumFromIndex = cms.index.reduce((count, pack) => (
      pack && !Array.isArray(pack) && typeof pack === 'object' && pack.classification === 'curriculum'
        ? count + 1
        : count
    ), 0);
    if (cms.index.some((pack) => !pack || Array.isArray(pack) || typeof pack !== 'object'
      || !['curriculum', 'demo'].includes(pack.classification))
      || curriculumFromIndex !== curriculumLessonCount) return null;
  }

  const acceptedGeneration = esp.acceptedGeneration;
  const acceptedChecksum = esp.indexChecksum;
  const materializationState = esp.materializationState;
  const connections = esp.connections;
  const lastPollAt = esp.lastPollAt;
  const lastMaterializedAt = esp.lastMaterializedAt;
  const espErrorCode = esp.lastErrorCode;
  if (!(acceptedGeneration === null || validPositiveSafeInteger(acceptedGeneration))
    || !validNullableChecksum(acceptedChecksum) || !MATERIALIZATION_STATES.has(materializationState)
    || !connections || Array.isArray(connections) || typeof connections !== 'object'
    || !validNullableTimestamp(lastPollAt) || !validNullableTimestamp(lastMaterializedAt)
    || !validNullableErrorCode(espErrorCode)) return null;
  const { connected, current, retrying, failed } = connections;
  if (![connected, current, retrying, failed].every(validSafeCount)
    || current + retrying + failed !== connected) return null;

  return {
    generation,
    curriculumLessonCount,
    packCount,
    buildState,
    pendingCount,
    materializationState,
    connected,
    current,
    retrying,
    failed,
    allConnectedCurrent: generation === acceptedGeneration
      && indexChecksum === acceptedChecksum
      && materializationState === 'ready'
      && connected > 0
      && current === connected
      && retrying === 0
      && failed === 0
      && buildState === 'idle',
    lastBuildAt,
    lastPollAt,
    lastMaterializedAt,
    lastErrorCode: buildErrorCode || espErrorCode || null,
  };
}

function generationStatusError(code, status) {
  return {
    code,
    ...(Number.isSafeInteger(status) ? { status } : {}),
  };
}

function fetchPublicGenerationJson(url) {
  let request;
  try {
    request = fetch(url, {
      method: 'GET',
      credentials: 'omit',
      headers: { Accept: 'application/json' },
    });
  } catch (error) {
    return Promise.reject(generationStatusError('GENERATION_STATUS_NETWORK_ERROR'));
  }
  return Promise.resolve(request)
    .then((response) => {
      if (!response || response.status !== 200) {
        throw generationStatusError('GENERATION_STATUS_HTTP_ERROR', response && response.status);
      }
      return Promise.resolve(response.json()).catch(() => {
        throw generationStatusError('GENERATION_STATUS_INVALID_JSON');
      });
    });
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

export function normalizeVisualAsset(raw) {
  const r = raw || {};
  const compatibilityMetadata = r.compatibility_metadata ?? r.compatibilityMetadata ?? null;
  const metadata = compatibilityMetadata && typeof compatibilityMetadata === 'object'
    ? compatibilityMetadata
    : {};
  const versionId = r.version_id ?? r.versionId ?? '';
  return {
    assetId: r.id ?? r.assetId ?? '', assetKey: r.asset_key ?? r.assetKey ?? '', category: r.category ?? '', title: r.title ?? '',
    versionId, versionIdentity: versionId, version: Number(r.version ?? 0), profile: r.profile ?? '', storagePath: r.storage_path ?? r.storagePath ?? '',
    url: r.url ?? '', sha256: r.sha256 ?? '', mimeType: r.mime_type ?? r.mimeType ?? '', bytes: Number(r.bytes ?? 0), width: Number(r.width ?? 0), height: Number(r.height ?? 0),
    publicationState: r.publication_state ?? r.publicationState ?? 'draft', usageCount: Number(r.usage_count ?? r.usageCount ?? 0),
    compatibilityMetadata,
    codec: metadata.codec ?? '', fps: Number(metadata.fps ?? 0), durationMs: Number(metadata.durationMs ?? metadata.duration_ms ?? 0),
    frameCount: Number(metadata.frameCount ?? metadata.frame_count ?? 0), rect: metadata.rect ?? null, chromaKey: metadata.chromaKey ?? metadata.chroma_key ?? null,
  };
}

export default {
  getTVideoJourneyPreset(onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/authoring/presets/tvideoJourney/1`,
      method: 'GET',
      onSuccess,
      onError,
    });
  },

  getTVideoJourney(lessonId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/authoring/${lessonId}/tvideo-journey`,
      method: 'GET',
      onSuccess: (payload) => onSuccess(normalizeTVideoJourneyResponse(payload)),
      onError,
    });
  },

  saveTVideoJourney(lessonId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/authoring/${lessonId}/tvideo-journey`,
      method: 'PUT',
      data,
      onSuccess: (payload) => onSuccess(normalizeTVideoJourneyResponse(payload)),
      onError,
    });
  },

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

  // GET /v1/admin/courses/:courseId/lessons/authoritative -> one live row per lesson key
  listAuthoritativeLessons(courseId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/courses/${courseId}/lessons/authoritative`,
      method: 'GET',
      onSuccess: (p) =>
        onSuccess((Array.isArray(p) ? p : []).map(normalizeLesson)),
      onError,
    });
  },

  eligibleDevices({ childId, lessonId, lessonVersion }, onSuccess, onError) {
    const qs = [
      `childId=${encodeURIComponent(childId)}`,
      `lessonId=${encodeURIComponent(lessonId)}`,
      `lessonVersion=${encodeURIComponent(String(lessonVersion))}`,
    ].join('&');
    nestRequest({
      url: `${getNestUrl()}/lesson-assignments/eligible-devices?${qs}`,
      method: 'GET',
      onSuccess: (payload) => onSuccess(normalizeEligibleDevicesResponse(payload)),
      onError,
    });
  },

  createAssignment(data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lesson-assignments`,
      method: 'POST',
      data,
      onSuccess: (payload) => onSuccess(normalizeAssignmentResponse(payload)),
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
      onSuccess: (p) => onSuccess(normalizeAuthoringLesson(p)),
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

  applyLessonVisuals(lessonId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/visuals`,
      method: 'PUT',
      data,
      onSuccess,
      onError,
    });
  },

  retryLessonAssetGeneration(onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lesson-assets/retry`,
      method: 'POST',
      data: {},
      onSuccess,
      onError,
    });
  },

  // POST /v1/admin/lessons/:lessonId/new-version -> new DRAFT lesson at v+1
  // "Edit a published lesson": published lessons are immutable, so the edit path is
  // a fresh draft (same lesson_key + course_id, next lesson_version) with the steps
  // + assets deep-copied. Publishing it supersedes the live version. Server 400s if
  // the source is not published or a draft of the next version already exists.
  createNextVersion(lessonId, dataOrSuccess, onSuccessOrError, onError) {
    const hasRequestBody = dataOrSuccess && typeof dataOrSuccess === 'object' && !Array.isArray(dataOrSuccess);
    const onSuccess = hasRequestBody ? onSuccessOrError : dataOrSuccess;
    const errorHandler = hasRequestBody ? onError : onSuccessOrError;
    const request = {
      url: `${getNestUrl()}/lessons/${lessonId}/new-version`,
      method: 'POST',
      onSuccess: (payload) => {
        const raw = payload && !Array.isArray(payload) && typeof payload === 'object' ? payload : {};
        const rawLessonId = raw.id ?? raw.lesson_id ?? raw.lessonId;
        const rawLessonVersion = raw.lesson_version ?? raw.lessonVersion;
        if (typeof rawLessonId !== 'string' || !rawLessonId.trim()
          || raw.status !== 'draft'
          || !Number.isSafeInteger(rawLessonVersion) || rawLessonVersion < 1) {
          if (errorHandler) errorHandler('Next-version response violated the backend contract.', {
            status: 200,
            contract: true,
            code: 'INVALID_NEXT_VERSION_RESPONSE',
          });
          return;
        }
        if (onSuccess) onSuccess(normalizeAuthoringLesson(raw));
      },
      onError: errorHandler,
    };
    if (hasRequestBody) request.data = dataOrSuccess;
    nestRequest(request);
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

  getLessonAssetGenerationStatus(onSuccess, onError) {
    let settled = false;
    const buildRequest = new Promise((resolve, reject) => {
      nestRequest({
        url: `${getNestUrl()}/lesson-assets/generation-status`,
        method: 'GET',
        onSuccess: resolve,
        onError: () => reject(generationStatusError('GENERATION_STATUS_ADMIN_ERROR')),
      });
    });
    const cmsRequest = fetchPublicGenerationJson('/v1/public/lesson-assets/latest');
    const espRequest = fetchPublicGenerationJson('/public/lesson-assets/generation');

    Promise.all([buildRequest, cmsRequest, espRequest])
      .then(([buildPayload, cmsPayload, espPayload]) => {
        if (settled) return;
        const normalized = normalizeLessonAssetGenerationStatus(buildPayload, cmsPayload, espPayload);
        if (!normalized) throw generationStatusError('INVALID_LESSON_ASSET_GENERATION_STATUS', 200);
        settled = true;
        if (onSuccess) onSuccess(normalized);
      })
      .catch((error) => {
        if (settled) return;
        settled = true;
        const metadata = error && typeof error === 'object' && typeof error.code === 'string'
          ? generationStatusError(error.code, error.status)
          : generationStatusError('GENERATION_STATUS_UNAVAILABLE');
        if (onError) onError('Lesson generation rollout status is unavailable.', metadata);
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

  // GET /v1/admin/lessons/:lessonId/flattened-cinematic-derivatives
  // Returns public output URLs only; malformed or unsafe responses fail closed.
  getFlattenedDerivativeStatus(lessonId, lessonVersion, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lessons/${lessonId}/flattened-cinematic-derivatives`,
      method: 'GET',
      onSuccess: (payload) => {
        const normalized = normalizeFlattenedDerivativeStatusResponse(payload, { lessonId, lessonVersion });
        if (normalized) {
          if (onSuccess) onSuccess(normalized);
          return;
        }
        if (onError) onError('Malformed flattened cinematic derivative status.', {
          code: 'FLATTENED_DERIVATIVE_STATUS_MALFORMED', transport: false,
        });
      },
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
