<template>
  <div class="detail-page">
    <HeaderBar />
    <main v-loading="loading">
      <el-button type="text" icon="el-icon-arrow-left" @click="$router.push({ name: 'LessonVisualLibrary' })">{{ $t('visual.libraryTitle') }}</el-button>
      <div class="title-row"><div><p class="eyebrow">{{ asset.category }}</p><h1>{{ assetKey }}</h1><p>{{ asset.title }}</p></div><el-tag :type="source.publicationState === 'published' ? 'success' : 'warning'">{{ source.publicationState }}</el-tag></div>
      <section class="facts">
        <div><span>{{ $t('visual.pinnedVersion') }}</span><b>v{{ source.version || 0 }}</b></div><div><span>{{ $t('visual.dimensions') }}</span><b>{{ source.width }} × {{ source.height }}</b></div>
        <div><span>{{ $t('visual.bytes') }}</span><b>{{ formatBytes(source.bytes) }}</b></div><div><span>SHA-256</span><b class="mono">{{ shaPrefix(source.sha256) }}</b></div>
        <div><span>{{ $t('visual.profile') }}</span><b>{{ source.profile || '—' }}</b></div><div><span>{{ $t('visual.usageCount') }}</span><b>{{ usages.length }}</b></div>
      </section>
      <el-row :gutter="18">
        <el-col :md="14"><el-card shadow="never"><h2>{{ $t('visual.derivativeComparison') }}</h2><el-table :data="comparisonRows"><el-table-column prop="label" :label="$t('visual.metric')" /><el-table-column prop="source" :label="$t('visual.source')" /><el-table-column prop="robot" :label="$t('visual.robotDerivative')" /></el-table></el-card></el-col>
        <el-col :md="10"><el-card shadow="never"><h2>{{ $t('visual.replaceTitle') }}</h2>
          <el-form label-position="top"><el-form-item :label="$t('visual.sourceVersion')"><el-select v-model="form.sourceVersionId" style="width:100%"><el-option v-for="v in versions" :key="v.versionId" :label="versionLabel(v)" :value="v.versionId" /></el-select></el-form-item>
          <el-form-item :label="$t('visual.targetVersion')"><el-select v-model="form.targetVersionId" style="width:100%"><el-option v-for="v in targetVersions" :key="v.versionId" :label="versionLabel(v)" :value="v.versionId" /></el-select></el-form-item>
          <el-form-item :label="$t('visual.mode')"><el-radio-group v-model="form.mode"><el-radio-button v-for="mode in modes" :key="mode" :label="mode" /></el-radio-group></el-form-item>
          <el-form-item v-if="form.mode !== 'global'" :label="$t('visual.affectedLessons')"><el-select v-model="form.lessonIds" :multiple="form.mode === 'selectedLessons'" filterable style="width:100%" :placeholder="$t('visual.lessonIdsHint')"><el-option v-for="lesson in replacementLessons" :key="lesson.lessonId" :label="lessonLabel(lesson)" :value="lesson.lessonId" /></el-select></el-form-item>
          <el-alert v-if="form.mode === 'cloneForLesson'" :title="$t('visual.cloneHint')" type="info" :closable="false" show-icon />
          <p v-if="impact.activeAssignments != null" class="active-impact">{{ $t('visual.activeAssignments') }}: <b>{{ impact.activeAssignments }}</b></p>
          <el-button class="replace-button" type="danger" :loading="submitting" :disabled="!replacementReady" @click="prepareReplacement">{{ $t('visual.reviewImpact') }}</el-button></el-form>
        </el-card></el-col>
      </el-row>
      <el-card shadow="never" class="affected"><h2>{{ $t('visual.affectedLessons') }}</h2><el-table :data="affectedLessons" empty-text="—"><el-table-column prop="courseKey" :label="$t('visual.course')" /><el-table-column prop="lessonKey" :label="$t('visual.lesson')" /><el-table-column prop="lessonVersion" :label="$t('visual.pinnedVersion')" width="100" /><el-table-column prop="lessonStatus" :label="$t('course.colStatus')" width="120"><template slot-scope="s"><el-tag :type="s.row.lessonStatus === 'draft' ? 'warning' : 'success'" size="mini">{{ s.row.lessonStatus }}</el-tag></template></el-table-column><el-table-column prop="activeAssignmentCount" :label="$t('visual.activeAssignments')" width="150" align="right" /></el-table></el-card>
      <AssetImpactDialog :visible="impactVisible" :impact="impact" :mode="reviewedRequest ? reviewedRequest.mode : form.mode" :confirming="submitting" @cancel="cancelImpact" @confirm="executeReplacement" />
    </main>
  </div>
