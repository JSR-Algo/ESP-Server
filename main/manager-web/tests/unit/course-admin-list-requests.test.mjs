import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const A = 'c0060000-0000-4000-8000-000000000001';
const B = 'c0060000-0000-4000-8000-000000000002';

function setup(view, method, state, loading) {
  const source = readFileSync(new URL(`../../src/views/${view}.vue`, import.meta.url), 'utf8');
  const script = source.split('<script>')[1].split('</script>')[0]
    .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\s*$/gm, '').replace('export default', 'return');
  const versions = []; const calls = []; const messages = []; const writes = []; const navigation = [];
  const api = { course: { getCourse: (id, ok) => ok({ courseId: id, title: 'Authoritative course' }), ...Object.fromEntries(['getCourseList', 'createCourse', 'updateCourse', 'cloneCourse', 'setTemplate', 'deleteCourse'].map(name => [name, (...args) => (name === 'getCourseList' ? calls : writes).push(args)])) }, lesson: { listLessons: (...args) => versions.push(args), listAuthoritativeLessons: (...args) => calls.push(args), createLesson: (...args) => writes.push(args), updateLesson: (...args) => writes.push(args), deleteLesson: (...args) => writes.push(args) }, courseInsights: { getCourseQuality: (...args) => calls.push(args), listLearners: (...args) => calls.push(args) } };
  const component = new Function('Api', 'HeaderBar', 'AddDeviceDialog', 'ManualAddDeviceDialog', 'VersionFooter', 'DEFAULT_LOCALE', 'DEFAULT_AGE_BAND', 'AGE_BANDS', 'LOCALES', 'validateCourseForm', 'mutationDetails', 'uncertainMutation', 'pageFromQuery', script)(api, {}, {}, {}, {}, 'vi', '3-5', [], [], ...Object.values(require('../../src/utils/courseForm.cjs')), require('../../src/utils/adminPagination.cjs').pageFromQuery);
  const vm = { ...component.data.call({ $route: { query: {} } }), $route: { path: '/course-lessons', query: { courseId: A } }, $t: key => key, $router: { push: route => navigation.push(route), replace: route => navigation.push(route) }, $message: { warning: msg => messages.push(msg), error: msg => messages.push(msg), success: msg => messages.push(msg) } };
  for (const [key, value] of Object.entries(component.methods)) vm[key] = value.bind(vm);
  for (const [key, getter] of Object.entries(component.computed || {})) Object.defineProperty(vm, key, { get: () => getter.call(vm) });
  if (view === 'CourseLessons') vm.courseInfo = { courseId: A, title: 'Authoritative course' };
  vm[state] = [{ id: 'existing' }];
  return { vm, calls, versions, messages, writes, navigation, component, request: keyword => vm[method](keyword), changeCourse: id => { vm.$route.query = { courseId: id }; component.watch?.courseId?.call(vm, id); component.watch?.['$route.query']?.call(vm); }, destroy: () => component.beforeDestroy?.call(vm), state, loading };
}

const lessons = () => setup('CourseLessons', 'fetchList', 'list', 'loading');
test('course query reuse clears old rows and dialogs, invalidates requests and reloads', () => {
  const { vm, calls, request, changeCourse } = lessons();
  request(); vm.dialogVisible = true; vm.editingMetadata = true; vm.form.title = 'old';
  vm.assignmentDialog.visible = true; const session = vm.assignmentDialog.dialogSessionId;
  changeCourse(B);
  assert.deepEqual(vm.list, []); assert.equal(vm.dialogVisible, false); assert.equal(vm.form.title, '');
  assert.equal(vm.editingMetadata, false); assert.equal(vm.assignmentDialog.visible, false);
  assert.ok(vm.assignmentDialog.dialogSessionId > session); assert.equal(calls.at(-1)[0], B);
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
  vm.editingMetadata = mutation === 'update'; vm.dialogVisible = true; vm.submit(); changeCourse(B);
  vm.dialogVisible = true; vm.form.title = 'new draft'; vm.saving = true;
  if (outcome === 'success') succeed(writes[0], { lessonId: 'old-created' }); else fail(writes[0]);
  assert.equal(vm.dialogVisible, true); assert.equal(vm.form.title, 'new draft'); assert.equal(vm.saving, true);
  assert.deepEqual(navigation, []); assert.deepEqual(messages, []);
});
test('old delete confirmation cannot dispatch after course route changes', async () => {
  const { vm, writes, changeCourse } = lessons(); let resolve;
  vm.$confirm = () => new Promise(done => { resolve = done; });
  vm.confirmDelete({ lessonId: 'old', lessonKey: 'old' }); changeCourse(B); resolve();
  await Promise.resolve(); assert.equal(writes.length, 0);
});

