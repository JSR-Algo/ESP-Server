import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const source = await readFile(new URL('src/components/lesson/flattened-cinematic-preview.js', root), 'utf8');
const preview = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const componentSource = await readFile(new URL('src/components/lesson/FlattenedCinematicPreview.vue', root), 'utf8');

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
