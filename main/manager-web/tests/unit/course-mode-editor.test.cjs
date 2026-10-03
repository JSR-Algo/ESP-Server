const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../../src/views/LessonEditor.vue'), 'utf8');
const block = source.slice(source.indexOf('    onCourseModeDraftInput(value)'), source.indexOf('    async loadCanonicalDemo()'));
const clone = value => JSON.parse(JSON.stringify(value));
for (const [name, lesson, query, expected] of [
  ['loaded lesson owns course', { courseId: 'actual' }, { courseId: 'stale' }, { path: '/course-lessons', query: { courseId: 'actual' } }],
  ['loading deep link waits for loaded identity', null, { courseId: 'linked' }, { path: '/course-management' }],
  ['same-course return ignores URL title', { courseId: 'linked' }, { courseId: 'linked', courseTitle: 'Farm' }, { path: '/course-lessons', query: { courseId: 'linked' } }],
  ['stale lesson does not own destination', { lessonId: 'old', courseId: 'old-course' }, { courseId: 'new-course' }, { path: '/course-management' }],
  ['missing course returns to catalog', null, {}, { path: '/course-management' }],
]) test(`Lessons navigation: ${name}`, () => {
  const match = source.match(/    returnToCourse\(\) \{([\s\S]*?)\n    \},/);
  assert.ok(match, 'editor must resolve the named parent instead of browser history');
  const loaded = lesson && { lessonId: 'current', ...lesson };
  const routes = [];
  new Function(match[1]).call({ lesson: loaded, lessonId: 'current', $route: { query }, $router: { push: route => routes.push(route) } });
  assert.deepEqual(routes, [expected]);
  assert.ok(source.includes('@click="returnToCourse"'));
});
test('course lesson list returns to the course catalog independently of history', () => {
  const list = fs.readFileSync(path.join(__dirname, '../../src/views/CourseLessons.vue'), 'utf8');
  assert.ok(list.includes('@click="$router.push({ path: \'/course-management\' })"'));
});
test('existing passive step authoring does not synthesize interaction metadata', () => {
  const helpers = require('../../src/components/lesson/lesson-builder-logic');
  const getter = source.slice(source.indexOf('    selectedAuthoring: {'), source.indexOf('    selectedTemplateAuthoring: {'));
  const computed = new Function('mergeAuthoringFields', 'mergePersistedAuthoringFields', `return {${getter}}`)(helpers.mergeAuthoringFields, helpers.mergePersistedAuthoringFields);
  const body = { durationSec: 12, terminal: true, scene: { assetId: 'retained' } };
  const authored = computed.selectedAuthoring.get.call({ selectedStep: { stepKey: 's1', stepBody: body }, selectedStepDrafts: {} });
  assert.equal(authored.interaction, undefined);
  assert.equal(authored.teachingWord, undefined);
  assert.equal(authored.durationSec, 12);
});
const snap = (id = 'A', letter = 'a') => ({ lessonId: id, checksum: letter.repeat(64), visualChecksum: 'b'.repeat(64), contract: { contractChecksum: letter.repeat(64), session: {}, targets: [], activities: [{ contextId: 'saved', activityId: 'a1', visual: {}, answerPolicy: {}, outcomes: {}, targetIds: [], modalities: [] }] } });
function setup() {
  const reads = [], writes = [];
  const methods = new Function('Api', 'withCourseModeChecksum', 'normalizeCourseModeVisualKeys', `return {${block}}`)({ lesson: {
    getCourseModeContract: (...args) => reads.push(args), saveCourseModeContract: (...args) => writes.push(args),
  } }, async value => value, value => value);
  const e = { ...methods, lessonId: 'A', isDraft: true, courseModeRequestId: 0, invalidatePreview() {}, fetchSteps() {}, $confirm: async () => true };
  e.resetCourseModeState();
  const load = () => { e.loadCourseModeContract(); reads.at(-1)[1](snap(e.lessonId)); };
  load();
  const edit = () => { const value = clone(e.courseModeDraft); value.activities[0].contextId = 'local'; e.onCourseModeDraftInput(value); };
  return { e, reads, writes, edit, load };
}
test('read and consecutive saves use the latest pair from one snapshot', async () => {
  const {e,writes,edit} = setup(); edit(); await e.saveCourseModeContract();
  assert.equal(writes[0][3], 'b'.repeat(64));
  const response = snap('A','c'); response.visualChecksum = 'd'.repeat(64); writes[0][4](response);
  edit(); await e.saveCourseModeContract(); assert.equal(writes[1][2], response.checksum); assert.equal(writes[1][3], response.visualChecksum);
});
test('double save dispatches once', async () => {const {e,writes,edit}=setup();edit();await Promise.all([e.saveCourseModeContract(),e.saveCourseModeContract()]);assert.equal(writes.length,1);});
for (const status of [0,401,403,409,500]) test(`failed save ${status} preserves draft and both original tokens`, async () => {
  const {e,writes,edit}=setup();edit();const before=clone(e.courseModeDraft);await e.saveCourseModeContract();
  const fail=writes[0].at(-1);fail('Request failed',{status});assert.deepEqual(e.courseModeDraft,before);assert.equal(e.courseModeDirty,true);assert.equal(e.courseModeSaving,false);assert.equal(e.courseModeExpectedVisualChecksum,'b'.repeat(64));assert.ok(e.courseModeError.length>20);
});
test('delayed load after lesson switch cannot replace current lesson', () => { const {e,reads,load}=setup();e.loadCourseModeContract();const stale=reads.at(-1);e.lessonId='B';e.resetCourseModeState();load();stale[1](snap('A','c'));assert.equal(e.courseModeExpectedChecksum,'a'.repeat(64)); });
test('delayed save after lesson switch cannot replace data or notification', async () => {const {e,writes,edit,load}=setup();edit();await e.saveCourseModeContract();const stale=writes[0];e.lessonId='B';e.resetCourseModeState();load();stale.at(-2)(snap('A','c'));assert.equal(e.courseModeExpectedChecksum,'a'.repeat(64));assert.equal(e.courseModeSavedMessage,'');});
for (const response of [undefined, {}, {contract:{}}, {...snap(), visualChecksum:undefined}]) test(`incomplete read ${JSON.stringify(response)} settles into an explicit error`, () => {const {e,reads}=setup();e.resetCourseModeState();e.loadCourseModeContract();assert.doesNotThrow(()=>reads.at(-1)[1](response));assert.equal(e.courseModeLoading,false);assert.equal(e.courseModeContract,null);assert.ok(e.courseModeError);});
test('conflict readback is separate until explicit comparison acceptance', async () => {const {e,reads,writes,edit}=setup();edit();await e.saveCourseModeContract();writes[0].at(-1)('Conflict',{status:409});e.reviewCourseModeConflict();reads.at(-1)[1](snap('A','c'));assert.equal(e.courseModeDraft.activities[0].contextId,'local');assert.equal(e.courseModeExpectedChecksum,'a'.repeat(64));e.keepCourseModeDraftAfterReview();assert.equal(e.courseModeDraft.activities[0].contextId,'local');assert.equal(e.courseModeExpectedChecksum,'c'.repeat(64));assert.equal(e.courseModeDirty,true);assert.equal(writes.length,1);});
test('undo affects current draft only', async()=>{const {e,edit}=setup();edit();await e.undoCourseModeDraft();assert.equal(e.courseModeDraft.activities[0].contextId,'saved');assert.equal(e.courseModeDirty,false);});
test('accepting an older discard confirmation preserves edits made while it was open', async () => {
  const { e, reads, edit } = setup();
  edit();
  e.reviewCourseModeConflict();
  reads.at(-1)[1](snap('A', 'c'));
  let confirm;
  e.$confirm = () => new Promise(resolve => { confirm = resolve; });
  const pending = e.useSavedCourseModeAfterReview();
  const newerDraft = clone(e.courseModeDraft);
  newerDraft.activities[0].contextId = 'newer edit';
  e.onCourseModeDraftInput(newerDraft);
  confirm();
  assert.equal(await pending, false);
  assert.deepEqual(e.courseModeDraft, newerDraft);
  assert.equal(e.courseModeDirty, true);
  assert.equal(e.courseModeExpectedChecksum, 'a'.repeat(64));
  assert.equal(e.courseModeConflict, true);
});
for (const action of ['cancel', 'replace snapshot', 'switch lesson']) {
  test(`pending discard confirmation preserves state on ${action}`, async () => {
    const { e, reads, edit } = setup();
    edit(); e.reviewCourseModeConflict(); reads.at(-1)[1](snap('A', 'c'));
    let resolve, reject;
    e.$confirm = () => new Promise((yes, no) => { resolve = yes; reject = no; });
    const pending = e.useSavedCourseModeAfterReview();
    if (action === 'replace snapshot') e.courseModeConflictSnapshot = snap('A', 'd');
    if (action === 'switch lesson') { e.lessonId = 'B'; e.resetCourseModeState(); }
    const before = clone({ draft: e.courseModeDraft, token: e.courseModeExpectedChecksum });
    if (action === 'cancel') reject(new Error('cancel')); else resolve();
    assert.equal(await pending, false);
    assert.deepEqual({ draft: e.courseModeDraft, token: e.courseModeExpectedChecksum }, before);
  });
}
test('malformed lesson response does not hang the editor',()=>{
 const body=source.slice(source.indexOf('    fetchAll() {'),source.indexOf('    clearPreviewProofState() {'));
 let success;
 const methods=new Function('Api',`return {${body}}`)({lesson:{getLesson:(id,ok)=>{success=ok},listStepTypes:()=>{},listSharedBackgrounds:()=>{}}});
 const e={...methods,lessonId:'A',lessonLoadRequestId:0,resetLessonAssetGenerationStatus(){},clearPreviewProofState(){},clearValidationProofState(){},clearPromptSaveState(){},loadCourseModeContract(){},$message:{error(){}},stepForm:{}};
 e.fetchAll();assert.doesNotThrow(()=>success(null));assert.equal(e.loading,false);assert.equal(e.lesson,null);assert.ok(e.lessonLoadError);
});

