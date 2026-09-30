const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const { isExpectedNavigationAbort } = require('../../e2e/lesson-studio/helpers/page-errors');

function request(overrides = {}) {
  return {
    method: () => 'GET', failure: () => ({ errorText: 'net::ERR_ABORTED' }),
    isNavigationRequest: () => false, resourceType: () => 'fetch',
    url: () => 'https://assets.tjbot.vn/course-mvp/m0-temp-phases-20260921/teach-placeholder.mp4',
    frame: () => ({ url: () => 'http://127.0.0.1:8132/' }), ...overrides,
  };
}

test('retains unexplained media aborts in admission evidence across known origins', () => {
  const { EventEmitter } = require('node:events');
  const { observeJourney } = require('../../e2e/lesson-studio/helpers/real-service-evidence');
  for (const [resourceType, url] of [
    ['fetch', 'https://assets.tjbot.vn/course-mvp/teach.mp4'],
    ['media', 'https://assets.tjbot.vn/course-mvp/teach.mp4'],
    ['media', 'http://127.0.0.1:8132/tvideo-demo/assets/flight.webm'],
    ['media', 'https://admin.tjbot.vn/tvideo-demo/assets/teach.mp4'],
    ['other', 'https://task4-media.localhost:9443/tvideo-demo/assets/teach.mp4'],
    ['image', 'https://admin.tjbot.vn/tvideo-demo/assets/barn.png'],
    ['font', 'https://fonts.gstatic.com/example.woff2'],
  ]) {
    const page = new EventEmitter();
    const journal = observeJourney(page);
    page.emit('requestfailed', request({ resourceType: () => resourceType, url: () => url }));
    assert.equal(journal.evidence.requestFailures.length, 1, url);
    assert.throws(() => journal.assertHappyPath());
  }
});

test('does not classify Course MVP fetch cancellation as navigation', () => {
  assert.equal(isExpectedNavigationAbort(request()), false);
  assert.equal(isExpectedNavigationAbort(request({ failure: () => ({ errorText: 'cancelled' }) })), false);
  for (const overrides of [
    { method: () => 'POST' },
    { failure: () => ({ errorText: 'net::ERR_CONNECTION_REFUSED' }) },
    { url: () => 'https://assets.tjbot.vn/private/teach.mp4' },
    { url: () => 'https://other.example/course-mvp/teach.mp4' },
    { url: () => 'https://assets.tjbot.vn/course-mvp/config.json' },
  ]) assert.equal(isExpectedNavigationAbort(request(overrides)), false);
});

test('does not infer intentional cancellation from a video URL', () => {
  for (const resourceType of ['media', 'other']) {
    assert.equal(isExpectedNavigationAbort(request({ resourceType: () => resourceType })), false);
    assert.equal(isExpectedNavigationAbort(request({ resourceType: () => resourceType,
      failure: () => ({ errorText: 'net::ERR_FAILED' }) })), false);
  }
  assert.equal(isExpectedNavigationAbort(request({ resourceType: () => 'xhr' })), false);
});

test('retains same-origin WebM and API aborts', () => {
  assert.equal(isExpectedNavigationAbort(request({ resourceType: () => 'media',
    url: () => 'http://127.0.0.1:8132/tvideo-demo/assets/robot-alive/flight/flight-in.webm' })), false);
  assert.equal(isExpectedNavigationAbort(request({ resourceType: () => 'xhr',
    url: () => 'http://127.0.0.1:8132/nestjs/v1/admin/lessons/example/steps' })), false);
});

function loadDecoderAssertion() {
  const filename = path.resolve(__dirname, '../../e2e/lesson-studio/helpers/real-service-evidence.js');
  const req = createRequire(filename);
  const expect = (value, message) => ({
    toBeGreaterThan: expected => assert.ok(value > expected, message),
    toBe: expected => assert.equal(value, expected, message),
    toHaveCount: async expected => assert.equal(await value.count(), expected, message),
  });
  expect.poll = (read, { message } = {}) => ({ toBe: async expected => assert.equal(await read(), expected, message) });
  const sandbox = { module: { exports: {} }, exports: {},
    require: name => name === '@playwright/test' ? { expect } : req(name) };
  vm.runInNewContext(fs.readFileSync(filename, 'utf8'), sandbox, { filename });
  return sandbox.module.exports.assertDecodedStage;
}

