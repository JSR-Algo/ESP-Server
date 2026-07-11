<template>
  <section class="interaction-panel">
    <div class="panel-title">Teach & interact</div>
    <el-form label-position="top" size="small">
      <div class="form-grid">
        <el-form-item label="Duration">
          <el-radio-group :value="model.durationPreset" :disabled="disabled" @input="set('durationPreset', $event)">
            <el-radio-button v-for="minute in [3, 5, 8]" :key="minute" :label="minute">{{ minute }} min</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="English teaching word">
          <el-input :value="model.teachingWord.text" :disabled="disabled" maxlength="12" show-word-limit @input="setNested('teachingWord', 'text', $event.toUpperCase())" />
        </el-form-item>
      </div>
      <div class="form-grid">
        <el-form-item label="Interaction template">
          <el-select :value="model.interaction.template" disabled style="width:100%"><el-option label="Safe speaking" value="safeSpeaking" /></el-select>
        </el-form-item>
        <el-form-item label="Fun pattern">
          <el-select :value="model.interaction.funPattern" :disabled="disabled" style="width:100%" @input="setNested('interaction', 'funPattern', $event)">
            <el-option v-for="pattern in funPatterns" :key="pattern" :label="humanize(pattern)" :value="pattern" />
          </el-select>
        </el-form-item>
      </div>
      <el-divider content-position="left">Story beat</el-divider>
      <el-form-item label="Goal"><el-input :value="model.storyBeat.goal" :disabled="disabled" @input="setNested('storyBeat', 'goal', $event)" /></el-form-item>
      <div class="form-grid">
        <el-form-item label="Success reaction"><el-input :value="model.storyBeat.successReaction" :disabled="disabled" @input="setNested('storyBeat', 'successReaction', $event)" /></el-form-item>
        <el-form-item label="Next tease"><el-input :value="model.storyBeat.nextTease" :disabled="disabled" @input="setNested('storyBeat', 'nextTease', $event)" /></el-form-item>
      </div>
      <el-divider content-position="left">Named robot motions</el-divider>
      <div class="motion-grid">
        <el-form-item v-for="slot in motionSlots" :key="slot" :label="humanize(slot)">
          <el-select :value="model.motion[slot]" :disabled="disabled" style="width:100%" @input="setNested('motion', slot, $event)">
            <el-option v-for="motion in motions" :key="motion" :label="humanize(motion)" :value="motion" />
          </el-select>
        </el-form-item>
      </div>
    </el-form>
  </section>
</template>

<script>
import { mergeAuthoringFields } from './lesson-builder-logic';

export default {
  name: 'LessonInteractionPanel',
  props: { value: { type: Object, required: true }, disabled: Boolean },
  data: () => ({
    funPatterns: ['mysteryReveal', 'copyMyMove', 'sillyChoice', 'whisperThenLoud', 'soundGuess', 'missingObject', 'robotForgot', 'miniStoryRescue', 'fastSlow', 'celebrationFinale'],
    motions: ['rest', 'teach', 'presentLeft', 'presentRight', 'listen', 'thinking', 'encourage', 'tryAgain', 'celebrate', 'goodbye'],
    motionSlots: ['present', 'listen', 'correct', 'nearMiss', 'incorrect'],
  }),
  computed: { model() { return mergeAuthoringFields({}, this.value); } },
  methods: {
    humanize(value) { return String(value).replace(/([A-Z])/g, ' $1').replace(/^./, (c) => c.toUpperCase()); },
    set(key, value) { this.$emit('input', mergeAuthoringFields(this.model, { [key]: value })); },
    setNested(group, key, value) { this.$emit('input', mergeAuthoringFields(this.model, { [group]: { [key]: value } })); },
  },
};
</script>

<style scoped>
.interaction-panel { background: #fffdf6; border: 1px solid #eadfca; border-radius: 18px; padding: 18px; }
.panel-title { color: #15312d; font-family: Georgia, serif; font-size: 22px; font-weight: 700; margin-bottom: 14px; }
.form-grid { display: grid; gap: 14px; grid-template-columns: 1fr 1fr; }
.motion-grid { display: grid; gap: 10px; grid-template-columns: repeat(3, 1fr); }
@media (max-width: 900px) { .form-grid, .motion-grid { grid-template-columns: 1fr; } }
</style>
