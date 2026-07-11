import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import {
  buildReplacementRequest,
  compareAssetVersions,
  filterVisualAssets,
  groupVisualAssets,
  lessonReplacementOptions,
  replacementSelectionIsValid,
  uniqueAffectedLessons,
  replacementNeedsImpact,
} from '../src/utils/lessonVisualLibraryState.mjs';

const root = process.cwd();
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8');

const rows = [
  { assetKey: 'apple', category: 'teachingObject', profile: 'espTft', version: 2, usageCount: 4, width: 160, height: 120, bytes: 1200, sha256: 'a'.repeat(64) },
  { assetKey: 'apple', category: 'teachingObject', profile: 'mobile', version: 1, usageCount: 2, width: 640, height: 480, bytes: 9200, sha256: 'b'.repeat(64) },
  { assetKey: 'park', category: 'scene', profile: 'espTft', version: 1, usageCount: 1 },
];

assert.deepEqual(filterVisualAssets(rows, { category: 'scene', profile: '' }), [rows[2]]);
assert.equal(groupVisualAssets(rows)[0].usageCount, 6, 'usage count is aggregated per asset key');
assert.equal(groupVisualAssets(rows)[0].pinnedVersion, 2, 'latest version is surfaced as pinned');
assert.deepEqual(compareAssetVersions(rows[1], rows[0]), {
  source: { width: 640, height: 480, bytes: 9200, shaPrefix: 'bbbbbbbb' },
  robot: { width: 160, height: 120, bytes: 1200, shaPrefix: 'aaaaaaaa' },
});

assert.equal(replacementNeedsImpact('global'), true);
assert.equal(replacementNeedsImpact('selectedLessons'), true);
assert.equal(replacementNeedsImpact('cloneForLesson'), false);
assert.deepEqual(buildReplacementRequest('source', 'target', 'cloneForLesson', ['lesson-1']), {
  sourceVersionId: 'source', targetVersionId: 'target', mode: 'cloneForLesson', lessonIds: ['lesson-1'],
});
assert.throws(() => buildReplacementRequest('source', 'source', 'global'), /different/);
assert.throws(() => buildReplacementRequest('source', 'target', 'cloneForLesson', ['one', 'two']), /exactly one/);

const usages = [
  { lessonId: 'draft-1', lessonKey: 'apple-draft', lessonVersion: 2, lessonStatus: 'draft', activeAssignmentCount: 0, stepKey: 'one' },
  { lessonId: 'draft-1', lessonKey: 'apple-draft', lessonVersion: 2, lessonStatus: 'draft', activeAssignmentCount: 0, stepKey: 'two' },
  { lessonId: 'published-1', lessonKey: 'apple-live', lessonVersion: 1, lessonStatus: 'published', activeAssignmentCount: 3, stepKey: 'one' },
];
assert.equal(uniqueAffectedLessons(usages).length, 2, 'affected lessons are de-duplicated across slots');
assert.deepEqual(lessonReplacementOptions(usages, 'selectedLessons').map((item) => item.lessonId), ['draft-1', 'published-1']);
assert.deepEqual(lessonReplacementOptions(usages, 'cloneForLesson').map((item) => item.lessonId), ['draft-1'], 'clone only offers current draft usages');
assert.equal(replacementSelectionIsValid(usages, 'cloneForLesson', ['draft-1']), true);
assert.equal(replacementSelectionIsValid(usages, 'cloneForLesson', ['published-1']), false, 'published lessons cannot be clone targets');

for (const file of [
  'src/views/LessonVisualLibrary.vue',
  'src/views/LessonVisualAssetDetail.vue',
  'src/components/lesson/AssetImpactDialog.vue',
]) assert.ok(fs.existsSync(path.join(root, file)), `${file} must exist`);

assert.match(read('src/apis/module/lesson.js'), /listVisualAssets/);
assert.match(read('src/apis/module/lesson.js'), /getVisualAssetDetail/);
assert.match(read('src/apis/module/lesson.js'), /sourceVersionId/);
assert.match(read('src/apis/module/lesson.js'), /visualReplacementImpact/);
assert.match(read('src/apis/module/lesson.js'), /replaceVisualAsset/);
assert.match(read('src/views/LessonVisualAssetDetail.vue'), /activeAssignments/);
assert.match(read('src/views/LessonVisualAssetDetail.vue'), /lessonStatus/);
assert.match(read('src/views/LessonVisualAssetDetail.vue'), /cloneForLesson/);
assert.match(read('src/components/lesson/AssetImpactDialog.vue'), /publishedVersions/);
assert.match(read('src/router/index.js'), /LessonVisualLibrary/);
assert.match(read('src/components/HeaderBar.vue'), /lessonVisualLibrary/);

console.log('lesson visual library UI contract OK');
