import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const source = await readFile(new URL('src/components/lesson/robot-preview-projection.js', root), 'utf8');
const projection = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

assert.equal(projection.RENDERER_V2_MANIFEST_VERSION, 'teebot-lesson-renderer.v2');
assert.deepEqual(projection.VISUAL_STATES, ['teach', 'listen', 'thinking', 'correct', 'nearMiss', 'incorrect', 'retry', 'celebrate', 'completion']);
assert.deepEqual(projection.DEGRADED_REASONS, ['missingOverlay', 'animationStartFailed', 'phaseTimeout', 'reducedMotion', 'unsupportedContract', 'assetIdentityMismatch', 'insufficientHeap']);

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
const paths = ['correct', 'nearMiss', 'incorrect', 'retry', 'timeout', 'braveTry', 'completion', 'silence', 'sttUnavailable', 'missingOptionalVisual'];
for (const path of paths) {
  const result = projection.projectEspTftPreview(fixture.manifest, fixture.stepIndex, path);
  assert.equal(result.path, path);
  assert.ok(result.timeline.some((item) => item.label.startsWith('Slave command: ')), `${path} lacks slave command timeline`);
}

const servedManifest = structuredClone(fixture.manifest);
delete servedManifest.steps[0].scene.teachingObject.primaryWord;
servedManifest.steps[0].teachingWord = { text: 'APPLE', style: 'wordPill' };
servedManifest.steps[0].motion = {
  present: 'presentLeft',
  listen: 'listen',
  correct: 'goodbye',
  nearMiss: 'thinking',
  incorrect: 'presentRight'
};
delete servedManifest.steps[0].responsePaths;
delete servedManifest.steps[0].motionPreset;

const servedCorrect = projection.projectEspTftPreview(servedManifest, 0, 'correct');
assert.equal(servedCorrect.layers.find((layer) => layer.id === 'wordPill').text, 'APPLE');
assert.ok(servedCorrect.timeline.some((item) => item.label === 'Slave command: goodbye'));
assert.ok(projection.projectEspTftPreview(servedManifest, 0, 'nearMiss').timeline.some((item) => item.label === 'Slave command: thinking'));
assert.ok(projection.projectEspTftPreview(servedManifest, 0, 'incorrect').timeline.some((item) => item.label === 'Slave command: presentRight'));
servedManifest.steps[0].entrance = 'flyIn';
assert.equal(projection.projectEspTftPreview(servedManifest, 0, 'correct').entrance, 'flyIn', 'renderer-v1 step entrances must remain compatible');

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

const rendererV2 = structuredClone(fixture.manifest);
rendererV2.manifestVersion = 'teebot-lesson-renderer.v2';
rendererV2.physicalMotionOwner = 'server';
rendererV2.rendererCapabilities = ['teebot-lesson-renderer.v2'];
rendererV2.openingEntrance = {
  template: 'tvideoFlyWalk',
  preset: 'flyLandWalkGreet',
  policy: 'oncePerLessonSession',
  layoutPreset: 'centerRoad',
  phases: ['hidden', 'flyIn', 'landFar', 'settle', 'walkToward', 'arriveNear', 'greetIdle', 'revealTeachingContent'],
  fallback: 'staticGreet'
};
rendererV2.steps.forEach((step) => { step.entrance = 'none'; });
rendererV2.steps[0].templateProjection = {
  templateId: 'tvideoFlyWalk',
  templateVersion: 1,
  layoutPreset: 'centerRoad',
  geometryVersion: 1,
  phases: [
    { name: 'hidden', durationMs: 100 }, { name: 'flyIn', durationMs: 1200 },
    { name: 'landFar', durationMs: 700 }, { name: 'settle', durationMs: 350 },
    { name: 'walkToward', durationMs: 1800 }, { name: 'arriveNear', durationMs: 250 },
    { name: 'greetIdle', durationMs: 650 }, { name: 'revealTeachingContent', durationMs: 100 }
  ]
};
rendererV2.steps[0].visualStates = Object.fromEntries(projection.VISUAL_STATES.map((state) => [state, { prompt: `${state} prompt`, motionPreset: `${state} motion`, overlayKey: `${state} overlay` }]));
rendererV2.steps.push({ ...structuredClone(rendererV2.steps[0]), id: 'second-step', entrance: 'none' });

const openingTrace = projection.projectRendererV2OpeningTrace(rendererV2.steps[0].templateProjection, [
  { name: 'hidden', advanceMs: 0 },
  { name: 'flyIn', advanceMs: 100 },
  { name: 'walkTowardMidpoint', advanceMs: 3150 }
]);
assert.deepEqual(openingTrace.map(({ boundary, phase }) => ({ boundary, phase })), [
  { boundary: 'hidden', phase: 'hidden' },
  { boundary: 'flyIn', phase: 'flyIn' },
  { boundary: 'walkTowardMidpoint', phase: 'walkToward' }
]);
assert.deepEqual(openingTrace[2].bounds, { x: 234, y: 150, width: 104, height: 70 });

