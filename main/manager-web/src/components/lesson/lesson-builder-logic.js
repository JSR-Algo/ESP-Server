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

function stepReferencesAssetInLayer(value, assetKey, layer) {
  assertJsonTree(value, 'step body');
  const paths = {
    teachingObject: ['teachingObject'],
    backgroundScene: ['backgroundScene'],
    robotOverlay: ['robotOverlay'],
  };
  const root = paths[layer];
  if (!root || !value || typeof value !== 'object') return false;
  return containsAssetKey(value[root[0]], assetKey, root);
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

function persistedAssetReference(clonedAsset) {
  return {
    assetId: clonedAsset.assetId,
    key: clonedAsset.assetKey,
    src: clonedAsset.path || clonedAsset.src,
    sha256: clonedAsset.sha256,
  };
}

/** Binds a newly cloned picker selection to the selected step's intended layer. */
function bindClonedAssetToStep(value, intent, clonedAsset) {
  assertJsonTree(value, 'step body');
  assertJsonTree(intent, 'asset binding intent');
  assertJsonTree(clonedAsset, 'cloned asset');
  const layer = intent && intent.layer;
  const boundAssetKey = intent && intent.boundAssetKey;
  if (boundAssetKey && containsAssetKey(value, boundAssetKey)) {
    return replaceStepAssetReference(value, boundAssetKey, clonedAsset);
  }
  const body = replaceStepAssetReference(value, '__no_existing_asset__', clonedAsset);
  if (layer === 'teachingObject') {
    return {
      ...body,
      teachingObject: {
        ...(body.teachingObject || {}),
        asset: persistedAssetReference(clonedAsset),
      },
    };
  }
  if (layer === 'backgroundScene') {
    return {
      ...body,
      backgroundScene: {
        ...(body.backgroundScene || {}),
        mode: 'poster',
        poster: {
          ...((body.backgroundScene && body.backgroundScene.poster) || {}),
          ...persistedAssetReference(clonedAsset),
        },
      },
    };
  }
  throw new TypeError(`unsupported asset binding layer: ${layer || 'unknown'}`);
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

function simulationPreviewIdentity(value) {
  const preview = value && value.preview;
  if (!value || typeof value.checksum !== 'string' || !value.checksum
    || typeof value.etag !== 'string' || !value.etag
    || !preview || preview.profile !== 'espTft'
    || Number(preview.width) !== 480 || Number(preview.height) !== 320) return null;
  return {
    checksum: value.checksum,
    etag: value.etag,
    profile: preview.profile,
    width: Number(preview.width),
    height: Number(preview.height),
  };
}

function sameSimulationPreviewIdentity(left, right) {
  return Boolean(left && right
    && left.checksum === right.checksum && left.etag === right.etag
    && left.profile === right.profile && left.width === right.width && left.height === right.height);
}

function validSimulationEvidence(result, expectedPreview, authoringSteps = []) {
  const expectedIdentity = simulationPreviewIdentity(expectedPreview);
  const resultIdentity = simulationPreviewIdentity(result);
  if (!sameSimulationPreviewIdentity(expectedIdentity, resultIdentity)) return false;
  const simulation = result && result.simulation;
  if (!simulation || typeof simulation !== 'object' || Array.isArray(simulation)
    || typeof simulation.terminated !== 'boolean'
    || !['lesson_completed', 'max_transitions'].includes(simulation.terminationReason)
    || !Array.isArray(simulation.trace)) return false;
  if (simulation.terminated !== (simulation.terminationReason === 'lesson_completed')) return false;
  if (simulation.terminationReason === 'max_transitions' && simulation.trace.length !== 100) return false;

  const attempts = new Map();
  const completedSteps = new Set();
  const manifestSteps = expectedPreview && expectedPreview.manifest && expectedPreview.manifest.steps;
  if (!Array.isArray(manifestSteps)) return false;
  let manifestCursor = 0;
  const maxAttemptsByStep = new Map((Array.isArray(authoringSteps) ? authoringSteps : []).map((step) => {
    const interaction = step && step.stepBody && step.stepBody.interaction;
    const requested = Number(interaction && interaction.maxAttempts);
    return [step && step.stepKey, Number.isInteger(requested) && requested > 0 ? requested : 3];
  }));
  let completionEvents = 0;
  for (let index = 0; index < simulation.trace.length; index += 1) {
    const event = simulation.trace[index];
    if (!event || typeof event !== 'object' || Array.isArray(event)
      || typeof event.stepKey !== 'string' || !event.stepKey) return false;
    const action = event.action;
    if (!['auto_advance', 'advance', 'retry', 'fallback_advance', 'lesson_completed'].includes(action)) return false;

    if (action === 'lesson_completed') {
      completionEvents += 1;
      if (event.stepKey !== 'lesson' || index !== simulation.trace.length - 1
        || manifestCursor !== manifestSteps.length
        || Object.prototype.hasOwnProperty.call(event, 'attempt')
        || Object.prototype.hasOwnProperty.call(event, 'outcome')) return false;
      continue;
    }
    const expectedStep = manifestSteps[manifestCursor];
    if (!expectedStep || event.stepKey !== expectedStep.id
      || event.stepType !== expectedStep.type
      || event.completionClass !== expectedStep.completionClass
      || !Number.isFinite(event.timeoutSec) || event.timeoutSec <= 0
      || Number(event.timeoutSec) !== Number(expectedStep.timeoutSec)) return false;

    if (Object.prototype.hasOwnProperty.call(event, 'outcome')
      && !['correct', 'near_miss', 'brave_try', 'incorrect', 'retry', 'timeout'].includes(event.outcome)) return false;
    if (action === 'auto_advance') {
      if (event.completionClass !== 'passive'
        || Object.prototype.hasOwnProperty.call(event, 'attempt')
        || Object.prototype.hasOwnProperty.call(event, 'outcome')) return false;
      manifestCursor += 1;
      continue;
    }
    if (event.completionClass !== 'interactive'
      || !Number.isInteger(event.attempt) || event.attempt < 1) return false;
    if (completedSteps.has(event.stepKey)) return false;
    const previousAttempt = attempts.get(event.stepKey) || 0;
    if (event.attempt !== previousAttempt + 1) return false;
    attempts.set(event.stepKey, event.attempt);
    const maxAttempts = maxAttemptsByStep.get(event.stepKey) || 3;
    const hasOutcome = Object.prototype.hasOwnProperty.call(event, 'outcome');
    const supportive = ['correct', 'near_miss', 'brave_try'].includes(event.outcome);
    const retryable = ['incorrect', 'retry'].includes(event.outcome);
    if (action === 'advance' && !supportive) return false;
    if (action === 'retry' && (!retryable || event.attempt >= maxAttempts)) return false;
    if (action === 'fallback_advance') {
      const validFallback = !hasOutcome || event.outcome === 'timeout'
        || (retryable && event.attempt >= maxAttempts);
      if (!validFallback) return false;
    }
    if (action === 'advance' || action === 'fallback_advance') completedSteps.add(event.stepKey);
    if (action === 'advance' || action === 'fallback_advance') manifestCursor += 1;
  }

  if (simulation.terminated) return completionEvents === 1;
  return completionEvents === 0;
}

module.exports = {
  bindClonedAssetToStep,
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
  sameSimulationPreviewIdentity,
  simulationPreviewIdentity,
  stepReferencesAssetInLayer,
  validSimulationEvidence,
};
