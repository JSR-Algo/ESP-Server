<template>
  <section class="interaction-panel">
    <div class="panel-title">Teach & interact</div>
    <el-form label-position="top" size="small">
      <div class="form-grid">
        <el-form-item label="Lesson duration preset">
          <el-radio-group :value="model.durationPreset" :disabled="disabled" @input="set('durationPreset', $event)">
            <el-radio-button v-for="minute in [3, 5, 8]" :key="minute" :label="minute">{{ minute }} min</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="English teaching word">
          <!-- Grapheme-counted, not maxlength: HTML maxlength counts UTF-16
               units, so a Vietnamese word with combining diacritics gets cut at
               a third of the 12 visible characters the backend actually allows. -->
          <el-input
            :value="model.teachingWord.text"
            :disabled="disabled"
            data-testid="teaching-word-input"
            @input="setTeachingWord($event)"
          />
          <span class="word-count" :class="{ 'is-over': teachingWordOver }">
            {{ teachingWordLength }}/{{ teachingWordMax }}
          </span>
        </el-form-item>
      </div>
      <el-divider content-position="left">Timing & ending</el-divider>
      <div class="form-grid">
        <el-form-item label="Step duration (seconds)">
          <el-input-number :value="stepDuration" :min="1" :disabled="disabled" data-testid="step-duration-input" @change="setDuration" />
        </el-form-item>
        <el-form-item label="End lesson after this step">
          <el-switch :value="model.terminal === true" :disabled="disabled" data-testid="step-terminal-input" @change="set('terminal', $event)" />
        </el-form-item>
      </div>
      <p class="flow-help">An ending stops the lesson here. Otherwise it follows the saved branches or continues to the next step.</p>
      <div v-for="(branch, name) in branchRows" :key="name" class="form-grid">
        <el-form-item :label="humanize(name) + ' branch: end lesson'">
          <el-switch :value="branch.terminal === true || branch.end === true" :disabled="disabled || model.terminal === true" @change="setBranchEnd(name, $event)" />
        </el-form-item>
        <el-form-item :label="humanize(name) + ' branch: next step key'">
          <el-input :value="branch.nextStepKey || ''" :disabled="disabled || model.terminal === true || branch.terminal === true || branch.end === true" @input="setBranchTarget(name, $event)" />
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
import {
  clampTeachingWord,
  mergeAuthoringFields,
  mergePersistedAuthoringFields,
  TEACHING_WORD_MAX_VISIBLE_CHARS,
  visibleGraphemeCount,
} from './lesson-builder-logic';

export default {
  name: 'LessonInteractionPanel',
  props: { value: { type: Object, required: true }, disabled: Boolean },
  data: () => ({
    funPatterns: ['mysteryReveal', 'copyMyMove', 'sillyChoice', 'whisperThenLoud', 'soundGuess', 'missingObject', 'robotForgot', 'miniStoryRescue', 'fastSlow', 'celebrationFinale'],
    motions: ['rest', 'teach', 'presentLeft', 'presentRight', 'listen', 'thinking', 'encourage', 'tryAgain', 'celebrate', 'goodbye'],
    motionSlots: ['present', 'listen', 'correct', 'nearMiss', 'incorrect'],
  }),
  computed: {
    model() { return mergeAuthoringFields({}, this.value); },
    teachingWordMax() { return TEACHING_WORD_MAX_VISIBLE_CHARS; },
    teachingWordLength() { return visibleGraphemeCount(this.model.teachingWord.text); },
    teachingWordOver() { return this.teachingWordLength > this.teachingWordMax; },
    stepDuration() {
      return [this.model.durationSec, this.model.timeoutSec].find((value) => typeof value === 'number' && Number.isFinite(value) && value > 0) || 12;
    },
    branchRows() {
      const branches = this.model.branches;
      return branches && typeof branches === 'object' && !Array.isArray(branches)
        ? Object.fromEntries(Object.entries(branches).filter(([, branch]) => branch && typeof branch === 'object' && !Array.isArray(branch))) : {};
    },
  },
  methods: {
    humanize(value) { return String(value).replace(/([A-Z])/g, ' $1').replace(/^./, (c) => c.toUpperCase()); },
    setDuration(value) {
      if (typeof value === 'number' && Number.isFinite(value) && value > 0) this.set('durationSec', value);
    },
    setBranchEnd(name, value) {
      this.set('branches', { ...this.model.branches, [name]: { ...this.model.branches[name], terminal: value, end: value } });
    },
    setBranchTarget(name, value) {
      this.set('branches', { ...this.model.branches, [name]: { ...this.model.branches[name], nextStepKey: value.trim() } });
    },
    setTeachingWord(value) {
      this.setNested('teachingWord', 'text', clampTeachingWord(String(value).toUpperCase()));
    },
    set(key, value) { this.$emit('input', mergePersistedAuthoringFields(this.value, { [key]: value })); },
    setNested(group, key, value) { this.$emit('input', mergePersistedAuthoringFields(this.value, { [group]: { [key]: value } })); },
  },
};
</script>

<style scoped>
.interaction-panel { background: #fffdf6; border: 1px solid #eadfca; border-radius: 18px; padding: 18px; }
.panel-title { color: #15312d; font-family: Georgia, serif; font-size: 22px; font-weight: 700; margin-bottom: 14px; }
.form-grid { display: grid; gap: 14px; grid-template-columns: 1fr 1fr; }
.word-count { color: #909399; display: block; font-size: 12px; text-align: right; }
.word-count.is-over { color: #f56c6c; }
.flow-help { color: #606266; font-size: 12px; }
.motion-grid { display: grid; gap: 10px; grid-template-columns: repeat(3, 1fr); }
@media (max-width: 900px) { .form-grid, .motion-grid { grid-template-columns: 1fr; } }
</style>
