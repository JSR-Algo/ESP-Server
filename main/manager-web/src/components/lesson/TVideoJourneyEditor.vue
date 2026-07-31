<template>
  <section class="journey-editor" data-testid="tvideo-journey-editor">
    <header class="journey-editor__header">
      <div>
        <span class="eyebrow">RENDERER V4 · FARM JOURNEY</span>
        <h3>{{ $t('lesson.tvideoJourney.title') }}</h3>
        <p>{{ $t('lesson.tvideoJourney.lockedPreset') }}</p>
      </div>
      <div class="journey-editor__identity">
        <el-tag type="success" size="small">{{ preset.presetId }}@{{ preset.presetVersion }} · locked</el-tag>
        <span class="mono">build {{ shortHash(preset.rendererBuildSha256) }}</span>
        <span>{{ effectSummary }}</span>
      </div>
    </header>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-alert v-if="saveMessage" :title="saveMessage" type="success" :closable="false" show-icon />

    <div class="journey-editor__tabs" role="tablist" aria-label="TVideo Journey preview modes" @keydown="onTabKeydown">
      <button v-for="(tab,index) in tabs" :id="`tvideo-tab-${tab.id}`" :key="tab.id" type="button" role="tab" :tabindex="activeTab === tab.id ? 0 : -1" :aria-selected="activeTab === tab.id ? 'true' : 'false'" :aria-controls="`tvideo-panel-${tab.id}`" @click="activeTab=tab.id" @focus="focusedTab=index">{{ tab.label }}</button>
    </div>

    <section v-show="activeTab==='sources'" id="tvideo-panel-sources" role="tabpanel" aria-labelledby="tvideo-tab-sources" class="journey-editor__panel">
      <div class="source-stage">
        <video v-if="mediaUrl(draft.assets.background.assetVersionId)" :src="mediaUrl(draft.assets.background.assetVersionId)" muted autoplay loop playsinline preload="metadata" />
        <div v-else class="source-stage__placeholder">{{ $t('lesson.tvideoJourney.selectBackground') }}</div>
        <img v-if="mediaUrl(selectedStep.teachingObject.assetVersionId)" class="source-stage__object" :src="mediaUrl(selectedStep.teachingObject.assetVersionId)" alt="" />
        <video v-if="mediaUrl(selectedRobotClip.assetVersionId)" class="source-stage__robot" :src="mediaUrl(selectedRobotClip.assetVersionId)" muted autoplay loop playsinline preload="metadata" />
        <span class="source-stage__identity source-stage__identity--background">background · object-fit cover</span>
        <span class="source-stage__identity source-stage__identity--object">teaching object · alpha PNG</span>
        <span class="source-stage__identity source-stage__identity--robot">{{ selectedRobotRole }} · WebM alpha/chroma visualization</span>
      </div>
      <div class="source-selectors">
        <div>
          <h4>{{ $t('lesson.tvideoJourney.background') }}</h4>
          <CinematicLayerPicker layer-slot="backgroundScene" :assets="backgroundAssets" :selected-version-id="draft.assets.background.assetVersionId" :disabled="disabled" @select="selectBackground" />
        </div>
        <div>
          <div class="source-selectors__line"><h4>{{ $t('lesson.tvideoJourney.teachingObject') }}</h4><el-select v-model="selectedStepIndex" size="mini" aria-label="Selected journey word"><el-option v-for="(step,index) in draft.steps" :key="step.stepKey" :label="step.targetWord" :value="index" /></el-select></div>
          <CinematicLayerPicker layer-slot="teachingObject" :assets="objectAssets" :selected-version-id="selectedStep.teachingObject.assetVersionId" :disabled="disabled" @select="selectTeachingObject" />
        </div>
        <div>
          <div class="source-selectors__line"><h4>{{ $t('lesson.tvideoJourney.robotClip') }}</h4><el-select v-model="selectedRobotRole" size="mini" aria-label="Selected robot role"><el-option v-for="role in robotRoles" :key="role" :label="role" :value="role" /></el-select></div>
          <CinematicLayerPicker layer-slot="robotOverlay" :assets="robotAssets" :selected-version-id="selectedRobotClip.assetVersionId" :disabled="disabled" @select="selectRobotClip" />
        </div>
      </div>
    </section>

    <section v-show="activeTab==='path'" id="tvideo-panel-path" role="tabpanel" aria-labelledby="tvideo-tab-path" class="journey-editor__panel">
      <div ref="pathStage" class="path-stage" :style="pathBackgroundStyle" @pointermove="movePathPoint" @pointerup="stopPathDrag" @pointercancel="stopPathDrag">
        <div class="path-stage__safe" :style="rectStyle(draft.scenePath.safeZone)"><span>safe zone</span></div>
        <svg viewBox="0 0 480 320" aria-label="Ordered journey route"><polyline :points="routePolyline" fill="none" stroke="#f08a4b" stroke-width="4" stroke-linecap="round" stroke-dasharray="8 7" /></svg>
        <button v-for="point in pathPoints" :key="point.id" type="button" class="path-stage__point" :class="`path-stage__point--${point.kind}`" :style="pointStyle(point.value)" :aria-label="point.label" @pointerdown.prevent="startPathDrag(point.id, $event)" @click="selectedPathPoint=point.id"><span>{{ point.short }}</span></button>
      </div>
      <div class="path-tools">
        <div><strong>{{ activePathPoint.label }}</strong><span>x {{ activePathPoint.value.x.toFixed(3) }} · y {{ activePathPoint.value.y.toFixed(3) }}</span><span v-if="activePathPoint.value.scale">scale {{ activePathPoint.value.scale }} (preserved)</span><span v-if="activePathPoint.value.timeMs!=null">{{ activePathPoint.value.timeMs }} ms (ordered)</span></div>
        <el-form label-position="top" size="mini">
          <div class="path-tools__grid"><el-form-item label="x"><el-input-number :value="activePathPoint.value.x" :min="0" :max="1" :step=".01" :disabled="disabled" @change="setActiveCoordinate('x',$event)" /></el-form-item><el-form-item label="y"><el-input-number :value="activePathPoint.value.y" :min="0" :max="1" :step=".01" :disabled="disabled" @change="setActiveCoordinate('y',$event)" /></el-form-item></div>
          <template v-if="activePathPoint.id==='safe-zone'"><div class="path-tools__grid"><el-form-item label="width"><el-input-number :value="draft.scenePath.safeZone.width" :min=".01" :max="1" :step=".01" :disabled="disabled" @change="setSafeSize('width',$event)" /></el-form-item><el-form-item label="height"><el-input-number :value="draft.scenePath.safeZone.height" :min=".01" :max="1" :step=".01" :disabled="disabled" @change="setSafeSize('height',$event)" /></el-form-item></div></template>
        </el-form>
        <p>{{ $t('lesson.tvideoJourney.pathHelp') }}</p>
      </div>
    </section>

    <section v-show="activeTab==='conversation'" id="tvideo-panel-conversation" role="tabpanel" aria-labelledby="tvideo-tab-conversation" class="journey-editor__panel">
      <div class="journey-editor__step-switch"><el-radio-group v-model="selectedStepIndex" size="mini"><el-radio-button v-for="(step,index) in draft.steps" :key="step.stepKey" :label="index">{{ step.targetWord }}</el-radio-button></el-radio-group></div>
      <TVideoConversationPreview :step="selectedStep" :next-step="draft.steps[selectedStepIndex+1] || null" />
    </section>

    <section v-show="activeTab==='flattened'" id="tvideo-panel-flattened" role="tabpanel" aria-labelledby="tvideo-tab-flattened" class="journey-editor__panel">
      <TVideoRobotPreview :journey="draft" :preset="preset" :media-url="mediaUrl" :selected-step-index="selectedStepIndex" />
    </section>

    <section class="journey-content">
      <div class="journey-content__head"><div><span class="eyebrow">EXACT TWO-STEP CONTENT</span><h4>{{ $t('lesson.tvideoJourney.content') }}</h4></div><el-tag size="small">maxContextTurns=2</el-tag></div>
      <el-collapse v-model="openSteps">
        <el-collapse-item v-for="(step,index) in draft.steps" :key="step.stepKey" :name="step.stepKey" :title="`${index+1}/2 · ${step.targetWord}`">
          <el-form label-position="top" size="small">
            <div class="form-grid"><el-form-item label="stepKey"><el-input v-model.trim="step.stepKey" :disabled="disabled" @input="changed" /></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.targetWord')"><el-input v-model.trim="step.targetWord" :disabled="disabled" @input="changed" /></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.expectedAnswer')"><el-input v-model="step.expectedAnswer" :disabled="disabled" @input="changed" /></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.slowModel')"><el-input v-model="step.pronunciation.slowModel" :disabled="disabled" @input="changed" /></el-form-item></div>
            <div class="form-grid form-grid--wide"><el-form-item :label="$t('lesson.tvideoJourney.meaningsVi')"><el-input :value="lines(step.vietnameseMeanings)" type="textarea" :disabled="disabled" @input="setLines(step,'vietnameseMeanings',$event)" /></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.related')"><el-input :value="lines(step.relatedConcepts)" type="textarea" :disabled="disabled" @input="setLines(step,'relatedConcepts',$event)" /></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.questionSeeds')"><el-input :value="lines(step.questionSeeds)" type="textarea" :disabled="disabled" @input="setLines(step,'questionSeeds',$event)" /></el-form-item></div>
            <div class="form-grid form-grid--wide"><el-form-item label="Intro"><el-input v-model="step.teachingCopy.intro" type="textarea" :disabled="disabled" @input="changed" /></el-form-item><el-form-item label="Explanation"><el-input v-model="step.teachingCopy.explanation" type="textarea" :disabled="disabled" @input="changed" /></el-form-item><el-form-item label="Prompt"><el-input v-model="step.teachingCopy.prompt" type="textarea" :disabled="disabled" @input="changed" /></el-form-item></div>
            <div class="form-grid"><el-form-item :label="$t('lesson.tvideoJourney.pronunciationUnits')"><el-select :value="pronunciationMode(step)" :disabled="disabled" @change="setPronunciationMode(step,$event)"><el-option label="approvedSegments" value="approvedSegments" /><el-option label="approvedPhonemes" value="approvedPhonemes" /></el-select></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.units')"><el-input :value="lines(step.pronunciation[pronunciationMode(step)])" :disabled="disabled" @input="setLines(step.pronunciation,pronunciationMode(step),$event)" /></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.l1Guidance')"><el-input :value="lines(step.pronunciation.vietnameseL1Guidance)" type="textarea" :disabled="disabled" @input="setLines(step.pronunciation,'vietnameseL1Guidance',$event)" /></el-form-item><el-form-item :label="$t('lesson.tvideoJourney.contextTurns')"><el-input :value="lines(step.contextTurns)" type="textarea" :disabled="disabled" @input="setContextTurns(step,$event)" /></el-form-item></div>
          </el-form>
        </el-collapse-item>
      </el-collapse>
    </section>

    <section class="cue-status" aria-labelledby="tvideo-cue-title">
      <div class="cue-status__head"><div><span class="eyebrow">BACKEND DERIVATIVES</span><h4 id="tvideo-cue-title">19 cues · {{ response.set && response.set.state }}</h4></div><el-tag :type="response.publishReady ? 'success' : 'warning'">publishReady={{ response.publishReady === true }}</el-tag></div>
      <div v-if="response.set && response.set.issues && response.set.issues.length" class="cue-status__issues" role="alert"><p v-for="issue in response.set.issues" :key="issue.code"><strong>{{ issue.code }}</strong> · {{ (issue.cueIds || []).join(', ') }}</p><span>{{ $t('lesson.tvideoJourney.rebuildGuidance') }}</span></div>
      <div class="cue-status__grid"><article v-for="row in orderedStatuses" :key="row.cueId"><span class="mono">{{ row.cueId }}</span><el-tag size="mini" :type="statusType(row.state)">{{ $t(`lesson.tvideoJourney.status.${safeState(row.state)}`) }}</el-tag><small v-if="row.attempt">{{ $t('lesson.tvideoJourney.attempts') }} {{ row.attempt.count }}/{{ row.attempt.max }}</small><small v-if="row.errorCode" class="cue-status__error">{{ row.errorCode }}</small></article></div>
    </section>

    <footer class="journey-editor__actions"><span v-if="dirty" role="status">{{ $t('lesson.tvideoJourney.unsaved') }}</span><el-button :disabled="saving" @click="$emit('reload')">{{ $t('lesson.tvideoJourney.reload') }}</el-button><el-button type="primary" :loading="saving" :disabled="disabled || saving || !dirty" @click="$emit('save', normalizedDraft())">{{ $t('lesson.tvideoJourney.save') }}</el-button></footer>
  </section>
