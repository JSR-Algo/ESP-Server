const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../../src/components/lesson');
const projectionSource = fs.readFileSync(path.join(root, 'robot-preview-projection.js'), 'utf8');
const project = new Function(projectionSource.replace(/export /g, '') + '\nreturn projectEspTftPreview;')();
const componentSource = fs.readFileSync(path.join(root, 'RobotEspTftProjectionPreview.vue'), 'utf8');
function component() {
  const script = componentSource.split('<script>')[1].split('</script>')[0]
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'return');
  return new Function('CinematicVideoLayer', 'FlattenedCinematicPreview', script)({}, {});
}
const rect = (x) => ({ x, y: 70, width: 150, height: 240 });
function manifest() {
  const phase = (phaseId, activityId, x, hasObject = true) => ({
    templateId: 'layeredCinematic', phaseId, activityIds: [activityId],
    playbackMode: phaseId === 'teach' ? 'loop' : 'once', timing: { durationMs: 3000 },
    layers: [
      { slot: 'backgroundScene', url: '/background.jpg', assetVersionId: 'bg', sha256: 'a'.repeat(64), metadata: { mediaType: 'image/jpeg', rect: { x: 0, y: 0, width: 480, height: 320 }, fit: 'cover' } },
      ...(hasObject ? [{ slot: 'teachingObject', url: '/object.png', assetVersionId: 'obj', sha256: 'b'.repeat(64), metadata: { mediaType: 'image/png', rect: { x: 20, y: 168, width: 95, height: 95 }, fit: 'contain' } }] : []),
      { slot: 'robotOverlay', url: `/${activityId}-${phaseId}.mp4`, assetVersionId: `${activityId}-${phaseId}`, sha256: 'c'.repeat(64), metadata: { mediaType: 'video/mp4', rect: rect(x), durationMs: 3000 } },
    ],
  });
  const phases = [phase('teach', 'a', 118), phase('celebrate', 'a', 120), phase('teach', 'b', 130, false)];
  const assets = [];
  for (const p of phases) for (const layer of p.layers) {
    layer.assetKey = layer.assetVersionId; layer.version = 1; layer.bytes = 100;
    assets.push({ id: `${layer.assetKey}@v1`, assetKey: layer.assetKey, version: 1, sha256: layer.sha256, bytes: layer.bytes, url: layer.url, mediaType: layer.metadata.mediaType });
    delete layer.url;
  }
  return { manifestVersion: 'teebot-lesson-renderer.v5', lessonId: 'A', profile: 'espTft', assets,
    steps: [{ id: 'a', activityId: 'a', scene: { robotOverlay: { asset: { src: '/stale-teach.mp4' } }, teachingObject: { asset: { src: '/stale-object.png' } } } }, { id: 'b', activityId: 'b' }],
    cinematicPhases: phases };
}
test('v5 selects phase and preserves exact media identity, geometry and timing', () => {
  const p = project(manifest(), 0, 'correct', null, null, 'celebrate');
  const robot = p.layers.find(layer => layer.id === 'robotOverlay');
  assert.equal(robot.src, '/a-celebrate.mp4');
  assert.equal(robot.assetVersionId, 'a-celebrate');
  assert.equal(robot.sha256, 'c'.repeat(64));
  assert.equal(robot.bounds.x, 120);
  assert.equal(p.cinematicPhase.phaseId, 'celebrate');
  assert.equal(p.cinematicPhase.playbackMode, 'once');
  assert.equal(p.cinematicPhase.durationMs, 3000);
});
test('phase object omission stays hidden even when legacy scene contains an object', () => {
  const m = manifest(); m.cinematicPhases[0].layers.splice(1, 1);
  const p = project(m, 0, 'teach', null, null, 'teach');
  assert.equal(p.layers.find(layer => layer.id === 'teachingObject').visible, false);
  assert.equal(p.layers.find(layer => layer.id === 'wordPill').visible, false);
});
test('v5 stage never adds caption, progress or word UI absent from TFT composite', () => {
  const m = manifest(); m.steps[0].prompt = 'Listen to the word';
  const p = project(m, 0, 'listen', null, null, 'teach');
  assert.equal(p.layers.filter(layer => !['background', 'teachingObject', 'robotOverlay'].includes(layer.id) && layer.visible).length, 0);
});
test('phase selection stays scoped to selected activity', () => {
  const p = project(manifest(), 1, 'teach', null, null, 'teach');
  assert.equal(p.layers.find(layer => layer.id === 'robotOverlay').src, '/b-teach.mp4');
  assert.deepEqual(p.availablePhases, ['teach']);
});
test('missing requested phase fails visibly without substituting another clip', () => {
  const p = project(manifest(), 1, 'teach', null, null, 'celebrate');
  assert.equal(p.layers.find(layer => layer.id === 'robotOverlay').visible, false);
  assert.ok(p.warnings.some(warning => /phase.*celebrate/i.test(warning)));
});
test('mismatched catalog hash never falls back to scene media', () => {
  const m = manifest();
  m.assets.find(asset => asset.assetKey === 'a-celebrate').sha256 = 'd'.repeat(64);
  const p = project(m, 0, 'correct', null, null, 'celebrate');
  assert.equal(p.layers.find(layer => layer.id === 'robotOverlay').visible, false);
  assert.ok(p.warnings.some(warning => /source is unavailable/.test(warning)));
});
test('v5 never adds CSS motion to baked media', () => {
  const c = component();
  assert.equal(c.computed.motionClass.call({ isV5: true, projection: { timeline: [] } }), '');
  assert.equal(c.computed.entranceClass.call({ isV5: true, playing: true, projection: { entrance: 'flyIn' } }), '');
});
test('once clock stops at final frame while loop clock wraps', () => {
  const c = component();
  for (const [mode, expected, playing] of [['once', 3000, false], ['loop', 200, true]]) {
    const state = { isV5: true, cinematicPlaying: true, cinematicStartedAt: 0, cinematicClockMs: 0, cinematicDurationMs: 3000, cinematicPlaybackMode: mode, cinematicMediaReady() { return true; }, cinematicLayerById() { return { mediaPlaybackState: () => ({ currentTimeSec: 3.2 }) }; }, stopCinematicClock() { this.stopped = true; } };
    c.methods.advanceCinematicClock.call(state, 3200);
    assert.equal(state.cinematicClockMs, expected);
    assert.equal(state.cinematicPlaying, playing);
  }
});
test('manifest switch releases lesson and cinematic clocks and clears phase', () => {
  const c = component(); let stopped = 0;
  assert.equal(typeof c.watch.manifest, 'function');
  const state = { selectedPhase: 'celebrate', playStep: 4, mediaRetryNonce: 0, mediaErrors: { robotOverlay: 'old' }, stopPlay() { stopped++; }, resetCinematicPlayback() { stopped++; } };
  c.watch.manifest.call(state);
  assert.equal(stopped, 2);
  assert.equal(state.selectedPhase, '');
  assert.equal(state.playStep, 0);
  assert.deepEqual(state.mediaErrors, {});
  assert.equal(state.mediaRetryNonce, 1, 'same-source failures must reload when changing lessons');
});
test('changing phase retains an image failure for the same source', () => {
  const c = component();
  const state = { projection: { layers: [{ id: 'background', src: '/bad.jpg' }] }, mediaErrors: { 'background:/bad.jpg': 'Image unavailable' }, resetCinematicPlayback() {}, armImageDeadline() {} };
  c.watch.projection.call(state);
  assert.equal(state.mediaErrors['background:/bad.jpg'], 'Image unavailable');
});
test('seek clamps to duration and rebases playback without losing paused state', () => {
  const c = component();
  const state = { cinematicDurationMs: 3000, cinematicClockMs: 0, cinematicStartedAt: 100, cinematicReplayNonce: 0, cinematicPlaying: false };
  c.methods.seekCinematic.call(state, 9000);
  assert.equal(state.cinematicClockMs, 3000);
  assert.equal(state.cinematicStartedAt, null);
  assert.equal(state.cinematicReplayNonce, 1);
  assert.equal(state.cinematicPlaying, false);
});
test('stale media errors cannot stop the newly selected source', () => {
  const c = component();
  const state = { projection: { layers: [{ id: 'robotOverlay', src: '/new.mp4' }] }, cinematicPlaying: true };
  c.methods.handleMediaError.call(state, { layerId: 'robotOverlay', src: '/old.mp4', message: 'failure' });
  assert.equal(state.cinematicPlaying, true);
});

