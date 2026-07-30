<template>
  <div :class="layerClass" :style="positionStyle">
    <video
      ref="video"
      :class="['cinematic-video', { hidden: usesChromaKey }]"
      :src="src"
      crossorigin="anonymous"
      autoplay
      loop
      muted
      playsinline
      preload="auto"
      @loadeddata="start"
      @play="start"
    />
    <canvas v-if="usesChromaKey" ref="canvas" class="cinematic-canvas" />
  </div>
</template>

<script>
export default {
  name: 'CinematicVideoLayer',
  props: {
    src: { type: String, required: true },
    chromaKey: { type: Object, default: null },
    layerClass: { type: [String, Array, Object], default: '' },
    positionStyle: { type: Object, required: true }
  },
  data() {
    return { frameHandle: null, lastVideoTime: -1, chromaUnavailable: false };
  },
  computed: {
    usesChromaKey() {
      const color = this.chromaKey && this.chromaKey.color;
      return !this.chromaUnavailable && Boolean(color && [color.r, color.g, color.b].every(Number.isFinite));
    }
  },
  watch: {
    src() {
      this.lastVideoTime = -1;
      this.chromaUnavailable = false;
      this.$nextTick(this.start);
    }
  },
  beforeDestroy() {
    this.stop();
  },
  methods: {
    start() {
      this.stop();
      if (!this.usesChromaKey || !this.$refs.video || !this.$refs.canvas) return;
      const render = () => {
        const video = this.$refs.video;
        if (!video || !this.$refs.canvas) return;
        if (video.readyState >= 2 && video.currentTime !== this.lastVideoTime) {
          this.lastVideoTime = video.currentTime;
          if (!this.renderFrame(video, this.$refs.canvas)) {
            this.chromaUnavailable = true;
            this.stop();
            return;
          }
        }
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
