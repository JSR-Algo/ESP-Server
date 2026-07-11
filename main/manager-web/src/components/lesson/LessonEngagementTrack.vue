<template>
  <section class="engagement">
    <div class="engagement__header"><strong>Engagement rhythm</strong><span>{{ totalSeconds }} sec authored</span></div>
    <div class="engagement__rail">
      <button v-for="(item, index) in track" :key="item.stepKey" type="button" :class="['beat', 'beat--' + item.kind]" @click="$emit('select', index)">
        <span class="beat__icon">{{ icons[item.kind] }}</span><strong>{{ item.kind }}</strong><small>{{ item.durationSec }}s<span v-if="item.hasMotion"> · motion</span></small>
      </button>
    </div>
  </section>
</template>
<script>
import { buildEngagementTrack } from './lesson-builder-logic';
export default {
  name: 'LessonEngagementTrack',
  props: { steps: { type: Array, default: () => [] } },
  data: () => ({ icons: { passive: '◉', voice: '●', motion: '↗', minigame: '★', recall: '↺', ending: '✓' } }),
  computed: {
    track() { return buildEngagementTrack(this.steps); },
    totalSeconds() { return this.track.reduce((sum, item) => sum + item.durationSec, 0); },
  },
};
</script>
<style scoped>
.engagement { background: #fff; border-radius: 18px; box-shadow: 0 8px 28px rgba(23,49,45,.08); padding: 16px; }
.engagement__header { align-items:center; display:flex; justify-content:space-between; margin-bottom:12px; }
.engagement__header span { color:#78827f; font-size:12px; }
.engagement__rail { display:flex; gap:8px; overflow-x:auto; padding-bottom:4px; }
.beat { border:0; border-radius:14px; color:#17312d; cursor:pointer; display:grid; flex:1 0 105px; gap:3px; min-height:76px; padding:9px; text-align:left; }
.beat--passive { background:#e9efec; }.beat--voice { background:#bfe6dc; }.beat--motion { background:#a9d7ee; }.beat--minigame { background:#ffdb74; }.beat--recall { background:#ffc2a8; }.beat--ending { background:#9dd8a5; }
.beat__icon { font-size:17px; }.beat small { opacity:.7; }
</style>