for (const state of projection.VISUAL_STATES) {
  const rendered = projection.projectEspTftPreview(rendererV2, 0, state);
  assert.equal(rendered.visualState, state);
  assert.equal(rendered.motionPreset, `${state} motion`);
  assert.equal(rendered.timeline[1].label, `Server motion: ${state} motion`);
  assert.equal(rendered.layers.find((layer) => layer.id === 'prompt').text, `${state} prompt`);
}

const exactV2 = projection.projectEspTftPreview(rendererV2, 0, 'teach');
assert.equal(exactV2.manifestVersion, 'teebot-lesson-renderer.v2');
assert.equal(exactV2.rendererLabel, 'Renderer v2');
assert.equal(exactV2.physicalMotionOwner, 'server');
assert.equal(exactV2.openingEntranceCount, 1);
assert.equal(exactV2.openingEntrance.policy, 'oncePerLessonSession');
assert.equal(exactV2.capability.supported, true);
assert.equal(exactV2.warnings.length, 0);
assert.equal(projection.projectEspTftPreview(rendererV2, 1, 'teach').entrance, 'none', 'renderer-v2 entrance must not replay after step zero');

const realShapeManifest = structuredClone(rendererV2);
delete realShapeManifest.physicalMotionOwner;
delete realShapeManifest.rendererCapabilities;
const realShapeMetadata = {
  checksum: 'server-checksum',
  etag: 'server-etag',
  features: {
    renderer: ['teebot-lesson-renderer.v1', 'teebot-lesson-renderer.v2'],
    lessonRendererV2: { openingEntrance: true, visualStateEvents: true, physicalMotionOwner: 'server', singleSpriteEntrance: true }
  }
};
const realShapeProjection = projection.projectEspTftPreview(realShapeManifest, 0, 'teach', null, realShapeMetadata);
assert.equal(realShapeProjection.capability.supported, true);
assert.equal(realShapeProjection.physicalMotionOwner, 'server');
assert.equal(realShapeProjection.warnings.length, 0);

const runtimeControlProjection = projection.projectEspTftPreview(realShapeManifest, 0, 'teach', null, {
  features: { renderer: ['teebot-lesson-renderer.v2'] },
  body: { runtimeControls: { physicalMotionOwner: 'server' } }
});
assert.equal(runtimeControlProjection.capability.supported, true);
assert.equal(runtimeControlProjection.physicalMotionOwner, 'server');

const unreportedProjection = projection.projectEspTftPreview(realShapeManifest, 0, 'teach');
assert.equal(unreportedProjection.capability.supported, null);
assert.equal(unreportedProjection.physicalMotionOwner, null);
assert.equal(unreportedProjection.warnings.some((warning) => warning.includes('renderer-v1')), false);
assert.equal(unreportedProjection.warnings.some((warning) => warning.includes('physicalMotionOwner')), false);

for (const reason of projection.DEGRADED_REASONS) {
  const degraded = projection.projectEspTftPreview(rendererV2, 0, 'teach', reason);
  assert.equal(degraded.degraded.reason, reason);
  assert.ok(degraded.degraded.fallback);
  assert.equal(degraded.layers.find((layer) => layer.id === 'prompt').visible, true);
  assert.equal(degraded.layers.find((layer) => layer.id === 'background').visible, true);
}
assert.equal(projection.projectEspTftPreview(rendererV2, 0, 'teach', 'missingOverlay').layers.find((layer) => layer.id === 'robotOverlay').visible, false);
assert.equal(projection.projectEspTftPreview(rendererV2, 0, 'teach', 'insufficientHeap').layers.find((layer) => layer.id === 'robotOverlay').visible, false);
assert.equal(projection.projectEspTftPreview(rendererV2, 0, 'teach', 'animationStartFailed').layers.find((layer) => layer.id === 'robotOverlay').visible, true);

const v1Only = structuredClone(rendererV2);
v1Only.rendererCapabilities = ['teebot-lesson-renderer.v1'];
assert.ok(projection.projectEspTftPreview(v1Only).warnings.some((warning) => warning.includes('renderer-v1')));

const duplicateOpening = structuredClone(rendererV2);
duplicateOpening.steps.push({ ...structuredClone(duplicateOpening.steps[0]), id: 'second-step', entrance: 'flyIn' });
assert.ok(projection.projectEspTftPreview(duplicateOpening).warnings.some((warning) => warning.includes('exactly one opening entrance')));

console.log('robot lesson preview projection: golden layouts, renderer-v2 states, capability, and degraded fallbacks PASS');
