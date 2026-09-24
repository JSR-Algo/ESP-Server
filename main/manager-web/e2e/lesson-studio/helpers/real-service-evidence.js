const fs = require('node:fs/promises');
const { expect } = require('@playwright/test');
const journals = new WeakMap();

function observeJourney(page) {
  const evidence = { pageErrors: [], consoleErrors: [], requestFailures: [], mediaRetirements: [], httpFailures: [], expectedFaults: [], checkpoints: [] };
  const allowed = [];
  const pending = new Set();
  const generations = new Map();
  const requestGenerations = new WeakMap();
  const retiredFailures = new Set();
  let retirement = null;
  let bindingReady;
  page.on('framenavigated', frame => generations.set(frame, (generations.get(frame) || 0) + 1));
  let lastRequestChange = Date.now();
  page.on('request', request => {
    if (!['xhr', 'fetch', 'image'].includes(request.resourceType())) return;
    pending.add(request);
    if (request.resourceType() === 'image') requestGenerations.set(request, generations.get(request.frame()) || 0);
    lastRequestChange = Date.now();
  });
  const finished = request => {
    if (pending.delete(request)) lastRequestChange = Date.now();
  };
  page.on('requestfinished', finished);
  page.on('requestfailed', finished);
  page.on('pageerror', error => evidence.pageErrors.push(error.message));
  page.on('requestfailed', request => {
    // Media cancellation stays visible until its lifecycle cause is established.
    const { isExpectedNavigationAbort } = require('./page-errors');
    if (isExpectedNavigationAbort(request)) return;
    const failure = {
      method: request.method(), resourceType: request.resourceType(), url: request.url(), error: request.failure()?.errorText || 'unknown',
    };
    evidence.requestFailures.push(failure);
    const owner = retirement?.requests.get(request);
    if (owner?.hidden && ['net::ERR_ABORTED', 'cancelled'].includes(failure.error)) {
      retirement.failures.push({ owner, failure, failureIndex: evidence.requestFailures.length - 1 });
    }
  });
  page.on('console', message => {
    if (message.type() === 'error') evidence.consoleErrors.push({ text: message.text(), url: message.location().url || '' });
  });
  page.on('response', response => {
    if (response.status() < 400) return;
    const item = { method: response.request().method(), path: new URL(response.url()).pathname, status: response.status(), url: response.url() };
    const index = allowed.findIndex(rule => rule.method === item.method && rule.path === item.path && rule.status === item.status);
    if (index < 0) evidence.httpFailures.push(item);
    else evidence.expectedFaults.push({ ...item, label: allowed.splice(index, 1)[0].label });
  });
  const journal = {
    evidence,
    async retireDocumentForNavigation(action) {
      if (retirement) throw new Error('document navigation observation already active');
      bindingReady ||= page.exposeBinding('__m1DocumentHidden', ({ frame }, { token, event }) => {
        const owner = retirement?.documents.get(frame);
        if (!['beforeunload', 'pagehide'].includes(event)) return;
        if (!owner || owner.token !== token || (generations.get(frame) || 0) !== owner.generation) return;
        // A parent unload cancels child requests before the child's pagehide callback arrives.
        for (const document of retirement.documents.values()) {
          if (!document.hidden && (document.frame === frame || document.ancestors.includes(frame)) &&
              (generations.get(document.frame) || 0) === document.generation) {
            document.hidden = true;
            document.retiringToken = token;
            document.retiringEvent = event;
          }
        }
      });
      await bindingReady;
      const scope = { documents: new Map(), requests: new Map(), failures: [] };
      for (const frame of page.frames()) {
        const generation = generations.get(frame) || 0;
        const token = await frame.evaluate(() => {
          if (!window.__m1DocumentToken) {
            window.__m1DocumentToken = crypto.randomUUID();
            const token = window.__m1DocumentToken;
            for (const name of ['beforeunload', 'pagehide']) addEventListener(name, event => {
              if (event.isTrusted && !event.persisted) {
                void window.__m1DocumentHidden({ token, event: name }).catch(() => {});
              }
            });
          }
          return window.__m1DocumentToken;
        });
        if (generation !== (generations.get(frame) || 0)) throw new Error('document changed during navigation registration');
        const ancestors = [];
        for (let parent = frame.parentFrame?.(); parent; parent = parent.parentFrame()) ancestors.push(parent);
        scope.documents.set(frame, { frame, token, generation, ancestors, hidden: false });
      }
      // Freeze Request objects, not URLs: replacement documents can load identical assets.
      for (const request of pending) {
        if (request.method() !== 'GET' || request.resourceType() !== 'image') continue;
        const owner = scope.documents.get(request.frame());
        if (owner && requestGenerations.get(request) === owner.generation) scope.requests.set(request, owner);
      }
      retirement = scope;
      try {
        const result = await action();
        for (const owner of scope.documents.values()) {
          owner.replaced = !owner.frame.isDetached() && (
            (generations.get(owner.frame) || 0) > owner.generation &&
            await owner.frame.evaluate(() => window.__m1DocumentToken || null) !== owner.token);
        }
        for (const { owner, failure, failureIndex } of scope.failures) {
          const replacedAncestor = owner.ancestors.some(frame => scope.documents.get(frame)?.replaced);
          if (!owner.replaced && !(owner.frame.isDetached() && replacedAncestor)) continue;
          retiredFailures.add(failure);
          evidence.mediaRetirements.push({ failureIndex, documentToken: owner.token,
            retiringDocumentToken: owner.retiringToken, lifecycleEvent: owner.retiringEvent,
            cause: 'explicit-navigation-with-document-replacement' });
        }
        return result;
      } finally {
        retirement = null;
      }
    },
    async waitForSettledRequests() {
      // Save callbacks can start another readback and image load after response headers.
      await expect.poll(() => pending.size === 0 && Date.now() - lastRequestChange >= 250,
        { message: 'finish API and image bodies before deliberate navigation', timeout: 10000 }).toBe(true);
    },
    expectFault(method, path, status, label) { allowed.push({ method, path, status, label }); },
    async checkpoint(testInfo, name, state, capture = page) {
      const file = `${name}.png`;
      await capture.screenshot({ path: testInfo.outputPath(file), ...(capture === page ? { fullPage: true } : {}) });
      evidence.checkpoints.push({ name, file, state });
      await fs.writeFile(testInfo.outputPath('real-service-readback.json'), JSON.stringify(evidence, null, 2));
    },
    assertHappyPath() {
      expect(evidence.pageErrors).toEqual([]);
      expect(evidence.requestFailures.filter(failure => !retiredFailures.has(failure))).toEqual([]);
      expect(evidence.httpFailures).toEqual([]);
      const unexpectedConsole = evidence.consoleErrors.filter(message => !evidence.expectedFaults.some(fault =>
        message.url === fault.url && message.text.includes(String(fault.status))));
      expect(unexpectedConsole, 'console errors outside explicitly registered faults').toEqual([]);
      expect(allowed, 'each registered fault must actually execute').toEqual([]);
    },
  };
  journals.set(page, journal);
  return journal;
}

