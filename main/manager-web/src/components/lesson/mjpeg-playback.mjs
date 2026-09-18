import { MJPEG_LIMITS, MjpegError, parseMjpegMp4, validateJpegDimensions } from './mjpeg-mp4.mjs';

// Browser image decoding cannot be aborted. Serialize even across retired Vue
// instances so rapid phase replacement cannot accumulate decoded-frame work.
let decodeTail = Promise.resolve();

export class MjpegPlayback {
  constructor({ src, identity, mode, present, onError = () => {},
    fetcher = (...args) => fetch(...args), crypto = globalThis.crypto,
    decode = blob => createImageBitmap(blob), now = () => performance.now() }) {
    Object.assign(this, { src, identity, mode, present, onError, fetcher, crypto, decode, now });
    this.bytes = null;
    this.table = null;
    this.controller = new AbortController();
    this.disposed = false;
    this.ready = false;
    this.playing = false;
    this.ended = false;
    this.seeking = false;
    this.time = 0;
    this.index = -1;
    this.anchor = null;
    this.request = 0;
    this.desired = null;
    this.draining = null;
    this.loading = null;
  }
  state() {
    return { ready: this.ready, pending: Boolean(this.loading || this.draining),
      seeking: this.seeking, ended: this.ended, currentTimeSec: this.time };
  }
  load() {
    if (this.loading) return this.loading;
    this.loading = this.loadFile().finally(() => { this.loading = null; });
    return this.loading;
  }
  async loadFile() {
    const { bytes: declared, sha256, metadata: m } = this.identity || {};
    if (!Number.isSafeInteger(declared) || declared <= 0 || declared > MJPEG_LIMITS.fileBytes
      || !/^[a-f0-9]{64}$/.test(sha256 || '') || !m || m.codec !== 'mjpeg'
      || m.mediaType !== 'video/mp4' || m.hasAudio !== false || ![10, 15].includes(m.fps)) {
      throw new MjpegError('Invalid selected MJPEG identity');
    }
    const response = await this.fetcher(this.src, { signal: this.controller.signal,
      mode: 'cors', credentials: 'same-origin' });
    if (this.disposed) { if (response.body) await response.body.cancel(); return; }
    if (!response.ok || !response.body) {
      if (response.body) await response.body.cancel();
      throw new MjpegError('MJPEG fetch failed: ' + response.status);
    }
    const length = response.headers.get('content-length');
    if (length !== null && Number(length) !== declared) {
      await response.body.cancel();
      throw new MjpegError('MJPEG declared length mismatch');
    }
    const reader = response.body.getReader();
    let bytes = new Uint8Array(declared), offset = 0;
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (this.disposed) return;
        if (done) break;
        if (value.length > declared - offset) throw new MjpegError('MJPEG response exceeds declared bytes');
        bytes.set(value, offset); offset += value.length;
      }
      if (offset !== declared) throw new MjpegError('Truncated MJPEG response');
    } finally {
      try { await reader.cancel(); } finally { reader.releaseLock(); }
    }
    const digest = await this.crypto.subtle.digest('SHA-256', bytes);
    if (this.disposed) return;
    const actual = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    if (actual !== sha256) throw new MjpegError('MJPEG SHA-256 mismatch');
    const table = parseMjpegMp4(bytes);
    if (table.width !== m.width || table.height !== m.height || table.fps !== m.fps
      || table.samples.length !== m.frameCount || table.durationMs !== m.durationMs) {
      throw new MjpegError('MJPEG metadata mismatch');
    }
    for (const sample of table.samples) {
      validateJpegDimensions(bytes.subarray(sample.offset, sample.offset + sample.size), table.width, table.height);
    }
    this.table = table;
    this.bytes = bytes;
    bytes = null;
    this.seek(0);
    await this.settled();
    if (!this.disposed && !this.ready) throw new MjpegError('MJPEG first frame was not presented');
  }
  play() {
    // A pending seek replaces the ended frame; retain Play until it presents.
    if (this.disposed || this.playing || (this.ended && !this.seeking)) return;
    this.playing = true; this.anchor = null;
  }
  pause() {
    this.playing = false; this.anchor = null;
    // A pending explicit seek still needs to present while paused.
    if (!this.seeking) { this.request++; this.desired = null; }
  }
  seek(seconds) {
    if (this.disposed || !this.table) return;
    const duration = this.table.durationMs / 1000;
    const target = Math.max(0, Number(seconds) || 0);
    this.seeking = true; this.anchor = null;
    this.queue(this.mode === 'loop' ? target % duration : Math.min(target, duration));
  }
  tick(timestamp) {
    if (!this.playing || !this.ready || this.disposed || this.draining || this.seeking) {
      this.anchor = null; return;
    }
    if (this.anchor === null) { this.anchor = timestamp; return; }
    const elapsed = Math.max(0, timestamp - this.anchor) / 1000;
    this.anchor = timestamp;
    const duration = this.table.durationMs / 1000;
    const time = this.time + elapsed;
    this.queue(this.mode === 'loop' ? time % duration : Math.min(time, duration));
  }
  queue(time) {
    const index = Math.min(this.table.samples.length - 1,
      Math.floor(time * this.table.timescale / this.table.frameDurationTicks + 1e-9));
    if (!this.draining && index === this.index) {
      this.time = time; this.seeking = false;
      this.ended = this.mode !== 'loop' && time >= this.table.durationMs / 1000;
      if (this.ended) this.playing = false;
      return;
    }
    this.desired = { index, time, request: ++this.request };
    if (!this.draining) {
      this.draining = this.drain().catch(error => {
        if (this.disposed) return;
        this.ready = false; this.playing = false; this.desired = null; this.seeking = false;
        this.bytes = null; this.table = null;
        this.onError(error);
      }).finally(() => {
        this.draining = null; this.anchor = this.playing ? this.now() : null;
        if (this.desired && !this.disposed) this.queue(this.desired.time);
      });
    }
  }
  async drain() {
    while (this.desired && !this.disposed) {
      const target = this.desired;
      this.desired = null;
      if (target.index !== this.index) {
        let bitmap, release;
        const previous = decodeTail;
        decodeTail = new Promise(resolve => { release = resolve; });
        try {
          await previous;
          if (this.disposed || target.request !== this.request) continue;
          const sample = this.table.samples[target.index];
          bitmap = await this.decode(new Blob([this.bytes.subarray(sample.offset, sample.offset + sample.size)], { type: 'image/jpeg' }));
          if (this.disposed || target.request !== this.request) continue;
          if (bitmap.width !== this.table.width || bitmap.height !== this.table.height
            || !this.present(bitmap)) throw new MjpegError('MJPEG frame could not be presented');
        } catch (error) {
          if (!this.disposed && target.request === this.request) throw error;
          continue;
        } finally { if (bitmap) bitmap.close(); release(); }
      }
      if (this.disposed || target.request !== this.request) continue;
      this.index = target.index; this.time = target.time; this.ready = true; this.seeking = false;
      this.ended = this.mode !== 'loop' && this.time >= this.table.durationMs / 1000;
      if (this.ended) this.playing = false;
    }
  }
  async settled() { while (this.draining) await this.draining; }
  dispose() {
    this.disposed = true; this.controller.abort(); this.request++;
    this.playing = false; this.ready = false; this.desired = null;
    this.bytes = null; this.table = null;
    return Promise.allSettled([this.loading, this.draining]);
  }
}
