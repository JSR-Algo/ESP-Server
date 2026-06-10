import { getNestUrl } from './api';
import RequestService from './httpRequest';

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

export function settle(res, onSuccess, onError) {
  const status = res && res.status;
  if (status >= 200 && status < 300) {
    const body = res.data;
    const payload =
      body && typeof body === 'object' && 'data' in body ? body.data : body;
    if (onSuccess) onSuccess(payload);
  } else {
    const body = (res && res.data) || {};
    const msg =
      body.message ||
      body.msg ||
      (body.error && body.error.message) ||
      `Request failed (${status || 'network'})`;
    if (onError) onError(msg, res);
  }
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
  return token ? { 'X-Nest-Authorization': 'Bearer ' + token } : {};
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
 * Authorization header is sent here. `path` is relative to getNestUrl().
 */
export function nestUpload(path, file, fields, onSuccess, onError) {
  const fd = new FormData();
  fd.append('file', file);
  if (fields) {
    Object.keys(fields).forEach((k) => {
      if (fields[k] != null) fd.append(k, fields[k]);
    });
  }
  fetch(`${getNestUrl()}${path}`, { method: 'POST', body: fd, headers: nestTokenHeader() })
    .then(async (r) => {
      let body = {};
      try {
        body = await r.json();
      } catch (e) {
        body = {};
      }
      if (r.ok) {
        const payload = body && 'data' in body ? body.data : body;
        if (onSuccess) onSuccess(payload);
      } else {
        const msg =
          (body && (body.message || (body.error && body.error.message))) ||
          `Upload failed (${r.status})`;
        if (onError) onError(msg);
      }
    })
    .catch((e) => onError && onError(e.message || 'Upload network error'));
}
