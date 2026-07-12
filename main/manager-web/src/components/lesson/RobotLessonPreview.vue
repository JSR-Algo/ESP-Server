<template>
  <section class="robot-preview" aria-label="Exact ESP TFT lesson preview">
    <div v-if="projection.warnings.length" class="firmware-warning" role="alert" aria-live="assertive">
      <strong>Firmware-incompatible preview</strong>
      <span v-for="warning in projection.warnings" :key="warning">{{ warning }}</span>
    </div>

    <div class="stage-shell">
      <div class="stage" data-testid="esp-tft-stage">
        <template v-for="layer in projection.layers">
          <img
            v-if="layer.visible && ['background', 'teachingObject', 'robotOverlay'].includes(layer.id)"
            :key="layer.id"
            :class="['stage-layer', `layer-${layer.id}`, layer.id === 'robotOverlay' ? motionClass : '']"
            :style="layerStyle(layer)"
            :src="layer.src"
            :alt="layer.id === 'teachingObject' ? word : ''"
            draggable="false"
          />
          <div
            v-else-if="layer.id === 'teachingObject' && selectedPath === 'missingOptionalVisual'"
            :key="layer.id"
            class="stage-layer missing-visual"
            :style="layerStyle(layer)"
          >
            <span aria-hidden="true">Aa</span>
            <b>{{ word || 'Visual unavailable' }}</b>
          </div>
          <div
            v-else-if="layer.visible && layer.id === 'wordPill'"
            :key="layer.id"
            class="stage-layer word-pill"
            :style="layerStyle(layer)"
          >{{ layer.text }}</div>
          <div
            v-else-if="layer.visible && layer.id === 'progress'"
            :key="layer.id"
            class="stage-layer progress-dots"
            :style="layerStyle(layer)"
            :aria-label="`Step ${layer.active} of ${layer.total}`"
          >
            <i v-for="dot in layer.total" :key="dot" :class="{ active: dot <= layer.active }" />
          </div>
          <div
            v-else-if="layer.visible && layer.id === 'prompt'"
            :key="layer.id"
            class="stage-layer prompt-card"
            :style="layerStyle(layer)"
          >{{ layer.text }}</div>
        </template>

        <div v-if="showSafeZones" class="safe-zone safe-top" />
        <div v-if="showSafeZones" class="safe-zone safe-bottom" />
        <div v-if="showSafeZones" class="safe-zone safe-left" />
        <div v-if="showSafeZones" class="safe-zone safe-right" />
      </div>
    </div>

    <div class="preview-toolbar">
      <button
        v-for="path in responsePaths"
        :key="path"
        type="button"
        :class="{ selected: selectedPath === path }"
        :aria-pressed="selectedPath === path ? 'true' : 'false'"
        @click="selectPath(path)"
      >{{ pathLabels[path] }}</button>
      <label><input v-model="showSafeZones" type="checkbox" /> Safe zones</label>
    </div>

    <ol class="motion-timeline" aria-label="Robot command timeline">
      <li v-for="item in projection.timeline" :key="`${item.atMs}-${item.label}`">
        <time>{{ item.atMs }}ms</time><span>{{ item.label }}</span>
      </li>
    </ol>
  </section>
</template>

<script>
import { projectEspTftPreview, RESPONSE_PATHS } from './robot-preview-projection';

export default {
  name: 'RobotLessonPreview',
  props: {
    manifest: { type: Object, required: true },
    stepIndex: { type: Number, default: 0 },
    initialPath: { type: String, default: 'correct' }
  },
  data() {
    return {
      selectedPath: RESPONSE_PATHS.includes(this.initialPath) ? this.initialPath : 'correct',
      showSafeZones: false,
      motionNonce: 0,
      responsePaths: RESPONSE_PATHS,
      pathLabels: {
        correct: 'Correct',
        nearMiss: 'Near miss',
        incorrect: 'Incorrect',
        retry: 'Retry',
        timeout: 'Timeout',
        braveTry: 'Brave try',
        completion: 'Completion',
        silence: 'Silence',
        sttUnavailable: 'STT unavailable',
        missingOptionalVisual: 'Missing visual'
      }
    };
  },
  computed: {
    projection() {
      return projectEspTftPreview(this.manifest, this.stepIndex, this.selectedPath);
    },
    word() {
      const pill = this.projection.layers.find((layer) => layer.id === 'wordPill');
      return pill ? pill.text : '';
    },
    motionClass() {
      const command = this.projection.timeline.find((item) => item.label.startsWith('Slave command:'));
      const preset = command ? command.label.slice('Slave command:'.length).trim() : 'neutral';
      if (/nod|celebrate|encourage/i.test(preset)) return `motion-nod motion-${this.motionNonce % 2}`;
      if (/shake/i.test(preset)) return `motion-shake motion-${this.motionNonce % 2}`;
      if (/lean|tilt|teach/i.test(preset)) return `motion-tilt motion-${this.motionNonce % 2}`;
      return `motion-breathe motion-${this.motionNonce % 2}`;
    }
  },
  watch: {
    initialPath(path) {
      if (RESPONSE_PATHS.includes(path)) this.selectPath(path);
    }
  },
  methods: {
    layerStyle(layer) {
      const { x, y, width, height, fit } = layer.bounds;
      return {
        left: `${x}px`, top: `${y}px`, width: `${width}px`, height: `${height}px`,
        zIndex: layer.z, objectFit: fit || undefined
      };
    },
    selectPath(path) {
      this.selectedPath = path;
      this.motionNonce += 1;
      this.$emit('path-change', { path, projection: projectEspTftPreview(this.manifest, this.stepIndex, path) });
    }
  }
};
</script>

