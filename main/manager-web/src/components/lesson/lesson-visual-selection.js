function emptyLessonVisualPair() {
  return {
    backgroundAssetVersionId: '',
    backgroundAssetKey: '',
    objectAssetVersionId: '',
    objectAssetKey: '',
  };
}

function visualRefValue(reference, camelKey, snakeKey) {
  if (!reference || typeof reference !== 'object') return '';
  return reference[camelKey] || reference[snakeKey] || '';
}

function canonicalLessonVisualPair(steps) {
  const pair = emptyLessonVisualPair();
  if (!Array.isArray(steps) || !steps.length) return pair;

  const references = Array.isArray(steps[0] && steps[0].visualRefs)
    ? steps[0].visualRefs
    : [];
  const background = references.find((reference) => reference && reference.slot === 'backgroundScene');
  const object = references.find((reference) => reference && reference.slot === 'teachingObject');

  return {
    backgroundAssetVersionId: visualRefValue(background, 'assetVersionId', 'asset_version_id'),
    backgroundAssetKey: visualRefValue(background, 'assetKey', 'asset_key'),
    objectAssetVersionId: visualRefValue(object, 'assetVersionId', 'asset_version_id'),
    objectAssetKey: visualRefValue(object, 'assetKey', 'asset_key'),
  };
}

function buildLessonVisualRequest(current, patch) {
  const merged = { ...(current || {}), ...(patch || {}) };
  const { backgroundAssetVersionId, objectAssetVersionId } = merged;
  if (![backgroundAssetVersionId, objectAssetVersionId]
    .every((value) => typeof value === 'string' && value.trim())) {
    throw new Error('Background and object asset version ids are required.');
  }
  return { backgroundAssetVersionId, objectAssetVersionId };
}

function newestPublishedAssetVersions(rows) {
  const selected = {};
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const assetKey = String((row && (row.assetKey || row.asset_key)) || '').trim();
    const versionId = String((row && (row.versionId || row.version_id)) || '').trim();
    const publicationState = row && (row.publicationState || row.publication_state);
    const version = Number(row && row.version);
    if (!assetKey || !versionId || publicationState !== 'published'
      || !Number.isInteger(version) || version < 1) return;
    if (!selected[assetKey] || version > selected[assetKey].version) {
      selected[assetKey] = { versionId, version };
    }
  });
  return selected;
}

const COURSE_MODE_PHASES = Object.freeze([
  { id: 'flyIn', label: 'Fly in' }, { id: 'walk', label: 'Walk' },
  { id: 'teach', label: 'Talk' }, { id: 'listen', label: 'Listen' },
  { id: 'thinking', label: 'Thinking' }, { id: 'celebrate', label: 'Celebrate' }, { id: 'exit', label: 'Exit' },
]);
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const SHA = /^[a-f0-9]{64}$/;

function courseModeVisualSelection(snapshot) {
  if (!snapshot || !SHA.test(snapshot.checksum) || !SHA.test(snapshot.visualChecksum)
    || !Array.isArray(snapshot.refs) || !Array.isArray(snapshot.cinematicPhases)) {
    throw new Error('Incomplete visual snapshot. Read the saved version again.');
  }
  const refs = snapshot.refs;
  if (refs.some(ref => !ref || typeof ref.stepKey !== 'string' || !ref.stepKey
    || typeof ref.slot !== 'string' || !UUID.test(ref.assetVersionId))) throw new Error('Invalid persisted visual reference.');
  const first = slot => (refs.find(ref => ref.slot === slot) || {}).assetVersionId || '';
  const robotAssetVersionIds = {};
  COURSE_MODE_PHASES.forEach(({id}) => {
    const ids = new Set(refs.filter(ref => ref.slot === `robotOverlay.${id}`).map(ref => ref.assetVersionId));
    if (ids.size > 1) throw new Error(`Inconsistent saved ${id} pins. Restore the saved bindings.`);
    robotAssetVersionIds[id] = [...ids][0] || '';
  });
  return { backgroundAssetVersionId: first('backgroundScene'), objectAssetVersionId: first('teachingObject'), robotAssetVersionIds };
}

