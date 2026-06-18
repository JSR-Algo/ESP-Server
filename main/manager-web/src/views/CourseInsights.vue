<template>
  <div class="welcome">
    <HeaderBar />
    <div class="operation-bar">
      <div class="left-title">
        <h2 class="page-title">{{ $t('insights.pageTitle') }}</h2>
        <el-tabs v-model="activeTab" type="card" @tab-click="handleTabChange">
          <el-tab-pane :label="$t('insights.tabLearners')" name="learners" />
          <el-tab-pane :label="$t('insights.tabQuality')" name="quality" />
        </el-tabs>
      </div>
      <div class="right-operations">
        <span class="backend-hint">{{ $t('insights.backendHint') }}</span>
        <el-button size="small" @click="$router.push('/course-management')">
          {{ $t('insights.backCourses') }}
        </el-button>
        <el-button type="primary" size="small" :loading="refreshing" @click="refreshCurrent">
          {{ $t('insights.refresh') }}
        </el-button>
      </div>
    </div>

    <div class="main-wrapper" v-if="activeTab === 'learners'">
      <div class="split-layout">
        <el-card class="content-area learner-list" shadow="never">
          <div class="filter-row">
            <el-input
              v-model="learnerKeyword"
              :placeholder="$t('insights.searchLearner')"
              size="small"
              clearable
              @keyup.enter.native="fetchLearners"
            />
            <el-button size="small" type="primary" @click="fetchLearners">{{ $t('insights.search') }}</el-button>
          </div>
          <el-table v-loading="learnersLoading" :data="learners" stripe highlight-current-row @current-change="selectLearner">
            <el-table-column prop="childName" :label="$t('insights.child')" min-width="150">
              <template slot-scope="scope">
                <div class="primary-text">{{ scope.row.childName }}</div>
                <div class="muted small">{{ scope.row.parentEmail || scope.row.parentId }}</div>
              </template>
            </el-table-column>
            <el-table-column :label="$t('insights.personality')" min-width="210">
              <template slot-scope="scope">
                <el-tag v-for="tag in scope.row.personality.interests" :key="tag" size="mini" class="tag-gap" effect="plain">{{ tag }}</el-tag>
                <el-tag v-if="scope.row.personality.learningStyle" size="mini" type="success" effect="plain">{{ scope.row.personality.learningStyle }}</el-tag>
                <el-tag v-if="scope.row.personality.parentCareer" size="mini" type="warning" effect="plain">{{ scope.row.personality.parentCareer }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="$t('insights.progress')" width="110">
              <template slot-scope="scope">{{ scope.row.stats.completionRate }}%</template>
            </el-table-column>
            <template slot="empty">
              <span class="muted">{{ $t('insights.noLearners') }}</span>
            </template>
          </el-table>
        </el-card>

        <el-card class="content-area detail-panel" shadow="never">
          <div v-if="selectedLearner.childId">
            <div class="detail-head">
              <div>
                <h3>{{ selectedLearner.childName }}</h3>
                <p class="muted small">{{ selectedLearner.childId }}</p>
              </div>
              <div class="metric-line">
                <span>{{ $t('insights.age') }}: <strong>{{ selectedLearner.age || '-' }}</strong></span>
                <span>{{ $t('insights.assignments') }}: <strong>{{ selectedLearner.stats.assignments }}</strong></span>
                <span>{{ $t('insights.completed') }}: <strong>{{ selectedLearner.stats.completedAssignments }}</strong></span>
              </div>
            </div>

            <el-form :model="personalityForm" label-width="150px" size="small" class="personality-form">
              <el-form-item :label="$t('insights.interests')">
                <el-input v-model="personalityForm.interestsText" :placeholder="$t('insights.interestsHelp')" />
              </el-form-item>
              <el-form-item :label="$t('insights.learningStyle')">
                <el-select v-model="personalityForm.learningStyle" clearable>
                  <el-option label="visual" value="visual" />
                  <el-option label="audio" value="audio" />
                  <el-option label="interactive" value="interactive" />
                </el-select>
              </el-form-item>
              <el-form-item :label="$t('insights.parentCareer')">
                <el-select v-model="personalityForm.parentCareer" clearable filterable>
                  <el-option label="teacher" value="teacher" />
                  <el-option label="engineer" value="engineer" />
                  <el-option label="healthcare" value="healthcare" />
                  <el-option label="business" value="business" />
                  <el-option label="artist" value="artist" />
                </el-select>
              </el-form-item>
              <el-form-item :label="$t('insights.vocabularyLevel')">
                <el-select v-model="personalityForm.vocabularyLevel" clearable>
                  <el-option label="beginner" value="beginner" />
                  <el-option label="basic" value="basic" />
                  <el-option label="intermediate" value="intermediate" />
                  <el-option label="advanced" value="advanced" />
                </el-select>
              </el-form-item>
              <el-form-item :label="$t('insights.attentionSpan')">
                <el-input-number v-model="personalityForm.attentionSpanSec" :min="30" :max="3600" :step="30" />
              </el-form-item>
              <el-form-item>
                <el-button type="primary" :loading="savingPersonality" @click="savePersonality">{{ $t('insights.savePersonality') }}</el-button>
                <el-button :loading="previewLoading" @click="fetchPreview">{{ $t('insights.previewLessons') }}</el-button>
              </el-form-item>
            </el-form>

            <div class="preview-toolbar">
              <el-input
                v-model="previewKeyword"
                :placeholder="$t('insights.searchLessonKeyword')"
                size="small"
                clearable
                @keyup.enter.native="fetchPreview"
              />
              <el-button size="small" :loading="previewLoading" @click="fetchPreview">{{ $t('insights.search') }}</el-button>
            </div>

            <el-table v-loading="previewLoading" :data="previewLessons" stripe size="small" class="preview-table">
              <el-table-column prop="rank" :label="$t('insights.rank')" width="70" />
              <el-table-column prop="title" :label="$t('insights.lesson')" min-width="180">
                <template slot-scope="scope">
                  <div class="primary-text">{{ scope.row.title }}</div>
                  <div class="muted small">{{ scope.row.courseKey }} · {{ scope.row.lessonKey }}</div>
                </template>
              </el-table-column>
              <el-table-column :label="$t('insights.match')" min-width="190">
                <template slot-scope="scope">
                  <el-tag size="mini" type="success">{{ scope.row.suitabilityScore }}</el-tag>
                  <el-tag v-for="tag in scope.row.matchedTopics" :key="tag" size="mini" class="tag-gap" effect="plain">{{ tag }}</el-tag>
                  <span v-if="scope.row.difficultyMatch" class="pill">{{ $t('insights.difficultyFit') }}</span>
                  <span v-if="scope.row.durationFit" class="pill">{{ $t('insights.durationFit') }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="reasonCode" :label="$t('insights.reason')" width="150" />
              <template slot="empty">
                <span class="muted">{{ $t('insights.noPreview') }}</span>
              </template>
            </el-table>
          </div>
          <div v-else class="empty-state">{{ $t('insights.selectLearner') }}</div>
        </el-card>
      </div>
    </div>

    <div class="main-wrapper" v-else>
      <el-card class="content-area" shadow="never">
        <div class="quality-toolbar">
          <el-select v-model="qualityWindow" size="small" class="window-select" @change="fetchQuality">
            <el-option :label="$t('insights.window7')" :value="7" />
            <el-option :label="$t('insights.window14')" :value="14" />
            <el-option :label="$t('insights.window30')" :value="30" />
            <el-option :label="$t('insights.window90')" :value="90" />
          </el-select>
          <el-input
            v-model="qualityKeyword"
            :placeholder="$t('insights.searchCourseKeyword')"
            size="small"
            clearable
            class="quality-search"
            @keyup.enter.native="fetchQuality"
          />
          <el-select v-model="qualityRiskFilter" size="small" class="risk-select">
            <el-option :label="$t('insights.riskAll')" value="all" />
            <el-option :label="$t('insights.riskAttention')" value="attention" />
            <el-option :label="$t('insights.riskWatch')" value="watch" />
            <el-option :label="$t('insights.riskHealthy')" value="healthy" />
          </el-select>
          <el-button size="small" type="primary" @click="fetchQuality">{{ $t('insights.search') }}</el-button>
        </div>
        <div class="quality-stats">
          <div class="stat-item">
            <span class="stat-label">{{ $t('insights.avgQuality') }}</span>
            <strong>{{ avgQuality }}</strong>
          </div>
          <div class="stat-item">
            <span class="stat-label">{{ $t('insights.totalAssignments') }}</span>
            <strong>{{ totalAssignments }}</strong>
          </div>
          <div class="stat-item">
            <span class="stat-label">{{ $t('insights.activeChildren') }}</span>
            <strong>{{ totalActiveChildren }}</strong>
          </div>
          <div class="stat-item attention-stat">
            <span class="stat-label">{{ $t('insights.needsAttention') }}</span>
            <strong>{{ attentionCourses }}</strong>
          </div>
        </div>
        <el-table v-loading="qualityLoading" :data="filteredQualityRows" stripe>
          <el-table-column prop="courseKey" :label="$t('course.colKey')" min-width="150" />
          <el-table-column prop="title" :label="$t('course.colTitle')" min-width="160" />
          <el-table-column :label="$t('insights.riskLevel')" width="130">
            <template slot-scope="scope">
              <el-tag size="mini" :type="riskTagType(scope.row.riskLevel)">{{ riskLabel(scope.row.riskLevel) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('insights.qualityScore')" width="120">
            <template slot-scope="scope">
              <el-progress :percentage="scope.row.qualityScore" :stroke-width="8" :show-text="true" />
            </template>
          </el-table-column>
          <el-table-column :label="$t('insights.scoreBreakdown')" min-width="230">
            <template slot-scope="scope">
              <div class="breakdown-row">
                <span>{{ $t('insights.completionShort') }}</span>
                <el-progress :percentage="scope.row.completionScore" :stroke-width="6" :show-text="false" />
                <strong>{{ scope.row.completionScore }}</strong>
              </div>
              <div class="breakdown-row">
                <span>{{ $t('insights.successShort') }}</span>
                <el-progress :percentage="scope.row.successScore" :stroke-width="6" :show-text="false" />
                <strong>{{ scope.row.successScore }}</strong>
              </div>
              <div class="breakdown-row">
                <span>{{ $t('insights.personalizationShort') }}</span>
                <el-progress :percentage="scope.row.personalizationScore" :stroke-width="6" :show-text="false" />
                <strong>{{ scope.row.personalizationScore }}</strong>
              </div>
            </template>
          </el-table-column>
          <el-table-column :label="$t('insights.coverage')" width="130">
            <template slot-scope="scope">{{ scope.row.personalizedLessonCount }}/{{ scope.row.lessonCount }} · {{ scope.row.personalizationRate }}%</template>
          </el-table-column>
          <el-table-column :label="$t('insights.completionRate')" width="120">
            <template slot-scope="scope">{{ scope.row.completionRate }}%</template>
          </el-table-column>
          <el-table-column :label="$t('insights.successRate')" width="120">
            <template slot-scope="scope">{{ scope.row.avgSuccessRate == null ? '-' : scope.row.avgSuccessRate + '%' }}</template>
          </el-table-column>
          <el-table-column :label="$t('insights.avgDuration')" width="120">
            <template slot-scope="scope">{{ scope.row.avgDurationSec == null ? '-' : formatDuration(scope.row.avgDurationSec) }}</template>
          </el-table-column>
          <el-table-column prop="assignments" :label="$t('insights.assignments')" width="110" />
          <el-table-column prop="failed" :label="$t('insights.failed')" width="90" />
          <el-table-column prop="running" :label="$t('insights.running')" width="90" />
          <el-table-column :label="$t('insights.issues')" min-width="190">
            <template slot-scope="scope">
              <span v-if="!scope.row.issueTags.length" class="muted small">{{ $t('insights.noIssues') }}</span>
              <el-tag v-for="tag in scope.row.issueTags" :key="tag" size="mini" type="warning" class="tag-gap" effect="plain">
                {{ issueLabel(tag) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column :label="$t('insights.lastActivity')" width="170">
            <template slot-scope="scope">{{ formatTime(scope.row.lastActivityAt) }}</template>
          </el-table-column>
          <template slot="empty">
            <span class="muted">{{ $t('insights.noQuality') }}</span>
          </template>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script>
import HeaderBar from '@/components/HeaderBar.vue';
import Api from '@/apis/api';

export default {
  name: 'CourseInsights',
  components: { HeaderBar },
  data() {
    return {
      activeTab: 'learners',
      learnerKeyword: '',
      learners: [],
      learnersLoading: false,
      selectedLearner: {},
      personalityForm: { interestsText: '', learningStyle: '', parentCareer: '', vocabularyLevel: '', attentionSpanSec: 120 },
      savingPersonality: false,
      previewKeyword: '',
      previewLessons: [],
      previewLoading: false,
      qualityRows: [],
      qualityLoading: false,
      qualityWindow: 30,
      qualityKeyword: '',
      qualityRiskFilter: 'all',
    };
  },
  computed: {
    refreshing() {
      return this.learnersLoading || this.qualityLoading;
    },
    avgQuality() {
      if (!this.qualityRows.length) return 0;
      return Math.round(this.qualityRows.reduce((sum, row) => sum + row.qualityScore, 0) / this.qualityRows.length);
    },
    totalAssignments() {
      return this.qualityRows.reduce((sum, row) => sum + row.assignments, 0);
    },
    totalActiveChildren() {
      return this.qualityRows.reduce((sum, row) => sum + row.activeChildren, 0);
    },
    attentionCourses() {
      return this.qualityRows.filter((row) => row.riskLevel === 'attention').length;
    },
    filteredQualityRows() {
      if (this.qualityRiskFilter === 'all') return this.qualityRows;
      return this.qualityRows.filter((row) => row.riskLevel === this.qualityRiskFilter);
    },
  },
  created() {
    if (this.$route.query.tab === 'quality') this.activeTab = 'quality';
    this.fetchLearners();
    this.fetchQuality();
  },
  methods: {
    handleTabChange() {
      this.refreshCurrent();
    },
    refreshCurrent() {
      if (this.activeTab === 'quality') this.fetchQuality();
      else this.fetchLearners();
    },
    fetchLearners() {
      this.learnersLoading = true;
      Api.courseInsights.listLearners(
        { keyword: this.learnerKeyword.trim(), limit: 200 },
        (rows) => {
          this.learnersLoading = false;
          this.learners = rows;
          if (!this.selectedLearner.childId && rows.length) this.selectLearner(rows[0]);
        },
        (msg) => {
          this.learnersLoading = false;
          this.$message.error(msg || this.$t('insights.loadLearnersFail'));
        },
      );
    },
    selectLearner(row) {
      if (!row) return;
      this.selectedLearner = row;
      this.personalityForm = {
        interestsText: row.personality.interests.join(', '),
        learningStyle: row.personality.learningStyle,
        parentCareer: row.personality.parentCareer,
        vocabularyLevel: row.personality.vocabularyLevel,
        attentionSpanSec: row.personality.attentionSpanSec || 120,
      };
      this.fetchPreview();
    },
    parseInterests() {
      return this.personalityForm.interestsText
        .split(',')
        .map((s) => s.trim().toLowerCase().replace(/\s+/g, '-'))
        .filter(Boolean);
    },
    savePersonality() {
      if (!this.selectedLearner.childId) return;
      this.savingPersonality = true;
      Api.courseInsights.updateLearnerPersonality(
        this.selectedLearner.childId,
        {
          interests: this.parseInterests(),
          learningStyle: this.personalityForm.learningStyle,
          parentCareer: this.personalityForm.parentCareer,
          vocabularyLevel: this.personalityForm.vocabularyLevel,
          attentionSpanSec: this.personalityForm.attentionSpanSec,
        },
        (learner) => {
          this.savingPersonality = false;
          this.selectedLearner = learner;
          this.$message.success(this.$t('insights.personalitySaved'));
          this.fetchLearners();
          this.fetchPreview();
        },
        (msg) => {
          this.savingPersonality = false;
          this.$message.error(msg || this.$t('insights.saveFail'));
        },
      );
    },
    fetchPreview() {
      if (!this.selectedLearner.childId) return;
      this.previewLoading = true;
      Api.courseInsights.previewLearnerLessons(
        this.selectedLearner.childId,
        { keyword: this.previewKeyword.trim(), limit: 50 },
        (payload) => {
          this.previewLoading = false;
          this.previewLessons = payload.lessons;
        },
        (msg) => {
          this.previewLoading = false;
          this.$message.error(msg || this.$t('insights.previewFail'));
        },
      );
    },
    fetchQuality() {
      this.qualityLoading = true;
      Api.courseInsights.getCourseQuality(
        { windowDays: this.qualityWindow, keyword: this.qualityKeyword.trim() },
        (rows) => {
          this.qualityLoading = false;
          this.qualityRows = rows;
        },
        (msg) => {
          this.qualityLoading = false;
          this.$message.error(msg || this.$t('insights.qualityFail'));
        },
      );
    },
    formatTime(value) {
      if (!value) return '-';
      const d = new Date(value);
      if (Number.isNaN(d.getTime())) return value;
      return d.toLocaleString();
    },
    formatDuration(sec) {
      const n = Number(sec || 0);
      if (n < 60) return `${n}s`;
      return `${Math.round(n / 60)}m`;
    },
    riskTagType(level) {
      if (level === 'attention') return 'danger';
      if (level === 'healthy') return 'success';
      return 'warning';
    },
    riskLabel(level) {
      return this.$t(`insights.${level === 'attention' ? 'riskAttention' : level === 'healthy' ? 'riskHealthy' : 'riskWatch'}`);
    },
    issueLabel(tag) {
      const key = `insights.issue.${tag}`;
      const value = this.$t(key);
      return value === key ? tag : value;
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
.right-operations {
  display: flex;
  align-items: center;
  gap: 10px;
}
.backend-hint,
.muted {
  color: #909399;
}
.small {
  font-size: 12px;
}
.main-wrapper {
  padding: 16px 24px;
}
.split-layout {
  display: grid;
  grid-template-columns: minmax(360px, 0.42fr) minmax(520px, 0.58fr);
  gap: 16px;
}
.filter-row,
.quality-toolbar,
.preview-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
.detail-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  border-bottom: 1px solid #ebeef5;
  padding-bottom: 12px;
  margin-bottom: 16px;
}
.detail-head h3 {
  margin: 0 0 4px;
  font-size: 18px;
}
.metric-line {
  display: flex;
  gap: 14px;
  align-items: center;
  color: #606266;
  font-size: 13px;
}
.personality-form {
  max-width: 760px;
}
.preview-table {
  margin-top: 8px;
}
.primary-text {
  font-weight: 600;
  color: #303133;
}
.tag-gap {
  margin-right: 4px;
  margin-bottom: 4px;
}
.pill {
  display: inline-block;
  margin-left: 4px;
  padding: 0 6px;
  border-radius: 8px;
  background: #f4f4f5;
  color: #606266;
  font-size: 12px;
  line-height: 20px;
}
.empty-state {
  min-height: 320px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #909399;
}
.quality-stats {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.stat-item {
  min-width: 160px;
  padding: 12px 14px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  background: #fafafa;
}
.stat-label {
  display: block;
  color: #909399;
  font-size: 12px;
  margin-bottom: 4px;
}
.stat-item strong {
  font-size: 20px;
}
.attention-stat strong {
  color: #f56c6c;
}
.window-select {
  width: 180px;
}
.quality-search {
  width: 280px;
}
.risk-select {
  width: 170px;
}
.breakdown-row {
  display: grid;
  grid-template-columns: 36px minmax(80px, 1fr) 32px;
  align-items: center;
  gap: 8px;
  min-height: 22px;
  font-size: 12px;
  color: #606266;
}
.breakdown-row strong {
  color: #303133;
  font-weight: 600;
}
@media (max-width: 1100px) {
  .split-layout {
    grid-template-columns: 1fr;
  }
  .operation-bar,
  .detail-head,
  .metric-line,
  .right-operations,
  .quality-toolbar,
  .preview-toolbar,
  .filter-row {
    flex-wrap: wrap;
  }
  .operation-bar {
    align-items: flex-start;
    gap: 12px;
  }
  .filter-row .el-input,
  .preview-toolbar .el-input,
  .quality-search,
  .window-select,
  .risk-select {
    width: 100%;
  }
  .filter-row .el-button,
  .preview-toolbar .el-button,
  .quality-toolbar .el-button {
    width: 100%;
  }
}
@media (max-width: 720px) {
  .operation-bar,
  .main-wrapper {
    padding-left: 12px;
    padding-right: 12px;
  }
  .left-title {
    align-items: flex-start;
    flex-direction: column;
    gap: 8px;
  }
  .metric-line {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }
}
</style>
