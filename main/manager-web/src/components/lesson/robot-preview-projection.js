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
  'silence',
  'sttUnavailable',
  'missingOptionalVisual'
]);

const DEFAULT_PATHS = Object.freeze({
  correct: { prompt: 'Correct. Continue.', motionPreset: 'celebrate' },
  nearMiss: { prompt: 'Almost. Try once more.', motionPreset: 'encourage' },
  incorrect: { prompt: 'Let us try together.', motionPreset: 'gentle-shake' },
  silence: { prompt: 'Take your time.', motionPreset: 'patient-wait' },
  sttUnavailable: { prompt: 'Listening is unavailable. Follow along.', motionPreset: 'calm-idle' },
  missingOptionalVisual: { prompt: 'The optional visual is unavailable.', motionPreset: 'teach' }
});

const FORBIDDEN_KEYS = /(?:rawServo|servoAngle|servoCommand|firmwareCommand|motorCommand)/i;
const FORBIDDEN_MEDIA = /\.(?:gif|webm|mp4|mov|m4v)(?:[?#].*)?$/i;

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

function mediaSource(value) {
  const candidate = asObject(value);
  return typeof candidate.src === 'string' ? candidate.src : '';
}

function walkForbidden(value, warnings, path = 'manifest') {
  if (Array.isArray(value)) {
    value.forEach((item, index) => walkForbidden(item, warnings, `${path}[${index}]`));
    return;
  }
  if (!value || typeof value !== 'object') return;
  Object.entries(value).forEach(([key, child]) => {
    const childPath = `${path}.${key}`;
    if (FORBIDDEN_KEYS.test(key)) warnings.add(`Forbidden raw servo or firmware command at ${childPath}. Use a named motion preset.`);
    if (key === 'video' && child) warnings.add(`Forbidden background video at ${childPath}. espTft requires a poster.`);
    if (typeof child === 'string' && FORBIDDEN_MEDIA.test(child)) {
      const kind = /\.gif/i.test(child) ? 'GIF' : 'video';
      warnings.add(`Forbidden ${kind} source at ${childPath}. Use PNG/JPEG static assets.`);
    }
    walkForbidden(child, warnings, childPath);
  });
}

export function findForbiddenFirmwareCapabilities(manifest) {
  const warnings = new Set();
  const value = asObject(manifest);
  if (value.profile && value.profile !== 'espTft') warnings.add(`Unsupported profile "${value.profile}". Preview parity only applies to espTft.`);
  walkForbidden(value, warnings);
  return [...warnings].sort();
}

export function projectEspTftPreview(manifest, stepIndex = 0, requestedPath = 'correct') {
  const served = asObject(manifest);
  const steps = Array.isArray(served.steps) ? served.steps : [];
  const safeIndex = Math.max(0, Math.min(Number.isInteger(stepIndex) ? stepIndex : 0, Math.max(steps.length - 1, 0)));
  const step = asObject(steps[safeIndex]);
  const scene = asObject(step.scene);
  const background = asObject(scene.backgroundScene);
  const object = asObject(scene.teachingObject);
  const robot = asObject(scene.robotOverlay);
  const path = RESPONSE_PATHS.includes(requestedPath) ? requestedPath : 'correct';
  const response = { ...DEFAULT_PATHS[path], ...asObject(asObject(step.responsePaths)[path]) };
  const motionPreset = String(response.motionPreset || step.motionPreset || robot.pose || 'neutral');
  const optionalVisualMissing = path === 'missingOptionalVisual';

  return {
    profile: served.profile || null,
    lessonId: served.lessonId || null,
    durationMinutes: Number(served.durationMinutes) || null,
    step: { index: safeIndex, count: steps.length, id: step.id || null },
    path,
    stage: ESP_TFT_GEOMETRY.stage,
    safeZones: ESP_TFT_GEOMETRY.safeZones,
    layers: [
      { id: 'background', z: 0, bounds: ESP_TFT_GEOMETRY.background, src: mediaSource(background.poster), visible: Boolean(mediaSource(background.poster)) },
      { id: 'teachingObject', z: 10, bounds: ESP_TFT_GEOMETRY.teachingObject, src: optionalVisualMissing ? '' : mediaSource(object.asset), visible: !optionalVisualMissing && Boolean(mediaSource(object.asset)) },
      { id: 'robotOverlay', z: 20, bounds: ESP_TFT_GEOMETRY.robotOverlay, src: mediaSource(robot.asset) || mediaSource(robot.atlas), visible: Boolean(mediaSource(robot.asset) || mediaSource(robot.atlas)) },
      { id: 'wordPill', z: 30, bounds: ESP_TFT_GEOMETRY.wordPill, text: String(object.primaryWord || ''), visible: Boolean(object.primaryWord) },
      { id: 'progress', z: 40, bounds: ESP_TFT_GEOMETRY.progress, active: safeIndex + 1, total: steps.length, visible: steps.length > 0 },
      { id: 'prompt', z: 50, bounds: ESP_TFT_GEOMETRY.prompt, text: String(response.prompt || step.prompt || ''), visible: Boolean(response.prompt || step.prompt) }
    ],
    timeline: [
      { atMs: 0, label: `Render step: ${step.id || safeIndex + 1}` },
      { atMs: 120, label: `Slave command: ${motionPreset}` },
      { atMs: 240, label: `Response path: ${path}` }
    ],
    warnings: findForbiddenFirmwareCapabilities(served)
  };
}
