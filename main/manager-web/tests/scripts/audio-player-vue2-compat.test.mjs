import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const componentUrl = new URL('../../src/components/AudioPlayer.vue', import.meta.url);

test('AudioPlayer uses the Vue 2 Options API without unavailable Composition API imports', async () => {
  const source = await readFile(componentUrl, 'utf8');

  assert.doesNotMatch(source, /<script\s+setup>/);
  assert.doesNotMatch(source, /v-bind\(/);
  assert.doesNotMatch(
    source,
    /import\s*\{[^}]*\b(?:ref|computed|onMounted|onUnmounted|nextTick)\b[^}]*\}\s*from\s*['"]vue['"]/s,
  );
  assert.match(source, /export\s+default\s*\{/);
  assert.match(source, /beforeDestroy\s*\(\)/);
});
