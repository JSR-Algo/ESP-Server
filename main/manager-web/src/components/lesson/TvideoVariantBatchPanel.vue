<template>
  <section class="variant-batch">
    <div class="variant-batch__heading">
      <div><span>VARIANT SET</span><strong>Batch lesson generation</strong></div>
      <el-button size="small" icon="el-icon-plus" @click="addVariant">Add variant</el-button>
    </div>

    <div v-for="(variant, index) in variants" :key="variant.rowId" class="variant-row">
      <div class="variant-row__title">
        <strong>Variant {{ index + 1 }}</strong>
        <el-button v-if="variants.length > 1" type="text" class="danger" @click="removeVariant(index)">Remove</el-button>
      </div>
      <div class="variant-grid">
        <el-input v-model="variant.lessonKey" placeholder="Lesson key" />
        <el-input v-model="variant.title" placeholder="Lesson title" />
        <el-input v-model="variant.vocabularySetId" placeholder="vocabularySetId" />
        <el-input v-model="variant.wordsText" placeholder="Words, comma or line separated" />
        <el-select v-model="variant.backgroundVersionId" placeholder="backgroundVersionId" @change="syncLayout(variant)">
          <el-option v-for="background in backgrounds" :key="background.assetKey" :label="background.name || background.assetKey" :value="background.assetKey" />
        </el-select>
        <el-select v-model="variant.layoutPreset" placeholder="layoutPreset">
          <el-option v-for="preset in layoutsFor(variant)" :key="preset" :label="preset" :value="preset" />
        </el-select>
        <el-select v-model="variant.duplicateReason" clearable placeholder="Duplicate vocabulary reason (optional)">
          <el-option label="Recall" value="recall" />
          <el-option label="Spiral review" value="spiralReview" />
          <el-option label="Assessment" value="assessment" />
        </el-select>
      </div>
      <p v-if="variant.backgroundVersionId && !layoutsFor(variant).length" class="blocking">The selected background has no compatible named layout preset.</p>
    </div>

    <div class="variant-actions">
      <el-button type="primary" :loading="generating" @click="generate">Generate variants</el-button>
      <el-button :disabled="!createdLessonIds.length" :loading="checking" @click="$emit('readiness', createdLessonIds)">Run batch readiness</el-button>
    </div>

    <div v-if="generationResult" class="generation-result">
      <strong>{{ createdLessonIds.length }} drafts created</strong>
      <span v-if="failedVariants.length">{{ failedVariants.length }} failed without rolling back successful drafts.</span>
    </div>
    <div v-if="readiness" class="ready-subset">
      <div><span>Ready subset</span><strong>{{ readiness.readyCount }} / {{ readiness.lessons.length }}</strong></div>
      <code>{{ readiness.readyLessonIds.join(', ') || 'No lessons ready' }}</code>
      <div v-for="entry in readiness.lessons" :key="entry.lessonId" class="readiness-row">
        <el-tag :type="entry.ready ? 'success' : 'danger'" size="mini">{{ entry.ready ? 'READY' : 'BLOCKED' }}</el-tag>
        <span>{{ entry.lessonId }}</span>
        <small>{{ issueLabels(entry.issues) }}</small>
        <dl class="readiness-metrics">
          <div><dt>Vocabulary</dt><dd>{{ vocabularySummary(entry) }}</dd></div>
          <div><dt>Background</dt><dd>{{ entry.backgroundUsage || entry.background_usage || '—' }}</dd></div>
          <div><dt>Pack bytes</dt><dd>{{ formatBytes(metric(entry, 'packBytes')) }}</dd></div>
          <div><dt>Peak PSRAM</dt><dd>{{ formatBytes(metric(entry, 'estimatedVisualPeakBytes')) }}</dd></div>
          <div><dt>Offline</dt><dd>{{ yesNo(metric(entry, 'offlineReady')) }}</dd></div>
          <div><dt>Terminates</dt><dd>{{ yesNo(metric(entry, 'allPathsTerminate')) }}</dd></div>
        </dl>
      </div>
    </div>
  </section>
</template>

<script>
import { buildVariantGenerationRequest, compatibleLayouts } from './tvideo-template-logic';

let nextRowId = 1;

