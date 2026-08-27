<template>
  <section class="course-mode-timeline" data-testid="course-mode-activity-timeline">
    <header class="course-mode-timeline__header">
      <div>
        <span class="eyebrow">COURSE MODE · ACTIVITY AUTHORITY</span>
        <h3>Child interaction timeline</h3>
        <p>Edit the authored activities. Projected steps below remain read-only audit output.</p>
      </div>
      <el-button type="primary" size="small" :loading="saving" :disabled="disabled || saving || !dirty || report.overDuration || report.leakageActivityIds.length > 0" @click="$emit('save')">
        Save Course Mode
      </el-button>
    </header>

    <div class="course-mode-meter" data-testid="course-mode-duration-meter" :class="{ 'course-mode-meter--warning': report.overDuration }">
      <div class="course-mode-meter__copy">
        <strong>{{ formatDuration(report.totalSeconds) }} / 8:00</strong>
        <span>{{ report.overDuration ? 'Reduce activity time before saving.' : 'Deterministic authored duration' }}</span>
      </div>
      <el-progress :percentage="durationPercent" :status="report.overDuration ? 'exception' : undefined" :show-text="false" />
    </div>
    <el-alert v-if="report.leakageActivityIds.length" data-testid="course-mode-answer-leakage-warning" type="warning" :closable="false" show-icon>
      <template slot="title">Protected recall reveals an answer in: {{ report.leakageActivityIds.join(', ') }}</template>
    </el-alert>
    <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" />
    <p v-if="savedMessage" class="course-mode-timeline__saved" role="status">{{ savedMessage }}</p>

    <el-card v-for="target in draft.targets" :key="target.targetId" class="target-card" shadow="never">
      <div slot="header" class="target-card__header">
        <strong>{{ target.targetWord }}</strong>
        <span class="mono">{{ target.targetId }}</span>
      </div>
      <div class="target-grid">
        <el-form-item label="Word"><el-input v-model="target.targetWord" :disabled="disabled" @input="emitDraft" /></el-form-item>
        <el-form-item label="Role">
          <el-select v-model="target.role" :disabled="disabled" @change="emitDraft">
            <el-option v-for="role in targetRoles" :key="role" :label="role" :value="role" />
          </el-select>
        </el-form-item>
        <el-form-item label="Vietnamese meaning">
          <el-input :value="(target.vietnameseMeanings || []).join(', ')" :disabled="disabled" @input="setMeanings(target, $event)" />
        </el-form-item>
      </div>
    </el-card>

    <div class="activity-list">
      <el-card v-for="(activity, index) in draft.activities" :key="activity.activityId" class="activity-card" shadow="never">
        <div slot="header" class="activity-card__header">
          <span class="activity-card__number">{{ index + 1 }}</span>
          <div><strong>{{ activity.stage }}</strong><span class="mono">{{ activity.activityId }}</span></div>
          <span class="activity-card__duration">{{ activity.expectedDurationSec }}s</span>
        </div>
        <el-form label-position="top" size="small">
          <div class="activity-grid activity-grid--3">
            <el-form-item label="Stage">
              <el-select v-model="activity.stage" :disabled="disabled" @change="emitDraft">
                <el-option v-for="stage in stages" :key="stage" :label="stage" :value="stage" />
              </el-select>
            </el-form-item>
            <el-form-item label="Activity type"><el-input v-model="activity.activityType" :disabled="disabled" @input="emitDraft" /></el-form-item>
            <el-form-item label="Duration (seconds)"><el-input-number v-model="activity.expectedDurationSec" :min="1" :max="480" :disabled="disabled" @change="emitDraft" /></el-form-item>
          </div>
          <el-form-item label="Targets">
            <el-checkbox-group v-model="activity.targetIds" :disabled="disabled" @change="emitDraft">
              <el-checkbox v-for="target in draft.targets" :key="target.targetId" :label="target.targetId">{{ target.targetWord }}</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <div class="activity-grid activity-grid--3">
            <el-form-item label="Embodied intent">
              <el-select v-model="activity.embodiedIntent" :disabled="disabled" filterable @change="emitDraft">
                <el-option v-for="intent in embodiedIntents" :key="intent" :label="intent" :value="intent" />
              </el-select>
            </el-form-item>
            <el-form-item label="Visual focus">
              <el-select v-model="activity.visualFocusRegion" :disabled="disabled" @change="emitDraft">
                <el-option v-for="region in visualRegions" :key="region" :label="region" :value="region" />
              </el-select>
            </el-form-item>
            <el-form-item label="Context"><el-input v-model="activity.contextId" :disabled="disabled" @input="emitDraft" /></el-form-item>
          </div>
          <el-form-item label="Input modalities">
            <el-checkbox-group v-model="activity.modalities" :disabled="disabled" @change="emitDraft">
              <el-checkbox v-for="modality in modalities" :key="modality" :label="modality">{{ modality }}</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <div class="visual-policy">
            <strong>Visual policy</strong>
            <div class="activity-grid activity-grid--2">
              <el-form-item label="Strategy"><el-input v-model="activity.visual.strategy" :disabled="disabled" @input="emitDraft" /></el-form-item>
              <el-form-item label="Fallback"><el-input v-model="activity.visual.fallback" :disabled="disabled" @input="emitDraft" /></el-form-item>
              <el-form-item label="Published background key">
                <el-select :value="activity.visual.backgroundAssetKey" clearable filterable :disabled="disabled" @change="setVisualKey(activity, 'backgroundAssetKey', $event)">
                  <el-option v-for="key in backgroundKeys" :key="key" :label="key" :value="key" />
                </el-select>
              </el-form-item>
              <el-form-item label="Published object key">
                <el-select :value="activity.visual.objectAssetKey" clearable filterable :disabled="disabled" @change="setVisualKey(activity, 'objectAssetKey', $event)">
                  <el-option v-for="key in objectKeys" :key="key" :label="key" :value="key" />
                </el-select>
              </el-form-item>
            </div>
          </div>
          <div class="answer-policy">
            <strong>Assessment answer policy</strong>
            <el-checkbox v-for="field in answerLeakFields" :key="field" v-model="activity.answerPolicy[field]" :disabled="disabled" @change="emitDraft">{{ field }}</el-checkbox>
            <div class="activity-grid activity-grid--2 answer-policy__timing">
              <el-form-item label="Milliseconds since full model">
                <el-input-number v-model="activity.answerPolicy.minElapsedSinceFullModelMs" :min="0" :step="1000" :disabled="disabled" @change="emitDraft" />
              </el-form-item>
              <el-form-item label="Intervening activities">
                <el-input-number v-model="activity.answerPolicy.minInterveningActivityCount" :min="0" :disabled="disabled" @change="emitDraft" />
              </el-form-item>
            </div>
          </div>
          <div class="outcome-grid">
            <div v-for="(outcome, outcomeName) in activity.outcomes" :key="outcomeName" class="outcome-row">
              <strong>{{ outcomeName }}</strong>
              <el-select v-model="outcome.action" :disabled="disabled" @change="onOutcomeAction(outcome)">
                <el-option v-for="action in outcomeActions" :key="action" :label="action" :value="action" />
              </el-select>
              <el-select v-if="outcome.action === 'retry' || outcome.action === 'support'" v-model="outcome.activityId" :disabled="disabled" @change="emitDraft">
                <el-option v-for="candidate in draft.activities" :key="candidate.activityId" :label="candidate.activityId" :value="candidate.activityId" />
              </el-select>
            </div>
          </div>
        </el-form>
      </el-card>
    </div>
  </section>
