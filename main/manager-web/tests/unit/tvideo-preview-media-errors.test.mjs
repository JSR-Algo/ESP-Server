import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const Vue = require('vue');
const helperSource = readFileSync(new URL('../../src/components/lesson/tvideo-journey.js', import.meta.url), 'utf8');
const helpers = await import(`data:text/javascript;base64,${Buffer.from(helperSource).toString('base64')}`);

const source = readFileSync(new URL('../../src/components/lesson/TVideoRobotPreview.vue', import.meta.url), 'utf8');
const script = source.split('<script>')[1].split('</script>')[0].replace(/^import .*;$/gm, '').replace('export default', 'return');
const component = new Function(script)();
function setup() {
  const ctx = { clearRect() {} };
  const preview = { ...component.data(), ...component.methods, $refs: { canvas: { getContext: () => ctx }, background: {}, robot: {}, object: {} }, $set: (target, key, value) => { target[key] = value; } };
  for (const role of ['background', 'robot', 'object']) {
    preview[`${role}Url`] = `/${role}.media`;
    preview.$refs[role] = { getAttribute: () => `/${role}.media`, pause() {} };
  }
  preview.alphaSupported = true;
  preview.syncMediaClock = () => {};
  preview.draw = () => {};
  for (const name of ['hasMediaError', 'hasMissingMedia', 'hasLoadingMedia', 'mediaReadyForPreview']) {
    Object.defineProperty(preview, name, { get: () => component.computed[name]?.call(preview) });
  }
  return preview;
}

function readyAll(preview) {
  for (const role of ['background', 'robot', 'object']) preview.mediaReady({ target: preview.$refs[role] });
}

for (const role of ['background', 'robot', 'object']) {
  test(`missing ${role} source prevents preview acceptance and playback`, () => {
    const preview = setup();
    readyAll(preview);
    preview[`${role}Url`] = '';
    assert.equal(preview.hasMissingMedia, true);
    assert.equal(preview.mediaReadyForPreview, false);
    preview.toggle();
    assert.equal(preview.playing, false);
  });
}

test('all current sources must load before the canvas and Play become ready', () => {
  const preview = setup();
  assert.equal(preview.mediaReadyForPreview, false);
  assert.equal(preview.hasLoadingMedia, true);
  preview.mediaReady({ target: preview.$refs.background });
  preview.mediaReady({ target: preview.$refs.robot });
  assert.equal(preview.mediaReadyForPreview, false);
  preview.mediaReady({ target: preview.$refs.object });
  assert.equal(preview.mediaReadyForPreview, true);
  assert.equal(preview.hasLoadingMedia, false);
});

test('replacement rejects the old load even before Vue swaps element refs', () => {
  const preview = setup();
  readyAll(preview);
  const obsolete = preview.$refs.robot;
  preview.robotUrl = '/replacement.webm';
  component.watch.robotUrl.call(preview);
  preview.mediaReady({ target: obsolete });
  assert.equal(preview.mediaReadyForPreview, false);
  preview.$refs.robot = { getAttribute: () => preview.robotUrl, pause() {} };
  preview.mediaReady({ target: preview.$refs.robot });
  assert.equal(preview.mediaReadyForPreview, true);
  preview.mediaReady({ target: obsolete });
  assert.equal(preview.mediaReadyForPreview, true);
});

test('source replacement stops the live clock while the next source loads', async () => {
  const preview = setup();
  readyAll(preview);
  let ticks = 0;
  preview.playing = true;
  preview.timer = setInterval(() => { ticks += 1; }, 5);
  const timer = preview.timer;
  try {
    preview.objectUrl = '/next-object.png';
    component.watch.objectUrl.call(preview);
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(ticks, 0);
    assert.equal(preview.playing, false);
    assert.equal(preview.mediaReadyForPreview, false);
  } finally { clearInterval(timer); }
});

test('a fresh loadstart revokes readiness until loadeddata', () => {
  const preview = setup();
  readyAll(preview);
  preview.playing = true;
  preview.mediaLoading?.({ target: preview.$refs.robot });
  assert.equal(preview.mediaReadyForPreview, false);
  assert.equal(preview.playing, false);
  preview.mediaReady({ target: preview.$refs.robot });
  assert.equal(preview.mediaReadyForPreview, true);
});

test('seek completion draws only the current source without seeking again', () => {
  const preview = setup();
  let draws = 0;
  preview.draw = () => { draws += 1; };
  preview.syncMediaClock = () => { throw new Error('seek loop'); };
  preview.mediaSeeked?.({ target: {} });
  assert.equal(draws, 0);
  preview.mediaSeeked?.({ target: preview.$refs.robot });
  assert.equal(draws, 1);
});

