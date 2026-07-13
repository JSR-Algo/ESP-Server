<template>
  <el-dialog
    :visible="visible"
    :title="$t('lesson.sharedImpactTitle')"
    width="720px"
    append-to-body
    custom-class="shared-impact-dialog"
    :close-on-click-modal="false"
    :close-on-press-escape="!cloning && !cloneUncertain && !reconciling && !rebindPending && !clonedAsset"
    :show-close="!cloning && !cloneUncertain && !reconciling && !rebindPending && !clonedAsset"
    @open="loadImpact"
    @close="handleClose"
  >
    <div v-loading="loading" class="impact-review" role="region" :aria-label="$t('lesson.sharedImpactTitle')">
      <el-alert
        v-if="loadError"
        :title="loadError"
        type="error"
        show-icon
        :closable="false"
      />
      <el-alert v-if="rebindError" :title="rebindError" type="warning" show-icon :closable="false" />
      <el-alert v-if="cloneError" :title="cloneError" type="error" show-icon :closable="false" />
      <el-alert
        v-if="impactLoaded && requiresCurrentReference && !currentStepReferencesSource"
        :title="$t('lesson.sharedImpactSelectAffectedStep')"
        type="warning"
        show-icon
        :closable="false"
      />

      <dl v-if="asset" class="impact-review__source">
        <dt>{{ $t('lesson.sharedImpactSourceKey') }}</dt><dd class="mono">{{ authoritativeAsset.assetKey || '—' }}</dd>
        <dt>{{ $t('lesson.sharedImpactChecksum') }}</dt><dd class="mono">{{ authoritativeAsset.sha256 || authoritativeAsset.checksum || '—' }}</dd>
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
      <el-button size="small" :disabled="!impactLoaded || loading || cloning || cloneUncertain || reconciling || rebindPending || !!clonedAsset" @click="keepShared">{{ $t('lesson.sharedImpactKeep') }}</el-button>
      <el-button v-if="clonedAsset" type="primary" size="small" :loading="rebindPending" :disabled="rebindPending" @click="retryRebind">
        {{ rebindPending ? $t('lesson.sharedImpactRebinding') : $t('lesson.sharedImpactRetryRebind') }}
      </el-button>
      <el-button v-else-if="cloneUncertain || uncertainCloneKey" type="primary" size="small" :loading="reconciling" :disabled="reconciling" @click="retryDiscovery">
        {{ reconciling ? $t('lesson.sharedImpactReconciling') : $t('lesson.sharedImpactRetryDiscovery') }}
      </el-button>
      <el-button v-else type="primary" size="small" :loading="cloning" :disabled="!canClone" @click="confirmClone">
        {{ $t('lesson.sharedImpactClone') }}
      </el-button>
    </span>
  </el-dialog>
</template>

<script>
import Api from '@/apis/api';
import { collectAssetReferences, nextClonedAssetKey, stepReferencesAssetInLayer } from '@/components/lesson/lesson-builder-logic';

