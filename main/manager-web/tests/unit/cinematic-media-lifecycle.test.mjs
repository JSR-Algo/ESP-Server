import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';

const source = readFileSync(new URL('../../src/components/lesson/CinematicVideoLayer.vue', import.meta.url), 'utf8');
const helpers = readFileSync(new URL('../../src/components/lesson/flattened-cinematic-preview.js', import.meta.url), 'utf8');
const url = code => `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const parser = readFileSync(new URL('../../src/components/lesson/mjpeg-mp4.mjs', import.meta.url), 'utf8');
const playback = readFileSync(new URL('../../src/components/lesson/mjpeg-playback.mjs', import.meta.url), 'utf8').replace("'./mjpeg-mp4.mjs'", JSON.stringify(url(parser)));
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace("'./flattened-cinematic-preview'", JSON.stringify(url(helpers))).replace("'./mjpeg-playback.mjs'", JSON.stringify(url(playback)));
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

test('MJPEG clock advances through the actual parent clock owner only', () => {
 const {vm,preview}=transportSetup();let ticks=0;vm.usesMjpeg=true;vm._mjpeg={tick(t){ticks++;this.time=t/1000;},time:0,state(){return{ready:true,pending:false,seeking:false,ended:false,currentTimeSec:this.time};}};
 preview.advanceCinematicClock(250);assert.equal(ticks,1);assert.equal(preview.cinematicClockMs,250);
});
test('MJPEG source replacement retires fetch before starting the next identity',async()=>{
 const {vm,events}=setup();const oldFetch=globalThis.fetch;let resolveOld,signal,calls=0;
 vm.usesMjpeg=true;vm.mjpegIdentity={bytes:4,sha256:'a'.repeat(64),metadata:{codec:'mjpeg',mediaType:'video/mp4',hasAudio:false,fps:15}};
 vm.$nextTick=async()=>{};
 globalThis.fetch=async(_src,options)=>{calls++;signal=options.signal;return new Promise(r=>resolveOld=r);};
 try{const first=vm.loadMjpeg();for(let i=0;i<15;i++)await Promise.resolve();assert.equal(calls,1);
 const next=vm.loadMjpeg();for(let i=0;i<10;i++)await Promise.resolve();assert.ok(signal.aborted);assert.equal(calls,1);
 resolveOld(new Response(new Uint8Array(4)));for(let i=0;i<30;i++)await Promise.resolve();assert.equal(calls,2);
 component.beforeDestroy.call(vm);resolveOld(new Response(new Uint8Array(4)));await Promise.all([first,next]);assert.deepEqual(events,[]);
 }finally{globalThis.fetch=oldFetch;component.beforeDestroy.call(vm);}
});
test('changing from MJPEG identity to legacy mode retires the old adapter',()=>{
 const {vm}=setup();let reloads=0;vm.usesMjpeg=false;vm._mjpeg={};vm.loadMjpeg=()=>reloads++;component.watch.mjpegIdentity.handler.call(vm);assert.equal(reloads,1);
});
test('MJPEG timeout disposes its buffer and reports the selected URL',()=>{
 const {vm,events}=setup();let disposed=0;vm._mjpeg={dispose(){disposed++;},state(){return{ready:false};}};vm.usesMjpeg=true;
 const oldSet=globalThis.setTimeout,oldClear=globalThis.clearTimeout;let timeout;
 globalThis.setTimeout=fn=>{timeout=fn;return 42;};globalThis.clearTimeout=()=>{};
 try{vm.armLoadDeadline();timeout();assert.equal(disposed,1);assert.equal(events[0][1].src,vm.src);assert.match(events[0][1].message,/timed out/);}finally{globalThis.setTimeout=oldSet;globalThis.clearTimeout=oldClear;}
});

test('MJPEG canvas preserves contain and cover geometry for unequal source and rect',()=>{
 for(const [fit,expected] of [['contain',[50,0,100,100]],['cover',[0,-50,200,200]]]){
  const {vm}=setup();vm.usesMjpeg=true;vm.usesChromaKey=false;vm.positionStyle={objectFit:fit};vm.$el={clientWidth:200,clientHeight:100};
  let draw;const canvas={getContext:()=>({clearRect(){},drawImage(...args){draw=args.slice(1);},getImageData(){return{data:new Uint8ClampedArray(200*100*4).fill(255)};},putImageData(){}})};
  assert.ok(vm.renderFrame({width:100,height:100},canvas));assert.deepEqual(draw,expected);
 }
});
test('MJPEG replay does not hide a failed source while waiting for explicit media retry',()=>{
 const {vm}=setup();vm.usesMjpeg=true;vm.errorMessage='SHA-256 mismatch';vm._mjpeg={disposed:true};
 component.watch.replayNonce.call(vm);assert.equal(vm.errorMessage,'SHA-256 mismatch');
});

async function completedMjpeg() {
 const { MjpegPlayback } = await import(url(playback));
 const identity = JSON.parse(readFileSync(new URL('../fixtures/mjpeg/current-flyIn.json', import.meta.url)));
 const bytes = readFileSync(new URL('../fixtures/mjpeg/current-flyIn.mp4', import.meta.url));
 let now = 0;
 const player = new MjpegPlayback({ src: '/exact.mp4', identity, mode: 'once', crypto: webcrypto,
   fetcher: async () => new Response(bytes), now: () => now,
   decode: async () => ({ width: 240, height: 240, close() {} }), present: () => true });
 await player.load(); player.seek(3.2); await player.settled(); assert.equal(player.ended, true);
 const { vm } = setup();
 Object.assign(vm, { usesMjpeg: true, _mjpeg: player, transportMaster: true, playing: true });
 return { vm, player, tick: time => player.tick(now = time) };
}

for (const target of [0, 1000]) {
 test(`completed MJPEG immediately seeks to ${target} and retains actual component Play intent`, async () => {
   const { vm, player, tick } = await completedMjpeg();
   try {
     vm.clockMs = target; vm.syncPlayback(true);
     await player.settled(); assert.equal(player.playing, true);
     tick(100); tick(300); await player.settled();
     assert.ok(player.time > target / 1000);
   } finally { await player.dispose(); }
 });
}

for (const action of ['pause', 'dispose', 'repeat', 'decode-error']) {
 test(`completed MJPEG replay respects ${action} while seek is pending`, async () => {
   const { vm, player, tick } = await completedMjpeg();
   let settle, closes = 0;
   player.decode = () => new Promise((resolve, reject) => { settle = { resolve, reject }; });
   vm.clockMs = 1000; vm.syncPlayback(true);
   while (!settle) await Promise.resolve();
   if (action === 'pause') { vm.playing = false; vm.syncPlayback(); }
   if (action === 'dispose') component.beforeDestroy.call(vm);
   if (action === 'repeat') { vm.syncPlayback(); vm.syncPlayback(); }
   if (action === 'decode-error') settle.reject(new Error('decode failed'));
   else settle.resolve({ width: 240, height: 240, close() { closes++; } });
   await player.settled();
   assert.equal(player.playing, action === 'repeat');
   if (action !== 'repeat') { const time = player.time; tick(100); tick(300); assert.equal(player.time, time); }
   if (action === 'dispose' || action === 'decode-error') assert.equal(player.bytes, null);
   assert.equal(closes, action === 'decode-error' ? 0 : 1);
   await player.dispose();
 });
}

test('Play alone preserves MJPEG once-final hold without an explicit seek', async () => {
 const { vm, player, tick } = await completedMjpeg();
 vm.syncPlayback(); tick(100); tick(300); await player.settled();
 assert.equal(player.ended, true); assert.equal(player.playing, false); assert.equal(player.time, 3.2);
 await player.dispose();
});
