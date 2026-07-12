<template>
  <section class="readiness">
    <div class="readiness__title"><strong>Robot readiness</strong><el-tag :type="ready ? 'success' : 'warning'" size="mini">{{ ready ? 'READY' : 'CHECK' }}</el-tag></div>
    <div class="readiness__grid">
      <div><span>Download</span><strong>{{ formatBytes(metrics.downloadBytes) }}</strong></div>
      <div><span>Assets</span><strong>{{ metrics.uniqueAssetCount }} unique / {{ metrics.sharedReferenceCount }} shared</strong></div>
      <div><span>Peak PSRAM</span><strong>{{ formatBytes(metrics.estimatedPeakPsram) }}{{ metrics.estimateOnly ? ' estimated' : '' }}</strong></div>
      <div><span>Offline</span><strong>{{ metrics.offlineReady ? 'Ready' : 'Remote dependency' }}</strong></div>
      <div><span>All paths</span><strong>{{ metrics.allPathsTerminate ? 'Terminate' : 'Review branches' }}</strong></div>
    </div>
    <div v-if="issues.length" class="readiness__issues" aria-label="Validation issues">
      <article v-for="issue in issues" :key="issue.key" :class="['readiness__issue', `is-${issue.level}`]" :data-testid="`readiness-${issue.level}-${issue.code}`">
        <div><strong>{{ issue.code }}</strong><el-tag :type="issue.level === 'error' ? 'danger' : 'warning'" size="mini">{{ issue.level }}</el-tag></div>
        <p>{{ issue.message }}</p>
        <small v-if="issue.reference">{{ issue.reference }}</small>
      </article>
    </div>
  </section>
</template>
<script>
import { calculateReadiness } from './lesson-builder-logic';
export default {
  name: 'LessonPublishReadiness',
  props: { steps: { type: Array, default: () => [] }, assets: { type: Array, default: () => [] }, manifest: { type: Object, default: () => ({}) }, validation: { type: Object, default: null } },
  computed: {
    metrics() { return calculateReadiness({ steps: this.steps, assets: this.assets, manifest: this.manifest, validation: this.validation }); },
    ready() { return !this.metrics.estimateOnly && this.metrics.errors.length === 0 && this.metrics.offlineReady && this.metrics.allPathsTerminate && this.metrics.estimatedPeakPsram <= 1572864; },
    issues() {
      const normalize = (issue, level, index) => {
        const row = typeof issue === 'string' ? { message: issue } : (issue || {});
        const code = row.code || `${level}-${index + 1}`;
        const reference = row.stepKey ? `Step: ${row.stepKey}` : (row.assetKey ? `Asset: ${row.assetKey}` : '');
        return { ...row, code, level, reference, message: row.message || code, key: `${level}-${code}-${reference}-${index}` };
      };
      return [
        ...this.metrics.errors.map((issue, index) => normalize(issue, 'error', index)),
        ...this.metrics.warnings.map((issue, index) => normalize(issue, 'warning', index)),
      ];
    },
  },
  methods: { formatBytes(bytes) { const n = Number(bytes || 0); return n < 1024 ? `${n} B` : `${(n / 1048576).toFixed(n >= 1048576 ? 2 : 3)} MiB`; } },
};
</script>
<style scoped>
.readiness { background:#17312d; border-radius:18px; color:#fff8df; padding:16px; }.readiness__title { align-items:center; display:flex; justify-content:space-between; }.readiness__grid { display:grid; gap:10px; grid-template-columns:repeat(5,1fr); margin-top:14px; }.readiness__grid div { background:rgba(255,255,255,.08); border-radius:11px; display:grid; gap:5px; padding:10px; }.readiness__grid span { color:#b9cbc5; font-size:11px; text-transform:uppercase; }.readiness__grid strong { font-size:13px; }.readiness__issues{display:grid;gap:8px;margin-top:14px}.readiness__issue{border-left:4px solid #e4a23b;border-radius:8px;background:rgba(255,255,255,.09);padding:10px 12px}.readiness__issue.is-error{border-left-color:#ff6b57}.readiness__issue div{display:flex;align-items:center;justify-content:space-between;gap:10px}.readiness__issue p{margin:6px 0 0}.readiness__issue small{display:block;margin-top:5px;color:#c8d8d2} @media(max-width:900px){.readiness__grid{grid-template-columns:1fr 1fr}}
</style>
