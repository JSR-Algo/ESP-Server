import { getNestUrl } from '../api';
import { nestRequest } from '../nestHttp';

function normalizeLearner(raw) {
  const r = raw || {};
  const p = r.personality || {};
  const s = r.stats || {};
  return {
    childId: r.childId || '',
    householdId: r.householdId || '',
    childName: r.childName || '',
    birthYear: r.birthYear || null,
    age: r.age || null,
    parentId: r.parentId || '',
    parentEmail: r.parentEmail || '',
    personality: {
      interests: Array.isArray(p.interests) ? p.interests : [],
      learningStyle: p.learningStyle || '',
      vocabularyLevel: p.vocabularyLevel || '',
      attentionSpanSec: p.attentionSpanSec || null,
      confidenceScore: p.confidenceScore || null,
      parentCareer: p.parentCareer || '',
    },
    stats: {
      enrolledCourses: Number(s.enrolledCourses || 0),
      assignments: Number(s.assignments || 0),
      completedAssignments: Number(s.completedAssignments || 0),
      completionRate: Number(s.completionRate || 0),
      lastActivityAt: s.lastActivityAt || null,
    },
  };
}

function normalizePreviewLesson(raw) {
  const r = raw || {};
  return {
    lessonId: r.lessonId || '',
    lessonKey: r.lessonKey || '',
    lessonVersion: Number(r.lessonVersion || 0),
    title: r.title || '',
    courseId: r.courseId || '',
    courseKey: r.courseKey || '',
    courseTitle: r.courseTitle || '',
    topicTags: Array.isArray(r.topicTags) ? r.topicTags : [],
    difficultyBand: r.difficultyBand || '',
    estimatedDurationSec: r.estimatedDurationSec || null,
    rank: Number(r.rank || 0),
    suitabilityScore: Number(r.suitabilityScore || 0),
    reasonCode: r.reasonCode || 'neutral_order',
    matchedTopics: Array.isArray(r.matchedTopics) ? r.matchedTopics : [],
    difficultyMatch: Boolean(r.difficultyMatch),
    durationFit: Boolean(r.durationFit),
  };
}

function normalizeQuality(raw) {
  const r = raw || {};
  return {
    courseId: r.courseId || '',
    courseKey: r.courseKey || '',
    title: r.title || '',
    status: r.status || '',
    lessonCount: Number(r.lessonCount || 0),
    personalizedLessonCount: Number(r.personalizedLessonCount || 0),
    assignments: Number(r.assignments || 0),
    completed: Number(r.completed || 0),
    failed: Number(r.failed || 0),
    running: Number(r.running || 0),
    completionRate: Number(r.completionRate || 0),
    avgSuccessRate: r.avgSuccessRate == null ? null : Number(r.avgSuccessRate),
    avgDurationSec: r.avgDurationSec == null ? null : Number(r.avgDurationSec),
    activeChildren: Number(r.activeChildren || 0),
    qualityScore: Number(r.qualityScore || 0),
    lastActivityAt: r.lastActivityAt || null,
  };
}

export default {
  listLearners(params, onSuccess, onError) {
    const p = params || {};
    const qs = [];
    if (p.keyword) qs.push(`keyword=${encodeURIComponent(p.keyword)}`);
    if (p.limit) qs.push(`limit=${encodeURIComponent(p.limit)}`);
    const q = qs.length ? `?${qs.join('&')}` : '';
    nestRequest({
      url: `${getNestUrl()}/course-insights/learners${q}`,
      method: 'GET',
      onSuccess: (payload) => onSuccess((payload && Array.isArray(payload.learners) ? payload.learners : []).map(normalizeLearner)),
      onError,
    });
  },

  updateLearnerPersonality(childId, data, onSuccess, onError) {
    nestRequest({
      url: `${getNestUrl()}/course-insights/learners/${childId}/personality`,
      method: 'PATCH',
      data,
      onSuccess: (payload) => onSuccess(normalizeLearner(payload && payload.learner)),
      onError,
    });
  },

  previewLearnerLessons(childId, params, onSuccess, onError) {
    const p = params || {};
    const qs = [];
    if (p.courseId) qs.push(`courseId=${encodeURIComponent(p.courseId)}`);
    if (p.limit) qs.push(`limit=${encodeURIComponent(p.limit)}`);
    const q = qs.length ? `?${qs.join('&')}` : '';
    nestRequest({
      url: `${getNestUrl()}/course-insights/learners/${childId}/lesson-preview${q}`,
      method: 'GET',
      onSuccess: (payload) => onSuccess({
        learner: normalizeLearner(payload && payload.learner),
        lessons: (payload && Array.isArray(payload.lessons) ? payload.lessons : []).map(normalizePreviewLesson),
      }),
      onError,
    });
  },

  getCourseQuality(params, onSuccess, onError) {
    const p = params || {};
    const qs = [];
    if (p.windowDays) qs.push(`windowDays=${encodeURIComponent(p.windowDays)}`);
    if (p.courseId) qs.push(`courseId=${encodeURIComponent(p.courseId)}`);
    const q = qs.length ? `?${qs.join('&')}` : '';
    nestRequest({
      url: `${getNestUrl()}/course-insights/course-quality${q}`,
      method: 'GET',
      onSuccess: (payload) => onSuccess((payload && Array.isArray(payload.courses) ? payload.courses : []).map(normalizeQuality)),
      onError,
    });
  },
};
