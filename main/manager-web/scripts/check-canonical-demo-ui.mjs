import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { loadCanonicalDemoContext } from '../src/utils/canonicalDemoContext.mjs';

const source = readFileSync(resolve(process.cwd(), 'src/views/LessonEditor.vue'), 'utf8');
const playwrightConfig = readFileSync(resolve(process.cwd(), 'playwright.config.js'), 'utf8');
const canonicalE2e = readFileSync(resolve(process.cwd(), 'e2e/lesson-studio/canonical-roundtrip.spec.js'), 'utf8');

assert.match(source, /data-testid="canonical-source-video"/);
assert.match(source, /<video[^>]+muted[^>]+controls[^>]+playsinline/);
assert.match(source, /canonicalDemo\.adminPreview\.url/);
assert.match(source, /data-testid="canonical-source-asset"/);
assert.match(source, /loadCanonicalDemoContext/);
assert.match(source, /demoSource/);
assert.match(source, /\$route\.query\.demoSource/);
assert.match(source, /canonicalDemoLoadSequence/);
assert.match(playwrightConfig, /LESSON_STUDIO_E2E_BROWSER_CHANNEL/);
assert.match(playwrightConfig, /channel:/);
assert.match(canonicalE2e, /source\.responseVisuals/);
assert.match(canonicalE2e, /Number\.isFinite\(video\.duration\)/);

const validManifest = {
  sourceFolder: 'robot/tvideo-raw-code',
  adminPreview: {
    mediaType: 'video/mp4',
    path: 'admin/demo.mp4',
    posterPath: 'admin/source/poster.png',
  },
  espTft: [{
    sourcePath: 'assets/object.png',
    sourceCopyPath: 'admin/source/object.png',
    sourceSha256: 'a'.repeat(64),
  }],
};
const loaded = await loadCanonicalDemoContext('tvideo-raw-code', async () => ({
  ok: true,
  json: async () => validManifest,
}));
assert.equal(loaded.adminPreview.url, '/tvideo-demo/admin/demo.mp4');
assert.equal(loaded.adminPreview.posterUrl, '/tvideo-demo/admin/source/poster.png');
assert.equal(loaded.sourceAssets[0].url, '/tvideo-demo/admin/source/object.png');

for (const manifest of [
  { ...validManifest, adminPreview: { ...validManifest.adminPreview, posterPath: '../poster.png' } },
  { ...validManifest, espTft: [{ ...validManifest.espTft[0], sourceCopyPath: '../object.png' }] },
]) {
  await assert.rejects(
    loadCanonicalDemoContext('tvideo-raw-code', async () => ({ ok: true, json: async () => manifest })),
    /invalid/,
  );
}

process.stdout.write('canonical source demo UI contract: ok\n');