export default {
  name: 'TvideoVariantBatchPanel',
  props: {
    backgrounds: { type: Array, default: () => [] },
    templateAuthoring: { type: Object, default: null },
    generationResult: { type: Object, default: null },
    readiness: { type: Object, default: null },
    generating: Boolean,
    checking: Boolean,
  },
  data() { return { variants: [this.blankVariant()] }; },
  computed: {
    createdLessonIds() {
      const rows = this.generationResult && Array.isArray(this.generationResult.created) ? this.generationResult.created : [];
      return rows.map((row) => row.id || row.lessonId || row.lesson_id).filter(Boolean);
    },
    failedVariants() {
      return this.generationResult && Array.isArray(this.generationResult.failed) ? this.generationResult.failed : [];
    },
  },
  methods: {
    blankVariant() {
      const base = this.templateAuthoring || {};
      return {
        rowId: nextRowId++, lessonKey: '', title: '', vocabularySetId: '', wordsText: '',
        backgroundVersionId: base.backgroundVersionId || '', layoutPreset: base.layoutPreset || '', duplicateReason: '',
      };
    },
    addVariant() { this.variants.push(this.blankVariant()); },
    removeVariant(index) { this.variants.splice(index, 1); },
    backgroundFor(variant) { return this.backgrounds.find((background) => background.assetKey === variant.backgroundVersionId); },
    layoutsFor(variant) {
      const background = this.backgroundFor(variant);
      return compatibleLayouts(background && (background.compatibility || background));
    },
    syncLayout(variant) {
      const layouts = this.layoutsFor(variant);
      if (!layouts.includes(variant.layoutPreset)) variant.layoutPreset = layouts[0] || '';
    },
    generate() {
      try {
        const base = this.templateAuthoring || {};
        const variants = this.variants.map((variant) => ({
          ...base,
          ...variant,
          words: variant.wordsText,
          backgroundAssetVersionId: (this.backgroundFor(variant) || {}).assetVersionId,
        }));
        this.$emit('generate', buildVariantGenerationRequest(variants));
      } catch (error) {
        this.$message.error(error.message);
      }
    },
    issueLabels(issues) {
      return (issues || []).map((issue) => (typeof issue === 'string' ? issue : issue.code || issue.message)).filter(Boolean).join(', ');
    },
    metric(entry, key) {
      const profile = entry && entry.budgets && (entry.budgets.espTft || entry.budgets.esptft);
      return profile && profile.metrics ? profile.metrics[key] : undefined;
    },
    formatBytes(value) {
      return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '—';
    },
    yesNo(value) {
      if (value === true) return 'Yes';
      if (value === false) return 'No';
      return '—';
    },
    vocabularySummary(entry) {
      const vocabulary = entry.vocabulary || entry.vocabularySummary || {};
      const unique = Array.isArray(vocabulary.unique) ? vocabulary.unique.length : Number(vocabulary.uniqueCount || 0);
      const repeated = Array.isArray(vocabulary.repeated) ? vocabulary.repeated.length : Number(vocabulary.repeatedCount || 0);
      return `${unique} unique · ${repeated} repeated`;
    },
  },
};
</script>

<style scoped>
.variant-batch{background:#f7f1e5;border:1px solid #d8c9ac;border-radius:18px;margin:16px 0;padding:16px}.variant-batch__heading,.variant-row__title,.variant-actions,.generation-result{align-items:center;display:flex;justify-content:space-between}.variant-batch__heading div{display:grid;gap:3px}.variant-batch__heading span,.ready-subset span{color:#826e45;font-size:10px;font-weight:800;letter-spacing:.12em}.variant-batch__heading strong{color:#17312d;font-family:Georgia,serif;font-size:20px}.variant-row{background:rgba(255,255,255,.72);border-radius:12px;margin-top:12px;padding:12px}.variant-grid{display:grid;gap:10px;grid-template-columns:repeat(2,minmax(0,1fr));margin-top:10px}.variant-actions{justify-content:flex-start;margin-top:14px}.danger,.blocking{color:#b53d27}.generation-result{background:#fff;border-radius:10px;margin-top:12px;padding:10px}.ready-subset{background:#17312d;border-radius:12px;color:#fff8df;display:grid;gap:8px;margin-top:12px;padding:12px}.ready-subset>div:first-child{display:flex;justify-content:space-between}.ready-subset code{overflow-wrap:anywhere}.readiness-row{align-items:center;border-top:1px solid rgba(255,255,255,.12);display:grid;gap:8px;grid-template-columns:auto minmax(100px,1fr) 2fr;padding-top:9px}.readiness-row small{color:#c8d6d1}.readiness-metrics{display:grid;gap:6px;grid-column:1/-1;grid-template-columns:repeat(3,minmax(0,1fr));margin:2px 0 0}.readiness-metrics div{background:rgba(255,255,255,.07);border-radius:8px;padding:7px}.readiness-metrics dt{color:#9fb5ad;font-size:9px;text-transform:uppercase}.readiness-metrics dd{font-size:12px;margin:3px 0 0}@media(max-width:800px){.variant-grid{grid-template-columns:1fr}.readiness-row{grid-template-columns:auto 1fr}.readiness-row small{grid-column:1/-1}.readiness-metrics{grid-template-columns:1fr 1fr}}
</style>
