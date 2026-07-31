<template>
  <section class="flattened-preview" data-testid="flattened-cinematic-preview">
    <canvas
      ref="canvas"
      class="flattened-preview__canvas"
      width="480"
      height="320"
      aria-label="Flattened 480 by 320 robot cinematic preview"
    />
    <video
      v-for="layer in layers"
      :key="`${layer.id}-${layer.src}`"
      ref="layerVideos"
      class="flattened-preview__source"
      :data-layer-id="layer.id"
      :src="layer.src"
      crossorigin="anonymous"
      muted
      playsinline
      preload="auto"
      @loadeddata="startRendering"
      @seeked="handleSeeked"
      @error="handleMediaError(layer.id, layer.src, $event)"
    />
    <p v-if="errorMessage" class="flattened-preview__error" role="alert">
      {{ errorMessage }}
    </p>
  </section>
</template>

<script>
import {
  applyChromaKey,
  flattenableLayers,
  objectFitRect,
  shouldResyncVideo
} from './flattened-cinematic-preview';

const CORS_ERROR = 'Flattened preview unavailable: media host must allow CORS.';

export default {
  name: 'FlattenedCinematicPreview',
  props: {
    projection: { type: Object, required: true },
    playing: { type: Boolean, default: false },
    clockMs: { type: Number, default: 0 },
    replayNonce: { type: Number, default: 0 }
  },
  data() {
    return {
      frameHandle: null,
      chromaCanvasesByLayer: Object.create(null),
      chromaFrameSignaturesByLayer: Object.create(null),
      errorMessage: '',
      forceResync: true,
      forceRender: true,
      lastRenderSignature: '',
      playPendingByLayer: Object.create(null),
      playBlockedByLayer: Object.create(null),
      playGeneration: 0
    };
  },
  computed: {
    layers() {
      return flattenableLayers(this.projection);
    }
  },
  watch: {
    projection: {
      deep: true,
      handler() {
        this.restartRendering();
      }
    },
    playing() {
      this.resetPlaybackGuards();
      this.forceRender = true;
      this.syncPlayback();
      this.startRendering();
    },
    clockMs() {
      if (!this.playing) {
        this.forceRender = true;
        this.renderFrame();
      }
    },
    replayNonce() {
      this.resetPlaybackGuards();
      this.invalidateFrameCache();
      this.forceResync = true;
      this.startRendering();
    }
  },
  beforeDestroy() {
    this.stopRendering();
  },
  methods: {
    videoRefs() {
      const videos = this.$refs.layerVideos;
      if (!videos) return [];
      return Array.isArray(videos) ? videos : [videos];
    },
    videoByLayerId(layerId) {
      return this.videoRefs().find((video) => video.dataset.layerId === layerId) || null;
    },
    restartRendering() {
      this.stopRendering();
      this.errorMessage = '';
      this.forceResync = true;
      this.invalidateFrameCache();
      this.$nextTick(this.startRendering);
    },
    startRendering() {
      if (this.errorMessage) return;
      this.forceRender = true;
      this.syncPlayback();
      if (this.frameHandle !== null) return;
      const render = () => {
        this.frameHandle = null;
        this.renderFrame();
        if (this.playing && !this.errorMessage) {
          this.frameHandle = requestAnimationFrame(render);
        }
      };
      this.frameHandle = requestAnimationFrame(render);
    },
    handleSeeked() {
      this.invalidateFrameCache();
      this.startRendering();
    },
    stopRendering() {
      if (this.frameHandle !== null) cancelAnimationFrame(this.frameHandle);
      this.frameHandle = null;
      this.videoRefs().forEach((video) => video.pause());
      this.resetPlaybackGuards();
    },
    syncPlayback() {
      const targetTime = Math.max(0, Number(this.clockMs) || 0) / 1000;
      this.layers.forEach((layer) => {
        const video = this.videoByLayerId(layer.id);
        if (!video) return;
        if (video.readyState >= 1 && (this.forceResync || shouldResyncVideo(targetTime, video.currentTime))) {
          try {
            video.currentTime = targetTime;
          } catch (error) {
            this.failRender(layer.id);
            return;
          }
        }
        if (this.playing) {
          if (video.paused) this.playVideo(layer.id, video);
        } else if (!video.paused) {
          video.pause();
        }
      });
      this.forceResync = false;
    },
    playVideo(layerId, video) {
      if (this.playPendingByLayer[layerId] || this.playBlockedByLayer[layerId]) return;
      const generation = this.playGeneration;
      let playResult;
      try {
        playResult = video.play();
      } catch (error) {
        this.playBlockedByLayer[layerId] = true;
        return;
      }
      if (!playResult || typeof playResult.then !== 'function') return;
      this.playPendingByLayer[layerId] = playResult;
      playResult.then(() => {
        if (generation === this.playGeneration) delete this.playPendingByLayer[layerId];
      }).catch(() => {
        if (generation !== this.playGeneration) return;
        delete this.playPendingByLayer[layerId];
        this.playBlockedByLayer[layerId] = true;
      });
    },
    resetPlaybackGuards() {
      this.playGeneration += 1;
      this.playPendingByLayer = Object.create(null);
      this.playBlockedByLayer = Object.create(null);
    },
    invalidateFrameCache() {
      this.forceRender = true;
      this.lastRenderSignature = '';
      this.chromaFrameSignaturesByLayer = Object.create(null);
    },
    videoFrameSignature(video) {
      if (typeof video.getVideoPlaybackQuality === 'function') {
        const quality = video.getVideoPlaybackQuality();
        if (quality && Number.isFinite(quality.totalVideoFrames)) return `frames:${quality.totalVideoFrames}`;
      }
      if (Number.isFinite(video.webkitDecodedFrameCount)) return `frames:${video.webkitDecodedFrameCount}`;
      return `time:${Math.floor((Number(video.currentTime) || 0) * 30)}`;
    },
    renderFrame() {
      const canvas = this.$refs.canvas;
      if (!canvas || this.errorMessage) return;
      const context = canvas.getContext('2d');
      if (!context) return;

      try {
        this.syncPlayback();
        const renderableLayers = this.layers.map((layer) => ({
          layer,
          video: this.videoByLayerId(layer.id)
        })).filter(({ video }) => video && video.readyState >= 2 && video.videoWidth && video.videoHeight);
        const signature = renderableLayers
          .map(({ layer, video }) => `${layer.id}:${this.videoFrameSignature(video)}`)
          .join('|');
        if (!this.forceRender && signature === this.lastRenderSignature) return;

        context.clearRect(0, 0, canvas.width, canvas.height);
        for (const { layer, video } of renderableLayers) {
          if (!this.drawLayer(context, video, layer)) return;
        }
        this.lastRenderSignature = signature;
        this.forceRender = false;
      } catch (error) {
        this.failRender();
      }
    },
    drawLayer(context, video, layer) {
      const rect = objectFitRect(video.videoWidth, video.videoHeight, layer.bounds);
      if (!layer.chromaKey) {
        context.drawImage(video, rect.sx, rect.sy, rect.sw, rect.sh, rect.dx, rect.dy, rect.dw, rect.dh);
        return true;
      }

      const width = Math.max(1, Math.ceil(rect.dw));
      const height = Math.max(1, Math.ceil(rect.dh));
      const frameSignature = this.videoFrameSignature(video);
      const offscreen = this.getOffscreenCanvas(layer.id, width, height);
      const offscreenContext = offscreen.getContext('2d', { willReadFrequently: true });
      if (!offscreenContext) throw new Error('Canvas context unavailable');
      if (this.chromaFrameSignaturesByLayer[layer.id] !== frameSignature) {
        offscreenContext.clearRect(0, 0, width, height);
        offscreenContext.drawImage(video, rect.sx, rect.sy, rect.sw, rect.sh, 0, 0, width, height);
        let frame;
        try {
          frame = offscreenContext.getImageData(0, 0, width, height);
        } catch (error) {
          this.failCanvasReadback(layer.id);
          return false;
        }
        applyChromaKey(frame.data, layer.chromaKey);
        offscreenContext.putImageData(frame, 0, 0);
        this.chromaFrameSignaturesByLayer[layer.id] = frameSignature;
      }
      context.drawImage(offscreen, rect.dx, rect.dy, rect.dw, rect.dh);
      return true;
    },
    getOffscreenCanvas(layerId, width, height) {
      let canvas = this.chromaCanvasesByLayer[layerId];
      if (!canvas) {
        canvas = document.createElement('canvas');
        this.chromaCanvasesByLayer[layerId] = canvas;
      }
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      return canvas;
    },
    handleMediaError(layerId, source) {
      const currentLayer = this.layers.find((layer) => layer.id === layerId && layer.src === source);
      if (!currentLayer) return;
      this.errorMessage = `Flattened preview unavailable: ${layerId} failed to load/decode.`;
      this.stopRendering();
    },
    failCanvasReadback(layerId) {
      this.errorMessage = `${CORS_ERROR} Affected layer: ${layerId}.`;
      this.stopRendering();
    },
    failRender(layerId = 'unknown') {
      this.errorMessage = `Flattened preview unavailable: ${layerId} could not be rendered.`;
      this.stopRendering();
    }
  }
};
</script>

<style scoped>
.flattened-preview {
  margin: 0;
  min-width: 0;
}

.flattened-preview__canvas {
  background: #101615;
  display: block;
  height: auto;
  max-width: 100%;
  width: 480px;
}

.flattened-preview__source {
  height: 1px;
  left: -10000px;
  opacity: 0;
  pointer-events: none;
  position: fixed;
  top: 0;
  width: 1px;
}

.flattened-preview__error {
  background: #fff0eb;
  border: 1px solid #e88c72;
  color: #792d1b;
  font-size: 12px;
  margin: 10px 0 0;
  padding: 9px 11px;
}
</style>
