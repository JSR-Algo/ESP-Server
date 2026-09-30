import test from 'node:test';
import assert from 'node:assert/strict';
import { PickerMjpegThumbnails, ThumbnailAdmissions, PICKER_THUMBNAIL_LIMITS as limits } from '../../src/components/lesson/picker-mjpeg-thumbnails.mjs';

const identity = { bytes: 100, sha256: 'a'.repeat(64), metadata: { codec: 'mjpeg', width: 240, height: 240 } };
const tick = () => new Promise(resolve => setImmediate(resolve));
function harness(scheduler = new ThumbnailAdmissions()) {
  const players = [], canvases = [];
  const registry = new PickerMjpegThumbnails({ scheduler,
    createCanvas() {
      const canvas = { width: 0, height: 0, getContext: () => ({ drawImage() {} }) };
      canvases.push(canvas); return canvas;
    },
    createPlayer(options) {
      let resolve, reject;
      const player = { disposed: false,
        load: () => new Promise((yes, no) => { resolve = yes; reject = no; }),
        finish() { assert.equal(options.present({ width: 240, height: 240 }), true); resolve(); },
        fail() { reject(new Error('fixture decoder rejected')); },
        dispose() { this.disposed = true; },
      };
      players.push(player); return player;
    },
  });
  return { registry, players, canvases, scheduler };
}

test('duplicate consumers share accepted work; hidden work finishes and releases its full player', async () => {
  const h = harness(), frames = [];
  const first = h.registry.acquire('/same', identity, frame => frames.push(frame));
  const second = h.registry.acquire('/same', structuredClone(identity), frame => frames.push(frame));
  await tick();
  assert.equal(h.players.length, 1);
  first(); second();
  assert.equal(h.players[0].disposed, false, 'last consumer retirement cannot abort accepted work');
  h.players[0].finish(); await tick();
  assert.equal(h.players[0].disposed, true);
  assert.equal(h.scheduler.active, 0);
  assert.equal(frames.length, 0, 'hidden consumers cannot receive stale presentations');
  h.registry.acquire('/same', identity, frame => frames.push(frame));
  assert.equal(frames.length, 1);
  assert.equal(h.players.length, 1);
  assert.equal(h.registry.cachedBytes, 160 * 160 * 4);
  h.registry.dispose();
  assert.equal(h.registry.cachedBytes, 0);
  assert.ok(h.canvases.every(canvas => canvas.width === 0 && canvas.height === 0));
});

test('global admissions cap acquisitions and declared byte buffers across retired pickers', async () => {
  const scheduler = new ThumbnailAdmissions();
  const a = harness(scheduler), b = harness(scheduler);
  a.registry.acquire('/a', identity, () => {});
  a.registry.acquire('/b', identity, () => {});
  const cancelQueued = a.registry.acquire('/stale', identity, () => assert.fail('stale work presented'));
  await tick();
  assert.equal(a.players.length, 2);
  cancelQueued(); a.registry.dispose();
  b.registry.acquire('/c', identity, () => {});
  await tick();
  assert.equal(b.players.length, 0);
  assert.equal(scheduler.active, 2);
  a.players[0].finish(); await tick();
  assert.equal(b.players.length, 1);
  assert.equal(a.registry.entries.size, 0);
  assert.equal(a.registry.cachedBytes, 0);
  a.players[1].finish(); b.players[0].finish(); await tick();
  assert.equal(scheduler.active, 0);
  assert.equal(scheduler.bytes, 0);
  assert.equal(a.players.length, 2, 'removed queued work never started');
  b.registry.dispose();

  const c = harness();
  c.registry.acquire('/max-valid', { ...identity, bytes: limits.acquisitionBytes }, () => {});
  c.registry.acquire('/small-valid', identity, () => {});
  await tick();
  assert.equal(c.players.length, 1, 'valid maximum-size media is admitted without rejecting it');
  assert.equal(c.scheduler.bytes, limits.acquisitionBytes);
  c.players[0].finish(); await tick();
  assert.equal(c.players.length, 2);
  c.players[1].finish(); await tick();
  c.registry.dispose();
});