function expectObservedFault(page, method, path, status, label) {
  journals.get(page)?.expectFault(method, path, status, label);
}

async function assertHttpMedia(page, manifest) {
  const crypto = require('node:crypto');
  for (const asset of manifest.assets) {
    const url = new URL(asset.url);
    expect(['http:', 'https:']).toContain(url.protocol);
    // APIRequestContext does not use the browser's certificate-pinned fixture route.
    const trusted = await require('./session').trustedTask4MediaResponse(url.href);
    const response = trusted || await page.request.get(url.href, { timeout: 15000 });
    expect(trusted ? response.status : response.status(), `${asset.assetKey} HTTP media`).toBe(200);
    const body = trusted ? response.body : await response.body();
    expect(body.length, asset.assetKey).toBe(Number(asset.bytes));
    expect(crypto.createHash('sha256').update(body).digest('hex'), asset.assetKey).toBe(asset.sha256);
    expect((trusted ? response.headers : response.headers())['content-type']).not.toMatch(/text\/html/);
  }
}

async function assertDecodedStage(stage, phase) {
  const images = stage.locator('img');
  expect(await images.count()).toBeGreaterThan(0);
  for (const image of await images.all()) {
    await expect.poll(() => image.evaluate(img => img.complete && img.naturalWidth > 0)).toBe(true);
  }
  const video = stage.locator('video');
  const overlay = phase?.layers.find(layer => layer.slot === 'robotOverlay');
  const usesMjpeg = overlay?.metadata.codec === 'mjpeg';
  await expect(video).toHaveCount(usesMjpeg ? 0 : 1);
  if (!usesMjpeg) await expect.poll(() => video.evaluate(v => {
    if (v.readyState < 2 || !v.videoWidth || !v.videoHeight) return false;
    const canvas = document.createElement('canvas');
    canvas.width = v.videoWidth; canvas.height = v.videoHeight;
    const context = canvas.getContext('2d'); context.drawImage(v, 0, 0);
    return context.getImageData(0, 0, canvas.width, canvas.height).data.some((byte, index) => index % 4 === 3 && byte > 0);
  }), { message: 'actual decoder pixels required; metadata/time alone is insufficient' }).toBe(true);
  const keyed = stage.locator('.cinematic-canvas');
  await expect(keyed).toHaveCount(1);
  if (usesMjpeg) await expect.poll(() => keyed.evaluate(canvas => {
    const state = canvas.parentElement.__vue__?._mjpeg?.state();
    return Boolean(state?.ready && !state.pending && !state.seeking);
  }), { message: 'current MJPEG decoder must settle before inspecting pixels' }).toBe(true);
  await expect.poll(() => keyed.evaluate(canvas => canvas.getContext('2d')
    .getImageData(0, 0, canvas.width, canvas.height).data.some((byte, index) => index % 4 === 3 && byte > 0)),
  { message: 'rendered character pixels required after chroma removal' }).toBe(true);
}

async function waitForPublishedPack(page, lesson, checksum, evidence) {
  const { adminAuthHeaders } = require('./admin-api');
  const headers = await adminAuthHeaders(page);
  const lessonId = lesson.lesson_key || lesson.lessonKey;
  const lessonVersion = Number(lesson.lesson_version || lesson.lessonVersion);
  await expect.poll(async () => {
    const response = await page.request.get('/nestjs/v1/public/lesson-assets/latest', { headers, timeout: 10000 });
    evidence.push({ lessonId, lessonVersion, status: response.status() });
    if (response.status() === 503) return false;
    expect(response.status()).toBe(200);
    const { data } = await response.json();
    const pack = data.index.find(item => item.lessonId === lessonId && item.lessonVersion === lessonVersion
      && item.profile === 'espTft' && item.manifestChecksum === checksum);
    if (pack) evidence.push({ generation: data.generation, pack });
    return Boolean(pack);
  }, { timeout: 30000, intervals: [500, 1000, 2000], message: `exact published pack required for ${lessonId} v${lessonVersion}` }).toBe(true);
}

module.exports = { observeJourney, expectObservedFault, assertHttpMedia, assertDecodedStage, waitForPublishedPack };
