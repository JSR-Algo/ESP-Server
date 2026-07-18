const TEMPLATE_OPTIONS = Object.freeze(['none', 'tvideoFlyWalk']);
const LAYOUT_PRESETS = Object.freeze(['centerRoad', 'leftApproach', 'rightApproach']);

function sharedBackgroundOption(row) {
  const assetKey = String(row.asset_key || row.assetKey || '').trim();
  const version = Number(row.version);
  if (!assetKey || !Number.isInteger(version) || version < 1) throw new Error('invalid shared background version');
  return {
    assetKey: `${assetKey}@v${version}`,
    assetVersionId: row.version_id || row.versionId || '',
    name: `${row.title || assetKey} · v${version}`,
    layer: 'backgroundScene',
    compatibility: row.compatibility_metadata || row.compatibilityMetadata || null,
  };
}

function templateVisualRefTransition(previousBody, nextBody) {
  const previous = previousBody && previousBody.templateAuthoring;
  const next = nextBody && nextBody.templateAuthoring;
  const previousAssetVersionId = String((previous && previous.backgroundAssetVersionId) || '').trim() || null;
  const nextAssetVersionId = String((next && next.backgroundAssetVersionId) || '').trim() || null;
  return {
    shouldSync: previousAssetVersionId !== nextAssetVersionId,
    previousAssetVersionId,
    nextAssetVersionId,
  };
}

function mergeStepBodyForSave(previousBody, authoredBody) {
  const result = { ...(previousBody || {}), ...(authoredBody || {}) };
  if (!authoredBody || !authoredBody.templateAuthoring) delete result.templateAuthoring;
  return result;
}

function readinessVocabularySummary(entry) {
  const vocabulary = entry.vocabulary || entry.vocabularySummary;
  if (vocabulary) {
    const unique = Array.isArray(vocabulary.unique) ? vocabulary.unique.length : Number(vocabulary.uniqueCount || 0);
    const repeated = Array.isArray(vocabulary.repeated) ? vocabulary.repeated.length : Number(vocabulary.repeatedCount || 0);
    return `${unique} unique · ${repeated} repeated`;
  }
  const vocabularySetId = String(entry.vocabularySetId || entry.vocabulary_set_id || '').trim();
  const repeatedWords = Array.isArray(entry.repeatedWords)
    ? entry.repeatedWords
    : (Array.isArray(entry.repeated_words) ? entry.repeated_words : []);
  return `${vocabularySetId || 'Vocabulary set unavailable'} · ${repeatedWords.length} repeated`;
}

function compatibleLayouts(background) {
  if (!background || Number(background.geometryVersion) !== 1) return [];
  const supported = Array.isArray(background.supportedLayoutPresets) ? background.supportedLayoutPresets : [];
  return LAYOUT_PRESETS.filter((preset) => supported.includes(preset));
}

function buildTemplateAuthoring(input) {
  if (!input || input.templateId === 'none') return undefined;
  if (input.templateId !== 'tvideoFlyWalk') throw new Error('unsupported template');
  if (!LAYOUT_PRESETS.includes(input.layoutPreset)) throw new Error('unsupported layout preset');
  const result = {
    templateId: 'tvideoFlyWalk',
    layoutPreset: input.layoutPreset,
    backgroundVersionId: String(input.backgroundVersionId || '').trim(),
    arrivedPoseVersionId: String(input.arrivedPoseVersionId || '').trim(),
  };
  if (input.atlasVersionId) result.atlasVersionId = String(input.atlasVersionId).trim();
  if (input.vocabularySetId) result.vocabularySetId = String(input.vocabularySetId).trim();
  if (input.backgroundAssetVersionId) result.backgroundAssetVersionId = String(input.backgroundAssetVersionId).trim();
  if (['recall', 'spiralReview', 'assessment'].includes(input.duplicateReason)) result.duplicateReason = input.duplicateReason;
  if (!result.backgroundVersionId || !result.arrivedPoseVersionId) throw new Error('background and arrived pose are required');
  return result;
}

