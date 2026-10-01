const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { calculateReadiness } = require('../../src/components/lesson/lesson-builder-logic');
const source = fs.readFileSync(path.join(__dirname, '../../src/components/lesson/LessonPublishReadiness.vue'), 'utf8');
const script = source.split('<script>')[1].split('</script>')[0]
  .replace(/^import .*;$/gm, '').replace('export default', 'return');
const component = new Function('calculateReadiness', script)(calculateReadiness);
const validation = { budgets: { espTft: { errors: [], warnings: [], metrics: {
  packBytes: 100, uniqueAssetCount: 2, sharedAssetCount: 1,
  estimatedVisualPeakBytes: 100, offlineReady: true, allPathsTerminate: true,
} } } };
const result = { valid: true, profiles: ['espTft'], errors: [], warnings: [], findings: [] };
function setup(props = {}) {
  const vm = { steps: [], assets: [], manifest: {}, validation: null,
    validationResult: null, validationCurrent: false, $t: key => key, ...component.methods, ...props };
  for (const [key, get] of Object.entries(component.computed)) {
    Object.defineProperty(vm, key, { get: () => get.call(vm) });
  }
  return vm;
}
test('unvalidated lesson displays unknown checks instead of failures', () => {
  const vm = setup();
  for (const key of ['offline', 'paths']) {
    const row = vm.budgetRows.find(row => row.key === key);
    assert.equal(row.value, 'lesson.validationMissing');
    assert.equal(row.pass, null);
  }
  assert.equal(vm.ready, false);
});
test('stale passing budget does not claim current offline or termination proof', () => {
  const vm = setup({ validation, validationResult: result });
  for (const key of ['offline', 'paths']) {
    assert.equal(vm.budgetRows.find(row => row.key === key).value, 'lesson.proofStale');
  }
  assert.equal(vm.ready, false);
});
test('raw budget without current validation result cannot mark lesson ready', () => {
  assert.equal(setup({ validation }).ready, false);
});
test('current validation retains pass and fail budget evidence', () => {
  const vm = setup({ validation, validationResult: result, validationCurrent: true });
  assert.equal(vm.ready, true);
  assert.equal(vm.budgetRows.find(row => row.key === 'offline').value, 'lesson.statusPass');
  vm.validation = { budgets: { espTft: { ...validation.budgets.espTft,
    metrics: { ...validation.budgets.espTft.metrics, offlineReady: false } } } };
  assert.equal(vm.ready, false);
  assert.equal(vm.budgetRows.find(row => row.key === 'offline').value, 'lesson.statusFail');
});
