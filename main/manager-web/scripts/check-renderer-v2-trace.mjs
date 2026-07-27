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

let projectionSource = await readFile(path.join(managerRoot, 'src/components/lesson/robot-preview-projection.js'), 'utf8');
if (process.env.RENDERER_V2_TRACE_MUTATE_GEOMETRY === '1') {
  const mutated = projectionSource.replace('entry: Object.freeze({ x: 400, y: 22', 'entry: Object.freeze({ x: 401, y: 22');
  if (mutated === projectionSource) {
    console.error('renderer v2 mutation sentinel could not locate production centerRoad entry geometry');
    process.exit(2);
  }
  projectionSource = mutated;
}
const projection = await import(`data:text/javascript;base64,${Buffer.from(projectionSource).toString('base64')}`);
const fixture = JSON.parse(await readFile(fixturePath, 'utf8'));
const firmwareFixture = process.env.FIRMWARE_TRACE_FIXTURE || fixturePath;

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

  const openingTrace = projection.projectRendererV2OpeningTrace(step.templateProjection, traceConfig.boundaries);
  assert.equal(openingTrace.length, traceConfig.boundaries.length, 'production opening projection rejected trace fixture');
  const trace = openingTrace.map((snapshot) => {
    return row(snapshot.boundary, snapshot, overlay, traceConfig.visualGeneration,
      rendered.visualState, rendered.motionPreset, true, false, null);
  });
  const arrived = openingTrace[openingTrace.length - 1];

  for (const fallback of traceConfig.fallbacks) {
    const unsupported = fallback.mode === 'unsupportedContract';
    trace.push(row(fallback.name, arrived,
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

if (process.env.RENDERER_V2_TRACE_MUTATE_GEOMETRY !== '1') {
  const mutation = spawnSync(process.execPath, [fileURLToPath(import.meta.url)], {
    encoding: 'utf8',
    env: { ...process.env, RENDERER_V2_TRACE_MUTATE_GEOMETRY: '1' }
  });
  if (mutation.status !== 1 || !mutation.stderr.includes('$.trace.0.bounds.x: admin=401 firmware=400')) {
    console.error('renderer v2 non-tautology sentinel failed: production geometry mutation was not detected');
    process.stderr.write(mutation.stderr || mutation.stdout);
    process.exit(1);
  }
}

process.stdout.write(`${JSON.stringify(expected)}\n`);
