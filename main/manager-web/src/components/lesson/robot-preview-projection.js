export const ESP_TFT_GEOMETRY = Object.freeze({
  stage: Object.freeze({ width: 480, height: 320 }),
  safeZones: Object.freeze({ top: 24, right: 12, bottom: 82, left: 12 }),
  background: Object.freeze({ x: 0, y: 0, width: 480, height: 320, fit: 'cover' }),
  teachingObject: Object.freeze({ x: 129, y: 53, width: 221, height: 160, fit: 'contain' }),
  robotOverlay: Object.freeze({ x: 20, y: 158, width: 154, height: 109, fit: 'contain' }),
  wordPill: Object.freeze({ x: 166, y: 26, width: 148, height: 42 }),
  prompt: Object.freeze({ x: 19, y: 238, width: 442, height: 77 }),
  progress: Object.freeze({ x: 188, y: 218, width: 104, height: 12 })
});

export const RESPONSE_PATHS = Object.freeze([
  'correct',
  'nearMiss',
  'incorrect',
  'retry',
  'timeout',
  'braveTry',
  'completion',
  'silence',
  'sttUnavailable',
  'missingOptionalVisual'
]);

export const RENDERER_V2_MANIFEST_VERSION = 'teebot-lesson-renderer.v2';
export const RENDERER_V3_MANIFEST_VERSION = 'teebot-lesson-renderer.v3';
export const RENDERER_V2_OPENING_PHASES = Object.freeze([
  'hidden', 'flyIn', 'landFar', 'settle', 'walkToward', 'arriveNear', 'greetIdle', 'revealTeachingContent'
]);
export const RENDERER_V2_OPENING_GEOMETRY = Object.freeze({
  centerRoad: Object.freeze({
    entry: Object.freeze({ x: 400, y: 22, width: 96, height: 84 }),
    land: Object.freeze({ x: 284, y: 116, width: 96, height: 84 }),
    arrived: Object.freeze({ x: 184, y: 184, width: 112, height: 56 })
  }),
  leftApproach: Object.freeze({
    entry: Object.freeze({ x: 24, y: 22, width: 96, height: 84 }),
    land: Object.freeze({ x: 104, y: 116, width: 96, height: 84 }),
    arrived: Object.freeze({ x: 42, y: 148, width: 108, height: 92 })
  }),
  rightApproach: Object.freeze({
    entry: Object.freeze({ x: 410, y: 22, width: 96, height: 84 }),
    land: Object.freeze({ x: 326, y: 116, width: 96, height: 84 }),
    arrived: Object.freeze({ x: 330, y: 148, width: 108, height: 92 })
  })
});
export const VISUAL_STATES = Object.freeze([
  'teach',
  'listen',
  'thinking',
  'correct',
  'nearMiss',
  'incorrect',
  'retry',
  'celebrate',
  'completion'
]);
export const DEGRADED_REASONS = Object.freeze([
  'missingOverlay',
  'animationStartFailed',
  'phaseTimeout',
  'reducedMotion',
  'unsupportedContract',
  'assetIdentityMismatch',
  'insufficientHeap'
]);

const DEFAULT_PATHS = Object.freeze({
  correct: { prompt: 'Correct. Continue.', motionPreset: 'celebrate' },
  nearMiss: { prompt: 'Almost. Try once more.', motionPreset: 'encourage' },
  incorrect: { prompt: 'Let us try together.', motionPreset: 'gentle-shake' },
  retry: { prompt: 'Let us try once more.', motionPreset: 'tryAgain' },
  timeout: { prompt: 'Time is up. We can continue together.', motionPreset: 'encourage' },
  braveTry: { prompt: 'That was a brave try. Keep going!', motionPreset: 'encourage' },
  completion: { prompt: 'Lesson complete. Wonderful helping!', motionPreset: 'celebrate' },
  silence: { prompt: 'Take your time.', motionPreset: 'patient-wait' },
  sttUnavailable: { prompt: 'Listening is unavailable. Follow along.', motionPreset: 'calm-idle' },
  missingOptionalVisual: { prompt: 'The optional visual is unavailable.', motionPreset: 'teach' }
});