</template>

<script>
import { courseModeActivityReport, normalizeCourseModeVisualKeys } from './lesson-builder-logic';

const clone = (value) => JSON.parse(JSON.stringify(value || {}));

export default {
  name: 'CourseModeActivityTimeline',
  props: {
    value: { type: Object, required: true },
    assets: { type: Array, default: () => [] },
    disabled: { type: Boolean, default: false },
    saving: { type: Boolean, default: false },
    dirty: { type: Boolean, default: false },
    error: { type: String, default: '' },
    savedMessage: { type: String, default: '' },
  },
  data() {
    return {
      draft: clone(this.value),
      stages: ['OPENING', 'DISCOVER', 'UNDERSTAND', 'GUIDED_ACTION', 'SUPPORTED_SPEECH', 'RECALL', 'TRANSFER', 'DELAYED_RECALL', 'CLOSING'],
      modalities: ['speech_en', 'speech_vi', 'choice', 'gesture', 'silence', 'help'],
      targetRoles: ['primary', 'optional_secondary', 'exposure', 'review'],
      outcomeActions: ['advance', 'retry', 'support', 'pause', 'close', 'complete'],
      answerLeakFields: ['targetTextVisible', 'targetAudioBeforeAssessment', 'spokenTargetInPrompt', 'multipleChoiceContainsTarget'],
    };
  },
  computed: {
    report() { return courseModeActivityReport(this.draft); },
    durationPercent() { return Math.min(100, Math.round((this.report.totalSeconds / 480) * 100)); },
    embodiedIntents() { return Array.isArray(this.draft.embodiedIntentNames) ? this.draft.embodiedIntentNames : []; },
    visualRegions() { return this.draft.visualFocus && Array.isArray(this.draft.visualFocus.regions) ? this.draft.visualFocus.regions : []; },
    backgroundKeys() { return this.assetKeysFor(['scene', 'backgroundScene']); },
    objectKeys() { return this.assetKeysFor(['teachingObject', 'object']); },
  },
  watch: {
    value: {
      deep: true,
      handler(value) { this.draft = clone(value); },
    },
  },
  methods: {
    assetKeysFor(categories) {
      return [...new Set(this.assets
        .filter((asset) => categories.includes(asset.category) || categories.includes(asset.layer))
        .map((asset) => asset.assetKey)
        .filter(Boolean))].sort();
    },
    formatDuration(seconds) {
      const value = Math.max(0, Number(seconds) || 0);
      return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
    },
    setMeanings(target, value) {
      if (this.disabled || this.saving) return false;
      this.$set(target, 'vietnameseMeanings', String(value).split(',').map((item) => item.trim()).filter(Boolean));
      this.emitDraft();
      return true;
    },
    setVisualKey(activity, field, value) {
      if (this.disabled || this.saving) return false;
      this.$set(activity.visual, field, typeof value === 'string' && value.trim() ? value.trim() : null);
      this.emitDraft();
      return true;
    },
    onOutcomeAction(outcome) {
      if (this.disabled || this.saving) return false;
      if (!['retry', 'support'].includes(outcome.action)) this.$delete(outcome, 'activityId');
      else if (!outcome.activityId && this.draft.activities.length) this.$set(outcome, 'activityId', this.draft.activities[0].activityId);
      this.emitDraft();
      return true;
    },
    emitDraft() {
      if (this.disabled || this.saving) return false;
      const next = normalizeCourseModeVisualKeys(this.draft);
      next.targets = (next.targets || []).map((target) => ({
        ...target,
        activityIds: (next.activities || []).filter((activity) => (activity.targetIds || []).includes(target.targetId)).map((activity) => activity.activityId),
      }));
      this.$emit('input', next);
      return true;
    },
  },
};
</script>

