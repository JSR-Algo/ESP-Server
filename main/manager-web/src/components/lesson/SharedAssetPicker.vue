<template>
  <section class="asset-picker">
    <div class="asset-picker__head"><strong class="asset-picker__title">{{ title || $t('lesson.sharedVisual') }}</strong><el-input v-model="query" size="mini" clearable :placeholder="$t('lesson.sharedVisualFilter')" /></div>
    <div v-if="loading" class="asset-picker__state asset-picker__loading">Loading cinematic assets…</div>
    <div v-else-if="error" class="asset-picker__state asset-picker__error" role="alert">{{ error }}</div>
    <div v-else-if="!filtered.length" class="asset-picker__state asset-picker__empty">{{ $t('lesson.sharedVisualEmpty') }}</div>
    <div v-else ref="grid" class="asset-picker__grid">
      <div v-for="asset in filtered" :key="assetId(asset)" v-preview-tile="assetId(asset)" :class="['asset-tile', { selected: isSelected(asset) }]" @focusin="revealAsset($event)">
        <button type="button" class="asset-tile__select" :disabled="disabled" @click="selectAsset(asset)">
          <span class="asset-tile__preview">
            <PickerMjpegThumbnail v-if="previewVisible[assetId(asset)] && mjpegIdentities.get(asset)" :src="asset.url"
              :identity="mjpegIdentities.get(asset)" :layer-id="asset.assetKey" :registry="thumbnailRegistry" :ref="'thumbnail-' + assetId(asset)"
              @error="$set(previewErrors, assetId(asset), $event)" />
            <video v-else-if="previewVisible[assetId(asset)] && isMp4(asset)" v-release-video :src="asset.url" muted playsinline preload="metadata" />
            <img v-else-if="previewVisible[assetId(asset)] && !mjpegIdentities.get(asset) && !isMp4(asset) && (asset.thumbnailUrl || asset.url)" :src="asset.thumbnailUrl || asset.url" alt="" />
            <span v-else>{{ initials(asset.assetKey) }}</span>
          </span>
          <strong>{{ asset.assetKey }}</strong><small>v{{ asset.version || 1 }} · {{ formatBytes(asset.bytes) }} · {{ asset.usageCount || 0 }} uses</small>
        </button>
        <button v-if="previewVisible[assetId(asset)] && previewErrors[assetId(asset)]" type="button" :disabled="disabled"
          @click="retryPreview(asset)">Retry preview</button>
        <span v-if="showActions" class="asset-tile__actions"><button type="button" :disabled="disabled" @click="$emit('inspect', asset)">Inspect</button><button type="button" :disabled="disabled" @click="$emit('clone', asset)">Clone</button></span>
      </div>
    </div>
  </section>
</template>
<script>
import PickerMjpegThumbnail from './PickerMjpegThumbnail.vue';
import { PickerMjpegThumbnails } from './picker-mjpeg-thumbnails.mjs';

