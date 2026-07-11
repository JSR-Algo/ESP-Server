import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import {
  buildReplacementRequest,
  compareAssetVersions,
  filterVisualAssets,
  groupVisualAssets,
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
assert.throws(() => buildReplacementRequest('source', 'target', 'cloneForLesson', ['one', 'two']), /exactly one/);

for (const file of [
  'src/views/LessonVisualLibrary.vue',
  'src/views/LessonVisualAssetDetail.vue',
  'src/components/lesson/AssetImpactDialog.vue',
]) assert.ok(fs.existsSync(path.join(root, file)), `${file} must exist`);

assert.match(read('src/apis/module/lesson.js'), /listVisualAssets/);
assert.match(read('src/apis/module/lesson.js'), /getVisualAssetDetail/);
assert.match(read('src/apis/module/lesson.js'), /visualReplacementImpact/);
assert.match(read('src/apis/module/lesson.js'), /replaceVisualAsset/);
assert.match(read('src/views/LessonVisualAssetDetail.vue'), /activeAssignments/);
assert.match(read('src/views/LessonVisualAssetDetail.vue'), /cloneForLesson/);
assert.match(read('src/components/lesson/AssetImpactDialog.vue'), /publishedVersions/);
assert.match(read('src/router/index.js'), /LessonVisualLibrary/);
assert.match(read('src/components/HeaderBar.vue'), /lessonVisualLibrary/);

console.log('lesson visual library UI contract OK');
