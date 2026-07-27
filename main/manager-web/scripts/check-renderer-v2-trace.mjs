import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { isDeepStrictEqual } from 'node:util';

const managerRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixturePath = path.join(managerRoot, 'tests/fixtures/renderer-v2-manifest.json');
const firmwareRoot = process.env.FIRMWARE_ROOT;
if (!firmwareRoot) {
  console.error('renderer v2 trace parity requires explicit FIRMWARE_ROOT pointing at the firmware checkout');
  process.exit(2);
}

const projectionSource = await readFile(path.join(managerRoot, 'src/components/lesson/robot-preview-projection.js'), 'utf8');
const projection = await import(`data:text/javascript;base64,${Buffer.from(projectionSource).toString('base64')}`);
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'));
const firmwareFixture = process.env.FIRMWARE_TRACE_FIXTURE || fixturePath;

const PHASES = ['hidden', 'flyIn', 'landFar', 'settle', 'walkToward', 'arriveNear', 'greetIdle', 'revealTeachingContent'];
const PRESETS = Object.freeze({
  centerRoad: Object.freeze({
    entry: Object.freeze({ x: 400, y: 22, width: 96, height: 84 }),
    land: Object.freeze({ x: 284, y: 116, width: 96, height: 84 }),
    arrived: Object.freeze({ x: 184, y: 184, width: 112, height: 56 })
  }),
  leftApproach: Object.freeze({
    entry: Object.freeze({ x: 24, y: 22, width: 96, height: 84 }),
    land: Object.freeze({ x: 104, y: 116, width: 96, height: 84 }),
    arrived: Object.freeze({ x: 42, y: 148, width: 108, height: 92 })
  }),
  rightApproach: Object.freeze({
    entry: Object.freeze({ x: 410, y: 22, width: 96, height: 84 }),
    land: Object.freeze({ x: 326, y: 116, width: 96, height: 84 }),
    arrived: Object.freeze({ x: 330, y: 148, width: 108, height: 92 })
  })
});

function interpolate(from, to, elapsed, duration) {
  return from + Math.trunc(((to - from) * elapsed) / duration);
}

function interpolateRect(from, to, elapsed, duration) {
  return {
    x: interpolate(from.x, to.x, elapsed, duration),
    y: interpolate(from.y, to.y, elapsed, duration),
    width: interpolate(from.width, to.width, elapsed, duration),
    height: interpolate(from.height, to.height, elapsed, duration)
  };
}

function createAnimation(projectionContract) {
  assert.equal(projectionContract.templateId, 'tvideoFlyWalk');
  assert.equal(projectionContract.templateVersion, 1);
  assert.equal(projectionContract.geometryVersion, 1);
  assert.deepEqual(projectionContract.phases.map(({ name }) => name), PHASES);
  const preset = PRESETS[projectionContract.layoutPreset];
  assert.ok(preset, `unsupported admin trace layout preset ${projectionContract.layoutPreset}`);
  let phaseIndex = 0;
  let elapsed = 0;
  return {
    advance(delta) {
      elapsed += delta;
      while (phaseIndex < PHASES.length - 1 && elapsed >= projectionContract.phases[phaseIndex].durationMs) {
        elapsed -= projectionContract.phases[phaseIndex].durationMs;
        phaseIndex += 1;
      }
      if (phaseIndex === PHASES.length - 1) elapsed = 0;
    },
    snapshot() {
      const phase = PHASES[phaseIndex];
      let bounds = preset.arrived;
      if (phase === 'hidden') bounds = preset.entry;
      if (phase === 'flyIn') bounds = interpolateRect(preset.entry, preset.land, elapsed, projectionContract.phases[1].durationMs);
      if (phase === 'landFar' || phase === 'settle') bounds = preset.land;
      if (phase === 'walkToward') bounds = interpolateRect(preset.land, preset.arrived, elapsed, projectionContract.phases[4].durationMs);
      return { phase, bounds: { ...bounds }, contentVisible: phase === 'revealTeachingContent' };
    },
    arrived() {
      return { phase: 'revealTeachingContent', bounds: { ...preset.arrived }, contentVisible: true };
    }
  };
}

