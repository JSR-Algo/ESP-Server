<template>
  <section class="conversation-preview" aria-labelledby="tvideo-conversation-title">
    <div class="conversation-preview__head">
      <div>
        <span class="eyebrow">{{ $t('lesson.tvideoJourney.simulatorEyebrow') }}</span>
        <h4 id="tvideo-conversation-title">{{ step.targetWord }}</h4>
      </div>
      <el-select v-model="branch" size="small" :aria-label="$t('lesson.tvideoJourney.childInputClassLabel')">
        <el-option v-for="item in branches" :key="item" :label="branchLabel(item)" :value="item" />
      </el-select>
    </div>
    <div class="conversation-preview__flow" aria-live="polite">
      <article><small>{{ $t('lesson.tvideoJourney.inputClass') }}</small><strong>{{ result.inputClass }}</strong></article>
      <article><small>{{ $t('lesson.tvideoJourney.nextIntent') }}</small><strong>{{ result.nextIntent }}</strong></article>
      <article class="conversation-preview__wide"><small>{{ $t('lesson.tvideoJourney.question') }}</small><p>{{ result.question || '—' }}</p></article>
      <article class="conversation-preview__wide"><small>{{ $t('lesson.tvideoJourney.bridge') }}</small><p>{{ $t(result.bridge, result.bridgeParams) }}</p></article>
      <article class="conversation-preview__wide"><small>{{ $t('lesson.tvideoJourney.coaching') }}</small><p>{{ $t(result.coaching, result.coachingParams) }}</p></article>
      <article><small>{{ $t('lesson.tvideoJourney.cueEffectLabel') }}</small><strong class="mono">{{ result.cueId }} · {{ result.effect }}</strong></article>
      <article><small>{{ $t('lesson.tvideoJourney.coachingLevel') }}</small><strong>{{ result.coachingLevel || '—' }}</strong></article>
      <article><small>{{ $t('lesson.tvideoJourney.progressResult') }}</small><strong>{{ result.progressResult }}</strong></article>
    </div>
    <p class="conversation-preview__note">{{ $t('lesson.tvideoJourney.noGoogleCall') }}</p>
  </section>
</template>

<script>
import { CONVERSATION_BRANCHES, simulateConversationBranch } from './tvideo-journey';

export default {
  name: 'TVideoConversationPreview',
  props: {
    step: { type: Object, required: true },
    nextStep: { type: Object, default: null },
  },
  data: () => ({ branch: 'target', branches: CONVERSATION_BRANCHES }),
  computed: {
    result() { return simulateConversationBranch(this.branch, this.step, this.nextStep); },
  },
  methods: {
    branchLabel(value) {
      return this.$t(`lesson.tvideoJourney.branch.${value}`);
    },
  },
};
</script>

<style scoped>
.conversation-preview{background:#f7f2e8;border:1px solid #dfd3bd;border-radius:16px;padding:16px}.conversation-preview__head{align-items:center;display:flex;gap:16px;justify-content:space-between}.conversation-preview__head h4{font-size:24px;margin:3px 0;text-transform:uppercase}.conversation-preview__flow{display:grid;gap:9px;grid-template-columns:repeat(3,minmax(0,1fr));margin-top:14px}.conversation-preview__flow article{background:#fff;border-radius:11px;box-shadow:0 4px 12px rgba(73,60,40,.06);min-width:0;padding:11px}.conversation-preview__flow small{color:#756b5e;display:block;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.conversation-preview__flow strong,.conversation-preview__flow p{display:block;margin:5px 0 0;overflow-wrap:anywhere}.conversation-preview__wide{grid-column:span 3}.conversation-preview__note{color:#6c756f;font-size:12px;margin:12px 0 0}@media(max-width:680px){.conversation-preview__head{align-items:stretch;flex-direction:column}.conversation-preview__flow{grid-template-columns:1fr}.conversation-preview__wide{grid-column:auto}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style>
