<template>
  <section class="asset-picker">
    <div class="asset-picker__head"><strong>{{ title || $t('lesson.sharedVisual') }}</strong><el-input v-model="query" size="mini" clearable :placeholder="$t('lesson.sharedVisualFilter')" /></div>
    <div v-if="loading" class="asset-picker__state asset-picker__loading">Loading cinematic assets…</div>
    <div v-else-if="error" class="asset-picker__state asset-picker__error" role="alert">{{ error }}</div>
    <div v-else-if="!filtered.length" class="asset-picker__state asset-picker__empty">{{ $t('lesson.sharedVisualEmpty') }}</div>
    <div v-else class="asset-picker__grid">
      <div v-for="asset in filtered" :key="asset.versionId || (asset.assetKey + ':' + (asset.version || ''))" :class="['asset-tile', { selected: isSelected(asset) }]">
        <button type="button" class="asset-tile__select" :disabled="disabled" @click="selectAsset(asset)">
          <span class="asset-tile__preview">
            <video v-if="isMp4(asset)" :src="asset.url" muted playsinline preload="metadata" />
            <img v-else-if="asset.thumbnailUrl || asset.url" :src="asset.thumbnailUrl || asset.url" alt="" />
            <span v-else>{{ initials(asset.assetKey) }}</span>
          </span>
          <strong>{{ asset.assetKey }}</strong><small>v{{ asset.version || 1 }} · {{ formatBytes(asset.bytes) }} · {{ asset.usageCount || 0 }} uses</small>
        </button>
        <span v-if="showActions" class="asset-tile__actions"><button type="button" :disabled="disabled" @click="$emit('inspect', asset)">Inspect</button><button type="button" :disabled="disabled" @click="$emit('clone', asset)">Clone</button></span>
      </div>
    </div>
  </section>
</template>
<script>
export default {
  name: 'SharedAssetPicker',
  props: {
    assets: { type: Array, default: () => [] }, selectedKey: { type: String, default: '' }, selectedVersionId: { type: String, default: '' },
    category: { type: String, default: '' }, disabled: { type: Boolean, default: false }, loading: { type: Boolean, default: false },
    error: { type: String, default: '' }, title: { type: String, default: '' }, showActions: { type: Boolean, default: true },
  },
  data: () => ({ query: '' }),
  computed: { filtered() { const q = this.query.toLowerCase(); return this.assets.filter((a) => (!this.category || a.category === this.category || a.layer === this.category) && (!q || String(a.assetKey).toLowerCase().includes(q))); } },
  methods: {
    selectAsset(asset) {
      if (this.disabled) return;
      this.$emit('select-intent', asset);
      this.$emit('select-version', asset.versionId, asset);
    },
    isSelected(asset) { return this.selectedVersionId ? this.selectedVersionId === asset.versionId : this.selectedKey === asset.assetKey; },
    isMp4(asset) { return asset && (asset.mimeType === 'video/mp4' || /\.mp4(?:$|[?#])/i.test(asset.url || '')); },
    initials(key) { return String(key || 'AS').split('.').slice(-2).map((p) => p[0]).join('').toUpperCase(); },
    formatBytes(bytes) { const n = Number(bytes || 0); return n < 1024 ? `${n} B` : `${Math.round(n / 1024)} KiB`; },
  },
};
</script>
<style scoped>
.asset-picker { border-top:1px solid #eee3cd; margin-top:16px; padding-top:14px; }.asset-picker__head { align-items:center; display:flex; gap:14px; justify-content:space-between; }.asset-picker__head .el-input { width:190px; }
.asset-picker__grid { display:flex; gap:9px; margin-top:10px; overflow-x:auto; }.asset-picker__state { border-radius:10px; margin-top:10px; padding:16px; }.asset-picker__loading,.asset-picker__empty { background:#f3f6f4; color:#66736f; }.asset-picker__error { background:#fff1f0; color:#a63b32; }.asset-tile { background:#fff; border:2px solid transparent; border-radius:12px; display:grid; flex:0 0 145px; gap:4px; padding:7px; text-align:left; }.asset-tile.selected { border-color:#e6a62c; }.asset-tile__select { background:transparent;border:0;cursor:pointer;display:grid;gap:4px;padding:0;text-align:left;width:100%}.asset-tile__select:disabled { cursor:not-allowed; opacity:.55; }.asset-tile__preview { align-items:center; background:#edf2ef; border-radius:8px; display:flex; height:68px; justify-content:center; overflow:hidden; }.asset-tile__preview img,.asset-tile__preview video { height:100%; object-fit:cover; width:100%; }.asset-tile small { color:#7c8582; }.asset-tile__actions{display:flex;gap:4px}.asset-tile__actions button{background:#edf2ef;border:0;border-radius:7px;color:#31524a;cursor:pointer;flex:1;font-size:11px;padding:5px}.asset-tile__actions button:disabled{cursor:not-allowed;opacity:.55}
</style>
