export const NO_LESSON_ROLLOUT_CAPABILITIES = Object.freeze({
  sharedVisualAuthoring: false,
  exactEspTftPreview: false,
});

let authSessionGeneration = 0;

export function advanceLessonRolloutSessionGeneration() {
  authSessionGeneration += 1;
}

export function getLessonRolloutSessionGeneration() {
  return authSessionGeneration;
}

export function normalizeLessonRolloutCapabilities(payload) {
  const value = payload && typeof payload === 'object' ? payload : {};
  return {
    sharedVisualAuthoring: value.sharedVisualAuthoring === true,
    exactEspTftPreview: value.exactEspTftPreview === true,
  };
}

export function isLessonCapabilityNavigationCurrent(expectedSessionKey, currentSessionKey, isAuthorized) {
  return Boolean(expectedSessionKey && expectedSessionKey === currentSessionKey && isAuthorized);
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

export function createLessonRolloutCapabilitiesLoader({ getSessionKey, load }) {
  let generation = 0;
  let cached = null;
  let inFlight = null;

  const disabled = () => ({ ...NO_LESSON_ROLLOUT_CAPABILITIES });
  const currentSessionKey = () => String(getSessionKey() || '');

  return {
    load() {
      const sessionKey = currentSessionKey();
      if (!sessionKey) return Promise.resolve(disabled());
      if (cached && cached.sessionKey === sessionKey) return Promise.resolve({ ...cached.value });
      if (inFlight && inFlight.sessionKey === sessionKey && inFlight.generation === generation) {
        return inFlight.promise;
      }

      const requestGeneration = generation;
      const request = Promise.resolve()
        .then(() => load())
        .then((payload) => {
          if (requestGeneration !== generation || sessionKey !== currentSessionKey()) return disabled();
          const value = normalizeLessonRolloutCapabilities(payload);
          cached = { sessionKey, value };
          return { ...value };
        })
        .catch(() => {
          if (requestGeneration === generation && sessionKey === currentSessionKey()) cached = null;
          return disabled();
        })
        .finally(() => {
          if (inFlight && inFlight.promise === request) inFlight = null;
        });
      inFlight = { sessionKey, generation: requestGeneration, promise: request };
      return request;
    },
    peek() {
      const sessionKey = currentSessionKey();
      return cached && cached.sessionKey === sessionKey ? { ...cached.value } : disabled();
    },
    reset() {
      generation += 1;
      cached = null;
      inFlight = null;
    },
  };
}
