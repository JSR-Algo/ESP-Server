const test = require('node:test');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const { verifiedCatalogImage, createLegacyFixtureCourse, bindLegacyVisuals } = require('../../e2e/lesson-studio/helpers/legacy-fixtures');

function pageFor(rows, bytes = Buffer.from('actual-test-image'), dimensions = { width: 160, height: 120 }) {
  const calls = [];
  return {
    calls,
    evaluate: async (_fn, arg) => arg ? dimensions : { manager: 'test-manager', nest: 'test-author' },
    request: { fetch: async (url, options = {}) => {
      calls.push({ url, options });
      const data = url.includes('lesson-visual-assets?') ? rows
        : url.includes('/assets?profile=espTft') ? { profiles: ['espTft'], assets: [bundleAsset] }
          : { id: 'created', step_key: 's1' };
      return { ok: () => true, status: () => 200, text: async () => '', json: async () => ({ data }),
        headers: () => ({ 'content-type': 'image/png' }), body: async () => bytes };
    } },
  };
}

const bytes = Buffer.from('actual-test-image');
const source = { asset_key: 'real.object', version_id: 'real-version', category: 'teachingObject', profile: 'espTft',
  publication_state: 'published', storage_path: 'real/object.png', url: 'https://assets.example.invalid/real/object.png',
  sha256: createHash('sha256').update(bytes).digest('hex'), mime_type: 'image/png', bytes: bytes.length, width: 160, height: 120 };
const bundleAsset = { assetId: 'real-bundle-asset', assetKey: 'real.object', profile: 'espTft', layer: 'teachingObject',
  mediaType: source.mime_type, sha256: source.sha256, bytes: source.bytes, width: source.width, height: source.height, url: source.url };

function pageForSourceBundle(assets, { omitReadbackKey } = {}) {
  const page = pageFor([]);
  const fetch = page.request.fetch;
  const attached = [];
  page.request.fetch = async (url, options = {}) => {
    const response = await fetch(url, options);
    if (options.method === 'POST' && url.endsWith('/assets')) {
      const asset = assets.find(item => item.assetId === options.data.sourceAssetId);
      assert.ok(asset, 'attachment must reference an actual source asset');
      attached.push(asset);
    }
    if (url.includes('/assets?profile=espTft')) {
      const rows = url.includes('/lessons/created/')
        ? attached.filter(asset => asset.assetKey !== omitReadbackKey) : assets;
      response.json = async () => ({ data: { profiles: ['espTft'], assets: rows } });
    }
    return response;
  };
  return page;
}

const poseAssets = ['teach', 'listening', 'celebrate'].map(pose => ({
  ...bundleAsset, assetId: `source-${pose}`, assetKey: `robotOverlay.${pose}`, layer: 'robotOverlay',
  url: `https://assets.example.invalid/robot/${pose}.png`,
}));

test('legacy fixture persists all compatible source images including authored-step robot poses', async () => {
  const assets = [bundleAsset, ...poseAssets];
  const page = pageForSourceBundle([...assets,
    { ...bundleAsset, assetId: 'oversized', width: 640 },
    { ...bundleAsset, assetId: 'fake-url', url: 'fixture://fake.png' },
    { ...bundleAsset, assetId: 'wrong-profile', profile: 'mobile' },
  ]);
  await createLegacyFixtureCourse(page, 'e2e-complete-pose-bundle');
  const attachments = page.calls.filter(call => call.options.method === 'POST' && call.url.endsWith('/assets'));
  assert.deepEqual(attachments.map(call => call.options.data.sourceAssetId), assets.map(asset => asset.assetId));
  const firstWrite = page.calls.findIndex(call => call.options.method === 'POST');
  assert.deepEqual(page.calls.slice(0, firstWrite).filter(call => call.url.startsWith('https://')).map(call => call.url), assets.map(asset => asset.url));
});

test('corrupt later source pose fails byte verification before any fixture writes', async () => {
  const page = pageForSourceBundle([bundleAsset, { ...poseAssets[0], sha256: 'a'.repeat(64) }]);
  await assert.rejects(createLegacyFixtureCourse(page, 'e2e-corrupt-pose'), /source SHA-256/);
  assert.equal(page.calls.some(call => call.options.method === 'POST'), false);
});

test('successful attachment without a persisted required pose fails bundle readback', async () => {
  const page = pageForSourceBundle([bundleAsset, ...poseAssets], { omitReadbackKey: 'robotOverlay.listening' });
  await assert.rejects(createLegacyFixtureCourse(page, 'e2e-missing-pose-readback'), /robotOverlay.listening/);
});

test('catalog fixture preserves source metadata after HTTP bytes and decoded dimensions agree', async () => {
  const page = pageFor([source]);
  const actual = await verifiedCatalogImage(page, { category: 'teachingObject', profile: 'espTft' });
  assert.equal(actual.storagePath, source.storage_path);
  assert.equal(actual.sha256, source.sha256);
  assert.equal(actual.bytes, bytes.length);
  assert.equal(actual.versionId, source.version_id);
  assert.equal(page.calls[0].options.headers.Authorization, 'Bearer test-manager');
  assert.equal(page.calls[0].options.headers['X-Nest-Authorization'], 'Bearer test-author');
  assert.equal(page.calls[1].url, source.url);
  assert.equal(page.calls[1].options.headers, undefined, 'public media must not receive admin credentials');
});

