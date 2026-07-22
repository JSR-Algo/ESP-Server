import { getNestUrl } from './api';
import RequestService from './httpRequest';
import {
  isNestAuthDisabled,
  shouldPromptForNestAuth,
  shouldSendNestSessionToken,
} from '../utils/nestAuthModeCore.mjs';

const nestAuthDisabled = isNestAuthDisabled();

/**
 * Shared helpers for talking to the NestJS tbot-backend authoring API
 * (/v1/admin/*) through the dev-server `/nestjs` proxy. Used by the course and
 * lesson api modules. See vue.config.js `/nestjs` proxy (token injection) and
 * apis/api.js getNestUrl().
 *
 * NestJS conventions handled here:
 *  - payloads are wrapped in `{ data: ... }` (unwrapped in settle());
 *  - rows are snake_case with `id` (normalizers map to camelCase);
 *  - success statuses are 200/201/204, but the flyio wrapper only flags 200 as
 *    success (201/204 land in networkFail) — so callers funnel BOTH the success
 *    and networkFail callbacks through settle(), which treats any 2xx as success.
 */

export function normalizeCourse(raw) {
  const r = raw || {};
  return {
    courseId: r.id ?? r.course_id ?? r.courseId ?? '',
    courseKey: r.course_key ?? r.courseKey ?? '',
    title: r.title ?? '',
    locale: r.locale ?? '',
    ageBand: r.age_band ?? r.ageBand ?? '',
    status: r.status ?? 'draft',
    isTemplate: Boolean(r.is_template ?? r.isTemplate ?? false),
    sourceCourseId: r.source_course_id ?? r.sourceCourseId ?? null,
  };
}

export function normalizeLesson(raw) {
  const r = raw || {};
  const topicTags = r.topic_tags ?? r.topicTags ?? [];
  return {
    lessonId: r.id ?? r.lesson_id ?? r.lessonId ?? '',
    lessonKey: r.lesson_key ?? r.lessonKey ?? '',
    title: r.title ?? '',
    status: r.status ?? 'draft',
    lessonVersion: Number(r.lesson_version ?? r.lessonVersion ?? 0),
    courseId: r.course_id ?? r.courseId ?? '',
    locale: r.locale ?? '',
    ageBand: r.age_band ?? r.ageBand ?? '',
    manifestChecksum: r.manifest_checksum ?? r.manifestChecksum ?? '',
    publishedAt: r.published_at ?? r.publishedAt ?? null,
    lessonType: r.lesson_type ?? r.lessonType ?? 'lesson',
    topicTags: Array.isArray(topicTags) ? topicTags : [],
    difficultyBand: r.difficulty_band ?? r.difficultyBand ?? '',
    estimatedDurationSec: r.estimated_duration_sec ?? r.estimatedDurationSec ?? null,
    monitorable: Boolean(r.monitorable ?? true),
  };
}

export function normalizeStep(raw) {
  const r = raw || {};
  return {
    stepKey: r.step_key ?? r.stepKey ?? '',
    stepType: r.step_type ?? r.stepType ?? '',
    prompt: r.prompt ?? '',
    subject: r.subject ?? '',
    helperText: r.helper_text ?? r.helperText ?? '',
    l1TransferHint: r.l1_transfer_hint ?? r.l1TransferHint ?? '',
    choices: Array.isArray(r.choices) ? r.choices : (r.choices ?? null),
    stepBody: r.step_body ?? r.stepBody ?? {},
    visualRefs: Array.isArray(r.visual_refs ?? r.visualRefs) ? (r.visual_refs ?? r.visualRefs) : [],
    robotState: r.robot_state ?? r.robotState ?? '',
    pose: r.pose ?? '',
    expression: r.expression ?? '',
    phase: r.phase ?? '',
    entrance: r.entrance ?? '',
  };
}

export function normalizeStepType(raw) {
  const r = raw || {};
  return {
    stepType: r.stepType ?? r.step_type ?? '',
    completionClass: r.completionClass ?? r.completion_class ?? 'passive',
    isBuiltin: Boolean(r.isBuiltin ?? r.is_builtin ?? false),
  };
}

// Clear the per-user NestJS session token when AdminSessionGuard rejects it
// (expired/invalid/missing). Do NOT bounce to manager-api login — that logs the
// operator out of the console even though manager-api auth is still valid.
// Instead emit a UI event so App.vue can open NestLoginDialog ("Author sign-in").
// NOTE: we clear localStorage directly (not nestAuth.setNestToken) to avoid a
// circular import — nestAuth.js already imports from this module.
export function clearNestSession(status = 401) {
  try {
    localStorage.removeItem(NEST_TOKEN_KEY);
  } catch (e) {
    /* ignore */
  }
  if (
    shouldPromptForNestAuth({ disabled: nestAuthDisabled, status })
    && typeof window !== 'undefined'
    && typeof window.dispatchEvent === 'function'
  ) {
    window.dispatchEvent(new CustomEvent('tbot:nest-auth-required'));
  }
}

