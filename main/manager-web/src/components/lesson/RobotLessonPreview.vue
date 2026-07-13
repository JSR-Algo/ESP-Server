<template>
  <section class="robot-preview">
    <div class="preview-head">
      <div>
        <span class="eyebrow">ESP TFT preview</span>
        <strong>{{ currentTitle }}</strong>
      </div>
      <el-radio-group v-model="activePath" size="mini" @change="emitPath">
        <el-radio-button label="correct">Correct</el-radio-button>
        <el-radio-button label="nearMiss">Near miss</el-radio-button>
        <el-radio-button label="incorrect">Incorrect</el-radio-button>
      </el-radio-group>
    </div>

    <div class="screen" :style="backgroundStyle">
      <img v-if="teachingObjectSrc" class="teaching-object" :src="teachingObjectSrc" alt="" />
      <img v-if="robotOverlaySrc" class="robot-overlay" :src="robotOverlaySrc" alt="" />
      <div v-if="!backgroundSrc && !teachingObjectSrc" class="empty-scene">
        <span>{{ primaryWord || 'Robot scene' }}</span>
      </div>
      <div class="caption">
        {{ caption }}
      </div>
    </div>

    <div class="meta-grid">
      <div><span>Step</span><strong>{{ currentIndex + 1 }} / {{ steps.length || 1 }}</strong></div>
      <div><span>Type</span><strong>{{ currentStep.stepType || currentStep.type || '-' }}</strong></div>
      <div><span>Motion</span><strong>{{ motionForPath }}</strong></div>
      <div><span>Assets</span><strong>{{ assetCount }}</strong></div>
    </div>
  </section>
</template>

<script>
export default {
  name: 'RobotLessonPreview',
  props: {
    manifest: { type: Object, required: true },
    stepIndex: { type: Number, default: 0 },
    initialPath: { type: String, default: 'correct' },
  },
  data() {
    return {
      activePath: this.initialPath || 'correct',
    };
  },
  computed: {
    steps() {
      const raw = this.manifest || {};
      if (Array.isArray(raw.steps)) return raw.steps;
      if (Array.isArray(raw.lessonSteps)) return raw.lessonSteps;
      if (raw.lesson && Array.isArray(raw.lesson.steps)) return raw.lesson.steps;
      if (raw.frames && Array.isArray(raw.frames)) {
        return raw.frames
          .filter((frame) => frame && (frame.type === 'lesson_step' || frame.body))
          .map((frame) => ({ ...(frame.body || {}), frameType: frame.type, stepId: frame.stepId }));
      }
      return [];
    },
    currentIndex() {
      if (!this.steps.length) return 0;
      return Math.min(Math.max(Number(this.stepIndex || 0), 0), this.steps.length - 1);
    },
    currentStep() {
      return this.steps[this.currentIndex] || {};
    },
    body() {
      return this.currentStep.body || this.currentStep.stepBody || this.currentStep;
    },
    scene() {
      return this.body.scene || this.body;
    },
    backgroundSrc() {
      return this.assetSrc(this.scene.backgroundScene) || this.assetSrc(this.scene.background);
    },
    teachingObjectSrc() {
      return this.assetSrc(this.scene.teachingObject) || this.assetSrc(this.body.teachingObject);
    },
    robotOverlaySrc() {
      return this.assetSrc(this.scene.robotOverlay) || this.assetSrc(this.body.robotOverlay);
    },
    backgroundStyle() {
      return this.backgroundSrc
        ? { backgroundImage: `linear-gradient(rgba(15, 24, 36, 0.08), rgba(15, 24, 36, 0.08)), url("${this.backgroundSrc}")` }
        : {};
    },
    primaryWord() {
      return this.scene.primaryWord || this.body.primaryWord || this.currentStep.subject || '';
    },
    caption() {
      return this.scene.altCaption
        || this.body.caption
        || this.currentStep.prompt
        || this.currentStep.title
        || this.primaryWord
        || 'Generate a preview to inspect this lesson step.';
    },
    currentTitle() {
      return this.currentStep.stepId || this.currentStep.stepKey || this.currentStep.subject || `Step ${this.currentIndex + 1}`;
    },
    motionForPath() {
      const motion = this.body.motion || {};
      return motion[this.activePath] || motion.present || this.currentStep.pose || 'teach';
    },
    assetCount() {
      return [this.backgroundSrc, this.teachingObjectSrc, this.robotOverlaySrc].filter(Boolean).length;
    },
  },
  watch: {
    initialPath(value) {
      if (value && value !== this.activePath) this.activePath = value;
    },
    stepIndex() {
      this.emitPath();
    },
  },
  mounted() {
    this.emitPath();
  },
  methods: {
    assetSrc(value) {
      if (!value) return '';
      if (typeof value === 'string') return value;
      const asset = value.asset || value.poster || value.image || value;
      return asset.src || asset.url || asset.path || '';
    },
    emitPath() {
      this.$emit('path-change', {
        path: this.activePath,
        stepIndex: this.currentIndex,
        stepId: this.currentStep.stepId || this.currentStep.stepKey || null,
        motion: this.motionForPath,
      });
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
  letter-spacing: 0;
  text-transform: uppercase;
}

.screen {
  aspect-ratio: 3 / 2;
  background: #13211f;
  background-position: center;
  background-repeat: no-repeat;
  background-size: cover;
  border: 10px solid #0f1817;
  border-radius: 16px;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.12);
  min-height: 220px;
  overflow: hidden;
  position: relative;
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
  grid-template-columns: repeat(4, 1fr);
  margin-top: 12px;
}

.meta-grid div {
  background: #f5f8f7;
  border-radius: 10px;
  display: grid;
  gap: 4px;
  padding: 8px;
}

.meta-grid span {
  color: #788b86;
  font-size: 10px;
  text-transform: uppercase;
}

.meta-grid strong {
  color: #122a26;
  font-size: 12px;
}

@media (max-width: 900px) {
  .preview-head {
    align-items: stretch;
    flex-direction: column;
  }

  .meta-grid {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
