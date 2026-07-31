<template>
  <section class="robot-flattened" aria-labelledby="tvideo-flattened-title">
    <div class="robot-flattened__head">
      <div><span class="eyebrow">PREVIEW · NOT PUBLISH AUTHORITY</span><h4 id="tvideo-flattened-title">Robot Flattened</h4></div>
      <div class="robot-flattened__controls">
        <el-select v-model="cueId" size="mini" aria-label="Preview cue">
          <el-option v-for="cue in cues" :key="cue.cueId" :label="`${cue.cueId} · ${cue.effect}`" :value="cue.cueId" />
        </el-select>
        <el-button size="mini" @click="toggle">{{ playing ? $t('lesson.tvideoJourney.pause') : $t('lesson.tvideoJourney.play') }}</el-button>
        <el-button size="mini" @click="replay">{{ $t('lesson.tvideoJourney.replay') }}</el-button>
      </div>
    </div>
    <div class="robot-flattened__viewport">
      <canvas ref="canvas" width="480" height="320" aria-label="TVideo Journey flattened 480 by 320 preview canvas" />
      <video v-if="backgroundUrl" ref="background" class="robot-flattened__media" :src="backgroundUrl" muted playsinline loop preload="auto" @loadeddata="draw" />
      <video v-if="robotUrl" ref="robot" class="robot-flattened__media" :src="robotUrl" muted playsinline loop preload="auto" @loadeddata="draw" />
      <img v-if="objectUrl" ref="object" class="robot-flattened__media" :src="objectUrl" alt="" @load="draw" />
    </div>
    <div class="robot-flattened__identity">
      <span class="mono">{{ preset.presetId }}@{{ preset.presetVersion }}</span>
      <span class="mono">build {{ shortHash(preset.rendererBuildSha256) }}</span>
      <span>{{ clockMs }} ms · 10 FPS</span>
    </div>
    <p class="robot-flattened__notice">{{ $t('lesson.tvideoJourney.previewOnly') }}</p>
  </section>
</template>

<script>
import { deterministicPreviewState, quantizeClockMs, requiredCueIds } from './tvideo-journey';

const EFFECTS = ['opening', 'greet', 'teach', 'listen', 'thinking', 'correct', 'retry-level-1', 'retry-level-2', 'retry-level-3', 'celebrate', 'word-transition'];
const CONFETTI = ['#ffd166', '#ff8a6b', '#79d8bd', '#b39ddb', '#5bb8e6', '#ffffff'];

