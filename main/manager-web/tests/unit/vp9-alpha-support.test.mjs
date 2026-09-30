import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../src/components/lesson/vp9-alpha-support.js', import.meta.url), 'utf8');
function setup(alpha = [0, 255, 255, 0]) {
  let timeout;
  let cleared = false;
  let loads = 0;
  let paused = false;
  const video = { videoWidth: 2, videoHeight: 2, pause() { paused = true; }, removeAttribute() { delete this.src; }, load() { loads += 1; } };
  const ctx = { drawImage() {}, getImageData: () => ({ data: alpha.flatMap(value => [255, 0, 255, value]) }) };
  const document = { createElement: kind => kind === 'video' ? video : { getContext: () => ctx } };
  const supports = new Function('document', 'setTimeout', 'clearTimeout', source.replace('export function', 'function') + '\nreturn supportsVp9Alpha;')(document, fn => { timeout = fn; return 1; }, () => { cleared = true; });
  return { video, ctx, supports, timeout: () => timeout(), cleaned: () => paused && loads === 1 && cleared && !video.src && video.onerror === null && video.onloadeddata === null };
}

for (const [name, alpha, expected] of [
  ['transparent and opaque pixels', [0, 255, 255, 0], true],
  ['opaque decoder', [255, 255, 255, 255], false],
  ['blank canvas', [0, 0, 0, 0], null],
]) test(`canary distinguishes ${name} and releases media`, async () => {
  const s = setup(alpha);
  const promise = s.supports();
  assert.equal(s.supports(), promise, 'one probe per page');
  s.video.onloadeddata();
  assert.equal(await promise, expected);
  assert.ok(s.cleaned());
});

for (const failure of ['timeout', 'decode', 'canvas', 'dimensions']) test(`${failure} fails closed and releases media`, async () => {
  const s = setup();
  const promise = s.supports();
  if (failure === 'timeout') s.timeout();
  else if (failure === 'decode') s.video.onerror();
  else {
    if (failure === 'canvas') s.ctx.drawImage = () => { throw new Error('decode'); };
    else s.video.videoWidth = 0;
    s.video.onloadeddata();
  }
  assert.equal(await promise, null);
  assert.ok(s.cleaned());
  const retry = s.supports();
  assert.notEqual(retry, promise, 'inconclusive checks must be retryable');
  s.timeout();
  assert.equal(await retry, null);
});