</template>
<script>
import Api from '@/apis/api'; import HeaderBar from '@/components/HeaderBar.vue'; import AssetImpactDialog from '@/components/lesson/AssetImpactDialog.vue';
import { buildReplacementRequest, compareAssetVersions, lessonReplacementOptions, replacementNeedsImpact, replacementSelectionIsValid, REPLACEMENT_MODES, uniqueAffectedLessons } from '@/utils/lessonVisualLibraryState.mjs';
export default {
  name: 'LessonVisualAssetDetail', components: { HeaderBar, AssetImpactDialog },
  data: () => ({ loading: false, submitting: false, loadSequence: 0, reviewedRequest: null, assetDetail: {}, versions: [], usages: [], modes: REPLACEMENT_MODES, impactVisible: false, impact: {}, form: { sourceVersionId: '', targetVersionId: '', mode: 'global', lessonIds: [] } }),
  computed: {
    assetKey() { return this.$route.params.assetKey || ''; }, asset() { return { ...(this.versions[0] || {}), ...this.assetDetail }; },
    source() { return this.versions.find((v) => v.versionId === this.form.sourceVersionId) || this.asset; }, robot() { return this.versions.find((v) => v.profile === 'espTft') || this.source; },
    targetVersions() { return this.versions.filter((v) => v.versionId !== this.form.sourceVersionId); },
    affectedLessons() { return uniqueAffectedLessons(this.usages); },
    replacementLessons() { return lessonReplacementOptions(this.usages, this.form.mode); },
    replacementReady() { const ids = Array.isArray(this.form.lessonIds) ? this.form.lessonIds : [this.form.lessonIds]; return Boolean(this.form.sourceVersionId && this.form.targetVersionId && this.form.sourceVersionId !== this.form.targetVersionId && replacementSelectionIsValid(this.usages, this.form.mode, ids)); },
    comparisonRows() { const c = compareAssetVersions(this.source, this.robot); return [{ label: this.$t('visual.dimensions'), source: `${c.source.width} × ${c.source.height}`, robot: `${c.robot.width} × ${c.robot.height}` }, { label: this.$t('visual.bytes'), source: this.formatBytes(c.source.bytes), robot: this.formatBytes(c.robot.bytes) }, { label: 'SHA-256', source: c.source.shaPrefix, robot: c.robot.shaPrefix }]; },
  },
  watch: {
    'form.sourceVersionId'(value, previous) { if (previous && value !== previous) { this.clearReviewIfClosed(); this.form.lessonIds = []; if (this.form.targetVersionId === value) this.form.targetVersionId = (this.versions.find((v) => v.versionId !== value) || {}).versionId || ''; this.load(value); } },
    'form.targetVersionId'() { this.clearReviewIfClosed(); },
    'form.mode'(mode) { this.clearReviewIfClosed(); this.form.lessonIds = mode === 'cloneForLesson' ? '' : []; },
    'form.lessonIds': { deep: true, handler() { this.clearReviewIfClosed(); } },
  },
  mounted() { this.load(); },
  methods: {
    load(sourceVersionId) { const sequence = ++this.loadSequence; this.loading = true; Api.lesson.getVisualAssetDetail(this.assetKey, { sourceVersionId }, ({ asset, versions, usages, sourceVersionId: selected }) => { if (sequence !== this.loadSequence) return; this.assetDetail = asset; this.versions = versions.sort((a, b) => b.version - a.version); this.usages = usages; const source = this.versions.find((v) => v.versionId === selected) || this.versions[0] || {}; this.form.sourceVersionId = source.versionId || ''; if (!this.form.targetVersionId || this.form.targetVersionId === source.versionId) this.form.targetVersionId = (this.versions.find((v) => v.versionId !== source.versionId) || {}).versionId || ''; this.loading = false; }, (m) => { if (sequence !== this.loadSequence) return; this.loading = false; this.$message.error(m || this.$t('visual.loadFail')); }); },
    request() { const ids = Array.isArray(this.form.lessonIds) ? this.form.lessonIds : [this.form.lessonIds]; return buildReplacementRequest(this.form.sourceVersionId, this.form.targetVersionId, this.form.mode, ids); },
    prepareReplacement() { let request; this.reviewedRequest = null; try { request = this.request(); } catch (e) { this.$message.warning(e.message); return; } if (!replacementNeedsImpact(request.mode)) { this.replaceRequest(request); return; } this.submitting = true; const { targetVersionId, ...impactRequest } = request; Api.lesson.visualReplacementImpact(impactRequest, (impact) => { this.reviewedRequest = JSON.parse(JSON.stringify(request)); this.impact = impact; this.impactVisible = true; this.submitting = false; }, (m) => { this.reviewedRequest = null; this.submitting = false; this.$message.error(m); }); },
    executeReplacement() { if (!this.reviewedRequest) { this.$message.warning(this.$t('visual.reviewImpact')); return; } this.replaceRequest(JSON.parse(JSON.stringify(this.reviewedRequest))); },
    replaceRequest(request) { this.submitting = true; Api.lesson.replaceVisualAsset(request, () => { this.submitting = false; this.impactVisible = false; this.reviewedRequest = null; this.$message.success(this.$t('visual.replaced')); this.load(); }, (m) => { this.reviewedRequest = null; this.submitting = false; this.$message.error(m); }); },
    cancelImpact() { this.impactVisible = false; this.reviewedRequest = null; },
    clearReviewIfClosed() { if (!this.impactVisible) this.reviewedRequest = null; },
    versionLabel(v) { return `v${v.version} · ${v.profile} · ${v.publicationState}`; }, shaPrefix(value) { return String(value || '').slice(0, 12) || '—'; },
    lessonLabel(lesson) { return `${lesson.courseKey} / ${lesson.lessonKey} · v${lesson.lessonVersion} · ${lesson.lessonStatus}`; },
    formatBytes(value) { const n = Number(value || 0); return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`; },
  },
};
</script>
<style scoped>
.detail-page{min-height:100vh;background:#f5f4ef}.detail-page main{max-width:1450px;margin:auto;padding:24px 32px 50px}.title-row{display:flex;justify-content:space-between;align-items:center;margin:10px 0 20px}.title-row h1{font:38px Georgia,serif;color:#17372f;margin:3px 0}.title-row p{margin:0;color:#66756f}.eyebrow{font-size:11px!important;letter-spacing:2px;text-transform:uppercase;color:#b4652d!important}.facts{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:18px}.facts div{background:#fff;border:1px solid #e5e1d7;border-radius:12px;padding:16px}.facts span{display:block;color:#7a817e;font-size:12px;margin-bottom:7px}.facts b{font-size:17px}.mono{font-family:monospace}.el-card{border:0;border-radius:14px;margin-bottom:18px}.replace-button{width:100%;margin-top:18px}.active-impact{padding:10px;border-radius:8px;background:#fff1f0;color:#cf1322}.affected .el-tag{margin-right:8px}.muted{color:#7a817e}@media(max-width:900px){.detail-page main{padding:18px}.facts{grid-template-columns:repeat(2,1fr)}}
</style>
