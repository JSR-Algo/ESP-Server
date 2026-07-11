<template>
  <section class="readiness">
    <div class="readiness__title"><strong>Robot readiness</strong><el-tag :type="ready ? 'success' : 'warning'" size="mini">{{ ready ? 'READY' : 'CHECK' }}</el-tag></div>
    <div class="readiness__grid">
      <div><span>Download</span><strong>{{ formatBytes(metrics.downloadBytes) }}</strong></div>
      <div><span>Assets</span><strong>{{ metrics.uniqueAssetCount }} unique / {{ metrics.sharedReferenceCount }} shared</strong></div>
      <div><span>Peak PSRAM</span><strong>{{ formatBytes(metrics.estimatedPeakPsram) }}</strong></div>
      <div><span>Offline</span><strong>{{ metrics.offlineReady ? 'Ready' : 'Remote dependency' }}</strong></div>
      <div><span>All paths</span><strong>{{ metrics.allPathsTerminate ? 'Terminate' : 'Review branches' }}</strong></div>
    </div>
  </section>
</template>
<script>
import { calculateReadiness } from './lesson-builder-logic';
export default {
  name: 'LessonPublishReadiness',
  props: { steps: { type: Array, default: () => [] }, assets: { type: Array, default: () => [] }, manifest: { type: Object, default: () => ({}) } },
  computed: { metrics() { return calculateReadiness({ steps: this.steps, assets: this.assets, manifest: this.manifest }); }, ready() { return this.metrics.offlineReady && this.metrics.allPathsTerminate && this.metrics.estimatedPeakPsram <= 1572864; } },
  methods: { formatBytes(bytes) { const n = Number(bytes || 0); return n < 1024 ? `${n} B` : `${(n / 1048576).toFixed(n >= 1048576 ? 2 : 3)} MiB`; } },
};
</script>
<style scoped>
.readiness { background:#17312d; border-radius:18px; color:#fff8df; padding:16px; }.readiness__title { align-items:center; display:flex; justify-content:space-between; }.readiness__grid { display:grid; gap:10px; grid-template-columns:repeat(5,1fr); margin-top:14px; }.readiness__grid div { background:rgba(255,255,255,.08); border-radius:11px; display:grid; gap:5px; padding:10px; }.readiness__grid span { color:#b9cbc5; font-size:11px; text-transform:uppercase; }.readiness__grid strong { font-size:13px; } @media(max-width:900px){.readiness__grid{grid-template-columns:1fr 1fr}}
</style>
