<template>
  <el-card
    v-if="status"
    v-loading="loading"
    shadow="never"
    class="sd-sync-status"
    data-testid="lesson-sd-sync-status"
    :aria-label="$t('lesson.sdSyncTitle')"
  >
    <div slot="header" class="sd-sync-status__header">
      <div>
        <strong>{{ $t('lesson.sdSyncTitle') }}</strong>
        <p class="sd-sync-status__hint">{{ stateDescription }}</p>
      </div>
      <div class="sd-sync-status__actions">
        <el-tag :type="stateTagType" size="small" effect="plain">
          {{ stateLabel }}
        </el-tag>
        <el-button
          v-if="retryAvailable"
          type="primary"
          size="small"
          :loading="retrying"
          :disabled="retrying"
          :aria-label="$t('lesson.sdSyncRetryAction')"
          @click="$emit('retry')"
        >
          {{ $t('lesson.sdSyncRetryAction') }}
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="status.allConnectedCurrent"
      class="sd-sync-status__success"
      type="success"
      :closable="false"
      show-icon
      :title="$t('lesson.sdSyncAllConnectedCurrent')"
    />

    <div class="sd-sync-status__counts" :aria-label="$t('lesson.sdSyncCounters')">
      <div v-for="item in counters" :key="item.key" class="sd-sync-status__count">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </div>
    </div>

    <div class="sd-sync-status__meta" :aria-label="$t('lesson.sdSyncMetadata')">
      <div class="kv">
        <span class="muted">{{ $t('lesson.sdSyncBuildState') }}</span>
        <span>{{ buildStateLabel }}</span>
      </div>
      <div class="kv">
        <span class="muted">{{ $t('lesson.sdSyncMaterializationState') }}</span>
        <span>{{ materializationStateLabel }}</span>
      </div>
      <div class="kv">
        <span class="muted">{{ $t('lesson.sdSyncLastBuild') }}</span>
        <span>{{ formatTimestamp(status.lastBuildAt) }}</span>
      </div>
      <div class="kv">
        <span class="muted">{{ $t('lesson.sdSyncLastPoll') }}</span>
        <span>{{ formatTimestamp(status.lastPollAt) }}</span>
      </div>
      <div class="kv">
        <span class="muted">{{ $t('lesson.sdSyncLastMaterialized') }}</span>
        <span>{{ formatTimestamp(status.lastMaterializedAt) }}</span>
      </div>
      <div class="kv">
        <span class="muted">{{ $t('lesson.sdSyncLastErrorCode') }}</span>
        <span>{{ displayValue(status.lastErrorCode) }}</span>
      </div>
    </div>

    <div class="sd-sync-status__disclaimer" role="note">
      <i class="el-icon-info" aria-hidden="true" />
      <span>{{ $t('lesson.sdSyncOfflineDisclaimer') }}</span>
    </div>
  </el-card>
</template>