test('phase clock waits for decoded media without consuming entrance time', () => {
 const c=component();const state={isV5:true,cinematicStartedAt:null,cinematicClockMs:0,cinematicDurationMs:3200,cinematicPlaybackMode:'once',cinematicPlaying:true,cinematicMediaReady:()=>false};
 c.methods.advanceCinematicClock.call(state,4000);assert.equal(state.cinematicClockMs,0);assert.equal(state.cinematicPlaying,true);
 state.cinematicMediaReady=()=>true;state.cinematicLayerById=()=>({mediaPlaybackState:()=>({currentTimeSec:0})});c.methods.advanceCinematicClock.call(state,5000);assert.equal(state.cinematicClockMs,0);
});
test('explicit media retry resets failed sources and remounts their generation',()=>{
 const c=component();const state={mediaErrors:{'background:/bad.jpg':'failed'},mediaRetryNonce:0,resetCinematicPlayback(){},armImageDeadline(){}};
 c.methods.retryMedia.call(state);assert.deepEqual(state.mediaErrors,{});assert.equal(state.mediaRetryNonce,1);
});
test('pending image has a bounded visible error',()=>{
 const c=component();const oldSet=global.setTimeout,oldClear=global.clearTimeout;let callback;
 global.setTimeout=fn=>{callback=fn;return 42};global.clearTimeout=()=>{};
 try {const state={imageLoadTimer:null,projection:{layers:[{id:'background',src:'/pending.jpg',visible:true,mediaType:'image/jpeg'}]},$refs:{stageImages:[{complete:false,getAttribute:()=>'/pending.jpg'}]},$nextTick:fn=>fn(),handleMediaError(e){this.error=e.message},...c.methods};state.handleMediaError=e=>{state.error=e.message};state.armImageDeadline();callback();assert.match(state.error,/timed out/);}finally{global.setTimeout=oldSet;global.clearTimeout=oldClear}
});
test('Play does not start child media while the stage is pending',()=>{
 const c=component();const state={isV5:true,cinematicPlaying:false,projection:{cinematicPhase:{}},cinematicDurationMs:3200,mediaErrors:{},cinematicMediaReady:()=>false,cinematicClockMs:0,scheduleCinematicFrame(){this.scheduled=true;}};
 c.methods.toggleCinematicPlayback.call(state);assert.equal(state.cinematicPlaying,false);assert.equal(state.scheduled,undefined);
});
