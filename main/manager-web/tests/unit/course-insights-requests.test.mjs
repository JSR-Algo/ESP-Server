import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../../src/views/CourseInsights.vue', import.meta.url), 'utf8');
const script = source.split('<script>')[1].split('</script>')[0].replace(/^import .*;$/gm, '').replace('export default', 'return');
const learner = (childId, interests = [childId]) => ({ childId, personality: { interests, attentionSpanSec: 120 } });
function setup() {
  const requests = { listLearners: [], previewLearnerLessons: [], getCourseQuality: [], updateLearnerPersonality: [] };
  const api = Object.fromEntries(Object.keys(requests).map(key => [key, (...args) => requests[key].push(args)]));
  const component = new Function('Api', 'HeaderBar', script)({ courseInsights: api }, {});
  const messages = [];
  const vm = { ...component.data(), $t: key => key, $message: { success: msg => messages.push(msg), error: msg => messages.push(msg) } };
  for (const [key, method] of Object.entries(component.methods)) vm[key] = method.bind(vm);
  return { vm, requests, messages, destroy: () => component.beforeDestroy?.call(vm) };
}
const succeed = (request, value) => request.at(-2)(value, { page: 1, pageSize: 50, total: Array.isArray(value) ? value.length : 0, totalPages: Array.isArray(value) && value.length ? 1 : 0 });
const fail = request => request.at(-1)('failed');

for (const roundTrip of [false, true]) test(`save response respects selection identity${roundTrip ? ' through A/B/A' : ''}`, () => {
  const { vm, requests, messages } = setup();
  vm.selectLearner(learner('A')); vm.savePersonality();
  vm.selectLearner(learner('B'));
  if (roundTrip) vm.selectLearner(learner('A', ['new draft']));
  const selected = vm.selectedLearner; const form = { ...vm.personalityForm };
  succeed(requests.updateLearnerPersonality[0], learner('A', ['saved old']));
  assert.equal(vm.selectedLearner, selected);
  assert.deepEqual(vm.personalityForm, form);
  assert.equal(vm.savingPersonality, false);
  assert.deepEqual(messages, ['insights.personalitySaved: A']);
  vm.savePersonality();
  assert.equal(requests.updateLearnerPersonality[1][0], selected.childId);
  assert.deepEqual(requests.updateLearnerPersonality[1][1].interests, vm.parseInterests());
});

test('duplicate saves remain blocked even after selecting another learner', () => {
  const { vm, requests } = setup();
  vm.selectLearner(learner('A')); vm.savePersonality(); vm.savePersonality();
  vm.selectLearner(learner('B')); vm.savePersonality();
  assert.equal(requests.updateLearnerPersonality.length, 1);
  fail(requests.updateLearnerPersonality[0]);
  vm.savePersonality(); assert.equal(requests.updateLearnerPersonality.length, 2);
});

test('save preserves edits made while pending and same-row table selection', () => {
  const { vm, requests } = setup();
  vm.selectLearner(learner('A')); vm.savePersonality();
  vm.personalityForm.interestsText = 'new edit';
  succeed(requests.updateLearnerPersonality[0], learner('A', ['saved']));
  vm.selectLearner(learner('A', ['saved']));
  assert.equal(vm.personalityForm.interestsText, 'new edit');
});

for (const outcome of ['success', 'error']) test(`selection change fences save ${outcome}`, () => {
  const { vm, requests, messages } = setup();
  vm.selectLearner(learner('A')); vm.savePersonality(); vm.selectLearner(learner('B'));
  if (outcome === 'success') succeed(requests.updateLearnerPersonality[0], learner('A'));
  else fail(requests.updateLearnerPersonality[0]);
  assert.equal(vm.selectedLearner.childId, 'B'); assert.deepEqual(messages, outcome === 'success' ? ['insights.personalitySaved: A'] : []);
});