test('navigation settlement waits for image and API bodies, including follow-up reads', async () => {
  const { EventEmitter } = require('node:events');
  const { observeJourney } = require('../../e2e/lesson-studio/helpers/real-service-evidence');
  const page = new EventEmitter();
  const journal = observeJourney(page);
  const api = request({ resourceType: () => 'xhr' });
  const image = request({ resourceType: () => 'image' });
  page.emit('request', api);
  let settled = false;
  const done = journal.waitForSettledRequests().then(() => { settled = true; });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(settled, false);
  page.emit('response', { status: () => 200 });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(settled, false, 'response headers are not body completion');
  page.emit('request', image);
  page.emit('requestfinished', api);
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(settled, false, 'follow-up image must finish before navigation');
  page.emit('requestfinished', image);
  await done;
  assert.equal(settled, true);
});

test('settlement does not erase an aborted API request from failure evidence', async () => {
  const { EventEmitter } = require('node:events');
  const { observeJourney } = require('../../e2e/lesson-studio/helpers/real-service-evidence');
  const page = new EventEmitter();
  const journal = observeJourney(page);
  const api = request({ resourceType: () => 'xhr' });
  page.emit('request', api);
  page.emit('requestfailed', api);
  await journal.waitForSettledRequests();
  assert.equal(journal.evidence.requestFailures.length, 1);
});

function canvasStage(alpha, ready = true) {
  const canvas = { width: 2, height: 2,
    parentElement: { __vue__: { _mjpeg: { state: () => ({ ready, pending: !ready, seeking: false }) } } },
    getContext: () => ({ getImageData: () => ({ data: Uint8ClampedArray.from([90, 80, 70, alpha]) }) }) };
  const image = { evaluate: async read => read({ complete: true, naturalWidth: 2 }) };
  return { locator: selector => ({
    count: async () => selector === 'video' ? 0 : 1,
    all: async () => [image], evaluate: async read => read(canvas),
  }) };
}
const phase = { layers: [{ slot: 'robotOverlay', metadata: { codec: 'mjpeg', mediaType: 'video/mp4' } }] };

test('accepts decoded MJPEG canvas pixels without a native video element', async () => {
  await loadDecoderAssertion()(canvasStage(255), phase);
});

test('still rejects an empty MJPEG canvas', async () => {
  await assert.rejects(loadDecoderAssertion()(canvasStage(0), phase), /rendered character pixels/);
});

test('rejects stale visible pixels while the current MJPEG decoder is pending', async () => {
  await assert.rejects(loadDecoderAssertion()(canvasStage(255, false), phase), /current MJPEG decoder/);
});

