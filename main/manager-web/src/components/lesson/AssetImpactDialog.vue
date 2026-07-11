<template>
  <el-dialog :visible.sync="shown" :title="$t('visual.impactTitle')" width="min(560px, calc(100vw - 24px))" custom-class="asset-impact-dialog" :close-on-click-modal="false">
    <el-alert :title="$t('visual.impactWarning')" type="warning" show-icon :closable="false" />
    <div class="impact-grid">
      <div><strong>{{ impact.courses || 0 }}</strong><span>{{ $t('visual.courses') }}</span></div>
      <div><strong>{{ impact.lessons || 0 }}</strong><span>{{ $t('visual.lessons') }}</span></div>
      <div><strong>{{ impact.publishedVersions || 0 }}</strong><span>{{ $t('visual.publishedVersions') }}</span></div>
      <div class="active"><strong>{{ impact.activeAssignments || 0 }}</strong><span>{{ $t('visual.activeAssignments') }}</span></div>
    </div>
    <p class="muted">{{ $t('visual.impactMode') }}: <b>{{ mode }}</b></p>
    <span slot="footer">
      <el-button @click="$emit('cancel')">{{ $t('course.cancel') }}</el-button>
      <el-button type="danger" :loading="confirming" @click="$emit('confirm')">{{ $t('visual.confirmReplace') }}</el-button>
    </span>
  </el-dialog>
</template>
<script>
export default {
  name: 'AssetImpactDialog', props: { visible: Boolean, impact: { type: Object, default: () => ({}) }, mode: { type: String, default: '' }, confirming: Boolean },
  computed: { shown: { get() { return this.visible; }, set(value) { if (!value) this.$emit('cancel'); } } },
};
</script>
<style scoped>
.impact-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:20px 0}.impact-grid div{padding:14px 8px;border-radius:10px;background:#f2f5fb;text-align:center}.impact-grid strong{display:block;font-size:24px}.impact-grid span{font-size:12px;color:#667085}.impact-grid .active{background:#fff1f0;color:#cf1322}.muted{color:#667085}@media(max-width:600px){.impact-grid{grid-template-columns:repeat(2,1fr)}}
</style>
