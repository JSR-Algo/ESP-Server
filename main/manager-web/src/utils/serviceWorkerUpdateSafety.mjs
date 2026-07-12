const dirtyAuthoringHandles = new Set();
const cleanCallbacks = new Set();
const scheduledWorkers = new WeakSet();

function flushCleanCallbacks() {
  if (dirtyAuthoringHandles.size > 0) return;
  const callbacks = [...cleanCallbacks];
  cleanCallbacks.clear();
  callbacks.forEach((callback) => callback());
}

export function createAuthoringDirtyHandle() {
  const token = {};
  let released = false;

  return {
    setDirty(dirty) {
      if (released) return;
      if (dirty) {
        dirtyAuthoringHandles.add(token);
        return;
      }
      dirtyAuthoringHandles.delete(token);
      flushCleanCallbacks();
    },
    release() {
      if (released) return;
      released = true;
      dirtyAuthoringHandles.delete(token);
      flushCleanCallbacks();
    },
  };
}

export function scheduleAuthoringSafeCallback(callback) {
  if (typeof callback !== 'function') return;
  if (dirtyAuthoringHandles.size === 0) {
    callback();
    return;
  }
  cleanCallbacks.add(callback);
}

export function scheduleServiceWorkerActivation(worker) {
  if (!worker || typeof worker.postMessage !== 'function' || scheduledWorkers.has(worker)) return;
  scheduledWorkers.add(worker);

  scheduleAuthoringSafeCallback(() => worker.postMessage({ type: 'SKIP_WAITING' }));
}
