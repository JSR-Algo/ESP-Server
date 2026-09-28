const { expect } = require('@playwright/test');
const { createHash } = require('node:crypto');
const { adminApi } = require('./admin-api');

async function verifiedCatalogImage(page, { category, profile, excludeSha256 } = {}) {
  const rows = await adminApi(page, 'GET', `/lesson-visual-assets?category=${encodeURIComponent(category)}&profile=${encodeURIComponent(profile)}`);
  const row = rows.find(asset => asset.category === category && asset.profile === profile
    && !/^(?:e2e[.-]|clone\.)/.test(asset.asset_key || '')
    && asset.publication_state === 'published' && /^image\/(png|jpeg)$/.test(asset.mime_type)
    && /^https?:\/\//.test(asset.url || '') && asset.sha256 !== excludeSha256
    && Number(asset.width) > 0 && Number(asset.height) > 0
    // Match the legacy limits in backend lesson-esptft-asset-budget.ts.
    && (profile !== 'espTft' || (asset.width * asset.height * 2 <= 153600
      && (category === 'scene' ? asset.width <= 480 && asset.height <= 320 : Math.max(asset.width, asset.height) <= 192))));
  expect(row, `canonical published ${profile} ${category} image with real HTTP media and legacy dimensions is required`).toBeTruthy();
  await verifyImageBytes(page, row);
  return {
    versionId: row.version_id,
    category: row.category,
    title: row.title || row.asset_key,
    profile: row.profile,
    storagePath: row.storage_path,
    sha256: row.sha256,
    mimeType: row.mime_type,
    bytes: Number(row.bytes),
    width: Number(row.width),
    height: Number(row.height),
    publicationState: row.publication_state,
    compatibilityMetadata: row.compatibility_metadata,
  };
}

async function verifyImageBytes(page, row) {
  const response = await page.request.fetch(row.url, { method: 'GET' });
  expect(response.ok(), `source image HTTP ${response.status()}: ${row.asset_key}`).toBe(true);
  const bytes = await response.body();
  expect(createHash('sha256').update(bytes).digest('hex'), `source SHA-256: ${row.asset_key}`).toBe(row.sha256);
  expect(bytes.length, `source bytes: ${row.asset_key}`).toBe(Number(row.bytes));
  expect(response.headers()['content-type']?.split(';')[0], `source MIME: ${row.asset_key}`).toBe(row.mime_type);
  const dimensions = await page.evaluate(async ({ base64, mimeType }) => {
    const image = new Image();
    image.src = `data:${mimeType};base64,${base64}`;
    await image.decode();
    return { width: image.naturalWidth, height: image.naturalHeight };
  }, { base64: bytes.toString('base64'), mimeType: row.mime_type });
  expect(dimensions, `source decoded dimensions: ${row.asset_key}`).toEqual({ width: Number(row.width), height: Number(row.height) });
}

async function copyVisualVersion(page, assetKey, image) {
  const { versionId: _sourceVersionId, ...metadata } = image;
  return adminApi(page, 'POST', `/lesson-visual-assets/${encodeURIComponent(assetKey)}/versions`, metadata);
}

async function createLegacyFixtureCourse(page, courseKey) {
  const sourceLessonId = process.env.LESSON_STUDIO_E2E_VISUAL_SOURCE_LESSON_ID
    || '00000006-0002-0000-0000-000000000001';
  const sourceBundle = await adminApi(page, 'GET', `/lessons/${sourceLessonId}/assets?profile=espTft`);
  const sourceAssets = sourceBundle.assets.filter(asset => asset.profile === 'espTft'
    && /^image\/(png|jpeg)$/.test(asset.mediaType) && /^https?:\/\//.test(asset.url || '')
    && asset.width > 0 && asset.height > 0 && asset.width * asset.height * 2 <= 153600
    && (asset.layer === 'backgroundScene' ? asset.width <= 480 && asset.height <= 320
      : ['teachingObject', 'robotOverlay'].includes(asset.layer) && Math.max(asset.width, asset.height) <= 192));
  expect(sourceAssets.length, `source lesson ${sourceLessonId} must have a real legacy-compatible espTft image bundle`).toBeGreaterThan(0);
  for (const asset of sourceAssets) {
    await verifyImageBytes(page, { ...asset, asset_key: asset.assetKey, mime_type: asset.mediaType });
  }
  const course = await adminApi(page, 'POST', '/courses', { courseKey, title: courseKey, locale: 'en-US', ageBand: '4-6' });
  const lesson = await adminApi(page, 'POST', `/courses/${course.id}/lessons`, {
    lessonKey: courseKey, title: courseKey, locale: 'en-US', ageBand: '4-6',
    rendererVersion: 'teebot-lesson-renderer.v1', estimatedDurationSec: 180, durationPreset: 3,
  });
  const steps = [];
  for (const stepType of ['greeting', 'repeat', 'listen', 'review', 'celebrate']) {
    steps.push(await adminApi(page, 'POST', `/lessons/${lesson.id}/steps`, {
      stepType, prompt: 'Find the barn together.', subject: 'barn', stepBody: {
        timeoutSec: 5,
        storyBeat: { goal: 'Find the barn together.', successReaction: 'We found the barn.', nextTease: 'What will we find next?' },
        ...(stepType === 'greeting' ? { teachingWord: { text: 'BARN', style: 'wordPill', position: 'objectSide', highlightMode: 'wholeWord' } } : {}),
        ...(stepType === 'repeat' ? { interaction: { template: 'safeSpeaking', funPattern: 'copyMyMove', maxAttempts: 2 } } : {}),
        ...(stepType === 'celebrate' ? { terminal: true } : {}),
      },
    }));
  }
  // Attach the whole verified image set, including the poses required by authored steps.
  for (const asset of sourceAssets) {
    await adminApi(page, 'POST', `/lessons/${lesson.id}/assets`, { profile: 'espTft', sourceAssetId: asset.assetId });
  }
  const attached = await adminApi(page, 'GET', `/lessons/${lesson.id}/assets?profile=espTft`);
  expect(attached.profiles, 'fixture must have an actual espTft bundle before preview/publish').toContain('espTft');
  expect(attached.assets).toEqual(expect.arrayContaining(sourceAssets.map(asset => expect.objectContaining({
    assetKey: asset.assetKey, sha256: asset.sha256, bytes: asset.bytes,
    mediaType: asset.mediaType, width: asset.width, height: asset.height,
  }))));
  return { course, lesson, steps, stepKey: steps[0].step_key };
}

async function bindLegacyVisuals(page, lessonId, backgroundVersionId, objectVersionId) {
  const snapshot = await adminApi(page, 'GET', `/lessons/${lessonId}/visuals`);
  return adminApi(page, 'PUT', `/lessons/${lessonId}/visuals`, {
    expectedChecksum: snapshot.checksum,
    expectedVisualChecksum: snapshot.visualChecksum,
    backgroundAssetVersionId: backgroundVersionId,
    objectAssetVersionId: objectVersionId,
  });
}

module.exports = { verifiedCatalogImage, copyVisualVersion, createLegacyFixtureCourse, bindLegacyVisuals };
