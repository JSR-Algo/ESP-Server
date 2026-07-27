<template>
  <RobotEspTftProjectionPreview
    v-if="manifest"
    :manifest="manifest"
    :renderer-metadata="rendererMetadata"
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
};
</script>