export default {
  name: 'SharedAssetPicker',
  components: { PickerMjpegThumbnail },
  directives: {
    previewTile: {
      inserted(el, binding, vnode) { vnode.context.observeTile(el, binding.value); },
      unbind(el, binding, vnode) { vnode.context.retireTile(el, binding.value); },
    },
    releaseVideo: {
      unbind(video) { video.pause(); video.removeAttribute('src'); video.load(); },
    },
  },
  props: {
    assets: { type: Array, default: () => [] }, selectedKey: { type: String, default: '' }, selectedVersionId: { type: String, default: '' },
    category: { type: String, default: '' }, disabled: { type: Boolean, default: false }, loading: { type: Boolean, default: false },
    error: { type: String, default: '' }, title: { type: String, default: '' }, showActions: { type: Boolean, default: true },
  },
  data: () => ({ query: '', previewVisible: {}, previewErrors: {} }),
  created() {
    this.thumbnailRegistry = new PickerMjpegThumbnails();
    this._previewTiles = new Map();
    if (typeof IntersectionObserver !== 'undefined') {
      this._previewObserver = new IntersectionObserver(entries => {
        if (this._isBeingDestroyed || this._isDestroyed) return;
        for (const entry of entries) {
          const id = this._previewTiles.get(entry.target);
          // Threshold zero includes touching edges; no new callback is guaranteed
          // when that tile moves further into view, so keep the same predicate.
          if (id !== undefined) this.$set(this.previewVisible, id, entry.isIntersecting);
        }
      });
    }
  },
  mounted() {
    if (!this._previewObserver) {
      window.addEventListener('scroll', this.schedulePreviewMeasure, true);
      window.addEventListener('resize', this.schedulePreviewMeasure);
      this.schedulePreviewMeasure();
    }
  },
  updated() { if (!this._previewObserver) this.schedulePreviewMeasure(); },
  beforeDestroy() {
    this.thumbnailRegistry.dispose();
    if (this._previewObserver) this._previewObserver.disconnect();
    window.removeEventListener('scroll', this.schedulePreviewMeasure, true);
    window.removeEventListener('resize', this.schedulePreviewMeasure);
    if (this._previewMeasure) cancelAnimationFrame(this._previewMeasure);
    this._previewTiles.clear();
  },
  computed: {
    filtered() { const q = this.query.toLowerCase(); return this.assets.filter((a) => (!this.category || a.category === this.category || a.layer === this.category) && (!q || String(a.assetKey).toLowerCase().includes(q))); },
    mjpegIdentities() {
      // Catalog readbacks replace objects without necessarily changing media.
      const previous = this._mjpegIdentityCache || new Map();
      const current = new Map();
      const identities = new Map(this.assets.filter(asset => asset.compatibilityMetadata?.codec === 'mjpeg')
        .map(asset => {
          const identity = { bytes: asset.bytes, sha256: asset.sha256,
            metadata: { ...asset.compatibilityMetadata, mediaType: asset.mimeType, width: asset.width, height: asset.height } };
          const key = JSON.stringify(identity);
          const stable = current.get(key) || previous.get(key) || identity;
          current.set(key, stable);
          return [asset, stable];
        }));
      this._mjpegIdentityCache = current;
      return identities;
    },
  },
  methods: {
    retryPreview(asset) {
      const previews = this.$refs['thumbnail-' + this.assetId(asset)];
      if (previews && previews[0]) previews[0].retry();
    },
    assetId(asset) { return asset.versionId || (asset.assetKey + ':' + (asset.version || '')); },
    observeTile(el, id) {
      this._previewTiles.set(el, id);
      if (this._previewObserver) this._previewObserver.observe(el);
      else this.schedulePreviewMeasure();
    },
    retireTile(el, id) {
      if (this._previewObserver) this._previewObserver.unobserve(el);
      this._previewTiles.delete(el);
      this.$delete(this.previewVisible, id);
    },
    revealAsset(event) {
      // Keyboard navigation also scrolls the focused choice into the preview window.
      event.currentTarget.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      if (!this._previewObserver) this.schedulePreviewMeasure();
    },
    schedulePreviewMeasure() {
      if (this._previewMeasure || this._isBeingDestroyed || this._isDestroyed) return;
      this._previewMeasure = requestAnimationFrame(() => {
        this._previewMeasure = null;
        for (const [tile, id] of this._previewTiles) {
          let left = 0, top = 0, right = window.innerWidth, bottom = window.innerHeight;
          // Older browsers still honor every scroll ancestor instead of loading the catalog.
          for (let parent = tile.parentElement; parent; parent = parent.parentElement) {
            const style = getComputedStyle(parent), rect = parent.getBoundingClientRect();
            if (/(auto|scroll|hidden|clip)/.test(style.overflowX)) { left = Math.max(left, rect.left); right = Math.min(right, rect.right); }
            if (/(auto|scroll|hidden|clip)/.test(style.overflowY)) { top = Math.max(top, rect.top); bottom = Math.min(bottom, rect.bottom); }
          }
          const rect = tile.getBoundingClientRect();
          const visible = rect.width > 0 && rect.height > 0 && rect.right > left && rect.left < right && rect.bottom > top && rect.top < bottom;
          if (this.previewVisible[id] !== visible) this.$set(this.previewVisible, id, visible);
        }
      });
    },
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
.asset-picker { border-top:1px solid #eee3cd; margin-top:16px; max-width:100%; min-width:0; padding-top:14px; }
.asset-picker__head { align-items:center; display:flex; gap:14px; justify-content:space-between; max-width:100%; min-width:0; }
.asset-picker__title { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.asset-picker__head .el-input { flex:0 1 190px; min-width:0; width:190px; }
.asset-picker__grid { display:flex; gap:9px; margin-top:10px; max-width:100%; min-width:0; overflow-x:auto; overscroll-behavior-x:contain; }
.asset-picker__state { border-radius:10px; margin-top:10px; max-width:100%; min-width:0; overflow-wrap:anywhere; padding:16px; word-break:break-word; }
.asset-picker__loading,.asset-picker__empty { background:#f3f6f4; color:#66736f; }.asset-picker__error { background:#fff1f0; color:#a63b32; }
.asset-tile { background:#fff; border:2px solid transparent; border-radius:12px; display:grid; flex:0 0 145px; gap:4px; max-width:145px; min-width:0; padding:7px; text-align:left; }
.asset-tile.selected { border-color:#e6a62c; }
.asset-tile__select { background:transparent;border:0;cursor:pointer;display:grid;gap:4px;min-width:0;padding:0;text-align:left;width:100%}
.asset-tile__select:disabled { cursor:not-allowed; opacity:.55; }
.asset-tile__preview { align-items:center; background:#edf2ef; border-radius:8px; display:flex; height:68px; justify-content:center; min-width:0; overflow:hidden; }
.asset-tile__preview img,.asset-tile__preview video { height:100%; object-fit:cover; width:100%; }
.asset-tile strong,.asset-tile small { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.asset-tile small { color:#7c8582; }
.asset-tile__actions{display:flex;gap:4px;min-width:0}
.asset-tile__actions button{background:#edf2ef;border:0;border-radius:7px;color:#31524a;cursor:pointer;flex:1;font-size:11px;min-width:0;overflow:hidden;padding:5px;text-overflow:ellipsis;white-space:nowrap}
.asset-tile__actions button:disabled{cursor:not-allowed;opacity:.55}
@media (max-width:560px) {
  .asset-picker__head { align-items:stretch; flex-direction:column; gap:8px; }
  .asset-picker__title { white-space:normal; }
  .asset-picker__head .el-input { flex:0 0 auto; width:100%; }
}
</style>
