import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

function setup(view, method, state, loading) {
  const source = readFileSync(new URL(`../../src/views/${view}.vue`, import.meta.url), 'utf8');
  const script = source.split('<script>')[1].split('</script>')[0]
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\s*$/gm, '').replace('export default', 'return');
  const calls = []; const messages = []; const writes = []; const navigation = [];
  const api = { lesson: { listAuthoritativeLessons: (...args) => calls.push(args), createLesson: (...args) => writes.push(args), updateLesson: (...args) => writes.push(args), deleteLesson: (...args) => writes.push(args) }, courseInsights: { listLearners: (...args) => calls.push(args) } };
  const component = new Function('Api', 'HeaderBar', 'AddDeviceDialog', 'ManualAddDeviceDialog', 'VersionFooter', 'DEFAULT_LOCALE', 'DEFAULT_AGE_BAND', 'AGE_BANDS', 'LOCALES', script)(api, {}, {}, {}, {}, 'vi', '3-5', [], []);
  const vm = { ...component.data.call({ $route: { query: {} } }), courseId: 'course-A', $t: key => key, $router: { push: route => navigation.push(route), replace: route => navigation.push(route) }, $message: { error: msg => messages.push(msg), success: msg => messages.push(msg) } };
  for (const [key, value] of Object.entries(component.methods)) vm[key] = value.bind(vm);
  vm[state] = [{ id: 'existing' }];
  return { vm, calls, messages, writes, navigation, request: keyword => vm[method](keyword), changeCourse: id => { vm.courseId = id; component.watch?.courseId?.call(vm, id); }, destroy: () => component.beforeDestroy?.call(vm), state, loading };
}

const lessons = () => setup('CourseLessons', 'fetchList', 'list', 'loading');
test('course query reuse clears old rows and dialogs, invalidates requests and reloads', () => {
  const { vm, calls, request, changeCourse } = lessons();
  request(); vm.dialogVisible = true; vm.editingMetadata = true; vm.form.title = 'old';
  vm.assignmentDialog.visible = true; const session = vm.assignmentDialog.dialogSessionId;
  changeCourse('course-B');
  assert.deepEqual(vm.list, []); assert.equal(vm.dialogVisible, false); assert.equal(vm.form.title, '');
  assert.equal(vm.editingMetadata, false); assert.equal(vm.assignmentDialog.visible, false);
  assert.ok(vm.assignmentDialog.dialogSessionId > session); assert.equal(calls.at(-1)[0], 'course-B');
  succeed(calls[0], [{ id: 'old' }]); assert.deepEqual(vm.list, []);
  succeed(calls.at(-1), [{ id: 'new' }]); assert.deepEqual(vm.list, [{ id: 'new' }]);
});
test('missing course redirects without dispatching a lesson read', () => {
  const { vm, calls, navigation, changeCourse } = lessons(); changeCourse(undefined);
  assert.deepEqual(vm.list, []); assert.deepEqual(navigation, ['/course-management']); assert.equal(calls.length, 0);
});
for (const mutation of ['create', 'update']) for (const outcome of ['success', 'error']) test(`${mutation} callback cannot affect a reused course route (${outcome})`, () => {
  const { vm, writes, navigation, messages, changeCourse } = lessons();
  vm.form = { lessonId: 'old', lessonKey: 'old', title: 'old', locale: 'vi', ageBand: '3-5' };
  vm.editingMetadata = mutation === 'update'; vm.submit(); changeCourse('course-B');
  vm.dialogVisible = true; vm.form.title = 'new draft'; vm.saving = true;
  if (outcome === 'success') succeed(writes[0], { lessonId: 'old-created' }); else fail(writes[0]);
  assert.equal(vm.dialogVisible, true); assert.equal(vm.form.title, 'new draft'); assert.equal(vm.saving, true);
  assert.deepEqual(navigation, []); assert.deepEqual(messages, []);
});
test('old delete confirmation cannot dispatch after course route changes', async () => {
  const { vm, writes, changeCourse } = lessons(); let resolve;
  vm.$confirm = () => new Promise(done => { resolve = done; });
  vm.confirmDelete({ lessonId: 'old', lessonKey: 'old' }); changeCourse('course-B'); resolve();
  await Promise.resolve(); assert.equal(writes.length, 0);
});

