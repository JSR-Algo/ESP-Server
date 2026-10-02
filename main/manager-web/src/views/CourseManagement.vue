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
      <div class="course-filter-panel">
        <el-input
          v-model="courseKeyword"
          :placeholder="$t('course.quickSearchPlaceholder')"
          size="small"
          clearable
          class="filter-input wide"
        />
        <el-select v-model="riskFilter" size="small" class="filter-input">
          <el-option :label="$t('course.riskAll')" value="all" />
          <el-option :label="$t('insights.riskAttention')" value="attention" />
          <el-option :label="$t('insights.riskWatch')" value="watch" />
          <el-option :label="$t('insights.riskHealthy')" value="healthy" />
        </el-select>
        <el-select v-model="qualityWindow" size="small" class="filter-input" @change="fetchQuality">
          <el-option :label="$t('insights.window7')" :value="7" />
          <el-option :label="$t('insights.window14')" :value="14" />
          <el-option :label="$t('insights.window30')" :value="30" />
          <el-option :label="$t('insights.window90')" :value="90" />
        </el-select>
        <el-input
          v-model="learnerKeyword"
          :placeholder="$t('course.learnerFilterPlaceholder')"
          size="small"
          clearable
          class="filter-input wide"
          @keyup.enter.native="openLearnerFilter"
        />
        <el-button size="small" @click="openLearnerFilter">{{ $t('course.openLearners') }}</el-button>
      </div>

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
        <div class="stat-item quality-preview">
          <span class="stat-label">{{ $t('course.qualityPreview') }}</span>
          <strong>{{ avgQuality }}</strong>
        </div>
        <div class="stat-item attention-preview">
          <span class="stat-label">{{ $t('course.needsAttention') }}</span>
          <strong>{{ needsAttentionCount }}</strong>
        </div>
      </div>
      <el-alert
        v-if="qualityFailed"
        data-testid="course-quality-unavailable"
        type="warning"
        :title="$t('course.qualityLoadFail')"
        :closable="false"
        show-icon
        class="quality-alert"
      >
        <el-button type="text" size="mini" @click="fetchQuality">{{ $t('course.refresh') }}</el-button>
      </el-alert>
      <el-card class="content-area" shadow="never">
        <el-alert v-if="listFailed" :title="$t('course.loadFail')" type="error" :closable="false" show-icon />
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
          <el-table-column :label="$t('course.colQuality')" width="150">
            <template slot-scope="scope">
              <div v-if="qualityFor(scope.row).courseId" class="quality-cell">
                <el-tag size="mini" :type="riskTagType(qualityFor(scope.row).riskLevel)">
                  {{ qualityFor(scope.row).qualityScore }}
                </el-tag>
                <span class="muted small">{{ riskLabel(qualityFor(scope.row).riskLevel) }}</span>
              </div>
              <span v-else-if="qualityFailed" class="muted small">{{ $t('course.qualityUnavailable') }}</span>
              <span v-else class="muted small">{{ $t('course.noQuality') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('course.colActions')" min-width="650">
            <template slot-scope="scope">
              <el-button type="text" size="small" @click="openLessons(scope.row)">
                {{ $t('course.lessons') }}
              </el-button>
              <el-button type="text" size="small" @click="openInsightsForCourse(scope.row)">
                {{ $t('course.quality') }}
              </el-button>
              <el-button type="text" size="small" @click="openClone(scope.row)">
                {{ $t('course.clone') }}
              </el-button>
              <el-button type="text" size="small" :disabled="!!actionPending['template:' + scope.row.courseId]" @click="toggleTemplate(scope.row)">
                {{ scope.row.isTemplate ? $t('course.unmarkTemplate') : $t('course.markTemplate') }}
              </el-button>
              <el-button type="text" size="small" @click="openEdit(scope.row)">
                {{ $t('course.edit') }}
              </el-button>
              <el-button type="text" size="small" data-testid="course-publish" :disabled="!!actionPending['lifecycle:' + scope.row.courseId]" @click="openLifecycle(scope.row, 'published')">
                {{ $t(scope.row.status === 'published' ? 'course.republish' : 'course.publish') }}
              </el-button>
              <el-button v-if="scope.row.status !== 'archived'" type="text" size="small" data-testid="course-archive" :disabled="!!actionPending['lifecycle:' + scope.row.courseId]" @click="openLifecycle(scope.row, 'archived')">{{ $t('course.archive') }}</el-button>
              <el-button v-if="scope.row.status !== 'draft'" type="text" size="small" data-testid="course-return-draft" :disabled="!!actionPending['lifecycle:' + scope.row.courseId]" @click="openLifecycle(scope.row, 'draft')">{{ $t('course.returnDraft') }}</el-button>
              <el-button type="text" size="small" class="danger-text" :disabled="!!actionPending['delete:' + scope.row.courseId]" @click="confirmDelete(scope.row)">
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

    <el-dialog :title="$t('course.lifecycleTitle')" :visible.sync="lifecycleVisible" width="560px" class="lifecycle-dialog">
      <div v-if="lifecycle" v-loading="lifecycle.loading" data-testid="course-lifecycle">
        <p><strong>{{ lifecycle.course.title }}</strong> · {{ lifecycle.course.courseKey }}</p>
        <p data-testid="course-lifecycle-state">{{ $t('course.currentStatus') }}: {{ lifecycle.course.status }} → {{ lifecycle.status }}</p>
        <p>{{ $t(lifecycle.status === 'published' ? 'course.publishPrerequisites' : 'course.archivePolicy') }}</p>
        <p class="muted">{{ $t('course.readinessSeparate') }}</p>
        <el-alert v-if="lifecycle.notice" data-testid="course-lifecycle-notice" :title="lifecycle.notice" type="warning" :closable="false" show-icon />
        <p v-if="lifecycle.details.lessonKey" data-testid="course-lifecycle-lesson">{{ lifecycle.details.lessonKey }} · v{{ lifecycle.details.lessonVersion }} · {{ lifecycle.details.lessonId }}</p>
        <el-button v-if="lifecycle.details.lessonKey" type="text" @click="openLessons(lifecycle.course)">{{ $t('course.lessons') }}</el-button>
        <p v-if="lifecycle.review" data-testid="course-lifecycle-readback">{{ $t('course.storedState') }}: {{ lifecycle.review.title }} · {{ lifecycle.review.status }}</p>
        <el-button v-if="lifecycle.needsReview && lifecycle.review" data-testid="course-lifecycle-review" :disabled="lifecycle.loading" @click="reviewLifecycle">{{ $t('course.reviewLifecycle') }}</el-button>
        <el-button v-if="lifecycle.needsReview && !lifecycle.review" data-testid="course-lifecycle-refresh" :disabled="lifecycle.loading" @click="readLifecycle(lifecycle)">{{ $t('course.refresh') }}</el-button>
      </div>
      <span slot="footer">
        <el-button @click="lifecycleVisible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" data-testid="course-lifecycle-confirm" :loading="!!lifecycle && lifecycle.pending" :disabled="!lifecycleCanConfirm" @click="confirmLifecycle">{{ $t('course.confirmLifecycle') }}</el-button>
      </span>
    </el-dialog>

    <el-dialog
      :title="editing ? $t('course.editTitle') : $t('course.createTitle')"
      :visible.sync="dialogVisible"
      width="480px"
      @closed="!dialogVisible && resetForm()"
    >
      <el-alert v-if="formNotice" :title="formNotice" type="warning" :closable="false" />
      <el-button v-if="editing && foundCourse" @click="allowReviewedRetry">{{ $t('course.retryReviewed') }}</el-button>
      <el-button v-if="foundCourse" @click="openLessons(foundCourse)">{{ $t('course.reviewFound') }}</el-button>
      <el-form ref="form" :model="form" label-width="110px" size="small">
        <el-form-item :label="$t('course.colKey')" :error="formErrors.courseKey" required>
          <el-input
            v-model="form.courseKey"
            @keyup.enter.native="submit"
            :disabled="editing"
            :placeholder="$t('course.keyPlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="$t('course.colTitle')" :error="formErrors.title" required>
          <el-input v-model="form.title" @keyup.enter.native="submit" />
        </el-form-item>
        <el-form-item :label="$t('course.colLocale')" :error="formErrors.locale" required>
          <el-select
            v-model="form.locale"
            data-testid="course-locale"
            filterable
            allow-create
            default-first-option
            :placeholder="defaultLocale"
            style="width: 100%"
          >
            <el-option v-for="l in locales" :key="l" :label="l" :value="l" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('course.colAgeBand')" :error="formErrors.ageBand" required>
          <el-select
            v-model="form.ageBand"
            data-testid="course-age-band"
            filterable
            allow-create
            default-first-option
            :placeholder="defaultAgeBand"
            style="width: 100%"
          >
            <el-option v-for="b in ageBands" :key="b" :label="b" :value="b" />
          </el-select>
          <el-alert
            v-if="ageBandSeverity === 'unenforced'"
            data-testid="course-age-band-unenforced"
            type="error"
            :title="$t('lesson.ageBandUnenforced')"
            :closable="false"
            show-icon
            class="age-band-alert"
          />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="dialogVisible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="saving" data-testid="course-submit" @click="submit">
          {{ $t('course.save') }}
        </el-button>
      </span>
    </el-dialog>

    <el-dialog :title="$t('course.cloneTitle')" :visible.sync="cloneVisible" width="480px" @closed="!cloneVisible && resetClone()">
      <el-alert v-if="cloneNotice" :title="cloneNotice" type="warning" :closable="false" />
      <el-button v-if="cloneFoundCourse" @click="openLessons(cloneFoundCourse)">{{ $t('course.reviewFound') }}</el-button>
      <p class="muted">{{ $t('course.cloneHint', { source: cloneSource.courseKey }) }}</p>
      <el-form :model="cloneForm" label-width="110px" size="small">
        <el-form-item :label="$t('course.colKey')" :error="cloneErrors.courseKey" required>
          <el-input v-model="cloneForm.courseKey" @keyup.enter.native="doClone" :placeholder="$t('course.keyPlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('course.colTitle')" :error="cloneErrors.title" required>
          <el-input v-model="cloneForm.title" @keyup.enter.native="doClone" />
        </el-form-item>
      </el-form>
      <span slot="footer">
        <el-button size="small" @click="cloneVisible = false">{{ $t('course.cancel') }}</el-button>
        <el-button type="primary" size="small" :loading="cloning" data-testid="course-clone-submit" @click="doClone">{{ $t('course.clone') }}</el-button>
      </span>
    </el-dialog>
  </div>
</template>

<script>
import { validateCourseForm, mutationDetails, uncertainMutation } from '@/utils/courseForm.cjs';
import HeaderBar from '@/components/HeaderBar.vue';
import Api from '@/apis/api';
import {
  AGE_BANDS,
  DEFAULT_AGE_BAND,
  DEFAULT_LOCALE,
  LOCALES,
  ageBandSeverity,
} from '@/utils/courseTaxonomy.mjs';

const blankCourseForm = () => ({
  courseId: '',
  courseKey: '',
  title: '',
  locale: DEFAULT_LOCALE,
  ageBand: DEFAULT_AGE_BAND,
});

export default {
  name: 'CourseManagement',
  components: { HeaderBar },
  data() {
    return {
      requestsDisposed: false,
      listSequence: 0,
      listFailed: false,
      list: [],
      loading: false,
      dialogVisible: false,
      editing: false,
      saving: false,
      formErrors: {},
      cloneErrors: {},
      formNotice: '',
      cloneNotice: '',
      foundCourse: null,
      cloneFoundCourse: null,
      lifecycleVisible: false,
      lifecycle: null,
      lifecycleUncertain: {},
      actionPending: {},
      uncertainActions: {},
      kindFilter: 'all',
      courseKeyword: '',
      learnerKeyword: '',
      riskFilter: 'all',
      qualityWindow: 30,
      qualityRows: [],
      qualityLoading: false,
      qualityFailed: false,
      qualitySequence: 0,
      cloneVisible: false,
      cloning: false,
      cloneSource: {},
      cloneForm: { courseKey: '', title: '' },
      form: blankCourseForm(),
      ageBands: AGE_BANDS,
      locales: LOCALES,
      defaultAgeBand: DEFAULT_AGE_BAND,
      defaultLocale: DEFAULT_LOCALE,
    };
  },
  computed: {
    lifecycleCanConfirm() {
      const l = this.lifecycle;
      return !!l && !l.loading && !l.pending && !l.needsReview && /^[a-f0-9]{32}$/.test(l.course.revision || '');
    },
    ageBandSeverity() {
      return ageBandSeverity(this.form.ageBand);
    },
    filteredList() {
      const kw = this.courseKeyword.trim().toLowerCase();
      return this.list.filter((c) => {
        if (this.kindFilter === 'template' && !c.isTemplate) return false;
        if (this.kindFilter === 'custom' && c.isTemplate) return false;
        if (kw && ![c.courseKey, c.title, c.locale, c.ageBand, c.status].some((v) => String(v || '').toLowerCase().includes(kw))) return false;
        // When the insights fetch failed there are no risk levels to match, so
        // applying the filter would silently empty the whole course list and
        // read as "no courses exist". Fall back to showing every course; the
        // quality banner explains why the filter is inert.
        if (this.riskFilter !== 'all' && !this.qualityFailed) {
          const q = this.qualityFor(c);
          if (!q.courseId || q.riskLevel !== this.riskFilter) return false;
        }
        return true;
      });
    },
    qualityByCourse() {
      return this.qualityRows.reduce((acc, row) => {
        if (row.courseId) acc[row.courseId] = row;
        if (row.courseKey) acc[row.courseKey] = row;
        return acc;
      }, {});
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
    // An insights outage must read as "unknown", never as a healthy 0.
    avgQuality() {
      if (this.qualityFailed || this.qualityLoading) return '—';
      if (!this.qualityRows.length) return 0;
      return Math.round(this.qualityRows.reduce((sum, row) => sum + row.qualityScore, 0) / this.qualityRows.length);
    },
    needsAttentionCount() {
      if (this.qualityFailed || this.qualityLoading) return '—';
      return this.qualityRows.filter((row) => row.riskLevel === 'attention').length;
    },
  },
  created() {
    this.fetchList();
    this.fetchQuality();
  },
  beforeDestroy() {
    this.requestsDisposed = true;
    this.listSequence++;
    this.qualitySequence++;
  },
  methods: {
    openLifecycle(row, status) {
      if (this.requestsDisposed || !['draft', 'published', 'archived'].includes(status) || this.actionPending['lifecycle:' + row.courseId]) return;
      const l = { course: { ...row }, status, loading: false, pending: false, needsReview: !!this.lifecycleUncertain[row.courseId], review: null, notice: '', details: {} };
      this.lifecycle = l;
      this.lifecycleVisible = true;
      this.readLifecycle(l);
    },
    readLifecycle(l, afterWrite = false) {
      if (this.requestsDisposed || l.loading) return;
      const id = l.course.courseId;
      l.loading = true;
      this.actionPending = { ...this.actionPending, ['lifecycle:' + id]: true };
      Api.course.getCourse(id, (course) => {
        if (this.requestsDisposed) return;
        l.loading = false; l.pending = false;
        this.actionPending = { ...this.actionPending, ['lifecycle:' + id]: false };
        if (!course || course.courseId !== id || !/^[a-f0-9]{32}$/.test(course.revision || '')) {
          if (afterWrite) this.lifecycleUncertain[id] = true;
          l.needsReview = true; l.review = null; l.notice = this.$t('course.lifecycleReadFail');
          return;
        }
        if (afterWrite && !l.needsReview && course.status === l.status) {
          delete this.lifecycleUncertain[id];
          this.fetchList();
          if (this.lifecycle === l && this.lifecycleVisible) {
            this.lifecycleVisible = false;
            this.$message.success(this.$t('course.lifecycleSaved'));
          }
          return;
        }
        if (afterWrite || l.needsReview) {
          l.needsReview = true; l.review = course;
          this.lifecycleUncertain[id] = true;
          if (!l.notice) l.notice = this.$t('course.lifecycleUncertain');
        } else l.course = course;
      }, () => {
        if (this.requestsDisposed) return;
        l.loading = false; l.pending = false; l.needsReview = true; l.review = null;
        this.actionPending = { ...this.actionPending, ['lifecycle:' + id]: false };
        if (afterWrite) this.lifecycleUncertain[id] = true;
        l.notice = this.$t('course.lifecycleReadFail');
      });
    },
    reviewLifecycle() {
      const l = this.lifecycle;
      if (!l || l.loading || l.pending || !l.review) return;
      l.course = l.review; l.review = null; l.needsReview = false; l.notice = ''; l.details = {};
      delete this.lifecycleUncertain[l.course.courseId];
      this.fetchList();
    },
    confirmLifecycle() {
      if (this.requestsDisposed || !this.lifecycleCanConfirm) return;
      const l = this.lifecycle; const id = l.course.courseId; const action = 'lifecycle:' + id;
      if (this.actionPending[action]) return;
      this.actionPending = { ...this.actionPending, [action]: true };
      l.pending = true; l.notice = ''; l.details = {};
      Api.course.transitionCourse(id, l.status, l.course.revision, () => {
        if (this.requestsDisposed) return;
        this.readLifecycle(l, true);
      }, (msg, response) => {
        if (this.requestsDisposed) return;
        l.pending = false;
        this.actionPending = { ...this.actionPending, [action]: false };
        l.details = mutationDetails(response);
        if (l.details.reason === 'stale' || uncertainMutation(response)) {
          l.needsReview = true; this.lifecycleUncertain[id] = true;
          l.notice = this.$t(l.details.reason === 'stale' ? 'course.lifecycleStale' : 'course.lifecycleUncertain') + (msg ? ' ' + msg : '');
          this.readLifecycle(l); return;
        }
        l.notice = (l.details.reason === 'no_published_lessons' ? this.$t('course.publishPrerequisites') + ' ' : '')
          + (msg || this.$t('course.actionFailed'));
      });
    },
    qualityFor(row) {
      return this.qualityByCourse[row.courseId] || this.qualityByCourse[row.courseKey] || {};
    },
    riskTagType(level) {
      if (level === 'attention') return 'danger';
      if (level === 'healthy') return 'success';
      return 'warning';
    },
    riskLabel(level) {
      if (level === 'attention') return this.$t('insights.riskAttention');
      if (level === 'healthy') return this.$t('insights.riskHealthy');
      return this.$t('insights.riskWatch');
    },
    statusType(status) {
      if (status === 'published') return 'success';
      if (status === 'archived') return 'info';
      return 'warning';
    },
    fetchList() {
      if (this.requestsDisposed) return;
      const sequence = ++this.listSequence;
      this.list = [];
      this.listFailed = false;
      this.loading = true;
      Api.course.getCourseList(
        (rows) => {
          if (this.requestsDisposed || sequence !== this.listSequence) return;
          this.loading = false;
          this.list = rows;
        },
        (msg) => {
          if (this.requestsDisposed || sequence !== this.listSequence) return;
          this.loading = false;
          this.listFailed = true;
          this.$message.error(msg || this.$t('course.loadFail'));
        },
      );
    },
    // Quality is supplementary to the course list, so a failure must not block
    // the page — but it must not masquerade as "this course has no quality
    // data" either. `qualityFailed` drives an explicit banner + a distinct
    // per-row label so an insights outage is never read as a healthy zero.
    // `qualitySequence` drops out-of-order responses: the window selector
    // refetches on every change, and a slow 90-day response landing after a
    // fast 7-day one would otherwise paint stale scores.
    fetchQuality() {
      if (this.requestsDisposed) return;
      const sequence = ++this.qualitySequence;
      this.qualityRows = [];
      this.qualityLoading = true;
      Api.courseInsights.getCourseQuality(
        { windowDays: this.qualityWindow },
        (rows) => {
          if (this.requestsDisposed || sequence !== this.qualitySequence) return;
          this.qualityLoading = false;
          this.qualityFailed = false;
          this.qualityRows = rows;
        },
        (msg) => {
          if (this.requestsDisposed || sequence !== this.qualitySequence) return;
          this.qualityLoading = false;
          this.qualityFailed = true;
          this.qualityRows = [];
          this.$message.warning(msg || this.$t('course.qualityLoadFail'));
        },
      );
    },
    openInsightsForCourse(row) {
      this.$router.push({
        path: '/course-insights',
        query: { tab: 'quality', courseId: row.courseId, keyword: row.courseKey },
      });
    },
    openLearnerFilter() {
      this.$router.push({
        path: '/course-insights',
        query: { tab: 'learners', keyword: this.learnerKeyword.trim() || this.courseKeyword.trim() },
      });
    },
    openLessons(row) {
      this.$router.push({
        path: '/course-lessons',
        query: { courseId: row.courseId, courseKey: row.courseKey, title: row.title },
      });
    },
    openClone(row) {
      this.cloneNotice = ''; this.cloneErrors = {}; this.cloneFoundCourse = null;
      this.cloneSource = { ...row };
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
    // Lock handlers as well as buttons. A captured form object is the dialog identity.
    runCourseMutation({ action, target, form, clone = false, request, success }) {
      if (this.requestsDisposed || this.actionPending[action]) return;
      const current = () => !this.requestsDisposed && (!form || (clone ? this.cloneForm === form && this.cloneVisible : this.form === form && this.dialogVisible));
      const notice = (key) => {
        if (!current()) return;
        if (form) this[clone ? 'cloneNotice' : 'formNotice'] = this.$t(key);
        else this.$message.warning(this.$t(key));
      };
      const finish = () => {
        this.actionPending = { ...this.actionPending, [action]: false };
        if (form) this[clone ? 'cloning' : 'saving'] = false;
      };
      this.actionPending = { ...this.actionPending, [action]: true };
      if (form) this[clone ? 'cloning' : 'saving'] = true;
      const reconcile = () => {
        notice('course.outcomeUnknown');
        const failed = () => { finish(); notice('course.readbackFailed'); };
        const found = (course) => {
          finish();
          if (target.courseId && !form) delete this.uncertainActions[action];
          if (!current()) return;
          if (form) this[clone ? 'cloneFoundCourse' : 'foundCourse'] = course;
          notice('course.foundReview');
          if (!form) this.fetchList();
        };
        if (target.courseId) {
          Api.course.getCourse(target.courseId, found, (msg, res) => {
            if (Number(res && res.status) === 404) { finish(); notice('course.resourceMissing'); if (!form) this.fetchList(); }
            else failed();
          });
        } else {
          Api.course.getCourseList((rows) => {
            const match = rows.find(row => row.courseKey === target.courseKey);
            if (match) Api.course.getCourse(match.courseId, found, failed);
            else {
              delete this.uncertainActions[action]; finish(); notice('course.notFoundRetry');
              // Absence allows a deliberate same-key attempt; never replay here.
            }
          }, failed);
        }
      };
      if (this.uncertainActions[action]) { reconcile(); return; }
      request((payload) => {
        finish();
        if (current()) success(payload);
      }, (msg, response) => {
        const details = mutationDetails(response);
        if (uncertainMutation(response)) {
          this.uncertainActions[action] = { ...target };
          reconcile(); return;
        }
        if (details.reason === 'duplicate') {
          this.uncertainActions[action] = { ...target };
          if (current() && form) this[clone ? 'cloneErrors' : 'formErrors'] = { courseKey: this.$t('course.duplicateKey') };
          reconcile(); return;
        }
        finish();
        if (!current()) return;
        const message = details.reason === 'duplicate' ? this.$t('course.duplicateKey')
          : details.reason === 'nonempty' ? this.$t('course.nonemptyDelete') : msg;
        if (form && details.field) this[clone ? 'cloneErrors' : 'formErrors'] = { [details.field]: message };
        this.$message.error(message || this.$t('course.actionFailed'));
        if (details.reason === 'nonempty' || Number(response && response.status) === 404) this.fetchList();
      });
    },
    doClone() {
      if (this.cloning) return;
      const f = this.cloneForm;
      this.cloneErrors = Object.fromEntries(Object.entries(validateCourseForm(f, true)).map(([k,v]) => [k,this.$t(v)]));
      if (Object.keys(this.cloneErrors).length) return;
      if (new TextEncoder().encode(JSON.stringify({ courseKey: f.courseKey, title: f.title })).length > 102400) { this.cloneNotice = this.$t('course.payloadTooLarge'); return; }
      const sourceId = this.cloneSource.courseId;
      const payload = { courseKey: f.courseKey, title: f.title };
      this.runCourseMutation({ action: 'key:' + f.courseKey.trim(), target: { courseKey: f.courseKey.trim() }, form: f, clone: true,
        request: (ok, fail) => Api.course.cloneCourse(sourceId, payload, ok, fail),
        success: (course) => { this.cloneVisible = false; this.$message.success(this.$t('course.cloned')); this.openLessons(course); },
      });
    },
    toggleTemplate(row) {
      const id = row.courseId; const next = !row.isTemplate;
      this.runCourseMutation({ action: 'template:' + id, target: { courseId: id },
        request: (ok, fail) => Api.course.setTemplate(id, next, ok, fail),
        success: () => { this.$message.success(this.$t(next ? 'course.markedTemplate' : 'course.unmarkedTemplate')); this.fetchList(); },
      });
    },
    openCreate() {
      this.formErrors = {}; this.formNotice = ''; this.foundCourse = null;
      this.editing = false;
      this.form = blankCourseForm();
      this.dialogVisible = true;
    },
    openEdit(row) {
      this.formErrors = {}; this.formNotice = ''; this.foundCourse = null;
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
      this.form = blankCourseForm();
    },
    allowReviewedRetry() {
      delete this.uncertainActions['edit:' + this.form.courseId];
      this.foundCourse = null; this.formNotice = '';
    },
    submit() {
      if (this.saving) return;
      const f = this.form;
      this.formErrors = Object.fromEntries(Object.entries(validateCourseForm(f)).map(([k,v]) => [k,this.$t(v)]));
      if (Object.keys(this.formErrors).length) return;
      const editing = this.editing;
      const payload = { title: f.title, locale: f.locale, ageBand: f.ageBand };
      if (!editing) payload.courseKey = f.courseKey;
      if (new TextEncoder().encode(JSON.stringify(payload)).length > 102400) { this.formNotice = this.$t('course.payloadTooLarge'); return; }
      this.runCourseMutation({ action: editing ? 'edit:' + f.courseId : 'key:' + f.courseKey,
        target: editing ? { courseId: f.courseId } : { courseKey: f.courseKey }, form: f,
        request: (ok, fail) => editing ? Api.course.updateCourse(f.courseId, payload, ok, fail) : Api.course.createCourse(payload, ok, fail),
        success: () => { this.dialogVisible = false; this.$message.success(this.$t(editing ? 'course.updated' : 'course.created')); this.fetchList(); },
      });
    },
    confirmDelete(row) {
      const id = row.courseId; const key = row.courseKey;
      const action = 'delete:' + id;
      if (this.actionPending[action]) return;
      this.actionPending = { ...this.actionPending, [action]: true };
      this.$confirm(this.$t('course.deleteConfirm', { key }), this.$t('course.delete'), { type: 'warning' })
        .then(() => {
          this.actionPending = { ...this.actionPending, [action]: false };
          this.runCourseMutation({ action, target: { courseId: id },
            request: (ok, fail) => Api.course.deleteCourse(id, ok, fail),
            success: () => { this.$message.success(this.$t('course.deleted')); this.fetchList(); },
          });
        }).catch(() => { this.actionPending = { ...this.actionPending, [action]: false }; });
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
.course-filter-panel {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.filter-input {
  width: 190px;
}
.filter-input.wide {
  width: 280px;
}
.course-stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
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
.quality-preview strong {
  color: #409eff;
}
.attention-preview strong {
  color: #f56c6c;
}
.quality-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}
.age-band-alert {
  margin-top: 8px;
}
.quality-alert {
  margin-bottom: 12px;
}
.lifecycle-dialog ::v-deep .el-dialog {
  max-width: calc(100vw - 24px);
}
.lifecycle-dialog p {
  text-align: left;
  word-break: normal;
  line-height: 1.5;
  overflow-wrap: anywhere;
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
@media (max-width: 720px) {
  .operation-bar,
  .main-wrapper {
    padding-left: 12px;
    padding-right: 12px;
  }
  .left-title,
  .right-operations,
  .course-filter-panel {
    align-items: stretch;
    flex-direction: column;
    width: 100%;
  }
  .filter-input,
  .filter-input.wide,
  .course-filter-panel .el-button {
    width: 100%;
  }
  .course-stats {
    grid-template-columns: 1fr;
  }
}
</style>
