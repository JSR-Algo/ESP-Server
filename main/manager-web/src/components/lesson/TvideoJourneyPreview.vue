<template>
  <section class="journey-preview">
    <header><div><span>EXACT 480 × 320</span><strong>{{ phase.name }}</strong></div><div><button @click="showArrivedFrame">Arrived frame</button><button @click="replay">Replay</button><button @click="paused = !paused">{{ paused ? 'Resume' : 'Pause' }}</button></div></header>
    <div v-if="contractValid" class="tft" :style="{ backgroundImage: backgroundSrc ? `url(${backgroundSrc})` : '' }">
      <div v-for="(zone,index) in layout.safeZones" :key="index" class="safe-zone" :style="rect(zone)">SAFE</div>
      <div class="walk-corridor" :style="rect(layout.walkCorridor)">WALK CORRIDOR</div>
      <img v-if="robotSrc" class="robot" :src="robotSrc" :style="robotStyle" alt="" />
      <img v-if="contentVisible && teachingObjectSrc" class="teaching-object teaching-object--image" :src="teachingObjectSrc" :style="rect(layout.teachingObject)" alt="" />
      <div v-else-if="contentVisible" class="teaching-object" :style="rect(layout.teachingObject)">OBJECT</div>
      <div v-if="contentVisible" class="word-pill" :style="rect(layout.wordPill)">{{ word }}</div>
      <div v-if="contentVisible" class="prompt" :style="rect(layout.prompt)">{{ prompt }}</div>
      <div v-if="contentVisible" class="progress" :style="rect(layout.progress)">1 / 5</div>
      <div class="status">{{ elapsedMs }}ms · {{ degradedReason || 'full motion' }}</div>
    </div>
    <div v-else class="invalid-contract">Preview blocked: unsupported immutable template contract.</div>
    <div class="simulations">
      <label><input v-model="simulation" type="radio" value="none"> Normal</label>
      <label><input v-model="simulation" type="radio" value="missingAtlas"> missingAtlas</label>
      <label><input v-model="simulation" type="radio" value="missingOverlay"> missingOverlay</label>
      <label><input v-model="simulation" type="radio" value="phaseTimeout"> phaseTimeout</label>
      <label><input v-model="simulation" type="radio" value="reducedMotion"> reducedMotion</label>
    </div>
    <div class="phase-selector">
      <button v-for="(phaseDef, index) in phaseDefs" :key="phaseDef.name" :class="{ active: index === phaseIndex }" @click="selectPhase(index)">{{ phaseDef.name }}</button>
    </div>
  </section>
