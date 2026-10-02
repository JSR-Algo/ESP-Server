const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const A = '11111111-1111-4111-8111-111111111111';
const B = '22222222-2222-4222-8222-222222222222';
function setup(view = 'CourseLessons', id = A) {
  const calls = {}, notices = [], pushes = [], replaces = [];
  const module = new Proxy({}, { get: (_, name) => (...args) => {
    const request = { args, ok: (rows) => args.at(-2)(rows, { page: 1, pageSize: 50, total: Array.isArray(rows) ? rows.length : 0, totalPages: Array.isArray(rows) && rows.length ? 1 : 0 }), fail: args.at(-1) };
    (calls[name] ||= []).push(request);
  } });
  const Api = { course: module, lesson: module, monitoring: module, courseInsights: module };
  const source = fs.readFileSync(path.join(__dirname, '../../src/views', view + '.vue'), 'utf8');
  const script = source.split('<script>')[1].split('</script>')[0]
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'return');
  const component = new Function('Api', 'HeaderBar', 'AGE_BANDS', 'DEFAULT_AGE_BAND', 'DEFAULT_LOCALE', 'LOCALES', 'ageBandMinimum', 'ageBandSeverity', 'validateCourseForm', 'mutationDetails', 'uncertainMutation', 'isUncertainNestError', 'pageFromQuery', script)
    (Api, {}, [], '4-6', 'en-US', [], () => 4, () => 'info', ...Object.values(require('../../src/utils/courseForm.cjs')), e => Number(e?.status) === 0 || Number(e?.status) >= 500, require('../../src/utils/adminPagination.cjs').pageFromQuery);
  const s = { ...component.data(), $route: { query: { courseId: id, title: 'Forged URL title', lessonId: 'lesson-A' } }, $router: { push: x => pushes.push(x), replace: x => replaces.push(x) }, $t: x => x, $message: { error: x => notices.push(x), success: x => notices.push(x) } };
  for (const [key, f] of Object.entries(component.methods)) s[key] = f.bind(s);
  for (const [key, f] of Object.entries(component.computed || {})) Object.defineProperty(s, key, { get: () => f.call(s) });
  return { s, c: calls, component, pushes, replaces, notices };
}
const metadata = id => ({ courseId: id, courseKey: 'course-' + id, title: 'Server ' + id });
const lesson = (id, version, status = 'published') => ({ lessonId: id, lessonKey: 'same-key', lessonVersion: version, status, title: 'Version ' + version, topicTags: [] });
test('deep link never trusts URL title and reads authoritative course before list', () => {
  const { s, c, component } = setup();
  assert.equal(s.courseTitle, ''); component.created.call(s);
  assert.equal(c.getCourse.length, 1); assert.equal(c.listAuthoritativeLessons, undefined);
  c.getCourse[0].ok(metadata(A)); assert.equal(s.courseTitle, 'Server ' + A);
  assert.equal(c.listAuthoritativeLessons[0].args[0], A);
});
test('A to B clears old title/rows/actions immediately and ignores late A success', () => {
  const { s, c, component } = setup(); component.created.call(s);
  c.getCourse[0].ok(metadata(A)); c.listAuthoritativeLessons[0].ok([lesson('published-A', 1)]);
  s.$route.query.courseId = B; component.watch.courseId.call(s, B, A); component.watch['$route.query'].call(s);
  assert.equal(s.courseTitle, ''); assert.deepEqual(s.list, []); s.openCreate(); assert.equal(s.dialogVisible, false);
  c.getCourse[1].ok(metadata(B)); c.listAuthoritativeLessons[0].ok([lesson('late-A', 1)]);
  c.listAuthoritativeLessons[1].ok([lesson('published-B', 1)]);
  assert.equal(s.courseTitle, 'Server ' + B); assert.equal(s.list[0].lessonId, 'published-B');
});
test('late A metadata error cannot replace B route notice', () => {
  const { s, c, component, notices } = setup(); component.created.call(s);
  s.$route.query.courseId = B; component.watch.courseId.call(s, B, A); component.watch['$route.query'].call(s);
  c.getCourse[1].ok(metadata(B)); c.getCourse[0].fail('late-A'); assert.deepEqual(notices, []);
});
for (const id of ['', '../bad', ['a', 'b']]) test('invalid course route does not call an API: ' + JSON.stringify(id), () => {
  const { s, c, component } = setup('CourseLessons', id); component.created.call(s);
  assert.deepEqual(c, {}); assert.equal(s.dialogVisible, false);
});
test('mismatched metadata identity blocks list and create', () => {
  const { s, c, component } = setup(); component.created.call(s); c.getCourse[0].ok(metadata(B));
  assert.equal(c.listAuthoritativeLessons, undefined); s.openCreate(); assert.equal(s.dialogVisible, false); assert.equal(s.listFailed, true);
});
test('history exposes highest draft while published-first browsing row remains unchanged', () => {
  const { s, c, component, pushes } = setup(); component.created.call(s); c.getCourse[0].ok(metadata(A));
  const pub = lesson('published', 1); c.listAuthoritativeLessons[0].ok([pub]);
  c.listLessons[0].ok([pub, lesson('draft2', 2, 'draft'), lesson('draft3', 3, 'draft'), lesson('old-draft', 1, 'draft')]);
  assert.equal(s.list[0].lessonId, 'published'); assert.equal(s.existingDraft(pub).lessonId, 'draft3');
  s.openEditor(s.existingDraft(pub)); assert.equal(pushes[0].query.lessonId, 'draft3'); assert.equal(pushes[0].query.courseId, A);
});
test('late A history never surfaces an A draft under B', () => {
  const { s, c, component } = setup(); component.created.call(s); c.getCourse[0].ok(metadata(A));
  s.$route.query.courseId = B; component.watch.courseId.call(s, B, A); component.watch['$route.query'].call(s); c.getCourse[1].ok(metadata(B));
  c.listLessons[0].ok([lesson('late-A-draft', 2, 'draft')]); assert.equal(s.existingDraft(lesson('B', 1)), null);
});
test('destroy suppresses pending metadata/list/history callbacks', () => {
  const { s, c, component } = setup(); component.created.call(s); component.beforeDestroy.call(s);
  c.getCourse[0].ok(metadata(A)); assert.equal(c.listAuthoritativeLessons, undefined); assert.equal(s.courseTitle, '');
});
test('monitor query update resets event dialog and only paints B assignment rows', () => {
  const { s, c, component } = setup('LessonMonitoring'); component.created.call(s); s.openEvents({ assignmentId: 'A' });
  s.$route.query.lessonId = 'lesson-B'; s.$route.query.lessonVersion = '2'; component.watch['$route.query'].call(s);
  assert.equal(s.filters.lessonId, 'lesson-B'); assert.equal(s.eventsVisible, false); assert.deepEqual(s.list, []);
  c.listAssignments[0].ok([{ assignmentId: 'late-A' }]); c.getAssignmentEvents[0].ok([{ id: 'late-event' }]);
  c.listAssignments[1].ok([{ assignmentId: 'B', state: 'CANCELLED' }]); assert.equal(s.list[0].state, 'CANCELLED'); assert.deepEqual(s.events, []);
});
test('monitor destroy invalidates pending lists/events', () => {
  const { s, c, component } = setup('LessonMonitoring'); component.created.call(s); s.openEvents({ assignmentId: 'A' }); component.beforeDestroy.call(s);
  c.listAssignments[0].ok([{ assignmentId: 'late' }]); c.getAssignmentEvents[0].ok([{ id: 'late' }]); assert.deepEqual(s.list, []); assert.deepEqual(s.events, []);
});