test('conflict undo then keep draft recomputes unsaved state',async()=>{const {e,edit,writes,reads}=setup();edit();await e.saveCourseModeContract();writes[0].at(-1)('Conflict',{status:409});await e.undoCourseModeDraft();e.reviewCourseModeConflict();const remote=snap('A','c');remote.contract.activities[0].contextId='remote';reads.at(-1)[1](remote);e.keepCourseModeDraftAfterReview();assert.equal(e.courseModeDirty,true);});
for(const field of ['visual','answerPolicy','outcomes']) test('nested missing '+field+' rejects before render',()=>{const {e,reads}=setup();e.resetCourseModeState();e.loadCourseModeContract();const response=snap();delete response.contract.activities[0][field];reads.at(-1)[1](response);assert.equal(e.courseModeContract,null);assert.ok(e.courseModeError);});
test('malformed target meanings rejects before rendering join',()=>{const {e,reads}=setup();e.resetCourseModeState();e.loadCourseModeContract();const response=snap();response.contract.targets=[{targetId:'word',vietnameseMeanings:'meaning'}];reads.at(-1)[1](response);assert.equal(e.courseModeContract,null);assert.ok(e.courseModeError);});
test('delayed next-version response cannot navigate away from a different lesson',()=>{
 const block=source.slice(source.indexOf('    submitNextVersion(data)'),source.indexOf('    formatTVideoJourneyError'));
 let done;const api={lesson:{createNextVersion:(id,ok)=>{done=ok}}};const methods=new Function('Api',`return {${block}}`)(api);const notices=[],routes=[];
 const e={...methods,lessonId:'A',lessonLoadRequestId:1,lesson:{lessonId:'A',status:'published',lessonVersion:1},$route:{path:'/lesson-editor',query:{}},$router:{replace:v=>routes.push(v)},$message:{success:v=>notices.push(v),error:v=>notices.push(v)},$t:v=>v};
 e.submitNextVersion();e.lessonId='B';e.lessonLoadRequestId=2;done({lessonId:'A2',lessonVersion:2,status:'draft'});assert.equal(routes.length,0);assert.equal(notices.length,0);
});

