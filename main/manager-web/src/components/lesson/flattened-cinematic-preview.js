export const CINEMATIC_LAYER_IDS = ['background', 'teachingObject', 'robotOverlay'];
export const CINEMATIC_SYNC_TOLERANCE_SEC = 0.08;

export function flattenableLayers(projection) {
  const layers = projection && Array.isArray(projection.layers) ? projection.layers : [];
  return layers
    .filter((layer) => layer
      && CINEMATIC_LAYER_IDS.includes(layer.id)
      && layer.visible
      && layer.mediaType === 'video/mp4'
      && Boolean(layer.src))
    .sort((left, right) => Number(left.z) - Number(right.z));
}

export function isFlattenableProjection(projection) {
  return cinematicProjectionStatus(projection).flattenable;
}

export function cinematicProjectionStatus(projection) {
  const candidate = Boolean(projection && projection.manifestVersion === 'teebot-lesson-renderer.v3');
  if (!candidate) {
    return { candidate: false, flattenable: false, missingLayerIds: [], unsupportedLayerIds: [] };
  }

  const layers = projection && Array.isArray(projection.layers) ? projection.layers : [];
  const missingLayerIds = [];
  const unsupportedLayerIds = [];
  CINEMATIC_LAYER_IDS.forEach((id) => {
    const layer = layers.find((candidateLayer) => candidateLayer && candidateLayer.id === id);
    if (!layer || !layer.visible || !layer.src) {
      missingLayerIds.push(id);
    } else if (layer.mediaType !== 'video/mp4') {
      unsupportedLayerIds.push(id);
    }
  });

  return {
    candidate,
    flattenable: missingLayerIds.length === 0 && unsupportedLayerIds.length === 0,
    missingLayerIds,
    unsupportedLayerIds
  };
}

export function chooseMasterLayer(layers) {
  if (!Array.isArray(layers) || layers.length === 0) return null;
  return layers.find((layer) => layer.id === 'background') || layers[0];
}

export function shouldResyncVideo(masterTime, videoTime, tolerance = CINEMATIC_SYNC_TOLERANCE_SEC) {
  if (!Number.isFinite(masterTime) || !Number.isFinite(videoTime)) return false;
  return Math.abs(masterTime - videoTime) > tolerance;
}

export function objectFitRect(sourceWidth, sourceHeight, bounds) {
  const target = bounds || {};
  const dx = Number(target.x) || 0;
  const dy = Number(target.y) || 0;
  const dw = Number(target.width) || 0;
  const dh = Number(target.height) || 0;
  const fit = target.fit || 'fill';
  const sourceAspect = sourceWidth / sourceHeight;
  const targetAspect = dw / dh;

  if (fit === 'contain') {
    const scale = Math.min(dw / sourceWidth, dh / sourceHeight);
    const fittedWidth = sourceWidth * scale;
    const fittedHeight = sourceHeight * scale;
    return {
      sx: 0,
      sy: 0,
      sw: sourceWidth,
      sh: sourceHeight,
      dx: dx + ((dw - fittedWidth) / 2),
      dy: dy + ((dh - fittedHeight) / 2),
      dw: fittedWidth,
      dh: fittedHeight
    };
  }

  if (fit === 'cover') {
    const cropWidth = sourceAspect > targetAspect ? sourceHeight * targetAspect : sourceWidth;
    const cropHeight = sourceAspect > targetAspect ? sourceHeight : sourceWidth / targetAspect;
    return {
      sx: (sourceWidth - cropWidth) / 2,
      sy: (sourceHeight - cropHeight) / 2,
      sw: cropWidth,
      sh: cropHeight,
      dx,
      dy,
      dw,
      dh
    };
  }

  return { sx: 0, sy: 0, sw: sourceWidth, sh: sourceHeight, dx, dy, dw, dh };
}

export function applyChromaKey(data, chromaKey) {
  const color = chromaKey && chromaKey.color;
  if (!data || !color || ![color.r, color.g, color.b].every(Number.isFinite)) return data;

  const tolerance = Math.max(0, Number(chromaKey.tolerance) || 0);
  const feather = Math.max(0, Number(chromaKey.feather) || 0);
  for (let index = 0; index < data.length; index += 4) {
    const distance = Math.max(
      Math.abs(data[index] - color.r),
      Math.abs(data[index + 1] - color.g),
      Math.abs(data[index + 2] - color.b)
    );
    if (distance <= tolerance) {
      data[index + 3] = 0;
    } else if (feather > 0 && distance < tolerance + feather) {
      data[index + 3] *= (distance - tolerance) / feather;
    }
  }
  return data;
}