test('completed snapshots obey count and pixel bounds, evict, and refetch on return', async () => {
  const h = harness();
  for (let index = 0; index < 40; index++) {
    const release = h.registry.acquire('/asset-' + index, identity, () => {});
    await tick(); h.players.at(-1).finish(); await tick(); release();
    assert.ok(h.registry.cachedBytes <= limits.pixelsBytes);
    assert.ok(h.registry.entries.size <= limits.entries);
  }
  assert.ok(h.canvases[0].width === 0, 'evicted pixel storage is released');
  h.registry.acquire('/asset-0', identity, () => {}); await tick();
  assert.equal(h.players.length, 41);
  h.players.at(-1).finish(); await tick(); h.registry.dispose();
});

test('identity metadata and URLs isolate snapshots; cached errors stay visible without a retry storm', async () => {
  const h = harness(), errors = [];
  h.registry.acquire('/same', identity, entry => errors.push(entry.error));
  await tick(); h.players[0].fail(); await tick();
  h.registry.acquire('/same', structuredClone(identity), entry => errors.push(entry.error));
  assert.equal(h.players.length, 1);
  assert.equal(errors.length, 2);
  assert.match(errors[0].message, /fixture decoder rejected/);
  h.registry.acquire('/different', identity, () => {});
  h.registry.acquire('/same', { ...identity, metadata: { ...identity.metadata, width: 150 } }, () => {});
  await tick(); assert.equal(h.players.length, 3);
  h.players[1].finish(); h.players[2].finish(); await tick();
  h.registry.dispose();
});

test('same-turn retire and reacquire cannot delete the replacement identity entry', async () => {
  const h = harness();
  const retire = h.registry.acquire('/race', identity, () => assert.fail('retired callback'));
  retire();
  h.registry.acquire('/race', identity, () => {});
  await tick();
  h.registry.acquire('/race', identity, () => {});
  await tick();
  assert.equal(h.players.length, 1, 'replacement entry survives old admitted microtask');
  h.players[0].finish(); await tick(); h.registry.dispose();
});

test('explicit retry restarts one failed identity for all still-visible consumers', async () => {
  const h = harness(), updates = [];
  h.registry.acquire('/retry', identity, entry => updates.push(entry.status));
  h.registry.acquire('/retry', identity, entry => updates.push(entry.status));
  await tick(); h.players[0].fail(); await tick();
  assert.deepEqual(updates, ['error', 'error']);
  h.registry.retry('/retry', identity);
  await tick();
  assert.equal(h.players.length, 2);
  h.players[1].finish(); await tick();
  assert.deepEqual(updates.slice(-2), ['ready', 'ready']);
  h.registry.dispose();
});

test('unexpected job failure is reported and still releases global admission', async () => {
  const scheduler = new ThumbnailAdmissions(), reported = [];
  scheduler.add(123, () => { throw new Error('unexpected callback'); }, error => reported.push(error.message));
  await tick();
  assert.deepEqual(reported, ['unexpected callback']);
  assert.equal(scheduler.active, 0);
  assert.equal(scheduler.bytes, 0);
});

test('accepted load uses existing 15-second deadline and surfaces timeout after releasing its player', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let rejectLoad, disposed = false;
  const registry = new PickerMjpegThumbnails({ createPlayer: () => ({
    load: () => new Promise((resolve, reject) => { rejectLoad = reject; }),
    dispose() { disposed = true; rejectLoad(new Error('AbortError')); },
  }) });
  const outcomes = [];
  registry.acquire('/stalled', identity, entry => outcomes.push(entry.error?.message));
  await tick();
  t.mock.timers.tick(limits.timeoutMs);
  await tick();
  assert.equal(disposed, true);
  assert.deepEqual(outcomes, ['load timed out. Check the selected media origin.']);
  registry.dispose();
});
