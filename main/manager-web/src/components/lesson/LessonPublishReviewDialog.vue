<template>
  <el-dialog
    :title="$t('lesson.publishReviewTitle')"
    :visible="visible"
    width="760px"
    :close-on-click-modal="!publishing"
    :close-on-press-escape="!publishing"
    :show-close="!publishing"
    @close="close"
  >
    <template v-if="snapshot">
      <el-alert :title="$t('lesson.publishImmutableWarning')" type="warning" :closable="false" show-icon />
      <div class="evidence-grid">
        <section>
          <h4>{{ $t('lesson.publishSourceEvidence') }}</h4>
          <dl>
            <dt>Lesson</dt><dd class="mono">{{ snapshot.originalLessonId }}</dd>
            <dt>Version</dt><dd>v{{ snapshot.originalVersion }}</dd>
            <dt>Checksum</dt><dd class="mono">{{ originalChecksum }}</dd>
            <dt>Pins</dt><dd>{{ snapshot.originalAssets.length }}</dd>
            <dt>Bytes</dt><dd>{{ formatBytes(snapshot.originalBytes) }}</dd>
          </dl>
        </section>
        <section>
          <h4>{{ $t('lesson.publishTargetEvidence') }}</h4>
          <dl>
            <dt>Version</dt><dd>v{{ snapshot.targetVersion }}</dd>
            <dt>Steps</dt><dd>{{ snapshot.stepCount }}</dd>
            <dt>Assets</dt><dd>{{ snapshot.assetCount }}</dd>
            <dt>Profile</dt><dd>{{ snapshot.previewProfile }}</dd>
            <dt>Stage</dt><dd>{{ snapshot.previewWidth }}×{{ snapshot.previewHeight }}</dd>
            <dt>Checksum</dt><dd class="mono">{{ previewChecksum }}</dd>
            <dt>ETag</dt><dd class="mono">{{ snapshot.previewEtag }}</dd>
          </dl>
        </section>
      </div>
      <section class="proof-strip">
        <div><span>{{ $t('lesson.publishSimulationEvidence') }}</span><strong>{{ snapshot.simulationTerminationReason }}</strong><small class="mono">{{ snapshot.simulationChecksum }}</small><small class="mono">{{ snapshot.simulationEtag }} · proof {{ snapshot.proofVersion }}</small><small class="mono">{{ formatCompletion(snapshot.simulationCompletionEvent) }}</small></div>
        <div><span>{{ $t('lesson.serverValidation') }}</span><strong>{{ snapshot.validationResult.valid ? 'PASS' : 'FAIL' }}</strong><small>{{ snapshot.validationProfiles.join(', ') || '—' }}</small><small class="mono">{{ formatValidation(snapshot.validationResult) }}</small></div>
      </section>
      <section class="pin-evidence">
        <h4>{{ $t('lesson.publishSourcePins') }}</h4>
        <div v-for="asset in snapshot.originalAssets" :key="`${asset.profile}:${asset.assetKey}`" class="pin-row mono">
          <span>{{ asset.profile }} / {{ asset.assetKey }}</span><span>{{ asset.sha256 }} · {{ asset.bytes == null ? '?' : asset.bytes }} B</span>
        </div>
        <p v-if="!snapshot.originalAssets.length">—</p>
      </section>
      <el-checkbox v-model="acknowledged" :disabled="publishing" data-testid="immutable-ack">
        {{ $t('lesson.publishImmutableAck', { source: snapshot.originalVersion, target: snapshot.targetVersion }) }}
      </el-checkbox>
      <el-alert v-if="result" :title="result.title" :type="result.type" :closable="false" show-icon class="result-alert">
        <div v-if="result.originalComparison">
          <strong>{{ result.originalComparison.pass ? 'ORIGINAL IMMUTABILITY: PASS' : 'ORIGINAL IMMUTABILITY: FAIL' }}</strong>
          <ul v-if="result.originalComparison.differences.length"><li v-for="item in result.originalComparison.differences" :key="item">{{ item }}</li></ul>
        </div>
        <div v-if="result.targetEvidence" class="target-result mono">{{ formatTarget(result.targetEvidence) }}</div>
      </el-alert>
    </template>
    <span slot="footer">
      <el-button :disabled="publishing" @click="close">{{ $t('lesson.cancel') }}</el-button>
      <el-button type="primary" :loading="publishing" :disabled="!acknowledged || publishing || !!result" @click="confirmPublish">
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
    close() { if (!this.publishing) this.$emit('update:visible', false); },
    confirmPublish() { if (this.acknowledged && !this.publishing && !this.result) this.$emit('publish', this.snapshot); },
    formatBytes(bytes) { const n = Number(bytes || 0); return n < 1024 ? `${n} B` : `${(n / 1048576).toFixed(2)} MiB`; },
    formatTarget(target) { return `v${target.lessonVersion || '?'} · ${target.checksum || 'checksum unavailable'} · ${target.assetCount || 0} assets`; },
    formatCompletion(event) { return event ? `${event.stepKey || 'lesson'} / ${event.action || 'completed'}` : 'completion event unavailable'; },
    formatValidation(result) { return JSON.stringify(result); },
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