for (const mutation of ['create', 'update']) test(`current ${mutation} retains success behavior`, () => {
  const { vm, writes, calls, navigation, messages } = lessons();
  vm.form = { lessonId: 'current', lessonKey: 'current', title: 'current', locale: 'vi', ageBand: '3-5' };
  vm.editingMetadata = mutation === 'update'; vm.dialogVisible = true; vm.submit(); succeed(writes[0], { lessonId: 'created' });
  assert.equal(vm.saving, false); assert.equal(vm.dialogVisible, false);
  assert.deepEqual(messages, [mutation === 'update' ? 'lesson.metadataSaved' : 'lesson.created']);
  if (mutation === 'update') assert.equal(calls.length, 1);
  else assert.equal(navigation[0].query.lessonId, 'created');
});
for (const mutation of ['create', 'update']) for (const outcome of ['success', 'error']) test(`destroyed ${mutation} ignores ${outcome}`, () => {
  const { vm, writes, navigation, messages, destroy } = lessons();
  vm.form = { lessonId: 'old', lessonKey: 'old', title: 'old', locale: 'vi', ageBand: '3-5' };
  vm.editingMetadata = mutation === 'update'; vm.dialogVisible = true; vm.submit(); destroy(); const before = JSON.stringify(vm);
  if (outcome === 'success') succeed(writes[0], { lessonId: 'old-created' }); else fail(writes[0]);
  assert.equal(JSON.stringify(vm), before); assert.deepEqual(navigation, []); assert.deepEqual(messages, []);
});
for (const outcome of ['success', 'error']) test(`old dispatched delete ignores ${outcome} after route reuse`, async () => {
  const { vm, writes, calls, messages, changeCourse } = lessons(); vm.$confirm = async () => true;
  vm.confirmDelete({ lessonId: 'old', lessonKey: 'old' }); await Promise.resolve();
  assert.equal(writes.length, 1); changeCourse(B); const readCount = calls.length;
  if (outcome === 'success') succeed(writes[0]); else fail(writes[0]);
  assert.equal(calls.length, readCount); assert.deepEqual(messages, []);
});
test('current confirmed delete still refreshes list', async () => {
  const { vm, writes, calls, messages } = lessons(); vm.$confirm = async () => true;
  vm.confirmDelete({ lessonId: 'current', lessonKey: 'current' }); await Promise.resolve();
  assert.equal(writes[0][0], 'current'); succeed(writes[0]);
  assert.equal(calls.length, 1); assert.deepEqual(messages, ['lesson.deleted']);
});
const succeed = (call, rows) => call.at(-2)(rows, { page: 1, pageSize: 50, total: Array.isArray(rows) ? rows.length : 0, totalPages: Array.isArray(rows) && rows.length ? 1 : 0 });
const fail = call => call.at(-1)('failed');

for (const [view, method, state, loading] of [
  ['CourseManagement', 'fetchList', 'list', 'loading'],
  ['CourseLessons', 'fetchList', 'list', 'loading'],
  ['DeviceManagement', 'fetchChildOptions', 'childOptions', 'childLoading'],
]) {
  for (const outcome of ['success', 'error']) test(`${view} stale ${outcome} preserves latest loading and existing rows`, () => {
    const { vm, calls, messages, request } = setup(view, method, state, loading);
    request('old'); request('new');
    if (outcome === 'success') succeed(calls[0], [{ id: 'old' }]); else fail(calls[0]);
    assert.equal(vm[loading], true); assert.deepEqual(vm[state], view === 'DeviceManagement' ? [{ id: 'existing' }] : []); assert.deepEqual(messages, []);
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
    assert.deepEqual(vm[state], []);
    assert.deepEqual(messages, view !== 'DeviceManagement' ? ['failed'] : []);
  });
}

