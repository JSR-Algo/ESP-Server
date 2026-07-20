const CANONICAL_DEMO_ROOT = '/tvideo-demo';

function safeAssetPath(value) {
  if (typeof value !== 'string' || !value || value.startsWith('/') || value.includes('\\')) {
    throw new Error('canonical demo metadata is invalid');
  }
  const segments = value.split('/');
  if (segments.some((segment) => !segment || segment === '.' || segment === '..')) {
    throw new Error('canonical demo metadata is invalid');
  }
  return value;
}

export async function loadCanonicalDemoContext(demoSource, fetchImpl = window.fetch.bind(window)) {
  if (demoSource !== 'tvideo-raw-code') return null;
  const response = await fetchImpl(`${CANONICAL_DEMO_ROOT}/asset-manifest.json`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`canonical demo metadata returned ${response.status}`);
  const manifest = await response.json();
  const preview = manifest && manifest.adminPreview;
  if (!manifest || manifest.sourceFolder !== 'robot/tvideo-raw-code' || !Array.isArray(manifest.espTft)
    || !preview || preview.mediaType !== 'video/mp4') {
    throw new Error('canonical demo metadata is invalid');
  }
  const previewPath = safeAssetPath(preview.path);
  const posterPath = preview.posterPath ? safeAssetPath(preview.posterPath) : '';
  return {
    sourceFolder: manifest.sourceFolder,
    sourceAssets: manifest.espTft.map((asset) => {
      if (typeof asset.sourcePath !== 'string' || !/^[0-9a-f]{64}$/.test(asset.sourceSha256 || '')) {
        throw new Error('canonical demo metadata is invalid');
      }
      return {
        sourcePath: asset.sourcePath,
        sourceSha256: asset.sourceSha256,
        url: `${CANONICAL_DEMO_ROOT}/${safeAssetPath(asset.sourceCopyPath)}`,
      };
    }),
    adminPreview: {
      ...preview,
      url: `${CANONICAL_DEMO_ROOT}/${previewPath}`,
      posterUrl: posterPath ? `${CANONICAL_DEMO_ROOT}/${posterPath}` : '',
    },
  };
}
