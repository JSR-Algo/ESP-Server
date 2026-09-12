<template>
  <section class="course-visuals" data-testid="course-mode-visual-selection" :aria-busy="loading || saving">
    <h3>Lesson images and character clips</h3>
    <p>Static JPEG background, PNG teaching objects and silent MJPEG character video. Published assets are selectable; device download and playback readiness are checked separately.</p>
    <p v-if="disabled">Save or undo activity edits before changing visual bindings.</p>
    <p v-if="!isDraft">Published lesson bindings are read-only. Create a new draft to edit.</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <p v-if="catalogLoading" role="status">Loading asset library...</p>
    <el-alert v-if="catalogError" :title="catalogError" type="error" :closable="false" />
    <el-button size="small" :disabled="catalogLoading || saving" @click="$emit('reload-assets')">Refresh asset library</el-button>
    <p v-if="loading" role="status">Reading saved visual versions...</p>
    <template v-if="snapshot">
      <div class="course-visuals__grid">
        <div v-for="row in selectors" :key="row.key" :data-testid="`course-visual-${row.key}`">
          <label :for="`visual-${row.key}`">{{ row.label }}</label>
          <select :id="`visual-${row.key}`" :value="selected(row)" :disabled="disabled || !isDraft || loading || saving" @change="choose(row, $event.target.value)">
            <option value="" :selected="!selected(row)">{{ row.optional ? 'No object (activity fallback)' : 'Select a version' }}</option>
            <option v-if="selected(row) && !options(row).some(a => a.versionId === selected(row))" :value="selected(row)" selected disabled>Saved/selected version unavailable or incompatible: {{ selected(row) }}</option>
            <option v-for="asset in options(row)" :key="asset.versionId" :value="asset.versionId" :selected="asset.versionId === selected(row)">{{ label(asset.versionId) }}</option>
          </select>
          <small>Selected version: {{ selected(row) || 'none' }}</small>
          <details v-if="rejections(row).length"><summary>Unavailable choices and compatibility</summary><ul><li v-for="item in rejections(row)" :key="item.id">{{ item.label }}: {{ item.reason }}</li></ul></details>
        </div>
      </div>
      <p>Activity background/object keys determine the effective image on each activity. This selector replaces versions of those saved keys. Activity key changes are validated separately; unresolved new keys are rejected while existing bindings are preserved.</p>
      <div class="course-visuals__actions">
        <el-button type="primary" :disabled="disabled || !isDraft || !dirty || loading || conflict" :loading="saving" @click="save">Save visual bindings</el-button>
        <el-button :disabled="saving || loading" @click="load(dirty || conflict)">Read saved bindings</el-button>
        <el-button v-if="dirty && !conflict" :disabled="saving || loading" @click="discard">Undo visual edits</el-button>
      </div>
      <p v-if="savedMessage" role="status">{{ savedMessage }}</p>
      <div v-if="comparison" class="course-visuals__comparison" data-testid="visual-comparison">
        <h4>Saved bindings to compare</h4><pre>{{ comparison.refs }}</pre>
        <el-button :disabled="loading || saving" @click="keepDraft">Keep my selections with this saved version</el-button>
        <el-button :disabled="loading || saving" @click="useSaved">Use saved bindings</el-button>
      </div>
      <details open><summary>Effective persisted sources by activity</summary>
        <table><thead><tr><th>Activity / step</th><th>Layer or phase</th><th>Saved asset and immutable version</th></tr></thead>
          <tbody><tr v-for="ref in snapshot.refs" :key="ref.stepKey + ':' + ref.slot"><td>{{ ref.stepKey }}</td><td>{{ slotLabel(ref.slot) }}</td><td>{{ label(ref.assetVersionId) }}<small>{{ ref.assetVersionId }}</small></td></tr></tbody>
        </table>
        <p v-if="!snapshot.refs.length">No persisted visual bindings. Select all required clips and images, then save.</p>
      </details>
    </template>
    <el-button v-else :disabled="loading" @click="load()">Retry loading bindings</el-button>
  </section>
</template>

<script>
import Api from '@/apis/api';
import { COURSE_MODE_PHASES, courseModeVisualSelection, buildCourseModeVisualRequest, courseModeAssetRejection } from './lesson-visual-selection';