</template>

<script>
import CinematicLayerPicker from './CinematicLayerPicker.vue';
import TVideoConversationPreview from './TVideoConversationPreview.vue';
import TVideoRobotPreview from './TVideoRobotPreview.vue';
import { ROBOT_CLIP_ROLES, clampPoint, clampSafeZone, normalizeScenePath, orderCueStatuses, safeCueState } from './tvideo-journey';

const clone = (value) => JSON.parse(JSON.stringify(value));
export default {
  name: 'TVideoJourneyEditor', components: { CinematicLayerPicker, TVideoConversationPreview, TVideoRobotPreview },
  props: { value: { type: Object, required: true }, preset: { type: Object, required: true }, response: { type: Object, required: true }, assets: { type: Array, default: () => [] }, saving: { type: Boolean, default: false }, disabled: { type: Boolean, default: false }, error: { type: String, default: '' }, saveMessage: { type: String, default: '' } },
  data() { return { activeTab: 'sources', focusedTab: 0, tabs: [{ id: 'sources', label: '3 Sources' }, { id: 'path', label: 'Journey Path' }, { id: 'conversation', label: 'Conversation' }, { id: 'flattened', label: 'Robot Flattened' }], draft: clone(this.value), cleanSnapshot: JSON.stringify(this.value), selectedStepIndex: 0, selectedRobotRole: 'flight', selectedPathPoint: 'flight-start', dragTarget: '', robotRoles: ROBOT_CLIP_ROLES, openSteps: ['barn'] }; },
  computed: {
    dirty() { return JSON.stringify(this.draft) !== this.cleanSnapshot; }, selectedStep() { return this.draft.steps[this.selectedStepIndex] || this.draft.steps[0]; }, selectedRobotClip() { return this.draft.assets.robotClips.find((row) => row.role === this.selectedRobotRole) || {}; },
    backgroundAssets() { return this.assets.filter((asset) => asset.mimeType === 'video/mp4' && asset.width === 480 && asset.height === 320); }, objectAssets() { return this.assets.filter((asset) => asset.mimeType === 'image/png'); }, robotAssets() { return this.assets.filter((asset) => asset.mimeType === 'video/webm'); },
    effectSummary() { const effects = this.preset.effects || {}; return `${Object.keys(effects).length} locked effects · ${this.preset.width || 480}×${this.preset.height || 320} · ${this.preset.fps || 10} FPS`; }, orderedStatuses() { return orderCueStatuses(this.response.statuses, this.draft.steps); },
    pathPoints() { const scene = this.draft.scenePath; return [{ id: 'flight-start', label: 'Flight ingress start', short: 'S', kind: 'flight', value: scene.flightIngress.start }, { id: 'flight-mid', label: 'Flight ingress midpoint', short: 'M', kind: 'flight', value: scene.flightIngress.mid }, { id: 'flight-end', label: 'Flight ingress end', short: 'E', kind: 'flight', value: scene.flightIngress.end }, { id: 'landing', label: 'Landing', short: 'L', kind: 'landing', value: scene.landing }, ...scene.walk.keyframes.map((value, index) => ({ id: `walk-${index}`, label: `Walk point ${index + 1}`, short: `${index + 1}`, kind: 'walk', value })), { id: 'teaching-anchor', label: 'Teaching anchor', short: 'T', kind: 'anchor', value: scene.teachingAnchor }, { id: 'object-anchor', label: 'Object anchor', short: 'O', kind: 'object', value: scene.objectAnchor }, { id: 'safe-zone', label: 'Safe zone origin', short: 'Z', kind: 'safe', value: scene.safeZone }]; },
    activePathPoint() { return this.pathPoints.find((point) => point.id === this.selectedPathPoint) || this.pathPoints[0]; }, routePolyline() { return this.pathPoints.filter((point) => ['flight', 'landing', 'walk'].includes(point.kind)).map((point) => `${point.value.x * 480},${point.value.y * 320}`).join(' '); },
    pathBackgroundStyle() { const url = this.mediaUrl(this.draft.assets.background.assetVersionId); return url ? { backgroundImage: `linear-gradient(rgba(20,40,34,.1),rgba(20,40,34,.1)),url(${url})` } : {}; },
  },
  watch: { value: { deep: true, handler(value) { this.draft = clone(value); this.cleanSnapshot = JSON.stringify(value); } }, dirty(value) { this.$emit('dirty-change', value); } },
  beforeDestroy() { window.removeEventListener('pointerup', this.stopPathDrag); },
  methods: {
    shortHash(value) { return value ? `${value.slice(0, 8)}…${value.slice(-6)}` : 'unavailable'; }, lines(value) { return (value || []).join('\n'); }, parsedLines(value) { return String(value || '').split(/\n+/).map((item) => item.trim()).filter(Boolean); }, changed() { this.$emit('input', clone(this.draft)); },
    setLines(target, key, value) { this.$set(target, key, this.parsedLines(value)); this.changed(); }, setContextTurns(step, value) { this.$set(step, 'contextTurns', this.parsedLines(value).slice(0, 2)); this.changed(); }, pronunciationMode(step) { return Object.prototype.hasOwnProperty.call(step.pronunciation, 'approvedPhonemes') ? 'approvedPhonemes' : 'approvedSegments'; },
    setPronunciationMode(step, mode) { const old = this.pronunciationMode(step); const values = step.pronunciation[old] || []; this.$delete(step.pronunciation, old); this.$set(step.pronunciation, mode, values); this.changed(); },
    assetDto(asset, expected) { return { assetVersionId: asset.versionId || asset.assetVersionId, sha256: asset.sha256, mediaType: expected.mediaType, width: Number(asset.width), height: Number(asset.height) }; },
    selectBackground(selection) { const asset = selection.asset || {}; this.$set(this.draft.assets, 'background', this.assetDto(asset, { mediaType: 'video/mp4' })); this.changed(); }, selectTeachingObject(selection) { const asset = selection.asset || {}; this.$set(this.selectedStep, 'teachingObject', this.assetDto(asset, { mediaType: 'image/png' })); this.changed(); }, selectRobotClip(selection) { const asset = selection.asset || {}; const index = this.draft.assets.robotClips.findIndex((row) => row.role === this.selectedRobotRole); this.$set(this.draft.assets.robotClips, index, { role: this.selectedRobotRole, assetVersionId: asset.versionId || asset.assetVersionId, sha256: asset.sha256, mediaType: 'video/webm', alpha: true }); this.changed(); },
    mediaUrl(versionId) { const asset = this.assets.find((row) => (row.versionId || row.assetVersionId) === versionId); return asset ? asset.url : ''; }, pointStyle(value) { return { left: `${value.x * 100}%`, top: `${value.y * 100}%` }; }, rectStyle(value) { return { left: `${value.x * 100}%`, top: `${value.y * 100}%`, width: `${value.width * 100}%`, height: `${value.height * 100}%` }; },
    startPathDrag(id, event) { if (this.disabled) return; this.selectedPathPoint = id; this.dragTarget = id; if (event.currentTarget.setPointerCapture) event.currentTarget.setPointerCapture(event.pointerId); window.addEventListener('pointerup', this.stopPathDrag, { once: true }); }, stopPathDrag() { this.dragTarget = ''; }, movePathPoint(event) { if (!this.dragTarget || this.disabled) return; const rect = this.$refs.pathStage.getBoundingClientRect(); this.setPathPoint(this.dragTarget, clampPoint({ x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height })); },
    setPathPoint(id, point) { const target = this.pathPoints.find((row) => row.id === id); if (!target) return; this.$set(target.value, 'x', point.x); this.$set(target.value, 'y', point.y); if (id === 'safe-zone') this.$set(this.draft.scenePath, 'safeZone', clampSafeZone(this.draft.scenePath.safeZone)); this.changed(); }, setActiveCoordinate(key, value) { this.setPathPoint(this.activePathPoint.id, { ...this.activePathPoint.value, [key]: value }); }, setSafeSize(key, value) { this.$set(this.draft.scenePath.safeZone, key, value); this.$set(this.draft.scenePath, 'safeZone', clampSafeZone(this.draft.scenePath.safeZone)); this.changed(); },
    normalizedDraft() { const dto = clone(this.draft); dto.presetId = 'tvideoJourney'; dto.presetVersion = 1; dto.boundedContext = { maxTurns: 2 }; dto.scenePath = normalizeScenePath(dto.scenePath); dto.steps = dto.steps.slice(0, 2).map((step, index) => ({ ...step, progress: { index: index + 1, count: 2 }, contextTurns: (step.contextTurns || []).slice(0, 2) })); return dto; }, safeState: safeCueState, statusType(state) { return ({ ready: 'success', failed: 'danger', stale: 'info', retryable: 'warning', processing: '', queued: '', 'not-requested': 'info' })[safeCueState(state)] || 'info'; },
    onTabKeydown(event) { const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End']; if (!keys.includes(event.key)) return; event.preventDefault(); if (event.key === 'Home') this.focusedTab = 0; else if (event.key === 'End') this.focusedTab = this.tabs.length - 1; else this.focusedTab = (this.focusedTab + (event.key === 'ArrowRight' ? 1 : -1) + this.tabs.length) % this.tabs.length; this.activeTab = this.tabs[this.focusedTab].id; this.$nextTick(() => document.getElementById(`tvideo-tab-${this.activeTab}`).focus()); },
  },
};
</script>

<style scoped>
.journey-editor{background:#fff;border:1px solid #dfd5c3;border-radius:18px;box-shadow:0 15px 35px rgba(68,54,35,.08);margin-bottom:18px;max-width:100%;min-width:0;overflow:hidden}.journey-editor__header{align-items:flex-start;background:linear-gradient(120deg,#f7efd9,#edf5ed 72%);display:flex;gap:20px;justify-content:space-between;padding:20px}.journey-editor__header h3{font-size:25px;margin:4px 0}.journey-editor__header p{color:#65736d;margin:0}.journey-editor__identity{align-items:flex-end;display:flex;flex-direction:column;font-size:11px;gap:7px}.journey-editor__tabs{background:#253f37;display:flex;gap:4px;overflow-x:auto;padding:7px}.journey-editor__tabs button{background:transparent;border:0;border-radius:9px;color:#dce7e2;cursor:pointer;font-weight:700;min-width:max-content;padding:10px 16px}.journey-editor__tabs button[aria-selected=true]{background:#f0b85a;color:#253f37}.journey-editor__tabs button:focus-visible{outline:3px solid #fff;outline-offset:-3px}.journey-editor__panel{padding:18px}.source-stage,.path-stage{aspect-ratio:3/2;background:#9fcfc2 center/cover;border-radius:14px;max-width:480px;overflow:hidden;position:relative;width:100%}.source-stage{margin:0 auto}.source-stage>video:first-child{height:100%;object-fit:cover;width:100%}.source-stage__object{bottom:18%;height:24%;left:18%;object-fit:contain;position:absolute;width:24%}.source-stage__robot{bottom:8%;height:54%;object-fit:contain;position:absolute;right:28%;width:28%}.source-stage__placeholder{align-items:center;color:#31524a;display:flex;height:100%;justify-content:center}.source-stage__identity{background:rgba(27,48,41,.78);border-radius:5px;color:#fff;font-size:9px;padding:4px 6px;position:absolute}.source-stage__identity--background{left:8px;top:8px}.source-stage__identity--object{bottom:8px;left:8px}.source-stage__identity--robot{bottom:8px;right:8px}.source-selectors{display:grid;gap:16px;grid-template-columns:repeat(3,minmax(0,1fr));margin-top:18px}.source-selectors h4{margin:0}.source-selectors__line{align-items:center;display:flex;gap:8px;justify-content:space-between}.path-stage{touch-action:none}.path-stage svg{height:100%;inset:0;pointer-events:none;position:absolute;width:100%}.path-stage__safe{background:rgba(75,177,146,.16);border:2px dashed #2c8b72;pointer-events:none;position:absolute}.path-stage__safe span{background:#2c8b72;color:#fff;font-size:9px;padding:2px 5px}.path-stage__point{align-items:center;background:#fff;border:3px solid #f08a4b;border-radius:50%;cursor:grab;display:flex;height:28px;justify-content:center;position:absolute;touch-action:none;transform:translate(-50%,-50%);width:28px;z-index:2}.path-stage__point--anchor{border-color:#2c8b72}.path-stage__point--object{border-color:#d95f43}.path-stage__point--safe{border-color:#3157a4}.path-tools{display:grid;gap:14px;grid-template-columns:1fr 2fr;margin-top:14px}.path-tools>div:first-child{display:flex;flex-direction:column;gap:5px}.path-tools__grid{display:grid;gap:10px;grid-template-columns:1fr 1fr}.path-tools p{color:#68746e;font-size:12px;grid-column:span 2;margin:0}.journey-editor__step-switch{margin-bottom:10px}.journey-content,.cue-status{border-top:1px solid #e8e0d2;padding:18px}.journey-content__head,.cue-status__head{align-items:center;display:flex;justify-content:space-between}.journey-content__head h4,.cue-status__head h4{margin:4px 0 12px}.form-grid{display:grid;gap:12px;grid-template-columns:repeat(4,minmax(0,1fr))}.form-grid--wide{grid-template-columns:repeat(3,minmax(0,1fr))}.cue-status__issues{background:#fff5df;border-radius:10px;color:#754f1f;margin-bottom:12px;padding:10px}.cue-status__issues p{margin:2px 0}.cue-status__grid{display:grid;gap:7px;grid-template-columns:repeat(3,minmax(0,1fr))}.cue-status__grid article{align-items:center;background:#f6f7f4;border-radius:8px;display:grid;gap:4px;grid-template-columns:1fr auto;min-width:0;padding:8px}.cue-status__grid .mono{font-size:10px;overflow:hidden;text-overflow:ellipsis}.cue-status__grid small{grid-column:1/-1}.cue-status__error{color:#aa3f32}.journey-editor__actions{align-items:center;background:#f7f4ed;display:flex;gap:9px;justify-content:flex-end;padding:14px 18px}.journey-editor__actions span{color:#a45d20;margin-right:auto}@media(max-width:900px){.source-selectors,.cue-status__grid{grid-template-columns:1fr}.form-grid,.form-grid--wide{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:620px){.journey-editor__header{flex-direction:column}.journey-editor__identity{align-items:flex-start}.journey-editor__panel{padding:12px}.path-tools,.form-grid,.form-grid--wide{grid-template-columns:1fr}.path-tools p{grid-column:auto}.journey-editor__actions{flex-wrap:wrap}.journey-editor__actions span{flex-basis:100%}}@media(prefers-reduced-motion:reduce){.journey-editor *{scroll-behavior:auto!important;transition:none!important}}
@media (prefers-reduced-motion: reduce){.journey-editor *{animation:none!important}}
</style>
