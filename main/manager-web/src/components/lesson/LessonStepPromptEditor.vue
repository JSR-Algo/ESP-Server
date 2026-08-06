<template>
  <div class="lesson-step-prompt-editor">
    <label class="lesson-step-prompt-editor__label" for="lesson-step-prompt">
      {{ $t('lesson.promptEditorLabel') }}
    </label>
    <el-input
      id="lesson-step-prompt"
      data-testid="lesson-step-prompt"
      type="textarea"
      :value="value"
      :disabled="disabled"
      :rows="3"
      maxlength="500"
      show-word-limit
      :aria-label="$t('lesson.promptEditorLabel')"
      aria-describedby="lesson-step-prompt-hint"
      @input="$emit('input', $event)"
    />
    <p id="lesson-step-prompt-hint" class="lesson-step-prompt-editor__hint">
      {{ $t('lesson.promptEditorHint') }}
    </p>
  </div>
</template>

<script>
export default {
  name: 'LessonStepPromptEditor',
  props: {
    value: {
      type: String,
      default: '',
    },
    disabled: {
      type: Boolean,
      default: false,
    },
  },
};
</script>

<style scoped>
.lesson-step-prompt-editor__label {
  color: #606266;
  display: block;
  font-size: 14px;
  line-height: 1.4;
  margin-bottom: 8px;
}
.lesson-step-prompt-editor__hint {
  margin: 6px 0 0;
  color: #7b8496;
  font-size: 12px;
  line-height: 1.5;
}
</style>
