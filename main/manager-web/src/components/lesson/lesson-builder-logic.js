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

/**
 * Authoring helper inputs must be acyclic JSON trees: JSON primitives, standard
 * arrays, and plain objects whose prototype is Object.prototype.
 */
function assertJsonTree(value, label, ancestors = new Set()) {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number' && Number.isFinite(value)) return;
  if (!value || typeof value !== 'object') {
    throw new TypeError(`${label} must contain only JSON primitives, arrays, and plain objects`);
  }

  const prototype = Object.getPrototypeOf(value);
  const isStandardArray = Array.isArray(value) && prototype === Array.prototype;
  const isPlainObject = !Array.isArray(value) && prototype === Object.prototype;
  if (!isStandardArray && !isPlainObject) {
    throw new TypeError(`${label} must contain only JSON primitives, arrays, and plain objects`);
  }
  if (ancestors.has(value)) throw new TypeError(`${label} must be an acyclic JSON tree`);

  ancestors.add(value);
  Object.values(value).forEach((item) => assertJsonTree(item, label, ancestors));
  ancestors.delete(value);
}

function isKeyedAssetReference(value, assetKey, path) {
  const propertyName = path[path.length - 1];
  const parentProperty = path[path.length - 2];
  const isPersistedAsset = propertyName === 'asset';
  const isBackgroundPoster = parentProperty === 'backgroundScene' && propertyName === 'poster';
  return (isPersistedAsset || isBackgroundPoster) && value.key === assetKey;
}

function containsAssetKey(value, assetKey, path = []) {
  if (Array.isArray(value)) return value.some((item) => containsAssetKey(item, assetKey, path));
  if (!value || typeof value !== 'object') return false;
  if (value.assetKey === assetKey) return true;
  if (isKeyedAssetReference(value, assetKey, path)) return true;
  return Object.entries(value).some(([key, item]) => containsAssetKey(item, assetKey, path.concat(key)));
}

/** Finds references in authoring bodies, which must be acyclic JSON trees. */
function collectAssetReferences(steps, assetKey) {
  return (Array.isArray(steps) ? steps : [])
    .filter((step) => {
      const body = step.stepBody || step.body || {};
      assertJsonTree(body, 'step body');
      return containsAssetKey(body, assetKey);
    })
    .map((step) => step.stepKey || step.stepId)
    .filter(Boolean);
}

function nextClonedAssetKey(assetKey, assets) {
  const match = String(assetKey || '').match(/^(.*)\.v(\d+)$/);
  const base = match ? match[1] : String(assetKey || '');
  const maxVersion = (Array.isArray(assets) ? assets : []).reduce(
    (maximum, asset) => {
      const candidate = String(asset.assetKey || '').match(/^(.*)\.v(\d+)$/);
      if (!candidate || candidate[1] !== base) return maximum;
      return Math.max(maximum, Number(candidate[2]));
    },
    match ? Number(match[2]) : 1,
  );
  return `${base}.v${maxVersion + 1}`;
}

function replaceAssetReference(value, fromKey, clonedAsset, path = []) {
  if (Array.isArray(value)) {
    return value.map((item) => replaceAssetReference(item, fromKey, clonedAsset, path));
  }
  if (!value || typeof value !== 'object') return value;

  const replaced = Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      replaceAssetReference(item, fromKey, clonedAsset, path.concat(key)),
    ]),
  );
  if (isKeyedAssetReference(value, fromKey, path)) {
    return {
      ...replaced,
      assetId: clonedAsset.assetId,
      key: clonedAsset.assetKey,
      src: clonedAsset.path || clonedAsset.src,
      sha256: clonedAsset.sha256,
    };
  }
  if (value.assetKey !== fromKey) return replaced;
  return {
    ...replaced,
    assetId: clonedAsset.assetId,
    assetKey: clonedAsset.assetKey,
    path: clonedAsset.path,
    sha256: clonedAsset.sha256,
  };
}

/** Immutably rewrites an acyclic JSON step body using a plain JSON clone record. */
function replaceStepAssetReference(value, fromKey, clonedAsset) {
  assertJsonTree(value, 'step body');
  assertJsonTree(clonedAsset, 'cloned asset');
  const prototype = clonedAsset && typeof clonedAsset === 'object'
    ? Object.getPrototypeOf(clonedAsset)
    : undefined;
  if (!clonedAsset || Array.isArray(clonedAsset)
    || prototype !== Object.prototype) {
    throw new TypeError('cloned asset must be a plain JSON object');
  }
  return replaceAssetReference(value, fromKey, clonedAsset);
}

function calculateReadiness({ steps, assets, manifest } = {}) {
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
    decodedByLayer[layer] = Math.max(decodedByLayer[layer] || 0, Number(asset.decodedBytes || asset.psramBytes || 0));
  });
  const decodedValues = Object.values(decodedByLayer);
  const explicitPeak = uniqueAssets.reduce((peak, asset) => Math.max(peak, Number(asset.estimatedPeakPsram || 0)), 0);
  const decodedTotal = uniqueAssets.reduce((sum, asset) => sum + Number(asset.decodedBytes || asset.psramBytes || 0), 0);
  const estimatedPeakPsram = explicitPeak || (decodedByLayer.unassigned ? decodedTotal : decodedValues.reduce((sum, bytes) => sum + bytes, 0));
  const offlineReady = uniqueAssets.every((asset) => {
    const src = asset.path || asset.src || '';
    return !/^https?:\/\//i.test(src) && asset.offlineReady !== false;
  });
  const stepRows = Array.isArray(steps) ? steps : [];
  const inferredTermination = stepRows.length > 0 && stepRows.every((step) => {
    const interaction = step.stepBody && step.stepBody.interaction;
    return !interaction || Number(interaction.maxAttempts || 3) <= 3;
  });
  return {
    downloadBytes: uniqueAssets.reduce((sum, asset) => sum + Number(asset.bytes || 0), 0),
    uniqueAssetCount: uniqueAssets.length,
    sharedReferenceCount: Math.max(0, rows.length - uniqueAssets.length),
    estimatedPeakPsram,
    offlineReady,
    allPathsTerminate: manifest && typeof manifest.pathsTerminate === 'boolean'
      ? manifest.pathsTerminate
      : inferredTermination,
  };
}

module.exports = {
  DEFAULT_AUTHORING_FIELDS,
  DURATION_PRESETS,
  NAMED_MOTIONS,
  buildEngagementTrack,
  calculateReadiness,
  collectAssetReferences,
  createAuthoringFields,
  mergeAuthoringFields,
  nextClonedAssetKey,
  replaceStepAssetReference,
};
