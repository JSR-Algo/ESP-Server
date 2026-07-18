const SCREEN = Object.freeze({ width: 480, height: 320 });
const PHASES = Object.freeze([
  { name: 'hidden', durationMs: 100 }, { name: 'flyIn', durationMs: 1200 }, { name: 'landFar', durationMs: 700 },
  { name: 'settle', durationMs: 350 }, { name: 'walkToward', durationMs: 1800 },
  { name: 'arriveNear', durationMs: 250 }, { name: 'greetIdle', durationMs: 650 },
  { name: 'revealTeachingContent', durationMs: 100 },
]);

const shared = {
  safeZones: [{ left: 18, top: 238, width: 444, height: 68 }],
  wordPill: { left: 150, top: 244, width: 180, height: 42 },
  prompt: { left: 26, top: 18, width: 428, height: 48 },
  progress: { left: 390, top: 286, width: 64, height: 16 },
};

const LAYOUT_PRESETS = Object.freeze({
  centerRoad: Object.freeze({ ...shared, teachingObject: { left: 40, top: 104, width: 124, height: 124 }, entry: { left: 400, top: 22 }, land: { left: 284, top: 116 }, arrive: { left: 184, top: 184, width: 112, height: 56 }, walkCorridor: { left: 178, top: 112, width: 154, height: 132 } }),
  leftApproach: Object.freeze({ ...shared, teachingObject: { left: 240, top: 104, width: 124, height: 124 }, entry: { left: 24, top: 22 }, land: { left: 104, top: 116 }, arrive: { left: 42, top: 148, width: 108, height: 92 }, walkCorridor: { left: 38, top: 112, width: 150, height: 132 } }),
  rightApproach: Object.freeze({ ...shared, teachingObject: { left: 116, top: 104, width: 124, height: 124 }, entry: { left: 410, top: 22 }, land: { left: 326, top: 116 }, arrive: { left: 330, top: 148, width: 108, height: 92 }, walkCorridor: { left: 292, top: 112, width: 150, height: 132 } }),
});

function rectOverlaps(a, b) {
  return a.left < b.left + b.width && a.left + a.width > b.left
    && a.top < b.top + b.height && a.top + a.height > b.top;
}

function validateProjection(projection) {
  return Boolean(projection)
    && projection.templateId === 'tvideoFlyWalk'
    && projection.templateVersion === 1
    && projection.geometryVersion === 1
    && projection.revealPhase === 'revealTeachingContent'
    && projection.fallbackPolicy === 'snapToArriveNearAndReveal'
    && Boolean(LAYOUT_PRESETS[projection.layoutPreset])
    && JSON.stringify(projection.phases || []) === JSON.stringify(PHASES);
}

function phaseRobotRect(layout, phaseName, elapsedMs, durationMs) {
  if (phaseName === 'hidden' || phaseName === 'flyIn') return layout.entry;
  if (phaseName === 'landFar' || phaseName === 'settle') return layout.land;
  if (phaseName === 'walkToward') {
    const ratio = Math.min(1, Math.max(0, elapsedMs / durationMs));
    return {
      left: layout.land.left + (layout.arrive.left - layout.land.left) * ratio,
      top: layout.land.top + (layout.arrive.top - layout.land.top) * ratio,
      width: layout.arrive.width,
      height: layout.arrive.height,
    };
  }
  return layout.arrive;
}

module.exports = { SCREEN, PHASES, LAYOUT_PRESETS, rectOverlaps, validateProjection, phaseRobotRect };
