import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const source = await readFile(new URL('src/components/lesson/flattened-cinematic-preview.js', root), 'utf8');
const preview = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const componentSource = await readFile(new URL('src/components/lesson/FlattenedCinematicPreview.vue', root), 'utf8');
const videoLayerSource = await readFile(new URL('src/components/lesson/CinematicVideoLayer.vue', root), 'utf8');
const exactPreviewSource = await readFile(new URL('src/components/lesson/RobotEspTftProjectionPreview.vue', root), 'utf8');
const helperModuleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
const videoLayerScript = videoLayerSource.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace("'./flattened-cinematic-preview'", JSON.stringify(helperModuleUrl));
const videoLayer = (await import(`data:text/javascript;base64,${Buffer.from(videoLayerScript).toString('base64')}`)).default;
const emptyComponentUrl = `data:text/javascript;base64,${Buffer.from('export default {};').toString('base64')}`;
const projectionStubUrl = `data:text/javascript;base64,${Buffer.from(`
  export const RESPONSE_PATHS = ['correct'];
  export const VISUAL_STATES = [];
  export const DEGRADED_REASONS = [];
  export function projectEspTftPreview() { return {}; }
`).toString('base64')}`;
const exactPreviewScript = exactPreviewSource.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/import \{[\s\S]*?\} from '\.\/robot-preview-projection';/, `import { projectEspTftPreview, RESPONSE_PATHS, VISUAL_STATES, DEGRADED_REASONS } from ${JSON.stringify(projectionStubUrl)};`)
  .replace("'./CinematicVideoLayer.vue'", JSON.stringify(emptyComponentUrl))
  .replace("'./FlattenedCinematicPreview.vue'", JSON.stringify(emptyComponentUrl))
  .replace("'./flattened-cinematic-preview'", JSON.stringify(helperModuleUrl));
const exactPreview = (await import(`data:text/javascript;base64,${Buffer.from(exactPreviewScript).toString('base64')}`)).default;

assert.equal(exactPreview.computed.cinematicDurationMs.call({ manifest: {
  cinematicPhases: [{
    templateId: 'directMp4Cinematic',
    layers: [{ slot: 'backgroundScene', metadata: { durationMs: 4321 } }]
  }]
} }), 4321, 'shared clock duration must come from the current cinematic phase metadata');
assert.equal(exactPreview.computed.cinematicDurationMs.call({ manifest: {} }), 10000, 'missing duration metadata must use the safe preview fallback');

for (const token of [
  "import FlattenedCinematicPreview from './FlattenedCinematicPreview.vue';",
  'isFlattenableProjection',
  "cinematicComparisonEnabled ? 'cinematic-preview-comparison' : null",
  '3 Layers',
  'Robot Flattened',
  ':projection="projection"',
  ':clock-ms="cinematicClockMs"',
  ':replay-nonce="cinematicReplayNonce"',
  ':controlled="cinematicComparisonEnabled"'
]) {
  assert.ok(exactPreviewSource.includes(token), `RobotEspTftProjectionPreview.vue must include ${token}`);
}
assert.ok(exactPreviewSource.includes('data-testid="esp-tft-stage"'), 'legacy exact stage must remain');
assert.match(exactPreviewSource, /:data-testid="cinematicComparisonEnabled \? 'cinematic-preview-comparison' : null"/, 'comparison test id must only render for a flattenable projection');
assert.match(exactPreviewSource, /@click="toggleCinematicPlayback"/, 'comparison must expose a play/pause control');
assert.match(exactPreviewSource, /@click="replayCinematic"/, 'comparison must expose a replay control');
assert.doesNotMatch(exactPreviewSource, /<output>[\s\S]*cinematicClockMs[\s\S]*<\/output>/, 'RAF clock must not announce every frame through an output element');
assert.match(exactPreviewSource, /<span aria-hidden="true">\{\{ Math\.round\(cinematicClockMs\) \}\}ms \/ \{\{ cinematicDurationMs \}\}ms<\/span>/, 'visual cinematic timer must be hidden from assistive technology');
assert.match(exactPreviewSource, /beforeDestroy\(\)[\s\S]*stopCinematicClock/, 'destroy must cancel the cinematic clock');
assert.match(exactPreviewSource, /projection\(\)[\s\S]*resetCinematicPlayback/, 'projection changes must reset and cancel cinematic playback');

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