test('media integrity uses the fixture CA and retains TLS and byte validation', async t => {
  const { execFileSync } = require('node:child_process');
  const { createServer } = require('node:https');
  const { createHash } = require('node:crypto');
  const { request: playwrightRequest } = require('@playwright/test');
  const { assertHttpMedia } = require('../../e2e/lesson-studio/helpers/real-service-evidence');
  const root = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'media-ca-'));
  const previous = process.env.TASK4_ASSIGNMENT_TLS_ROOT;
  const previousRuntime = process.env.TASK4_ASSIGNMENT_RUNTIME_ROOT;
  const certs = {};
  for (const host of ['task4-media.localhost', 'wrong.localhost']) {
    const directory = path.join(root, host);
    fs.mkdirSync(directory);
    execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
      '-subj', `/CN=${host}`, '-addext', `subjectAltName=DNS:${host}`,
      '-keyout', path.join(directory, 'key.pem'), '-out', path.join(directory, 'cert.pem')], { stdio: 'ignore' });
    certs[host] = { directory, key: fs.readFileSync(path.join(directory, 'key.pem')),
      cert: fs.readFileSync(path.join(directory, 'cert.pem')) };
  }
  const bytes = Buffer.from('unchanged media bytes');
  const context = await playwrightRequest.newContext();
  const servers = [];
  t.after(async () => {
    await context.dispose();
    await Promise.all(servers.map(server => new Promise(resolve => server.close(resolve))));
    if (previous === undefined) delete process.env.TASK4_ASSIGNMENT_TLS_ROOT;
    else process.env.TASK4_ASSIGNMENT_TLS_ROOT = previous;
    if (previousRuntime === undefined) delete process.env.TASK4_ASSIGNMENT_RUNTIME_ROOT;
    else process.env.TASK4_ASSIGNMENT_RUNTIME_ROOT = previousRuntime;
    fs.rmSync(root, { recursive: true, force: true });
  });
  const serve = async certificate => {
    const server = createServer(certificate, (_request, response) => {
      response.writeHead(200, { 'Content-Type': 'application/octet-stream' });
      response.end(bytes);
    });
    servers.push(server);
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    return `https://task4-media.localhost:${server.address().port}/tvideo-demo/asset.bin`;
  };
  const url = await serve(certs['task4-media.localhost']);
  const manifest = { assets: [{ assetKey: 'tls-proof', url, bytes: bytes.length,
    sha256: createHash('sha256').update(bytes).digest('hex') }] };
  const page = { request: context };
  process.env.TASK4_ASSIGNMENT_TLS_ROOT = certs['task4-media.localhost'].directory;
  await t.test('accepts only exact bytes over the pinned certificate', async () => {
    await assertHttpMedia(page, manifest);
  });
  await t.test('the browser route uses the same verified transport', async () => {
    const { installTrustedTask4MediaRoute } = require('../../e2e/lesson-studio/helpers/session');
    let result;
    await installTrustedTask4MediaRoute({ route: async (pattern, handle) => {
      assert.ok(pattern.test(url));
      await handle({ request: () => ({ url: () => url, headers: () => ({}) }),
        fulfill: async response => { result = response; } });
    } });
    assert.equal(result.status, 200);
    assert.deepEqual(result.body, bytes);
  });
  await t.test('fixture trust does not apply to other origins or paths', async () => {
    const { trustedTask4MediaResponse } = require('../../e2e/lesson-studio/helpers/session');
    for (const other of [url.replace('task4-media.localhost', 'assets.example.com'),
      url.replace('https:', 'http:'), url.replace('/tvideo-demo/', '/private/'),
      url.replace('task4-media.localhost', 'task4-media.localhost.evil')]) {
      assert.equal(await trustedTask4MediaResponse(other), null);
    }
  });
  await t.test('rejects a validly delivered hash mismatch', async () => {
    await assert.rejects(assertHttpMedia(page, { assets: [{ ...manifest.assets[0], sha256: '0'.repeat(64) }] }), /tls-proof/);
  });
  await t.test('rejects an untrusted server certificate', async () => {
    process.env.TASK4_ASSIGNMENT_TLS_ROOT = certs['wrong.localhost'].directory;
    await assert.rejects(assertHttpMedia(page, manifest), /self.signed|certificate/i);
  });
  await t.test('rejects a trusted certificate for the wrong hostname', async () => {
    const wrongUrl = await serve(certs['wrong.localhost']);
    await assert.rejects(assertHttpMedia(page, { assets: [{ ...manifest.assets[0], url: wrongUrl }] }), /hostname|altnames/i);
  });
  await t.test('missing fixture CA does not disable normal TLS verification', async () => {
    delete process.env.TASK4_ASSIGNMENT_TLS_ROOT;
    delete process.env.TASK4_ASSIGNMENT_RUNTIME_ROOT;
    await assert.rejects(assertHttpMedia(page, manifest), /self.signed|certificate/i);
  });
});
