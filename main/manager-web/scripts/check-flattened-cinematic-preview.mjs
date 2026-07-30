import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const source = await readFile(new URL('src/components/lesson/flattened-cinematic-preview.js', root), 'utf8');
const preview = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const componentSource = await readFile(new URL('src/components/lesson/FlattenedCinematicPreview.vue', root), 'utf8');
const videoLayerSource = await readFile(new URL('src/components/lesson/CinematicVideoLayer.vue', root), 'utf8');

for (const token of [
  'data-testid="flattened-cinematic-preview"',
  'width="480"',
  'height="320"',
  'crossorigin="anonymous"',
  'requestAnimationFrame',
  'cancelAnimationFrame',
  'getImageData',
  'applyChromaKey',
  'flattened-preview__error'
]) {
  assert.ok(componentSource.includes(token), `FlattenedCinematicPreview.vue must include ${token}`);
}

assert.ok(componentSource.includes(':data-layer-id="layer.id"'), 'source videos must carry their projection layer id');
assert.match(componentSource, /videoByLayerId\(layerId\)[\s\S]*dataset\.layerId === layerId/, 'video lookup must match data-layer-id');
assert.doesNotMatch(componentSource, /videos\s*\[\s*index\s*\]/, 'rendering must not depend on Vue ref array order');

assert.ok(componentSource.includes('playPendingByLayer'), 'play promises must be tracked per layer');
assert.ok(componentSource.includes('playBlockedByLayer'), 'rejected play attempts must be blocked per layer');
assert.match(
  componentSource,
  /if \(this\.playPendingByLayer\[layerId\] \|\| this\.playBlockedByLayer\[layerId\]\) return;/,
  'pending or rejected play attempts must not retry every animation frame'
);
assert.match(componentSource, /resetPlaybackGuards\(\)[\s\S]*playGeneration/, 'play guards must be invalidated during lifecycle resets');

assert.ok(componentSource.includes('videoFrameSignature'), 'rendering must identify decoded source frames');
assert.ok(componentSource.includes('lastRenderSignature'), 'rendering must cache its last source-frame signature');
assert.match(
  componentSource,
  /if \(!this\.forceRender && signature === this\.lastRenderSignature\) return;/,
  'duplicate source frames must skip canvas compositing'
);
assert.match(componentSource, /invalidateFrameCache\(\)[\s\S]*lastRenderSignature/, 'source and replay changes must invalidate frame caches');
assert.match(componentSource, /stopRendering\(\)[\s\S]*cancelAnimationFrame[\s\S]*pause/, 'cleanup must cancel RAF and pause source videos');

assert.deepEqual(preview.CINEMATIC_LAYER_IDS, ['background', 'teachingObject', 'robotOverlay']);
assert.equal(preview.CINEMATIC_SYNC_TOLERANCE_SEC, 0.08);

const projection = {
  manifestVersion: 'teebot-lesson-renderer.v3',
  layers: [
    { id: 'prompt', z: 30, visible: true, mediaType: '', src: '', text: 'APPLE' },
    { id: 'robotOverlay', z: 20, visible: true, mediaType: 'video/mp4', src: 'robot.mp4' },
    { id: 'background', z: 0, visible: true, mediaType: 'video/mp4', src: 'background.mp4' },
    { id: 'teachingObject', z: 10, visible: true, mediaType: 'video/mp4', src: 'object.mp4' }
  ]
};

const layers = preview.flattenableLayers(projection);
assert.deepEqual(layers.map((layer) => layer.id), ['background', 'teachingObject', 'robotOverlay']);
assert.deepEqual(layers.map((layer) => layer.z), [0, 10, 20]);
assert.deepEqual(preview.flattenableLayers({ layers: [
  { id: 'background', z: 0, visible: true, mediaType: 'video/mp4', src: 'background.mp4' },
  { id: 'teachingObject', z: 10, visible: false, mediaType: 'video/mp4', src: 'hidden.mp4' },
  { id: 'robotOverlay', z: 20, visible: true, mediaType: 'image/png', src: 'robot.png' },
  { id: 'robotOverlay', z: 21, visible: true, mediaType: 'video/mp4', src: '' },
  { id: 'prompt', z: 30, visible: true, mediaType: 'video/mp4', src: 'prompt.mp4' }
]}).map((layer) => layer.id), ['background']);
assert.equal(preview.isFlattenableProjection(projection), true);
assert.equal(preview.isFlattenableProjection({ ...projection, manifestVersion: 'teebot-lesson-renderer.v2' }), false);
assert.equal(preview.isFlattenableProjection({ ...projection, layers: projection.layers.slice(0, 3) }), false);

