const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../..');
const page = (n = 1, total = 260, size = 50) => ({ page: n, pageSize: size, total, totalPages: Math.ceil(total / size) });
function view(name, query = {}) {
  const pending = {}, routes = [];
  const api = new Proxy({}, { get: () => new Proxy({}, { get: (_, method) => (...args) => (pending[method] ||= []).push({ args, ok: args.at(-2), fail: args.at(-1) }) }) });
  const script = fs.readFileSync(path.join(root, 'src/views', name + '.vue'), 'utf8').split('<script>')[1].split('</script>')[0].replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'return');
  const c = new Function('Api', 'HeaderBar', 'AGE_BANDS', 'LOCALES', 'DEFAULT_AGE_BAND', 'DEFAULT_LOCALE', 'validateCourseForm', 'mutationDetails', 'uncertainMutation', 'pageFromQuery', script)(api, {}, [], [], '4-6', 'en-US', ...Object.values(require('../../src/utils/courseForm.cjs')), q => Number(q.page) || 1);
  const s = { ...c.data(), $route: { path: '/x', query }, $router: { push: r => { routes.push(r); return Promise.resolve(); }, replace: r => { routes.push(r); return Promise.resolve(); } }, $t: x => x, $message: { error() {}, warning() {}, success() {} } };
  for (const [k, f] of Object.entries(c.methods)) s[k] = f.bind(s);
  for (const [k, f] of Object.entries(c.computed || {})) Object.defineProperty(s, k, { get: () => f.call(s) });
  return { s, c, p: pending, routes };
}
function adapter(file) {
  let request;
  const src = fs.readFileSync(path.join(root, 'src/apis/module', file + '.js'), 'utf8').replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace(/export function/g, 'function').replace('export default', 'return');
  const helper = (() => { try { return require('../../src/utils/adminPagination.cjs'); } catch { return {}; } })();
  const a = new Function('getNestUrl', 'nestRequest', 'normalizeCourse', 'normalizeLesson', 'decodeList', 'listQuery', 'isPaged', src)(() => '/v1/admin', r => { request = r; }, r => r, r => r, helper.decodeList, helper.listQuery, helper.isPaged);
  return { a, request: () => request };
}
test('course opt-in serializes page and server keyword/kind while legacy callback remains array', () => {
  const { a, request } = adapter('course'); let rows, meta;
  a.getCourseList({ page: 2, pageSize: 50, keyword: 'late %_', kind: 'custom' }, (r, m) => { rows = r; meta = m; }, () => {});
  assert.match(request().url, /page=2/); assert.match(request().url, /keyword=late%20%25_/);
  request().onSuccess({ items: [{ id: 'B' }], pagination: page(2, 51) }); assert.equal(rows[0].id, 'B'); assert.equal(meta.total, 51);
  a.getCourseList(r => { rows = r; }, () => {}); request().onSuccess([{ id: 'legacy' }]); assert.equal(rows[0].id, 'legacy');
});
for (const payload of [[], { items: [] }, { items: [], pagination: page(1, -1) }, { items: [], pagination: { ...page(), totalPages: 1 } }, { items: [{}], pagination: page(1, 0) }]) test('paged malformed success is refused: ' + JSON.stringify(payload), () => {
  const { a, request } = adapter('course'); let success = false, failed = false;
  a.getCourseList({ page: 1, pageSize: 50 }, () => { success = true; }, () => { failed = true; });
  request().onSuccess(payload); assert.equal(success, false); assert.equal(failed, true);
});
test('learner opt-in reaches page beyond old 200 limit and retains metadata', () => {
  const { a, request } = adapter('courseInsights'); let meta, rows;
  a.listLearners({ page: 5, pageSize: 50, keyword: 'same name' }, (r, m) => { rows = r; meta = m; }, () => {});
  assert.match(request().url, /page=5/); assert.doesNotMatch(request().url, /limit=/);
  request().onSuccess({ learners: Array.from({ length: 50 }, (_, n) => ({ childId: 'child-' + (251 + n), childName: 'Same name' })), pagination: page(5) });
  assert.equal(rows[0].childId, 'child-251'); assert.equal(meta.total, 260);
});
test('quality legacy malformed success does not become an empty healthy page', () => {
  const { a, request } = adapter('courseInsights'); let success = false, failed = false;
  a.getCourseQuality({}, () => { success = true; }, () => { failed = true; }); request().onSuccess({});
  assert.equal(success, false); assert.equal(failed, true);
});
test('course route Back restores server page/filter, newest reversed page alone paints', () => {
  const { s, c, p } = view('CourseManagement', { page: '3', keyword: 'later', kind: 'custom' }); c.created.call(s);
  assert.equal(p.getCourseList[0].args[0].page, 3); assert.equal(p.getCourseList[0].args[0].keyword, 'later');
  s.$route.query = { page: '2', keyword: 'new', kind: 'template' }; c.watch['$route.query'].call(s);
  p.getCourseList[1].ok([{ courseId: 'B' }], page(2)); p.getCourseList[0].ok([{ courseId: 'A' }], page(3));
  assert.equal(s.list[0].courseId, 'B'); assert.equal(s.pagination.page, 2);
});
test('course delete empty final page moves to last valid server page', () => {
  const { s, c, p, routes } = view('CourseManagement', { page: '3' }); c.created.call(s);
  p.getCourseList[0].ok([], page(3, 100)); assert.equal(routes.at(-1).query.page, '2');
});
test('course keyword debounce invalidates pending old page immediately and resets route page', async () => {
  const { s, c, p, routes } = view('CourseManagement', { page: '4' }); c.created.call(s);
  s.courseKeyword = 'new'; s.searchList(true); p.getCourseList[0].ok([{ courseId: 'old' }], page(4));
  assert.deepEqual(s.list, []); await new Promise(r => setTimeout(r, 330)); assert.equal(routes.at(-1).query.page, '1');
});
test('learners route rehydrates page beyond 200 and quality cards are page aggregates', () => {
  const { s, c, p } = view('CourseInsights', { page: '5', keyword: 'late' }); c.created.call(s);
  assert.equal(p.listLearners[0].args[0].page, 5); assert.equal(p.listLearners[0].args[0].keyword, 'late');
  p.listLearners[0].ok([{ childId: '251', personality: { interests: [] } }], page(5)); assert.equal(s.learners[0].childId, '251');
  p.getCourseQuality[0].ok([{ assignments: 7, activeChildren: 2 }, { assignments: 3, activeChildren: 2 }], page(5));
  s.activeTab = 'quality';
  assert.equal(s.totalAssignments, 10); assert.equal(s.totalActiveChildren, 4);
  assert.match(fs.readFileSync(path.join(root, 'src/views/CourseInsights.vue'), 'utf8'), /pagination\.pageScope/);
});
test('lessons route preserves metadata authority and requests server keyword/status page', () => {
  const id = '11111111-1111-4111-8111-111111111111';
  const { s, c, p } = view('CourseLessons', { courseId: id, page: '2', keyword: 'later', status: 'published' }); c.created.call(s);
  p.getCourse[0].ok({ courseId: id, title: 'Server' });
  assert.equal(p.listAuthoritativeLessons[0].args[1].page, 2); assert.equal(p.listAuthoritativeLessons[0].args[1].keyword, 'later');
  assert.equal(p.listAuthoritativeLessons[0].args[1].status, 'published');
  p.listAuthoritativeLessons[0].ok([{ lessonId: 'B', title: 'Server match' }], page(2)); assert.equal(s.filteredList.length, 1);
});
test('assignment chooser can navigate tied learners beyond first page without changing selected target', () => {
  const { s, p } = view('CourseLessons');
  s.openAssignmentDialog({ lessonId: 'L', lessonKey: 'same', lessonVersion: 1 });
  s.assignmentDialog.childId = 'selected'; s.searchAssignmentLearners('same name', 6);
  assert.equal(p.listLearners.at(-1).args[0].page, 6); assert.equal(p.listLearners.at(-1).args[0].pageSize, 20);
  p.listLearners.at(-1).ok([{ childId: '251', childName: 'same name' }], page(6, 260, 20));
  assert.equal(s.assignmentDialog.learners[0].childId, '251'); assert.equal(s.assignmentDialog.childId, 'selected');
  assert.equal(s.assignmentDialog.learnerPagination.total, 260);
});
test('query page parsing rejects arrays and unsafe offsets', () => {
  const { pageFromQuery } = require('../../src/utils/adminPagination.cjs');
  assert.equal(pageFromQuery({ page: ['2'] }), 1); assert.equal(pageFromQuery({ page: '0' }), 1);
  assert.equal(pageFromQuery({ page: String(Number.MAX_SAFE_INTEGER) }), 1); assert.equal(pageFromQuery({ page: '5' }), 5);
});
test('missing stable row identity is rejected in paged and legacy course success', () => {
  const { a, request } = adapter('course'); let failed = 0;
  a.getCourseList({ page: 1, pageSize: 50 }, () => assert.fail('malformed success'), () => failed++);
  request().onSuccess({ items: [{}], pagination: page(1, 1) });
  a.getCourseList(() => assert.fail('malformed legacy success'), () => failed++); request().onSuccess([{}]);
  assert.equal(failed, 2);
});
test('quality for filtered catalog uses exact visible course identities and ignores earlier-page failure', () => {
  const { s, p } = view('CourseManagement'); s.list = [{ courseId: 'A' }]; s.fetchQuality();
  assert.equal(p.getCourseQuality[0].args[0].courseId, 'A');
  s.list = [{ courseId: 'B' }]; s.fetchQuality();
  p.getCourseQuality[1].ok([{ courseId: 'B', qualityScore: 75 }]); p.getCourseQuality[0].fail('old');
  assert.equal(s.qualityFailed, false); assert.equal(s.avgQuality, 75);
});
test('insights page-only navigation preserves unsaved selected child and pending write lock', () => {
  const { s, c } = view('CourseInsights', { page: '1' }); c.created.call(s);
  s.selectedLearner = { childId: 'A' }; s.personalityForm.interestsText = 'unsaved'; s.savingPersonality = true;
  s.$route.query = { page: '2' }; c.watch['$route.query'].call(s);
  assert.equal(s.selectedLearner.childId, 'A'); assert.equal(s.personalityForm.interestsText, 'unsaved'); assert.equal(s.savingPersonality, true);
});
test('course page selection invalidates old callbacks before the router settles', () => {
  const { s, c, p } = view('CourseManagement'); c.created.call(s); s.setListPage(2);
  p.getCourseList[0].ok([{ courseId: 'old' }], page()); assert.deepEqual(s.list, []);
});
test('catalog quality fanout stays bounded to four concurrent requests', () => {
  const { s, p } = view('CourseManagement'); s.list = Array.from({ length: 10 }, (_, i) => ({ courseId: 'C' + i })); s.fetchQuality();
  assert.equal(p.getCourseQuality.length, 4); p.getCourseQuality[0].ok([{ courseId: 'C0' }]); assert.equal(p.getCourseQuality.length, 5);
});
test('incomplete success page cannot masquerade as empty while snapshot total says rows exist', () => {
  const { a, request } = adapter('course'); let failed = false;
  a.getCourseList({ page: 1, pageSize: 50 }, () => assert.fail('incomplete page'), () => { failed = true; });
  request().onSuccess({ items: [], pagination: page(1, 51) }); assert.equal(failed, true);
});
