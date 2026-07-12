import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  loadLessonRolloutCapabilitiesWith,
  normalizeLessonRolloutCapabilities,
} from '../src/utils/lessonRolloutCapabilitiesCore.mjs';

const root = new URL('../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');

const [api, client, core, router, header, editor] = await Promise.all([
  read('src/apis/module/lesson.js'),
  read('src/utils/lessonRolloutCapabilities.js'),
  read('src/utils/lessonRolloutCapabilitiesCore.mjs'),
  read('src/router/index.js'),
  read('src/components/HeaderBar.vue'),
  read('src/views/LessonEditor.vue'),
]);

assert.match(api, /lesson-rollout-capabilities/);
assert.match(core, /sharedVisualAuthoring:\s*false/);
assert.match(core, /exactEspTftPreview:\s*false/);
assert.match(client, /loadLessonRolloutCapabilitiesWith\(Api\.lesson\)/);

assert.deepEqual(normalizeLessonRolloutCapabilities({ sharedVisualAuthoring: true, exactEspTftPreview: 'true' }), {
  sharedVisualAuthoring: true,
  exactEspTftPreview: false,
});
assert.deepEqual(await loadLessonRolloutCapabilitiesWith({ getRolloutCapabilities(ok) { ok({ sharedVisualAuthoring: true, exactEspTftPreview: true }); } }), {
  sharedVisualAuthoring: true,
  exactEspTftPreview: true,
});
for (const apiClient of [
  { getRolloutCapabilities(ok) { ok(null); } },
  { getRolloutCapabilities(ok, fail) { fail('network'); } },
  { getRolloutCapabilities() { throw new Error('broken client'); } },
]) {
  assert.deepEqual(await loadLessonRolloutCapabilitiesWith(apiClient), {
    sharedVisualAuthoring: false,
    exactEspTftPreview: false,
  });
}
assert.deepEqual(await Promise.race([
  loadLessonRolloutCapabilitiesWith({ getRolloutCapabilities() {} }, 0),
  new Promise((resolve) => setTimeout(() => resolve('client did not fail closed'), 50)),
]), {
  sharedVisualAuthoring: false,
  exactEspTftPreview: false,
});

assert.match(router, /requiredLessonCapability:\s*'sharedVisualAuthoring'/);
assert.match(router, /loadLessonRolloutCapabilities/);
assert.match(router, /capabilities\[requiredCapability\]/);

assert.match(header, /lessonCapabilities\.sharedVisualAuthoring/);
assert.match(header, /loadLessonRolloutCapabilities/);

assert.match(editor, /lessonCapabilities\.sharedVisualAuthoring/);
assert.match(editor, /lessonCapabilities\.exactEspTftPreview/);
assert.match(editor, /if \(!this\.lessonCapabilities\.exactEspTftPreview\) return/);
assert.match(editor, /if \(!this\.lessonCapabilities\.sharedVisualAuthoring\) return/);
assert.match(editor, /if \(this\.lessonCapabilities\.sharedVisualAuthoring\) this\.fetchSharedVisualAssets\(\)/);
assert.doesNotMatch(editor.match(/fetchAll\(\) \{[\s\S]*?\n    \},\n    fetchSharedVisualAssets/)?.[0] || '', /listVisualAssets/);

console.log('lesson rollout capabilities fail-closed API, route, nav, and editor gates PASS');
