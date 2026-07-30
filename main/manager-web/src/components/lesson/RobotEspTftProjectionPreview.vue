<template>
  <section class="robot-preview" aria-label="Exact ESP TFT lesson preview">
    <div class="contract-head">
      <div>
        <span class="contract-kicker">Exact 480x320 TFT projection</span>
        <strong>{{ projection.rendererLabel }}</strong>
        <code>{{ projection.manifestVersion || 'manifest identity unavailable' }}</code>
      </div>
      <span :class="['capability-badge', { supported: projection.capability.supported === true, unknown: projection.capability.supported === null }]">
        {{ capabilityLabel }}
      </span>
    </div>

    <dl class="contract-grid">
      <div><dt>Opening entrance</dt><dd>{{ openingPolicy }}</dd></div>
      <div><dt>Visual state</dt><dd>{{ projection.visualState }}</dd></div>
      <div><dt>Motion owner</dt><dd>{{ projection.physicalMotionOwner || 'unknown' }}</dd></div>
      <div><dt>Fallback</dt><dd>{{ projection.degraded.fallback || 'none' }}</dd></div>
    </dl>

    <div v-if="projection.warnings.length" class="firmware-warning" role="alert" aria-live="assertive">
      <strong>Firmware-incompatible preview</strong>
      <span v-for="warning in projection.warnings" :key="warning">{{ warning }}</span>
    </div>

    <div class="stage-shell">
      <div class="stage" data-testid="esp-tft-stage">
        <template v-for="layer in projection.layers">
          <CinematicVideoLayer
            v-if="layer.visible && ['background', 'teachingObject', 'robotOverlay'].includes(layer.id) && layer.mediaType === 'video/mp4'"
            :key="layer.id === 'robotOverlay' ? `robotOverlay-video-${activeIndex}-${motionNonce}` : `${layer.id}-video`"
            :src="layer.src"
            :chroma-key="layer.chromaKey"
            :layer-class="['stage-layer', `layer-${layer.id}`, layer.id === 'robotOverlay' ? (playing ? entranceClass : motionClass) : '']"
            :position-style="layerStyle(layer)"
          />
          <img
            v-else-if="layer.visible && ['background', 'teachingObject', 'robotOverlay'].includes(layer.id)"
            :key="layer.id === 'robotOverlay' ? `robotOverlay-${activeIndex}-${motionNonce}` : layer.id"
            :class="['stage-layer', `layer-${layer.id}`, layer.id === 'robotOverlay' ? (playing ? entranceClass : motionClass) : '']"
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
    <p class="truth-note">The stage is the exact robot layer projection. Browser transitions only illustrate timing; the physical entrance is firmware-owned.</p>
    <ol v-if="projection.openingPhaseTrace.length" class="opening-phase-trace" aria-label="Renderer v2 opening phase geometry">
      <li v-for="sample in projection.openingPhaseTrace" :key="sample.boundary">
        <strong>{{ sample.boundary }}</strong>
        <span>{{ sample.phase }}</span>
        <code>{{ sample.bounds.x }},{{ sample.bounds.y }} · {{ sample.bounds.width }}×{{ sample.bounds.height }}</code>
        <em>{{ sample.contentVisible ? 'content visible' : 'content hidden' }}</em>
      </li>
    </ol>

    <div class="play-bar">
      <button type="button" class="play-btn" :aria-pressed="playing ? 'true' : 'false'" @click="togglePlay">
        {{ playing ? '❚❚ Pause' : '► Play lesson' }}
      </button>
      <span class="play-step">Step {{ projection.step.index + 1 }} / {{ projection.step.count }}<template v-if="projection.step.type"> · {{ projection.step.type }}</template></span>
      <span v-if="projection.presentMotion" class="play-tag">motion: {{ projection.presentMotion }}</span>
      <span v-if="projection.entrance" class="play-tag">entrance: {{ projection.entrance }}</span>
      <span class="play-note">preview tempo (compressed)</span>
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

    <div class="state-controls" aria-label="Renderer v2 visual states">
      <span>Runtime visual state</span>
      <button
        v-for="state in visualStates"
        :key="state"
        type="button"
        data-testid="visual-state-control"
        :data-state="state"
        :class="{ selected: projection.visualState === state }"
        @click="selectVisualState(state)"
      >{{ stateLabels[state] }}</button>
    </div>

    <div class="degraded-controls" aria-label="Deterministic degraded fallbacks">
      <span>Inspect degraded fallback</span>
      <button type="button" :class="{ selected: !degradedReason }" @click="selectDegradedReason(null)">Normal</button>
      <button
        v-for="reason in degradedReasons"
        :key="reason"
        type="button"
        data-testid="degraded-reason-control"
        :data-reason="reason"
        :class="{ selected: degradedReason === reason }"
        @click="selectDegradedReason(reason)"
      >{{ reason }}</button>
    </div>

    <ol class="motion-timeline" aria-label="Robot command timeline">
      <li v-for="item in projection.timeline" :key="`${item.atMs}-${item.label}`">
        <time>{{ item.atMs }}ms</time><span>{{ item.label }}</span>
      </li>
    </ol>
  </section>
