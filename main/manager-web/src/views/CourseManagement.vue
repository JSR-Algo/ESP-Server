<template>
  <div class="welcome">
    <HeaderBar />
    <div class="operation-bar">
      <div class="left-title">
        <h2 class="page-title">{{ $t('course.pageTitle') }}</h2>
        <el-radio-group v-model="kindFilter" size="small">
          <el-radio-button label="all">{{ $t('course.filterAll') }}</el-radio-button>
          <el-radio-button label="template">{{ $t('course.filterTemplate') }}</el-radio-button>
          <el-radio-button label="custom">{{ $t('course.filterCustom') }}</el-radio-button>
        </el-radio-group>
      </div>
      <div class="right-operations">
        <span class="backend-hint">{{ $t('course.backendHint') }}</span>
        <el-button size="small" @click="$router.push('/lesson-monitoring')">
          {{ $t('lesson.monitor') }}
        </el-button>
        <el-button size="small" @click="$router.push('/course-insights')">
          {{ $t('course.insights') }}
        </el-button>
        <el-button type="primary" size="small" @click="openCreate">
          {{ $t('course.createBtn') }}
        </el-button>
        <el-button size="small" :loading="loading" @click="fetchList">
          {{ $t('course.refresh') }}
        </el-button>
      </div>
    </div>

    <div class="main-wrapper">
      <div class="course-stats">
        <div class="stat-item">
          <span class="stat-label">{{ $t('course.statTotal') }}</span>
          <strong>{{ list.length }}</strong>
        </div>
        <div class="stat-item">
          <span class="stat-label">{{ $t('course.statTemplates') }}</span>
          <strong>{{ templateCount }}</strong>
        </div>
        <div class="stat-item">
          <span class="stat-label">{{ $t('course.statCustom') }}</span>
          <strong>{{ customCount }}</strong>
        </div>
        <div class="stat-item">
          <span class="stat-label">{{ $t('course.statPublished') }}</span>
          <strong>{{ publishedCount }}</strong>
        </div>
      </div>
      <el-card class="content-area" shadow="never">
        <el-table v-loading="loading" :data="filteredList" stripe style="width: 100%">
          <el-table-column prop="courseKey" :label="$t('course.colKey')" min-width="160" />
          <el-table-column prop="title" :label="$t('course.colTitle')" min-width="160" />
          <el-table-column :label="$t('course.colType')" width="120">
            <template slot-scope="scope">
              <el-tag v-if="scope.row.isTemplate" type="primary" size="small">{{ $t('course.template') }}</el-tag>
              <el-tag v-else-if="scope.row.sourceCourseId" type="warning" size="small" effect="plain">{{ $t('course.clonedTag') }}</el-tag>
              <span v-else class="muted small">—</span>
            </template>
          </el-table-column>
          <el-table-column prop="ageBand" :label="$t('course.colAgeBand')" width="90" />
          <el-table-column :label="$t('course.colStatus')" width="110">
            <template slot-scope="scope">
              <el-tag :type="statusType(scope.row.status)" size="small">{{ scope.row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('course.colActions')" width="360">
            <template slot-scope="scope">
              <el-button type="text" size="small" @click="openLessons(scope.row)">
                {{ $t('course.lessons') }}
              </el-button>
              <el-button type="text" size="small" @click="openClone(scope.row)">
                {{ $t('course.clone') }}
              </el-button>
              <el-button type="text" size="small" @click="toggleTemplate(scope.row)">
                {{ scope.row.isTemplate ? $t('course.unmarkTemplate') : $t('course.markTemplate') }}
              </el-button>
              <el-button type="text" size="small" @click="openEdit(scope.row)">
                {{ $t('course.edit') }}
              </el-button>
              <el-button type="text" size="small" class="danger-text" @click="confirmDelete(scope.row)">
                {{ $t('course.delete') }}
              </el-button>
            </template>
          </el-table-column>
          <template slot="empty">
            <span class="muted">{{ $t('course.empty') }}</span>
          </template>
        </el-table>
      </el-card>
    </div>

    <el-dialog
      :title="editing ? $t('course.editTitle') : $t('course.createTitle')"
      :visible.sync="dialogVisible"
      width="480px"
      @closed="resetForm"
    >
      <el-form ref="form" :model="form" label-width="110px" size="small">
        <el-form-item :label="$t('course.colKey')" required>
          <el-input
            v-model="form.courseKey"
            :disabled="editing"
            :placeholder="$t('course.keyPlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="$t('course.colTitle')" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item :label="$t('course.colLocale')" required>
          <el-input v-model="form.locale" placeholder="en" />
        </el-form-item>
        <el-form-item :label="$t('course.colAgeBand')" required>
          <el-input v-model="form.ageBand" placeholder="6-8" />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="dialogVisible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="saving" @click="submit">
          {{ $t('course.save') }}
        </el-button>
      </span>
    </el-dialog>

    <el-dialog :title="$t('course.cloneTitle')" :visible.sync="cloneVisible" width="480px" @closed="resetClone">
      <p class="muted">{{ $t('course.cloneHint', { source: cloneSource.courseKey }) }}</p>
      <el-form :model="cloneForm" label-width="110px" size="small">
        <el-form-item :label="$t('course.colKey')" required>
          <el-input v-model="cloneForm.courseKey" :placeholder="$t('course.keyPlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('course.colTitle')" required>
          <el-input v-model="cloneForm.title" />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="cloneVisible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="cloning" @click="doClone">{{ $t('course.clone') }}</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import Api from '@/apis/api';

export default {
  name: 'CourseManagement',
  components: { HeaderBar },
  data() {
    return {
      list: [],
      loading: false,
      dialogVisible: false,
      editing: false,
      saving: false,
      kindFilter: 'all',
      cloneVisible: false,
      cloning: false,
      cloneSource: {},
      cloneForm: { courseKey: '', title: '' },
      form: { courseId: '', courseKey: '', title: '', locale: 'en', ageBand: '6-8' },
    };
  },
  computed: {
    filteredList() {
      if (this.kindFilter === 'template') return this.list.filter((c) => c.isTemplate);
      if (this.kindFilter === 'custom') return this.list.filter((c) => !c.isTemplate);
      return this.list;
    },
    templateCount() {
      return this.list.filter((c) => c.isTemplate).length;
    },
    customCount() {
      return this.list.filter((c) => !c.isTemplate).length;
    },
    publishedCount() {
      return this.list.filter((c) => c.status === 'published').length;
    },
  },
  created() {
    this.fetchList();
  },
  methods: {
    statusType(status) {
      if (status === 'published') return 'success';
      if (status === 'archived') return 'info';
      return 'warning';
    },
    fetchList() {
      this.loading = true;
      Api.course.getCourseList(
        (rows) => {
          this.loading = false;
          this.list = rows;
        },
        (msg) => {
          this.loading = false;
          this.$message.error(msg || this.$t('course.loadFail'));
        },
      );
    },
    openLessons(row) {
      this.$router.push({
        path: '/course-lessons',
        query: { courseId: row.courseId, courseKey: row.courseKey, title: row.title },
      });
    },
    openClone(row) {
      this.cloneSource = row;
      this.cloneForm = {
        courseKey: row.courseKey + '-custom',
        title: this.$t('course.copyOf', { title: row.title }),
      };
      this.cloneVisible = true;
    },
    resetClone() {
      this.cloneForm = { courseKey: '', title: '' };
      this.cloneSource = {};
    },
    doClone() {
      const f = this.cloneForm;
      if (!f.courseKey || !f.title) {
        this.$message.warning(this.$t('course.required'));
        return;
      }
      this.cloning = true;
      Api.course.cloneCourse(
        this.cloneSource.courseId,
        { courseKey: f.courseKey, title: f.title },
        (course) => {
          this.cloning = false;
          this.cloneVisible = false;
          this.$message.success(this.$t('course.cloned'));
          this.openLessons(course); // jump into the new custom course's lessons
        },
        (msg) => {
          this.cloning = false;
          this.$message.error(msg);
        },
      );
    },
    toggleTemplate(row) {
      const next = !row.isTemplate;
      Api.course.setTemplate(
        row.courseId,
        next,
        () => {
          this.$message.success(next ? this.$t('course.markedTemplate') : this.$t('course.unmarkedTemplate'));
          this.fetchList();
        },
        (msg) => this.$message.error(msg),
      );
    },
    openCreate() {
      this.editing = false;
      this.form = { courseId: '', courseKey: '', title: '', locale: 'en', ageBand: '6-8' };
      this.dialogVisible = true;
    },
    openEdit(row) {
      this.editing = true;
      this.form = {
        courseId: row.courseId,
        courseKey: row.courseKey,
        title: row.title,
        locale: row.locale,
        ageBand: row.ageBand,
      };
      this.dialogVisible = true;
    },
    resetForm() {
      this.form = { courseId: '', courseKey: '', title: '', locale: 'en', ageBand: '6-8' };
    },
    submit() {
      const f = this.form;
      if (!f.courseKey || !f.title || !f.locale || !f.ageBand) {
        this.$message.warning(this.$t('course.required'));
        return;
      }
      this.saving = true;
      const onErr = (msg) => {
        this.saving = false;
        this.$message.error(msg);
      };
      if (this.editing) {
        // Course key is immutable; only mutable fields are sent.
        Api.course.updateCourse(
          f.courseId,
          { title: f.title, locale: f.locale, ageBand: f.ageBand },
          () => {
            this.saving = false;
            this.dialogVisible = false;
            this.$message.success(this.$t('course.updated'));
            this.fetchList();
          },
          onErr,
        );
      } else {
        Api.course.createCourse(
          { courseKey: f.courseKey, title: f.title, locale: f.locale, ageBand: f.ageBand },
          () => {
            this.saving = false;
            this.dialogVisible = false;
            this.$message.success(this.$t('course.created'));
            this.fetchList();
          },
          onErr,
        );
      }
    },
    confirmDelete(row) {
      this.$confirm(
        this.$t('course.deleteConfirm', { key: row.courseKey }),
        this.$t('course.delete'),
        { type: 'warning' },
      )
        .then(() => {
          Api.course.deleteCourse(
            row.courseId,
            () => {
              this.$message.success(this.$t('course.deleted'));
              this.fetchList();
            },
            (msg) => this.$message.error(msg),
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
.course-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr));
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
  .course-stats {
    grid-template-columns: repeat(2, minmax(140px, 1fr));
  }
}
</style>