for (const mutation of ['create', 'update']) test(`current ${mutation} retains success behavior`, () => {
  const { vm, writes, calls, navigation, messages } = lessons();
  vm.form = { lessonId: 'current', lessonKey: 'current', title: 'current', locale: 'vi', ageBand: '3-5' };
  vm.editingMetadata = mutation === 'update'; vm.submit(); succeed(writes[0], { lessonId: 'created' });
  assert.equal(vm.saving, false); assert.equal(vm.dialogVisible, false);
  assert.deepEqual(messages, [mutation === 'update' ? 'lesson.metadataSaved' : 'lesson.created']);
  if (mutation === 'update') assert.equal(calls.length, 1);
  else assert.equal(navigation[0].query.lessonId, 'created');
});
for (const mutation of ['create', 'update']) for (const outcome of ['success', 'error']) test(`destroyed ${mutation} ignores ${outcome}`, () => {
  const { vm, writes, navigation, messages, destroy } = lessons();
  vm.form = { lessonId: 'old', lessonKey: 'old', title: 'old', locale: 'vi', ageBand: '3-5' };
  vm.editingMetadata = mutation === 'update'; vm.submit(); destroy(); const before = JSON.stringify(vm);
  if (outcome === 'success') succeed(writes[0], { lessonId: 'old-created' }); else fail(writes[0]);
  assert.equal(JSON.stringify(vm), before); assert.deepEqual(navigation, []); assert.deepEqual(messages, []);
});
for (const outcome of ['success', 'error']) test(`old dispatched delete ignores ${outcome} after route reuse`, async () => {
  const { vm, writes, calls, messages, changeCourse } = lessons(); vm.$confirm = async () => true;
  vm.confirmDelete({ lessonId: 'old', lessonKey: 'old' }); await Promise.resolve();
  assert.equal(writes.length, 1); changeCourse('course-B'); const readCount = calls.length;
  if (outcome === 'success') succeed(writes[0]); else fail(writes[0]);
  assert.equal(calls.length, readCount); assert.deepEqual(messages, []);
});
test('current confirmed delete still refreshes list', async () => {
  const { vm, writes, calls, messages } = lessons(); vm.$confirm = async () => true;
  vm.confirmDelete({ lessonId: 'current', lessonKey: 'current' }); await Promise.resolve();
  assert.equal(writes[0][0], 'current'); succeed(writes[0]);
  assert.equal(calls.length, 1); assert.deepEqual(messages, ['lesson.deleted']);
});
const succeed = (call, rows) => call.at(-2)(rows);
const fail = call => call.at(-1)('failed');

for (const [view, method, state, loading] of [
  ['CourseLessons', 'fetchList', 'list', 'loading'],
  ['DeviceManagement', 'fetchChildOptions', 'childOptions', 'childLoading'],
]) {
  for (const outcome of ['success', 'error']) test(`${view} stale ${outcome} preserves latest loading and existing rows`, () => {
    const { vm, calls, messages, request } = setup(view, method, state, loading);
    request('old'); request('new');
    if (outcome === 'success') succeed(calls[0], [{ id: 'old' }]); else fail(calls[0]);
    assert.equal(vm[loading], true); assert.deepEqual(vm[state], [{ id: 'existing' }]); assert.deepEqual(messages, []);
    succeed(calls[1], [{ id: 'new' }]);
    assert.equal(vm[loading], false); assert.deepEqual(vm[state], [{ id: 'new' }]);
  });
  for (const outcome of ['success', 'error']) test(`${view} late ${outcome} cannot replace completed latest result`, () => {
    const { vm, calls, messages, request } = setup(view, method, state, loading);
    request('old'); request('new'); succeed(calls[1], [{ id: 'new' }]);
    if (outcome === 'success') succeed(calls[0], [{ id: 'old' }]); else fail(calls[0]);
    assert.equal(vm[loading], false); assert.deepEqual(vm[state], [{ id: 'new' }]); assert.deepEqual(messages, []);
  });
  for (const outcome of ['success', 'error']) test(`${view} ignores destroyed ${outcome} and future reads`, () => {
    const { vm, calls, messages, request, destroy } = setup(view, method, state, loading);
    request('old'); destroy(); const before = JSON.stringify(vm);
    if (outcome === 'success') succeed(calls[0], [{ id: 'old' }]); else fail(calls[0]);
    assert.equal(JSON.stringify(vm), before); assert.deepEqual(messages, []);
    request('new'); assert.equal(calls.length, 1);
  });
  test(`${view} current failure retains existing error behavior`, () => {
    const { vm, calls, messages, request } = setup(view, method, state, loading);
    request('current'); fail(calls[0]); assert.equal(vm[loading], false);
    assert.deepEqual(vm[state], view === 'CourseLessons' ? [{ id: 'existing' }] : []);
    assert.deepEqual(messages, view === 'CourseLessons' ? ['failed'] : []);
  });
}
