<template>
  <div class="welcome">
    <HeaderBar />
    <div class="operation-bar">
      <div class="left-title">
        <el-button type="text" icon="el-icon-arrow-left" @click="$router.back()">
          {{ $t('course.pageTitle') }}
        </el-button>
        <h2 class="page-title">{{ $t('lesson.pageTitle') }} · {{ courseTitle }}</h2>
      </div>
      <div class="right-operations">
        <el-button type="primary" size="small" @click="openCreate">
          {{ $t('lesson.createBtn') }}
        </el-button>
        <el-button size="small" :loading="loading" @click="fetchList">
          {{ $t('course.refresh') }}
        </el-button>
      </div>
    </div>

    <div class="main-wrapper">
      <div class="lesson-stats">
        <div class="stat-item">
          <span class="stat-label">{{ $t('lesson.statTotal') }}</span>
          <strong>{{ list.length }}</strong>
        </div>
        <div class="stat-item">
          <span class="stat-label">{{ $t('lesson.statMonitorable') }}</span>
          <strong>{{ monitorableCount }}</strong>
        </div>
        <div class="stat-item">
          <span class="stat-label">{{ $t('lesson.statPersonalized') }}</span>
          <strong>{{ personalizedCount }}</strong>
        </div>
        <div class="stat-item">
          <span class="stat-label">{{ $t('lesson.statDuration') }}</span>
          <strong>{{ totalDurationLabel }}</strong>
        </div>
      </div>

      <el-card class="content-area" shadow="never">
        <div class="filter-row">
          <el-input
            v-model="filters.keyword"
            :placeholder="$t('lesson.filterKeyword')"
            size="small"
            clearable
            class="filter-input wide"
          />
          <el-select v-model="filters.status" :placeholder="$t('lesson.filterStatus')" size="small" clearable class="filter-select">
            <el-option v-for="s in statuses" :key="s" :label="s" :value="s" />
          </el-select>
          <el-select v-model="filters.lessonType" :placeholder="$t('lesson.filterType')" size="small" clearable class="filter-select">
            <el-option v-for="t in lessonTypeOptions" :key="t" :label="t" :value="t" />
          </el-select>
          <el-select v-model="filters.topic" :placeholder="$t('lesson.filterPersonality')" size="small" clearable filterable class="filter-select">
            <el-option v-for="tag in topicOptions" :key="tag" :label="tag" :value="tag" />
          </el-select>
          <el-select v-model="filters.difficultyBand" :placeholder="$t('lesson.filterDifficulty')" size="small" clearable class="filter-select">
            <el-option v-for="d in difficultyOptions" :key="d" :label="d" :value="d" />
          </el-select>
          <el-select v-model="filters.monitorable" :placeholder="$t('lesson.filterMonitorable')" size="small" clearable class="filter-select small-select">
            <el-option :label="$t('lesson.monitorableYes')" value="yes" />
            <el-option :label="$t('lesson.monitorableNo')" value="no" />
          </el-select>
          <el-button size="small" @click="resetFilters">{{ $t('lesson.clearFilters') }}</el-button>
          <span class="filter-count">{{ filteredList.length }}/{{ list.length }}</span>
        </div>

        <el-table v-loading="loading" :data="filteredList" stripe style="width: 100%">
          <el-table-column prop="lessonKey" :label="$t('lesson.colKey')" min-width="190" show-overflow-tooltip />
          <el-table-column prop="title" :label="$t('lesson.colTitle')" min-width="180" show-overflow-tooltip />
          <el-table-column prop="lessonVersion" :label="$t('lesson.colVersion')" width="90" align="center" />
          <el-table-column :label="$t('lesson.colStatus')" width="120">
            <template slot-scope="scope">
              <el-tag :type="statusType(scope.row.status)" size="small">{{ scope.row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colLessonType')" width="110">
            <template slot-scope="scope">
              <el-tag size="small" effect="plain">{{ scope.row.lessonType || 'lesson' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colPersonalityTags')" min-width="190">
            <template slot-scope="scope">
              <el-tag
                v-for="tag in scope.row.topicTags"
                :key="tag"
                size="mini"
                class="topic-tag"
                effect="plain"
              >{{ tag }}</el-tag>
              <span v-if="!scope.row.topicTags.length" class="muted small">{{ $t('lesson.noTags') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colDifficulty')" width="130">
            <template slot-scope="scope">
              <span v-if="scope.row.difficultyBand">{{ scope.row.difficultyBand }}</span>
              <span v-else class="muted small">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colDuration')" width="120" align="right">
            <template slot-scope="scope">
              <span v-if="scope.row.estimatedDurationSec">{{ durationLabel(scope.row.estimatedDurationSec) }}</span>
              <span v-else class="muted small">—</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colMonitorable')" width="120" align="center">
            <template slot-scope="scope">
              <el-tag :type="scope.row.monitorable ? 'success' : 'info'" size="small" effect="plain">
                {{ scope.row.monitorable ? $t('lesson.monitorableYes') : $t('lesson.monitorableNo') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colActions')" width="330" fixed="right">
            <template slot-scope="scope">
              <el-button type="text" size="small" @click="openEditor(scope.row)">
                {{ $t('lesson.editSteps') }}
              </el-button>
              <el-button v-if="scope.row.status === 'draft'" type="text" size="small" @click="openMetadata(scope.row)">
                {{ $t('lesson.editMetadata') }}
              </el-button>
              <el-button type="text" size="small" @click="openMonitoring(scope.row)">
                {{ $t('lesson.monitor') }}
              </el-button>
              <el-tooltip
                v-if="scope.row.status === 'published'"
                effect="dark"
                :content="$t('lesson.newVersionHint')"
                placement="top"
              >
                <el-button
                  type="text"
                  size="small"
                  :loading="branchingId === scope.row.lessonId"
                  @click="createNextVersion(scope.row)"
                >
                  {{ $t('lesson.newVersion') }}
                </el-button>
              </el-tooltip>
              <el-button
                v-if="scope.row.status === 'draft'"
                type="text"
                size="small"
                class="danger-text"
                @click="confirmDelete(scope.row)"
              >
                {{ $t('lesson.delete') }}
              </el-button>
            </template>
          </el-table-column>
          <template slot="empty">
            <span class="muted">{{ $t('lesson.empty') }}</span>
          </template>
        </el-table>
      </el-card>
    </div>

    <el-dialog :title="dialogTitle" :visible.sync="dialogVisible" width="560px" @closed="resetForm">
      <el-form :model="form" label-width="150px" size="small">
        <el-form-item :label="$t('lesson.colKey')" required>
          <el-input v-model="form.lessonKey" :disabled="editingMetadata" :placeholder="$t('lesson.keyPlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('lesson.colTitle')" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item :label="$t('course.colLocale')" required>
          <el-input v-model="form.locale" placeholder="en" />
        </el-form-item>
        <el-form-item :label="$t('course.colAgeBand')" required>
          <el-input v-model="form.ageBand" placeholder="6-8" />
        </el-form-item>
        <el-form-item :label="$t('lesson.colPersonalityTags')">
          <el-input v-model="form.topicTags" :placeholder="$t('lesson.topicTagsPlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('lesson.colDifficulty')">
          <el-select v-model="form.difficultyBand" clearable :placeholder="$t('lesson.filterDifficulty')" style="width: 100%">
            <el-option v-for="d in difficultyOptions" :key="d" :label="d" :value="d" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('lesson.colDuration')">
          <el-input-number v-model="form.estimatedDurationSec" :min="1" :step="30" controls-position="right" style="width: 180px" />
          <span class="muted small form-help">{{ $t('lesson.durationHelp') }}</span>
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="dialogVisible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="saving" @click="submit">{{ $t('course.save') }}</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import Api from '@/apis/api';

const blankForm = () => ({
  lessonId: '',
  lessonKey: '',
  title: '',
  locale: 'en',
  ageBand: '6-8',
  topicTags: '',
  difficultyBand: '',
  estimatedDurationSec: null,
});

export default {
  name: 'CourseLessons',
  components: { HeaderBar },
  data() {
    return {
      list: [],
      loading: false,
      dialogVisible: false,
      editingMetadata: false,
      saving: false,
      branchingId: '',
      form: blankForm(),
      filters: {
        keyword: '',
        status: '',
        lessonType: '',
        topic: '',
        difficultyBand: '',
        monitorable: '',
      },
      statuses: ['draft', 'published', 'archived'],
      difficultyOptions: ['beginner', 'basic', 'intermediate', 'advanced'],
    };
  },
  computed: {
    courseId() {
      return this.$route.query.courseId;
    },
    courseTitle() {
      return this.$route.query.title || this.$route.query.courseKey || '';
    },
    dialogTitle() {
      return this.editingMetadata ? this.$t('lesson.editMetadataTitle') : this.$t('lesson.createTitle');
    },
    lessonTypeOptions() {
      return Array.from(new Set(this.list.map((l) => l.lessonType || 'lesson'))).sort();
    },
    topicOptions() {
      return Array.from(new Set(this.list.flatMap((l) => l.topicTags || []))).sort();
    },
    monitorableCount() {
      return this.list.filter((l) => l.monitorable).length;
    },
    personalizedCount() {
      return this.list.filter((l) => (l.topicTags || []).length || l.difficultyBand || l.estimatedDurationSec).length;
    },
    totalDurationLabel() {
      const seconds = this.list.reduce((sum, l) => sum + (Number(l.estimatedDurationSec) || 0), 0);
      return seconds ? this.durationLabel(seconds) : '—';
    },
    filteredList() {
      const keyword = this.filters.keyword.trim().toLowerCase();
      return this.list.filter((row) => {
        if (keyword) {
          const haystack = [row.lessonKey, row.title, ...(row.topicTags || [])].join(' ').toLowerCase();
          if (!haystack.includes(keyword)) return false;
        }
        if (this.filters.status && row.status !== this.filters.status) return false;
        if (this.filters.lessonType && (row.lessonType || 'lesson') !== this.filters.lessonType) return false;
        if (this.filters.topic && !(row.topicTags || []).includes(this.filters.topic)) return false;
        if (this.filters.difficultyBand && row.difficultyBand !== this.filters.difficultyBand) return false;
        if (this.filters.monitorable === 'yes' && !row.monitorable) return false;
        if (this.filters.monitorable === 'no' && row.monitorable) return false;
        return true;
      });
    },
  },
  created() {
    if (!this.courseId) {
      this.$router.replace('/course-management');
      return;
    }
    this.fetchList();
  },
  methods: {
    statusType(status) {
      if (status === 'published') return 'success';
      if (status === 'archived') return 'info';
      return 'warning';
    },
    durationLabel(seconds) {
      const total = Number(seconds) || 0;
      if (!total) return '—';
      const mins = Math.floor(total / 60);
      const secs = total % 60;
      return mins ? `${mins}m${secs ? ' ' + secs + 's' : ''}` : `${secs}s`;
    },
    parseTags(value) {
      return Array.from(new Set(String(value || '')
        .split(',')
        .map((t) => t.trim().toLowerCase())
        .filter(Boolean)));
    },
    metadataPayload() {
      return {
        title: this.form.title,
        locale: this.form.locale,
        ageBand: this.form.ageBand,
        topicTags: this.parseTags(this.form.topicTags),
        difficultyBand: this.form.difficultyBand || null,
        estimatedDurationSec: this.form.estimatedDurationSec || null,
      };
    },
    fetchList() {
      this.loading = true;
      Api.lesson.listLessons(
        this.courseId,
        (rows) => {
          this.loading = false;
          this.list = rows;
        },
        (msg) => {
          this.loading = false;
          this.$message.error(msg || this.$t('lesson.loadFail'));
        },
      );
    },
    resetFilters() {
      this.filters = { keyword: '', status: '', lessonType: '', topic: '', difficultyBand: '', monitorable: '' };
    },
    openEditor(row) {
      this.$router.push({
        path: '/lesson-editor',
        query: {
          lessonId: row.lessonId,
          courseId: this.courseId,
          courseTitle: this.courseTitle,
        },
      });
    },
    openMonitoring(row) {
      this.$router.push({
        path: '/lesson-monitoring',
        query: { lessonId: row.lessonId, lesson: row.title || row.lessonKey },
      });
    },
    openCreate() {
      this.editingMetadata = false;
      this.form = blankForm();
      this.dialogVisible = true;
    },
    openMetadata(row) {
      this.editingMetadata = true;
      this.form = {
        lessonId: row.lessonId,
        lessonKey: row.lessonKey,
        title: row.title,
        locale: row.locale,
        ageBand: row.ageBand,
        topicTags: (row.topicTags || []).join(', '),
        difficultyBand: row.difficultyBand || '',
        estimatedDurationSec: row.estimatedDurationSec || null,
      };
      this.dialogVisible = true;
    },
    resetForm() {
      this.form = blankForm();
      this.editingMetadata = false;
    },
    submit() {
      const f = this.form;
      if (!f.lessonKey || !f.title || !f.locale || !f.ageBand) {
        this.$message.warning(this.$t('course.required'));
        return;
      }
      this.saving = true;
      if (this.editingMetadata) {
        Api.lesson.updateLesson(
          f.lessonId,
          this.metadataPayload(),
          () => {
            this.saving = false;
            this.dialogVisible = false;
            this.$message.success(this.$t('lesson.metadataSaved'));
            this.fetchList();
          },
          (msg) => {
            this.saving = false;
            this.$message.error(msg);
          },
        );
        return;
      }
      Api.lesson.createLesson(
        this.courseId,
        { lessonKey: f.lessonKey, ...this.metadataPayload() },
        (lesson) => {
          this.saving = false;
          this.dialogVisible = false;
          this.$message.success(this.$t('lesson.created'));
          this.openEditor(lesson);
        },
        (msg) => {
          this.saving = false;
          this.$message.error(msg);
        },
      );
    },
    confirmDelete(row) {
      this.$confirm(this.$t('lesson.deleteConfirm', { key: row.lessonKey }), this.$t('lesson.delete'), {
        type: 'warning',
      })
        .then(() => {
          Api.lesson.deleteLesson(
            row.lessonId,
            () => {
              this.$message.success(this.$t('lesson.deleted'));
              this.fetchList();
            },
            (msg) => this.$message.error(msg),
          );
        })
        .catch(() => {});
    },
    createNextVersion(row) {
      this.$confirm(
        this.$t('lesson.newVersionConfirm', { key: row.lessonKey }),
        this.$t('lesson.newVersion'),
        { type: 'warning' },
      )
        .then(() => {
          this.branchingId = row.lessonId;
          Api.lesson.createNextVersion(
            row.lessonId,
            (lesson) => {
              this.branchingId = '';
              this.$message.success(this.$t('lesson.newVersionCreated', { v: lesson.lessonVersion }));
              this.openEditor(lesson);
            },
            (msg) => {
              this.branchingId = '';
              this.$message.error(msg);
            },
          );
        })
        .catch(() => {});
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
  gap: 12px;
  min-width: 0;
}
.page-title {
  margin: 0;
  font-size: 18px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.right-operations {
  display: flex;
  align-items: center;
  gap: 10px;
}
.main-wrapper {
  padding: 16px 24px;
}
.lesson-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}
.stat-item {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fff;
  padding: 12px 14px;
}
.stat-label {
  display: block;
  margin-bottom: 6px;
  color: #606266;
  font-size: 12px;
}
.stat-item strong {
  font-size: 22px;
  color: #303133;
}
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.filter-input.wide {
  width: 240px;
}
.filter-select {
  width: 150px;
}
.small-select {
  width: 130px;
}
.filter-count {
  color: #909399;
  font-size: 13px;
}
.topic-tag {
  margin-right: 5px;
  margin-bottom: 4px;
}
.small {
  font-size: 12px;
}
.form-help {
  margin-left: 8px;
}
.danger-text {
  color: #f56c6c;
}
.muted {
  color: #909399;
}
@media (max-width: 960px) {
  .operation-bar {
    align-items: flex-start;
    flex-direction: column;
    gap: 12px;
  }
  .lesson-stats {
    grid-template-columns: repeat(2, minmax(140px, 1fr));
  }
}
</style>
