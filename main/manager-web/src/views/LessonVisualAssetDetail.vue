<template>
  <div class="detail-page">
    <HeaderBar />
    <main v-loading="loading">
      <el-button type="text" icon="el-icon-arrow-left" @click="$router.push({ name: 'LessonVisualLibrary' })">{{ $t('visual.libraryTitle') }}</el-button>
      <div class="title-row"><div><p class="eyebrow">{{ asset.category }}</p><h1>{{ assetKey }}</h1><p>{{ asset.title }}</p></div><el-tag :type="source.publicationState === 'published' ? 'success' : 'warning'">{{ source.publicationState }}</el-tag></div>
      <section class="facts">
        <div><span>{{ $t('visual.pinnedVersion') }}</span><b>v{{ source.version || 0 }}</b></div><div><span>{{ $t('visual.dimensions') }}</span><b>{{ source.width }} × {{ source.height }}</b></div>
        <div><span>{{ $t('visual.bytes') }}</span><b>{{ formatBytes(source.bytes) }}</b></div><div><span>SHA-256</span><b class="mono">{{ shaPrefix(source.sha256) }}</b></div>
        <div><span>{{ $t('visual.profile') }}</span><b>{{ source.profile || '—' }}</b></div><div><span>{{ $t('visual.usageCount') }}</span><b>{{ source.usageCount || 0 }}</b></div>
      </section>
      <el-row :gutter="18">
        <el-col :md="14"><el-card shadow="never"><h2>{{ $t('visual.derivativeComparison') }}</h2><el-table :data="comparisonRows"><el-table-column prop="label" :label="$t('visual.metric')" /><el-table-column prop="source" :label="$t('visual.source')" /><el-table-column prop="robot" :label="$t('visual.robotDerivative')" /></el-table></el-card></el-col>
        <el-col :md="10"><el-card shadow="never"><h2>{{ $t('visual.replaceTitle') }}</h2>
          <el-form label-position="top"><el-form-item :label="$t('visual.sourceVersion')"><el-select v-model="form.sourceVersionId" style="width:100%"><el-option v-for="v in versions" :key="v.versionId" :label="versionLabel(v)" :value="v.versionId" /></el-select></el-form-item>
          <el-form-item :label="$t('visual.targetVersion')"><el-select v-model="form.targetVersionId" style="width:100%"><el-option v-for="v in versions" :key="v.versionId" :label="versionLabel(v)" :value="v.versionId" /></el-select></el-form-item>
          <el-form-item :label="$t('visual.mode')"><el-radio-group v-model="form.mode"><el-radio-button v-for="mode in modes" :key="mode" :label="mode" /></el-radio-group></el-form-item>
          <el-form-item v-if="form.mode !== 'global'" :label="$t('visual.affectedLessons')"><el-select v-model="form.lessonIds" multiple filterable allow-create default-first-option style="width:100%" :placeholder="$t('visual.lessonIdsHint')"><el-option v-for="id in affectedLessonIds" :key="id" :label="id" :value="id" /></el-select></el-form-item>
          <el-alert v-if="form.mode === 'cloneForLesson'" :title="$t('visual.cloneHint')" type="info" :closable="false" show-icon />
          <p v-if="impact.activeAssignments != null" class="active-impact">{{ $t('visual.activeAssignments') }}: <b>{{ impact.activeAssignments }}</b></p>
          <el-button class="replace-button" type="danger" :loading="submitting" @click="prepareReplacement">{{ $t('visual.reviewImpact') }}</el-button></el-form>
        </el-card></el-col>
      </el-row>
      <el-card shadow="never" class="affected"><h2>{{ $t('visual.affectedLessons') }}</h2><p v-if="!affectedLessonIds.length" class="muted">{{ $t('visual.affectedLessonsCount', { count: source.usageCount || 0 }) }}</p><el-tag v-for="id in affectedLessonIds" :key="id">{{ id }}</el-tag></el-card>
      <AssetImpactDialog :visible="impactVisible" :impact="impact" :mode="form.mode" :confirming="submitting" @cancel="impactVisible=false" @confirm="executeReplacement" />
    </main>
  </div>