<style scoped>
.course-mode-timeline { background:#f4f8f5; border:1px solid #d9e6df; border-radius:18px; margin-bottom:20px; padding:18px; }
.course-mode-timeline__header { align-items:flex-start; display:flex; gap:18px; justify-content:space-between; }
.course-mode-timeline__header h3 { color:#17312d; margin:4px 0; }.course-mode-timeline__header p { color:#66736f; margin:0; }
.course-mode-meter { background:#fff; border-radius:14px; margin:16px 0; padding:12px 14px; }.course-mode-meter--warning { border:1px solid #e6a23c; }
.course-mode-meter__copy { display:flex; justify-content:space-between; margin-bottom:8px; }.course-mode-meter__copy span { color:#78827f; font-size:12px; }
.course-mode-timeline__saved { color:#2c7a55; font-weight:600; }.target-card,.activity-card { border-color:#dce8e1; margin-top:12px; }
.target-card__header,.activity-card__header { align-items:center; display:flex; gap:10px; }.target-card__header .mono,.activity-card__header .mono { color:#7b8783; display:block; font-size:11px; }
.activity-card__number { align-items:center; background:#17312d; border-radius:50%; color:#fff; display:inline-flex; height:28px; justify-content:center; width:28px; }.activity-card__duration { margin-left:auto; }
.target-grid,.activity-grid { display:grid; gap:12px; }.target-grid,.activity-grid--3 { grid-template-columns:repeat(3,minmax(0,1fr)); }.activity-grid--2 { grid-template-columns:repeat(2,minmax(0,1fr)); }
.visual-policy,.answer-policy { background:#f8faf9; border-radius:12px; margin-top:8px; padding:12px; }.answer-policy .el-checkbox { margin-top:10px; }
.answer-policy__timing { margin-top:10px; }
.outcome-grid { display:grid; gap:8px; margin-top:12px; }.outcome-row { align-items:center; display:grid; gap:10px; grid-template-columns:minmax(90px,.7fr) minmax(120px,1fr) minmax(160px,1.4fr); }
.el-select,.el-input-number { width:100%; }
@media (max-width:760px) { .course-mode-timeline { padding:12px; }.course-mode-timeline__header,.course-mode-meter__copy { align-items:stretch; flex-direction:column; }.target-grid,.activity-grid--2,.activity-grid--3 { grid-template-columns:1fr; }.outcome-row { grid-template-columns:1fr; }.answer-policy .el-checkbox { display:block; margin-left:0; }.activity-card__header { align-items:flex-start; } }
</style>
