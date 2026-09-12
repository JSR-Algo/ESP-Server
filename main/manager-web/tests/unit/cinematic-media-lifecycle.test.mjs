import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../../src/components/lesson/CinematicVideoLayer.vue', import.meta.url), 'utf8');
const helpers = readFileSync(new URL('../../src/components/lesson/flattened-cinematic-preview.js', import.meta.url), 'utf8');
const url = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace("'./flattened-cinematic-preview'", JSON.stringify(url(helpers)));
const component = (await import(url(script))).default;
const parentSource = readFileSync(new URL('../../src/components/lesson/RobotEspTftProjectionPreview.vue', import.meta.url), 'utf8');
const parentScript = parentSource.match(/<script>([\s\S]*?)<\/script>/)[1]
  .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'return');
const parent = new Function('CinematicVideoLayer', 'FlattenedCinematicPreview', parentScript)({}, {});
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
function setup() {
  const events = [];
  const video = { readyState: 2, currentTime: 0, duration: 2, paused: true, ended: false,
    pauseCalls: 0, loadCalls: 0, removed: [],
    pause() { this.pauseCalls++; this.paused = true; },
    play() { this.paused = false; return Promise.resolve(); },
    removeAttribute(name) { this.removed.push(name); }, load() { this.loadCalls++; } };
  const vm = { ...component.data(), ...component.methods, layerId: 'robotOverlay', src: 'selected.mp4',
    controlled: true, playing: false, clockMs: 0, playbackMode: 'once', durationMs: 2000,
    usesChromaKey: true, $refs: { video, canvas: {} }, $emit: (...args) => events.push(args),
    $nextTick: callback => callback(), $el: { clientWidth: 150, clientHeight: 150 } };
  return { vm, video, events };
}
test('unmount pauses and unloads media and invalidates pending play', () => {
  const { vm, video } = setup(); const generation = vm.playGeneration;
  component.beforeDestroy.call(vm);
  assert.equal(video.pauseCalls, 1); assert.deepEqual(video.removed, ['src']);
  assert.equal(video.loadCalls, 1); assert.ok(vm.playGeneration > generation);
});
test('decode error stops playback and reports affected source', () => {
  const { vm, video, events } = setup();
  vm.handleMediaError({ target: video });
  assert.match(vm.errorMessage, /load\/decode/); assert.equal(video.pauseCalls, 1);
  assert.equal(events[0][0], 'media-error'); assert.equal(events[0][1].src, 'selected.mp4');
});
test('failed play promise is visible and cannot block a new generation', async () => {
  const { vm, video } = setup(); vm.playing = true;
  video.play = () => Promise.reject(new Error('blocked'));
  vm.syncPlayback(); await Promise.resolve();
  assert.match(vm.errorMessage, /playback/);
  component.watch.src.call(vm); assert.equal(vm.errorMessage, '');
});
test('once clips hold final frame without another play request', () => {
  const { vm, video } = setup(); vm.playing = true; vm.clockMs = 2000; video.currentTime = 2; video.ended = true;
  let calls = 0; video.play = () => { calls++; }; vm.syncPlayback();
  assert.equal(calls, 0); assert.equal(video.paused, true);
});
test('loop clips seek the mapped local time', () => {
  const { vm, video } = setup(); vm.playbackMode = 'loop'; vm.clockMs = 2500;
  vm.syncPlayback(); assert.equal(video.currentTime, 0.5);
});
test('chroma readback error remains an explicit failed preview', () => {
  const { vm, video } = setup(); vm.$refs.canvas = { getContext: () => ({ clearRect() {}, drawImage() {}, getImageData() { throw new Error('tainted'); } }) };
  vm.start(true); assert.match(vm.errorMessage, /composite|CORS/);
});
test('a pending source times out visibly and unmount cancels the deadline', () => {
  const { vm, video } = setup(); video.readyState = 0;
  const oldSet = globalThis.setTimeout, oldClear = globalThis.clearTimeout;
  let callback, cancelled;
  globalThis.setTimeout = fn => { callback = fn; return 41; };
  globalThis.clearTimeout = id => { cancelled = id; };
  try {
    component.mounted.call(vm); callback();
    assert.match(vm.errorMessage, /timed out/);
    component.beforeDestroy.call(vm); assert.equal(cancelled, 41);
  } finally { globalThis.setTimeout = oldSet; globalThis.clearTimeout = oldClear; }
});
test('replay clears a previous play rejection before rendering a fresh frame', () => {
  const {vm}=setup(); vm.errorMessage='playback failed'; let draws=0; vm.renderFrame=()=>{draws++;return true;};
  component.watch.replayNonce.call(vm);vm.start(true);
  assert.equal(vm.errorMessage,'');assert.equal(draws,1);
});
test('metadata alone is not a decoded frame ready for the phase clock', () => {
  const {vm,video}=setup();video.readyState=1;assert.equal(vm.mediaPlaybackState().ready,false);
});

test('decoder metadata without any actual pixel is a visible decode failure',()=>{
 const {vm,video}=setup();const canvas={getContext:()=>({clearRect(){},drawImage(){},getImageData(){return {data:new Uint8ClampedArray(150*150*4)}},putImageData(){}})};
 assert.equal(vm.renderFrame(video,canvas),false);assert.match(vm.errorMessage,/decoded frame/);
});

