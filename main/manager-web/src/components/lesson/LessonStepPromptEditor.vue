<template>
  <el-form-item :label="$t('lesson.promptEditorLabel')" class="lesson-step-prompt-editor">
    <el-input
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
  </el-form-item>
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
.lesson-step-prompt-editor__hint {
  margin: 6px 0 0;
  color: #7b8496;
  font-size: 12px;
  line-height: 1.5;
}
</style>
