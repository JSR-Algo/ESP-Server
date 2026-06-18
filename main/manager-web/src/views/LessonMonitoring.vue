<template>
  <div class="welcome">
    <HeaderBar />
    <div class="operation-bar">
      <div class="left-title">
        <h2 class="page-title">{{ $t('monitoring.pageTitle') }}</h2>
      </div>
      <div class="right-operations">
        <span class="backend-hint">{{ $t('monitoring.backendHint') }}</span>
      </div>
    </div>

    <div class="main-wrapper">
      <el-card class="content-area" shadow="never">
        <div class="filter-row">
          <el-input
            v-model="filters.deviceId"
            :placeholder="$t('monitoring.filterDeviceId')"
            size="small"
            clearable
            class="filter-input"
            @keyup.enter.native="fetchList"
          />
          <el-input
            v-model="filters.childId"
            :placeholder="$t('monitoring.filterChildId')"
            size="small"
            clearable
            class="filter-input"
            @keyup.enter.native="fetchList"
          />
          <el-input
            v-model="filters.lessonId"
            :placeholder="$t('monitoring.filterLessonId')"
            size="small"
            clearable
            class="filter-input"
            @keyup.enter.native="fetchList"
          />
          <el-select
            v-model="filters.state"
            :placeholder="$t('monitoring.filterState')"
            size="small"
            clearable
            class="filter-select"
          >
            <el-option
              v-for="s in states"
              :key="s"
              :label="s"
              :value="s"
            />
          </el-select>
          <el-button type="primary" size="small" :loading="loading" @click="fetchList">
            {{ $t('monitoring.refresh') }}
          </el-button>
          <span
            class="monitoring-count"
            :class="{ 'is-capped': list.length >= limit }"
            :title="list.length >= limit ? $t('monitoring.capHint') : ''"
          >{{ list.length }}</span>
        </div>

        <el-table v-loading="loading" :data="list" stripe style="width: 100%">
          <el-table-column prop="lessonTitle" :label="$t('monitoring.colLesson')" min-width="160">
            <template slot-scope="scope">
              <span v-if="scope.row.lessonTitle">{{ scope.row.lessonTitle }}</span>
              <span v-else class="muted">{{ scope.row.lessonId }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitoring.colState')" width="130">
            <template slot-scope="scope">
              <el-tag :type="stateType(scope.row.state)" size="small">{{ scope.row.state }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="deviceId" :label="$t('monitoring.colDevice')" min-width="140" show-overflow-tooltip />
          <el-table-column prop="childId" :label="$t('monitoring.colChild')" min-width="140" show-overflow-tooltip />
          <el-table-column :label="$t('monitoring.colSteps')" width="100" align="center">
            <template slot-scope="scope">
              {{ scope.row.stepsSucceeded }}/{{ scope.row.stepsCompleted }}
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitoring.colStartedAt')" width="170">
            <template slot-scope="scope">
              <span v-if="scope.row.startedAt">{{ formatTime(scope.row.startedAt) }}</span>
              <span v-else class="muted small">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitoring.colCompletedAt')" width="170">
            <template slot-scope="scope">
              <span v-if="scope.row.completedAt">{{ formatTime(scope.row.completedAt) }}</span>
              <span v-else class="muted small">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('monitoring.colActions')" width="100">
            <template slot-scope="scope">
              <el-button type="text" size="small" @click="openEvents(scope.row)">
                {{ $t('monitoring.view') }}
              </el-button>
            </template>
          </el-table-column>
          <template slot="empty">
            <span class="muted">{{ $t('monitoring.empty') }}</span>
          </template>
        </el-table>
      </el-card>
    </div>

    <el-dialog
      :title="$t('monitoring.eventsTitle')"
      :visible.sync="eventsVisible"
      width="640px"
      @closed="resetEvents"
    >
      <p class="muted small dialog-sub">
        {{ eventsSource.lessonTitle || eventsSource.lessonId }}
      </p>
      <el-table v-loading="eventsLoading" :data="events" stripe size="small" style="width: 100%">
        <el-table-column prop="eventType" :label="$t('monitoring.evType')" min-width="160" />
        <el-table-column :label="$t('monitoring.evStep')" width="120">
          <template slot-scope="scope">
            <span v-if="scope.row.stepId">{{ scope.row.stepId }}</span>
            <span v-else class="muted small">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitoring.evOutcome')" width="110">
          <template slot-scope="scope">
            <el-tag v-if="scope.row.outcome" :type="outcomeType(scope.row.outcome)" size="small" effect="plain">
              {{ scope.row.outcome }}
            </el-tag>
            <span v-else class="muted small">—</span>
          </template>
        </el-table-column>
        <el-table-column :label="$t('monitoring.evOccurredAt')" width="180">
          <template slot-scope="scope">
            {{ formatTime(scope.row.occurredAt) }}
          </template>
        </el-table-column>
        <template slot="empty">
          <span class="muted">{{ $t('monitoring.evEmpty') }}</span>
        </template>
      </el-table>
      <span slot="footer">
        <el-button size="small" @click="eventsVisible = false">{{ $t('monitoring.close') }}</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import Api from '@/apis/api';

export default {
  name: 'LessonMonitoring',
  components: { HeaderBar },
  data() {
    return {
      list: [],
      loading: false,
      limit: 200, // backend caps at 200; raised from the silent default of 50
      filters: { deviceId: '', childId: '', lessonId: '', state: '' },
      states: [
        'ASSIGNED',
        'PRELOADING',
        'READY',
        'RUNNING',
        'COMPLETED',
        'PAUSED',
        'FAILED',
        'CANCELLED',
      ],
      eventsVisible: false,
      eventsLoading: false,
      events: [],
      eventsSource: {},
    };
  },
  created() {
    if (this.$route.query.lessonId) {
      this.filters.lessonId = String(this.$route.query.lessonId);
    }
    this.fetchList();
  },
  methods: {
    stateType(state) {
      if (state === 'COMPLETED') return 'success';
      if (state === 'RUNNING' || state === 'READY') return 'primary';
      if (state === 'FAILED') return 'danger';
      if (state === 'CANCELLED') return 'info';
      return 'warning';
    },
    outcomeType(outcome) {
      if (outcome === 'success') return 'success';
      if (outcome === 'miss') return 'danger';
      if (outcome === 'timeout') return 'warning';
      return 'info';
    },
    formatTime(value) {
      if (!value) return '—';
      const d = new Date(value);
      if (Number.isNaN(d.getTime())) return value;
      return d.toLocaleString();
    },
    fetchList() {
      this.loading = true;
      Api.monitoring.listAssignments(
        {
          deviceId: this.filters.deviceId.trim(),
          childId: this.filters.childId.trim(),
          lessonId: this.filters.lessonId.trim(),
          state: this.filters.state,
          limit: this.limit,
        },
        (rows) => {
          this.loading = false;
          this.list = rows;
        },
        (msg) => {
          this.loading = false;
          this.$message.error(msg || this.$t('monitoring.loadFail'));
        },
      );
    },
    openEvents(row) {
      this.eventsSource = row;
      this.eventsVisible = true;
      this.eventsLoading = true;
      this.events = [];
      Api.monitoring.getAssignmentEvents(
        row.assignmentId,
        (rows) => {
          this.eventsLoading = false;
          this.events = rows;
        },
        (msg) => {
          this.eventsLoading = false;
          this.$message.error(msg || this.$t('monitoring.eventsLoadFail'));
        },
      );
    },
    resetEvents() {
      this.events = [];
      this.eventsSource = {};
    },
  },
};
</script>

<style lang="scss" scoped>
.operation-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 24px 0;
}
.left-title {
  display: flex;
  align-items: center;
  gap: 16px;
}
.page-title {
  margin: 0;
  font-size: 18px;
}
.small {
  font-size: 12px;
}
.right-operations {
  display: flex;
  align-items: center;
  gap: 10px;
}
.backend-hint {
  color: #909399;
  font-size: 12px;
}
.main-wrapper {
  padding: 16px 24px;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}
.filter-input {
  width: 200px;
}
.filter-select {
  width: 160px;
}
.dialog-sub {
  margin-top: 0;
}
.muted {
  color: #909399;
}
.monitoring-count {
  margin-left: 12px;
  font-size: 13px;
  color: #909399;
}
.monitoring-count.is-capped {
  color: #e6a23c;
  cursor: help;
  font-weight: 600;
}
</style>