<script>
export default {
  name: 'LessonSdSyncStatus',
  props: {
    status: { type: Object, default: null },
    loading: { type: Boolean, default: false },
    retrying: { type: Boolean, default: false },
  },
  computed: {
    counters() {
      return [
        { key: 'generation', label: this.$t('lesson.sdSyncGeneration'), value: this.status.generation },
        { key: 'curriculumLessonCount', label: this.$t('lesson.sdSyncCurriculumLessons'), value: this.status.curriculumLessonCount },
        { key: 'packCount', label: this.$t('lesson.sdSyncTotalPacks'), value: this.status.packCount },
        { key: 'connected', label: this.$t('lesson.sdSyncConnected'), value: this.status.connected },
        { key: 'current', label: this.$t('lesson.sdSyncCurrent'), value: this.status.current },
        { key: 'retrying', label: this.$t('lesson.sdSyncRetrying'), value: this.status.retrying },
        { key: 'failed', label: this.$t('lesson.sdSyncFailed'), value: this.status.failed },
      ];
    },
    stateKey() {
      if (this.status.allConnectedCurrent) return 'AllCurrent';
      if (this.status.buildState === 'failed' || this.status.failed > 0) return 'Failed';
      if (this.status.connected === 0) return 'NoConnected';
      if (['pending', 'building'].includes(this.status.buildState)) return 'Building';
      if (this.status.materializationState === 'materializing') return 'Materializing';
      if (this.status.retrying > 0 || this.status.materializationState === 'retry_wait') return 'Retrying';
      if (['empty', 'polling'].includes(this.status.materializationState)) return 'Polling';
      if (this.status.current < this.status.connected) return 'RollingOut';
      return 'GenerationMismatch';
    },
    stateLabel() {
      return this.$t(`lesson.sdSyncState${this.stateKey}`);
    },
    stateDescription() {
      return this.$t(`lesson.sdSyncState${this.stateKey}Description`);
    },
    stateTagType() {
      if (this.stateKey === 'AllCurrent') return 'success';
      if (this.stateKey === 'Failed') return 'danger';
      if (['Building', 'Materializing', 'Retrying', 'RollingOut'].includes(this.stateKey)) return 'warning';
      return 'info';
    },
    retryAvailable() {
      return ['Failed', 'Retrying', 'GenerationMismatch', 'RollingOut'].includes(this.stateKey);
    },
    buildStateLabel() {
      const label = this.$t(`lesson.sdSyncBuild${this.capitalize(this.status.buildState)}`);
      return this.status.pendingCount > 0
        ? this.$t('lesson.sdSyncBuildWithPending', { state: label, count: this.status.pendingCount })
        : label;
    },
    materializationStateLabel() {
      return this.$t(`lesson.sdSyncMaterialization${this.pascalCase(this.status.materializationState)}`);
    },
  },
  methods: {
    capitalize(value) {
      return value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : '';
    },
    pascalCase(value) {
      return String(value || '').split('_').map(this.capitalize).join('');
    },
    displayValue(value) {
      return value === null || value === undefined || value === '' ? this.$t('lesson.sdSyncUnavailable') : value;
    },
    formatTimestamp(value) {
      if (!value) return this.$t('lesson.sdSyncUnavailable');
      const date = new Date(value);
      return Number.isNaN(date.getTime()) ? this.$t('lesson.sdSyncUnavailable') : date.toLocaleString();
    },
  },
};
</script>

<style lang="scss" scoped>
.sd-sync-status {
  margin-bottom: 16px;
}
.sd-sync-status__header {
  align-items: center;
  display: flex;
  gap: 12px;
  justify-content: space-between;
}
.sd-sync-status__hint {
  color: #606266;
  font-size: 12px;
  line-height: 1.4;
  margin: 4px 0 0;
}
.sd-sync-status__actions {
  align-items: center;
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
}
.sd-sync-status__success {
  margin-bottom: 12px;
}
.sd-sync-status__counts {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(7, minmax(88px, 1fr));
  margin-bottom: 14px;
}
.sd-sync-status__count {
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  min-width: 0;
  padding: 9px;
}
.sd-sync-status__count span {
  color: #909399;
  display: block;
  font-size: 12px;
  line-height: 1.3;
}
.sd-sync-status__count strong {
  color: #303133;
  display: block;
  font-size: 18px;
  margin-top: 4px;
}
.sd-sync-status__meta {
  display: grid;
  gap: 8px 20px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-bottom: 14px;
}
.kv {
  display: flex;
  gap: 8px;
  min-width: 0;
}
.kv .muted {
  color: #909399;
  flex: 0 0 150px;
}
.sd-sync-status__disclaimer {
  align-items: flex-start;
  background: #f4f4f5;
  border-radius: 4px;
  color: #606266;
  display: flex;
  font-size: 12px;
  gap: 7px;
  line-height: 1.5;
  padding: 9px 11px;
}
.sd-sync-status__disclaimer i {
  margin-top: 2px;
}
@media (max-width: 1100px) {
  .sd-sync-status__counts {
    grid-template-columns: repeat(4, minmax(88px, 1fr));
  }
}
@media (max-width: 700px) {
  .sd-sync-status__counts,
  .sd-sync-status__meta {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .kv {
    display: block;
  }
  .kv .muted {
    display: block;
    margin-bottom: 2px;
  }
}
@media (max-width: 460px) {
  .sd-sync-status__header {
    align-items: flex-start;
    flex-direction: column;
  }
  .sd-sync-status__actions {
    justify-content: space-between;
    width: 100%;
  }
  .sd-sync-status__meta {
    grid-template-columns: 1fr;
  }
}
</style>
