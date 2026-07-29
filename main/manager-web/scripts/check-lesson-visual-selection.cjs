const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.cwd();

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), 'utf8');
}

const {
  canonicalLessonVisualPair,
  buildLessonVisualRequest,
} = require('../src/components/lesson/lesson-visual-selection');

const firstStep = {
  visualRefs: [
    { slot: 'backgroundScene', assetVersionId: 'background-version-1', assetKey: 'background.forest' },
    { slot: 'teachingObject', assetVersionId: 'object-version-1', assetKey: 'object.apple' },
  ],
};
const laterStep = {
  visualRefs: [
    { slot: 'backgroundScene', assetVersionId: 'background-version-2', assetKey: 'background.space' },
    { slot: 'teachingObject', assetVersionId: 'object-version-2', assetKey: 'object.rocket' },
  ],
};

assert.deepEqual(canonicalLessonVisualPair([firstStep, laterStep]), {
  backgroundAssetVersionId: 'background-version-1',
  backgroundAssetKey: 'background.forest',
  objectAssetVersionId: 'object-version-1',
  objectAssetKey: 'object.apple',
});
assert.deepEqual(canonicalLessonVisualPair([{ visualRefs: [] }, laterStep]), {
  backgroundAssetVersionId: '',
  backgroundAssetKey: '',
  objectAssetVersionId: '',
  objectAssetKey: '',
});
assert.deepEqual(canonicalLessonVisualPair([]), {
  backgroundAssetVersionId: '',
  backgroundAssetKey: '',
  objectAssetVersionId: '',
  objectAssetKey: '',
});
assert.deepEqual(canonicalLessonVisualPair([{ visualRefs: [
  { slot: 'backgroundScene', asset_version_id: 'background-snake', asset_key: 'background.snake' },
  { slot: 'teachingObject', asset_version_id: 'object-snake', asset_key: 'object.snake' },
] }]), {
  backgroundAssetVersionId: 'background-snake',
  backgroundAssetKey: 'background.snake',
  objectAssetVersionId: 'object-snake',
  objectAssetKey: 'object.snake',
});

const current = {
  backgroundAssetVersionId: 'background-version-1',
  backgroundAssetKey: 'background.forest',
  objectAssetVersionId: 'object-version-1',
  objectAssetKey: 'object.apple',
};
const patch = { objectAssetVersionId: 'object-version-3', objectAssetKey: 'object.ball' };
const currentSnapshot = JSON.stringify(current);
const patchSnapshot = JSON.stringify(patch);
assert.deepEqual(buildLessonVisualRequest(current, patch), {
  backgroundAssetVersionId: 'background-version-1',
  objectAssetVersionId: 'object-version-3',
});
assert.equal(JSON.stringify(current), currentSnapshot);
assert.equal(JSON.stringify(patch), patchSnapshot);
assert.throws(
  () => buildLessonVisualRequest({ backgroundAssetVersionId: 'background-version-1' }, {}),
  /background and object asset version ids are required/i,
);
assert.throws(
  () => buildLessonVisualRequest(current, { objectAssetVersionId: '' }),
  /background and object asset version ids are required/i,
);

function loadLessonApi() {
  const calls = [];
  const source = read('src/apis/module/lesson.js')
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];\n/g, '')
    .replace(/export function /g, 'function ')
    .replace('export default {', 'const lessonApi = {');
  const lessonApi = vm.runInNewContext(`${source}\nlessonApi;`, {
    URLSearchParams,
    Array,
    Date,
    Error,
    JSON,
    Math,
    Number,
    Object,
    Promise,
    RegExp,
    Set,
    String,
    getNestUrl: () => '/nestjs/v1/admin',
    nestRequest: (request) => calls.push(request),
    nestUpload: () => {},
    normalizeLesson: (value) => value,
    normalizeStep: (value) => value,
    normalizeStepType: (value) => value,
  });
  return { lessonApi, calls };
}

const { lessonApi, calls } = loadLessonApi();
const data = {
  backgroundAssetVersionId: 'background-version-1',
  objectAssetVersionId: 'object-version-1',
};
const onSuccess = () => {};
const onError = () => {};
lessonApi.applyLessonVisuals('lesson-7', data, onSuccess, onError);
assert.equal(calls[0].method, 'PUT');
assert.equal(calls[0].url, '/nestjs/v1/admin/lessons/lesson-7/visuals');
assert.strictEqual(calls[0].data, data);
assert.strictEqual(calls[0].onSuccess, onSuccess);
assert.strictEqual(calls[0].onError, onError);

lessonApi.retrySdSync('lesson-7', onSuccess, onError);
assert.equal(calls[1].method, 'POST');
assert.equal(calls[1].url, '/nestjs/v1/admin/lessons/lesson-7/sd-sync/retry');
assert.deepEqual(JSON.parse(JSON.stringify(calls[1].data)), {});
assert.strictEqual(calls[1].onSuccess, onSuccess);
assert.strictEqual(calls[1].onError, onError);

console.log('lesson visual selection contract: OK');