export default {
  name: 'TVideoRobotPreview',
  props: {
    journey: { type: Object, required: true }, preset: { type: Object, required: true },
    mediaUrl: { type: Function, required: true }, selectedStepIndex: { type: Number, default: 0 },
  },
  data: () => ({ cueId: '', playing: false, clockMs: 0, timer: null }),
  computed: {
    cues() {
      return requiredCueIds(this.journey.steps).map((cueId) => ({ cueId, effect: EFFECTS.find((effect) => cueId.endsWith(effect)) || (cueId.includes('word-transition') ? 'word-transition' : 'opening') }));
    },
    selectedCue() { return this.cues.find((cue) => cue.cueId === this.cueId) || this.cues[0] || { cueId: '', effect: 'teach' }; },
    step() { return this.journey.steps[this.selectedStepIndex] || this.journey.steps[0] || {}; },
    backgroundUrl() { return this.mediaUrl(this.journey.assets.background.assetVersionId); },
    objectUrl() { return this.mediaUrl(this.step.teachingObject && this.step.teachingObject.assetVersionId); },
    robotRole() {
      if (this.selectedCue.effect === 'opening') return 'flight';
      if (this.selectedCue.effect === 'celebrate' || this.selectedCue.effect === 'correct') return 'celebration';
      if (this.selectedCue.effect === 'word-transition') return 'walking';
      return 'greeting-teaching';
    },
    robotUrl() { const clip = (this.journey.assets.robotClips || []).find((row) => row.role === this.robotRole); return this.mediaUrl(clip && clip.assetVersionId); },
  },
  watch: {
    cues: { immediate: true, handler(value) { if (!value.some((cue) => cue.cueId === this.cueId)) this.cueId = value[0] ? value[0].cueId : ''; } },
    cueId() { this.replay(); }, journey: { deep: true, handler() { this.$nextTick(this.draw); } },
  },
  beforeDestroy() { this.stopTimer(); },
  methods: {
    shortHash(value) { return value ? `${value.slice(0, 8)}…${value.slice(-6)}` : 'unavailable'; },
    toggle() { this.playing = !this.playing; if (this.playing) this.startTimer(); else this.stopTimer(); },
    replay() { this.clockMs = 0; this.draw(); if (this.playing) this.startTimer(); },
    startTimer() { this.stopTimer(); this.timer = setInterval(() => { this.clockMs = quantizeClockMs(this.clockMs + 100); this.draw(); }, 100); },
    stopTimer() { if (this.timer) clearInterval(this.timer); this.timer = null; },
    drawMedia(ctx, media, x, y, width, height) { if (!media || !(media.complete || media.readyState >= 2)) return false; try { ctx.drawImage(media, x, y, width, height); return true; } catch (error) { return false; } },
    draw() {
      const canvas = this.$refs.canvas; if (!canvas) return;
      const ctx = canvas.getContext('2d'); const path = this.journey.scenePath; const effect = this.selectedCue.effect;
      ctx.clearRect(0, 0, 480, 320);
      if (!this.drawMedia(ctx, this.$refs.background, 0, 0, 480, 320)) { const gradient = ctx.createLinearGradient(0, 0, 480, 320); gradient.addColorStop(0, '#9dd9cf'); gradient.addColorStop(1, '#eac875'); ctx.fillStyle = gradient; ctx.fillRect(0, 0, 480, 320); }
      ctx.fillStyle = 'rgba(25,50,42,.11)'; const safe = path.safeZone; ctx.fillRect(safe.x * 480, safe.y * 320, safe.width * 480, safe.height * 320);
      const anchor = path.teachingAnchor; const robotWidth = 108; const robotHeight = 150;
      if (!this.drawMedia(ctx, this.$refs.robot, anchor.x * 480 - robotWidth / 2, anchor.y * 320 - robotHeight, robotWidth, robotHeight)) { ctx.fillStyle = '#fff4d7'; ctx.beginPath(); ctx.arc(anchor.x * 480, anchor.y * 320 - 56, 37, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#31524a'; ctx.font = 'bold 18px sans-serif'; ctx.fillText('TB', anchor.x * 480 - 14, anchor.y * 320 - 50); }
      const object = path.objectAnchor; if (!this.drawMedia(ctx, this.$refs.object, object.x * 480 - 38, object.y * 320 - 42, 76, 76)) { ctx.fillStyle = '#d95f43'; ctx.fillRect(object.x * 480 - 28, object.y * 320 - 28, 56, 56); }
      ctx.fillStyle = 'rgba(255,255,255,.92)'; ctx.fillRect(22, 22, 155, 80); ctx.fillStyle = '#243c35'; ctx.font = '700 12px sans-serif'; ctx.fillText(`${this.step.progress ? this.step.progress.index : 1} / 2`, 34, 43); ctx.font = '700 27px sans-serif'; ctx.fillText(String(this.step.targetWord || '').toUpperCase(), 34, 74); ctx.font = '12px sans-serif'; ctx.fillText(effect, 34, 94);
      if (effect === 'celebrate' || effect === 'correct') { const state = deterministicPreviewState({ clockMs: this.clockMs, cueId: this.cueId, seed: this.preset.confettiSeed, pieces: this.preset.confettiPieces || 64 }); state.confetti.forEach((piece) => { ctx.save(); ctx.translate(piece.x * 480, piece.y * 320); ctx.rotate(piece.rotation * Math.PI / 180); ctx.fillStyle = CONFETTI[piece.colorIndex]; ctx.fillRect(-4, -7, 8, 14); ctx.restore(); }); }
      ctx.fillStyle = 'rgba(17,34,29,.72)'; ctx.fillRect(278, 287, 192, 22); ctx.fillStyle = '#fff'; ctx.font = '11px sans-serif'; ctx.fillText('ADMIN PREVIEW · NOT PUBLISH AUTHORITY', 287, 302);
    },
  },
};
</script>

<style scoped>
.robot-flattened{background:#f3eee3;border:1px solid #d9cdb7;border-radius:16px;padding:14px}.robot-flattened__head{align-items:flex-end;display:flex;gap:12px;justify-content:space-between}.robot-flattened__head h4{margin:3px 0}.robot-flattened__controls{display:flex;gap:7px}.robot-flattened__controls .el-select{width:235px}.robot-flattened__viewport{aspect-ratio:3/2;background:#243c35;border-radius:12px;margin:12px auto 0;max-width:480px;overflow:hidden;position:relative;width:100%}.robot-flattened__viewport canvas{display:block;height:100%;width:100%}.robot-flattened__media{height:1px;left:-9999px;position:absolute;width:1px}.robot-flattened__identity{color:#66736f;display:flex;font-size:11px;gap:12px;justify-content:center;margin-top:8px}.robot-flattened__notice{color:#8c3f31;font-size:12px;font-weight:700;margin:9px 0 0;text-align:center}@media(max-width:680px){.robot-flattened__head{align-items:stretch;flex-direction:column}.robot-flattened__controls{flex-wrap:wrap}.robot-flattened__controls .el-select{width:100%}.robot-flattened__identity{align-items:center;flex-direction:column;gap:3px}}@media(prefers-reduced-motion:reduce){.robot-flattened *{transition:none!important}}
</style>
