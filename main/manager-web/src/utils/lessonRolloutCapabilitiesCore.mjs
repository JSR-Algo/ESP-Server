export const NO_LESSON_ROLLOUT_CAPABILITIES = Object.freeze({
  sharedVisualAuthoring: false,
  exactEspTftPreview: false,
});

export function normalizeLessonRolloutCapabilities(payload) {
  const value = payload && typeof payload === 'object' ? payload : {};
  return {
    sharedVisualAuthoring: value.sharedVisualAuthoring === true,
    exactEspTftPreview: value.exactEspTftPreview === true,
  };
}

export function loadLessonRolloutCapabilitiesWith(api, timeoutMs = 5000) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (capabilities) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      resolve(capabilities);
    };
    const failClosed = () => finish({ ...NO_LESSON_ROLLOUT_CAPABILITIES });
    const timeout = setTimeout(failClosed, Math.max(0, timeoutMs));
    try {
      api.getRolloutCapabilities(
        (payload) => finish(normalizeLessonRolloutCapabilities(payload)),
        failClosed,
      );
    } catch (error) {
      failClosed();
    }
  });
}
