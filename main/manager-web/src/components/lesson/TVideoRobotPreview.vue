<template>
  <section class="robot-flattened" aria-labelledby="tvideo-flattened-title">
    <div class="robot-flattened__head">
      <div><span class="eyebrow">{{ $t('lesson.tvideoJourney.previewAuthorityEyebrow') }}</span><h4 id="tvideo-flattened-title">{{ $t('lesson.tvideoJourney.tab.flattened') }}</h4></div>
      <div class="robot-flattened__controls">
        <el-select v-model="cueId" size="mini" :aria-label="$t('lesson.tvideoJourney.previewCueLabel')">
          <el-option v-for="cue in cues" :key="cue.cueId" :label="`${cue.cueId} · ${cue.effect}`" :value="cue.cueId" />
        </el-select>
        <el-button size="mini" @click="toggle">{{ playing ? $t('lesson.tvideoJourney.pause') : $t('lesson.tvideoJourney.play') }}</el-button>
        <el-button size="mini" @click="replay">{{ $t('lesson.tvideoJourney.replay') }}</el-button>
      </div>
    </div>
    <div class="robot-flattened__viewport">
      <canvas ref="canvas" width="480" height="320" :aria-label="$t('lesson.tvideoJourney.canvasLabel')" />
      <video v-if="backgroundUrl" ref="background" class="robot-flattened__media" :src="backgroundUrl" muted playsinline loop preload="auto" @loadeddata="mediaReady" @seeked="draw" />
      <video v-if="robotUrl" ref="robot" class="robot-flattened__media" :src="robotUrl" muted playsinline loop preload="auto" @loadeddata="mediaReady" @seeked="draw" />
      <img v-if="objectUrl" ref="object" class="robot-flattened__media" :src="objectUrl" alt="" @load="draw" />
    </div>
    <div class="robot-flattened__identity">
      <span class="mono">{{ preset.presetId }}@{{ preset.presetVersion }}</span>
      <span class="mono">{{ $t('lesson.tvideoJourney.buildIdentity', { hash: shortHash(preset.rendererBuildSha256) }) }}</span>
      <span>{{ $t('lesson.tvideoJourney.clockSummary', { clock: clockMs, fps: 10 }) }}</span>
    </div>
    <p class="robot-flattened__notice">{{ $t('lesson.tvideoJourney.previewOnly') }}</p>
  </section>
</template>

<script>
import { deterministicPreviewState, quantizeClockMs, requiredCueIds } from './tvideo-journey';

const EFFECTS = ['opening', 'greet', 'teach', 'listen', 'thinking', 'correct', 'retry-level-1', 'retry-level-2', 'retry-level-3', 'celebrate', 'word-transition'];
const CONFETTI = ['#ffd166', '#ff8a6b', '#79d8bd', '#b39ddb', '#5bb8e6', '#ffffff'];
const PUFF = [
  { x: -34, y: -26, color: '#ffd166' }, { x: 28, y: -34, color: '#79d8bd' },
  { x: -16, y: -46, color: '#ff8a6b' }, { x: 40, y: -14, color: '#ffffff' },
  { x: 6, y: -54, color: '#b39ddb' },
];
const RETRY_VISUALS = { 1: { amplitudePx: 2, pulses: 1 }, 2: { amplitudePx: 4, pulses: 2 }, 3: { amplitudePx: 6, pulses: 3 } };

