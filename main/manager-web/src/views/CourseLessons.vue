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
      <el-card class="content-area" shadow="never">
        <el-table v-loading="loading" :data="list" stripe style="width: 100%">
          <el-table-column prop="lessonKey" :label="$t('lesson.colKey')" min-width="180" />
          <el-table-column prop="title" :label="$t('lesson.colTitle')" min-width="180" />
          <el-table-column prop="lessonVersion" :label="$t('lesson.colVersion')" width="90" align="center" />
          <el-table-column :label="$t('lesson.colStatus')" width="120">
            <template slot-scope="scope">
              <el-tag :type="statusType(scope.row.status)" size="small">{{ scope.row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colActions')" width="180">
            <template slot-scope="scope">
              <el-button type="text" size="small" @click="openEditor(scope.row)">
                {{ $t('lesson.editSteps') }}
              </el-button>
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

    <el-dialog :title="$t('lesson.createTitle')" :visible.sync="dialogVisible" width="480px" @closed="resetForm">
      <el-form :model="form" label-width="110px" size="small">
        <el-form-item :label="$t('lesson.colKey')" required>
          <el-input v-model="form.lessonKey" :placeholder="$t('lesson.keyPlaceholder')" />
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

export default {
  name: 'CourseLessons',
  components: { HeaderBar },
  data() {
    return {
      list: [],
      loading: false,
      dialogVisible: false,
      saving: false,
      form: { lessonKey: '', title: '', locale: 'en', ageBand: '6-8' },
    };
  },
  computed: {
    courseId() {
      return this.$route.query.courseId;
    },
    courseTitle() {
      return this.$route.query.title || this.$route.query.courseKey || '';
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
    openCreate() {
      this.form = { lessonKey: '', title: '', locale: 'en', ageBand: '6-8' };
      this.dialogVisible = true;
    },
    resetForm() {
      this.form = { lessonKey: '', title: '', locale: 'en', ageBand: '6-8' };
    },
    submit() {
      const f = this.form;
      if (!f.lessonKey || !f.title || !f.locale || !f.ageBand) {
        this.$message.warning(this.$t('course.required'));
        return;
      }
      this.saving = true;
      Api.lesson.createLesson(
        this.courseId,
        { lessonKey: f.lessonKey, title: f.title, locale: f.locale, ageBand: f.ageBand },
        (lesson) => {
          this.saving = false;
          this.dialogVisible = false;
          this.$message.success(this.$t('lesson.created'));
          // Jump straight into the editor for the new draft.
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
}
.page-title {
  margin: 0;
  font-size: 18px;
}
.right-operations {
  display: flex;
  align-items: center;
  gap: 10px;
}
.main-wrapper {
  padding: 16px 24px;
}
.danger-text {
  color: #f56c6c;
}
.muted {
  color: #909399;
}
</style>
