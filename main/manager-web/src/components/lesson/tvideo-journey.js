export const CONVERSATION_BRANCHES = Object.freeze([
  'target', 'meaning_vi', 'related', 'silence', 'uncertain',
  'retry_level_1', 'retry_level_2', 'retry_level_3',
]);

export const ROBOT_CLIP_ROLES = Object.freeze([
  'flight', 'walking', 'greeting-teaching', 'celebration',
]);

export const CUE_STATES = Object.freeze([
  'not-requested', 'queued', 'processing', 'retryable', 'ready', 'failed', 'stale',
]);

const STEP_EFFECTS = Object.freeze([
  'teach', 'listen', 'thinking', 'correct',
  'retry-level-1', 'retry-level-2', 'retry-level-3', 'celebrate',
]);

const finite = (value, fallback = 0) => (Number.isFinite(Number(value)) ? Number(value) : fallback);
export const clamp01 = (value) => Math.min(1, Math.max(0, finite(value)));

export function clampPoint(value = {}) {
  return { x: clamp01(value.x), y: clamp01(value.y) };
}

export function clampPose(value = {}) {
  return {
    ...clampPoint(value),
    scale: Math.min(2, Math.max(0.01, finite(value.scale, 1))),
  };
}

export function clampSafeZone(value = {}) {
  const point = clampPoint(value);
  const bounded = (size, origin) => Number(Math.min(1 - origin, Math.max(0.01, finite(size, 0.01))).toFixed(6));
  return {
    ...point,
    width: bounded(value.width, point.x),
    height: bounded(value.height, point.y),
  };
}

export function clampWalkKeyframes(values = []) {
  return (Array.isArray(values) ? values : [])
    .map((value) => ({
      timeMs: Math.max(0, Math.round(finite(value && value.timeMs))),
      ...clampPose(value),
    }))
    .sort((a, b) => a.timeMs - b.timeMs);
}

export function normalizeScenePath(scenePath = {}) {
  const flight = scenePath.flightIngress || {};
  const walk = scenePath.walk || {};
  return {
    flightIngress: {
      durationMs: 3200,
      start: clampPose(flight.start),
      mid: clampPose(flight.mid),
      end: clampPose(flight.end),
    },
    landing: { ...clampPose(scenePath.landing), durationMs: 500 },
    walk: { durationMs: 5000, keyframes: clampWalkKeyframes(walk.keyframes) },
    teachingAnchor: clampPoint(scenePath.teachingAnchor),
    objectAnchor: clampPoint(scenePath.objectAnchor),
    safeZone: clampSafeZone(scenePath.safeZone),
  };
}

export function requiredCueIds(steps = []) {
  if (!Array.isArray(steps) || !steps.length) return [];
  const ids = [`${steps[0].stepKey}-opening`, `${steps[0].stepKey}-greet`];
  steps.forEach((step, index) => {
    STEP_EFFECTS.forEach((effect) => ids.push(`${step.stepKey}-${effect}`));
    if (steps[index + 1]) ids.push(`${step.stepKey}-to-${steps[index + 1].stepKey}-word-transition`);
  });
  return ids;
}

export function orderCueStatuses(statuses = [], steps = []) {
  const required = requiredCueIds(steps);
  const rows = Array.isArray(statuses) ? statuses : [];
  const byId = new Map(rows.map((row) => [row && row.cueId, row]));
  return required.map((cueId) => byId.get(cueId) || {
    cueId, state: 'not-requested', errorCode: null, attempt: null, output: null,
  });
}

export function exactCueReadiness(statuses = [], requiredIds = [], sourceRevision) {
  const required = new Set(requiredIds);
  const counts = new Map();
  const missing = [];
  const extra = [];
  const duplicate = [];
  const stale = [];
  const notReady = [];
  (Array.isArray(statuses) ? statuses : []).forEach((row) => {
    const cueId = row && row.cueId;
    counts.set(cueId, (counts.get(cueId) || 0) + 1);
    if (!required.has(cueId)) extra.push(cueId);
    if (row && (row.state === 'stale' || (sourceRevision != null && row.sourceRevision !== sourceRevision))) stale.push(cueId);
    if (!row || row.state !== 'ready' || !row.output) notReady.push(cueId);
  });
  required.forEach((cueId) => { if (!counts.has(cueId)) missing.push(cueId); });
  counts.forEach((count, cueId) => { if (count > 1) duplicate.push(cueId); });
  const issues = [];
  if (missing.length) issues.push({ code: 'MISSING_CUES', cueIds: missing });
  if (extra.length) issues.push({ code: 'EXTRA_CUES', cueIds: extra });
  if (duplicate.length) issues.push({ code: 'DUPLICATE_CUES', cueIds: duplicate });
  if (stale.length) issues.push({ code: 'STALE_CUES', cueIds: [...new Set(stale)] });
  if (notReady.length) issues.push({ code: 'NOT_READY_CUES', cueIds: [...new Set(notReady)] });
  return { ready: issues.length === 0 && statuses.length === required.size, issues };
}