const clamp01 = (value) => Math.max(0, Math.min(1, value));
const lerp = (start, end, progress) => start + ((end - start) * progress);
const easeOut = (progress) => 1 - ((1 - clamp01(progress)) ** 3);
const easeInOut = (progress) => {
  const value = clamp01(progress);
  return value < 0.5 ? 4 * value * value * value : 1 - ((-2 * value + 2) ** 3) / 2;
};
const interpolatePose = (start = {}, end = {}, progress = 0) => ({
  x: lerp(Number(start.x) || 0, Number(end.x) || 0, progress),
  y: lerp(Number(start.y) || 0, Number(end.y) || 0, progress),
  scale: lerp(Number(start.scale) || 1, Number(end.scale) || 1, progress),
});

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
    cueStepIndex() {
      const transition = /^(.+)-to-(.+)-word-transition$/.exec(this.selectedCue.cueId);
      const key = transition ? transition[2] : (this.journey.steps || []).find((row) => this.selectedCue.cueId.startsWith(`${row.stepKey}-`))?.stepKey;
      const index = (this.journey.steps || []).findIndex((row) => row.stepKey === key);
      return index >= 0 ? index : Math.max(0, Math.min(this.selectedStepIndex, (this.journey.steps || []).length - 1));
    },
    step() { return this.journey.steps[this.cueStepIndex] || this.journey.steps[0] || {}; },
    previousStep() { return this.journey.steps[this.cueStepIndex - 1] || null; },
    backgroundUrl() { return this.mediaUrl(this.journey.assets.background.assetVersionId); },
    objectUrl() {
      const step = this.selectedCue.effect === 'word-transition' && this.clockMs < 400 && this.previousStep
        ? this.previousStep : this.step;
      return this.mediaUrl(step.teachingObject && step.teachingObject.assetVersionId);
    },
    previewFrameState() {
      const effect = this.selectedCue.effect;
      const clockMs = quantizeClockMs(this.clockMs);
      const path = this.journey.scenePath || {};
      const flight = path.flightIngress || {};
      const landing = path.landing || flight.end || {};
      const walk = path.walk || {};
      const flightDuration = Number(flight.durationMs) || 3200;
      const landingDuration = Number(landing.durationMs) || 500;
      let phase = effect;
      let pose = { ...(path.teachingAnchor || {}), scale: 1 };
      let squashY = 1;
      let puffVisible = false;
      let shadowVisible = effect !== 'opening';
      if (effect === 'opening' && clockMs < flightDuration) {
        phase = 'opening-flight';
        const progress = easeOut(clockMs / flightDuration);
        pose = progress < 0.55
          ? interpolatePose(flight.start, flight.mid, progress / 0.55)
          : interpolatePose(flight.mid, flight.end, (progress - 0.55) / 0.45);
        shadowVisible = false;
      } else if (effect === 'opening' && clockMs < flightDuration + landingDuration) {
        phase = 'opening-landing';
        const progress = (clockMs - flightDuration) / landingDuration;
        pose = { ...landing, scale: Number(landing.scale) || Number(flight.end && flight.end.scale) || 0.35 };
        squashY = progress >= 0.2 && progress <= 0.7 ? 0.94 : 1;
        puffVisible = true;
        shadowVisible = true;
      } else if (effect === 'opening') {
        const elapsed = clockMs - flightDuration - landingDuration;
        const keyframes = walk.keyframes || [];
        const walkDuration = Number(keyframes[keyframes.length - 1] && keyframes[keyframes.length - 1].timeMs) || 5000;
        phase = elapsed >= walkDuration ? 'opening-arrived' : 'opening-walk';
        pose = phase === 'opening-arrived'
          ? { ...(path.teachingAnchor || {}), scale: 0.9 }
          : this.walkPoseAt(elapsed, keyframes, landing, path.teachingAnchor);
        shadowVisible = true;
      }
      const retryLevel = Number((/retry-level-(\d)/.exec(effect) || [])[1] || 0);
      const retry = (this.preset.motion && this.preset.motion.retries && this.preset.motion.retries[retryLevel]) || RETRY_VISUALS[retryLevel] || {};
      const wordProgress = effect === 'word-transition' ? clamp01(clockMs / 1000) : 0;
      const cardVariant = phase === 'opening-walk' || phase === 'opening-arrived' ? 'greet' : effect;
      const pulse = effect === 'correct' || effect === 'celebrate' ? 'joy'
        : retryLevel === 3 ? 'focused' : retryLevel === 2 ? 'supportive'
          : (retryLevel === 1 || effect === 'listen' || effect === 'thinking') ? 'gentle' : 'none';
      const count = Math.max(1, Number(this.step.progress && this.step.progress.count) || (this.journey.steps || []).length || 1);
      const index = Math.max(1, Number(this.step.progress && this.step.progress.index) || this.cueStepIndex + 1);
      const openingContentVisible = phase === 'opening-arrived';
      const reward = effect === 'correct' || effect === 'celebrate';
      const contentVisible = effect === 'opening' ? openingContentVisible : !reward;
      const outgoingStep = this.previousStep || this.step;
      return {
        clockMs, effect, phase,
        robot: { pose, squashY, pulse },
        shadow: { visible: shadowVisible, pulse: shadowVisible && phase !== 'opening-landing' },
        puff: { visible: puffVisible, progress: phase === 'opening-landing' ? clamp01((clockMs - flightDuration) / 800) : 0 },
        object: { visible: contentVisible },
        progressDots: Array.from({ length: count }, (_, dotIndex) => ({ active: dotIndex < index })),
        card: {
          variant: cardVariant, visible: contentVisible,
          listeningGlow: effect === 'listen', thinking: effect === 'thinking',
          correctChip: reward, retryLevel, pulse,
          pulsePx: Number(retry.amplitudePx) || 0, pulseCount: Number(retry.pulses) || 0,
        },
        confetti: { visible: reward },
        wordTransition: effect === 'word-transition' ? {
          outgoing: String(outgoingStep.targetWord || ''), incoming: String(this.step.targetWord || ''),
          progress: Number(wordProgress.toFixed(1)),
          displayWord: clockMs < 400 ? String(outgoingStep.targetWord || '') : String(this.step.targetWord || ''),
          displayStepKey: clockMs < 400 ? String(outgoingStep.stepKey || '') : String(this.step.stepKey || ''),
        } : null,
      };
    },
    robotRole() {
      if (this.previewFrameState.phase === 'opening-flight' || this.previewFrameState.phase === 'opening-landing') return 'flight';
      if (this.previewFrameState.phase === 'opening-walk') return 'walking';
      if (this.selectedCue.effect === 'celebrate' || this.selectedCue.effect === 'correct') return 'celebration';
      if (this.selectedCue.effect === 'word-transition') return 'greeting-teaching';
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
    shortHash(value) { return value ? `${value.slice(0, 8)}…${value.slice(-6)}` : this.$t('lesson.tvideoJourney.unavailable'); },
    walkPoseAt(elapsed, keyframes, landing, teachingAnchor) {
      const frames = keyframes.length ? keyframes : [
        { timeMs: 0, ...landing }, { timeMs: 5000, ...(teachingAnchor || {}), scale: 1 },
      ];
      if (elapsed <= frames[0].timeMs) return { ...frames[0] };
      for (let index = 1; index < frames.length; index += 1) {
        if (elapsed <= frames[index].timeMs) {
          const previous = frames[index - 1]; const next = frames[index];
          return interpolatePose(previous, next, easeInOut((elapsed - previous.timeMs) / Math.max(1, next.timeMs - previous.timeMs)));
        }
      }
      return { ...frames[frames.length - 1] };
    },
    toggle() { this.playing = !this.playing; if (this.playing) this.startTimer(); else this.stopTimer(); },
    replay() { this.clockMs = 0; this.syncMediaClock(); this.draw(); if (this.playing) this.startTimer(); },
    startTimer() { this.stopTimer(); this.timer = setInterval(() => { this.clockMs = quantizeClockMs(this.clockMs + 100); this.syncMediaClock(); this.draw(); }, 100); },
    stopTimer() { if (this.timer) clearInterval(this.timer); this.timer = null; },
    mediaReady(event) { if (event && event.target) event.target.pause(); this.syncMediaClock(); this.draw(); },
    syncMediaClock() {
      [this.$refs.background, this.$refs.robot].forEach((media) => {
        if (!media || !Number.isFinite(media.duration) || media.duration <= 0) return;
        try { media.currentTime = (this.clockMs / 1000) % media.duration; } catch (error) { /* media can still be loading */ }
      });
    },
    drawMedia(ctx, media, x, y, width, height) { if (!media || !(media.complete || media.readyState >= 2)) return false; try { ctx.drawImage(media, x, y, width, height); return true; } catch (error) { return false; } },
    roundedRect(ctx, x, y, width, height, radius) {
      const value = Math.min(radius, width / 2, height / 2);
      ctx.beginPath(); ctx.moveTo(x + value, y); ctx.arcTo(x + width, y, x + width, y + height, value); ctx.arcTo(x + width, y + height, x, y + height, value); ctx.arcTo(x, y + height, x, y, value); ctx.arcTo(x, y, x + width, y, value); ctx.closePath();
    },
    drawGroundShadow(ctx, state, x, y, scale) {
      if (!state.shadow.visible) return;
      const pulse = state.shadow.pulse ? 0.94 + (Math.sin(state.clockMs / 510) * 0.06) : 1;
      ctx.save(); ctx.translate(x, y + 3); ctx.scale(scale * pulse, scale); ctx.fillStyle = 'rgba(28,44,54,.28)'; ctx.beginPath(); ctx.ellipse(0, 0, 38, 8, 0, 0, Math.PI * 2); ctx.fill(); ctx.restore();
    },
    drawLandingPuff(ctx, state, x, y) {
      if (!state.puff.visible) return;
      const progress = easeOut(state.puff.progress);
      PUFF.forEach((piece) => { ctx.globalAlpha = Math.max(0, 1 - progress); ctx.fillStyle = piece.color; ctx.beginPath(); ctx.arc(x + piece.x * progress, y + piece.y * progress, 3 + 3 * progress, 0, Math.PI * 2); ctx.fill(); });
      ctx.globalAlpha = 1;
    },
    drawProgressDots(ctx, state) {
      state.progressDots.forEach((dot, index) => { ctx.fillStyle = dot.active ? '#ffd166' : 'rgba(255,255,255,.78)'; ctx.strokeStyle = dot.active ? '#d8a800' : 'rgba(14,34,48,.2)'; ctx.beginPath(); ctx.arc(26 + index * 15, 24, 4, 0, Math.PI * 2); ctx.fill(); ctx.stroke(); });
    },
    cardCopy(state) {
      const copy = this.step.teachingCopy || {};
      if (state.card.variant === 'greet' || state.card.variant === 'opening') return copy.intro || this.step.targetWord || '';
      if (state.card.variant === 'teach') return copy.explanation || copy.intro || this.step.targetWord || '';
      if (state.card.variant === 'listen') return copy.prompt || this.step.targetWord || '';
      if (state.card.variant === 'thinking') return (this.step.questionSeeds || [])[0] || copy.prompt || '';
      if (state.card.retryLevel) return copy.prompt || this.step.targetWord || '';
      if (state.wordTransition) return `${state.wordTransition.outgoing}  →  ${state.wordTransition.incoming}`;
      return this.step.targetWord || '';
    },
    drawStepCard(ctx, state) {
      if (!state.card.visible) return;
      const pulseAmount = state.card.pulse === 'focused' ? 4 : state.card.pulse === 'supportive' ? 3 : state.card.pulse === 'gentle' ? 2 : 0;
      const pulse = pulseAmount ? (1 + Math.sin(state.clockMs / 210) * pulseAmount / 100) : 1;
      const duration = Number(this.preset.effects && this.preset.effects[state.effect] && this.preset.effects[state.effect].durationMs) || 1;
      const retryOffset = state.card.pulseCount
        ? Math.sin((Math.PI * 2 * state.card.pulseCount * state.clockMs) / duration) * state.card.pulsePx : 0;
      ctx.save(); ctx.translate(374 + retryOffset, 257); ctx.scale(pulse, pulse); ctx.shadowColor = state.card.listeningGlow ? 'rgba(91,184,230,.72)' : 'rgba(73,60,40,.18)'; ctx.shadowBlur = state.card.listeningGlow ? 18 : 10; ctx.shadowOffsetY = 4;
      ctx.fillStyle = 'rgba(255,255,255,.95)'; ctx.strokeStyle = state.card.thinking ? '#b39ddb' : state.card.retryLevel ? '#79d8bd' : '#d9cdb7'; ctx.lineWidth = 1.5; this.roundedRect(ctx, -91, -48, 182, 88, 14); ctx.fill(); ctx.stroke(); ctx.shadowColor = 'transparent';
      ctx.fillStyle = state.card.thinking ? '#6f5b96' : '#b84f39'; ctx.font = '800 10px sans-serif'; ctx.fillText(`${String(this.step.stepKey || '').toUpperCase()} · ${state.card.variant}`, -76, -28);
      ctx.fillStyle = '#243c35'; ctx.font = '600 13px sans-serif'; const copy = this.cardCopy(state); ctx.fillText(copy.slice(0, 24), -76, -7); if (copy.length > 24) ctx.fillText(copy.slice(24, 48), -76, 11);
      if (state.card.correctChip) { ctx.fillStyle = '#79d8bd'; this.roundedRect(ctx, -76, 20, 72, 18, 9); ctx.fill(); ctx.fillStyle = '#173f34'; ctx.font = '800 10px sans-serif'; ctx.fillText('✓ CORRECT', -67, 33); }
      if (state.card.retryLevel) { ctx.fillStyle = '#31524a'; ctx.font = '800 10px sans-serif'; ctx.fillText(`LEVEL ${state.card.retryLevel}`, 30, 33); }
      ctx.restore();
    },
    draw() {
      const canvas = this.$refs.canvas; if (!canvas) return;
      const ctx = canvas.getContext('2d'); const path = this.journey.scenePath; const state = this.previewFrameState; const effect = state.effect;
      ctx.clearRect(0, 0, 480, 320);
      if (!this.drawMedia(ctx, this.$refs.background, 0, 0, 480, 320)) { const gradient = ctx.createLinearGradient(0, 0, 480, 320); gradient.addColorStop(0, '#9dd9cf'); gradient.addColorStop(1, '#eac875'); ctx.fillStyle = gradient; ctx.fillRect(0, 0, 480, 320); }
      const object = path.objectAnchor; const objectBob = Math.sin(state.clockMs / 410) * 4; const transition = state.wordTransition; const objectAlpha = !state.object.visible ? 0 : transition ? (state.clockMs <= 400 ? 1 - state.clockMs / 400 : clamp01((state.clockMs - 400) / 550)) : 1;
      ctx.save(); ctx.globalAlpha = clamp01(objectAlpha); ctx.shadowColor = 'rgba(58,58,74,.3)'; ctx.shadowBlur = 12; ctx.shadowOffsetY = 8; if (!this.drawMedia(ctx, this.$refs.object, object.x * 480 - 38, object.y * 320 - 42 + objectBob, 76, 76)) { ctx.fillStyle = '#d95f43'; ctx.fillRect(object.x * 480 - 28, object.y * 320 - 28 + objectBob, 56, 56); } ctx.restore();
      const pose = state.robot.pose; const robotX = clamp01(Number(pose.x) || 0) * 480; const robotY = clamp01(Number(pose.y) || 0) * 320; const robotScale = Math.max(0.1, Number(pose.scale) || 1); const robotWidth = 108 * robotScale; const robotHeight = 150 * robotScale;
      this.drawGroundShadow(ctx, state, robotX, robotY, robotScale);
      ctx.save(); ctx.translate(robotX, robotY); ctx.scale(1, state.robot.squashY); const bounce = state.robot.pulse === 'joy' ? Math.sin(state.clockMs / 105) * 6 : state.robot.pulse !== 'none' ? Math.sin(state.clockMs / 250) * 2 : 0;
      if (!this.drawMedia(ctx, this.$refs.robot, -robotWidth / 2, -robotHeight + bounce, robotWidth, robotHeight)) { ctx.fillStyle = '#fff4d7'; ctx.beginPath(); ctx.arc(0, -56 * robotScale + bounce, 37 * robotScale, 0, Math.PI * 2); ctx.fill(); ctx.fillStyle = '#31524a'; ctx.font = `bold ${Math.max(10, 18 * robotScale)}px sans-serif`; ctx.fillText('TB', -14 * robotScale, -50 * robotScale + bounce); } ctx.restore();
      this.drawLandingPuff(ctx, state, robotX, robotY);
      this.drawProgressDots(ctx, state);
      this.drawStepCard(ctx, state);
      if (state.confetti.visible) { const preview = deterministicPreviewState({ clockMs: this.clockMs, cueId: this.cueId, seed: this.preset.confettiSeed, pieces: this.preset.confettiPieces || 64 }); preview.confetti.forEach((piece) => { ctx.save(); ctx.translate(piece.x * 480, piece.y * 320); ctx.rotate(piece.rotation * Math.PI / 180); ctx.fillStyle = CONFETTI[piece.colorIndex]; ctx.fillRect(-4, -7, 8, 14); ctx.restore(); }); }
      ctx.fillStyle = 'rgba(17,34,29,.72)'; ctx.fillRect(250, 287, 220, 22); ctx.fillStyle = '#fff'; ctx.font = '11px sans-serif'; ctx.fillText(this.$t('lesson.tvideoJourney.canvasPreviewLabel'), 258, 302);
    },
  },
};
</script>

