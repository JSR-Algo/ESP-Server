<template>
  <section class="asset-picker">
    <div class="asset-picker__head"><strong>Shared visual</strong><el-input v-model="query" size="mini" clearable placeholder="Filter asset key" /></div>
    <div class="asset-picker__grid">
      <div v-for="asset in filtered" :key="asset.assetKey + ':' + (asset.version || '')" :class="['asset-tile', { selected: selectedKey === asset.assetKey }]">
        <button type="button" class="asset-tile__select" @click="$emit('select', asset)">
          <span class="asset-tile__preview"><img v-if="asset.thumbnailUrl || asset.url" :src="asset.thumbnailUrl || asset.url" alt="" /><span v-else>{{ initials(asset.assetKey) }}</span></span>
          <strong>{{ asset.assetKey }}</strong><small>v{{ asset.version || 1 }} · {{ formatBytes(asset.bytes) }} · {{ asset.usageCount || 0 }} uses</small>
        </button>
        <span class="asset-tile__actions"><button type="button" @click="$emit('inspect', asset)">Inspect</button><button type="button" @click="$emit('clone', asset)">Clone</button></span>
      </div>
      <div v-if="!filtered.length" class="empty">No matching shared visuals.</div>
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
    initials(key) { return String(key || 'AS').split('.').slice(-2).map((p) => p[0]).join('').toUpperCase(); },
    formatBytes(bytes) { const n = Number(bytes || 0); return n < 1024 ? `${n} B` : `${Math.round(n / 1024)} KiB`; },
  },
};
</script>
<style scoped>
.asset-picker { border-top:1px solid #eee3cd; margin-top:16px; padding-top:14px; }.asset-picker__head { align-items:center; display:flex; gap:14px; justify-content:space-between; }.asset-picker__head .el-input { width:190px; }
.asset-picker__grid { display:flex; gap:9px; margin-top:10px; overflow-x:auto; }.asset-tile { background:#fff; border:2px solid transparent; border-radius:12px; display:grid; flex:0 0 145px; gap:4px; padding:7px; text-align:left; }.asset-tile.selected { border-color:#e6a62c; }.asset-tile__select { background:transparent;border:0;cursor:pointer;display:grid;gap:4px;padding:0;text-align:left;width:100%}.asset-tile__preview { align-items:center; background:#edf2ef; border-radius:8px; display:flex; height:68px; justify-content:center; overflow:hidden; }.asset-tile__preview img { height:100%; object-fit:cover; width:100%; }.asset-tile small { color:#7c8582; }.asset-tile__actions{display:flex;gap:4px}.asset-tile__actions button{background:#edf2ef;border:0;border-radius:7px;color:#31524a;cursor:pointer;flex:1;font-size:11px;padding:5px}.empty { color:#909399; padding:16px 0; }
</style>
