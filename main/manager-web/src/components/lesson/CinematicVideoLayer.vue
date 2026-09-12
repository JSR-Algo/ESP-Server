<template>
  <div :class="layerClass" :style="positionStyle">
    <video
      ref="video"
      :class="['cinematic-video', { hidden: usesChromaKey }]"
      :src="src"
      crossorigin="anonymous"
      :autoplay="!controlled"
      :loop="!controlled || (transportMaster && playbackMode === 'loop')"
      muted
      playsinline
      preload="auto"
      @loadeddata="handleLoadedData"
      @play="start"
      @seeked="handleSeeked"
      @error="handleMediaError"
    />
    <canvas v-if="usesChromaKey" ref="canvas" class="cinematic-canvas" />
    <p v-if="errorMessage" class="cinematic-error" role="alert">{{ errorMessage }}</p>
  </div>
</template>

<script>
import { applyChromaKey, shouldResyncVideo } from './flattened-cinematic-preview';

export default {
  name: 'CinematicVideoLayer',
  props: {
    layerId: { type: String, default: '' },
    src: { type: String, required: true },
    chromaKey: { type: Object, default: null },
    layerClass: { type: [String, Array, Object], default: '' },
    positionStyle: { type: Object, required: true },
    controlled: { type: Boolean, default: false },
    transportMaster: { type: Boolean, default: false },
    playing: { type: Boolean, default: true },
    clockMs: { type: Number, default: 0 },
    replayNonce: { type: Number, default: 0 },
    playbackMode: { type: String, default: '' },
    durationMs: { type: Number, default: 0 }
  },
  data() {
    return {
      frameHandle: null,
      lastVideoTime: -1,
      chromaUnavailable: false,
      playPending: false,
      playBlocked: false,
      playGeneration: 0,
      errorMessage: '',
      destroyed: false,
      loadTimer: null
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
      this.errorMessage = '';
      this.resetPlaybackGuards();
      this.$nextTick(() => {
        if (this.destroyed) return;
        this.armLoadDeadline();
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
      this.errorMessage = '';
      this.chromaUnavailable = false;
      this.lastVideoTime = -1;
      this.resetPlaybackGuards();
      this.syncPlayback(true);
    }
  },
  mounted() {
    this.armLoadDeadline();
  },
  beforeDestroy() {
    this.destroyed = true;
    this.clearLoadDeadline();
    this.stop();
    this.resetPlaybackGuards();
    const video = this.$refs.video;
    if (video) {
      video.pause();
      video.removeAttribute('src');
      video.load();
    }
  },
  methods: {
    clearLoadDeadline() {
      if (this.loadTimer !== null) clearTimeout(this.loadTimer);
      this.loadTimer = null;
    },
    armLoadDeadline() {
      this.clearLoadDeadline();
      const src = this.src;
      this.loadTimer = setTimeout(() => {
        if (!this.destroyed && this.src === src && this.$refs.video && this.$refs.video.readyState < 2) {
          this.failMedia('load timed out. Check the selected media origin.');
        }
      }, 15000);
      if (this.loadTimer && this.loadTimer.unref) this.loadTimer.unref();
    },
    failMedia(message) {
      if (this.destroyed) return;
      this.clearLoadDeadline();
      this.errorMessage = `${this.layerId || 'Video'} ${message}`;
      this.stop();
      if (this.$refs.video) this.$refs.video.pause();
      if (this.$emit) this.$emit('media-error', { layerId: this.layerId, src: this.src, message: this.errorMessage });
    },
    handleMediaError(event) {
      if (event && event.target !== this.$refs.video) return;
      this.failMedia('failed to load/decode. A verified browser representation is required if this codec is unsupported.');
    },
    mediaPlaybackState() {
      const video = this.$refs.video;
      const currentTimeSec = video ? Number(video.currentTime) : Number.NaN;
      return {
        layerId: this.layerId,
        ready: Boolean(video && video.readyState >= 2 && Number.isFinite(currentTimeSec)),
        pending: this.playPending,
        seeking: Boolean(video && video.seeking),
        ended: Boolean(video && video.ended),
        currentTimeSec: Number.isFinite(currentTimeSec) ? currentTimeSec : 0
      };
    },
    syncToExternalClock(clockMs = this.clockMs) {
      if (!this.controlled) return false;
      return this.syncPlayback(false, clockMs);
    },
    handleLoadedData() {
      this.clearLoadDeadline();
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
      if (this.destroyed) return;
      if (!this.controlled || !this.$refs.video) return;
      const video = this.$refs.video;
      const targetSeconds = Math.max(0, Number(externalClockMs) || 0) / 1000;
      const duration = Number(this.durationMs) > 0 ? this.durationMs / 1000 : video.duration;
      const bounded = Number.isFinite(duration) && duration > 0;
      const localSeconds = bounded && this.playbackMode === 'loop' ? targetSeconds % duration
        : bounded && this.playbackMode === 'once' ? Math.min(targetSeconds, duration) : targetSeconds;
      let didSeek = false;
      if (video.readyState > 0 && (force || (!this.transportMaster && shouldResyncVideo(targetSeconds, video.currentTime)))) {
        try {
          video.currentTime = localSeconds;
          didSeek = true;
        } catch (error) {
          // Metadata may still be settling after a source change; loadeddata will retry once.
        }
      }

      if (!this.playing || (this.playbackMode === 'once' && bounded && targetSeconds >= duration)) {
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
        this.failMedia('playback failed. Replay after checking media availability.');
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
          this.failMedia('playback failed. Replay after checking media availability.');
        }
      );
      return didSeek;
    },
    start(forceFrame = false) {
      this.stop();
      if (this.destroyed || this.errorMessage) return;
      if (!this.usesChromaKey || !this.$refs.video || !this.$refs.canvas) return;
      const drawFrame = (force = false) => {
        const video = this.$refs.video;
        if (!video || !this.$refs.canvas) return false;
        if (video.readyState >= 2 && (force || video.currentTime !== this.lastVideoTime)) {
          this.lastVideoTime = video.currentTime;
          if (!this.renderFrame(video, this.$refs.canvas)) {
            this.chromaUnavailable = true;
            if (!this.errorMessage) this.failMedia('could not composite the frame. Check media CORS and decoding.');
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
        context.clearRect(0, 0, width, height);
        context.drawImage(video, 0, 0, width, height);
        const frame = context.getImageData(0, 0, width, height);
        // MP4 frames are opaque before chroma keying, including the green exit tail.
        let decoded = false;
        for (let index = 3; index < frame.data.length; index += 4) {
          if (frame.data[index] !== 0) { decoded = true; break; }
        }
        if (!decoded) {
          this.failMedia('has no decoded frame. A verified browser representation is required.');
          return false;
        }
        applyChromaKey(frame.data, this.chromaKey);
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
.cinematic-error { position: absolute; inset: 0; margin: 0; padding: 8px; background: #fff0ea; color: #78140d; font-size: 12px; overflow: auto; }
</style>