<style scoped>
.robot-flattened{background:#f3eee3;border:1px solid #d9cdb7;border-radius:16px;padding:14px}.robot-flattened__head{align-items:flex-end;display:flex;gap:12px;justify-content:space-between}.robot-flattened__head h4{margin:3px 0}.robot-flattened__controls{display:flex;gap:7px}.robot-flattened__controls .el-select{width:235px}.robot-flattened__viewport{aspect-ratio:3/2;background:#243c35;border-radius:12px;margin:12px auto 0;max-width:480px;overflow:hidden;position:relative;width:100%}.robot-flattened__viewport canvas{display:block;height:100%;width:100%}.robot-flattened__media{height:1px;left:-9999px;position:absolute;width:1px}.robot-flattened__identity{color:#66736f;display:flex;font-size:11px;gap:12px;justify-content:center;margin-top:8px}.robot-flattened__notice{color:#8c3f31;font-size:12px;font-weight:700;margin:9px 0 0;text-align:center}@media(max-width:680px){.robot-flattened__head{align-items:stretch;flex-direction:column}.robot-flattened__controls{flex-wrap:wrap}.robot-flattened__controls .el-select{width:100%}.robot-flattened__identity{align-items:center;flex-direction:column;gap:3px}}@media(prefers-reduced-motion:reduce){.robot-flattened *{transition:none!important}}
</style>