for (const [method, api, state, loading, value] of [
  ['fetchLearners', 'listLearners', 'learners', 'learnersLoading', [learner('B')]],
  ['fetchPreview', 'previewLearnerLessons', 'previewLessons', 'previewLoading', { lessons: ['B'] }],
  ['fetchQuality', 'getCourseQuality', 'qualityRows', 'qualityLoading', ['B']],
]) {
  for (const outcome of ['success', 'error']) test(`${method} ignores superseded ${outcome}`, () => {
    const { vm, requests, messages } = setup(); vm.selectedLearner = learner('B');
    vm[method](); vm[method]();
    if (outcome === 'success') succeed(requests[api][0], value); else fail(requests[api][0]);
    assert.equal(vm[loading], true); assert.deepEqual(vm[state], []); assert.deepEqual(messages, []);
    succeed(requests[api][1], value);
    assert.equal(vm[loading], false); assert.deepEqual(vm[state], value.lessons || value);
  });
  for (const outcome of ['success', 'error']) test(`${method} ignores post-destroy ${outcome}`, () => {
    const { vm, requests, messages, destroy } = setup(); vm.selectedLearner = learner('B');
    vm[method](); destroy(); const before = JSON.stringify(vm);
    if (outcome === 'success') succeed(requests[api][0], value); else fail(requests[api][0]);
    assert.equal(JSON.stringify(vm), before); assert.deepEqual(messages, []);
  });
  test(`${method} retains latest results when an older response arrives last`, () => {
    const { vm, requests } = setup(); vm.selectedLearner = learner('B');
    vm[method](); vm[method](); succeed(requests[api][1], value);
    succeed(requests[api][0], api === 'previewLearnerLessons' ? { lessons: ['obsolete'] } : ['obsolete']);
    assert.deepEqual(vm[state], value.lessons || value);
  });
  test(`${method} reports current failure and releases loading`, () => {
    const { vm, requests, messages } = setup(); vm.selectedLearner = learner('B');
    vm[method](); fail(requests[api][0]);
    assert.equal(vm[loading], false); assert.deepEqual(messages, ['failed']);
  });
}

test('current save reports success, refreshes persisted reads, and allows another save', () => {
  const { vm, requests, messages } = setup();
  vm.selectLearner(learner('A')); vm.savePersonality();
  succeed(requests.updateLearnerPersonality[0], learner('A', ['saved']));
  assert.deepEqual(vm.selectedLearner.personality.interests, ['saved']);
  assert.deepEqual(messages, ['insights.personalitySaved: A']);
  assert.equal(requests.listLearners.length, 0);
  assert.equal(requests.previewLearnerLessons.length, 2);
  vm.savePersonality(); assert.equal(requests.updateLearnerPersonality.length, 2);
});

test('current save failure reports the error and permits retry', () => {
  const { vm, requests, messages } = setup();
  vm.selectLearner(learner('A')); vm.savePersonality(); fail(requests.updateLearnerPersonality[0]);
  assert.deepEqual(messages, ['failed']);
  vm.savePersonality(); assert.equal(requests.updateLearnerPersonality.length, 2);
});

for (const outcome of ['success', 'error']) test(`save ignores post-destroy ${outcome}`, () => {
  const { vm, requests, messages, destroy } = setup();
  vm.selectLearner(learner('A')); vm.savePersonality(); destroy(); const before = JSON.stringify(vm);
  if (outcome === 'success') succeed(requests.updateLearnerPersonality[0], learner('A')); else fail(requests.updateLearnerPersonality[0]);
  assert.equal(JSON.stringify(vm), before); assert.deepEqual(messages, []);
});

test('new preview and quality requests clear old data immediately', () => {
  const { vm, requests } = setup();
  vm.selectLearner(learner('A')); succeed(requests.previewLearnerLessons[0], { lessons: ['A'] });
  vm.selectLearner(learner('B')); assert.deepEqual(vm.previewLessons, []);
  vm.selectLearner(learner('A')); succeed(requests.previewLearnerLessons[0], { lessons: ['obsolete A'] });
  assert.deepEqual(vm.previewLessons, []);
  vm.qualityRows = ['90 days']; vm.qualityWindow = 7; vm.fetchQuality();
  assert.deepEqual(vm.qualityRows, []);
});

test('A/B/A selection suppresses an old save error and releases the write lock', () => {
 const { vm, requests, messages } = setup();
 vm.selectLearner(learner('A')); vm.savePersonality(); vm.selectLearner(learner('B')); vm.selectLearner(learner('A', ['new edit']));
 fail(requests.updateLearnerPersonality[0]);
 assert.deepEqual(messages, []); assert.equal(vm.savingPersonality, false); assert.equal(vm.personalityForm.interestsText, 'new edit');
});
