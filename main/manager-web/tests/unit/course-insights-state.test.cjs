const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

function setup(view = 'CourseInsights') {
  const pending = {};
  const api = new Proxy({}, { get: (_, group) => new Proxy({}, { get: (_, method) => (...args) => {
    (pending[method] ||= []).push({ args, ok: args.at(-2), fail: args.at(-1) });
  } }) });
  const source = fs.readFileSync(path.join(__dirname, '../../src/views', `${view}.vue`), 'utf8');
  const script = source.split('<script>')[1].split('</script>')[0]
    .replace(/import[\s\S]*?from\s+['"][^'"]+['"];?/g, '').replace('export default', 'return');
  const component = new Function('Api', 'HeaderBar', 'AGE_BANDS', 'LOCALES', 'DEFAULT_AGE_BAND', 'DEFAULT_LOCALE', script)(api, {}, [], [], '3-5', 'en');
  const messages = [];
  const state = { ...component.data(), $route: { query: { courseId: 'A' } }, $router: { replace() {} },
    $t: k => k, $message: Object.fromEntries(['error','warning','success'].map(k => [k, msg => messages.push([k, msg])])) };
  for (const [k, f] of Object.entries(component.methods)) state[k] = f.bind(state);
  for (const [k, f] of Object.entries(component.computed || {})) Object.defineProperty(state, k, { get: () => f.call(state) });
  return { state, component, pending, messages };
}
const learner = id => ({ childId: id, childName: `Child ${id}`, personality: { interests: [], learningStyle: '', parentCareer: '', vocabularyLevel: '', attentionSpanSec: 120 }, stats: {} });