assert.equal(preview.chooseMasterLayer(layers).id, 'background');
assert.equal(preview.chooseMasterLayer(layers.slice(1)).id, 'teachingObject');
assert.equal(preview.chooseMasterLayer([]), null);

assert.equal(preview.shouldResyncVideo(1, 1.079), false);
assert.equal(preview.shouldResyncVideo(1, 1.081), true);
assert.equal(preview.shouldResyncVideo(Number.NaN, 1.2), false);

for (const propContract of [
  /controlled:\s*\{\s*type:\s*Boolean,\s*default:\s*false\s*\}/,
  /playing:\s*\{\s*type:\s*Boolean,\s*default:\s*true\s*\}/,
  /clockMs:\s*\{\s*type:\s*Number,\s*default:\s*0\s*\}/,
  /replayNonce:\s*\{\s*type:\s*Number,\s*default:\s*0\s*\}/
]) {
  assert.match(videoLayerSource, propContract, 'CinematicVideoLayer must expose the controlled playback props');
}
assert.match(
  videoLayerSource,
  /import\s*\{\s*shouldResyncVideo\s*\}\s*from\s*['"]\.\/flattened-cinematic-preview['"]/,
  'CinematicVideoLayer must share the 80 ms synchronization helper'
);
assert.ok(videoLayerSource.includes('syncPlayback('), 'CinematicVideoLayer must provide syncPlayback');
assert.match(videoLayerSource, /:autoplay="!controlled"/, 'legacy mode must retain autoplay while controlled mode disables it');
assert.match(videoLayerSource, /:loop="!controlled"/, 'legacy mode must retain looping while controlled mode disables it');
assert.match(
  videoLayerSource,
  /syncPlayback\([^)]*\)\s*\{[\s\S]*if \(!this\.controlled/,
  'external playback synchronization must be gated behind controlled mode'
);
assert.match(
  videoLayerSource,
  /shouldResyncVideo\(targetSeconds, video\.currentTime\)/,
  'controlled playback must only seek when drift exceeds the shared tolerance'
);
assert.match(
  videoLayerSource,
  /const targetSeconds = Math\.max\(0, Number\(this\.clockMs\) \|\| 0\) \/ 1000;/,
  'controlled playback must convert its millisecond clock to video seconds'
);
assert.match(videoLayerSource, /playPending/, 'controlled playback must guard pending play promises');
assert.match(videoLayerSource, /playBlocked/, 'controlled playback must block repeated rejected play promises');

assert.deepEqual(
  preview.objectFitRect(640, 480, { x: 0, y: 0, width: 480, height: 320, fit: 'fill' }),
  { sx: 0, sy: 0, sw: 640, sh: 480, dx: 0, dy: 0, dw: 480, dh: 320 }
);
assert.deepEqual(
  preview.objectFitRect(640, 480, { x: 10, y: 20, width: 480, height: 320, fit: 'contain' }),
  { sx: 0, sy: 0, sw: 640, sh: 480, dx: 36.666666666666686, dy: 20, dw: 426.66666666666663, dh: 320 }
);
const cover = preview.objectFitRect(640, 480, { x: 0, y: 0, width: 480, height: 320, fit: 'cover' });
assert.deepEqual(
  { sx: cover.sx, sw: cover.sw, dx: cover.dx, dy: cover.dy, dw: cover.dw, dh: cover.dh },
  { sx: 0, sw: 640, dx: 0, dy: 0, dw: 480, dh: 320 }
);
assert.ok(Math.abs(cover.sy - (80 / 3)) < Number.EPSILON * 128);
assert.ok(Math.abs(cover.sh - (1280 / 3)) < Number.EPSILON * 2048);

const pixels = new Uint8ClampedArray([
  250, 250, 250, 255,
  240, 240, 240, 255,
  0, 0, 0, 128
]);
const keyed = preview.applyChromaKey(pixels, {
  color: { r: 255, g: 255, b: 255 },
  tolerance: 5,
  feather: 20
});
assert.equal(keyed[3], 0);
assert.ok(keyed[7] > 0 && keyed[7] < 255);
assert.equal(keyed[11], 128);

const unchanged = new Uint8ClampedArray([10, 20, 30, 40]);
assert.equal(preview.applyChromaKey(unchanged, null), unchanged);

console.log('flattened cinematic preview helpers PASS');