for (const key of ['constructor', '__proto__']) test('draft discovery accepts literal special key ' + key, () => {
  const { s, c, component } = setup(); component.created.call(s); c.getCourse[0].ok(metadata(A));
  const pub = { ...lesson('published', 1), lessonKey: key };
  c.listLessons[0].ok([{ ...lesson('draft', 2, 'draft'), lessonKey: key }]);
  assert.equal(s.existingDraft(pub).lessonId, 'draft');
});

test('asset pack refusal stays distinguishable from active assignment conflict without auto retry', () => {
  const { s, c } = setup(); s.openAssignmentDialog(lesson('published', 1)); s.assignmentDialog.childId = 'child';
  s.createLessonAssignment({ deviceId: 'device', availability: 'available' });
  c.createAssignment[0].fail('Pack has not materialized', { status: 409, data: { code: 'ASSET_PACK_NOT_READY', retryable: true } });
  assert.match(s.assignmentDialog.statusMessage, /ASSET_PACK_NOT_READY/);
  assert.equal(s.assignmentDialog.submitting, false); assert.equal(s.assignmentDialog.readinessBlocked, true);
  s.createLessonAssignment({ deviceId: 'device', availability: 'available' }); assert.equal(c.createAssignment.length, 1);
});

test('monitor deep-link keyword and exact IDs hydrate on creation and clear on navigation', () => {
  const { s, c, component } = setup('LessonMonitoring');
  Object.assign(s.$route.query, { keyword: 'later page', childId: A, deviceId: B }); component.created.call(s);
  assert.equal(c.listAssignments[0].args[0].keyword, 'later page'); assert.equal(c.listAssignments[0].args[0].childId, A);
  s.$route.query = {}; component.watch['$route.query'].call(s);
  assert.equal(c.listAssignments[1].args[0].keyword, ''); assert.equal(c.listAssignments[1].args[0].childId, '');
});
test('pending A metadata mutation cannot leave B creation locked or change B form', () => {
  const { s, c, component } = setup(); component.created.call(s); c.getCourse[0].ok(metadata(A));
  s.openCreate(); Object.assign(s.form, { lessonKey: 'first', title: 'A', locale: 'en-US', ageBand: '4-6' }); s.submit();
  s.$route.query.courseId = B; component.watch.courseId.call(s, B, A); component.watch['$route.query'].call(s); c.getCourse[1].ok(metadata(B));
  s.openCreate(); Object.assign(s.form, { lessonKey: 'second', title: 'B', locale: 'en-US', ageBand: '4-6' }); s.submit();
  assert.equal(c.createLesson.length, 2); assert.equal(c.createLesson[1].args[0], B);
  c.createLesson[0].ok(lesson('late-A', 1, 'draft')); assert.equal(s.form.title, 'B'); assert.equal(s.dialogVisible, true);
  assert.equal(s.saving, true); s.submit(); assert.equal(c.createLesson.length, 2);
});
