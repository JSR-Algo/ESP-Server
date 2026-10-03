const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { parse } = require('@babel/parser');
const source = fs.readFileSync(path.join(__dirname, '../../src/views/LessonEditor.vue'), 'utf8');
const script = source.split('<script>')[1].split('</script>')[0];
const component = parse(script, { sourceType: 'module' }).program.body.find(node => node.type === 'ExportDefaultDeclaration').declaration;
const group = name => component.properties.find(node => node.key.name === name).value;
function method(name, parent = group('methods')) {
  const node = parent.properties.find(node => (node.key.name || node.key.value) === name);
  return node && (Api => new Function('Api', 'createLessonStepEditorState',
    `return ({${script.slice(node.start, node.end)}})[${JSON.stringify(name)}];`)
    (Api, require('../../src/components/lesson/lesson-step-editor-state').createLessonStepEditorState));
}
function setup() {
  const reads = [], pushes = [], notices = [];
  const Api = { lesson: {
    getLesson: (...args) => reads.push(args), listStepTypes() {}, listSharedBackgrounds() {},
  } };
  const e = {
    lessonId: 'A', lessonLoadRequestId: 0, lesson: { lessonId: 'A', courseId: 'course-A', status: 'draft' },
    steps: [{ stepKey: 'step-A' }], loading: false, stepForm: {},
    resetLessonAssetGenerationStatus() {}, clearPreviewProofState() {}, clearValidationProofState() {}, clearPromptSaveState() {},
    loadCourseModeContract() {}, resetTVideoJourneyState() {}, fetchSteps() {}, loadLessonAssetGenerationStatus() {}, loadFlattenedDerivativeStatus() {},
    $route: { query: { lessonId: 'A', courseId: 'forged-course' } },
    $router: { push: value => pushes.push(value) }, $message: { error: value => notices.push(value) },
  };
  e.fetchAll = method('fetchAll')(Api).bind(e);
  const returnMethod = method('returnToCourse');
  if (returnMethod) e.returnToCourse = returnMethod(Api).bind(e);
  return { e, reads, pushes, notices };
}
const snapshot = id => ({ lessonId: id, courseId: 'course-' + id, status: 'draft', manifestVersion: 'teebot-lesson-renderer.v5' });
test('switching A to B clears the old lesson and steps before the B read settles', () => {
  const { e, reads } = setup(); e.lessonId = 'B'; e.fetchAll();
  assert.equal(reads[0][0], 'B'); assert.equal(e.loading, true);
  assert.equal(e.lesson, null); assert.deepEqual(e.steps, []);
});
test('a failed B read cannot leave A metadata and actions in the editor', () => {
  const { e, reads } = setup(); e.lessonId = 'B'; e.fetchAll(); reads[0][2]('not found');
  assert.equal(e.loading, false); assert.equal(e.lesson, null); assert.deepEqual(e.steps, []); assert.equal(e.lessonLoadError, 'not found');
});
test('late A success and failure cannot replace the B snapshot or error', () => {
  const { e, reads, notices } = setup(); e.fetchAll(); e.lessonId = 'B'; e.fetchAll();
  reads[1][1](snapshot('B')); reads[0][1](snapshot('A')); reads[0][2]('late A');
  assert.equal(e.lesson.lessonId, 'B'); assert.equal(e.lessonLoadError, ''); assert.deepEqual(notices, []);
});
test('return navigation uses the loaded lesson course instead of URL or browser history', () => {
  const { e, pushes } = setup(); assert.equal(typeof e.returnToCourse, 'function'); e.returnToCourse();
  assert.deepEqual(pushes, [{ path: '/course-lessons', query: { courseId: 'course-A' } }]);
});
test('return navigation never sends stale A course while B is loading', () => {
  const { e, pushes } = setup(); assert.equal(typeof e.returnToCourse, 'function'); e.lessonId = 'B'; e.returnToCourse();
  assert.deepEqual(pushes, [{ path: '/course-management' }]);
});
test('query-only context changes preserve the current unsaved lesson draft', () => {
  const update = method('beforeRouteUpdate', component)({});
  const draft = { activities: [{ subject: 'unsaved' }] };
  const e = { hasPendingAuthoringChanges: true, courseModeDraft: draft, requestEditorLeave() { throw Error('should not discard same lesson'); } };
  let nextCalls = 0; update.call(e, { query: { lessonId: 'A', courseId: 'other' } }, { query: { lessonId: 'A', courseId: 'old' } }, () => nextCalls++);
  assert.equal(nextCalls, 1); assert.equal(e.courseModeDraft, draft);
});
test('switching lesson with unsaved edits requires existing leave confirmation', () => {
  const update = method('beforeRouteUpdate', component)({}); let guarded, navigation = 0;
  const next = () => navigation++; const e = { hasPendingAuthoringChanges: true, requestEditorLeave: value => { guarded = value; } };
  update.call(e, { query: { lessonId: 'B' } }, { query: { lessonId: 'A' } }, next);
  assert.equal(guarded, next); assert.equal(navigation, 0);
});
test('a same-lesson refresh keeps unsaved step and course-mode drafts', () => {
  const { e } = setup(); const steps = e.steps;
  e.courseModeDraft = { activities: [{ subject: 'unsaved' }] }; const draft = e.courseModeDraft;
  e.fetchAll(); assert.equal(e.lesson.lessonId, 'A'); assert.equal(e.steps, steps); assert.equal(e.courseModeDraft, draft);
});
test('a stale published snapshot cannot create a next version for the previous route', () => {
  const writes = []; const { e } = setup(); e.lesson.status = 'published'; e.lessonId = 'B';
  const submit = method('submitNextVersion')({ lesson: { createNextVersion: (...args) => writes.push(args) } });
  assert.equal(submit.call(e), false); assert.deepEqual(writes, []);
});
test('a stale draft snapshot never enables editing under another lesson route', () => {
  const isDraft = method('isDraft', group('computed'))({}); const { e } = setup(); e.lessonId = 'B';
  assert.equal(Boolean(isDraft.call(e)), false);
});
test('accepted lesson switch discards only the previous lesson step drafts and prompt', () => {
  const watcher = method('$route.query.lessonId', group('watch'))({});
  const oldState = { authoringDrafts: { 'shared-step-key': { subject: 'discarded A' } }, dirtyKeys: { 'shared-step-key': true } };
  const e = { stepEditor: oldState, promptDraft: 'discarded A prompt', promptDirty: true,
    resetTVideoJourneyState() {}, resetCourseModeState() {}, resetLessonAssetGenerationStatus() {}, resetFlattenedDerivativeStatus() {}, fetchAll() {} };
  watcher.call(e, 'B', 'A'); assert.notEqual(e.stepEditor, oldState);
  assert.deepEqual(e.stepEditor.authoringDrafts, {}); assert.deepEqual(e.stepEditor.dirtyKeys, {});
  assert.equal(e.promptDraft, ''); assert.equal(e.promptDirty, false);
});
test('same lesson query update never discards current step drafts', () => {
  const watcher = method('$route.query.lessonId', group('watch'))({});
  const drafts = { 'shared-step-key': { subject: 'keep A' } }; const e = { stepEditor: { authoringDrafts: drafts }, promptDraft: 'keep A prompt', promptDirty: true };
  watcher.call(e, 'A', 'A'); assert.equal(e.stepEditor.authoringDrafts, drafts); assert.equal(e.promptDraft, 'keep A prompt');
});
