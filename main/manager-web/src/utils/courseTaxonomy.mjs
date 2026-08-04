/**
 * Canonical course/lesson taxonomy for the authoring UI (course + lesson forms).
 *
 * WHY THIS EXISTS — the age band is a CHILD-SAFETY field, not a label.
 * `lesson_assignment.service.ts` gates every assignment through
 * `assertChildMeetsAgeBand(childId, lesson.age_band)`, which derives the minimum
 * age via `ageBandMinimum()`:
 *
 *     export function ageBandMinimum(ageBand) {
 *       if (typeof ageBand !== 'string') return null;
 *       const min = parseInt(ageBand.split('-')[0], 10);
 *       return Number.isFinite(min) ? min : null;
 *     }
 *
 * and then **fails open** — `if (minAge === null) return;` — so a band with no
 * parseable leading integer (`'K-2'`, `'mẫu giáo'`, `'all ages'`, `''`) silently
 * disables the age restriction for that lesson instead of blocking it. That
 * fail-open is deliberate backend policy (a malformed band must not brick every
 * assignment), which makes the AUTHORING UI the only place the mistake can be
 * caught. `ageGateEnforced()` below exists so the forms can say so out loud.
 *
 * `ageBandMinimum` here is a deliberate byte-for-byte mirror of the backend
 * function. `scripts/check-course-taxonomy.cjs` pins the parity, including the
 * fail-open cases — if the backend parse ever changes, that gate goes red.
 */

/**
 * Age bands offered by default. `'4-6'` is the only band present in the shipped
 * curriculum seed (migration 076) and is the 26-week curriculum's target, so it
 * is the default. The rest are the bands already referenced across the backend
 * scripts; authors may still type a new one (the selects allow-create).
 */
export const AGE_BANDS = ['3-4', '3-5', '4-6', '7-9', '8-10', '10-12'];

/** Safe default: matches the seeded curriculum rather than an invented band. */
export const DEFAULT_AGE_BAND = '4-6';

/**
 * Locales offered by default. `'en-US'` is the only locale in the seed and the
 * value `lesson-assignment.service.ts` stamps on the assignment identity, so a
 * lesson authored as bare `'en'` ships a manifest whose `locale` disagrees with
 * the assignment record describing it.
 */
export const LOCALES = ['en-US', 'vi-VN'];

/** Safe default: matches the seeded content and the assignment identity. */
export const DEFAULT_LOCALE = 'en-US';

/**
 * Mirror of the backend `ageBandMinimum`. Returns the minimum age a band
 * expresses, or null when the band has no parseable leading integer.
 */
export function ageBandMinimum(ageBand) {
  if (typeof ageBand !== 'string') return null;
  const min = parseInt(ageBand.split('-')[0], 10);
  return Number.isFinite(min) ? min : null;
}

/**
 * True when the backend will actually enforce an age restriction for this band.
 * False means the assignment age gate fails open and ANY child may be assigned
 * the lesson — the condition the forms warn about.
 */
export function ageGateEnforced(ageBand) {
  return ageBandMinimum(ageBand) !== null;
}

/**
 * True when the band is one this UI offers by default. A band can be valid
 * (gate-enforceable) without being canonical — authors may create new bands.
 */
export function isCanonicalAgeBand(ageBand) {
  return AGE_BANDS.includes(ageBand);
}

/** True when the locale is one this UI offers by default. */
export function isCanonicalLocale(locale) {
  return LOCALES.includes(locale);
}

/**
 * Classify an age band for the form UI.
 *   'ok'        — canonical band, gate enforced.
 *   'custom'    — non-canonical but gate-enforceable (author's deliberate band).
 *   'unenforced'— no parseable minimum: the age gate will NOT run. Warn loudly.
 */
export function ageBandSeverity(ageBand) {
  if (!ageGateEnforced(ageBand)) return 'unenforced';
  return isCanonicalAgeBand(ageBand) ? 'ok' : 'custom';
}