const FORBIDDEN_KEYS = /(?:rawServo|servoAngle|servoCommand|firmwareCommand|motorCommand)/i;
const FORBIDDEN_MEDIA = /\.(?:gif|webm|mov|m4v)(?:[?#].*)?$/i;
const MP4_MEDIA = /\.mp4(?:[?#].*)?$/i;
const MOTION_PATH_KEY = Object.freeze({ retry: 'incorrect', timeout: 'listen', braveTry: 'nearMiss', completion: 'correct' });
const PATH_STATE = Object.freeze({
  timeout: 'listen',
  braveTry: 'nearMiss',
  silence: 'listen',
  sttUnavailable: 'teach',
  missingOptionalVisual: 'teach'
});
const VISUAL_STATE_DEFAULTS = Object.freeze({
  teach: { prompt: 'Let us learn together.', motionPreset: 'teach' },
  listen: { prompt: 'I am listening.', motionPreset: 'listen' },
  thinking: { prompt: 'Let us think.', motionPreset: 'thinking' },
  correct: DEFAULT_PATHS.correct,
  nearMiss: DEFAULT_PATHS.nearMiss,
  incorrect: DEFAULT_PATHS.incorrect,
  retry: DEFAULT_PATHS.retry,
  celebrate: { prompt: 'Wonderful work!', motionPreset: 'celebrate' },
  completion: DEFAULT_PATHS.completion
});
const DEGRADED_FALLBACKS = Object.freeze({
  missingOverlay: { fallback: 'contentWithoutRobotOverlay', hideOverlay: true },
  animationStartFailed: { fallback: 'staticArrivedPose', hideOverlay: false },
  phaseTimeout: { fallback: 'staticArrivedPose', hideOverlay: false },
  reducedMotion: { fallback: 'staticArrivedPose', hideOverlay: false },
  unsupportedContract: { fallback: 'staticSafeScene', hideOverlay: true },
  assetIdentityMismatch: { fallback: 'staticSafeScene', hideOverlay: true },
  insufficientHeap: { fallback: 'verifiedStaticLayers', hideOverlay: true }
});

function interpolateOpeningValue(from, to, elapsed, duration) {
  return from + Math.trunc(((to - from) * elapsed) / duration);
}

function interpolateOpeningRect(from, to, elapsed, duration) {
  return {
    x: interpolateOpeningValue(from.x, to.x, elapsed, duration),
    y: interpolateOpeningValue(from.y, to.y, elapsed, duration),
    width: interpolateOpeningValue(from.width, to.width, elapsed, duration),
    height: interpolateOpeningValue(from.height, to.height, elapsed, duration)
  };
}

function defaultOpeningBoundaries(phases) {
  const duration = Object.fromEntries(phases.map((phase) => [phase.name, phase.durationMs]));
  const firstWalkHalf = Math.trunc(duration.walkToward / 2);
  return [
    { name: 'hidden', advanceMs: 0 },
    { name: 'flyIn', advanceMs: duration.hidden },
    { name: 'landFar', advanceMs: duration.flyIn },
    { name: 'settle', advanceMs: duration.landFar },
    { name: 'walkToward', advanceMs: duration.settle },
    { name: 'walkTowardMidpoint', advanceMs: firstWalkHalf },
    { name: 'arriveNear', advanceMs: duration.walkToward - firstWalkHalf },
    { name: 'greetIdle', advanceMs: duration.arriveNear },
    { name: 'revealTeachingContent', advanceMs: duration.greetIdle }
  ];
}

export function projectRendererV2OpeningTrace(templateProjection, requestedBoundaries = null) {
  const contract = asObject(templateProjection);
  const phases = Array.isArray(contract.phases) ? contract.phases : [];
  const phaseNames = phases.map((phase) => phase && phase.name);
  const geometry = RENDERER_V2_OPENING_GEOMETRY[contract.layoutPreset];
  if (contract.templateId !== 'tvideoFlyWalk' || contract.templateVersion !== 1 ||
      contract.geometryVersion !== 1 || !geometry ||
      phaseNames.length !== RENDERER_V2_OPENING_PHASES.length ||
      phaseNames.some((name, index) => name !== RENDERER_V2_OPENING_PHASES[index]) ||
      phases.some((phase) => !Number.isInteger(phase.durationMs) || phase.durationMs <= 0)) return [];

  const boundaries = Array.isArray(requestedBoundaries) ? requestedBoundaries : defaultOpeningBoundaries(phases);
  let phaseIndex = 0;
  let elapsed = 0;
  return boundaries.map((boundary) => {
    elapsed += Number.isInteger(boundary.advanceMs) ? boundary.advanceMs : 0;
    while (phaseIndex < RENDERER_V2_OPENING_PHASES.length - 1 && elapsed >= phases[phaseIndex].durationMs) {
      elapsed -= phases[phaseIndex].durationMs;
      phaseIndex += 1;
    }
    if (phaseIndex === RENDERER_V2_OPENING_PHASES.length - 1) elapsed = 0;
    const phase = RENDERER_V2_OPENING_PHASES[phaseIndex];
    let bounds = geometry.arrived;
    if (phase === 'hidden') bounds = geometry.entry;
    if (phase === 'flyIn') bounds = interpolateOpeningRect(geometry.entry, geometry.land, elapsed, phases[1].durationMs);
    if (phase === 'landFar' || phase === 'settle') bounds = geometry.land;
    if (phase === 'walkToward') bounds = interpolateOpeningRect(geometry.land, geometry.arrived, elapsed, phases[4].durationMs);
    return {
      boundary: String(boundary.name || phase),
      phase,
      bounds: { ...bounds },
      contentVisible: phase === 'revealTeachingContent'
    };
  });
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function mediaSource(value) {
  const candidate = asObject(value);
  return typeof candidate.src === 'string' ? candidate.src : '';
}

function walkForbidden(value, warnings, path = 'manifest', allowDirectMp4 = false) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkForbidden(item, warnings, `${path}[${index}]`, allowDirectMp4));
    return;
  }
  if (!value || typeof value !== 'object') return;
  Object.entries(value).forEach(([key, child]) => {
    const childPath = `${path}.${key}`;
    if (FORBIDDEN_KEYS.test(key)) warnings.add(`Forbidden raw servo or firmware command at ${childPath}. Use a named motion preset.`);
    if (key === 'video' && child) warnings.add(`Forbidden background video at ${childPath}. espTft requires a poster.`);
    if (typeof child === 'string' && (FORBIDDEN_MEDIA.test(child) || (!allowDirectMp4 && MP4_MEDIA.test(child)))) {
      const kind = /\.gif/i.test(child) ? 'GIF' : 'video';
      warnings.add(`Forbidden ${kind} source at ${childPath}. Use PNG/JPEG static assets.`);
    }
    walkForbidden(child, warnings, childPath, allowDirectMp4);
  });
}

function supportsDirectMp4Cinematic(manifest) {
  const value = asObject(manifest);
  const feature = asObject(asObject(value.features).lessonRendererV3);
  return value.manifestVersion === RENDERER_V3_MANIFEST_VERSION
    && value.protocolVersion === RENDERER_V3_MANIFEST_VERSION
    && feature.directMp4Cinematic === true
    && feature.assetSource === 'publishedVersionedVisualRefs';
}

export function findForbiddenFirmwareCapabilities(manifest) {
  const warnings = new Set();
  const value = asObject(manifest);
  if (value.profile && value.profile !== 'espTft') warnings.add(`Unsupported profile "${value.profile}". Preview parity only applies to espTft.`);
  walkForbidden(value, warnings, 'manifest', supportsDirectMp4Cinematic(value));
  return [...warnings].sort();
}

function cinematicLayer(manifest, slot) {
  if (!supportsDirectMp4Cinematic(manifest)) return null;
  const phases = Array.isArray(manifest.cinematicPhases) ? manifest.cinematicPhases : [];
  const phase = phases.find((candidate) => asObject(candidate).templateId === 'directMp4Cinematic');
  const layers = Array.isArray(asObject(phase).layers) ? phase.layers : [];
  return layers.find((candidate) => asObject(candidate).slot === slot) || null;
}

function cinematicBounds(layer, fallback, fit) {
  const rect = asObject(asObject(asObject(layer).metadata).rect);
  const valid = ['x', 'y', 'width', 'height'].every((key) => Number.isFinite(rect[key]));
  return { ...(valid ? rect : fallback), fit };
}

function cinematicSource(layer) {
  const value = asObject(layer);
  return typeof value.url === 'string' && value.url ? value.url : (typeof value.path === 'string' ? value.path : '');
}

function rendererLabel(manifestVersion) {
  const match = String(manifestVersion || '').match(/\.v(\d+)$/);
  return match ? `Renderer v${match[1]}` : 'Renderer unknown';
}

function stringList(value) {
  if (Array.isArray(value)) return value.map(String);
  if (typeof value === 'string') return value.split(',').map((item) => item.trim()).filter(Boolean);
  return null;
}

function rendererCapability(served, metadata) {
  const responseFeatures = asObject(metadata.features);
  const manifestFeatures = asObject(served.features);
  const advertised = stringList(responseFeatures.renderer)
    || stringList(manifestFeatures.renderer)
    || stringList(metadata.rendererCapabilities)
    || stringList(served.rendererCapabilities);
  const feature = asObject(responseFeatures.lessonRendererV2);
  const manifestFeature = asObject(manifestFeatures.lessonRendererV2);
  const featureReported = Object.keys(feature).length > 0 || Object.keys(manifestFeature).length > 0;
  return {
    supported: advertised ? advertised.includes(RENDERER_V2_MANIFEST_VERSION) : (featureReported ? true : null),
    advertised: advertised || []
  };
}

function physicalMotionOwner(served, metadata) {
  const responseFeatures = asObject(metadata.features);
  const manifestFeatures = asObject(served.features);
  const candidates = [
    asObject(responseFeatures.lessonRendererV2).physicalMotionOwner,
    asObject(metadata.runtimeControls).physicalMotionOwner,
    asObject(asObject(metadata.body).runtimeControls).physicalMotionOwner,
    asObject(manifestFeatures.lessonRendererV2).physicalMotionOwner,
    asObject(served.runtimeControls).physicalMotionOwner,
    served.physicalMotionOwner
  ];
  const owner = candidates.find((value) => typeof value === 'string' && value.trim());
  return owner ? owner.trim() : null;
}

function openingEntranceCount(served, steps) {
  const lessonOpening = Object.keys(asObject(served.openingEntrance)).length ? 1 : 0;
  return lessonOpening + steps.filter((item) => {
    const entrance = String(asObject(item).entrance || '').toLowerCase();
    return entrance && entrance !== 'none';
  }).length;
}

export function projectEspTftPreview(manifest, stepIndex = 0, requestedPath = 'correct', requestedDegradedReason = null, rendererMetadata = null) {
  const served = asObject(manifest);
  const metadata = asObject(rendererMetadata);
  const steps = Array.isArray(served.steps) ? served.steps : [];
  const safeIndex = Math.max(0, Math.min(Number.isInteger(stepIndex) ? stepIndex : 0, Math.max(steps.length - 1, 0)));
  const step = asObject(steps[safeIndex]);
  const scene = asObject(step.scene);
  const background = asObject(scene.backgroundScene);
  const object = asObject(scene.teachingObject);
  const robot = asObject(scene.robotOverlay);
  const teachingWord = asObject(step.teachingWord);
  const motion = asObject(step.motion);
  const visualStateRequested = VISUAL_STATES.includes(requestedPath);
  const path = RESPONSE_PATHS.includes(requestedPath) ? requestedPath : 'correct';
  const visualState = visualStateRequested ? requestedPath : (PATH_STATE[path] || path);
  const responseOverride = asObject(asObject(step.responsePaths)[path]);
  const visualStateOverride = asObject(asObject(step.visualStates)[visualState]);
  const response = {
    ...(visualStateRequested ? VISUAL_STATE_DEFAULTS[visualState] : DEFAULT_PATHS[path]),
    ...responseOverride,
    ...visualStateOverride
  };
  const motionPreset = String(visualStateOverride.motionPreset || responseOverride.motionPreset || motion[MOTION_PATH_KEY[visualState] || visualState] || response.motionPreset || step.motionPreset || robot.pose || 'neutral');
  const optionalVisualMissing = path === 'missingOptionalVisual';
  const degradedReason = DEGRADED_REASONS.includes(requestedDegradedReason) ? requestedDegradedReason : null;
  const degradedFallback = degradedReason ? DEGRADED_FALLBACKS[degradedReason] : null;
  const manifestVersion = String(served.manifestVersion || '');
  const v2 = manifestVersion === RENDERER_V2_MANIFEST_VERSION;
  const v3 = supportsDirectMp4Cinematic(served);
  const capability = rendererCapability(served, metadata);
  const motionOwner = physicalMotionOwner(served, metadata);
  const opening = asObject(served.openingEntrance);
  const openingPhaseTrace = v2 ? projectRendererV2OpeningTrace(step.templateProjection) : [];
  const openingCount = openingEntranceCount(served, steps);
  const warnings = new Set(findForbiddenFirmwareCapabilities(served));
  if (v2 && openingCount !== 1) warnings.add('Renderer v2 requires exactly one opening entrance.');
  if (v2 && motionOwner && motionOwner !== 'server') warnings.add('Renderer v2 requires physicalMotionOwner=server.');
  if (v2 && capability.supported === false) warnings.add('Selected firmware is renderer-v1 only; the authored renderer-v2 entrance and visual states are unsupported.');
  if (degradedReason) warnings.add(`Deterministic degraded fallback: ${degradedReason} -> ${degradedFallback.fallback}.`);
  const cinematicBackground = cinematicLayer(served, 'backgroundScene');
  const cinematicObject = cinematicLayer(served, 'teachingObject');
  const cinematicRobot = cinematicLayer(served, 'robotOverlay');
  const backgroundSrc = v3 ? cinematicSource(cinematicBackground) : mediaSource(background.poster);
  const objectSrc = v3 ? cinematicSource(cinematicObject) : mediaSource(object.asset);
  const robotSrc = v3 ? cinematicSource(cinematicRobot) : (mediaSource(robot.asset) || mediaSource(robot.atlas));
  const hideRobotOverlay = Boolean(degradedFallback && degradedFallback.hideOverlay);

  return {
    manifestVersion,
    rendererLabel: rendererLabel(manifestVersion),
    physicalMotionOwner: motionOwner,
    openingEntrance: opening,
    openingPhaseTrace,
    openingEntranceCount: openingCount,
    capability,
    degraded: { active: Boolean(degradedReason), reason: degradedReason, fallback: degradedFallback ? degradedFallback.fallback : null },
    profile: served.profile || null,
    lessonId: served.lessonId || null,
    durationMinutes: Number(served.durationMinutes) || null,
    step: {
      index: safeIndex,
      count: steps.length,
      ...(step.id ? { id: step.id } : {}),
      ...(step.type ? { type: step.type } : {})
    },
    // Surfaced for the animated "play the lesson" preview: the step's entrance
    // transition and the motion preset the robot performs when it presents the step.
    entrance: v2
      ? (safeIndex === 0 && openingCount === 1 ? String(opening.preset || 'opening') : 'none')
      : String(step.entrance || ''),
    presentMotion: String(motion.present || step.motionPreset || robot.pose || ''),
    motionPreset,
    path,
    visualState,
    stage: ESP_TFT_GEOMETRY.stage,
    safeZones: ESP_TFT_GEOMETRY.safeZones,
    layers: [
      { id: 'background', z: 0, bounds: v3 ? cinematicBounds(cinematicBackground, ESP_TFT_GEOMETRY.background, 'cover') : ESP_TFT_GEOMETRY.background, src: backgroundSrc, mediaType: v3 ? String(asObject(cinematicBackground).mediaType || '') : '', chromaKey: null, visible: Boolean(backgroundSrc) },
      { id: 'teachingObject', z: 10, bounds: v3 ? cinematicBounds(cinematicObject, ESP_TFT_GEOMETRY.teachingObject, 'contain') : ESP_TFT_GEOMETRY.teachingObject, src: optionalVisualMissing ? '' : objectSrc, mediaType: v3 ? String(asObject(cinematicObject).mediaType || '') : '', chromaKey: v3 ? (asObject(asObject(cinematicObject).metadata).chromaKey || null) : null, visible: !optionalVisualMissing && Boolean(objectSrc) },
      { id: 'robotOverlay', z: 20, bounds: v3 ? cinematicBounds(cinematicRobot, ESP_TFT_GEOMETRY.robotOverlay, 'contain') : (openingPhaseTrace.length ? openingPhaseTrace[openingPhaseTrace.length - 1].bounds : ESP_TFT_GEOMETRY.robotOverlay), src: robotSrc, mediaType: v3 ? String(asObject(cinematicRobot).mediaType || '') : '', chromaKey: v3 ? (asObject(asObject(cinematicRobot).metadata).chromaKey || null) : null, visible: !hideRobotOverlay && Boolean(robotSrc), overlayKey: String(visualStateOverride.overlayKey || robot.assetKey || robot.overlayKey || '') },
      { id: 'wordPill', z: 30, bounds: ESP_TFT_GEOMETRY.wordPill, text: String(teachingWord.text || object.primaryWord || ''), visible: Boolean(teachingWord.text || object.primaryWord) },
      { id: 'progress', z: 40, bounds: ESP_TFT_GEOMETRY.progress, active: safeIndex + 1, total: steps.length, visible: steps.length > 0 },
      { id: 'prompt', z: 50, bounds: ESP_TFT_GEOMETRY.prompt, text: String(response.prompt || step.prompt || ''), visible: Boolean(response.prompt || step.prompt) }
    ],
    timeline: [
      { atMs: 0, label: `Render step: ${step.id || safeIndex + 1}` },
      { atMs: 120, label: `${v2 ? 'Server motion' : 'Slave command'}: ${motionPreset}` },
      { atMs: 240, label: v2 ? `Visual state: ${visualState}` : `Response path: ${path}` }
    ],
    warnings: [...warnings].sort()
  };
}