function row(boundary, snapshot, overlay, generation, state, motion, accepted, degraded, reason) {
  return {
    boundary,
    phase: snapshot.phase,
    bounds: snapshot.bounds,
    contentVisible: snapshot.contentVisible,
    overlay,
    generation,
    state,
    motion,
    ack: { accepted, degraded, reason }
  };
}

function adminTrace(manifest) {
  assert.equal(manifest.schemaVersion, 'renderer-v2-trace.v1');
  assert.equal(manifest.manifestVersion, projection.RENDERER_V2_MANIFEST_VERSION);
  assert.equal(manifest.protocolVersion, projection.RENDERER_V2_MANIFEST_VERSION);
  const step = manifest.steps[0];
  const traceConfig = manifest.trace;
  const rendered = projection.projectEspTftPreview(manifest, 0, traceConfig.state);
  const overlay = rendered.layers.find((layer) => layer.id === 'robotOverlay')?.overlayKey || null;
  assert.equal(rendered.visualState, traceConfig.state);
  assert.equal(rendered.motionPreset, traceConfig.motion);
  assert.equal(overlay, traceConfig.overlay);

  const animation = createAnimation(step.templateProjection);
  const trace = traceConfig.boundaries.map((boundary) => {
    animation.advance(boundary.advanceMs);
    return row(boundary.name, animation.snapshot(), overlay, traceConfig.visualGeneration,
      rendered.visualState, rendered.motionPreset, true, false, null);
  });

  for (const fallback of traceConfig.fallbacks) {
    const unsupported = fallback.mode === 'unsupportedContract';
    trace.push(row(fallback.name, animation.arrived(),
      fallback.mode === 'missingOverlay' || unsupported ? null : overlay,
      traceConfig.visualGeneration, rendered.visualState, rendered.motionPreset,
      !unsupported, !unsupported, fallback.mode));
  }
  return {
    schemaVersion: manifest.schemaVersion,
    manifestVersion: projection.RENDERER_V2_MANIFEST_VERSION,
    protocolVersion: projection.RENDERER_V2_MANIFEST_VERSION,
    trace
  };
}

function collectDiffs(expected, actual, prefix = '$', output = []) {
  if (isDeepStrictEqual(expected, actual)) return output;
  if (!expected || !actual || typeof expected !== 'object' || typeof actual !== 'object') {
    output.push(`${prefix}: admin=${JSON.stringify(expected)} firmware=${JSON.stringify(actual)}`);
    return output;
  }
  const keys = new Set([...Object.keys(expected), ...Object.keys(actual)]);
  for (const key of [...keys].sort()) collectDiffs(expected[key], actual[key], `${prefix}.${key}`, output);
  return output;
}

const expected = adminTrace(fixture);
const firmwareScript = path.join(firmwareRoot, 'scripts/run_host_native_lesson_renderer_trace_test.sh');
const result = spawnSync(firmwareScript, [firmwareFixture], { encoding: 'utf8' });
if (result.error) {
  console.error(`could not execute firmware trace script ${firmwareScript}: ${result.error.message}`);
  process.exit(2);
}
if (result.status !== 0) {
  process.stderr.write(result.stderr || result.stdout || `firmware trace exporter exited ${result.status}\n`);
  process.exit(result.status || 1);
}

let actual;
try {
  actual = JSON.parse(result.stdout);
} catch (error) {
  console.error(`firmware trace stdout is not one JSON document: ${error.message}`);
  console.error(result.stdout);
  process.exit(1);
}
const differences = collectDiffs(expected, actual);
if (differences.length > 0) {
  console.error(`renderer v2 trace parity failed with ${differences.length} difference(s):`);
  differences.slice(0, 40).forEach((difference) => console.error(`- ${difference}`));
  process.exit(1);
}

process.stdout.write(`${JSON.stringify(expected)}\n`);