function buildCourseModeVisualRequest(snapshot, selection) {
  courseModeVisualSelection(snapshot);
  if (!UUID.test(selection.backgroundAssetVersionId)) throw new Error('Select a background image.');
  const robotAssetVersionIds = {};
  COURSE_MODE_PHASES.forEach(({id, label}) => {
    const versionId = selection.robotAssetVersionIds && selection.robotAssetVersionIds[id];
    if (!UUID.test(versionId)) throw new Error(`Select a ${label} character clip.`);
    robotAssetVersionIds[id] = versionId;
  });
  const ids = [selection.backgroundAssetVersionId, ...Object.values(robotAssetVersionIds)];
  if (selection.objectAssetVersionId) {
    if (!UUID.test(selection.objectAssetVersionId)) throw new Error('Select a valid object version.');
    ids.push(selection.objectAssetVersionId);
  }
  if (new Set(ids).size !== ids.length) throw new Error('Each layer and character phase requires a distinct version.');
  return { expectedChecksum: snapshot.checksum, expectedVisualChecksum: snapshot.visualChecksum,
    backgroundAssetVersionId: selection.backgroundAssetVersionId,
    ...(selection.objectAssetVersionId ? { objectAssetVersionId: selection.objectAssetVersionId } : {}), robotAssetVersionIds };
}

function courseModeAssetRejection(asset, slot, phase) {
  const a = asset || {}, m = a.compatibilityMetadata || {};
  const expected = { backgroundScene: ['scene', 'image/jpeg'], teachingObject: ['teachingObject', 'image/png'], robotOverlay: ['robotPose', 'video/mp4'] }[slot];
  if (!expected || a.category !== expected[0]) return 'Incompatible visual slot.';
  if (a.profile !== 'espTft') return 'Requires the espTft profile.';
  if (a.publicationState !== 'published') return `Version is ${a.publicationState || 'unknown'}; select a published version.`;
  if (!UUID.test(a.versionId) || !SHA.test(a.sha256) || !Number.isSafeInteger(a.bytes) || a.bytes <= 0) return 'Missing immutable version, checksum or byte size.';
  if (a.mimeType !== expected[1]) return slot === 'robotOverlay' ? 'Requires silent MJPEG MP4 character video.' : `Requires ${expected[1]} image.`;
  if (!Number.isSafeInteger(a.width) || a.width <= 0 || !Number.isSafeInteger(a.height) || a.height <= 0
    || m.width !== a.width || m.height !== a.height || m.mediaType !== a.mimeType) return 'Invalid dimensions or media metadata.';
  const rectEquals = rect => m.rect && Object.entries(rect).every(([key,value]) => m.rect[key] === value);
  if (slot !== 'robotOverlay') {
    if (m.mediaKind !== 'image') return 'Requires static image metadata.';
    if (slot === 'backgroundScene' && (a.width !== 480 || a.height !== 320 || m.fit !== 'cover'
      || !rectEquals({x:0,y:0,width:480,height:320}))) return 'Background must be a static 480 x 320 JPEG.';
    if (slot === 'teachingObject' && (m.fit !== 'contain' || !rectEquals({x:20,y:168,width:95,height:95}))) return 'Object must be a PNG with the teaching-object placement.';
    return '';
  }
  if (m.mediaKind !== 'video' || m.codec !== 'mjpeg') return 'Requires MJPEG codec.';
  if (m.hasAudio !== false) return 'Character video must be silent.';
  if (![10,15].includes(m.fps) || !Number.isSafeInteger(m.durationMs) || m.durationMs <= 0
    || !Number.isSafeInteger(m.frameCount) || m.frameCount <= 0 || m.durationMs * m.fps !== m.frameCount * 1000) return 'Invalid frame timing; use 10 or 15 fps with matching duration/frame count.';
  const c = m.chromaKey || {};
  if (c.keyColor !== '#00ff00' || !Number.isInteger(c.tolerance) || c.tolerance < 0 || c.tolerance > 255
    || !Number.isInteger(c.featherPx) || c.featherPx < 0 || c.featherPx > 4) return 'Invalid green chroma key, tolerance or feather.';
  const rect = phase === 'flyIn' ? {x:240,y:0,width:240,height:240}
    : phase === 'walk' ? {x:73,y:80,width:240,height:240}
      : phase === 'celebrate' && rectEquals({x:118,y:70,width:150,height:240}) ? {x:118,y:70,width:150,height:240}
        : {x:118,y:160,width:150,height:150};
  if (!rectEquals(rect) || a.width !== rect.width || a.height !== rect.height) return 'Clip dimensions and baked placement do not match this character phase.';
  return '';
}

module.exports = {
  canonicalLessonVisualPair,
  buildLessonVisualRequest,
  newestPublishedAssetVersions,
  COURSE_MODE_PHASES,
  courseModeVisualSelection,
  buildCourseModeVisualRequest,
  courseModeAssetRejection,
};
