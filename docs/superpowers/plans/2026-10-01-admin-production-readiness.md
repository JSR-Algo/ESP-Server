# Admin Production Readiness Implementation Plan

Goal: make the existing course/lesson admin correctly inspect and author production lessons using canonical API contracts.
Architecture: preserve immutable publications and existing auth. Load published bundles read-only using the native asset component; keep exact validation findings; distinguish shared catalog from attached legacy assets. Correct source-less demo media. Never fabricate catalog or weaken publish validation.

- [x] Reproduce production API results and inspect native editor: legacy v1,9steps,8 hash-verified assets,empty shared catalog,422 typed validation findings.
- [x] Add failing regression for published bundle mounting/read-only mutation fencing and lesson-switch stale asset prevention. Implement in LessonEditor.vue and LessonAssetManager.vue.
- [x] Add failing regression for typed422 validation details; retain errors/warnings/metrics in editor without admitting publication.
- [x] Fix source-less reference video using a browser regression; preserve alpha sources and codecs.
- [x] Check canonical legacy draft authoring and publication dependencies; resolve code bugs and record data/content prerequisites explicitly.
- [x] Run focused regressions, repository aggregate/build, native actual API/database E2E including published assets and typed validation. Keep source stable during browser runs.
- [ ] Review changes and deploy only verified scope, then authenticate on production and verify real editor/preview. Do not claim content or physical readiness without proof.

Verified: aggregate Lesson Studio checks pass (412 TAP cases, zero failures/skips, plus native browser checks). Published legacy bundle, actual typed422 recovery, exact step persistence and UI validate/preview/simulate/publish pass on Chromium desktop. Four-project acceptance matrix passed40/40 with zero skips, failures or retries. Production-mode build passes. Independent review found no actionable regressions.

Data prerequisites: production published Barn v1 still fails current content validation (48s passive run, non-terminating graph, missing teaching-word metadata); the shared catalog is empty. Do not mutate that publication or advertise device qualification. Canonical 3/5/8-minute fixture was installed only in the owned isolated database, with 13 real hash-verified PNGs. Backend-container loopback media fetch is unavailable in that test topology; browser media was verified separately.

Reproduce legacy browser coverage: set `LESSON_STUDIO_E2E_LEGACY_SOURCE_LESSON_ID` to a published canonical legacy fixture created by the backend repository native `scripts/seed-production-lesson-studio-fixture.ts`, with its content-addressed images served from the configured origin. The test intentionally fails if this prerequisite is absent. Retained cloned publications live only in the disposable test database.
