<template>
  <section class="template-panel">
    <div class="template-panel__heading">
      <div><span>OPTIONAL ENTRANCE</span><strong>TVideo Fly &amp; Walk</strong></div>
      <el-switch v-model="enabled" active-text="Use template" />
    </div>
    <template v-if="enabled">
      <el-form label-position="top" size="small">
        <el-form-item label="Vocabulary set ID">
          <el-input v-model="draft.vocabularySetId" placeholder="e.g. animals-a1" />
        </el-form-item>
        <el-form-item label="Background workflow">
          <el-radio-group v-model="draft.backgroundMode" size="small">
            <el-radio-button label="reuseShared">Reuse shared background</el-radio-button>
            <el-radio-button label="cloneForLesson">Clone for this lesson</el-radio-button>
            <el-radio-button label="newSharedVersion">Upload a new shared background version</el-radio-button>
          </el-radio-group>
          <p v-if="draft.backgroundMode === 'cloneForLesson'" class="workflow-note">The selected shared version is pinned now; use the lesson asset manager to upload the lesson-specific clone.</p>
          <p v-if="draft.backgroundMode === 'newSharedVersion'" class="workflow-note">Publish and curate compatibility for the new shared version before selecting it here.</p>
        </el-form-item>
        <el-form-item label="Compatible background">
          <el-select v-model="draft.backgroundVersionId" placeholder="Choose a versioned background" @change="syncBackground">
            <el-option v-for="asset in backgrounds" :key="asset.assetKey" :label="asset.name || asset.assetKey" :value="asset.assetKey" />
          </el-select>
        </el-form-item>
        <el-form-item label="Named layout preset">
          <el-radio-group v-model="draft.layoutPreset" :disabled="!availableLayouts.length">
            <el-radio-button v-for="preset in availableLayouts" :key="preset" :label="preset">{{ preset }}</el-radio-button>
          </el-radio-group>
          <p v-if="draft.backgroundVersionId && !availableLayouts.length" class="blocking">This background has no compatible reviewed layout.</p>
        </el-form-item>
        <div class="asset-pins">
          <el-form-item label="Static arrived pose"><el-select v-model="draft.arrivedPoseVersionId"><el-option v-for="asset in arrivedPoses" :key="asset.assetKey" :label="asset.assetKey" :value="asset.assetKey" /></el-select></el-form-item>
          <el-form-item label="Optional sprite atlas"><el-select v-model="draft.atlasVersionId" clearable><el-option v-for="asset in atlases" :key="asset.assetKey" :label="asset.assetKey" :value="asset.assetKey" /></el-select></el-form-item>
        </div>
      </el-form>
      <div class="template-panel__note">Vocabulary sets and backgrounds stay independent. Geometry, phase timing, and firmware motion are reviewed presets, not lesson fields.</div>
    </template>
  </section>
</template>
<script>
import { buildTemplateAuthoring, compatibleLayouts } from './tvideo-template-logic';
export default {
  name: 'TvideoTemplatePanel',
  props: { value: { type: Object, default: null }, assets: { type: Array, default: () => [] } },
  data() { return { enabled: Boolean(this.value), draft: { templateId: 'tvideoFlyWalk', vocabularySetId: '', backgroundMode: 'reuseShared', layoutPreset: 'centerRoad', backgroundVersionId: '', backgroundAssetVersionId: '', arrivedPoseVersionId: '', atlasVersionId: '', backgroundCompatibility: null, ...(this.value || {}) } }; },
  computed: {
    backgrounds() { return this.assets.filter((asset) => asset.layer === 'backgroundScene'); },
    arrivedPoses() { return this.assets.filter((asset) => asset.layer === 'robotOverlay' && /arriv|pose/i.test(`${asset.assetKey} ${asset.role || ''}`)); },
    atlases() { return this.assets.filter((asset) => /atlas/i.test(`${asset.assetKey} ${asset.role || ''}`)); },
    selectedBackground() { return this.backgrounds.find((asset) => asset.assetKey === this.draft.backgroundVersionId); },
    availableLayouts() { const meta = this.selectedBackground && (this.selectedBackground.compatibility || this.selectedBackground); return compatibleLayouts(meta); },
  },
  watch: {
    enabled() { this.emitValue(); },
    draft: { deep: true, handler() { this.emitValue(); } },
  },
  methods: {
    syncBackground() { this.draft.backgroundCompatibility = this.selectedBackground && (this.selectedBackground.compatibility || this.selectedBackground); this.draft.backgroundAssetVersionId = (this.selectedBackground && this.selectedBackground.assetVersionId) || ''; if (!this.availableLayouts.includes(this.draft.layoutPreset)) this.draft.layoutPreset = this.availableLayouts[0] || ''; },
    emitValue() { if (!this.enabled) return this.$emit('input', null); try { this.$emit('input', buildTemplateAuthoring(this.draft)); } catch (_) { this.$emit('input', { ...this.draft, invalid: true }); } },
  },
};
</script>
<style scoped>
.template-panel{background:linear-gradient(135deg,#fff4d4,#e7f3e8);border:1px solid #d2b86d;border-radius:18px;margin-bottom:16px;padding:16px}.template-panel__heading{align-items:center;display:flex;justify-content:space-between;margin-bottom:14px}.template-panel__heading div{display:grid;gap:3px}.template-panel__heading span{color:#806f3d;font-size:10px;font-weight:800;letter-spacing:.12em}.template-panel__heading strong{color:#17312d;font-family:Georgia,serif;font-size:20px}.asset-pins{display:grid;gap:12px;grid-template-columns:1fr 1fr}.template-panel__note{background:rgba(255,255,255,.62);border-radius:10px;color:#415b55;font-size:12px;line-height:1.5;padding:10px}.blocking{color:#b53d27;font-size:12px;margin:6px 0 0}.workflow-note{color:#5b6d67;font-size:11px;margin:6px 0 0}@media(max-width:800px){.asset-pins{grid-template-columns:1fr}}
</style>
