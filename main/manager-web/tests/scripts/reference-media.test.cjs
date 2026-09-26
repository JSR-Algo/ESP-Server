const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup(protocol = 'http:') {
  const html = fs.readFileSync(path.resolve(__dirname, '../../public/tvideo-demo/index.html'), 'utf8');
  const source = html.slice(html.indexOf('const referenceClipSources='), html.indexOf('function loadClip('));
  const requests = [], blobs = [];
  let readerFailure = false;
  const context = { WeakMap, Map, Promise, location: { protocol },
    fetch(url) { return new Promise(resolve => requests.push({ url, resolve })); },
    FileReader: class { readAsDataURL(blob) {
      if (readerFailure) { readerFailure = false; this.error = new Error('reader failed'); this.onerror(); return; }
      blobs.push(blob); this.result = `data:video/quicktime;base64,${blob.toString('base64')}`; this.onload();
    } } };
  vm.createContext(context); vm.runInContext(source, context);
  const el = { src: '', loads: 0, load() { this.loads++; } };
  return { el, requests, blobs, failReader: () => { readerFailure = true; }, set: url => context.setReferenceClipSource(el, url),
    finish(index, ok = true) { requests[index].resolve({ ok, status: ok ? 200 : 404, blob: async () => Buffer.from(requests[index].url) }); } };
}

test('HTTP clips assign only complete original bytes and reuse the document download', async () => {
  const f = setup(); const first = f.set('greet.mov');
  assert.equal(f.el.loads, 0); f.finish(0); assert.equal(await first, true);
  assert.deepEqual(f.blobs[0], Buffer.from('greet.mov'));
  assert.equal(await f.set('greet.mov'), false); assert.equal(f.el.loads, 1);
  const other = f.set('celebrate.mov'); f.finish(1); await other;
  await f.set('greet.mov'); assert.equal(f.requests.length, 2); assert.equal(f.el.src, 'data:video/quicktime;base64,' + Buffer.from('greet.mov').toString('base64'));
});

test('rapid A-B-A selection applies only the latest request once', async () => {
  const f = setup(); const a = f.set('a.mov'), b = f.set('b.mov'), last = f.set('a.mov');
  f.finish(0); f.finish(1); await Promise.all([a, b, last]);
  assert.equal(f.el.loads, 1); assert.equal(f.el.src, 'data:video/quicktime;base64,' + Buffer.from('a.mov').toString('base64'));
});

test('failed HTTP selection stays rejected and permits a later retry', async () => {
  const f = setup(); const first = f.set('a.mov'); f.finish(0, false);
  await assert.rejects(first, /HTTP 404/); assert.equal(f.el.loads, 0);
  const retry = f.set('a.mov'); assert.equal(f.requests.length, 2);
  f.finish(1); assert.equal(await retry, true);
});

test('file demo retains synchronous direct video loading without fetch', async () => {
  const f = setup('file:'); const pending = f.set('greet.mov');
  assert.equal(f.el.src, 'greet.mov'); assert.equal(f.el.loads, 1);
  assert.equal(await pending, true); assert.equal(f.requests.length, 0);
});

test('inline conversion failure rejects and releases the selection for retry', async () => {
  const f = setup(); f.failReader(); const first = f.set('a.mov'); f.finish(0);
  await assert.rejects(first, /reader failed/); assert.equal(f.el.loads, 0);
  const retry = f.set('a.mov'); f.finish(1); assert.equal(await retry, true);
});