for (const role of ['background', 'robot', 'object']) {
  test(`${role} decode failure is visible and stops playback`, () => {
    const preview = setup();
    preview.playing = true;
    preview.mediaFailed({ target: preview.$refs[role] });
    assert.equal(preview.hasMediaError, true);
    assert.equal(preview.playing, false);
    assert.equal(preview.timer, null);
    preview.toggle();
    assert.equal(preview.playing, false);
  });
}

test('an obsolete element cannot fail the replacement source', () => {
  const preview = setup();
  preview.mediaFailed({ target: {} });
  assert.equal(preview.hasMediaError, false);
});

test('failure clears a live interval, not only the playing flag', async () => {
  const preview = setup();
  let ticks = 0;
  preview.playing = true;
  preview.timer = setInterval(() => { ticks += 1; }, 5);
  const timer = preview.timer;
  try {
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.ok(ticks > 0);
    preview.mediaFailed({ target: preview.$refs.robot });
    const stoppedAt = ticks;
    await new Promise(resolve => setTimeout(resolve, 25));
    assert.equal(ticks, stoppedAt);
    assert.equal(preview.timer, null);
  } finally { clearInterval(timer); }
});

test('replacing one failed source retains another source failure', () => {
  const preview = setup();
  preview.mediaFailed({ target: preview.$refs.background });
  preview.mediaFailed({ target: preview.$refs.robot });
  component.watch.robotUrl.call(preview);
  assert.equal(preview.hasMediaError, true);
  component.watch.backgroundUrl.call(preview);
  assert.equal(preview.hasMediaError, false);
});

test('a probe resolving after destruction cannot update the preview', async () => {
  let resolve;
  const pending = new Promise(done => { resolve = done; });
  const c = new Function('supportsVp9Alpha', script)(() => pending);
  const preview = { ...c.data(), ...c.methods, draw() { throw new Error('destroyed draw'); } };
  c.mounted.call(preview);
  preview._isDestroyed = true;
  resolve(true);
  await pending;
  assert.equal(preview.alphaSupported, null);
});

test('inconclusive alpha support can be retried with Replay', async () => {
  const values = [null, true];
  const c = new Function('supportsVp9Alpha', script)(() => Promise.resolve(values.shift()));
  const preview = { ...c.data(), ...c.methods, draw() {}, syncMediaClock() {} };
  c.mounted.call(preview);
  await Promise.resolve();
  assert.equal(preview.alphaCheckFailed, true);
  preview.replay();
  await Promise.resolve();
  assert.equal(preview.alphaCheckFailed, false);
  assert.equal(preview.alphaSupported, true);
});

test('a canvas decode exception is surfaced instead of silently falling back', () => {
  const preview = setup();
  preview.$refs.robot.readyState = 2;
  assert.equal(preview.drawMedia({ drawImage() { throw new Error('decode'); } }, preview.$refs.robot, 0, 0, 10, 10), false);
  assert.equal(preview.hasMediaError, true);
});

test('an unavailable decoded frame clears the canvas without drawing a placeholder', () => {
  const preview = setup();
  readyAll(preview);
  let clears = 0;
  preview.$refs.canvas = { getContext: () => ({ clearRect() { clears += 1; } }) };
  preview.journey = { scenePath: {} };
  preview.previewFrameState = { effect: 'teach' };
  assert.doesNotThrow(() => component.methods.draw.call(preview));
  assert.ok(clears > 0);
});

test('real Vue source watchers accept initial events and stop on a robot phase transition', async () => {
  const c = new Function('quantizeClockMs', 'requiredCueIds', script)(helpers.quantizeClockMs, helpers.requiredCueIds);
  const preview = new Vue({
    ...c,
    propsData: {
      journey: { steps: [{ stepKey: 'barn', teachingObject: { assetVersionId: 'object' } }], assets: { background: { assetVersionId: 'background' }, robotClips: [{ role: 'flight', assetVersionId: 'flight' }, { role: 'walking', assetVersionId: 'walking' }] }, scenePath: {} },
      preset: {}, mediaUrl: value => value ? `/${value}` : '',
    },
    methods: { ...c.methods, draw() {}, syncMediaClock() {} },
  });
  try {
    await Vue.nextTick();
    preview.alphaSupported = true;
    for (const role of ['background', 'robot', 'object']) {
      const url = preview[`${role}Url`];
      preview.$refs[role] = { getAttribute: () => url, pause() {} };
    }
    readyAll(preview);
    await Vue.nextTick();
    assert.equal(preview.mediaReadyForPreview, true);
    preview.playing = true;
    preview.clockMs = 3700;
    assert.equal(preview.robotUrl, '/walking');
    assert.equal(preview.mediaReadyForPreview, false);
    await Vue.nextTick();
    assert.equal(preview.playing, false);
    assert.equal(preview.timer, null);
    preview.$refs.robot = { getAttribute: () => '/walking', pause() {} };
    preview.mediaReady({ target: preview.$refs.robot });
    assert.equal(preview.mediaReadyForPreview, true);
  } finally { preview.$destroy(); }
});
