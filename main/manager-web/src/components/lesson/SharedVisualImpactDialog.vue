<template>
  <el-dialog
    :visible="visible"
    :title="$t('lesson.sharedImpactTitle')"
    width="720px"
    append-to-body
    :close-on-click-modal="false"
    @open="loadImpact"
    @close="$emit('close')"
  >
    <div v-loading="loading" class="impact-review" role="region" :aria-label="$t('lesson.sharedImpactTitle')">
      <el-alert
        v-if="loadError"
        :title="loadError"
        type="error"
        show-icon
        :closable="false"
      />

      <dl v-if="asset" class="impact-review__source">
        <dt>{{ $t('lesson.sharedImpactSourceKey') }}</dt><dd class="mono">{{ asset.assetKey }}</dd>
        <dt>{{ $t('lesson.sharedImpactChecksum') }}</dt><dd class="mono">{{ asset.sha256 || asset.checksum || '—' }}</dd>
        <dt>{{ $t('lesson.sharedImpactCloneKey') }}</dt><dd><el-input v-model="cloneKey" size="small" :aria-label="$t('lesson.sharedImpactCloneKey')" /></dd>
      </dl>

      <h4>{{ $t('lesson.sharedImpactUsages') }}</h4>
      <el-table :data="backendUsages" size="mini" border>
        <el-table-column :label="$t('lesson.sharedImpactLessonKey')" min-width="150">
          <template slot-scope="scope"><span class="mono">{{ scope.row.lessonKey || scope.row.key || '—' }}</span></template>
        </el-table-column>
        <el-table-column :label="$t('lesson.sharedImpactLesson')" min-width="180">
          <template slot-scope="scope">{{ scope.row.lessonTitle || scope.row.title || '—' }}</template>
        </el-table-column>
        <el-table-column :label="$t('lesson.sharedImpactVersion')" width="90">
          <template slot-scope="scope">{{ scope.row.lessonVersion || scope.row.version || '—' }}</template>
        </el-table-column>
        <el-table-column prop="status" :label="$t('lesson.sharedImpactStatus')" width="100" />
        <el-table-column prop="profile" :label="$t('lesson.sharedImpactProfile')" width="100" />
        <el-table-column :label="$t('lesson.sharedImpactUsageKey')" min-width="170">
          <template slot-scope="scope"><span class="mono">{{ scope.row.assetKey || scope.row.key || asset.assetKey }}</span></template>
        </el-table-column>
      </el-table>
      <p v-if="!loading && !backendUsages.length" class="muted">{{ $t('lesson.sharedImpactNoUsages') }}</p>

      <h4>{{ $t('lesson.sharedImpactLocalSteps') }}</h4>
      <div class="step-keys">
        <el-tag v-for="key in localAffectedStepKeys" :key="key" size="small">{{ key }}</el-tag>
        <span v-if="!localAffectedStepKeys.length" class="muted">{{ $t('lesson.sharedImpactNoLocalSteps') }}</span>
      </div>
    </div>

    <span slot="footer">
      <el-button size="small" @click="keepShared">{{ $t('lesson.sharedImpactKeep') }}</el-button>
      <el-button type="primary" size="small" :loading="cloning" :disabled="loading || !cloneKey" @click="confirmClone">
        {{ $t('lesson.sharedImpactClone') }}
      </el-button>
    </span>
  </el-dialog>
</template>

<script>
import Api from '@/apis/api';
import { collectAssetReferences, nextClonedAssetKey } from '@/components/lesson/lesson-builder-logic';

export default {
  name: 'SharedVisualImpactDialog',
  props: {
    visible: { type: Boolean, default: false },
    lessonId: { type: [String, Number], required: true },
    asset: { type: Object, default: null },
    assets: { type: Array, default: () => [] },
    steps: { type: Array, default: () => [] },
    currentStep: { type: Object, default: null },
  },
  data() {
    return { impact: null, loading: false, cloning: false, loadError: '', cloneKey: '' };
  },
  computed: {
    backendUsages() {
      if (!this.impact) return [];
      const usages = this.impact.usages || this.impact.references || this.impact.usage || this.impact.affectedUsages || [];
      return Array.isArray(usages) ? usages : [];
    },
    localAffectedStepKeys() {
      return this.asset ? collectAssetReferences(this.steps, this.asset.assetKey) : [];
    },
  },
  watch: {
    asset: {
      immediate: true,
      handler(asset) {
        this.cloneKey = asset ? nextClonedAssetKey(asset.assetKey, this.assets) : '';
      },
    },
  },
  methods: {
    loadImpact() {
      if (!this.asset || !this.asset.assetId) return;
      this.loading = true;
      this.loadError = '';
      Api.lesson.reviewSharedVisualImpact(
        this.asset.assetId,
        (impact) => { this.loading = false; this.impact = impact || {}; },
        (msg) => { this.loading = false; this.loadError = String(msg || this.$t('lesson.sharedImpactLoadError')); this.$emit('error', msg); },
      );
    },
    keepShared() {
      this.$emit('keep-shared', { asset: this.asset, currentStep: this.currentStep });
      this.$emit('close');
    },
    confirmClone() {
      if (!this.asset || !this.asset.assetId || !this.cloneKey || this.cloning) return;
      this.cloning = true;
      Api.lesson.cloneSharedVisual(this.lessonId, this.asset.assetId, {
        profile: 'espTft',
        assetKey: this.cloneKey,
      }, (result) => {
        this.cloning = false;
        this.$emit('cloned', result && (result.asset || result.clone) ? (result.asset || result.clone) : result);
      }, (msg) => {
        this.cloning = false;
        this.$emit('error', msg);
      });
    },
  },
};
</script>

<style scoped>
.impact-review__source { display:grid; grid-template-columns:150px minmax(0, 1fr); gap:8px 12px; margin:0 0 18px; }.impact-review__source dt { color:#7c8582; }.impact-review__source dd { margin:0; min-width:0; }.impact-review h4 { margin:18px 0 8px; }.mono { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap:anywhere; }.muted { color:#909399; }.step-keys { display:flex; flex-wrap:wrap; gap:6px; }
</style>
