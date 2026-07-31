<template>
  <div :class="layerClass" :style="positionStyle">
    <video
      ref="video"
      :class="['cinematic-video', { hidden: usesChromaKey }]"
      :src="src"
      crossorigin="anonymous"
      :autoplay="!controlled"
      :loop="!controlled"
      muted
      playsinline
      preload="auto"
      @loadeddata="handleLoadedData"
      @play="start"
      @seeked="handleSeeked"
    />
    <canvas v-if="usesChromaKey" ref="canvas" class="cinematic-canvas" />
  </div>
</template>

<script>
import { shouldResyncVideo } from './flattened-cinematic-preview';

export default {
  name: 'CinematicVideoLayer',
  props: {
    layerId: { type: String, default: '' },
    src: { type: String, required: true },
    chromaKey: { type: Object, default: null },
    layerClass: { type: [String, Array, Object], default: '' },
    positionStyle: { type: Object, required: true },
    controlled: { type: Boolean, default: false },
    playing: { type: Boolean, default: true },
    clockMs: { type: Number, default: 0 },
    replayNonce: { type: Number, default: 0 }
  },
  data() {
    return {
      frameHandle: null,
      lastVideoTime: -1,
      chromaUnavailable: false,
      playPending: false,
      playBlocked: false,
      playGeneration: 0
    };
  },
  computed: {
    usesChromaKey() {
      const color = this.chromaKey && this.chromaKey.color;
      return !this.chromaUnavailable && Boolean(color && [color.r, color.g, color.b].every(Number.isFinite));
    }
  },
  watch: {
    src() {
      this.stop();
      this.lastVideoTime = -1;
      this.chromaUnavailable = false;
      this.resetPlaybackGuards();
      this.$nextTick(() => {
        if (this.controlled) this.syncPlayback(true);
        else this.start();
      });
    },
    controlled() {
      this.resetPlaybackGuards();
      this.$nextTick(() => this.syncPlayback(true));
    },
    playing() {
      this.resetPlaybackGuards();
      this.syncPlayback();
    },
    clockMs() {
      this.syncPlayback();
    },
    replayNonce() {
      this.lastVideoTime = -1;
      this.resetPlaybackGuards();
      this.syncPlayback(true);
    }
  },
  beforeDestroy() {
    this.stop();
  },
  methods: {
    mediaPlaybackState() {
      const video = this.$refs.video;
      const currentTimeSec = video ? Number(video.currentTime) : Number.NaN;
      return {
        layerId: this.layerId,
        ready: Boolean(video && video.readyState >= 1 && Number.isFinite(currentTimeSec)),
        currentTimeSec: Number.isFinite(currentTimeSec) ? currentTimeSec : 0
      };
    },
    syncToExternalClock(clockMs = this.clockMs) {
      if (!this.controlled) return false;
      return this.syncPlayback(false, clockMs);
    },
    handleLoadedData() {
      this.syncPlayback(true);
      this.start(this.controlled && !this.playing);
    },
    handleSeeked() {
      if (this.controlled && !this.playing) this.start(true);
    },
    resetPlaybackGuards() {
      this.playGeneration += 1;
      this.playPending = false;
      this.playBlocked = false;
    },
    syncPlayback(force = false, externalClockMs = this.clockMs) {
      if (!this.controlled || !this.$refs.video) return;
      const video = this.$refs.video;
      const targetSeconds = Math.max(0, Number(externalClockMs) || 0) / 1000;
      let didSeek = false;
      if (video.readyState > 0 && (force || shouldResyncVideo(targetSeconds, video.currentTime))) {
        try {
          video.currentTime = targetSeconds;
          didSeek = true;
        } catch (error) {
          // Metadata may still be settling after a source change; loadeddata will retry once.
        }
      }

      if (!this.playing) {
        video.pause();
        this.stop();
        return didSeek;
      }
      if (!video.paused && !video.ended) return didSeek;
      if (this.playPending || this.playBlocked) return didSeek;

      const generation = this.playGeneration;
      let playResult;
      try {
        playResult = video.play();
      } catch (error) {
        this.playBlocked = true;
        return didSeek;
      }
      if (!playResult || typeof playResult.then !== 'function') return didSeek;

      this.playPending = true;
      playResult.then(
        () => {
          if (generation === this.playGeneration) this.playPending = false;
        },
        () => {
          if (generation !== this.playGeneration) return;
          this.playPending = false;
          this.playBlocked = true;
        }
      );
      return didSeek;
    },
    start(forceFrame = false) {
      this.stop();
      if (!this.usesChromaKey || !this.$refs.video || !this.$refs.canvas) return;
      const drawFrame = (force = false) => {
        const video = this.$refs.video;
        if (!video || !this.$refs.canvas) return false;
        if (video.readyState >= 2 && (force || video.currentTime !== this.lastVideoTime)) {
          this.lastVideoTime = video.currentTime;
          if (!this.renderFrame(video, this.$refs.canvas)) {
            this.chromaUnavailable = true;
            this.stop();
            return false;
          }
        }
        return true;
      };

      if (this.controlled && !this.playing) {
        if (forceFrame) drawFrame(true);
        return;
      }

      const render = () => {
        if (this.controlled && !this.playing) {
          this.frameHandle = null;
          return;
        }
        if (!drawFrame()) return;
        this.frameHandle = requestAnimationFrame(render);
      };
      this.frameHandle = requestAnimationFrame(render);
    },
    stop() {
      if (this.frameHandle !== null) cancelAnimationFrame(this.frameHandle);
      this.frameHandle = null;
    },
    renderFrame(video, canvas) {
      try {
        const width = Math.max(1, this.$el.clientWidth || video.videoWidth || 1);
        const height = Math.max(1, this.$el.clientHeight || video.videoHeight || 1);
        if (canvas.width !== width) canvas.width = width;
        if (canvas.height !== height) canvas.height = height;
        const context = canvas.getContext('2d', { willReadFrequently: true });
        if (!context) return false;
        context.drawImage(video, 0, 0, width, height);
        const frame = context.getImageData(0, 0, width, height);
        const color = this.chromaKey.color;
        const tolerance = Math.max(0, Number(this.chromaKey.tolerance) || 0);
        const feather = Math.max(1, Number(this.chromaKey.feather) || 1);
        for (let offset = 0; offset < frame.data.length; offset += 4) {
          const distance = Math.max(
            Math.abs(frame.data[offset] - color.r),
            Math.abs(frame.data[offset + 1] - color.g),
            Math.abs(frame.data[offset + 2] - color.b)
          );
          if (distance <= tolerance) frame.data[offset + 3] = 0;
          else if (distance < tolerance + feather) {
            frame.data[offset + 3] = Math.round(255 * (distance - tolerance) / feather);
          }
        }
        context.putImageData(frame, 0, 0);
        return true;
      } catch (error) {
        return false;
      }
    }
  }
};
</script>

<style scoped>
.cinematic-video,
.cinematic-canvas { display: block; width: 100%; height: 100%; object-fit: inherit; }
.cinematic-video.hidden { position: absolute; width: 1px; height: 1px; opacity: 0; pointer-events: none; }
.cinematic-canvas { position: absolute; inset: 0; }
</style>