function assessVariantReadiness(variants, backgrounds) {
  const counts = {};
  const words = new Set();
  variants.forEach((variant) => { counts[variant.backgroundVersionId] = (counts[variant.backgroundVersionId] || 0) + 1; });
  return variants.map((variant) => {
    const repeatedWords = (variant.words || []).map((word) => String(word).trim().toUpperCase()).filter((word) => {
      const repeated = words.has(word);
      words.add(word);
      return repeated;
    });
    const layouts = compatibleLayouts(backgrounds[variant.backgroundVersionId]);
    const issues = [];
    if (!layouts.includes(variant.layoutPreset)) issues.push('layout-incompatible');
    if (repeatedWords.length && !variant.duplicateReason) issues.push('duplicate-vocabulary-unacknowledged');
    return {
      ...variant,
      repeatedWords,
      backgroundUsage: counts[variant.backgroundVersionId] > 1 ? 'shared' : 'unique',
      issues,
      ready: issues.length === 0,
    };
  });
}

function cleanWords(words) {
  const source = Array.isArray(words) ? words : String(words || '').split(/[\n,]/);
  return source.map((word) => String(word).trim()).filter(Boolean);
}

function buildVariantGenerationRequest(variants) {
  if (!Array.isArray(variants) || !variants.length) throw new Error('at least one variant is required');
  return {
    variants: variants.map((variant) => {
      const lessonKey = String(variant.lessonKey || '').trim();
      const vocabularySetId = String(variant.vocabularySetId || '').trim();
      const words = cleanWords(variant.words);
      if (!lessonKey || !vocabularySetId || !words.length) throw new Error('lesson key, vocabulary set, and words are required');
      const templateAuthoring = buildTemplateAuthoring({
        templateId: 'tvideoFlyWalk',
        layoutPreset: variant.layoutPreset,
        backgroundVersionId: variant.backgroundVersionId,
        arrivedPoseVersionId: variant.arrivedPoseVersionId,
        atlasVersionId: variant.atlasVersionId,
        backgroundAssetVersionId: variant.backgroundAssetVersionId,
      });
      const result = { lessonKey, vocabularySetId, words, templateAuthoring };
      if (String(variant.title || '').trim()) result.title = String(variant.title).trim();
      if (['recall', 'spiralReview', 'assessment'].includes(variant.duplicateReason)) {
        result.duplicateReason = variant.duplicateReason;
      }
      return result;
    }),
  };
}

function normalizeBatchReadiness(entries) {
  const source = Array.isArray(entries) ? entries : (entries && Array.isArray(entries.lessons) ? entries.lessons : []);
  const lessons = source.map((entry) => ({
    ...entry,
    lessonId: String(entry.lessonId || entry.lesson_id || ''),
    issues: Array.isArray(entry.issues) ? entry.issues : [],
    ready: entry.ready === true,
  }));
  const derivedReadyIds = lessons.filter((entry) => entry.ready && entry.lessonId).map((entry) => entry.lessonId);
  const requestedReadyIds = entries && !Array.isArray(entries) && Array.isArray(entries.readyLessonIds)
    ? entries.readyLessonIds.map(String)
    : derivedReadyIds;
  const readySet = new Set(derivedReadyIds);
  const readyLessonIds = requestedReadyIds.filter((lessonId) => readySet.has(lessonId));
  return {
    lessons,
    readyLessonIds,
    readyCount: readyLessonIds.length,
    blockedCount: lessons.length - readyLessonIds.length,
  };
}

module.exports = {
  TEMPLATE_OPTIONS,
  LAYOUT_PRESETS,
  sharedBackgroundOption,
  templateVisualRefTransition,
  mergeStepBodyForSave,
  readinessVocabularySummary,
  compatibleLayouts,
  buildTemplateAuthoring,
  assessVariantReadiness,
  buildVariantGenerationRequest,
  normalizeBatchReadiness,
};
