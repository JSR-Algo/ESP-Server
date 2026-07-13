<template>
  <section class="simulation-panel">
    <div class="simulation-head">
      <div>
        <span class="eyebrow">Server branch simulation</span>
        <strong>Run deterministic speaking outcomes</strong>
      </div>
      <el-button type="primary" size="small" :loading="running" :disabled="disabled || !manifestSteps.length" @click="runSimulation">
        Simulate
      </el-button>
    </div>

    <el-radio-group v-model="activePreset" size="mini" class="preset-grid">
      <el-radio-button v-for="preset in presets" :key="preset.value" :label="preset.value">
        {{ preset.label }}
      </el-radio-button>
    </el-radio-group>

    <el-alert v-if="errorMessage" :title="errorMessage" type="error" :closable="false" show-icon />

    <div v-if="value" class="simulation-result">
      <div class="result-summary">
        <div><span>Terminated</span><strong>{{ value.simulation.terminated }}</strong></div>
        <div><span>Reason</span><strong>{{ value.simulation.terminationReason }}</strong></div>
        <div><span>Checksum</span><strong class="mono">{{ value.checksum }}</strong></div>
        <div><span>ETag</span><strong class="mono">{{ value.etag }}</strong></div>
      </div>
      <ol class="trace-list">
        <li v-for="(event, traceIndex) in value.simulation.trace" :key="traceIndex">
          <span class="trace-order">{{ traceIndex + 1 }}</span>
          <strong>{{ event.stepKey }}</strong>
          <span v-if="event.outcome">{{ event.outcome }}</span>
          <span v-if="event.attempt">attempt {{ event.attempt }}</span>
          <el-tag size="mini" effect="plain">{{ event.action }}</el-tag>
        </li>
      </ol>
    </div>
  </section>
</template>

<script>
import Api from '@/apis/api';
import { validSimulationEvidence as validateSimulationEvidence } from './lesson-builder-logic';

const BRANCH_ACTIONS = Object.freeze({
  correct: 'advance',
  near_miss: 'advance',
  brave_try: 'advance',
  incorrect: 'retry',
  retry: 'retry',
  timeout: 'fallback',
});

