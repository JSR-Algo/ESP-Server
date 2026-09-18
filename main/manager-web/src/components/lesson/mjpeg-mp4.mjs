// The accepted subset and ceilings mirror firmware lesson_mjpeg_mp4.h/.cc.
export const MJPEG_LIMITS = Object.freeze({
  fileBytes: 128 * 1024 * 1024, sampleBytes: 1024 * 1024,
  samples: 900, width: 1920, height: 1080, atoms: 128, stsc: 32, timescale: 1000000
});
export class MjpegError extends Error {
  constructor(message) { super(message); this.name = 'MjpegError'; }
}
function requireValue(ok, message) { if (!ok) throw new MjpegError(message); }

export function parseMjpegMp4(input) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input);
  requireValue(bytes.length >= 8 && bytes.length <= MJPEG_LIMITS.fileBytes, 'MP4 file size exceeds bounds');
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const u32 = p => view.getUint32(p);
  const u16 = p => view.getUint16(p);
  const four = p => String.fromCharCode(...bytes.subarray(p, p + 4));
  const u64 = p => {
    const n = u32(p) * 4294967296 + u32(p + 4);
    requireValue(Number.isSafeInteger(n), 'Unsafe MP4 integer');
    return n;
  };
  function scan(start, end, allowed) {
    const result = {};
    let count = 0;
    for (let p = start; p < end;) {
      requireValue(end - p >= 8, 'Truncated MP4 box');
      let size = u32(p), header = 8;
      const type = four(p + 4);
      if (size === 1) {
        requireValue(end - p >= 16, 'Truncated extended box');
        size = u64(p + 8); header = 16;
      }
      requireValue(size >= header && size <= end - p && size <= MJPEG_LIMITS.fileBytes, 'Invalid MP4 box range');
      requireValue(++count <= MJPEG_LIMITS.atoms && allowed.includes(type), 'Unsupported MP4 box: ' + type);
      requireValue(!result[type] || ['free', 'skip', 'wide'].includes(type), 'Duplicate MP4 box: ' + type);
      result[type] = { p: p + header, end: p + size, size: size - header };
      p += size;
    }
    return result;
  }
  const need = (map, name) => { requireValue(Boolean(map[name]), 'Missing MP4 box: ' + name); return map[name]; };
  const children = (a, allowed) => scan(a.p, a.end, allowed);
  function full(a, min, exact = false) {
    requireValue(a.size >= min && (!exact || a.size === min), 'Invalid MP4 table length');
    requireValue(bytes[a.p] === 0, 'Unsupported MP4 box version');
    return a.p;
  }
  const top = scan(0, bytes.length, ['ftyp', 'moov', 'mdat', 'free', 'skip', 'wide']);
  need(top, 'ftyp');
  const mdat = need(top, 'mdat');
  const moov = children(need(top, 'moov'), ['mvhd', 'trak', 'udta', 'meta']);
  const movie = full(need(moov, 'mvhd'), 20);
  const movieScale = u32(movie + 12), movieDuration = u32(movie + 16);
  const trak = children(need(moov, 'trak'), ['tkhd', 'mdia', 'edts']);
  need(trak, 'tkhd');
  if (trak.edts) {
    const edits = children(trak.edts, ['elst']), p = full(need(edits, 'elst'), 20, true);
    requireValue(u32(p + 4) === 1 && u32(p + 8) === movieDuration && u32(p + 12) === 0
      && u16(p + 16) === 1 && u16(p + 18) === 0, 'Unsupported MP4 edit list');
  }
  const mdia = children(need(trak, 'mdia'), ['mdhd', 'hdlr', 'minf']);
  const media = full(need(mdia, 'mdhd'), 20);
  const timescale = u32(media + 12), durationTicks = u32(media + 16);
  requireValue(timescale > 0 && timescale <= MJPEG_LIMITS.timescale && movieScale > 0
    && movieScale <= MJPEG_LIMITS.timescale && durationTicks > 0 && movieDuration > 0, 'Invalid MP4 timebase');
  const handler = full(need(mdia, 'hdlr'), 12);
  requireValue(four(handler + 8) === 'vide', 'MP4 must contain one video track only');
  const minf = children(need(mdia, 'minf'), ['vmhd', 'dinf', 'stbl']);
  need(minf, 'vmhd');
  const stbl = children(need(minf, 'stbl'), ['stsd', 'stts', 'stsc', 'stsz', 'stco', 'co64']);
  const description = need(stbl, 'stsd'), sd = full(description, 16);
  requireValue(u32(sd + 4) === 1, 'Multiple MP4 sample descriptions');
  const entrySize = u32(sd + 8), codec = four(sd + 12);
  requireValue(entrySize >= 86 && entrySize === description.size - 8
    && ['mp4v', 'jpeg', 'mjpa'].includes(codec), 'Unsupported MP4 sample entry');
  const width = u16(sd + 40), height = u16(sd + 42);
  requireValue(width > 0 && width <= MJPEG_LIMITS.width && height > 0
    && height <= MJPEG_LIMITS.height, 'MP4 dimensions exceed bounds');
  const ts = full(need(stbl, 'stts'), 16, true);
  const count = u32(ts + 8), delta = u32(ts + 12);
  requireValue(u32(ts + 4) === 1 && count > 0 && count <= MJPEG_LIMITS.samples && delta > 0, 'Invalid MP4 timing table');
  requireValue(count * delta === durationTicks && durationTicks * movieScale === movieDuration * timescale
    && timescale * 1000 % delta === 0, 'MP4 duration mismatch');
  const sizeBox = need(stbl, 'stsz'), sz = full(sizeBox, 12), common = u32(sz + 4);
  requireValue(u32(sz + 8) === count && sizeBox.size === 12 + (common ? 0 : count * 4), 'MP4 sample count mismatch');
  const sizes = Array.from({ length: count }, (_, i) => {
    const size = common || u32(sz + 12 + i * 4);
    requireValue(size >= 4 && size <= MJPEG_LIMITS.sampleBytes, 'MP4 sample size exceeds bounds');
    return size;
  });
  requireValue(Boolean(stbl.stco) !== Boolean(stbl.co64), 'Ambiguous MP4 offsets');
  const offsetBox = stbl.stco || stbl.co64, co = full(offsetBox, 8), chunks = u32(co + 4), stride = stbl.co64 ? 8 : 4;
  requireValue(chunks > 0 && chunks <= MJPEG_LIMITS.samples && offsetBox.size === 8 + chunks * stride, 'Invalid MP4 chunk count');
  const scBox = need(stbl, 'stsc'), sc = full(scBox, 8), entries = u32(sc + 4);
  requireValue(entries > 0 && entries <= MJPEG_LIMITS.stsc && scBox.size === 8 + entries * 12, 'Invalid MP4 chunk table');
  const runs = Array.from({ length: entries }, (_, i) => {
    const p = sc + 8 + i * 12, first = u32(p), perChunk = u32(p + 4);
    requireValue(first >= 1 && first <= chunks && (i ? first > u32(p - 12) : first === 1)
      && perChunk > 0 && perChunk <= count && u32(p + 8) === 1, 'Invalid MP4 chunk mapping');
    return { first, perChunk };
  });
  const samples = [];
  let run = 0, previousEnd = mdat.p;
  for (let chunk = 1; chunk <= chunks; chunk++) {
    while (run + 1 < runs.length && runs[run + 1].first <= chunk) run++;
    const p = co + 8 + (chunk - 1) * stride;
    let offset = stride === 8 ? u64(p) : u32(p);
    for (let j = 0; j < runs[run].perChunk; j++) {
      requireValue(samples.length < count, 'Excess MP4 samples');
      const size = sizes[samples.length];
      requireValue(offset >= previousEnd && offset <= mdat.end && size <= mdat.end - offset, 'MP4 sample outside media data');
      requireValue(u16(offset) === 0xffd8 && u16(offset + size - 2) === 0xffd9, 'MP4 sample is not JPEG');
      samples.push({ offset, size, ptsTicks: samples.length * delta });
      previousEnd = offset += size;
    }
  }
  requireValue(samples.length === count, 'Missing MP4 samples');
  return { width, height, timescale, durationTicks, frameDurationTicks: delta,
    durationMs: durationTicks * 1000 / timescale, fps: timescale / delta, samples };
}

// Check the compressed image dimensions before asking a browser to allocate pixels.
export function validateJpegDimensions(bytes, width, height) {
  let p = 2;
  while (p < bytes.length - 2) {
    requireValue(bytes[p++] === 255, 'Invalid JPEG marker');
    while (bytes[p] === 255) p++;
    const marker = bytes[p++];
    requireValue(p + 2 <= bytes.length && marker !== 0xda && marker !== 0xd9, 'Missing JPEG frame header');
    const size = bytes[p] * 256 + bytes[p + 1];
    requireValue(size >= 2 && size <= bytes.length - p, 'Invalid JPEG segment');
    if ([0xc0, 0xc1, 0xc2].includes(marker)) {
      requireValue(size >= 8 && bytes[p + 2] === 8 && bytes[p + 3] * 256 + bytes[p + 4] === height
        && bytes[p + 5] * 256 + bytes[p + 6] === width, 'JPEG dimensions mismatch');
      return;
    }
    p += size;
  }
  throw new MjpegError('Missing JPEG frame header');
}
