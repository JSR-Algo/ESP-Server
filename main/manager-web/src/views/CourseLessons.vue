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
              <el-button
                v-if="scope.row.status === 'published'"
                type="text"
                size="small"
                @click="openAssignmentDialog(scope.row)"
              >
                {{ $t('lesson.assignToChild') }}
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

    <el-dialog :title="dialogTitle" :visible.sync="dialogVisible" width="560px" @closed="resetForm">
      <el-form :model="form" label-width="150px" size="small">
        <el-form-item :label="$t('lesson.colKey')" required>
          <el-input v-model="form.lessonKey" :disabled="editingMetadata" :placeholder="$t('lesson.keyPlaceholder')" />
        </el-form-item>
        <el-form-item :label="$t('lesson.colTitle')" required>
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item :label="$t('course.colLocale')" required>
          <el-select
            v-model="form.locale"
            data-testid="lesson-locale"
            filterable
            allow-create
            default-first-option
            :placeholder="defaultLocale"
            style="width: 100%"
          >
            <el-option v-for="l in locales" :key="l" :label="l" :value="l" />
          </el-select>
        </el-form-item>
        <el-form-item :label="$t('course.colAgeBand')" required>
          <el-select
            v-model="form.ageBand"
            data-testid="lesson-age-band"
            filterable
            allow-create
            default-first-option
            :placeholder="defaultAgeBand"
            style="width: 100%"
          >
            <el-option v-for="b in ageBands" :key="b" :label="b" :value="b" />
          </el-select>
          <!-- The assignment age gate fails open on an unparseable band, so the
               only place this can surface is here, before the lesson is saved. -->
          <el-alert
            v-if="ageBandSeverity === 'unenforced'"
            data-testid="lesson-age-band-unenforced"
            type="error"
            :title="$t('lesson.ageBandUnenforced')"
            :closable="false"
            show-icon
            class="age-band-alert"
          />
          <span
            v-else-if="ageBandSeverity === 'custom'"
            data-testid="lesson-age-band-custom"
            class="muted small form-help"
          >{{ $t('lesson.ageBandCustom', { min: ageBandMinimum }) }}</span>
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

    <el-dialog
      :title="$t('lesson.assignDialogTitle')"
      :visible.sync="assignmentDialog.visible"
      width="720px"
      @closed="resetAssignmentDialog"
    >
      <div v-if="assignmentDialog.lesson" class="assignment-dialog">
        <div class="assignment-lesson">
          <strong>{{ assignmentDialog.lesson.title || assignmentDialog.lesson.lessonKey }}</strong>
          <span class="muted small">{{ assignmentDialog.lesson.lessonKey }} · v{{ assignmentDialog.lesson.lessonVersion }}</span>
        </div>
        <el-select
          v-model="assignmentDialog.childId"
          filterable
          remote
          clearable
          :remote-method="searchAssignmentLearners"
          :loading="assignmentDialog.loadingLearners"
          :disabled="assignmentDialog.submitting"
          :placeholder="$t('lesson.assignChildPlaceholder')"
          style="width: 100%"
          @change="handleAssignmentChildChange"
        >
          <el-option
            v-for="learner in assignmentDialog.learners"
            :key="learner.childId"
            :label="learner.childName || learner.childId"
            :value="learner.childId"
          />
        </el-select>
        <el-alert
          v-if="assignmentDialog.statusMessage"
          :type="assignmentDialog.statusType"
          :title="assignmentDialog.statusMessage"
          :closable="false"
          show-icon
          class="assignment-alert"
        >
          <el-button v-if="assignmentDialog.statusType === 'warning'" type="text" size="mini" @click="openMonitoring(assignmentDialog.lesson)">
            {{ $t('lesson.monitor') }}
          </el-button>
        </el-alert>
        <el-table
          v-loading="assignmentDialog.loadingDevices"
          :data="assignmentDialog.devices"
          stripe
          class="assignment-devices"
          style="width: 100%"
        >
          <el-table-column prop="displayName" :label="$t('lesson.assignDevice')" min-width="170" show-overflow-tooltip />
          <el-table-column prop="serialNumber" :label="$t('lesson.assignSerial')" min-width="130" show-overflow-tooltip />
          <el-table-column :label="$t('lesson.assignAvailability')" width="150">
            <template slot-scope="scope">
              <el-tag :type="availabilityType(scope.row.availability)" size="small">
                {{ availabilityLabel(scope.row.availability) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.assignCurrent')" min-width="220">
            <template slot-scope="scope">
              <span v-if="scope.row.currentAssignment">
                {{ scope.row.currentAssignment.lessonTitle || scope.row.currentAssignment.lessonId }}
                <span class="muted small">· {{ scope.row.currentAssignment.state }}</span>
              </span>
              <span v-else class="muted small">{{ $t('lesson.assignNoCurrent') }}</span>
            </template>
          </el-table-column>
          <el-table-column :label="$t('lesson.colActions')" width="120" align="right">
            <template slot-scope="scope">
              <el-button
                type="primary"
                size="mini"
                :disabled="scope.row.availability === 'busy' || assignmentDialog.submitting"
                :loading="assignmentDialog.submittingDeviceId === scope.row.deviceId"
                @click="createLessonAssignment(scope.row)"
              >
                {{ scope.row.availability === 'already_assigned' ? $t('lesson.assignReuse') : $t('lesson.assign') }}
              </el-button>
            </template>
          </el-table-column>
          <template slot="empty">
            <span class="muted">{{ assignmentDialog.childId ? $t('lesson.assignNoDevices') : $t('lesson.assignChooseChild') }}</span>
          </template>
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import Api from '@/apis/api';
import { isUncertainNestError } from '@/apis/nestHttp';
import {
  AGE_BANDS,
  DEFAULT_AGE_BAND,
  DEFAULT_LOCALE,
  LOCALES,
  ageBandMinimum,
  ageBandSeverity,
} from '@/utils/courseTaxonomy.mjs';

const blankForm = () => ({
  lessonId: '',
  lessonKey: '',
  title: '',
  locale: DEFAULT_LOCALE,
  ageBand: DEFAULT_AGE_BAND,
  topicTags: '',
  difficultyBand: '',
  estimatedDurationSec: null,
});

const blankAssignmentDialog = () => ({
  visible: false,
  lesson: null,
  learners: [],
  childId: '',
  devices: [],
  loadingLearners: false,
  loadingDevices: false,
  submitting: false,
  submittingDeviceId: '',
  statusMessage: '',
  statusType: 'info',
  dialogSessionId: 0,
  learnerRequestToken: 0,
  deviceRequestToken: 0,
  submitRequestId: 0,
  submitContext: null,
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
      ageBands: AGE_BANDS,
      locales: LOCALES,
      defaultAgeBand: DEFAULT_AGE_BAND,
      defaultLocale: DEFAULT_LOCALE,
      assignmentDialog: blankAssignmentDialog(),
    };
  },
  computed: {
    ageBandSeverity() {
      return ageBandSeverity(this.form.ageBand);
    },
    ageBandMinimum() {
      return ageBandMinimum(this.form.ageBand);
    },
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
      Api.lesson.listAuthoritativeLessons(
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
    openAssignmentDialog(row) {
      this.resetAssignmentDialog();
      this.assignmentDialog.visible = true;
      this.assignmentDialog.lesson = row;
      this.searchAssignmentLearners('');
    },
    resetAssignmentDialog() {
      const previous = this.assignmentDialog || {};
      const nextDialog = blankAssignmentDialog();
      nextDialog.dialogSessionId = Number(previous.dialogSessionId || 0) + 1;
      nextDialog.learnerRequestToken = Number(previous.learnerRequestToken || 0) + 1;
      nextDialog.deviceRequestToken = Number(previous.deviceRequestToken || 0) + 1;
      nextDialog.submitRequestId = Number(previous.submitRequestId || 0) + 1;
      this.assignmentDialog = nextDialog;
    },
    setAssignmentStatus(type, key) {
      this.assignmentDialog.statusType = type;
      this.assignmentDialog.statusMessage = key ? this.$t(key) : '';
    },
    isAssignmentSubmitCurrent(context) {
      return Boolean(
        context
        && this.assignmentDialog.visible
        && this.assignmentDialog.submitContext
        && this.assignmentDialog.submitContext.requestId === context.requestId
        && this.assignmentDialog.submitContext.childId === context.childId
        && this.assignmentDialog.submitContext.lessonKey === context.lessonKey
        && this.assignmentDialog.submitContext.lessonVersion === context.lessonVersion
        && this.assignmentDialog.submitContext.deviceId === context.deviceId
        && this.assignmentDialog.submitContext.dialogSessionId === context.dialogSessionId
      );
    },
    isAssignmentEligibilityCurrent(context) {
      return Boolean(
        context
        && this.assignmentDialog.visible
        && this.assignmentDialog.dialogSessionId === context.dialogSessionId
        && this.assignmentDialog.childId === context.childId
        && this.assignmentDialog.lesson
        && this.assignmentDialog.lesson.lessonKey === context.lessonKey
        && this.assignmentDialog.lesson.lessonVersion === context.lessonVersion,
      );
    },
    isAssignmentResponseExact(context, assignment) {
      return Boolean(
        context
        && assignment
        && assignment.deviceId === context.deviceId
        && assignment.childId === context.childId
        && assignment.lessonId === context.lessonKey
        && Number(assignment.lessonVersion) === Number(context.lessonVersion)
        && assignment.profile === 'espTft'
        && ['ASSIGNED', 'PRELOADING', 'READY', 'RUNNING', 'PAUSED'].includes(assignment.state),
      );
    },
    releaseAssignmentSubmit(context) {
      if (!this.isAssignmentSubmitCurrent(context)) return false;
      this.assignmentDialog.submitting = false;
      this.assignmentDialog.submittingDeviceId = '';
      this.assignmentDialog.submitContext = null;
      return true;
    },
    searchAssignmentLearners(keyword) {
      const token = ++this.assignmentDialog.learnerRequestToken;
      const dialogSessionId = this.assignmentDialog.dialogSessionId;
      this.assignmentDialog.loadingLearners = true;
      Api.courseInsights.listLearners(
        { keyword, limit: 20 },
        (learners) => {
          if (!this.assignmentDialog.visible
            || dialogSessionId !== this.assignmentDialog.dialogSessionId
            || token !== this.assignmentDialog.learnerRequestToken) return;
          this.assignmentDialog.loadingLearners = false;
          this.assignmentDialog.learners = learners;
        },
        (msg) => {
          if (!this.assignmentDialog.visible
            || dialogSessionId !== this.assignmentDialog.dialogSessionId
            || token !== this.assignmentDialog.learnerRequestToken) return;
          this.assignmentDialog.loadingLearners = false;
          this.$message.error(msg || this.$t('lesson.assignLearnersLoadFail'));
        },
      );
    },
    handleAssignmentChildChange() {
      if (this.assignmentDialog.submitting) return;
      this.assignmentDialog.devices = [];
      this.assignmentDialog.submitting = false;
      this.assignmentDialog.submittingDeviceId = '';
      this.setAssignmentStatus('info', '');
      if (this.assignmentDialog.childId) {
        this.refreshAssignmentEligibility();
      }
    },
    refreshAssignmentEligibility(done, context) {
      const row = context
        ? { lessonKey: context.lessonKey, lessonVersion: context.lessonVersion }
        : this.assignmentDialog.lesson;
      const childId = context ? context.childId : this.assignmentDialog.childId;
      if (!row || !childId || (context && !this.isAssignmentSubmitCurrent(context))) {
        if (done) done([]);
        return;
      }
      const eligibilityContext = context || {
        dialogSessionId: this.assignmentDialog.dialogSessionId,
        childId,
        lessonKey: row.lessonKey,
        lessonVersion: row.lessonVersion,
      };
      const token = ++this.assignmentDialog.deviceRequestToken;
      this.assignmentDialog.loadingDevices = true;
      Api.lesson.eligibleDevices(
        { childId, lessonId: row.lessonKey, lessonVersion: row.lessonVersion },
        (devices) => {
          if (token !== this.assignmentDialog.deviceRequestToken) return;
          if (context && !this.isAssignmentSubmitCurrent(context)) return;
          if (!context && !this.isAssignmentEligibilityCurrent(eligibilityContext)) return;
          this.assignmentDialog.loadingDevices = false;
          this.assignmentDialog.devices = devices;
          if (done) done(devices);
        },
        (msg) => {
          if (token !== this.assignmentDialog.deviceRequestToken) return;
          if (context && !this.isAssignmentSubmitCurrent(context)) return;
          if (!context && !this.isAssignmentEligibilityCurrent(eligibilityContext)) return;
          this.assignmentDialog.loadingDevices = false;
          this.setAssignmentStatus('error', 'lesson.assignEligibilityLoadFail');
          this.$message.error(msg || this.$t('lesson.assignEligibilityLoadFail'));
          if (done) done([]);
        },
      );
    },
    availabilityType(availability) {
      if (availability === 'available') return 'success';
      if (availability === 'already_assigned') return 'warning';
      return 'danger';
    },
    availabilityLabel(availability) {
      if (availability === 'available') return this.$t('lesson.assignAvailable');
      if (availability === 'already_assigned') return this.$t('lesson.assignAlready');
      return this.$t('lesson.assignBusy');
    },
    createLessonAssignment(device) {
      if (this.assignmentDialog.submitting || device.availability === 'busy') return;
      const row = this.assignmentDialog.lesson;
      const childId = this.assignmentDialog.childId;
      if (!row || !childId) return;
      const submitContext = {
        requestId: this.assignmentDialog.submitRequestId + 1,
        childId,
        lessonKey: row.lessonKey,
        lessonVersion: row.lessonVersion,
        deviceId: device.deviceId,
        dialogSessionId: this.assignmentDialog.dialogSessionId,
      };
      this.assignmentDialog.submitRequestId = submitContext.requestId;
      this.assignmentDialog.submitContext = submitContext;
      this.assignmentDialog.submitting = true;
      this.assignmentDialog.submittingDeviceId = device.deviceId;
      this.setAssignmentStatus('info', '');
      Api.lesson.createAssignment(
        {
          deviceId: submitContext.deviceId,
          lessonId: submitContext.lessonKey,
          lessonVersion: submitContext.lessonVersion,
          childId: submitContext.childId,
          profile: 'espTft',
        },
        ({ created, assignment }) => {
          if (!this.isAssignmentSubmitCurrent(submitContext)) return;
          if (!this.isAssignmentResponseExact(submitContext, assignment)) {
            this.releaseAssignmentSubmit(submitContext);
            this.setAssignmentStatus('error', 'lesson.assignUncertainRetry');
            this.refreshAssignmentEligibility();
            return;
          }
          if (!this.releaseAssignmentSubmit(submitContext)) return;
          if (created === false) {
            this.setAssignmentStatus('success', 'lesson.assignAlreadySuccess');
          } else {
            this.setAssignmentStatus('success', 'lesson.assignCreatedSuccess');
          }
          this.refreshAssignmentEligibility();
        },
        (msg, error) => {
          if (!this.isAssignmentSubmitCurrent(submitContext)) return;
          const status = Number(error && (error.status ?? (error.response && error.response.status)));
          if (status === 409) {
            this.setAssignmentStatus('warning', 'lesson.assignConflict');
            this.refreshAssignmentEligibility(() => {
              this.releaseAssignmentSubmit(submitContext);
            }, submitContext);
            return;
          }
          if (isUncertainNestError(error)) {
            this.reconcileAssignmentAfterUncertainCreate(submitContext);
            return;
          }
          this.releaseAssignmentSubmit(submitContext);
          this.setAssignmentStatus('error', '');
          this.$message.error(msg || this.$t('lesson.assignFailed'));
        },
      );
    },
    reconcileAssignmentAfterUncertainCreate(context) {
      if (!this.isAssignmentSubmitCurrent(context)) return;
      this.refreshAssignmentEligibility((devices) => {
        if (!this.isAssignmentSubmitCurrent(context)) return;
        const reconciled = devices.find((candidate) => candidate.deviceId === context.deviceId);
        this.releaseAssignmentSubmit(context);
        if (reconciled && reconciled.availability === 'already_assigned') {
          this.setAssignmentStatus('success', 'lesson.assignAlreadySuccess');
          return;
        }
        this.setAssignmentStatus('error', 'lesson.assignUncertainRetry');
      }, context);
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
.age-band-alert {
  margin-top: 8px;
}
.assignment-dialog {
  display: grid;
  gap: 12px;
}
.assignment-lesson {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.assignment-alert {
  margin: 2px 0;
}
.assignment-devices {
  margin-top: 4px;
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
