import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = process.cwd();

function read(rel) {
  return fs.readFileSync(path.join(root, rel), 'utf8');
}

function expectContains(file, needle, reason) {
  const body = read(file);
  if (!body.includes(needle)) {
    throw new Error(`${file} missing ${needle}: ${reason}`);
  }
}

function expectRegex(file, regex, reason) {
  const body = read(file);
  if (!regex.test(body)) {
    throw new Error(`${file} missing ${regex}: ${reason}`);
  }
}

function expectNotContains(file, needle, reason) {
  const body = read(file);
  if (body.includes(needle)) {
    throw new Error(`${file} unexpectedly contains ${needle}: ${reason}`);
  }
}

function extractObjectMethod(source, name) {
  const marker = `${name}(`;
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`${name} method not found`);
  const paramsStart = source.indexOf('(', start);
  const paramsEnd = source.indexOf(')', paramsStart);
  const braceStart = source.indexOf('{', paramsEnd);
  let depth = 0;
  for (let i = braceStart; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1;
    if (source[i] === '}') {
      depth -= 1;
      if (depth === 0) {
        const params = source.slice(paramsStart + 1, paramsEnd);
        const body = source.slice(braceStart + 1, i);
        return `function ${name}(${params}) {${body}}`;
      }
    }
  }
  throw new Error(`${name} method body not closed`);
}

expectContains('src/views/LessonEditor.vue', '<lesson-step-prompt-editor', 'selected draft steps need prompt editing');
expectContains('src/views/LessonEditor.vue', 'prompt: this.promptDraft', 'save must persist the edited prompt');
expectContains('src/views/LessonEditor.vue', 'promptDirty', 'prompt edits need explicit dirty tracking');
expectContains('src/views/LessonEditor.vue', 'promptEditRevision', 'prompt edits need revision tracking');
expectContains('src/views/LessonEditor.vue', 'stepEditRevisions', 'all step drafts need revision tracking');
expectContains('src/views/LessonEditor.vue', 'promptSaveRequestId', 'save/refetch callbacks need request gating');
expectContains('src/views/LessonEditor.vue', 'selectedStep.stepKey', 'prompt drafts must reset when the selected step changes');
expectContains(
  'src/views/LessonEditor.vue',
  'this.promptStepKey === this.selectedStep.stepKey',
  'a previous step prompt draft must never enable saving for the next selection',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /findIndex\(\(step\)\s*=>\s*step\.stepKey\s*===\s*selectedKey\)/m,
  'fresh fetches must preserve the latest selected step by key',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /Api\.lesson\.updateStep\([\s\S]*?prompt:\s*this\.promptDraft[\s\S]*?fetchSteps/m,
  'dirty state must clear through a server-confirmed refetch',
);

expectNotContains('src/components/lesson/LessonStepPromptEditor.vue', 'this.$emit', 'Vue 2 template handlers must not reference this');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "$emit('input', $event)", 'editor must emit input with Vue 2 template syntax');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', ':value="value"', 'editor must render its value prop');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', ':disabled="disabled"', 'editor must honor read-only lessons');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', 'maxlength="500"', 'prompt length must be capped at 500 characters');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', 'show-word-limit', 'authors must see the prompt character count');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "lesson.promptEditorLabel", 'prompt editor needs a localized accessible label');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "lesson.promptEditorHint", 'prompt editor needs localized guidance');

expectRegex(
  'src/views/LessonEditor.vue',
  /:disabled="promptDirty\s*\|\|\s*savingStep"/m,
  'manifest preview must be disabled while the prompt is unsaved or saving',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /<lesson-step-prompt-editor[\s\S]*?:disabled="!isDraft\s*\|\|\s*savingStep"/m,
  'prompt input must be disabled during its save request',
);

const editorSource = read('src/views/LessonEditor.vue');
const shouldApplySavedStepState = vm.runInNewContext(`(${extractObjectMethod(editorSource, 'shouldApplySavedStepState')})`);
const matching = {
  promptSaveRequestId: 7,
  promptStepKey: 's2',
  promptEditRevision: 4,
  stepEditRevisions: { s2: 9 },
};
const guard = { requestId: 7, stepKey: 's2', promptRevision: 4, stepRevision: 9 };
if (!shouldApplySavedStepState.call(matching, guard)) {
  throw new Error('matching save state must be eligible for reset after refetch');
}
for (const changed of [
  { ...matching, promptSaveRequestId: 8 },
  { ...matching, promptStepKey: 's3' },
  { ...matching, promptEditRevision: 5 },
  { ...matching, stepEditRevisions: { s2: 10 } },
]) {
  if (shouldApplySavedStepState.call(changed, guard)) {
    throw new Error('stale save/refetch state must not reset a newer or differently selected draft');
  }
}

for (const locale of ['src/i18n/en.js', 'src/i18n/vi.js']) {
  expectContains(locale, "'lesson.promptEditorLabel'", 'prompt editor label must be localized');
  expectContains(locale, "'lesson.promptEditorHint'", 'prompt editor hint must be localized');
  expectContains(locale, "'lesson.stepSaved'", 'save confirmation must be localized');
}

expectContains('src/components/lesson/SharedAssetPicker.vue', "this.$emit('select-intent'", 'selection must review impact first');
expectNotContains('src/components/lesson/SharedAssetPicker.vue', "$emit('select', asset)", 'shared selection must not mutate a draft before review');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'reviewSharedVisualImpact', 'dialog must load backend usage truth');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'cloneSharedVisual', 'dialog must clone without mutating source pins');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', "profile: 'espTft'", 'clone payload must target the firmware profile');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'scope.row.lessonKey', 'every backend lesson usage must be rendered');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'scope.row.lessonVersion', 'every backend lesson version must be rendered');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'localAffectedStepKeys', 'the current draft step references must be visible');
expectContains('src/components/lesson/SharedVisualImpactDialog.vue', 'cloneKey', 'the collision-free clone key must be visible');
expectContains('src/components/LessonAssetManager.vue', "this.$emit('impact-review-request'", 'shared replacement must request review first');
expectContains('src/components/LessonAssetManager.vue', 'confirmReplace', 'replacement mode needs an explicit parent confirmation gate');
expectRegex(
  'src/views/LessonEditor.vue',
  /replaceStepAssetReference\(step\.stepBody\s*\|\|\s*\{\},\s*intent\.asset\.assetKey,\s*clonedAsset\)/m,
  'clone must rewrite only the selected step body',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /Api\.lesson\.updateStep\([\s\S]*?step\.stepKey[\s\S]*?fetchSteps[\s\S]*?reloadAssets[\s\S]*?preview\s*=\s*null[\s\S]*?previewManifest\s*=\s*null/m,
  'clone rebind must wait for server confirmation before refetch and preview invalidation',
);

for (const locale of ['src/i18n/en.js', 'src/i18n/vi.js']) {
  for (const key of [
    'lesson.sharedImpactTitle',
    'lesson.sharedImpactKeep',
    'lesson.sharedImpactClone',
    'lesson.sharedImpactUsages',
    'lesson.sharedImpactLocalSteps',
    'lesson.sharedImpactCloneKey',
  ]) expectContains(locale, `'${key}'`, 'shared visual review must be localized');
}

console.log('lesson editor UI contracts OK');