for (const outcome of ['ok','fail']) test(`preview ignores obsolete ${outcome} while newer request loads`, () => {
  const { state: s, pending: p, messages } = setup();
  s.selectLearner(learner('A')); s.selectLearner(learner('B'));
  p.previewLearnerLessons[0][outcome](outcome === 'ok' ? { lessons: [{ lessonId: 'A' }] } : 'old error');
  assert.equal(s.previewLoading, true); assert.equal(messages.length, 0);
  p.previewLearnerLessons[1].ok({ lessons: [{ lessonId: 'B' }] });
  p.previewLearnerLessons[0].ok({ lessons: [{ lessonId: 'A' }] });
  assert.equal(s.previewLessons[0].lessonId, 'B');
});
test('selection clears preview and same-child refresh preserves draft', () => {
  const { state: s, pending: p } = setup(); s.selectLearner(learner('A'));
  p.previewLearnerLessons[0].ok({ lessons: [{ lessonId: 'A' }] });
  s.selectLearner(learner('B')); assert.equal(s.previewLessons.length, 0);
  s.personalityForm.interestsText = 'unsaved'; s.selectLearner(learner('B'));
  assert.equal(s.personalityForm.interestsText, 'unsaved');
});
test('save A updates only A list entry and preserves B draft and selection', () => {
  const { state: s, pending: p, messages } = setup(); s.learners = [learner('A'),learner('B')];
  s.selectLearner(s.learners[0]); s.savePersonality(); s.selectLearner(s.learners[1]); s.personalityForm.interestsText = 'B draft';
  const saved = learner('A'); saved.personality.interests = ['saved']; p.updateLearnerPersonality[0].ok(saved);
  assert.equal(s.selectedLearner.childId, 'B'); assert.equal(s.personalityForm.interestsText, 'B draft');
  assert.equal(s.learners[0].personality.interests[0], 'saved'); assert.match(messages[0][1], /Child A/);
  assert.equal(p.listLearners, undefined);
});
test('same-child save keeps edits made while pending and suppresses duplicate submit', () => {
  const { state: s, pending: p } = setup(); s.selectLearner(learner('A')); s.savePersonality(); s.savePersonality();
  assert.equal(p.updateLearnerPersonality.length, 1); s.personalityForm.interestsText = 'new draft';
  p.updateLearnerPersonality[0].ok(learner('A')); assert.equal(s.personalityForm.interestsText, 'new draft');
});
for (const [view, method, rows, loading, failed] of [
  ['CourseInsights','fetchLearners','learners','learnersLoading','learnersFailed'],
  ['CourseInsights','fetchQuality','qualityRows','qualityLoading','qualityFailed'],
  ['CourseManagement','fetchList','list','loading','listFailed'],
  ['CourseManagement','fetchQuality','qualityRows','qualityLoading','qualityFailed'],
  ['CourseLessons','fetchList','list','loading','listFailed'],
]) {
  const api = method === 'fetchLearners' ? 'listLearners' : method === 'fetchQuality' ? 'getCourseQuality' : view === 'CourseLessons' ? 'listAuthoritativeLessons' : 'getCourseList';
  test(`${view} ${method}: newest response wins, stale error cannot settle loading`, () => {
    const { state:s, pending:p, messages }=setup(view); s[method](); s[method](); p[api][0].fail('old');
    assert.equal(s[loading],true); assert.equal(messages.length,0);
    p[api][1].ok([]); p[api][0].ok([learner('old')]); assert.equal(s[rows].length,0);
  });
  test(`${view} ${method}: empty and unavailable differ, old rows are cleared`, () => {
    const { state:s, pending:p }=setup(view); s[rows]=[learner('old')]; s[method]();
    assert.equal(s[rows].length,0); p[api][0].fail('503'); assert.equal(s[failed],true);
    assert.equal(s[rows].length,0); s[method](); p[api][1].ok([]); assert.equal(s[failed],false);
  });
  test(`${view} ${method}: destroyed component ignores success and error`, () => {
    const { state:s, component:c, pending:p, messages }=setup(view); s[method]();
    assert.equal(typeof c.beforeDestroy,'function'); c.beforeDestroy.call(s);
    const before=JSON.stringify(s); p[api][0].ok([learner('late')]); p[api][0].fail('late');
    assert.equal(JSON.stringify(s),before); assert.equal(messages.length,0);
  });
}
test('Insights route reuse resets identity and invalidates pending save and preview', () => {
  const { state:s, component:c, pending:p, messages }=setup(); s.selectLearner(learner('A')); s.savePersonality(); s.fetchQuality();
  s.$route.query={ tab:'quality',courseId:'B',keyword:'B' };
  assert.equal(typeof c.watch?.['$route.query'],'function'); c.watch['$route.query'].call(s);
  assert.equal(s.qualityCourseId,'B'); assert.equal(s.selectedLearner.childId,undefined);
  p.previewLearnerLessons[0].ok({lessons:[{}]}); p.updateLearnerPersonality[0].ok(learner('A')); p.getCourseQuality[0].fail('old');
  assert.equal(s.previewLessons.length,0); assert.equal(s.qualityLoading,true); assert.equal(messages.length,0);
});
test('Lessons route reuse fetches the new course and rejects old list', () => {
  const { state:s, component:c, pending:p }=setup('CourseLessons'); s.fetchList(); s.$route.query={courseId:'B'};
  assert.equal(typeof c.watch?.courseId,'function'); c.watch.courseId.call(s);
  assert.equal(p.listAuthoritativeLessons[1].args[0],'B'); p.listAuthoritativeLessons[0].ok([{lessonId:'A'}]);
  assert.equal(s.list.length,0); assert.equal(s.loading,true); p.listAuthoritativeLessons[1].ok([{lessonId:'B'}]); assert.equal(s.list[0].lessonId,'B');
});
for (const outcome of ['ok', 'fail']) test(`destroyed Insights ignores preview and save ${outcome}`, () => {
  const { state:s, component:c, pending:p, messages }=setup();
  s.selectLearner(learner('A')); s.savePersonality(); c.beforeDestroy.call(s);
  const before=JSON.stringify(s);
  p.previewLearnerLessons[0][outcome](outcome === 'ok' ? {lessons:[{}]} : 'late');
  p.updateLearnerPersonality[0][outcome](outcome === 'ok' ? learner('A') : 'late');
  assert.equal(JSON.stringify(s),before); assert.equal(messages.length,0);
});
test('preview A-B-A rejects first A response even though child identity matches again', () => {
  const { state:s, pending:p }=setup();
  s.selectLearner(learner('A')); s.selectLearner(learner('B')); s.selectLearner(learner('A'));
  p.previewLearnerLessons[0].ok({lessons:[{lessonId:'old A'}]}); assert.equal(s.previewLoading,true);
  assert.equal(s.previewLessons.length,0); p.previewLearnerLessons[2].ok({lessons:[{lessonId:'new A'}]});
  assert.equal(s.previewLessons[0].lessonId,'new A');
});
test('quality metrics are unknown while loading or unavailable and zero only for successful empty result', () => {
  const { state:s,pending:p }=setup(); s.qualityRows=[{qualityScore:99,assignments:20,activeChildren:10,riskLevel:'attention'}];
  s.fetchQuality(); for (const key of ['avgQuality','totalAssignments','totalActiveChildren','attentionCourses']) assert.equal(s[key],'-');
  p.getCourseQuality[0].fail('503'); assert.equal(s.avgQuality,'-');
  s.fetchQuality(); p.getCourseQuality[1].ok([]); assert.equal(s.avgQuality,0);
});
test('save failure for A cannot show an error in B detail', () => {
  const {state:s,pending:p,messages}=setup(); s.selectLearner(learner('A')); s.savePersonality();
  s.selectLearner(learner('B')); s.personalityForm.interestsText='B draft';
  p.updateLearnerPersonality[0].fail('A failed');
  assert.equal(messages.length,0); assert.equal(s.personalityForm.interestsText,'B draft');
  assert.equal(s.savingPersonality,false); assert.equal(s.previewLoading,true);
});
