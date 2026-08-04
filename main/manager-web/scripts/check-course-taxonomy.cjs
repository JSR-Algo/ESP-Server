/**
 * check-course-taxonomy.cjs
 *
 * Pins the course/lesson authoring taxonomy contract.
 *
 * WHAT THIS PROTECTS — the lesson `age_band` is a child-safety field. The
 * backend gate (`tbot-backend/src/lessons/lesson-assignment.service.ts`)
 * derives a minimum age from it and FAILS OPEN when the band has no parseable
 * leading integer:
 *
 *     const minAge = ageBandMinimum(ageBand);
 *     if (minAge === null) return;   // <- no age restriction at all
 *
 * So a typo in the authoring form ('K-2', 'all ages', 'mẫu giáo') silently
 * removes the age restriction from a lesson instead of erroring. The UI is the
 * only place that can catch it, which is why the forms must (a) offer canonical
 * bands and (b) warn when the chosen band is unenforceable.
 *
 * Mutations that would turn this red:
 *  - Changing `ageBandMinimum` here to stop mirroring the backend parse (e.g.
 *    returning 0 instead of null) -> the fail-open parity assertions fail.
 *  - Dropping the unenforced warning from either form -> the template
 *    assertions fail.
 *  - Reverting the form defaults to the pre-fix 'en'/'6-8' (values that appear
 *    nowhere in the seeded content) -> the default assertions fail.
 */
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

// ---------------------------------------------------------------------------
// 1. Parse parity with the backend `ageBandMinimum`.
// ---------------------------------------------------------------------------
const taxonomySource = read('src/utils/courseTaxonomy.mjs');

// Re-implement the BACKEND function here from its own source semantics, so the
// assertions below compare two independently-written implementations rather
// than comparing the module to itself.
function backendAgeBandMinimum(ageBand) {
  if (typeof ageBand !== 'string') return null;
  const min = parseInt(ageBand.split('-')[0], 10);
  return Number.isFinite(min) ? min : null;
}

// Load the ESM module under test without a bundler.
const taxonomy = {};
{
  const body = taxonomySource
    .replace(/^export\s+(const|function)\s+/gm, '$1 ')
    .concat(
      '\n;__out.AGE_BANDS = AGE_BANDS;'
      + '__out.DEFAULT_AGE_BAND = DEFAULT_AGE_BAND;'
      + '__out.LOCALES = LOCALES;'
      + '__out.DEFAULT_LOCALE = DEFAULT_LOCALE;'
      + '__out.ageBandMinimum = ageBandMinimum;'
      + '__out.ageGateEnforced = ageGateEnforced;'
      + '__out.isCanonicalAgeBand = isCanonicalAgeBand;'
      + '__out.ageBandSeverity = ageBandSeverity;',
    );
  // eslint-disable-next-line no-new-func
  new Function('__out', body)(taxonomy);
}

const PARSE_CASES = [
  // [band, expected minimum]
  ['4-6', 4],
  ['3-4', 3],
  ['10-12', 10],
  ['7', 7],
  // Fail-open cases: NO parseable leading integer -> gate does not run.
  ['K-2', null],
  ['all ages', null],
  ['mẫu giáo', null],
  ['', null],
  [null, null],
  [undefined, null],
  [{}, null],
];

for (const [band, expected] of PARSE_CASES) {
  assert.strictEqual(
    taxonomy.ageBandMinimum(band),
    expected,
    `ageBandMinimum(${JSON.stringify(band)}) must be ${expected}`,
  );
  assert.strictEqual(
    taxonomy.ageBandMinimum(band),
    backendAgeBandMinimum(band),
    `ageBandMinimum(${JSON.stringify(band)}) must match the backend parse`,
  );
}

// The fail-open condition must be reported as unenforced, not merely "custom".
for (const band of ['K-2', 'all ages', 'mẫu giáo', '']) {
  assert.strictEqual(taxonomy.ageGateEnforced(band), false, `${band} must be unenforceable`);
  assert.strictEqual(
    taxonomy.ageBandSeverity(band),
    'unenforced',
    `${band} must be classified 'unenforced' so the form warns`,
  );
}
assert.strictEqual(taxonomy.ageBandSeverity('4-6'), 'ok');
assert.strictEqual(taxonomy.ageBandSeverity('5-7'), 'custom', 'parseable non-canonical band is custom');

// ---------------------------------------------------------------------------
// 2. Defaults must match content that actually exists.
// ---------------------------------------------------------------------------
// migration 076 seeds age_band '4-6' and locale 'en-US'; the pre-fix form
// defaults ('6-8' / 'en') matched NOTHING in the system.
assert.strictEqual(taxonomy.DEFAULT_AGE_BAND, '4-6', 'default band must match the seeded curriculum');
assert.strictEqual(taxonomy.DEFAULT_LOCALE, 'en-US', 'default locale must match the seeded content');
assert.ok(taxonomy.AGE_BANDS.includes('4-6'), 'canonical bands must offer the seeded band');
assert.ok(taxonomy.LOCALES.includes('en-US'), 'canonical locales must offer the seeded locale');
assert.ok(
  taxonomy.AGE_BANDS.every((b) => taxonomy.ageGateEnforced(b)),
  'every offered age band must be gate-enforceable',
);

// ---------------------------------------------------------------------------
// 3. Both authoring forms must use the taxonomy and surface the warning.
// ---------------------------------------------------------------------------
for (const [view, testid] of [
  ['src/views/CourseLessons.vue', 'lesson'],
  ['src/views/CourseManagement.vue', 'course'],
]) {
  const src = read(view);
  assert.ok(
    /from '@\/utils\/courseTaxonomy\.mjs'/.test(src),
    `${view} must source its bands/locales from courseTaxonomy.mjs`,
  );
  assert.ok(
    new RegExp(`data-testid="${testid}-age-band"`).test(src),
    `${view} must expose the age-band control for e2e`,
  );
  assert.ok(
    new RegExp(`data-testid="${testid}-age-band-unenforced"`).test(src),
    `${view} must render the unenforced-age-gate warning`,
  );
  assert.ok(
    /\$t\('lesson\.ageBandUnenforced'\)/.test(src),
    `${view} must use the translated unenforced warning`,
  );
  assert.ok(
    !/v-model="form\.ageBand"[\s\S]{0,80}placeholder="6-8"/.test(src),
    `${view} must not reintroduce the free-text '6-8' age band input`,
  );
}

// ---------------------------------------------------------------------------
// 4. Translations exist in both maintained locales.
// ---------------------------------------------------------------------------
for (const locale of ['en', 'vi']) {
  const src = read(`src/i18n/${locale}.js`);
  for (const key of ['lesson.ageBandUnenforced', 'lesson.ageBandCustom']) {
    assert.ok(
      src.includes(`'${key}'`),
      `${locale}.js must define ${key}`,
    );
  }
}

console.log('check-course-taxonomy: OK');