export function safeCueState(value) {
  return CUE_STATES.includes(value) ? value : 'not-requested';
}

export function quantizeClockMs(value) {
  return Math.max(0, Math.round(finite(value) / 100) * 100);
}

function hashText(value, seed) {
  let hash = seed >>> 0;
  for (const char of String(value)) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619) >>> 0;
  return hash;
}

function randomUnit(seed) {
  let x = seed >>> 0;
  x ^= x << 13; x ^= x >>> 17; x ^= x << 5;
  return (x >>> 0) / 4294967296;
}

export function deterministicPreviewState({ clockMs = 0, cueId = '', seed = 0x54424f54, pieces = 64 } = {}) {
  const clock = quantizeClockMs(clockMs);
  const base = hashText(cueId, seed);
  const confetti = Array.from({ length: Math.max(0, Math.round(pieces)) }, (_, index) => {
    const a = randomUnit(base + index * 101);
    const b = randomUnit(base + index * 211);
    const c = randomUnit(base + index * 307);
    const elapsed = Math.max(0, clock - Math.floor(a * 300));
    return {
      x: Number((0.465 + (b - 0.5) * Math.min(0.78, elapsed / 2400)).toFixed(4)),
      y: Number((0.54 - Math.sin(Math.min(1, elapsed / 1900) * Math.PI) * (0.25 + c * 0.35) + elapsed / 5200).toFixed(4)),
      rotation: Math.round((a * 720 + clock * (0.08 + c * 0.08)) % 360),
      colorIndex: Math.floor(c * 6),
    };
  });
  return Object.freeze({ clockMs: clock, cueId, confetti });
}

const branchLevel = (branch) => Number((/retry_level_(\d)/.exec(branch) || [])[1] || 0);

export function simulateConversationBranch(branch, step = {}, nextStep = null) {
  const selected = CONVERSATION_BRANCHES.includes(branch) ? branch : 'uncertain';
  const level = branchLevel(selected);
  const target = step.targetWord || step.stepKey || '';
  const firstMeaning = (step.vietnameseMeanings || [])[0] || '';
  const firstRelated = (step.relatedConcepts || [])[0] || '';
  const question = (step.questionSeeds || [])[0] || (step.teachingCopy && step.teachingCopy.prompt) || '';
  const shared = { branch: selected, coachingLevel: level, progress: step.progress || null };
  if (selected === 'target') return { ...shared, inputClass: selected, nextIntent: nextStep ? 'bridge_next_word' : 'celebrate_complete', question: nextStep ? nextStep.teachingCopy.intro : '', bridge: nextStep ? 'lesson.tvideoJourney.simulation.bridge.targetNext' : 'lesson.tvideoJourney.simulation.bridge.targetComplete', bridgeParams: { target, nextTarget: nextStep && nextStep.targetWord }, coaching: 'lesson.tvideoJourney.simulation.coaching.target', coachingParams: {}, cueId: `${step.stepKey}-celebrate`, effect: 'celebrate', progressResult: 'advance' };
  if (selected === 'meaning_vi') return { ...shared, inputClass: selected, nextIntent: 'bridge_to_english', question, bridge: 'lesson.tvideoJourney.simulation.bridge.meaning', bridgeParams: { meaning: firstMeaning, target }, coaching: 'lesson.tvideoJourney.simulation.coaching.meaning', coachingParams: { target }, cueId: `${step.stepKey}-teach`, effect: 'teach', progressResult: 'stay' };
  if (selected === 'related') return { ...shared, inputClass: selected, nextIntent: 'connect_related_concept', question, bridge: 'lesson.tvideoJourney.simulation.bridge.related', bridgeParams: { related: firstRelated, target }, coaching: 'lesson.tvideoJourney.simulation.coaching.related', coachingParams: { target }, cueId: `${step.stepKey}-thinking`, effect: 'thinking', progressResult: 'stay' };
  if (selected === 'silence') return { ...shared, inputClass: selected, nextIntent: 'gentle_reprompt', question, bridge: 'lesson.tvideoJourney.simulation.bridge.silence', bridgeParams: {}, coaching: 'lesson.tvideoJourney.simulation.coaching.silence', coachingParams: { target }, cueId: `${step.stepKey}-listen`, effect: 'listen', progressResult: 'stay' };
  if (selected === 'uncertain') return { ...shared, inputClass: selected, nextIntent: 'clarify_with_choice', question, bridge: 'lesson.tvideoJourney.simulation.bridge.uncertain', bridgeParams: {}, coaching: 'lesson.tvideoJourney.simulation.coaching.uncertain', coachingParams: { target }, cueId: `${step.stepKey}-thinking`, effect: 'thinking', progressResult: 'stay' };
  return { ...shared, inputClass: selected, nextIntent: `coach_level_${level}`, question, bridge: 'lesson.tvideoJourney.simulation.bridge.retry', bridgeParams: { level }, coaching: `lesson.tvideoJourney.simulation.coaching.retry${level}`, coachingParams: { target }, cueId: `${step.stepKey}-retry-level-${level}`, effect: `retry-level-${level}`, progressResult: 'stay' };
}