for (const outcome of ['success', 'error']) test(`rename ignores replaced lesson ${outcome}`, () => {
  const calls = []; const messages = [];
  const block = source.slice(source.indexOf('    openRename()'), source.indexOf('    doValidate('));
  const methods = new Function('Api', `return {${block}}`)({ lesson: { updateLesson: (...args) => calls.push(args) } });
  const vm = { ...methods, lessonId: 'A', lessonLoadRequestId: 1, titleDraft: 'new A', lesson: { lessonId: 'A' },
    invalidatePreview() {}, $t: x => x, $message: { success: x => messages.push(x), error: x => messages.push(x) }, handleUncertainMutationError() {} };
  vm.doRename(); vm.lessonId = 'B'; vm.lessonLoadRequestId = 2; vm.lesson = { lessonId: 'B' }; vm.renaming = false;
  if (outcome === 'success') calls[0][2]({ lessonId: 'A' }); else calls[0][3]('failed');
  assert.equal(vm.lesson.lessonId, 'B'); assert.deepEqual(messages, []);
});
for (const value of ['', null, 'replacement', undefined]) test(`step hint PATCH preserves explicit ${JSON.stringify(value)}`, () => {
  const api = fs.readFileSync(path.join(__dirname, '../../src/apis/module/lesson.js'), 'utf8');
  const block = api.slice(api.indexOf('  updateStep('), api.indexOf('  // POST /v1/admin/lessons/:lessonId/steps/reorder'));
  let request;
  const method = new Function('nestRequest', 'getNestUrl', `return {${block}}`)(x => { request = x; }, () => '/admin');
  method.updateStep('lesson', 'step', { helperText: value, l1TransferHint: value });
  const sent = JSON.parse(JSON.stringify(request.data));
  for (const field of ['helperText', 'l1TransferHint']) {
    assert.equal(sent[field], value); assert.equal(Object.hasOwn(sent, field), value !== undefined);
  }
});

