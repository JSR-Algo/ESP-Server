export const VISUAL_CATEGORIES = ['scene', 'teachingObject', 'robotPose', 'correctFeedback', 'nearMissFeedback', 'incorrectFeedback', 'lessonEnding', 'pet', 'effect', 'uiDecoration'];
export const VISUAL_PROFILES = ['espTft', 'piTft', 'mobile'];
export const REPLACEMENT_MODES = ['global', 'selectedLessons', 'cloneForLesson'];

export function filterVisualAssets(rows, filters = {}) {
  return (rows || []).filter((row) =>
    (!filters.category || row.category === filters.category) &&
    (!filters.profile || row.profile === filters.profile) &&
    (!filters.keyword || `${row.assetKey} ${row.title}`.toLowerCase().includes(filters.keyword.toLowerCase()))
  );
}

export function groupVisualAssets(rows) {
  const groups = new Map();
  (rows || []).forEach((row) => {
    const group = groups.get(row.assetKey) || { ...row, versions: [], usageCount: 0, pinnedVersion: 0 };
    group.versions.push(row);
    group.usageCount += Number(row.usageCount || 0);
    group.pinnedVersion = Math.max(group.pinnedVersion, Number(row.version || 0));
    groups.set(row.assetKey, group);
  });
  return [...groups.values()].sort((a, b) => a.assetKey.localeCompare(b.assetKey));
}

export function compareAssetVersions(source, robot) {
  const project = (row = {}) => ({
    width: Number(row.width || 0), height: Number(row.height || 0), bytes: Number(row.bytes || 0), shaPrefix: String(row.sha256 || '').slice(0, 8),
  });
  return { source: project(source), robot: project(robot) };
}

export function replacementNeedsImpact(mode) {
  return mode === 'global' || mode === 'selectedLessons';
}

export function buildReplacementRequest(sourceVersionId, targetVersionId, mode, lessonIds = []) {
  if (!REPLACEMENT_MODES.includes(mode)) throw new Error('invalid replacement mode');
  if (!sourceVersionId || !targetVersionId || (mode !== 'cloneForLesson' && sourceVersionId === targetVersionId)) throw new Error('source and target versions must be different');
  const ids = [...new Set((lessonIds || []).filter(Boolean))];
  if (mode === 'selectedLessons' && !ids.length) throw new Error('selected replacement requires lessons');
  if (mode === 'cloneForLesson' && ids.length !== 1) throw new Error('clone-for-current-lesson requires exactly one lesson');
  return { sourceVersionId, targetVersionId, mode, lessonIds: mode === 'global' ? [] : ids };
}

export function uniqueAffectedLessons(usages = []) {
  const lessons = new Map();
  usages.forEach((usage) => {
    if (!lessons.has(usage.lessonId)) lessons.set(usage.lessonId, usage);
  });
  return [...lessons.values()];
}

export function lessonReplacementOptions(usages, mode) {
  const lessons = uniqueAffectedLessons(usages);
  return mode === 'cloneForLesson' ? lessons.filter((usage) => usage.lessonStatus === 'draft') : lessons;
}

export function replacementSelectionIsValid(usages, mode, lessonIds = []) {
  if (mode === 'global') return true;
  const selected = new Set((lessonIds || []).filter(Boolean));
  const allowed = lessonReplacementOptions(usages, mode);
  if (mode === 'cloneForLesson') return selected.size === 1 && allowed.some((usage) => selected.has(usage.lessonId));
  return selected.size > 0 && [...selected].every((id) => allowed.some((usage) => usage.lessonId === id));
}
