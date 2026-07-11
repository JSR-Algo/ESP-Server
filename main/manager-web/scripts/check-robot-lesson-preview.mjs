import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const source = await readFile(new URL('src/components/lesson/robot-preview-projection.js', root), 'utf8');
const projection = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

for (const minutes of [3, 5, 8]) {
  const fixture = JSON.parse(await readFile(new URL(`tests/fixtures/robot-preview-${minutes}m.json`, root), 'utf8'));
  const golden = JSON.parse(await readFile(new URL(`tests/golden/robot-preview-${minutes}m.json`, root), 'utf8'));
  const rendered = projection.projectEspTftPreview(fixture.manifest, fixture.stepIndex, fixture.path);
  const actual = JSON.parse(JSON.stringify({
    stage: rendered.stage,
    safeZones: rendered.safeZones,
    step: rendered.step,
    path: rendered.path,
    layers: rendered.layers.map((layer) => ({
      id: layer.id,
      z: layer.z,
      bounds: layer.bounds,
      visible: layer.visible,
      text: layer.text,
      active: layer.active,
      total: layer.total
    })),
    timeline: rendered.timeline
  }));
  assert.deepEqual(actual, golden, `${minutes}-minute projection drifted from its golden layout`);
}

const fixture = JSON.parse(await readFile(new URL('tests/fixtures/robot-preview-5m.json', root), 'utf8'));
const paths = ['correct', 'nearMiss', 'incorrect', 'silence', 'sttUnavailable', 'missingOptionalVisual'];
for (const path of paths) {
  const result = projection.projectEspTftPreview(fixture.manifest, fixture.stepIndex, path);
  assert.equal(result.path, path);
  assert.ok(result.timeline.some((item) => item.label.startsWith('Slave command: ')), `${path} lacks slave command timeline`);
}

const hostile = structuredClone(fixture.manifest);
hostile.steps[0].scene.backgroundScene.video = { src: 'https://bad.test/movie.mp4' };
hostile.steps[0].scene.robotOverlay.asset.src = 'https://bad.test/raw-servo.GIF';
hostile.steps[0].rawServo = { angle: 180 };
const warnings = projection.findForbiddenFirmwareCapabilities(hostile);
assert.ok(warnings.some((warning) => warning.includes('background video')));
assert.ok(warnings.some((warning) => warning.includes('GIF')));
assert.ok(warnings.some((warning) => warning.includes('raw servo')));

const malformed = projection.projectEspTftPreview({ profile: 'mobile', steps: [{ scene: null }] }, 99, 'inject-success');
assert.equal(malformed.path, 'correct');
assert.equal(malformed.stage.width, 480);
assert.equal(malformed.stage.height, 320);
assert.ok(malformed.warnings.some((warning) => warning.includes('Unsupported profile')));
assert.equal(projection.projectEspTftPreview({}, -4, 'silence').layers.length, 6);

console.log('robot lesson preview projection: golden 3/5/8 layouts and six response paths PASS');