test('published lessons mount a read-only keyed bundle reader', () => {
  const tag = source.match(/<LessonAssetManager\b[^>]*>/)[0];
  const attr = name => tag.match(new RegExp(name + '=\"([^\"]*)\"'))?.[1];
  assert.ok(new Function('isDraft', 'lesson', `return ${attr('v-if')}`)(false, { status: 'published' }));
  assert.equal(attr(':key'), 'lessonId');
  assert.equal(attr(':read-only'), '!isDraft');
});

test('published bundle component rejects mutations even if disabled is false', () => {
  const text = fs.readFileSync(path.join(__dirname, '../../src/components/LessonAssetManager.vue'), 'utf8');
  const body = text.slice(text.indexOf('    beginMutation() {'), text.indexOf('    finishMutation(id)'));
  let calls = 0;
  const methods = new Function('nextAssetMutationId', `return {${body}}`)(() => { calls++; return 'id'; });
  const vm = { ...methods, readOnly: true, disabled: false, mutationPending: false, $emit() {} };
  assert.equal(vm.beginMutation(), null);
  assert.equal(calls, 0);
});

test('typed validation rejection preserves actionable findings and metrics', () => {
  const block = source.slice(source.indexOf('    doValidate('), source.indexOf('    validManifestPreviewResponse('));
  let reject;
  const methods = new Function('Api', `return {${block}}`)({ lesson: { validate(id, ok, fail) { reject = fail; } } });
  const vm = { ...methods, lessonId: 'A', lessonLoadRequestId: 1, validationRequestId: 0, publishReviewRequestId: 0, proofVersion: 3, hasUnsafeProofState: () => false, $message: { error() {} } };
  vm.doValidate();
  const errors = [{ code: 'passive-run', stepKey: 's1', message: '48 exceeds 30', actual: 48, limit: 30 }];
  reject('Validation failed', { status: 422, data: { code: 'LESSON_PUBLISH_VALIDATION_FAILED', details: { errors, warnings: [], metrics: { assetCount: 8 } } } });
  assert.deepEqual(vm.validationResult.errors, errors);
  assert.deepEqual(vm.validationResult.metrics, { assetCount: 8 });
  assert.equal(vm.validationResult.valid, false);
});

const libraryGetter = source.slice(source.indexOf('    cinematicLibraries() {'), source.indexOf('    selectedStep() {'));
const libraryComputed = new Function(`return {${libraryGetter}}`)();
const selectionBlock = source.slice(source.indexOf('    selectCinematicLayer(selection) {'), source.indexOf('    selectTeachObject(obj) {'));
for (const version of ['v1', 'v2']) {
  test(`${version} picker excludes videos from every visual slot`, () => {
    const image = { versionId: 'image', mimeType: 'image/png' };
    const video = { versionId: 'video', mimeType: 'video/mp4' };
    const vm = { lesson: { manifestVersion: `teebot-lesson-renderer.${version}` },
      isCourseModeV5: false, rawCinematicLibraries: Object.fromEntries(
        ['backgroundScene', 'teachingObject', 'robotOverlay'].map(slot => [slot, [image, video]])) };
    const libraries = libraryComputed.cinematicLibraries.call(vm);
    for (const rows of Object.values(libraries)) assert.deepEqual(rows, [image]);
  });
  test(`${version} rejects an incompatible selection before dispatching a write`, () => {
    const writes = [], warnings = [];
    const methods = new Function('Api', `return {${selectionBlock}}`)({ lesson: { setVisualRef: (...args) => writes.push(args) } });
    const vm = { lesson: { manifestVersion: `teebot-lesson-renderer.${version}` },
      isCourseModeV5: false, isDraft: true, selectedStep: { stepKey: 's1' },
      $message: { warning: message => warnings.push(message) } };
    methods.selectCinematicLayer.call(vm, { slot: 'robotOverlay', assetVersionId: 'video', asset: { mimeType: 'video/mp4' } });
    assert.equal(writes.length, 0);
    assert.equal(warnings.length, 1);
  });
}
test('v3 picker retains supported direct-video candidates', () => {
  const video = { versionId: 'video', mimeType: 'video/mp4' };
  const vm = { lesson: { manifestVersion: 'teebot-lesson-renderer.v3' }, isCourseModeV5: false,
    rawCinematicLibraries: { robotOverlay: [video] } };
  assert.deepEqual(libraryComputed.cinematicLibraries.call(vm).robotOverlay, [video]);
});