export default {
  name: 'CourseModeVisualSelection',
  props: {
    lessonId: { type: String, required: true }, isDraft: { type: Boolean, default: false },
    disabled: { type: Boolean, default: false }, assets: { type: Array, default: () => [] },
    contract: { type: Object, required: true }, visualChecksum: { type: String, default: '' },
    catalogLoading: { type: Boolean, default: false }, catalogError: { type: String, default: '' },
  },
  data: () => ({ snapshot: null, draft: null, comparison: null, loading: false, saving: false,
    conflict: false, error: '', savedMessage: '', requestId: 0, destroyed: false, saveReceipt: null }),
  computed: {
    dirty() { return Boolean(this.snapshot && this.draft && JSON.stringify(this.draft) !== JSON.stringify(courseModeVisualSelection(this.snapshot))); },
    selectors() {
      return [ {key:'background',slot:'backgroundScene',field:'backgroundAssetVersionId',label:'Background image (JPEG)'},
        {key:'object',slot:'teachingObject',field:'objectAssetVersionId',label:'Teaching object (PNG)',optional:!(this.contract.activities || []).some(a=>a.visual && a.visual.objectAssetKey)},
        ...COURSE_MODE_PHASES.map(phase=>({key:phase.id,phase:phase.id,slot:'robotOverlay',label:phase.label})) ];
    },
  },
  watch: {
    dirty(value) { this.$emit('dirty', value); },
    saving(value) { this.$emit('saving', value); },
    visualChecksum(value, old) { if (value !== old && !this.saving) { if (this.dirty) { this.conflict = true; this.error = 'The saved lesson changed. Read and compare bindings; your selections are retained.'; } else this.load(); } },
  },
  mounted() { this.load(); },
  beforeDestroy() { this.destroyed = true; this.requestId += 1; },
  methods: {
    selected(row) { return this.draft ? row.phase ? this.draft.robotAssetVersionIds[row.phase] : this.draft[row.field] : ''; },
    label(id) { const asset = this.assets.find(a=>a.versionId === id); return asset ? `${asset.assetKey} / v${asset.version} / ${asset.publicationState}` : `Unavailable version ${id}`; },
    slotLabel(slot) { const phase = COURSE_MODE_PHASES.find(p=>slot === `robotOverlay.${p.id}`); return phase ? phase.label : slot === 'backgroundScene' ? 'Background image' : slot === 'teachingObject' ? 'Teaching object' : slot; },
    rejection(asset, row) {
      const reason = courseModeAssetRejection(asset,row.slot,row.phase);
      if (reason || row.phase) return reason;
      const field = row.slot === 'backgroundScene' ? 'backgroundAssetKey' : 'objectAssetKey';
      const keys = (this.contract.activities || []).map(a=>a.visual && a.visual[field]).filter(Boolean);
      return keys.includes(asset.assetKey) ? '' : 'This image key is not selected by the saved activities.';
    },
    options(row) { return this.assets.filter(a=>!this.rejection(a,row)); },
    rejections(row) { const category = row.slot === 'backgroundScene' ? 'scene' : row.slot === 'teachingObject' ? 'teachingObject' : 'robotPose'; return this.assets.filter(a=>a.category === category).map(a=>({id:a.versionId,label:`${a.assetKey} v${a.version}`,reason:this.rejection(a,row)})).filter(a=>a.reason); },
    choose(row, id) {
      if (this.disabled || !this.isDraft || this.loading || this.saving || !this.draft) return;
      if (id && !this.options(row).some(a=>a.versionId === id)) return;
      if (row.phase) this.draft.robotAssetVersionIds[row.phase] = id;
      else this.draft[row.field] = id;
      this.savedMessage = '';
    },
    adopt(snapshot, keep = false) {
      const selection = courseModeVisualSelection(snapshot);
      this.snapshot = JSON.parse(JSON.stringify(snapshot));
      if (!keep) this.draft = selection;
      this.comparison = null; this.conflict = false; this.error = '';
    },
    load(compare = false, afterSave = false) {
      if (this.destroyed || this.loading || (this.saving && !afterSave)) return;
      const lessonId = this.lessonId, requestId = ++this.requestId;
      this.loading = true; this.error = '';
      const current = () => !this.destroyed && requestId === this.requestId && lessonId === this.lessonId;
      Api.lesson.getLessonVisuals(lessonId, response => {
        if (!current()) return;
        this.loading = false;
        try {
          if (!response || response.lessonId !== lessonId) throw new Error('Wrong lesson visual response.');
          courseModeVisualSelection(response);
          if (afterSave && (!this.saveReceipt || response.checksum !== this.saveReceipt.contractChecksum
            || response.visualChecksum !== this.saveReceipt.visualChecksum)) {
            this.comparison = JSON.parse(JSON.stringify(response));
            this.conflict = true;
            throw new Error('Saved bindings changed during readback. Compare the saved version; your selections are retained.');
          }
          if (compare) this.comparison = JSON.parse(JSON.stringify(response));
          else this.adopt(response);
          if (afterSave) { this.savedMessage = 'Visual versions saved and read back. Device readiness is checked separately.'; this.$emit('saved', response); }
        } catch (error) { this.error = error.message; this.conflict = true; }
        this.saving = false;
      }, (message) => {
        if (!current()) return;
        this.loading = false; this.saving = false;
        this.error = `${message || 'Read failed.'} Your selections are retained. Retry reading the saved bindings.`;
        if (afterSave) this.conflict = true;
      });
    },
    discard() { if (this.saving || this.loading) return; this.draft = courseModeVisualSelection(this.snapshot); this.error = ''; },
    keepDraft() { if (this.comparison && !this.loading && !this.saving) this.adopt(this.comparison,true); },
    useSaved() { if (this.comparison && !this.loading && !this.saving) this.adopt(this.comparison); },
    save() {
      if (this.disabled || !this.isDraft || !this.dirty || this.saving || this.loading || this.conflict) return;
      let request;
      try { request = buildCourseModeVisualRequest(this.snapshot,this.draft); }
      catch(error) { this.error = error.message; return; }
      const lessonId = this.lessonId, requestId = ++this.requestId;
      const current = () => !this.destroyed && requestId === this.requestId && lessonId === this.lessonId;
      this.saving = true; this.error = ''; this.savedMessage = '';
      Api.lesson.applyLessonVisuals(lessonId,request,response=>{
        if (!current()) return;
        // PUT checksum is the manifest identity, not the learning token. Re-read one coherent snapshot.
        if (!response || response.lessonId !== lessonId || !/^[a-f0-9]{64}$/.test(response.contractChecksum)
          || !/^[a-f0-9]{64}$/.test(response.visualChecksum)) {
          this.saving = false; this.conflict = true; this.error = 'Incomplete save response. Read and compare saved bindings; your selections are retained.'; return;
        }
        this.saveReceipt = { contractChecksum: response.contractChecksum, visualChecksum: response.visualChecksum };
        this.load(false,true);
      },(message,error)=>{
        if (!current()) return;
        this.saving = false;
        const status = error && error.status;
        this.conflict = !status || status === 409 || status >= 500;
        this.error = `${message || 'Visual save failed.'} Your selections are retained. ${status === 401 ? 'Sign in again.' : status === 403 ? 'Contact the lesson owner for access.' : 'Read and compare saved bindings before retrying.'}`;
      });
    },
  },
};
</script>

<style scoped>
.course-visuals { background:#f7f8f3; border:1px solid #d9e2d9; border-radius:12px; padding:18px; margin-bottom:18px; min-width:0; }
.course-visuals__grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; }
label,small { display:block; } label { font-weight:600; margin-bottom:6px; } small { color:#5b6d62; overflow-wrap:anywhere; margin-top:6px; }
select { width:100%; padding:10px; min-width:0; } details { margin-top:12px; } li,pre,td { overflow-wrap:anywhere; white-space:pre-wrap; }
.course-visuals__actions { display:flex; gap:8px; flex-wrap:wrap; margin:16px 0; }
table { table-layout:fixed; width:100%; border-collapse:collapse; } th,td { text-align:left; padding:8px; border-bottom:1px solid #d9e2d9; } th:first-child { width:22%; } th:nth-child(2) { width:18%; }
@media(max-width:800px) { .course-visuals__grid { grid-template-columns:1fr; } .course-visuals { padding:12px; } }
</style>