test('fixture rejects fabricated hashes and incorrect decoded dimensions', async () => {
  await assert.rejects(verifiedCatalogImage(pageFor([{ ...source, sha256: 'a'.repeat(64) }]), { category: 'teachingObject', profile: 'espTft' }), /SHA-256/);
  await assert.rejects(verifiedCatalogImage(pageFor([{ ...source, bytes: 9999 }]), { category: 'teachingObject', profile: 'espTft' }), /source bytes/);
  await assert.rejects(verifiedCatalogImage(pageFor([{ ...source, mime_type: 'image/jpeg' }]), { category: 'teachingObject', profile: 'espTft' }), /source MIME/);
  await assert.rejects(verifiedCatalogImage(pageFor([source], bytes, { width: 64, height: 64 }), { category: 'teachingObject', profile: 'espTft' }), /dimensions/);
});

test('missing, oversized, or fabricated-location catalog images fail before any fixture writes', async () => {
  for (const rows of [[], [{ ...source, width: 640 }], [{ ...source, url: 'fixture://fake.png' }], [{ ...source, asset_key: 'e2e.previous.fake' }]]) {
    const page = pageFor(rows);
    await assert.rejects(verifiedCatalogImage(page, { category: 'teachingObject', profile: 'espTft' }), /canonical published/);
    assert.equal(page.calls.some(call => call.options.method !== 'GET'), false);
  }
});

test('legacy course fixture uses supported v1 authoring endpoints and never deletes shared drafts', async () => {
  const page = pageFor([]);
  await createLegacyFixtureCourse(page, 'e2e-visual-unit');
  const writes = page.calls.filter(call => call.options.method === 'POST');
  assert.equal(writes[0].url, '/nestjs/v1/admin/courses');
  assert.equal(writes[0].options.data.courseKey, 'e2e-visual-unit');
  assert.equal(writes[1].options.data.rendererVersion, 'teebot-lesson-renderer.v1');
  const steps = writes.filter(call => call.url.endsWith('/steps'));
  assert.deepEqual(steps.map(call => call.options.data.stepType), ['greeting', 'repeat', 'listen', 'review', 'celebrate']);
  assert.equal(steps.at(-1).options.data.stepBody.terminal, true);
  assert.equal(page.calls.some(call => call.options.method === 'DELETE'), false);
});

test('legacy fixture attaches a real source asset and reads back its bundle before returning', async () => {
  const page = pageFor([]);
  await createLegacyFixtureCourse(page, 'e2e-bundle-unit');
  assert.match(page.calls[0].url, /\/lessons\/[^/]+\/assets\?profile=espTft$/);
  const attachment = page.calls.find(call => call.options.method === 'POST' && call.url.endsWith('/assets'));
  assert.deepEqual(attachment?.options.data, { profile: 'espTft', sourceAssetId: bundleAsset.assetId });
  assert.equal(page.calls.at(-1).url, '/nestjs/v1/admin/lessons/created/assets?profile=espTft');
});

test('missing canonical source bundle fails before course creation', async () => {
  const page = pageFor([]);
  const fetch = page.request.fetch;
  page.request.fetch = async (url, options) => {
    const response = await fetch(url, options);
    if (url.includes('/assets?')) response.json = async () => ({ data: { profiles: [], assets: [] } });
    return response;
  };
  await assert.rejects(createLegacyFixtureCourse(page, 'e2e-empty-bundle'), /source.*bundle/i);
  assert.equal(page.calls.some(call => call.options.method === 'POST'), false);
});

test('attachment success without a persisted bundle fails readback verification', async () => {
  const page = pageFor([]);
  const fetch = page.request.fetch;
  page.request.fetch = async (url, options) => {
    const response = await fetch(url, options);
    if (url === '/nestjs/v1/admin/lessons/created/assets?profile=espTft') {
      response.json = async () => ({ data: { profiles: [], assets: [] } });
    }
    return response;
  };
  await assert.rejects(createLegacyFixtureCourse(page, 'e2e-unpersisted-bundle'), /actual espTft bundle/);
});

test('visual bind carries the two concurrency tokens from the same snapshot', async () => {
  const page = pageFor([]);
  const fetch = page.request.fetch;
  page.request.fetch = async (url, options) => {
    const response = await fetch(url, options);
    if (options.method === 'GET') response.json = async () => ({ data: { checksum: null, visualChecksum: 'snapshot-visual-token' } });
    return response;
  };
  await bindLegacyVisuals(page, 'owned-lesson', 'real-background', 'real-object');
  assert.deepEqual(page.calls.map(call => call.options.method), ['GET', 'PUT']);
  assert.deepEqual(page.calls[1].options.data, { expectedChecksum: null, expectedVisualChecksum: 'snapshot-visual-token', backgroundAssetVersionId: 'real-background', objectAssetVersionId: 'real-object' });
});
