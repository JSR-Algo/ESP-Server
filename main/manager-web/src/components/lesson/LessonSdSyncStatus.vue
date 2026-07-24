<template>
  <el-card v-if="status" shadow="never" class="sd-sync-status" data-testid="lesson-sd-sync-status">
    <div slot="header" class="sd-sync-status__header">
      <div>
        <strong>{{ $t('lesson.sdSyncTitle') }}</strong>
        <p class="sd-sync-status__hint">{{ availabilityText }}</p>
      </div>
      <div class="sd-sync-status__actions">
        <el-tag :type="stateTagType" size="small" effect="plain">
          {{ stateLabel }}
        </el-tag>
        <el-button
          size="small"
          type="primary"
          icon="el-icon-refresh"
          :loading="retrying"
          :disabled="retryDisabled"
          :aria-label="$t('lesson.sdSyncRetry')"
          @click="$emit('retry', retryDeviceIds)"
        >
          {{ $t('lesson.sdSyncRetry') }}
        </el-button>
      </div>
    </div>

    <div class="sd-sync-status__counts" :aria-label="$t('lesson.sdSyncCounters')">
      <div v-for="item in counters" :key="item.key" class="sd-sync-status__count">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
      </div>
    </div>

    <div class="sd-sync-status__meta">
      <div class="kv"><span class="muted">{{ $t('lesson.sdSyncVersion') }}</span><span class="mono">{{ displayValue(status.version) }}</span></div>
      <div class="kv"><span class="muted">{{ $t('lesson.sdSyncChecksum') }}</span><span class="mono">{{ displayValue(status.checksum) }}</span></div>
      <div class="kv"><span class="muted">{{ $t('lesson.sdSyncLastSuccess') }}</span><span>{{ formatTimestamp(status.lastSuccessAt) }}</span></div>
      <div class="kv"><span class="muted">{{ $t('lesson.sdSyncLastError') }}</span><span>{{ formatTimestamp(status.lastErrorAt) }}</span></div>
    </div>

    <el-collapse class="sd-sync-status__devices">
      <el-collapse-item :title="$t('lesson.sdSyncDevices')" name="devices">
        <el-table :data="status.devices" size="mini" stripe style="width: 100%">
          <el-table-column prop="deviceId" :label="$t('lesson.sdSyncDevice')" min-width="150" />
          <el-table-column :label="$t('lesson.sdSyncState')" width="130">
            <template slot-scope="scope">
              <el-tag :type="deviceTagType(scope.row.state)" size="mini" effect="plain">
                {{ labelForState(scope.row.state) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="version" :label="$t('lesson.sdSyncVersion')" width="90">
            <template slot-scope="scope">{{ displayValue(scope.row.version) }}</template>
          </el-table-column>
          <el-table-column :label="$t('lesson.sdSyncChecksum')" min-width="180">
            <template slot-scope="scope"><span class="mono">{{ displayValue(scope.row.checksum) }}</span></template>
          </el-table-column>
          <el-table-column :label="$t('lesson.sdSyncLastSuccess')" min-width="160">
            <template slot-scope="scope">{{ formatTimestamp(scope.row.lastSuccessAt) }}</template>
          </el-table-column>
          <el-table-column :label="$t('lesson.sdSyncLastError')" min-width="160">
            <template slot-scope="scope">{{ formatTimestamp(scope.row.lastErrorAt) }}</template>
          </el-table-column>
          <el-table-column prop="error" :label="$t('lesson.sdSyncError')" min-width="180">
            <template slot-scope="scope">{{ displayValue(scope.row.error) }}</template>
          </el-table-column>
          <template slot="empty"><span class="muted">{{ $t('lesson.sdSyncNoDevices') }}</span></template>
        </el-table>
      </el-collapse-item>
    </el-collapse>
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
    total() { return Number(this.status && this.status.total) || 0; },
    offlineReady() {
      if (!this.status) return false;
      const { complete, total } = this.status;
      return complete === total && total > 0;
    },
    retryDisabled() {
      return this.loading || this.retrying || !this.status || this.status.state === 'complete';
    },
    retryDeviceIds() {
      if (!this.status || !Array.isArray(this.status.devices)) return undefined;
      const ids = this.status.devices
        .filter((device) => device.state !== 'complete')
        .map((device) => device.deviceId)
        .filter(Boolean);
      return ids.length ? ids : undefined;
    },
    stateTagType() {
      return this.deviceTagType(this.status.state);
    },
    stateLabel() {
      return this.labelForState(this.status.state);
    },
    availabilityText() {
      if (this.offlineReady) return this.$t('lesson.sdSyncOfflineAvailable');
      if (!this.total) return this.$t('lesson.sdSyncNoDevices');
      return this.$t('lesson.sdSyncNotOfflineReady');
    },
    counters() {
      return [
        { key: 'total', label: this.$t('lesson.sdSyncTotal'), value: this.status.total },
        { key: 'complete', label: this.$t('lesson.sdSyncComplete'), value: this.status.complete },
        { key: 'syncing', label: this.$t('lesson.sdSyncSyncing'), value: this.status.syncing },
        { key: 'offlinePending', label: this.$t('lesson.sdSyncOfflinePending'), value: this.status.offlinePending },
        { key: 'failed', label: this.$t('lesson.sdSyncFailed'), value: this.status.failed },
        { key: 'remaining', label: this.$t('lesson.sdSyncRemaining'), value: Math.max(0, this.status.total - this.status.complete) },
      ];
    },
  },
  methods: {
    labelForState(state) {
      const key = {
        complete: 'lesson.sdSyncComplete',
        syncing: 'lesson.sdSyncSyncing',
        offline_pending: 'lesson.sdSyncOfflinePending',
        failed: 'lesson.sdSyncFailed',
      }[state] || 'lesson.sdSyncUnknown';
      return this.$t(key);
    },
    deviceTagType(state) {
      if (state === 'complete') return 'success';
      if (state === 'failed') return 'danger';
      if (state === 'offline_pending') return 'warning';
      return 'info';
    },
    displayValue(value) {
      return value === null || value === undefined || value === '' ? this.$t('lesson.sdSyncUnavailable') : value;
    },
    formatTimestamp(value) {
      if (!value) return this.$t('lesson.sdSyncUnavailable');
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return this.$t('lesson.sdSyncUnavailable');
      return date.toLocaleString();
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
  flex-wrap: wrap;
  gap: 8px;
  justify-content: flex-end;
}
.sd-sync-status__counts {
  display: grid;
  gap: 8px;
  grid-template-columns: repeat(6, minmax(88px, 1fr));
  margin-bottom: 12px;
}
.sd-sync-status__count {
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  min-width: 0;
  padding: 8px;
}
.sd-sync-status__count span {
  color: #909399;
  display: block;
  font-size: 12px;
}
.sd-sync-status__count strong {
  color: #303133;
  display: block;
  font-size: 18px;
  margin-top: 3px;
}
.sd-sync-status__meta {
  display: grid;
  gap: 6px 16px;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  margin-bottom: 8px;
}
.kv {
  display: flex;
  gap: 8px;
  min-width: 0;
}
.kv .muted {
  color: #909399;
  flex: 0 0 120px;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  word-break: break-all;
}
@media (max-width: 900px) {
  .sd-sync-status__counts {
    grid-template-columns: repeat(3, minmax(88px, 1fr));
  }
  .sd-sync-status__meta {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 600px) {
  .sd-sync-status__header {
    align-items: flex-start;
    flex-direction: column;
  }
  .sd-sync-status__actions {
    justify-content: flex-start;
  }
  .sd-sync-status__counts {
    grid-template-columns: repeat(2, minmax(88px, 1fr));
  }
}
</style>