// `handle401` (default true) lets data-fetch callers (nestRequest/nestUpload) opt
// into the clear-token + redirect behavior. The auth login/mfa flow passes false:
// a 401 there means "bad credentials", not "expired session", so it must surface
// as a normal form error instead of logging the user out / looping the redirect.
export function settle(res, onSuccess, onError, handle401 = true) {
  const status = res && res.status;
  if (status >= 200 && status < 300) {
    const body = res.data;
    const payload =
      body && typeof body === 'object' && 'data' in body ? body.data : body;
    if (onSuccess) onSuccess(payload);
  } else {
    const body = (res && (res.data || (res.response && res.response.data))) || {};
    const msg =
      body.message ||
      body.msg ||
      (body.error && body.error.message) ||
      `Request failed (${status || 'network'})`;
    if (status === 401 && handle401) {
      clearNestSession();
      if (onError) onError(msg, res);
      return;
    }
    if (onError) onError(msg, res);
  }
}

export function isUncertainNestError(error) {
  const status = Number(error && (error.status ?? (error.response && error.response.status)));
  if (error && error.transport === true) return true;
  return !Number.isFinite(status) || status === 0 || status >= 500;
}

// Per-user NestJS session token (issued by /v1/admin/auth/login). Sent on a
// CUSTOM header because flyio's send() force-overwrites Authorization with the
// manager-api token; the `/nestjs` proxy promotes X-Nest-Authorization →
// Authorization (and falls back to the shared dev token when absent). See
// vue.config.js onProxyReq + apis/module/nestAuth.js.
export const NEST_TOKEN_KEY = 'nestjs_session_token';
function nestTokenHeader() {
  let token = null;
  try {
    token = localStorage.getItem(NEST_TOKEN_KEY);
  } catch (e) {
    token = null;
  }
  return shouldSendNestSessionToken({ disabled: nestAuthDisabled, token })
    ? { 'X-Nest-Authorization': 'Bearer ' + token }
    : {};
}

function managerTokenHeader() {
  try {
    const stored = JSON.parse(localStorage.getItem('token') || 'null');
    return stored && typeof stored.token === 'string' && stored.token.length > 0
      ? { Authorization: 'Bearer ' + stored.token }
      : {};
  } catch (e) {
    return {};
  }
}

export function nestRequest({ url, method = 'GET', data, onSuccess, onError }) {
  RequestService.sendRequest()
    .url(url)
    .method(method)
    .header({
      'content-type': 'application/json; charset=utf-8',
      ...nestTokenHeader(),
    })
    .data(data || {})
    .success((res) => {
      RequestService.clearRequestTime();
      settle(res, onSuccess, onError);
    })
    .networkFail((res) => {
      RequestService.clearRequestTime();
      settle(res, onSuccess, onError);
    })
    .send();
}

/**
 * Multipart upload via raw fetch (NOT flyio): the browser sets
 * `multipart/form-data` with the correct boundary for a FormData body, avoiding
 * the content-type ambiguity that breaks an explicitly-JSON request layer. Auth
 * is handled by the `/nestjs` proxy (it injects the NestJS bearer), so no
 * The manager bearer is sent so nginx can authorize the proxy request before
 * replacing it with the server-only NestJS credential.
 */
export function nestUpload(path, file, fields, onSuccess, onError) {
  const fd = new FormData();
  fd.append('file', file);
  if (fields) {
    Object.keys(fields).forEach((k) => {
      if (fields[k] != null) fd.append(k, fields[k]);
    });
  }
  fetch(`${getNestUrl()}${path}`, {
    method: 'POST',
    body: fd,
    headers: { ...managerTokenHeader(), ...nestTokenHeader() },
  })
    .then(async (r) => {
      let body = {};
      try {
        body = await r.json();
      } catch (e) {
        body = {};
      }
      return { response: r, body };
    })
    .catch((e) => {
      if (onError) onError(e.message || 'Upload network error', {
        status: 0,
        error: e,
        transport: true,
      });
      return null;
    })
    .then((result) => {
      if (!result) return;
      const { response: r, body } = result;
      if (r.ok) {
        const payload = body && 'data' in body ? body.data : body;
        if (onSuccess) onSuccess(payload);
      } else if (r.status === 401) {
        // Expired/invalid nestjs_session_token rejected by the proxy guard:
        // clear it and bounce to login, matching settle()'s data-fetch behavior.
        clearNestSession();
        const msg = (body && (body.message || (body.error && body.error.message))) || 'Admin session expired';
        if (onError) onError(msg, { status: 401, response: r, transport: false });
      } else {
        const msg =
          (body && (body.message || (body.error && body.error.message))) ||
          `Upload failed (${r.status})`;
        if (onError) onError(msg, { status: r.status, response: r, transport: false });
      }
    });
}
