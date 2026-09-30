<template>
  <span class="picker-mjpeg-thumbnail">
    <canvas ref="canvas" />
    <span v-if="error" class="picker-mjpeg-thumbnail__error" role="alert">{{ layerId }} {{ error }}</span>
  </span>
</template>
<script>
export default {
  name: 'PickerMjpegThumbnail',
  props: {
    registry: { type: Object, required: true }, src: { type: String, required: true },
    identity: { type: Object, required: true }, layerId: { type: String, default: '' },
  },
  data: () => ({ error: '' }),
  watch: { src() { this.load(); }, identity() { this.load(); } },
  mounted() { this.load(); },
  beforeDestroy() { if (this._release) this._release(); },
  methods: {
    retry() {
      this.registry.retry(this.src, this.identity);
      // An evicted entry has no registry handle; reacquire through the visible consumer.
      this.load();
    },
    load() {
      if (this._release) this._release();
      this.error = '';
      const canvas = this.$refs.canvas;
      canvas.width = 0; canvas.height = 0;
      this._release = this.registry.acquire(this.src, this.identity, entry => {
        this.error = entry.error ? entry.error.message : '';
        this.$emit('error', this.error);
        if (entry.status !== 'ready') return;
        try {
          canvas.width = entry.canvas.width; canvas.height = entry.canvas.height;
          canvas.getContext('2d').drawImage(entry.canvas, 0, 0);
        } catch (error) { this.error = 'MJPEG frame could not be presented'; this.$emit('error', this.error); }
      });
    },
  },
};
</script>
<style scoped>
.picker-mjpeg-thumbnail { position:relative; display:flex; width:100%; height:100%; align-items:center; justify-content:center; }
.picker-mjpeg-thumbnail canvas { width:100%; height:100%; object-fit:contain; }
.picker-mjpeg-thumbnail__error { position:absolute; inset:0; padding:8px; background:#fff0ea; color:#78140d; font-size:12px; overflow:auto; }
</style>
