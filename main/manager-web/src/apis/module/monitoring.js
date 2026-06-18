import { getNestUrl } from '../api';
import { nestRequest } from '../nestHttp';

/**
 * Admin lesson-monitoring (read-only) — backed by the NestJS tbot-backend
 * (/v1/admin/lesson-monitoring/*), reached through the dev-server `/nestjs`
 * proxy. Same conventions as course.js / lesson.js (2xx success, {data}
 * envelope unwrap). The monitoring endpoints wrap their rows one level deeper
 * ({ data: { assignments } } / { data: { events } }), so after settle() unwraps
 * the outer envelope we pull the inner array out here.
 */

export default {
  // GET /v1/admin/lesson-monitoring/assignments?keyword=&deviceId=&childId=&state=&limit=
  //   -> { assignments: AssignmentProgress[] } (newest first)
  listAssignments(params, onSuccess, onError) {
    const qs = [];
    const p = params || {};
    if (p.keyword) qs.push(`keyword=${encodeURIComponent(p.keyword)}`);
    if (p.deviceId) qs.push(`deviceId=${encodeURIComponent(p.deviceId)}`);
    if (p.childId) qs.push(`childId=${encodeURIComponent(p.childId)}`);
    if (p.lessonId) qs.push(`lessonId=${encodeURIComponent(p.lessonId)}`);
    if (p.state) qs.push(`state=${encodeURIComponent(p.state)}`);
    if (p.limit) qs.push(`limit=${encodeURIComponent(p.limit)}`);
    const q = qs.length ? `?${qs.join('&')}` : '';
    nestRequest({
      url: `${getNestUrl()}/lesson-monitoring/assignments${q}`,
      method: 'GET',
      onSuccess: (payload) =>
        onSuccess(payload && Array.isArray(payload.assignments) ? payload.assignments : []),
      onError,
    });
  },

  // GET /v1/admin/lesson-monitoring/assignments/:assignmentId/events
  //   -> { events: ProgressEventView[] }
  getAssignmentEvents(assignmentId, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/lesson-monitoring/assignments/${assignmentId}/events`,
      method: 'GET',
      onSuccess: (payload) =>
        onSuccess(payload && Array.isArray(payload.events) ? payload.events : []),
      onError,
    });
  },
};