test('replay revalidates pixels after a chroma decoder failure', () => {
  const { vm } = setup(); vm.chromaUnavailable = true; vm.errorMessage = 'no decoded frame';
  component.watch.replayNonce.call(vm);
  assert.equal(vm.chromaUnavailable, false);
});

test('a stale canvas cannot certify a new source that draws no pixels', () => {
  const { vm, video } = setup(); const pixels = new Uint8ClampedArray(150 * 150 * 4).fill(255);
  const canvas = { width: 150, height: 150, getContext: () => ({
    clearRect() { pixels.fill(0); }, drawImage() {}, getImageData() { return { data: pixels }; }, putImageData() {}
  }) };
  assert.equal(vm.renderFrame(video, canvas), false);
  assert.match(vm.errorMessage, /decoded frame/);
});

function transportSetup(mode = 'once') {
  const child = setup(); child.vm.transportMaster = true; child.vm.durationMs = 3200; child.vm.playbackMode = mode;
  const preview = { ...parent.methods, isV5: true, cinematicPlaying: false, cinematicClockMs: 0,
    cinematicStartedAt: null, cinematicFrameHandle: null, cinematicReplayNonce: 0,
    cinematicDurationMs: 3200, cinematicPlaybackMode: mode, projection: { cinematicPhase: {} }, mediaErrors: {},
    $refs: { cinematicLayers: [child.vm], stageImages: [{ complete: true, naturalWidth: 480 }] }, scheduleCinematicFrame() {} };
  const sync = () => { child.vm.playing = preview.cinematicPlaying; child.vm.clockMs = preview.cinematicClockMs; child.vm.syncPlayback(); };
  return { ...child, preview, sync };
}

test('normal Play cannot consume a once phase while the actual play promise is pending', async () => {
  const { vm, video, preview, sync } = transportSetup(); let resolve;
  video.play = () => new Promise(done => { resolve = done; });
  preview.toggleCinematicPlayback(); sync(); assert.equal(vm.playPending, true);
  preview.advanceCinematicClock(1000); sync(); preview.advanceCinematicClock(4200); sync();
  assert.equal(preview.cinematicClockMs, 0); assert.equal(preview.cinematicPlaying, true);
  assert.equal(video.currentTime, 0);
  video.paused = false; resolve(); await Promise.resolve();
  video.currentTime = .2; preview.advanceCinematicClock(9000); sync();
  assert.equal(preview.cinematicClockMs, 200); assert.equal(video.currentTime, .2);
});

test('once and loop clocks follow real transport across buffering and resume', () => {
  for (const mode of ['once', 'loop']) {
    const { video, preview, sync } = transportSetup(mode); preview.cinematicPlaying = true; video.paused = false;
    video.currentTime = .7; preview.advanceCinematicClock(1000); sync(); assert.equal(preview.cinematicClockMs, 700);
    video.readyState = 1; preview.advanceCinematicClock(10000); sync(); assert.equal(preview.cinematicClockMs, 700);
    video.readyState = 2; preview.advanceCinematicClock(11000); sync(); assert.equal(preview.cinematicClockMs, 700);
    video.currentTime = 1.1; preview.advanceCinematicClock(12000); sync(); assert.equal(preview.cinematicClockMs, 1100);
    if (mode === 'once') {
      video.currentTime = 3.2; video.ended = true; video.paused = true; preview.advanceCinematicClock(13000); sync();
      assert.equal(preview.cinematicClockMs, 3200); assert.equal(preview.cinematicPlaying, false);
    } else {
      video.currentTime = .05; preview.advanceCinematicClock(13000); sync();
      assert.equal(preview.cinematicClockMs, 50); assert.equal(preview.cinematicPlaying, true);
    }
  }
});

test('transport master ignores automatic clock feedback but honors explicit seek and replay', () => {
  const { vm, video, preview, sync } = transportSetup(); preview.cinematicPlaying = true; video.paused = false; video.currentTime = .5;
  preview.cinematicClockMs = 2500; sync(); assert.equal(video.currentTime, .5);
  preview.seekCinematic(1700); vm.clockMs = preview.cinematicClockMs; component.watch.replayNonce.call(vm); assert.equal(video.currentTime, 1.7);
  preview.replayCinematic(); vm.clockMs = preview.cinematicClockMs; component.watch.replayNonce.call(vm); assert.equal(video.currentTime, 0);
});

test('pause/source change invalidates pending play settlement and rejection', async () => {
  for (const rejectOld of [false, true]) {
    const { vm, video } = transportSetup(); let settle;
    vm.playing = true; video.play = () => new Promise((resolve, reject) => { settle = rejectOld ? reject : resolve; });
    vm.syncPlayback(); vm.playing = false; component.watch.playing.call(vm);
    vm.src = 'next.mp4'; component.watch.src.call(vm); settle(); await Promise.resolve();
    assert.equal(vm.playPending, false); assert.equal(vm.playBlocked, false); assert.equal(vm.errorMessage, ''); assert.equal(video.paused, true);
  }
});

test('stale play completion cannot settle a newer resumed play request', async () => {
  const { vm, video } = transportSetup(); const completions = [];
  video.play = () => new Promise(resolve => completions.push(resolve));
  vm.playing = true; vm.syncPlayback();
  vm.playing = false; component.watch.playing.call(vm);
  vm.playing = true; component.watch.playing.call(vm); assert.equal(completions.length, 2);
  completions[0](); await Promise.resolve(); assert.equal(vm.playPending, true);
  completions[1](); await Promise.resolve(); assert.equal(vm.playPending, false);
});