<style scoped>
.robot-preview { --ink: #17211b; --cream: #fff8e7; --lime: #b9ec45; color: var(--ink); }
.firmware-warning { display: grid; gap: 3px; margin-bottom: 12px; padding: 12px 14px; border: 2px solid #9c2218; border-radius: 10px; background: #fff0ea; color: #78140d; }
.stage-shell { width: 100%; overflow-x: auto; padding: 14px; box-sizing: border-box; border-radius: 18px; background: repeating-linear-gradient(135deg, #18231d, #18231d 10px, #202f26 10px, #202f26 20px); }
.stage { position: relative; width: 480px; height: 320px; margin: 0 auto; overflow: hidden; background: #dce8c2; box-shadow: 0 12px 30px rgba(0, 0, 0, .35); font-family: "Trebuchet MS", sans-serif; }
.stage-layer { position: absolute; box-sizing: border-box; }
.layer-background { object-fit: cover; }
.layer-teachingObject, .layer-robotOverlay { object-fit: contain; }
.word-pill { display: flex; align-items: center; justify-content: center; border: 3px solid #16251c; border-radius: 24px; background: #fff8dc; box-shadow: 0 4px 0 #16251c; font-size: 25px; font-weight: 900; letter-spacing: .4px; text-transform: lowercase; }
.prompt-card { display: flex; align-items: center; justify-content: center; padding: 8px 24px; border-radius: 19px 19px 0 0; background: rgba(12, 24, 17, .9); color: white; font-size: 20px; font-weight: 800; line-height: 1.15; text-align: center; }
.progress-dots { display: flex; align-items: center; justify-content: center; gap: 6px; }
.progress-dots i { width: 8px; height: 8px; border: 2px solid white; border-radius: 50%; background: rgba(0, 0, 0, .38); }
.progress-dots i.active { background: var(--lime); }
.missing-visual { display: flex; flex-direction: column; align-items: center; justify-content: center; border: 3px dashed #263d30; border-radius: 28px; background: rgba(255, 248, 231, .9); }
.missing-visual span { font-size: 50px; font-weight: 900; }
.safe-zone { position: absolute; z-index: 90; pointer-events: none; background: rgba(255, 80, 40, .15); outline: 1px dashed rgba(255, 45, 20, .85); }
.safe-top { top: 0; left: 0; width: 100%; height: 24px; }.safe-bottom { bottom: 0; left: 0; width: 100%; height: 82px; }.safe-left { top: 0; left: 0; width: 12px; height: 100%; }.safe-right { top: 0; right: 0; width: 12px; height: 100%; }
.preview-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
.preview-toolbar button { padding: 7px 11px; border: 1px solid #9ba89f; border-radius: 999px; background: white; color: #26342b; cursor: pointer; font: inherit; }
.preview-toolbar button.selected { border-color: #17211b; background: var(--lime); box-shadow: inset 0 -2px rgba(0, 0, 0, .18); font-weight: 800; }
.preview-toolbar label { margin-left: auto; font-size: 13px; }
.motion-timeline { display: grid; gap: 6px; margin: 12px 0 0; padding: 0; list-style: none; }
.motion-timeline li { display: grid; grid-template-columns: 62px 1fr; gap: 8px; padding: 7px 10px; border-left: 3px solid #648c22; background: #f1f6e8; }
.motion-timeline time { color: #607064; font-variant-numeric: tabular-nums; }
.motion-nod { animation: nod .55s ease-in-out; transform-origin: 50% 100%; }.motion-shake { animation: shake .55s ease-in-out; }.motion-tilt { animation: tilt .7s ease-in-out; transform-origin: 50% 100%; }.motion-breathe { animation: breathe 1.2s ease-in-out; }
.motion-1 { animation-delay: .001s; }
@keyframes nod { 30% { transform: rotate(4deg) translateY(5px); } 65% { transform: rotate(-2deg); } }
@keyframes shake { 25% { transform: translateX(-8px); } 70% { transform: translateX(8px); } }
@keyframes tilt { 45% { transform: rotate(-7deg); } }
@keyframes breathe { 50% { transform: translateY(-3px) scale(1.015); } }
@media (max-width: 560px) { .stage-shell { padding: 8px; }.stage { transform-origin: top left; } .preview-toolbar label { width: 100%; margin-left: 0; } }
@media (prefers-reduced-motion: reduce) { .layer-robotOverlay { animation: none; } }
</style>