const rafCallbacks = new Map();
const cancelledFrames = [];
let nextFrameHandle = 1;
globalThis.requestAnimationFrame = (callback) => {
  const handle = nextFrameHandle;
  nextFrameHandle += 1;
  rafCallbacks.set(handle, callback);
  return handle;
};
globalThis.cancelAnimationFrame = (handle) => {
  cancelledFrames.push(handle);
  rafCallbacks.delete(handle);
};

function createExactPreviewClock({ playing = false, clockMs = 0, durationMs = 1000 } = {}) {
  return {
    cinematicPlaying: playing,
    cinematicClockMs: clockMs,
    cinematicReplayNonce: 0,
    cinematicFrameHandle: null,
    cinematicStartedAt: null,
    cinematicComparisonEnabled: true,
    cinematicDurationMs: durationMs,
    ...exactPreview.methods
  };
}

rafCallbacks.clear();
cancelledFrames.length = 0;
const exactClock = createExactPreviewClock({ playing: true, clockMs: 250, durationMs: 1000 });
exactClock.scheduleCinematicFrame();
exactClock.scheduleCinematicFrame();
assert.equal(rafCallbacks.size, 1, 'shared clock must never schedule duplicate RAF loops');
const firstClockFrame = [...rafCallbacks.entries()][0];
rafCallbacks.delete(firstClockFrame[0]);
firstClockFrame[1](1000);
assert.equal(exactClock.cinematicClockMs, 250, 'first RAF must preserve the existing clock when anchoring');
const secondClockFrame = [...rafCallbacks.entries()][0];
rafCallbacks.delete(secondClockFrame[0]);
secondClockFrame[1](1900);
assert.equal(exactClock.cinematicClockMs, 150, 'shared clock must advance by exact elapsed time and wrap at phase duration');
exactClock.toggleCinematicPlayback();
assert.equal(exactClock.cinematicPlaying, false, 'pause control must stop shared playback');
assert.equal(rafCallbacks.size, 0, 'pause control must cancel the active RAF');

exactClock.cinematicPlaying = true;
exactClock.cinematicClockMs = 740;
exactClock.replayCinematic();
assert.equal(exactClock.cinematicClockMs, 0, 'replay must reset the shared clock');
assert.equal(exactClock.cinematicReplayNonce, 1, 'replay must notify both renderers');
assert.equal(rafCallbacks.size, 1, 'replay while playing must restart one RAF loop');
exactClock.resetCinematicPlayback();
assert.equal(exactClock.cinematicPlaying, false, 'projection reset must pause cinematic playback');
assert.equal(rafCallbacks.size, 0, 'projection reset must cancel the RAF loop');

function createVideoLayer({ playing = true, clockMs = 0, currentTime = 0, paused = true, play } = {}) {
  const video = {
    readyState: 2,
    currentTime,
    paused,
    ended: false,
    playCalls: 0,
    pauseCalls: 0,
    play() {
      this.playCalls += 1;
      return play ? play() : Promise.resolve();
    },
    pause() {
      this.pauseCalls += 1;
      this.paused = true;
    }
  };
  const instance = {
    ...videoLayer.data(),
    controlled: true,
    playing,
    clockMs,
    replayNonce: 0,
    usesChromaKey: true,
    renderCalls: 0,
    $refs: { video, canvas: {} },
    $el: { clientWidth: 480, clientHeight: 320 },
    $nextTick(callback) { callback(); },
    ...videoLayer.methods
  };
  instance.renderFrame = () => {
    instance.renderCalls += 1;
    return true;
  };
  return { instance, video };
}

