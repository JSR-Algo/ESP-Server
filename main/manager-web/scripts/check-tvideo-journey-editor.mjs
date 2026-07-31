import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const read = (path) => readFile(new URL(path, root), 'utf8');

const helperSource = await read('src/components/lesson/tvideo-journey.js');
const helper = await import(`data:text/javascript;base64,${Buffer.from(helperSource).toString('base64')}`);

assert.deepEqual(helper.CONVERSATION_BRANCHES, [
  'target', 'meaning_vi', 'related', 'silence', 'uncertain',
  'retry_level_1', 'retry_level_2', 'retry_level_3',
]);

assert.deepEqual(helper.clampPoint({ x: -2, y: 4 }), { x: 0, y: 1 });
assert.deepEqual(helper.clampSafeZone({ x: 0.8, y: -1, width: 0.8, height: 2 }), {
  x: 0.8, y: 0, width: 0.2, height: 1,
});
assert.deepEqual(
  helper.clampWalkKeyframes([
    { timeMs: 5000, x: 2, y: -1, scale: 3 },
    { timeMs: 0, x: 0.5, y: 0.5, scale: 0 },
  ]),
  [
    { timeMs: 0, x: 0.5, y: 0.5, scale: 0.01 },
    { timeMs: 5000, x: 1, y: 0, scale: 2 },
  ],
);
assert.equal(helper.quantizeClockMs(149), 100);
assert.equal(helper.quantizeClockMs(150), 200);

const steps = [
  {
    stepKey: 'barn', targetWord: 'barn', vietnameseMeanings: ['nhà kho'],
    relatedConcepts: ['farm building'], questionSeeds: ['Where is the barn?'],
    teachingCopy: { intro: 'This is a barn.', explanation: 'A farm building.', prompt: 'Say barn.' },
    expectedAnswer: 'barn', progress: { index: 1, count: 2 },
  },
  {
    stepKey: 'hay', targetWord: 'hay', vietnameseMeanings: ['cỏ khô'],
    relatedConcepts: ['animal food'], questionSeeds: ['What is hay?'],
    teachingCopy: { intro: 'This is hay.', explanation: 'Dried grass.', prompt: 'Say hay.' },
    expectedAnswer: 'hay', progress: { index: 2, count: 2 },
  },
];
for (const branch of helper.CONVERSATION_BRANCHES) {
  const result = helper.simulateConversationBranch(branch, steps[0], steps[1]);
  assert.equal(result.branch, branch);
  assert.ok(result.inputClass && result.nextIntent && result.cueId && result.effect);
  assert.doesNotMatch(JSON.stringify(result), /wrong/i);
}

const required = helper.requiredCueIds(steps);
assert.equal(required.length, 19);
assert.deepEqual(required.slice(0, 3), ['barn-opening', 'barn-greet', 'barn-teach']);
assert.equal(required.at(-1), 'hay-celebrate');

const ready = required.map((cueId) => ({ cueId, state: 'ready', sourceRevision: 4, output: { url: `/preview/${cueId}` } }));
assert.equal(helper.exactCueReadiness(ready, required, 4).ready, true);
assert.equal(helper.exactCueReadiness(ready.slice(1), required, 4).issues[0].code, 'MISSING_CUES');
assert.ok(helper.exactCueReadiness([...ready, { ...ready[0] }], required, 4).issues.some((issue) => issue.code === 'DUPLICATE_CUES'));
assert.ok(helper.exactCueReadiness([...ready, { cueId: 'extra', state: 'ready', sourceRevision: 4, output: {} }], required, 4).issues.some((issue) => issue.code === 'EXTRA_CUES'));
assert.ok(helper.exactCueReadiness(ready.map((row, index) => index ? row : { ...row, state: 'stale' }), required, 4).issues.some((issue) => issue.code === 'STALE_CUES'));

const stateA = helper.deterministicPreviewState({ clockMs: 2314, cueId: 'barn-celebrate', seed: 0x54424f54, pieces: 8 });
const stateB = helper.deterministicPreviewState({ clockMs: 2314, cueId: 'barn-celebrate', seed: 0x54424f54, pieces: 8 });
assert.deepEqual(stateA, stateB);
assert.equal(stateA.clockMs % 100, 0);

const [editor, conversation, robot, lessonEditor, api, pkg, en, vi] = await Promise.all([
  read('src/components/lesson/TVideoJourneyEditor.vue'),
  read('src/components/lesson/TVideoConversationPreview.vue'),
  read('src/components/lesson/TVideoRobotPreview.vue'),
  read('src/views/LessonEditor.vue'),
  read('src/apis/module/lesson.js'),
  read('package.json'),
  read('src/i18n/en.js'),
  read('src/i18n/vi.js'),
]);

for (const label of ['3 Sources', 'Journey Path', 'Conversation', 'Robot Flattened']) {
  assert.ok(editor.includes(label), `missing exact preview tab ${label}`);
}
for (const branch of helper.CONVERSATION_BRANCHES) assert.ok(conversation.includes(branch), `missing branch ${branch}`);
for (const marker of ['role="tablist"', 'role="tab"', 'aria-selected', '@keydown', 'touch-action', '@media (prefers-reduced-motion: reduce)']) {
  assert.ok(editor.includes(marker) || robot.includes(marker), `missing accessibility/responsive marker ${marker}`);
}
assert.ok(editor.includes('CinematicLayerPicker'));
assert.ok(editor.includes('assetVersionId'));
assert.doesNotMatch(editor, /durationMs[^\n]*(?:el-input|el-slider)|easing[^\n]*(?:el-input|el-slider)|confettiPieces[^\n]*(?:el-input|el-slider)/i);
assert.ok(robot.includes('width="480"') && robot.includes('height="320"'));
assert.ok(robot.includes('100'));
assert.ok(robot.includes('preview'));

for (const route of [
  '/lessons/authoring/presets/tvideoJourney/1',
  '/lessons/authoring/${lessonId}/tvideo-journey',
]) assert.ok(api.includes(route), `missing API route ${route}`);
assert.ok(api.includes("method: 'PUT'"));
assert.ok(api.includes('normalizeTVideoJourneyResponse'));

assert.ok(lessonEditor.includes('<TVideoJourneyEditor'));
assert.ok(lessonEditor.includes("flattenedDerivativeManifestVersion === 'teebot-lesson-renderer.v4'"));
assert.match(lessonEditor, /canPublishCurrentProof\(\)[\s\S]*tvideoJourneyPublishReady/);
assert.doesNotMatch(lessonEditor, /tvideoJourney[^\n]{0,60}(?:storagePath|filesystem)/i);

const packageJson = JSON.parse(pkg);
assert.equal(packageJson.scripts['test:tvideo-journey-editor'], 'node scripts/check-tvideo-journey-editor.mjs');
for (const key of ['lesson.tvideoJourney.title', 'lesson.tvideoJourney.previewOnly', 'lesson.tvideoJourney.status.stale']) {
  assert.ok(en.includes(`'${key}'`), `missing English key ${key}`);
  assert.ok(vi.includes(`'${key}'`), `missing Vietnamese key ${key}`);
}

console.log('TVideo Journey helper, editor, four previews, API wiring, readiness, and publish gate PASS');
