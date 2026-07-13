<template>
  <section class="robot-preview">
    <div class="preview-head">
      <div>
        <span class="eyebrow">Authoritative robot preview</span>
        <strong>{{ currentTitle }}</strong>
      </div>
      <div class="server-size">
        {{ manifestPreview.preview.profile }}
        {{ manifestPreview.preview.width }}x{{ manifestPreview.preview.height }}
      </div>
    </div>

    <div ref="scaler" class="stage-scaler">
      <div class="stage" :style="stageStyle">
        <img v-if="teachingObjectSrc" class="teaching-object" :src="teachingObjectSrc" alt="" />
        <img v-if="robotOverlaySrc" class="robot-overlay" :src="robotOverlaySrc" alt="" />
        <div v-if="!backgroundSrc && !teachingObjectSrc" class="empty-scene">
          <span>{{ currentStep.subject || 'Robot scene' }}</span>
        </div>
        <div class="caption">{{ caption }}</div>
      </div>
    </div>

    <div class="meta-grid">
      <div><span>Step</span><strong>{{ currentIndex + 1 }} / {{ steps.length || 1 }}</strong></div>
      <div><span>Type</span><strong>{{ currentStep.type || '-' }}</strong></div>
      <div><span>Checksum</span><strong class="mono">{{ manifestPreview.checksum }}</strong></div>
      <div><span>ETag</span><strong class="mono">{{ manifestPreview.etag }}</strong></div>
    </div>
  </section>
</template>

<script>
export default {
  name: 'RobotLessonPreview',
  props: {
    manifestPreview: { type: Object, required: true },
    stepIndex: { type: Number, default: 0 },
  },
  data() {
    return { previewScale: 1, resizeObserver: null };
  },
  computed: {
    steps() {
      const manifest = this.manifestPreview.manifest || {};
      return Array.isArray(manifest.steps) ? manifest.steps : [];
    },
    currentIndex() {
      if (!this.steps.length) return 0;
      return Math.min(Math.max(Number(this.stepIndex || 0), 0), this.steps.length - 1);
    },
    currentStep() {
      return this.steps[this.currentIndex] || {};
    },
    scene() {
      return this.currentStep.scene || {};
    },
    backgroundSrc() {
      const background = this.scene.backgroundScene || {};
      return this.assetSrc(background.poster || background);
    },
    teachingObjectSrc() {
      return this.assetSrc(this.scene.teachingObject);
    },
    robotOverlaySrc() {
      return this.assetSrc(this.scene.robotOverlay);
    },
    backgroundStyle() {
      return this.backgroundSrc ? { backgroundImage: `url("${this.backgroundSrc}")` } : {};
    },
    stageStyle() {
      return { ...this.backgroundStyle, transform: `scale(${this.previewScale})` };
    },
    caption() {
      const background = this.scene.backgroundScene || {};
      return background.altCaption || this.currentStep.prompt || this.currentStep.subject || 'Robot scene';
    },
    currentTitle() {
      return this.currentStep.id || `Step ${this.currentIndex + 1}`;
    },
  },
  mounted() {
    this.updatePreviewScale();
    if (typeof ResizeObserver === 'function') {
      this.resizeObserver = new ResizeObserver((entries) => {
        const entry = entries && entries[0];
        this.updatePreviewScale(entry && entry.contentRect && entry.contentRect.width);
      });
      this.resizeObserver.observe(this.$refs.scaler);
    }
    if (typeof window !== 'undefined') window.addEventListener('resize', this.updatePreviewScale);
  },
  beforeDestroy() {
    if (this.resizeObserver) this.resizeObserver.disconnect();
    this.resizeObserver = null;
    if (typeof window !== 'undefined') window.removeEventListener('resize', this.updatePreviewScale);
  },
  methods: {
    previewScaleForWidth(width) {
      const available = Number(width);
      if (!Number.isFinite(available) || available <= 0) return 1;
      return Math.min(1, available / 480);
    },
    updatePreviewScale(width) {
      const scaler = this.$refs && this.$refs.scaler;
      const available = Number(width) || (scaler && scaler.getBoundingClientRect().width) || 480;
      this.previewScale = this.previewScaleForWidth(available);
    },
    assetSrc(value) {
      if (!value || typeof value !== 'object') return '';
      const asset = value.asset || value;
      return asset.src || asset.url || asset.path || '';
    },
  },
};
</script>

<style scoped>
.robot-preview {
  background: #ffffff;
  border: 1px solid #d9e3df;
  border-radius: 18px;
  box-shadow: 0 8px 22px rgba(20, 42, 38, 0.08);
  min-width: 0;
  padding: 16px;
}

.preview-head {
  align-items: flex-start;
  display: flex;
  gap: 12px;
  justify-content: space-between;
  margin-bottom: 12px;
}

.eyebrow {
  color: #6e7f7a;
  display: block;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.server-size {
  background: #edf5f2;
  border-radius: 999px;
  color: #285348;
  font-size: 11px;
  font-weight: 800;
  padding: 5px 9px;
}

.stage-scaler {
  aspect-ratio: 3 / 2;
  margin: 0 auto;
  max-width: 480px;
  overflow: hidden;
  position: relative;
  width: 100%;
}

.stage {
  background: #13211f;
  background-position: center;
  background-repeat: no-repeat;
  background-size: cover;
  height: 320px;
  left: 0;
  overflow: hidden;
  position: absolute;
  top: 0;
  transform-origin: top left;
  width: 480px;
}

.teaching-object {
  left: 50%;
  max-height: 58%;
  max-width: 58%;
  object-fit: contain;
  position: absolute;
  top: 43%;
  transform: translate(-50%, -50%);
}

.robot-overlay {
  bottom: 34px;
  left: 18px;
  max-height: 34%;
  max-width: 30%;
  object-fit: contain;
  position: absolute;
}

.empty-scene {
  align-items: center;
  color: #f7f2df;
  display: flex;
  font-size: 28px;
  font-weight: 800;
  height: 100%;
  justify-content: center;
  padding: 18px;
  text-align: center;
}

.caption {
  background: rgba(255, 255, 255, 0.9);
  border-radius: 10px;
  bottom: 12px;
  color: #102522;
  font-weight: 700;
  left: 50%;
  line-height: 1.35;
  max-width: 86%;
  padding: 8px 12px;
  position: absolute;
  text-align: center;
  transform: translateX(-50%);
}

.meta-grid {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-top: 12px;
}

.meta-grid div {
  background: #f5f8f7;
  border-radius: 10px;
  display: grid;
  gap: 4px;
  min-width: 0;
  padding: 8px;
}

.meta-grid span { color: #788b86; font-size: 10px; text-transform: uppercase; }
.meta-grid strong { color: #122a26; font-size: 12px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
</style>