for (const view of ['CourseManagement', 'CourseLessons']) {
  for (const outcome of ['success', 'error']) test(`${view} old modal save cannot close or unlock a newly opened draft (${outcome})`, () => {
    const { vm, writes, navigation, messages } = setup(view, 'fetchList', 'list', 'loading');
    const open = view === 'CourseManagement' ? 'openEdit' : 'openMetadata';
    const row = id => ({ courseId: id, courseKey: id.toLowerCase(), lessonId: id, lessonKey: id.toLowerCase(), title: id, locale: 'vi', ageBand: '3-5' });
    vm[open](row('A')); vm.submit(); vm.resetForm(); vm[open](row('B')); vm.submit();
    assert.equal(writes.length, 2);
    if (outcome === 'success') succeed(writes[0]); else fail(writes[0]);
    assert.equal(vm.dialogVisible, true); assert.equal(vm.saving, true); assert.equal(vm.form.title, 'B');
    assert.deepEqual(messages, []); assert.deepEqual(navigation, []);
    succeed(writes[1]); assert.equal(vm.saving, false); assert.equal(vm.dialogVisible, false);
  });
}
test('CourseManagement duplicate submit dispatches once', () => {
  const { vm, writes } = setup('CourseManagement', 'fetchList', 'list', 'loading');
  vm.openCreate(); Object.assign(vm.form, { courseKey: 'course', title: 'title' });
  vm.submit(); vm.submit(); assert.equal(writes.length, 1);
});
for (const outcome of ['success', 'error']) test(`CourseManagement obsolete clone cannot navigate or alter a later dialog (${outcome})`, () => {
  const { vm, writes, navigation, messages } = setup('CourseManagement', 'fetchList', 'list', 'loading');
  vm.openClone({ courseId: 'A', courseKey: 'a', title: 'A' }); vm.doClone(); vm.doClone();
  assert.equal(writes.length, 1);
  vm.resetClone(); vm.openClone({ courseId: 'B', courseKey: 'b', title: 'B' }); vm.doClone();
  if (outcome === 'success') succeed(writes[0], { courseId: 'clone-A' }); else fail(writes[0]);
  assert.equal(vm.cloneVisible, true); assert.equal(vm.cloning, true); assert.equal(vm.cloneSource.courseId, 'B');
  assert.deepEqual(navigation, []); assert.deepEqual(messages, []);
});
test('CourseManagement quality callback after destruction is ignored', () => {
  const { vm, calls, destroy, messages } = setup('CourseManagement', 'fetchList', 'list', 'loading');
  vm.fetchQuality(); destroy(); fail(calls[0]); assert.deepEqual(messages, []);
});

test('published rows expose their latest draft without replacing published identity', () => {
  const { vm, calls, versions, component } = lessons(); vm.fetchList();
  succeed(calls[0], [{ lessonId: 'published', lessonKey: 'key', status: 'published', lessonVersion: 2 }]);
  succeed(versions[0], [{ lessonId: 'old', lessonKey: 'key', status: 'archived', lessonVersion: 1 }, { lessonId: 'draft', lessonKey: 'key', status: 'draft', lessonVersion: 3 }]);
  const drafts = vm.draftVersions;
  assert.equal(vm.list[0].lessonId, 'published'); assert.equal(drafts.key.lessonId, 'draft');
  assert.equal(vm.historyFailed, false); assert.equal(vm.statuses.includes('archived'), true); assert.equal(vm.existingDraft(vm.list[0]).lessonId, 'draft');
});
for (const outcome of ['success', 'error']) test(`draft versions reject obsolete ${outcome} on course change`, () => {
  const { vm, versions, changeCourse } = lessons(); vm.fetchList(); changeCourse(B);
  if (outcome === 'success') succeed(versions[0], [{ lessonId: 'A-draft' }]); else fail(versions[0]);
  assert.deepEqual(Object.keys(vm.draftVersions), []); assert.equal(vm.historyFailed, false);
  fail(versions[1]); assert.equal(vm.historyFailed, true);
});

test('draft keys cannot resolve inherited object members', () => {
 const { vm, component, calls, versions } = lessons();
 vm.fetchList(); succeed(calls[0], []); succeed(versions[0], []);
 assert.equal(vm.draftVersions.constructor, undefined);
 succeed(versions[0], [{ lessonKey: 'constructor', lessonId: 'draft', status: 'draft', lessonVersion: 2 }]);
 assert.equal(vm.existingDraft({ lessonKey: 'constructor', status: 'published', lessonVersion: 1 }).lessonId, 'draft');
});

for (const roundTrip of [false, true]) test(`pending delete confirmation cannot survive ${roundTrip ? 'A/B/A navigation' : 'destruction'}`, async () => {
  const { vm, writes, changeCourse, destroy } = lessons(); let resolve;
  vm.$confirm = () => new Promise(done => { resolve = done; });
  vm.confirmDelete({ lessonId: 'pending', lessonKey: 'pending' });
  if (roundTrip) { changeCourse(B); changeCourse(A); } else destroy();
  resolve(); await Promise.resolve();
  assert.equal(writes.length, 0); assert.equal(vm.deletePending.pending, false);
});

for (const outcome of ['success', 'error']) test(`dispatched delete cannot refresh a new A context after A/B/A (${outcome})`, async () => {
 const { vm, writes, calls, messages, changeCourse } = lessons(); vm.$confirm = async () => true;
 vm.confirmDelete({ lessonId: 'old', lessonKey: 'old' }); await Promise.resolve();
 changeCourse(B); changeCourse(A); const readCount = calls.length;
 if (outcome === 'success') succeed(writes[0]); else fail(writes[0]);
 assert.equal(calls.length, readCount); assert.deepEqual(messages, []);
});
