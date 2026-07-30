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
      :src="layer.src"
      crossorigin="anonymous"
      muted
      playsinline
      preload="auto"
      @loadeddata="startRendering"
      @error="failLayer"
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
      offscreenCanvas: null,
      errorMessage: '',
      forceResync: true
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
      this.syncPlayback();
      this.startRendering();
    },
    clockMs() {
      if (!this.playing) this.renderFrame();
    },
    replayNonce() {
      this.forceResync = true;
      this.startRendering();
    }
  },
  beforeDestroy() {
    this.stopRendering();
  },
  methods: {
    layerVideos() {
      const videos = this.$refs.layerVideos;
      if (!videos) return [];
      return Array.isArray(videos) ? videos : [videos];
    },
    restartRendering() {
      this.stopRendering();
      this.errorMessage = '';
      this.forceResync = true;
      this.$nextTick(this.startRendering);
    },
    startRendering() {
      if (this.errorMessage) return;
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
    stopRendering() {
      if (this.frameHandle !== null) cancelAnimationFrame(this.frameHandle);
      this.frameHandle = null;
      this.layerVideos().forEach((video) => video.pause());
    },
    syncPlayback() {
      const targetTime = Math.max(0, Number(this.clockMs) || 0) / 1000;
      this.layerVideos().forEach((video) => {
        if (video.readyState >= 1 && (this.forceResync || shouldResyncVideo(targetTime, video.currentTime))) {
          try {
            video.currentTime = targetTime;
          } catch (error) {
            this.failLayer();
            return;
          }
        }
        if (this.playing) {
          if (video.paused) {
            const playResult = video.play();
            if (playResult && typeof playResult.catch === 'function') playResult.catch(() => {});
          }
        } else if (!video.paused) {
          video.pause();
        }
      });
      this.forceResync = false;
    },
    renderFrame() {
      const canvas = this.$refs.canvas;
      if (!canvas || this.errorMessage) return;
      const context = canvas.getContext('2d');
      if (!context) return;

      try {
        this.syncPlayback();
        context.clearRect(0, 0, canvas.width, canvas.height);
        const videos = this.layerVideos();
        this.layers.forEach((layer, index) => {
          const video = videos[index];
          if (!video || video.readyState < 2 || !video.videoWidth || !video.videoHeight) return;
          this.drawLayer(context, video, layer);
        });
      } catch (error) {
        this.failLayer();
      }
    },
    drawLayer(context, video, layer) {
      const rect = objectFitRect(video.videoWidth, video.videoHeight, layer.bounds);
      if (!layer.chromaKey) {
        context.drawImage(video, rect.sx, rect.sy, rect.sw, rect.sh, rect.dx, rect.dy, rect.dw, rect.dh);
        return;
      }

      const width = Math.max(1, Math.ceil(rect.dw));
      const height = Math.max(1, Math.ceil(rect.dh));
      const offscreen = this.getOffscreenCanvas(width, height);
      const offscreenContext = offscreen.getContext('2d', { willReadFrequently: true });
      if (!offscreenContext) throw new Error('Canvas context unavailable');
      offscreenContext.clearRect(0, 0, width, height);
      offscreenContext.drawImage(video, rect.sx, rect.sy, rect.sw, rect.sh, 0, 0, width, height);
      const frame = offscreenContext.getImageData(0, 0, width, height);
      applyChromaKey(frame.data, layer.chromaKey);
      offscreenContext.putImageData(frame, 0, 0);
      context.drawImage(offscreen, rect.dx, rect.dy, rect.dw, rect.dh);
    },
    getOffscreenCanvas(width, height) {
      if (!this.offscreenCanvas) this.offscreenCanvas = document.createElement('canvas');
      if (this.offscreenCanvas.width !== width) this.offscreenCanvas.width = width;
      if (this.offscreenCanvas.height !== height) this.offscreenCanvas.height = height;
      return this.offscreenCanvas;
    },
    failLayer() {
      this.errorMessage = CORS_ERROR;
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
