<template>
  <aside class="step-nav" aria-label="Lesson steps">
    <div class="step-nav__heading">Lesson steps</div>
    <button
      v-for="(step, index) in steps"
      :key="step.stepKey || index"
      type="button"
      class="step-nav__item"
      :class="{ active: index === value }"
      @click="$emit('input', index)"
    >
      <span class="step-nav__number">{{ index + 1 }}</span>
      <span class="step-nav__label"><strong>{{ step.stepType }}</strong><small>{{ step.prompt || step.subject }}</small></span>
    </button>
    <button v-if="editable" type="button" class="step-nav__add" @click="$emit('add')">+ Add step</button>
  </aside>
</template>

<script>
export default {
  name: 'LessonStepNavigator',
  props: {
    steps: { type: Array, default: () => [] },
    value: { type: Number, default: 0 },
    editable: Boolean,
  },
};
</script>

<style scoped>
.step-nav { background: #16332f; border-radius: 18px; color: #f8f1da; max-width: 100%; min-width: 0; padding: 14px; }
.step-nav__heading { color: #ffd36a; font-size: 12px; font-weight: 800; letter-spacing: .12em; margin: 4px 6px 12px; text-transform: uppercase; }
.step-nav__item, .step-nav__add { border: 0; border-radius: 12px; cursor: pointer; display: flex; min-width: 0; text-align: left; width: 100%; }
.step-nav__item { align-items: center; background: transparent; color: inherit; gap: 10px; margin-bottom: 6px; padding: 9px; scroll-snap-align: start; }
.step-nav__item.active { background: #f7c956; color: #15312d; }
.step-nav__number { align-items: center; background: rgba(255,255,255,.14); border-radius: 50%; display: flex; flex: 0 0 28px; height: 28px; justify-content: center; }
.step-nav__label { min-width: 0; }
.step-nav__label strong { display: block; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-nav__item small { display: block; margin-top: 3px; max-width: 150px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-nav__add { background: #fff8df; color: #16332f; font-weight: 700; justify-content: center; margin-top: 12px; padding: 10px; }
@media (max-width: 760px) {
  .step-nav { display: flex; gap: 8px; overflow-x: auto; overscroll-behavior-x: contain; scroll-snap-type: x proximity; }
  .step-nav__heading { display: none; }
  .step-nav__item { flex: 0 0 min(190px, 75vw); margin-bottom: 0; width: auto; }
  .step-nav__add { flex: 0 0 auto; margin-top: 0; white-space: nowrap; width: auto; }
}
</style>