</template>
<script>
import { effectivePreviewPhaseName, LAYOUT_PRESETS, PHASES, isTeachingContentVisible, phaseRobotRect, validateProjection } from './tvideo-layout-presets';
export default {
  name: 'TvideoJourneyPreview',
  props: { projection: { type: Object, required: true }, scene: { type: Object, default: () => ({}) }, prompt: { type: String, default: '' }, word: { type: String, default: '' } },
  data() { return { phaseIndex: 0, elapsedMs: 0, paused: false, simulation: 'none', timer: null }; },
  computed: {
    contractValid() { return validateProjection(this.projection); },
    layout() { return LAYOUT_PRESETS[this.projection.layoutPreset]; },
    phaseDefs() { return this.contractValid ? this.projection.phases : PHASES; },
    phase() {
      const selected = this.phaseDefs[this.phaseIndex] || this.phaseDefs[this.phaseDefs.length - 1];
      const effectiveName = effectivePreviewPhaseName(selected.name, this.fallback);
      return this.phaseDefs.find((phase) => phase.name === effectiveName) || selected;
    },
    fallback() { return this.simulation !== 'none'; },
    contentVisible() { return isTeachingContentVisible(this.phase.name, this.projection.revealPhase, this.fallback); },
    degradedReason() { return this.fallback ? this.simulation : ''; },
    backgroundSrc() { const bg = this.scene.backgroundScene || {}; return (bg.poster || bg).src || ''; },
    teachingObjectSrc() { const object = this.scene.teachingObject || {}; return (object.asset || object).src || ''; },
    robotSrc() { if (this.simulation === 'missingOverlay') return ''; const overlay = this.scene.robotOverlay || {}; return (overlay.asset || overlay).src || ''; },
    robotStyle() { if (!this.layout) return {}; const target = phaseRobotRect(this.layout, this.phase.name, this.elapsedMs, this.phase.durationMs); return { ...this.rect({ width:96,height:84,...target }), opacity:this.phase.name === 'hidden' ? 0 : 1, transitionDuration:this.phase.name === 'walkToward' ? '100ms' : `${Math.min(this.phase.durationMs,400)}ms` }; },
  },
  watch: { simulation() { this.replay(); } },
  mounted() { this.start(); }, beforeDestroy() { clearInterval(this.timer); },
  methods: {
    rect(value) { return { left:`${value.left}px`,top:`${value.top}px`,width:`${value.width || 1}px`,height:`${value.height || 1}px` }; },
    replay() { const arriveIndex = this.phaseDefs.findIndex((phase) => phase.name === 'arriveNear'); this.phaseIndex = this.fallback ? arriveIndex : 0; this.elapsedMs = 0; this.paused = this.fallback; },
    selectPhase(index) { this.phaseIndex = index; this.elapsedMs = 0; this.paused = true; },
    showArrivedFrame() { const index = this.phaseDefs.findIndex((phase) => phase.name === 'arriveNear'); this.selectPhase(index < 0 ? 0 : index); },
    start() { this.timer = setInterval(() => { if (this.paused || !this.contractValid) return; this.elapsedMs += 100; if (this.elapsedMs >= this.phase.durationMs && this.phaseIndex < this.phaseDefs.length - 1) { this.elapsedMs = 0; this.phaseIndex += 1; } }, 100); },
  },
};
</script>
<style scoped>
.journey-preview{background:#17221f;border-radius:18px;color:#fff4d8;padding:14px}.journey-preview header{align-items:center;display:flex;justify-content:space-between;margin-bottom:10px}.journey-preview header div:first-child{display:grid}.journey-preview header span{color:#9eb8af;font-size:10px;letter-spacing:.12em}.journey-preview button{background:#f3ca52;border:0;border-radius:20px;color:#17221f;font-weight:800;margin-left:6px;padding:7px 12px}.tft{background-color:#6c8d77;background-position:center;background-size:cover;border:8px solid #080d0c;height:320px;overflow:hidden;position:relative;width:480px}.safe-zone,.walk-corridor{border:1px dashed rgba(255,245,180,.8);box-sizing:border-box;color:#fff6c6;font-size:8px;position:absolute}.walk-corridor{background:rgba(243,202,82,.12)}.robot,.teaching-object,.word-pill,.prompt,.progress{box-sizing:border-box;position:absolute}.robot{object-fit:contain;transition:all linear}.teaching-object{align-items:center;background:rgba(255,255,255,.8);color:#17312d;display:flex;justify-content:center}.teaching-object--image{object-fit:contain}.word-pill{background:#fff4cf;border-radius:20px;color:#17312d;font-weight:900;padding:10px;text-align:center}.prompt{background:rgba(10,18,16,.78);border-radius:10px;padding:9px;text-align:center}.progress{font-size:10px}.status{background:#080d0c;bottom:0;font-size:9px;left:0;padding:4px 7px;position:absolute}.simulations,.phase-selector{display:flex;flex-wrap:wrap;font-size:11px;gap:8px;margin-top:10px}.phase-selector button{background:#31433e;color:#dbe7e2;margin:0;padding:5px 8px}.phase-selector button.active{background:#f3ca52;color:#17221f}.invalid-contract{background:#57271f;border-radius:12px;color:#ffe7d7;padding:22px}@media(max-width:620px){.tft{height:auto;max-width:100%;aspect-ratio:3/2;transform-origin:top left}.journey-preview{overflow:auto}}
</style>