</template>

<script>
import {
  projectEspTftPreview,
  RESPONSE_PATHS,
  VISUAL_STATES,
  DEGRADED_REASONS
} from './robot-preview-projection';
import CinematicVideoLayer from './CinematicVideoLayer.vue';

export default {
  name: 'RobotEspTftProjectionPreview',
  components: { CinematicVideoLayer },
  props: {
    manifest: { type: Object, required: true },
    rendererMetadata: { type: Object, default: null },
    stepIndex: { type: Number, default: 0 },
    initialPath: { type: String, default: 'correct' }
  },
  data() {
    return {
      selectedPath: RESPONSE_PATHS.includes(this.initialPath) ? this.initialPath : 'correct',
      degradedReason: null,
      showSafeZones: false,
      motionNonce: 0,
      // Animated "play the lesson" preview. Steps the robot through every manifest
      // step, applying each step's entrance + present motion so the author sees the
      // lesson's effect (using the exact espTft assets the device renders).
      playing: false,
      playStep: 0,
      playTimer: null,
      previewStepMs: 1700,
      responsePaths: RESPONSE_PATHS,
      visualStates: VISUAL_STATES,
      degradedReasons: DEGRADED_REASONS,
      stateLabels: {
        teach: 'Teach', listen: 'Listen', thinking: 'Thinking', correct: 'Correct', nearMiss: 'Near miss',
        incorrect: 'Incorrect', retry: 'Retry', celebrate: 'Celebrate', completion: 'Completion'
      },
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
    activeIndex() {
      return this.playing ? this.playStep : this.stepIndex;
    },
    projection() {
      return projectEspTftPreview(this.manifest, this.activeIndex, this.selectedPath, this.degradedReason, this.rendererMetadata);
    },
    capabilityLabel() {
      if (this.projection.capability.supported === true) return 'Renderer v2 supported';
      if (this.projection.capability.supported === false) return 'Renderer v2 unsupported';
      return 'Renderer capability not reported';
    },
    openingPolicy() {
      const opening = this.projection.openingEntrance;
      if (!opening || !Object.keys(opening).length) return 'not declared';
      return `${opening.policy || 'policy unavailable'} · ${opening.preset || 'preset unavailable'}`;
    },
    word() {
      const pill = this.projection.layers.find((layer) => layer.id === 'wordPill');
      return pill ? pill.text : '';
    },
    motionClass() {
      // While playing, the robot performs the step's own "present" motion; otherwise
      // it reflects the response path the author is inspecting.
      const preset = this.playing && this.projection.presentMotion
        ? this.projection.presentMotion
        : (() => {
          if (this.projection.motionPreset) return this.projection.motionPreset;
          const command = this.projection.timeline.find((item) => /^(?:Server motion|Slave command):/.test(item.label));
          return command ? command.label.replace(/^(?:Server motion|Slave command):\s*/, '') : 'neutral';
        })();
      if (/nod|celebrate|encourage/i.test(preset)) return `motion-nod motion-${this.motionNonce % 2}`;
      if (/shake|tryagain/i.test(preset)) return `motion-shake motion-${this.motionNonce % 2}`;
      if (/lean|tilt|teach|present|listen|thinking/i.test(preset)) return `motion-tilt motion-${this.motionNonce % 2}`;
      return `motion-breathe motion-${this.motionNonce % 2}`;
    },
    entranceClass() {
      // During playback: a step with a scripted entrance (e.g. flyIn) plays that
      // arrival; a step without one performs its present motion instead, so every
      // step animates the way the lesson intends.
      if (!this.playing) return '';
      const entrance = String(this.projection.entrance || '').toLowerCase();
      if (entrance && entrance !== 'none') {
        const kind = /fly/.test(entrance) ? 'fly'
          : /walk/.test(entrance) ? 'walk'
            : /slide/.test(entrance) ? 'slide'
              : /pop|bounce/.test(entrance) ? 'pop'
                : 'fade';
        return `entrance-${kind}`;
      }
      return this.motionClass;
    }
  },
  watch: {
    initialPath(path) {
      if (RESPONSE_PATHS.includes(path)) this.selectPath(path);
    }
  },
  beforeDestroy() {
    this.clearPlayTimer();
  },
  methods: {
    clearPlayTimer() {
      if (this.playTimer) { clearTimeout(this.playTimer); this.playTimer = null; }
    },
    togglePlay() {
      if (this.playing) { this.stopPlay(); } else { this.startPlay(); }
    },
    startPlay() {
      const count = this.projection.step.count;
      if (count <= 0) return;
      this.playing = true;
      this.selectedPath = 'correct';
      this.playStep = 0;
      this.motionNonce += 1;
      this.scheduleAdvance();
    },
    stopPlay() {
      this.clearPlayTimer();
      this.playing = false;
    },
    scheduleAdvance() {
      this.clearPlayTimer();
      this.playTimer = setTimeout(() => {
        const count = this.projection.step.count || 1;
        if (this.playStep >= count - 1) {
          this.stopPlay();
          return;
        }
        this.playStep += 1;
        this.motionNonce += 1;
        if (this.playing) this.scheduleAdvance();
      }, this.previewStepMs);
    },
    layerStyle(layer) {
      const { x, y, width, height, fit } = layer.bounds;
      return {
        left: `${x}px`, top: `${y}px`, width: `${width}px`, height: `${height}px`,
        zIndex: layer.z, objectFit: fit || undefined
      };
    },
    selectPath(path) {
      if (this.playing) this.stopPlay();
      this.selectedPath = path;
      this.motionNonce += 1;
      this.$emit('path-change', { path, projection: projectEspTftPreview(this.manifest, this.activeIndex, path, this.degradedReason, this.rendererMetadata) });
    },
    selectVisualState(state) {
      if (!VISUAL_STATES.includes(state)) return;
      this.selectPath(state);
    },
    selectDegradedReason(reason) {
      if (reason !== null && !DEGRADED_REASONS.includes(reason)) return;
      this.degradedReason = reason;
      this.motionNonce += 1;
    }
  }
};
</script>

<style scoped>
.robot-preview { --ink: #17211b; --cream: #fff8e7; --lime: #b9ec45; color: var(--ink); }
.contract-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; margin-bottom:12px; }
.contract-head > div { display:grid; gap:3px; }
.contract-kicker { color:#607064; font-size:11px; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
.contract-head strong { font-size:22px; }
.contract-head code { color:#445349; font-size:12px; overflow-wrap:anywhere; }
.capability-badge { padding:6px 10px; border:1px solid #a42c20; border-radius:999px; background:#fff0ea; color:#78140d; font-size:12px; font-weight:800; white-space:nowrap; }
.capability-badge.supported { border-color:#648c22; background:#edf7d8; color:#34520d; }
.capability-badge.unknown { border-color:#8d978f; background:#f1f3f1; color:#536058; }
.contract-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; margin:0 0 12px; }
.contract-grid div { min-width:0; padding:9px 10px; border:1px solid #d7e1d4; border-radius:10px; background:#f7faef; }
.contract-grid dt { color:#6c796f; font-size:11px; text-transform:uppercase; }
.contract-grid dd { margin:3px 0 0; font-size:13px; font-weight:800; overflow-wrap:anywhere; }
.firmware-warning { display: grid; gap: 3px; margin-bottom: 12px; padding: 12px 14px; border: 2px solid #9c2218; border-radius: 10px; background: #fff0ea; color: #78140d; }
.stage-shell { width: 100%; overflow-x: auto; padding: 14px; box-sizing: border-box; border-radius: 18px; background: repeating-linear-gradient(135deg, #18231d, #18231d 10px, #202f26 10px, #202f26 20px); }
.stage { position: relative; width: 480px; height: 320px; margin: 0 auto; overflow: hidden; background: #dce8c2; box-shadow: 0 12px 30px rgba(0, 0, 0, .35); font-family: "Trebuchet MS", sans-serif; }
.truth-note { margin:7px 2px 0; color:#68766c; font-size:12px; }
.opening-phase-trace { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:6px; margin:10px 0 0; padding:0; list-style:none; }
.opening-phase-trace li { display:grid; gap:2px; padding:7px 9px; border:1px solid #cbd8cf; border-radius:8px; background:#f5f8f4; font-size:11px; }
.opening-phase-trace strong { color:#18382c; }
.opening-phase-trace span,.opening-phase-trace em { color:#617269; font-style:normal; }
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
.play-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-top: 12px; }
.play-btn { padding: 8px 16px; border: 2px solid #16251c; border-radius: 999px; background: var(--lime); color: #16251c; cursor: pointer; font: inherit; font-weight: 800; box-shadow: 0 3px 0 #16251c; }
.play-btn:active { transform: translateY(2px); box-shadow: 0 1px 0 #16251c; }
.play-step { font-weight: 700; color: #26342b; font-variant-numeric: tabular-nums; }
.play-tag { padding: 3px 9px; border-radius: 999px; background: #eef4e2; border: 1px solid #cbd9bb; font-size: 12px; color: #3a4a3c; }
.play-note { margin-left: auto; font-size: 12px; color: #7c8a7f; font-style: italic; }
.preview-toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 12px; }
.preview-toolbar button { padding: 7px 11px; border: 1px solid #9ba89f; border-radius: 999px; background: white; color: #26342b; cursor: pointer; font: inherit; }
.preview-toolbar button.selected { border-color: #17211b; background: var(--lime); box-shadow: inset 0 -2px rgba(0, 0, 0, .18); font-weight: 800; }
.preview-toolbar label { margin-left: auto; font-size: 13px; }
.state-controls, .degraded-controls { display:flex; flex-wrap:wrap; gap:7px; align-items:center; margin-top:12px; padding-top:12px; border-top:1px solid #dfe7dc; }
.state-controls > span, .degraded-controls > span { width:100%; color:#5e6e62; font-size:11px; font-weight:800; letter-spacing:.05em; text-transform:uppercase; }
.state-controls button, .degraded-controls button { padding:6px 9px; border:1px solid #aab5ad; border-radius:8px; background:#fff; color:#26342b; cursor:pointer; font:inherit; font-size:12px; }
.state-controls button.selected, .degraded-controls button.selected { border-color:#17211b; background:#17211b; color:#fff; }
.motion-timeline { display: grid; gap: 6px; margin: 12px 0 0; padding: 0; list-style: none; }
.motion-timeline li { display: grid; grid-template-columns: 62px 1fr; gap: 8px; padding: 7px 10px; border-left: 3px solid #648c22; background: #f1f6e8; }
.motion-timeline time { color: #607064; font-variant-numeric: tabular-nums; }
.motion-nod { animation: nod .55s ease-in-out; transform-origin: 50% 100%; }.motion-shake { animation: shake .55s ease-in-out; }.motion-tilt { animation: tilt .7s ease-in-out; transform-origin: 50% 100%; }.motion-breathe { animation: breathe 1.2s ease-in-out; }
.motion-1 { animation-delay: .001s; }
@keyframes nod { 30% { transform: rotate(4deg) translateY(5px); } 65% { transform: rotate(-2deg); } }
@keyframes shake { 25% { transform: translateX(-8px); } 70% { transform: translateX(8px); } }
@keyframes tilt { 45% { transform: rotate(-7deg); } }
@keyframes breathe { 50% { transform: translateY(-3px) scale(1.015); } }
/* Step entrance transitions played while the lesson auto-advances. */
.entrance-fly { animation: entranceFly .6s cubic-bezier(.2,.8,.2,1); }
.entrance-walk { animation: entranceWalk .6s ease-out; }
.entrance-slide { animation: entranceSlide .5s ease-out; }
.entrance-pop { animation: entrancePop .5s cubic-bezier(.2,1.4,.4,1); }
.entrance-fade { animation: entranceFade .45s ease-out; }
@keyframes entranceFly { 0% { transform: translateY(-120px) scale(.6); opacity: 0; } 70% { transform: translateY(6px) scale(1.02); opacity: 1; } 100% { transform: translateY(0) scale(1); } }
@keyframes entranceWalk { 0% { transform: translateX(-90px); opacity: 0; } 100% { transform: translateX(0); opacity: 1; } }
@keyframes entranceSlide { 0% { transform: translateX(60px); opacity: 0; } 100% { transform: translateX(0); opacity: 1; } }
@keyframes entrancePop { 0% { transform: scale(.4); opacity: 0; } 100% { transform: scale(1); opacity: 1; } }
@keyframes entranceFade { 0% { opacity: 0; } 100% { opacity: 1; } }
@media (max-width: 720px) { .contract-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width: 560px) { .contract-head { flex-direction:column; }.contract-grid { grid-template-columns:1fr; }.stage-shell { padding: 8px; }.stage { transform-origin: top left; } .preview-toolbar label { width: 100%; margin-left: 0; } }
@media (prefers-reduced-motion: reduce) { .layer-robotOverlay { animation: none; } }
</style>