export function createFarmJourneyDraft() {
  const source = (mediaType, width, height) => ({ assetVersionId: '', sha256: '', mediaType, width, height });
  const step = (stepKey, targetWord, meaning, related, index) => ({
    stepKey, targetWord, vietnameseMeanings: [meaning], relatedConcepts: [related],
    questionSeeds: [index === 1 ? 'Where does the farmer keep hay?' : 'What are the golden bales near the road?'],
    teachingCopy: index === 1
      ? { intro: 'Welcome to the farm. This is a barn.', explanation: 'A barn is a large farm building for animals, hay, and tools.', prompt: 'Look at the red building beside TeeBot and say barn.' }
      : { intro: 'Great job. Now look beside the road: this is hay.', explanation: 'Hay is dried grass that farmers store and feed to animals.', prompt: 'Point to the golden bale and say hay.' },
    expectedAnswer: targetWord, progress: { index, count: 2 },
    pronunciation: { slowModel: index === 1 ? 'barn: b - ar - n' : 'hay: h - ay', approvedSegments: index === 1 ? ['b', 'ɑː', 'n'] : ['h', 'eɪ'], vietnameseL1Guidance: [index === 1 ? 'Mở khẩu hình rộng cho âm giữa; không thêm nguyên âm sau âm n cuối.' : 'Thở nhẹ âm h ở đầu, rồi lướt liền sang âm ay; không tách thành hai tiếng.'] },
    contextTurns: [index === 1 ? 'Child identifies the red farm building.' : 'Child locates the golden bale.'],
    teachingObject: source('image/png', 192, 192),
  });
  return {
    presetId: 'tvideoJourney', presetVersion: 1,
    assets: { background: source('video/mp4', 480, 320), robotClips: ROBOT_CLIP_ROLES.map((role) => ({ role, assetVersionId: '', sha256: '', mediaType: 'video/webm', alpha: true })) },
    scenePath: normalizeScenePath({ flightIngress: { start: { x: .9, y: .08, scale: .45 }, mid: { x: .72, y: .3, scale: .4 }, end: { x: .52, y: .63, scale: .35 } }, landing: { x: .52, y: .63, scale: .35 }, walk: { keyframes: [{ timeMs: 0, x: .52, y: .63, scale: .35 }, { timeMs: 1800, x: .465, y: .72, scale: .55 }, { timeMs: 3400, x: .445, y: .82, scale: .75 }, { timeMs: 5000, x: .465, y: .685, scale: .9 }] }, teachingAnchor: { x: .465, y: .685 }, objectAnchor: { x: .3, y: .66 }, safeZone: { x: .174, y: .081, width: .653, height: .775 } }),
    boundedContext: { maxTurns: 2 },
    steps: [step('barn', 'barn', 'nhà kho nông trại', 'farm building', 1), step('hay', 'hay', 'cỏ khô', 'animal food', 2)],
  };
}