</template>
<script>
import Api from '@/apis/api'; import HeaderBar from '@/components/HeaderBar.vue'; import AssetImpactDialog from '@/components/lesson/AssetImpactDialog.vue';
import { buildReplacementRequest, compareAssetVersions, replacementNeedsImpact, REPLACEMENT_MODES } from '@/utils/lessonVisualLibraryState.mjs';
export default {
  name: 'LessonVisualAssetDetail', components: { HeaderBar, AssetImpactDialog },
  data: () => ({ loading: false, submitting: false, versions: [], modes: REPLACEMENT_MODES, affectedLessonIds: [], impactVisible: false, impact: {}, form: { sourceVersionId: '', targetVersionId: '', mode: 'global', lessonIds: [] } }),
  computed: {
    assetKey() { return this.$route.params.assetKey || ''; }, asset() { return this.versions[0] || {}; },
    source() { return this.versions.find((v) => v.versionId === this.form.sourceVersionId) || this.asset; }, robot() { return this.versions.find((v) => v.profile === 'espTft') || this.source; },
    comparisonRows() { const c = compareAssetVersions(this.source, this.robot); return [{ label: this.$t('visual.dimensions'), source: `${c.source.width} × ${c.source.height}`, robot: `${c.robot.width} × ${c.robot.height}` }, { label: this.$t('visual.bytes'), source: this.formatBytes(c.source.bytes), robot: this.formatBytes(c.robot.bytes) }, { label: 'SHA-256', source: c.source.shaPrefix, robot: c.robot.shaPrefix }]; },
  },
  mounted() { this.load(); },
  methods: {
    load() { this.loading = true; Api.lesson.getVisualAssetDetail(this.assetKey, ({ versions }) => { this.versions = versions.sort((a, b) => b.version - a.version); const source = this.versions[0] || {}; this.form.sourceVersionId = source.versionId || ''; this.form.targetVersionId = (this.versions.find((v) => v.versionId !== source.versionId) || source).versionId || ''; this.loading = false; }, (m) => { this.loading = false; this.$message.error(m || this.$t('visual.loadFail')); }); },
    request() { return buildReplacementRequest(this.form.sourceVersionId, this.form.targetVersionId, this.form.mode, this.form.lessonIds); },
    prepareReplacement() { let request; try { request = this.request(); } catch (e) { this.$message.warning(e.message); return; } if (!replacementNeedsImpact(request.mode)) { this.executeReplacement(); return; } this.submitting = true; const { targetVersionId, ...impactRequest } = request; Api.lesson.visualReplacementImpact(impactRequest, (impact) => { this.impact = impact; this.impactVisible = true; this.submitting = false; }, (m) => { this.submitting = false; this.$message.error(m); }); },
    executeReplacement() { let request; try { request = this.request(); } catch (e) { this.$message.warning(e.message); return; } this.submitting = true; Api.lesson.replaceVisualAsset(request, () => { this.submitting = false; this.impactVisible = false; this.$message.success(this.$t('visual.replaced')); this.load(); }, (m) => { this.submitting = false; this.$message.error(m); }); },
    versionLabel(v) { return `v${v.version} · ${v.profile} · ${v.publicationState}`; }, shaPrefix(value) { return String(value || '').slice(0, 12) || '—'; },
    formatBytes(value) { const n = Number(value || 0); return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`; },
  },
};
</script>
<style scoped>
.detail-page{min-height:100vh;background:#f5f4ef}.detail-page main{max-width:1450px;margin:auto;padding:24px 32px 50px}.title-row{display:flex;justify-content:space-between;align-items:center;margin:10px 0 20px}.title-row h1{font:38px Georgia,serif;color:#17372f;margin:3px 0}.title-row p{margin:0;color:#66756f}.eyebrow{font-size:11px!important;letter-spacing:2px;text-transform:uppercase;color:#b4652d!important}.facts{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:18px}.facts div{background:#fff;border:1px solid #e5e1d7;border-radius:12px;padding:16px}.facts span{display:block;color:#7a817e;font-size:12px;margin-bottom:7px}.facts b{font-size:17px}.mono{font-family:monospace}.el-card{border:0;border-radius:14px;margin-bottom:18px}.replace-button{width:100%;margin-top:18px}.active-impact{padding:10px;border-radius:8px;background:#fff1f0;color:#cf1322}.affected .el-tag{margin-right:8px}.muted{color:#7a817e}@media(max-width:900px){.detail-page main{padding:18px}.facts{grid-template-columns:repeat(2,1fr)}}
</style>
