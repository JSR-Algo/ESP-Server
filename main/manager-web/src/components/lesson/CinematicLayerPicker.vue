<template>
  <SharedAssetPicker
    class="cinematic-layer-picker"
    :data-slot="layerSlot"
    :title="title || layerSlot"
    :assets="assets"
    :category="category"
    :selected-version-id="selectedVersionId"
    :loading="loading"
    :error="error"
    :disabled="disabled"
    :show-actions="false"
    @select-version="selectVersion"
  />
</template>

<script>
import SharedAssetPicker from './SharedAssetPicker.vue';

export const SLOT_CATEGORY = Object.freeze({
  backgroundScene: 'scene',
  teachingObject: 'teachingObject',
  robotOverlay: 'robotPose',
});

export default {
  name: 'CinematicLayerPicker',
  components: { SharedAssetPicker },
  props: {
    layerSlot: { type: String, required: true, validator: (value) => Boolean(SLOT_CATEGORY[value]) },
    assets: { type: Array, default: () => [] }, selectedVersionId: { type: String, default: '' },
    loading: { type: Boolean, default: false }, error: { type: String, default: '' },
    disabled: { type: Boolean, default: false }, title: { type: String, default: '' },
  },
  computed: { category() { return SLOT_CATEGORY[this.layerSlot]; } },
  methods: {
    selectVersion(assetVersionId, asset) {
      if (this.disabled || !assetVersionId) return;
      this.$emit('select', { slot: this.layerSlot, category: this.category, assetVersionId, asset });
    },
  },
};
</script>
