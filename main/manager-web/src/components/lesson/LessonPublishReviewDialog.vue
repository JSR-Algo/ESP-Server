<template>
  <el-dialog
    :title="$t('lesson.publishReviewTitle')"
    :visible="visible"
    width="760px"
    :close-on-click-modal="!publishing && !locked"
    :close-on-press-escape="!publishing && !locked"
    :show-close="!publishing && !locked"
    @close="close"
  >
    <template v-if="snapshot">
      <el-alert :title="$t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstWarning' : 'lesson.publishImmutableWarning')" type="warning" :closable="false" show-icon />
      <div class="evidence-grid">
        <section>
          <h4>{{ $t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstSourceEvidence' : 'lesson.publishSourceEvidence') }}</h4>
          <dl>
            <dt>{{ $t('lesson.publishFieldLesson') }}</dt><dd class="mono">{{ snapshot.originalLessonId }}</dd>
            <dt>{{ $t('lesson.publishFieldVersion') }}</dt><dd>v{{ snapshot.originalVersion }}</dd>
            <dt>{{ $t('lesson.publishFieldChecksum') }}</dt><dd class="mono">{{ originalChecksum }}</dd>
            <dt>{{ $t('lesson.publishFieldPins') }}</dt><dd>{{ snapshot.originalAssets.length }}</dd>
            <dt>{{ $t('lesson.publishFieldBytes') }}</dt><dd>{{ formatBytes(snapshot.originalBytes) }}</dd>
          </dl>
        </section>
        <section>
          <h4>{{ $t('lesson.publishTargetEvidence') }}</h4>
          <dl>
            <dt>{{ $t('lesson.publishFieldVersion') }}</dt><dd>v{{ snapshot.targetVersion }}</dd>
            <dt>{{ $t('lesson.publishFieldSteps') }}</dt><dd>{{ snapshot.stepCount }}</dd>
            <dt>{{ $t('lesson.publishFieldAssets') }}</dt><dd>{{ snapshot.assetCount }}</dd>
            <dt>{{ $t('lesson.publishFieldProfile') }}</dt><dd>{{ snapshot.previewProfile }}</dd>
            <dt>{{ $t('lesson.publishFieldStage') }}</dt><dd>{{ snapshot.previewWidth }}×{{ snapshot.previewHeight }}</dd>
            <dt>{{ $t('lesson.publishFieldChecksum') }}</dt><dd class="mono">{{ previewChecksum }}</dd>
            <dt>{{ $t('lesson.publishFieldEtag') }}</dt><dd class="mono">{{ snapshot.previewEtag }}</dd>
          </dl>
        </section>
      </div>
      <section class="proof-strip">
        <div><span>{{ $t('lesson.publishSimulationEvidence') }}</span><strong>{{ snapshot.simulationTerminationReason }}</strong><small class="mono">{{ snapshot.simulationChecksum }}</small><small class="mono">{{ snapshot.simulationEtag }} · {{ $t('lesson.publishFieldProof') }} {{ snapshot.proofVersion }}</small><small class="mono">{{ formatCompletion(snapshot.simulationCompletionEvent) }}</small></div>
        <div><span>{{ $t('lesson.serverValidation') }}</span><strong>{{ snapshot.validationResult.valid ? $t('lesson.statusPass') : $t('lesson.statusFail') }}</strong><small>{{ snapshot.validationProfiles.join(', ') || '—' }}</small><small class="mono">{{ formatValidation(snapshot.validationResult) }}</small></div>
      </section>
      <section class="pin-evidence">
        <h4>{{ $t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstPins' : 'lesson.publishSourcePins') }}</h4>
        <div v-for="asset in snapshot.originalAssets" :key="`${asset.profile}:${asset.assetKey}`" class="pin-row mono">
          <span>{{ asset.profile }} / {{ asset.assetKey }}</span><span>{{ asset.sha256 }} · {{ asset.bytes == null ? '?' : asset.bytes }} B</span>
        </div>
        <p v-if="!snapshot.originalAssets.length">—</p>
      </section>
      <el-checkbox v-model="acknowledged" :disabled="publishing || locked" data-testid="immutable-ack">
        {{ $t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstAck' : 'lesson.publishImmutableAck', { source: snapshot.originalVersion, target: snapshot.targetVersion }) }}
      </el-checkbox>
      <el-alert v-if="result" :title="result.title" :type="result.type" :closable="false" show-icon class="result-alert">
        <div v-if="result.originalComparison">
          <strong>{{ result.originalComparison.pass
            ? $t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstSourcePass' : 'lesson.publishOriginalPass')
            : $t(snapshot.sourceMode === 'first-publish' ? 'lesson.publishFirstSourceFail' : 'lesson.publishOriginalFail') }}</strong>
          <ul v-if="result.originalComparison.differences.length"><li v-for="(item, index) in result.originalComparison.differences" :key="index">{{ formatDifference(item) }}</li></ul>
        </div>
        <div v-if="result.targetComparison">
          <strong>{{ result.targetComparison.pass ? $t('lesson.publishTargetPass') : $t('lesson.publishTargetFail') }}</strong>
          <ul v-if="result.targetComparison.differences.length"><li v-for="(item, index) in result.targetComparison.differences" :key="index">{{ formatDifference(item) }}</li></ul>
        </div>
        <div v-if="result.targetEvidence" class="target-result mono">{{ formatTarget(result.targetEvidence) }}</div>
      </el-alert>
    </template>
    <span slot="footer">
      <el-button :disabled="publishing || locked" @click="close">{{ $t('lesson.cancel') }}</el-button>
      <el-button v-if="locked" type="primary" :loading="reconciling" :disabled="reconciling" @click="$emit('reconcile')">
        {{ reconciling ? $t('lesson.publishReconciling') : $t('lesson.publishRetryReconciliation') }}
      </el-button>
      <el-button v-else type="primary" :loading="publishing" :disabled="!acknowledged || publishing || (!!result && !result.retryAllowed)" @click="confirmPublish">
        {{ $t('lesson.publishReviewedVersion') }}
      </el-button>
    </span>
  </el-dialog>
</template>

<script>
export default {
  name: 'LessonPublishReviewDialog',
  props: {
    visible: { type: Boolean, default: false },
    snapshot: { type: Object, default: null },
    publishing: { type: Boolean, default: false },
    locked: { type: Boolean, default: false },
    reconciling: { type: Boolean, default: false },
    result: { type: Object, default: null },
  },
  data() { return { acknowledged: false }; },
  computed: {
    originalChecksum() { return this.snapshot ? this.snapshot.originalChecksum : ''; },
    previewChecksum() { return this.snapshot ? this.snapshot.previewChecksum : ''; },
  },
  watch: {
    visible(value) { if (!value) this.acknowledged = false; },
    snapshot() { this.acknowledged = false; },
  },
  methods: {
    close() { if (!this.publishing && !this.locked) this.$emit('update:visible', false); },
    confirmPublish() { if (this.acknowledged && !this.publishing && !this.locked && (!this.result || this.result.retryAllowed)) this.$emit('publish', this.snapshot); },
    formatBytes(bytes) { const n = Number(bytes || 0); return n < 1024 ? `${n} B` : `${(n / 1048576).toFixed(2)} MiB`; },
    formatTarget(target) { return this.$t('lesson.publishTargetSummary', { version: target.lessonVersion || '?', checksum: target.checksum || this.$t('lesson.publishChecksumUnavailable'), count: target.assetCount || 0 }); },
    formatCompletion(event) { return event ? `${event.stepKey || 'lesson'} / ${event.action || 'completed'}` : this.$t('lesson.publishCompletionUnavailable'); },
    formatValidation(result) { return JSON.stringify(result); },
    formatDifference(item) { return item && item.key ? this.$t(item.key, item.params || {}) : String(item || ''); },
  },
};
</script>

<style scoped>
.evidence-grid { display:grid; gap:14px; grid-template-columns:1fr 1fr; margin-top:16px; }
.evidence-grid section,.proof-strip>div { background:#f4f7f5; border:1px solid #dbe5e0; border-radius:12px; padding:14px; }
h4 { color:#17312d; margin:0 0 10px; } dl { display:grid; grid-template-columns:90px minmax(0,1fr); margin:0; } dt,dd { border-bottom:1px solid #e2e8e5; margin:0; padding:5px 0; } dt { color:#71827b; }
.proof-strip { display:grid; gap:12px; grid-template-columns:1fr 1fr; margin:14px 0; }.proof-strip>div { display:grid; gap:4px; }.proof-strip span { color:#71827b; font-size:11px; text-transform:uppercase; }
.pin-evidence { background:#fffaf0; border:1px solid #eadcb9; border-radius:12px; margin-bottom:14px; max-height:180px; overflow:auto; padding:14px; }.pin-row { display:grid; gap:8px; grid-template-columns:minmax(150px,1fr) minmax(220px,1fr); padding:5px 0; }
.mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; overflow-wrap:anywhere; }.result-alert { margin-top:14px; }.target-result { margin-top:8px; }
@media(max-width:760px){.evidence-grid,.proof-strip{grid-template-columns:1fr}}
</style>
