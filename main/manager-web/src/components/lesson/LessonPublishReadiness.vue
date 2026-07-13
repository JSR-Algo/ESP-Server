<template>
  <section class="readiness">
    <div class="readiness__title">
      <strong>{{ $t('lesson.publishReadiness') }}</strong>
      <el-tag :type="ready ? 'success' : 'warning'" size="mini">{{ ready ? 'READY' : 'CHECK' }}</el-tag>
    </div>

    <div class="readiness__grid">
      <div v-for="row in budgetRows" :key="row.key" :class="{ 'is-failing': !row.pass }">
        <span>{{ row.label }}</span><strong>{{ row.value }}</strong>
      </div>
    </div>

    <div class="validation-result" data-testid="validation-result">
      <div class="validation-result__head">
        <strong>{{ $t('lesson.serverValidation') }}</strong>
        <el-tag v-if="validationResult" :type="validationReady ? 'success' : 'danger'" size="mini">
          {{ validationReady ? 'PASS' : 'FAIL' }}
        </el-tag>
        <el-tag v-else type="info" size="mini">{{ $t('lesson.validationMissing') }}</el-tag>
        <el-tag v-if="validationResult && !validationCurrent" type="warning" size="mini">{{ $t('lesson.proofStale') }}</el-tag>
      </div>
      <template v-if="validationResult">
        <div class="validation-profiles">
          <span>{{ $t('lesson.validationProfiles') }}</span>
          <el-tag v-for="profile in validationProfiles" :key="profile" size="mini" effect="plain">{{ profile }}</el-tag>
          <em v-if="!validationProfiles.length">—</em>
        </div>
        <ul v-if="validationErrors.length" class="validation-list validation-list--errors">
          <li v-for="(error, index) in validationErrors" :key="`error-${index}`">{{ formatFinding(error) }}</li>
        </ul>
        <ul v-if="validationWarnings.length" class="validation-list validation-list--warnings">
          <li v-for="(warning, index) in validationWarnings" :key="`warning-${index}`">{{ formatFinding(warning) }}</li>
        </ul>
        <p v-if="!validationErrors.length && !validationWarnings.length" class="validation-empty">{{ $t('lesson.validationNoFindings') }}</p>
      </template>
      <p v-else class="validation-empty">{{ $t('lesson.validationRunHint') }}</p>
    </div>
  </section>
</template>

<script>
import { calculateReadiness } from './lesson-builder-logic';

export default {
  name: 'LessonPublishReadiness',
  props: {
    steps: { type: Array, default: () => [] },
    assets: { type: Array, default: () => [] },
    manifest: { type: Object, default: () => ({}) },
    validationResult: { type: Object, default: null },
    validationCurrent: { type: Boolean, default: false },
  },
  computed: {
    metrics() {
      return calculateReadiness({ steps: this.steps, assets: this.assets, manifest: this.manifest });
    },
    validationProfiles() {
      const profiles = this.validationResult && this.validationResult.profiles;
      return Array.isArray(profiles) ? profiles.map((profile) => (
        typeof profile === 'string' ? profile : (profile.profile || profile.name || JSON.stringify(profile))
      )) : [];
    },
    validationErrors() {
      return this.findings('errors');
    },
    validationWarnings() {
      return this.findings('warnings');
    },
    validationReady() {
      return Boolean(this.validationResult && this.validationResult.valid === true && this.validationCurrent && !this.validationErrors.length);
    },
    budgetRows() {
      return [
        { key: 'download', label: this.$t('lesson.budgetDownload'), value: this.formatBytes(this.metrics.downloadBytes), pass: Number.isFinite(Number(this.metrics.downloadBytes)) },
        { key: 'assets', label: this.$t('lesson.budgetAssets'), value: `${this.metrics.uniqueAssetCount} / ${this.metrics.sharedReferenceCount}`, pass: true },
        { key: 'psram', label: this.$t('lesson.budgetPsram'), value: this.formatBytes(this.metrics.estimatedPeakPsram), pass: this.metrics.estimatedPeakPsram <= 1572864 },
        { key: 'offline', label: this.$t('lesson.budgetOffline'), value: this.metrics.offlineReady ? 'PASS' : 'FAIL', pass: this.metrics.offlineReady },
        { key: 'paths', label: this.$t('lesson.budgetPaths'), value: this.metrics.allPathsTerminate ? 'PASS' : 'FAIL', pass: this.metrics.allPathsTerminate },
      ];
    },
    budgetsReady() {
      return this.budgetRows.every((row) => row.pass);
    },
    ready() {
      return this.validationReady && this.budgetsReady;
    },
  },
  watch: {
    ready: { immediate: true, handler(value) { this.$emit('ready-change', value); } },
  },
  methods: {
    findings(key) {
      const direct = this.validationResult && this.validationResult[key];
      if (Array.isArray(direct)) return direct;
      const profiles = this.validationResult && this.validationResult.profiles;
      if (!Array.isArray(profiles)) return [];
      return profiles.reduce((all, profile) => all.concat(Array.isArray(profile && profile[key]) ? profile[key] : []), []);
    },
    formatFinding(finding) {
      if (typeof finding === 'string') return finding;
      if (!finding || typeof finding !== 'object') return String(finding || '');
      return finding.message || finding.reason || finding.code || JSON.stringify(finding);
    },
    formatBytes(bytes) {
      const n = Number(bytes || 0);
      return n < 1024 ? `${n} B` : `${(n / 1048576).toFixed(n >= 1048576 ? 2 : 3)} MiB`;
    },
  },
};
</script>

<style scoped>
.readiness { background:#17312d; border-radius:18px; color:#fff8df; padding:16px; }
.readiness__title,.validation-result__head,.validation-profiles { align-items:center; display:flex; flex-wrap:wrap; gap:8px; justify-content:space-between; }
.readiness__grid { display:grid; gap:10px; grid-template-columns:repeat(5,1fr); margin-top:14px; }
.readiness__grid>div { background:rgba(255,255,255,.08); border:1px solid transparent; border-radius:11px; display:grid; gap:5px; padding:10px; }
.readiness__grid>div.is-failing { border-color:#f3a89d; }
.readiness__grid span,.validation-profiles span { color:#b9cbc5; font-size:11px; text-transform:uppercase; }
.readiness__grid strong { font-size:13px; }
.validation-result { background:rgba(255,255,255,.07); border-radius:12px; margin-top:12px; padding:12px; }
.validation-result__head { justify-content:flex-start; }
.validation-profiles { justify-content:flex-start; margin-top:10px; }
.validation-list { margin:10px 0 0; padding-left:18px; }
.validation-list--errors { color:#ffd0c8; }.validation-list--warnings { color:#ffe0a3; }
.validation-empty { color:#b9cbc5; margin:10px 0 0; }
@media(max-width:900px){.readiness__grid{grid-template-columns:1fr 1fr}}
</style>
