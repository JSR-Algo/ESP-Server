import fs from 'node:fs';
import path from 'node:path';

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

expectContains('src/views/LessonEditor.vue', '<lesson-step-prompt-editor', 'selected draft steps need prompt editing');
expectContains('src/views/LessonEditor.vue', 'prompt: this.promptDraft', 'save must persist the edited prompt');
expectContains('src/views/LessonEditor.vue', 'promptDirty', 'prompt edits need explicit dirty tracking');
expectContains('src/views/LessonEditor.vue', 'selectedStep.stepKey', 'prompt drafts must reset when the selected step changes');
expectContains(
  'src/views/LessonEditor.vue',
  'this.promptStepKey === this.selectedStep.stepKey',
  'a previous step prompt draft must never enable saving for the next selection',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /fetchSteps\(.*?[\s\S]*?this\.steps\s*=\s*rows[\s\S]*?resetPromptDraft/m,
  'fresh server fetches must reset prompt draft state',
);
expectRegex(
  'src/views/LessonEditor.vue',
  /Api\.lesson\.updateStep\([\s\S]*?prompt:\s*this\.promptDraft[\s\S]*?fetchSteps/m,
  'dirty state must clear through a server-confirmed refetch',
);

expectContains('src/components/lesson/LessonStepPromptEditor.vue', "this.$emit('input'", 'editor must be controlled');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', ':value="value"', 'editor must render its value prop');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', ':disabled="disabled"', 'editor must honor read-only lessons');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', 'maxlength="500"', 'prompt length must be capped at 500 characters');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', 'show-word-limit', 'authors must see the prompt character count');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "lesson.promptEditorLabel", 'prompt editor needs a localized accessible label');
expectContains('src/components/lesson/LessonStepPromptEditor.vue', "lesson.promptEditorHint", 'prompt editor needs localized guidance');

for (const locale of ['src/i18n/en.js', 'src/i18n/vi.js']) {
  expectContains(locale, "'lesson.promptEditorLabel'", 'prompt editor label must be localized');
  expectContains(locale, "'lesson.promptEditorHint'", 'prompt editor hint must be localized');
  expectContains(locale, "'lesson.stepSaved'", 'save confirmation must be localized');
}

console.log('lesson editor UI contracts OK');
