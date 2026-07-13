<template>
  <section class="asset-picker">
    <div class="asset-picker__head"><strong>{{ $t('lesson.sharedVisual') }}</strong><el-input v-model="query" size="mini" clearable :placeholder="$t('lesson.sharedVisualFilter')" /></div>
    <div class="asset-picker__grid">
      <button v-for="asset in filtered" :key="asset.assetKey + ':' + (asset.version || '')" type="button" :class="['asset-tile', { selected: selectedKey === asset.assetKey }]" @click="selectAsset(asset)">
        <span class="asset-tile__preview"><img v-if="asset.thumbnailUrl || asset.url" :src="asset.thumbnailUrl || asset.url" alt="" /><span v-else>{{ initials(asset.assetKey) }}</span></span>
        <strong>{{ asset.assetKey }}</strong><small>v{{ asset.version || 1 }} · {{ formatBytes(asset.bytes) }}</small>
      </button>
      <div v-if="!filtered.length" class="empty">{{ $t('lesson.sharedVisualEmpty') }}</div>
    </div>
  </section>
</template>
<script>
export default {
  name: 'SharedAssetPicker',
  props: { assets: { type: Array, default: () => [] }, selectedKey: { type: String, default: '' }, category: { type: String, default: '' } },
  data: () => ({ query: '' }),
  computed: { filtered() { const q = this.query.toLowerCase(); return this.assets.filter((a) => (!this.category || a.category === this.category || a.layer === this.category) && (!q || String(a.assetKey).toLowerCase().includes(q))); } },
  methods: {
    selectAsset(asset) { this.$emit('select-intent', asset); },
    initials(key) { return String(key || 'AS').split('.').slice(-2).map((p) => p[0]).join('').toUpperCase(); },
    formatBytes(bytes) { const n = Number(bytes || 0); return n < 1024 ? `${n} B` : `${Math.round(n / 1024)} KiB`; },
  },
};
</script>
<style scoped>
.asset-picker { border-top:1px solid #eee3cd; margin-top:16px; padding-top:14px; }.asset-picker__head { align-items:center; display:flex; gap:14px; justify-content:space-between; }.asset-picker__head .el-input { width:190px; }
.asset-picker__grid { display:flex; gap:9px; margin-top:10px; overflow-x:auto; }.asset-tile { background:#fff; border:2px solid transparent; border-radius:12px; cursor:pointer; display:grid; flex:0 0 130px; gap:4px; padding:7px; text-align:left; }.asset-tile.selected { border-color:#e6a62c; }.asset-tile__preview { align-items:center; background:#edf2ef; border-radius:8px; display:flex; height:68px; justify-content:center; overflow:hidden; }.asset-tile__preview img { height:100%; object-fit:cover; width:100%; }.asset-tile small { color:#7c8582; }.empty { color:#909399; padding:16px 0; }
</style>