export default {
  name: 'LessonSimulationPanel',
  props: {
    lessonId: { type: [String, Number], required: true },
    manifestPreview: { type: Object, required: true },
    steps: { type: Array, default: () => [] },
    proofVersion: { type: Number, required: true },
    value: { type: Object, default: null },
    disabled: { type: Boolean, default: false },
  },
  data() {
    return {
      activePreset: 'correct',
      running: false,
      requestId: 0,
      errorMessage: '',
      presets: [
        { value: 'correct', label: 'Correct' },
        { value: 'near-miss', label: 'Near miss' },
        { value: 'brave-try', label: 'Brave try' },
        { value: 'incorrect-to-fallback', label: 'Incorrect to fallback' },
        { value: 'retry-then-correct', label: 'Retry then correct' },
        { value: 'timeout', label: 'Timeout' },
        { value: 'completion', label: 'Completion' },
      ],
    };
  },
  computed: {
    manifestSteps() {
      const manifest = this.manifestPreview && this.manifestPreview.manifest;
      return manifest && Array.isArray(manifest.steps) ? manifest.steps : [];
    },
  },
  watch: {
    proofVersion() {
      this.requestId += 1;
      this.running = false;
      this.errorMessage = '';
    },
    manifestPreview() {
      this.requestId += 1;
      this.running = false;
      this.errorMessage = '';
    },
  },
  beforeDestroy() {
    this.requestId += 1;
    this.running = false;
  },
  methods: {
    previewIdentity(value) {
      const preview = value && value.preview;
      if (!value || typeof value.checksum !== 'string' || !value.checksum
        || typeof value.etag !== 'string' || !value.etag
        || !preview || typeof preview.profile !== 'string'
        || !Number.isFinite(Number(preview.width)) || !Number.isFinite(Number(preview.height))) return null;
      return {
        checksum: value.checksum,
        etag: value.etag,
        profile: preview.profile,
        width: Number(preview.width),
        height: Number(preview.height),
      };
    },
    samePreviewIdentity(left, right) {
      return Boolean(left && right
        && left.checksum === right.checksum && left.etag === right.etag
        && left.profile === right.profile && left.width === right.width && left.height === right.height);
    },
    validSimulationEvidence(result, expectedPreview) {
      return validateSimulationEvidence(result, expectedPreview);
    },
    maxAttemptsFor(stepKey) {
      const source = this.steps.find((step) => step.stepKey === stepKey) || {};
      const interaction = source.stepBody && source.stepBody.interaction;
      const requested = Number(interaction && interaction.maxAttempts);
      return Number.isInteger(requested) && requested > 0 ? requested : 3;
    },
    outcomesFor(preset, maxAttempts) {
      if (preset === 'near-miss') return ['near_miss'];
      if (preset === 'brave-try') return ['brave_try'];
      if (preset === 'incorrect-to-fallback') return Array(maxAttempts).fill('incorrect');
      if (preset === 'retry-then-correct') return ['retry', 'correct'];
      if (preset === 'timeout') return ['timeout'];
      if (preset === 'completion') return [];
      return ['correct'];
    },
    buildSimulationPayload() {
      const projectionSteps = {};
      const outcomes = {};
      this.manifestSteps
        .filter((step) => step.completionClass === 'interactive')
        .forEach((step) => {
          const maxAttempts = this.maxAttemptsFor(step.id);
          projectionSteps[step.id] = {
            maxAttempts,
            on: { ...BRANCH_ACTIONS },
            fallback: 'advance',
          };
          outcomes[step.id] = this.outcomesFor(this.activePreset, maxAttempts);
        });
      return { projection: { steps: projectionSteps }, outcomes, maxTransitions: 100 };
    },
    runSimulation() {
      if (this.disabled || this.running) return;
      const requestId = this.requestId + 1;
      const proofVersion = this.proofVersion;
      const previewIdentity = this.previewIdentity(this.manifestPreview);
      if (!previewIdentity) {
        this.errorMessage = 'Generate a valid authoritative preview before simulation.';
        return;
      }
      this.requestId = requestId;
      this.running = true;
      this.errorMessage = '';
      this.$emit('evidence', null, proofVersion);
      Api.lesson.simulate(
        this.lessonId,
        this.buildSimulationPayload(),
        (result) => {
          if (requestId !== this.requestId || proofVersion !== this.proofVersion) return;
          this.running = false;
          if (!this.validSimulationEvidence(result, this.manifestPreview)) {
            this.errorMessage = 'Simulation returned an invalid response.';
            return;
          }
          const responseIdentity = this.previewIdentity(result);
          const currentIdentity = this.previewIdentity(this.manifestPreview);
          if (!this.samePreviewIdentity(previewIdentity, responseIdentity)
            || !this.samePreviewIdentity(previewIdentity, currentIdentity)) {
            this.errorMessage = 'Simulation response does not match the current manifest preview.';
            return;
          }
          this.$emit('evidence', result, proofVersion);
        },
        (message) => {
          if (requestId !== this.requestId || proofVersion !== this.proofVersion) return;
          this.running = false;
          this.errorMessage = message || 'Simulation failed.';
        },
      );
    },
  },
};
</script>

<style scoped>
.simulation-panel { background:#fff; border:1px solid #d9e3df; border-radius:18px; display:grid; gap:14px; padding:16px; }
.simulation-head { align-items:center; display:flex; gap:12px; justify-content:space-between; }
.simulation-head strong { color:#17312d; display:block; margin-top:3px; }
.eyebrow { color:#9a6820; font-size:10px; font-weight:800; letter-spacing:.12em; text-transform:uppercase; }
.preset-grid { display:flex; flex-wrap:wrap; }
.result-summary { display:grid; gap:8px; grid-template-columns:repeat(4,minmax(0,1fr)); }
.result-summary div { background:#f5f8f7; border-radius:10px; display:grid; gap:4px; min-width:0; padding:8px; }
.result-summary span { color:#788b86; font-size:10px; text-transform:uppercase; }
.result-summary strong { overflow-wrap:anywhere; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:11px; }
.trace-list { display:grid; gap:7px; list-style:none; margin:14px 0 0; padding:0; }
.trace-list li { align-items:center; background:#f8f4e8; border-radius:10px; display:flex; flex-wrap:wrap; gap:8px; padding:8px 10px; }
.trace-order { align-items:center; background:#17312d; border-radius:50%; color:#fff; display:inline-flex; font-size:10px; height:22px; justify-content:center; width:22px; }
@media (max-width:760px) { .simulation-head { align-items:stretch; flex-direction:column; }.result-summary { grid-template-columns:1fr 1fr; } }
</style>
