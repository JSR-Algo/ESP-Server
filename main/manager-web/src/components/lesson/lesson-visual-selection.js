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

module.exports = {
  canonicalLessonVisualPair,
  buildLessonVisualRequest,
  newestPublishedAssetVersions,
};