export default {
  name: 'SharedVisualImpactDialog',
  props: {
    visible: { type: Boolean, default: false },
    lessonId: { type: [String, Number], required: true },
    asset: { type: Object, default: null },
    assets: { type: Array, default: () => [] },
    steps: { type: Array, default: () => [] },
    currentStep: { type: Object, default: null },
    clonedAsset: { type: Object, default: null },
    rebindPending: { type: Boolean, default: false },
    rebindError: { type: String, default: '' },
    intentType: { type: String, default: 'select' },
    layer: { type: String, default: 'teachingObject' },
    uncertainCloneKey: { type: String, default: '' },
    reconciling: { type: Boolean, default: false },
  },
  data() {
    return { impact: null, impactLoaded: false, loading: false, cloning: false, cloneUncertain: false, loadError: '', cloneError: '', cloneKey: '' };
  },
  computed: {
    authoritativeAsset() {
      if (!this.impact) return {};
      return this.impact.sourceAsset || this.impact.asset || this.impact.source || this.impact;
    },
    backendUsages() {
      if (!this.impact) return [];
      const usages = this.impact.usages || this.impact.references || this.impact.usage || this.impact.affectedUsages || [];
      return Array.isArray(usages) ? usages : [];
    },
    localAffectedStepKeys() {
      return this.asset ? collectAssetReferences(this.steps, this.asset.assetKey) : [];
    },
    requiresCurrentReference() {
      return this.intentType === 'replace';
    },
    currentStepReferencesSource() {
      return Boolean(this.currentStep && this.authoritativeAsset.assetKey
        && stepReferencesAssetInLayer(this.currentStep.stepBody || {}, this.authoritativeAsset.assetKey, this.layer));
    },
    canClone() {
      return Boolean(this.impactLoaded && !this.loading && !this.cloning && !this.rebindPending
        && !this.cloneUncertain && !this.uncertainCloneKey && !this.reconciling && !this.clonedAsset && this.cloneKey
        && (!this.requiresCurrentReference || this.currentStepReferencesSource));
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
      this.impactLoaded = false;
      this.impact = null;
      this.loadError = '';
      this.cloneError = '';
      Api.lesson.reviewSharedVisualImpact(
        this.asset.assetId,
        (impact) => {
          this.loading = false;
          this.impact = impact || {};
          if (!this.authoritativeAsset.assetKey) {
            this.loadError = this.$t('lesson.sharedImpactInvalidResponse');
            return;
          }
          this.cloneKey = nextClonedAssetKey(this.authoritativeAsset.assetKey, this.assets);
          this.impactLoaded = true;
        },
        (msg) => {
          this.loading = false;
          this.impactLoaded = false;
          this.impact = null;
          this.loadError = String(msg || this.$t('lesson.sharedImpactLoadError'));
          this.$emit('error', msg);
        },
      );
    },
    keepShared() {
      if (!this.impactLoaded || this.cloning || this.cloneUncertain || this.reconciling || this.clonedAsset || this.rebindPending) return;
      this.$emit('keep-shared', { asset: this.asset, currentStep: this.currentStep });
      this.$emit('close');
    },
    confirmClone() {
      if (!this.canClone || !this.asset || !this.asset.assetId) return;
      this.cloning = true;
      this.cloneError = '';
      Api.lesson.cloneSharedVisual(this.lessonId, this.asset.assetId, {
        profile: 'espTft',
        assetKey: this.cloneKey,
      }, (result) => {
        if (!this.validCloneResponse(result)) {
          this.cloning = false;
          this.cloneUncertain = true;
          this.cloneError = this.$t('lesson.sharedImpactCloneUncertain', { key: this.cloneKey });
          this.$emit('clone-uncertain', { assetKey: this.cloneKey });
          return;
        }
        this.$emit('cloned', result);
      }, (msg) => {
        this.cloning = false;
        this.$emit('error', msg);
      });
    },
    retryRebind() {
      if (!this.clonedAsset || this.rebindPending) return;
      this.$emit('retry-rebind', this.clonedAsset);
    },
    retryDiscovery() {
      if ((!this.cloneUncertain && !this.uncertainCloneKey) || this.reconciling) return;
      this.$emit('retry-discovery');
    },
    validCloneResponse(result) {
      return Boolean(result && !Array.isArray(result) && ['assetId', 'assetKey', 'path', 'sha256']
        .every((key) => typeof result[key] === 'string' && result[key].trim()));
    },
    handleClose() {
      if (this.cloning || this.cloneUncertain || this.reconciling || this.rebindPending || this.clonedAsset) return;
      this.$emit('close');
    },
  },
};
</script>

<style scoped>
.impact-review__source { display:grid; grid-template-columns:150px minmax(0, 1fr); gap:8px 12px; margin:0 0 18px; }.impact-review__source dt { color:#7c8582; }.impact-review__source dd { margin:0; min-width:0; }.impact-review h4 { margin:18px 0 8px; }.mono { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap:anywhere; }.muted { color:#909399; }.step-keys { display:flex; flex-wrap:wrap; gap:6px; }
</style>
<style>
.shared-impact-dialog { max-width:calc(100vw - 24px); }
</style>