const withinTolerance = createVideoLayer({ playing: false, clockMs: 1000, currentTime: 0.95 });
withinTolerance.instance.syncPlayback();
assert.equal(withinTolerance.video.currentTime, 0.95, '50 ms drift must not seek');
withinTolerance.video.currentTime = 0.8;
withinTolerance.instance.syncPlayback();
assert.equal(withinTolerance.video.currentTime, 1, 'clock milliseconds must seek to seconds beyond 80 ms drift');

const alreadyPlaying = createVideoLayer({ playing: true, clockMs: 1000, currentTime: 1, paused: false });
alreadyPlaying.instance.syncPlayback();
assert.equal(alreadyPlaying.video.playCalls, 0, 'an already-playing video must not receive redundant play calls');

rafCallbacks.clear();
cancelledFrames.length = 0;
const pausedLayer = createVideoLayer({ playing: false, clockMs: 1000, currentTime: 1 });
pausedLayer.instance.frameHandle = requestAnimationFrame(() => {});
pausedLayer.instance.syncPlayback();
assert.equal(rafCallbacks.size, 0, 'pausing controlled playback must cancel its active render loop');
pausedLayer.instance.start(true);
assert.equal(pausedLayer.instance.renderCalls, 1, 'paused controlled playback may render one forced frame');
assert.equal(rafCallbacks.size, 0, 'a paused forced frame must not schedule a continuous render loop');

rafCallbacks.clear();
const pausedLoadLayer = createVideoLayer({ playing: false, clockMs: 0, currentTime: 0 });
pausedLoadLayer.instance.handleLoadedData();
assert.equal(pausedLoadLayer.instance.renderCalls, 1, 'loadeddata at the paused clock must render its available frame');
assert.equal(rafCallbacks.size, 0, 'a paused loadeddata frame must not schedule a render loop');

rafCallbacks.clear();
const legacyLayer = createVideoLayer({ playing: false });
legacyLayer.instance.controlled = false;
legacyLayer.instance.start();
assert.equal(rafCallbacks.size, 1, 'legacy uncontrolled chroma playback must retain its render loop');

const replayLayer = createVideoLayer({ playing: false, clockMs: 2250, currentTime: 0 });
videoLayer.watch.replayNonce.call(replayLayer.instance);
assert.equal(replayLayer.video.currentTime, 2.25, 'replay must force a seek to the shared clock');
replayLayer.instance.handleSeeked();
assert.equal(replayLayer.instance.renderCalls, 1, 'the seeked replay frame must render once while paused');

let rejectPlay;
const rejectedPlay = new Promise((resolve, reject) => { rejectPlay = reject; });
const blockedLayer = createVideoLayer({ playing: true, play: () => rejectedPlay });
blockedLayer.instance.syncPlayback();
rejectPlay(new Error('autoplay blocked'));
await Promise.resolve();
blockedLayer.instance.syncPlayback();
assert.equal(blockedLayer.video.playCalls, 1, 'a rejected play promise must block repeated attempts');

let rejectStalePlay;
const stalePlay = new Promise((resolve, reject) => { rejectStalePlay = reject; });
const staleLayer = createVideoLayer({ playing: true, play: () => stalePlay });
staleLayer.instance.syncPlayback();
staleLayer.instance.resetPlaybackGuards();
rejectStalePlay(new Error('stale rejection'));
await Promise.resolve();
assert.equal(staleLayer.instance.playBlocked, false, 'a stale play rejection must not block a newer generation');

rafCallbacks.clear();
cancelledFrames.length = 0;
const sourceResetLayer = createVideoLayer({ playing: false, clockMs: 0, currentTime: 0 });
sourceResetLayer.instance.frameHandle = requestAnimationFrame(() => {});
sourceResetLayer.instance.playBlocked = true;
videoLayer.watch.src.call(sourceResetLayer.instance);
assert.equal(sourceResetLayer.instance.playBlocked, false, 'source reset must clear playback guards');
assert.equal(rafCallbacks.size, 0, 'source reset must clean up the previous render loop');

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
