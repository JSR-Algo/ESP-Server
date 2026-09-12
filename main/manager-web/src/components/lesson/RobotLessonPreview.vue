<template>
  <RobotEspTftProjectionPreview
    v-if="exactManifest"
    :manifest="exactManifest"
    :renderer-metadata="rendererMetadata || manifestPreview"
    :step-index="stepIndex"
    :initial-path="initialPath"
    @path-change="$emit('path-change', $event)"
  />
  <RobotManifestServerPreview
    v-else-if="manifestPreview"
    :manifest-preview="manifestPreview"
    :step-index="stepIndex"
  />
</template>

<script>
import RobotEspTftProjectionPreview from './RobotEspTftProjectionPreview.vue';
import RobotManifestServerPreview from './RobotManifestServerPreview.vue';

// Merge of two lineages: the server-manifest preview (manifestPreview prop) and
// the exact ESP-TFT layer projection (manifest prop). The prop supplied selects
// the implementation; passing both prefers the exact projection.
export default {
  name: 'RobotLessonPreview',
  components: { RobotEspTftProjectionPreview, RobotManifestServerPreview },
  props: {
    manifest: { type: Object, default: null },
    rendererMetadata: { type: Object, default: null },
    manifestPreview: { type: Object, default: null },
    stepIndex: { type: Number, default: 0 },
    initialPath: { type: String, default: 'correct' },
  },
  computed: {
    exactManifest() {
      if (this.manifest) return this.manifest;
      const saved = this.manifestPreview && this.manifestPreview.manifest;
      return saved && saved.manifestVersion === 'teebot-lesson-renderer.v5' ? saved : null;
    },
  },
};
</script>
