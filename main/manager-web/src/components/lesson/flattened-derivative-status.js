export const FLATTENED_DERIVATIVE_STATES = Object.freeze([
  'not-requested', 'processing', 'ready', 'failed', 'stale',
]);

const STATE_SET = new Set(FLATTENED_DERIVATIVE_STATES);
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const PHASE_ID_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;
const ERROR_CODE_PATTERN = /^[a-z0-9][a-z0-9_]{0,63}$/;
const OUTPUT_KEYS = ['bytes', 'height', 'mediaType', 'metadata', 'sha256', 'url', 'width'];
const METADATA_KEYS = ['codec', 'durationMs', 'fps', 'frameCount', 'hasAudio', 'height', 'width'];

function exactKeys(value, keys) {
  return value && !Array.isArray(value) && typeof value === 'object'
    && JSON.stringify(Object.keys(value).sort()) === JSON.stringify(keys.slice().sort());
}

function positiveSafeInteger(value) {
  return Number.isSafeInteger(value) && value > 0;
}

function safePublicUrl(value) {
  if (typeof value !== 'string' || !value || value.includes('\\')) return false;
  const publicPath = value.split(/[?#]/, 1)[0];
  if (value.startsWith('//')) return false;
  if (value.startsWith('/') || !/^[a-z][a-z0-9+.-]*:/i.test(value)) {
    return !publicPath.split('/').includes('..') && !publicPath.startsWith('.');
  }
  try {
    const parsed = new URL(value);
    return (parsed.protocol === 'https:' || parsed.protocol === 'http:') && Boolean(parsed.hostname);
  } catch (error) {
    return false;
  }
}

function normalizeOutput(value) {
  if (!exactKeys(value, OUTPUT_KEYS) || !safePublicUrl(value.url)
    || typeof value.sha256 !== 'string' || !SHA256_PATTERN.test(value.sha256)
    || !positiveSafeInteger(value.bytes) || value.mediaType !== 'video/mp4'
    || value.width !== 480 || value.height !== 320 || !exactKeys(value.metadata, METADATA_KEYS)) return null;
  const metadata = value.metadata;
  if (metadata.codec !== 'mjpeg' || metadata.width !== 480 || metadata.height !== 320
    || !positiveSafeInteger(metadata.fps) || !positiveSafeInteger(metadata.durationMs)
    || !positiveSafeInteger(metadata.frameCount) || metadata.hasAudio !== false) return null;
  return {
    url: value.url, sha256: value.sha256, bytes: value.bytes, mediaType: value.mediaType,
    width: value.width, height: value.height, metadata: { ...metadata },
  };
}

function normalizePhase(value) {
  if (!value || Array.isArray(value) || typeof value !== 'object') return null;
  const expectedKeys = value.state === 'ready'
    ? ['derivativeId', 'errorCode', 'output', 'phaseId', 'sourceRevision', 'state']
    : ['derivativeId', 'errorCode', 'phaseId', 'sourceRevision', 'state'];
  if (!exactKeys(value, expectedKeys) || typeof value.phaseId !== 'string' || !PHASE_ID_PATTERN.test(value.phaseId)
    || typeof value.derivativeId !== 'string' || !SHA256_PATTERN.test(value.derivativeId)
    || !positiveSafeInteger(value.sourceRevision) || !STATE_SET.has(value.state)
    || !(value.errorCode === null || (typeof value.errorCode === 'string' && ERROR_CODE_PATTERN.test(value.errorCode)))) return null;
  if (value.state === 'failed' && value.errorCode === null) return null;
  if (value.state !== 'failed' && value.errorCode !== null) return null;
  const output = value.state === 'ready' ? normalizeOutput(value.output) : null;
  if (value.state === 'ready' && !output) return null;
  return {
    phaseId: value.phaseId,
    derivativeId: value.derivativeId,
    sourceRevision: value.sourceRevision,
    state: value.state,
    errorCode: value.errorCode,
    ...(output ? { output } : {}),
  };
}

export function normalizeFlattenedDerivativeStatus(payload, lesson) {
  if (!Array.isArray(payload) || !lesson || typeof lesson.lessonId !== 'string' || !lesson.lessonId
    || !positiveSafeInteger(lesson.lessonVersion)) return null;
  const phases = [];
  const phaseIds = new Set();
  let sourceRevision = null;
  for (const value of payload) {
    const phase = normalizePhase(value);
    if (!phase || phaseIds.has(phase.phaseId)) return null;
    if (sourceRevision !== null && sourceRevision !== phase.sourceRevision) return null;
    phaseIds.add(phase.phaseId);
    sourceRevision = phase.sourceRevision;
    phases.push(phase);
  }
  return { lessonId: lesson.lessonId, lessonVersion: lesson.lessonVersion, sourceRevision, phases };
}

export function anyFlattenedDerivativeProcessing(status) {
  return Boolean(status && Array.isArray(status.phases)
    && status.phases.some((phase) => phase.state === 'processing'));
}

export function flattenedDerivativesReadyForPublish(manifestVersion, status) {
  if (manifestVersion !== 'teebot-lesson-renderer.v4') return true;
  return Boolean(status && status.phases.length
    && status.phases.every((phase) => phase.state === 'ready' && phase.output));
}

export function flattenedDerivativeResponseIsCurrent(expected, actual) {
  return Boolean(expected && actual
    && expected.requestId === actual.requestId
    && expected.lessonId === actual.lessonId
    && expected.sourceEpoch === actual.sourceEpoch);
}
