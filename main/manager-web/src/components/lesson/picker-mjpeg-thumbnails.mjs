import { MjpegPlayback } from './mjpeg-playback.mjs';
import { MJPEG_LIMITS } from './mjpeg-mp4.mjs';

export const PICKER_THUMBNAIL_LIMITS = Object.freeze({
  concurrent: 2, acquisitionBytes: MJPEG_LIMITS.fileBytes,
  entries: 24, pixelsBytes: 2 * 1024 * 1024, edge: 160, timeoutMs: 15000,
});

// A retired picker may finish an accepted fetch. Admission remains bounded even
// when another picker mounts before that work finishes.
export class ThumbnailAdmissions {
  constructor() { this.queue = []; this.active = 0; this.bytes = 0; }
  add(bytes, run, onError = error => setTimeout(() => { throw error; }, 0)) {
    const job = { bytes, run, onError };
    this.queue.push(job);
    this.pump();
    return () => { const index = this.queue.indexOf(job); if (index !== -1) this.queue.splice(index, 1); };
  }
  pump() {
    while (this.active < PICKER_THUMBNAIL_LIMITS.concurrent) {
      const index = this.queue.findIndex(job => this.bytes + job.bytes <= PICKER_THUMBNAIL_LIMITS.acquisitionBytes);
      if (index === -1) return;
      const [job] = this.queue.splice(index, 1);
      this.active++; this.bytes += job.bytes;
      Promise.resolve().then(job.run).catch(job.onError).finally(() => {
        this.active--; this.bytes -= job.bytes; this.pump();
      });
    }
  }
}
const admissions = new ThumbnailAdmissions();

export class PickerMjpegThumbnails {
  constructor({ scheduler = admissions, createCanvas = () => document.createElement('canvas'),
    createPlayer = options => new MjpegPlayback(options) } = {}) {
    Object.assign(this, { scheduler, createCanvas, createPlayer });
    this.entries = new Map(); this.cachedBytes = 0; this.disposed = false;
  }
  acquire(src, identity, receive) {
    if (this.disposed) return () => {};
    const key = JSON.stringify([src, identity]);
    let entry = this.entries.get(key);
    if (!entry) {
      entry = { key, src, identity, consumers: new Set(), status: 'queued', canvas: null, error: null };
      this.entries.set(key, entry);
    }
    entry.consumers.add(receive);
    if (entry.status === 'ready' || entry.status === 'error') {
      this.entries.delete(key); this.entries.set(key, entry);
      receive(entry);
    } else if (!entry.cancel) {
      const bytes = Number.isSafeInteger(identity?.bytes) && identity.bytes > 0 && identity.bytes <= MJPEG_LIMITS.fileBytes ? identity.bytes : 0;
      entry.cancel = this.scheduler.add(bytes, () => this.load(entry));
    }
    return () => {
      entry.consumers.delete(receive);
      if (entry.status === 'queued' && !entry.consumers.size) {
        entry.cancel(); this.remove(entry);
      }
    };
  }
  remove(entry) { if (this.entries.get(entry.key) === entry) this.entries.delete(entry.key); }
  retry(src, identity) {
    const entry = this.entries.get(JSON.stringify([src, identity]));
    if (!entry || entry.status !== 'error' || !entry.consumers.size || this.disposed) return;
    entry.status = 'queued'; entry.error = null;
    for (const receive of entry.consumers) receive(entry);
    const bytes = Number.isSafeInteger(identity?.bytes) && identity.bytes > 0 && identity.bytes <= MJPEG_LIMITS.fileBytes ? identity.bytes : 0;
    entry.cancel = this.scheduler.add(bytes, () => this.load(entry));
  }
  async load(entry) {
    // The scheduler can admit a job immediately before its last consumer retires.
    if (this.disposed || !entry.consumers.size) { this.remove(entry); return; }
    entry.status = 'loading';
    let player, timer, timeoutError;
    try {
      player = this.createPlayer({ src: entry.src, identity: entry.identity, playing: false,
        present: bitmap => {
          const scale = Math.min(1, PICKER_THUMBNAIL_LIMITS.edge / Math.max(bitmap.width, bitmap.height));
          const canvas = this.createCanvas();
          canvas.width = Math.max(1, Math.round(bitmap.width * scale));
          canvas.height = Math.max(1, Math.round(bitmap.height * scale));
          const context = canvas.getContext('2d');
          if (!context) return false;
          context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
          entry.canvas = canvas;
          return true;
        },
        onError: error => { entry.error = error; },
      });
      timer = setTimeout(() => {
        timeoutError = new Error('load timed out. Check the selected media origin.');
        player.dispose();
      }, PICKER_THUMBNAIL_LIMITS.timeoutMs);
      await player.load();
      if (timeoutError || entry.error || !entry.canvas) throw timeoutError || entry.error || new Error('MJPEG first frame was not presented');
      entry.status = 'ready';
    } catch (error) {
      entry.error = timeoutError || error;
      entry.status = 'error';
      if (entry.canvas) { entry.canvas.width = 0; entry.canvas.height = 0; entry.canvas = null; }
    } finally {
      clearTimeout(timer);
      if (player) await player.dispose();
    }
    if (this.disposed) { this.releaseCanvas(entry); return; }
    this.cachedBytes += this.size(entry);
    for (const receive of entry.consumers) receive(entry);
    this.trim();
  }
  size(entry) { return entry.canvas ? entry.canvas.width * entry.canvas.height * 4 : 0; }
  releaseCanvas(entry) {
    if (entry.canvas) { entry.canvas.width = 0; entry.canvas.height = 0; entry.canvas = null; }
  }
  trim() {
    const completed = [...this.entries.values()].filter(entry => entry.status === 'ready' || entry.status === 'error');
    while (completed.length > PICKER_THUMBNAIL_LIMITS.entries || this.cachedBytes > PICKER_THUMBNAIL_LIMITS.pixelsBytes) {
      const entry = completed.shift();
      this.cachedBytes -= this.size(entry); this.releaseCanvas(entry); this.entries.delete(entry.key);
    }
  }
  dispose() {
    this.disposed = true;
    for (const entry of this.entries.values()) {
      entry.consumers.clear();
      if (entry.status === 'queued') entry.cancel();
      this.releaseCanvas(entry);
    }
    this.entries.clear(); this.cachedBytes = 0;
  }
}
