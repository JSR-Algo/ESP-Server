const DEFAULT_AUTHORING_FIELDS = Object.freeze({
  durationPreset: 5,
  teachingWord: { text: '', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord' },
  interaction: {
    template: 'safeSpeaking',
    maxAttempts: 3,
    listenTimeoutSec: 6,
    correctThreshold: 0.85,
    braveTryThreshold: 0.7,
    funPattern: 'copyMyMove',
  },
  motion: {
    present: 'teach',
    listen: 'listen',
    correct: 'celebrate',
    nearMiss: 'encourage',
    incorrect: 'tryAgain',
  },
  storyBeat: { goal: '', successReaction: '', nextTease: '' },
});
const DURATION_PRESETS = Object.freeze([3, 5, 8]);
const NAMED_MOTIONS = Object.freeze(['rest', 'teach', 'presentLeft', 'presentRight', 'listen', 'thinking', 'encourage', 'tryAgain', 'celebrate', 'goodbye']);

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function createAuthoringFields() {
  return clone(DEFAULT_AUTHORING_FIELDS);
}

function createInitialAuthoringFields({ teachingWord, prompt, subject } = {}) {
  const word = String(teachingWord || subject || '').trim();
  const topic = String(subject || word).trim();
  const goal = String(prompt || '').trim();
  return mergeAuthoringFields({}, {
    teachingWord: { text: word.toUpperCase() },
    storyBeat: {
      goal,
      successReaction: `Celebrate learning ${topic}.`,
      nextTease: `What will we discover about ${topic} next?`,
    },
  });
}

function mergeAuthoringFields(body, patch) {
  const source = body && typeof body === 'object' ? body : {};
  const next = patch && typeof patch === 'object' ? patch : {};
  const defaults = createAuthoringFields();
  const requestedDuration = Number(next.durationPreset || source.durationPreset || defaults.durationPreset);
  const mergedMotion = { ...defaults.motion, ...(source.motion || {}), ...(next.motion || {}) };
  Object.keys(mergedMotion).forEach((slot) => {
    if (!NAMED_MOTIONS.includes(mergedMotion[slot])) mergedMotion[slot] = defaults.motion[slot] || 'rest';
  });
  return {
    durationPreset: DURATION_PRESETS.includes(requestedDuration) ? requestedDuration : defaults.durationPreset,
    teachingWord: { ...defaults.teachingWord, ...(source.teachingWord || {}), ...(next.teachingWord || {}) },
    interaction: { ...defaults.interaction, ...(source.interaction || {}), ...(next.interaction || {}) },
    motion: mergedMotion,
    storyBeat: { ...defaults.storyBeat, ...(source.storyBeat || {}), ...(next.storyBeat || {}) },
  };
}

function engagementKind(step) {
  const body = step.stepBody || {};
  const pattern = body.interaction && body.interaction.funPattern;
  if (body.ending || step.stepType === 'celebrate' || step.stepType === 'ending') return 'ending';
  if (body.recall || step.stepType === 'review' || step.stepType === 'recall') return 'recall';
  if (pattern === 'copyMyMove' || (body.motion && step.stepType === 'imitate')) return 'motion';
  if (pattern && ['sillyChoice', 'soundGuess', 'missingObject', 'miniStoryRescue', 'fastSlow'].includes(pattern)) return 'minigame';
  if (body.interaction || ['listen', 'repeat', 'fillBlank', 'guess'].includes(step.stepType)) return 'voice';
  return 'passive';
}

function buildEngagementTrack(steps) {
  let elapsed = 0;
  return (Array.isArray(steps) ? steps : []).map((step, index) => {
    const body = step.stepBody || {};
    const durationSec = Math.max(1, Number(body.durationSec || body.timeoutSec || 10));
    const item = {
      stepKey: step.stepKey || `step-${index + 1}`,
      label: step.prompt || step.subject || step.stepType || `Step ${index + 1}`,
      kind: engagementKind(step),
      durationSec,
      startSec: elapsed,
      hasMotion: Boolean(body.motion && Object.values(body.motion).some(Boolean)),
    };
    elapsed += durationSec;
    return item;
  });
}

function assetIdentity(asset) {
  return asset.sha256 || asset.versionId || asset.path || asset.src || asset.assetKey;
}

function calculateReadiness({ steps, assets, manifest, validation } = {}) {
  const rows = Array.isArray(assets) ? assets : [];
  const unique = new Map();
  rows.forEach((asset) => {
    const key = assetIdentity(asset);
    if (key && !unique.has(key)) unique.set(key, asset);
  });
  const uniqueAssets = Array.from(unique.values());
  const decodedByLayer = {};
  uniqueAssets.forEach((asset) => {
    const layer = asset.layer || 'unassigned';
    const dimensionEstimate = Number(asset.width || 0) * Number(asset.height || 0) * (asset.hasAlpha ? 4 : 2);
    decodedByLayer[layer] = Math.max(decodedByLayer[layer] || 0, Number(asset.decodedBytes || asset.psramBytes || dimensionEstimate || 0));
  });
  const decodedValues = Object.values(decodedByLayer);
  const explicitPeak = uniqueAssets.reduce((peak, asset) => Math.max(peak, Number(asset.estimatedPeakPsram || 0)), 0);
  const decodedTotal = uniqueAssets.reduce((sum, asset) => {
    const dimensionEstimate = Number(asset.width || 0) * Number(asset.height || 0) * (asset.hasAlpha ? 4 : 2);
    return sum + Number(asset.decodedBytes || asset.psramBytes || dimensionEstimate || 0);
  }, 0);
  const estimatedPeakPsram = explicitPeak || (decodedByLayer.unassigned ? decodedTotal : decodedValues.reduce((sum, bytes) => sum + bytes, 0));
  const budget = validation && validation.budgets && validation.budgets.espTft;
  const metrics = budget && budget.metrics;
  const errors = budget && Array.isArray(budget.errors) ? budget.errors : [];
  const warnings = budget && Array.isArray(budget.warnings) ? budget.warnings : [];
  const validationKnown = Boolean(metrics);
  const issueCodes = new Set([...errors, ...warnings].map((issue) => issue && issue.code).filter(Boolean));
  const authoritativePeak = metrics && Number(metrics.estimatedVisualPeakBytes);
  const authoritativePackBytes = metrics && Number(metrics.packBytes);
  const authoritativeAssetCount = metrics && Number(metrics.assetCount);
  const authoritativeUniqueCount = metrics && Number(metrics.uniqueAssetCount);
  const authoritativeSharedCount = metrics && Number(metrics.sharedAssetCount);
  const explicitOffline = metrics && typeof metrics.offlineReady === 'boolean' ? metrics.offlineReady : null;
  const explicitTermination = metrics && typeof metrics.allPathsTerminate === 'boolean' ? metrics.allPathsTerminate : null;
  return {
    downloadBytes: Number.isFinite(authoritativePackBytes) ? authoritativePackBytes : uniqueAssets.reduce((sum, asset) => sum + Number(asset.bytes || 0), 0),
    uniqueAssetCount: Number.isFinite(authoritativeUniqueCount) ? authoritativeUniqueCount : (Number.isFinite(authoritativeAssetCount) ? authoritativeAssetCount : uniqueAssets.length),
    sharedReferenceCount: Number.isFinite(authoritativeSharedCount) ? authoritativeSharedCount : Math.max(0, rows.length - uniqueAssets.length),
    estimatedPeakPsram: Number.isFinite(authoritativePeak) ? authoritativePeak : estimatedPeakPsram,
    estimateOnly: !validationKnown,
    offlineReady: explicitOffline == null ? false : explicitOffline,
    allPathsTerminate: explicitTermination == null ? (validationKnown && !issueCodes.has('branch-termination')) : explicitTermination,
    errors,
    warnings,
  };
}

module.exports = {
  DEFAULT_AUTHORING_FIELDS,
  DURATION_PRESETS,
  NAMED_MOTIONS,
  buildEngagementTrack,
  calculateReadiness,
  createAuthoringFields,
  createInitialAuthoringFields,
  mergeAuthoringFields,
};
