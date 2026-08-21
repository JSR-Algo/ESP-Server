# Course Mode V2 Task 06 Runtime and Integration Validation

Date: 2026-08-22
Status: `REVIEW PENDING` (runtime lanes pass; final independent review not yet closed)
Data policy: synthetic scenarios only; no real-child audio, transcript, profile, or observation data was used.

## Frozen Candidate

| Component | Immutable identity |
| --- | --- |
| ESP server | `7e2628a9b9b4c3c7bbde4b426455700a4e0b7268` |
| Firmware | `d47174daebe17b9c1a9d1a1eb506711a57cd3512` |
| Backend/pilot | `657474ff3b58fba2c3c31f2978d53370ffad8b11` |
| Fixture SHA-256 | `05e18ae61aee0660c653a9386854552a23f90c8a1f8cfb9e7ff4e15d1d277470` in all three repositories |
| Pilot | `course-mode-pilot-cat-ball@v1`, package version 1 |
| Renderer | `teebot-lesson-renderer.v4` only |
| Semantic checksum | `cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264` |
| Layout checksum | `e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c` |
| Release state | draft, unpublished, unassigned, production disabled |

Validation used task-owned clean worktrees pinned to these SHAs. The owning ESP `main` has an authorized external modification to `test_lesson_voice_output_discipline.py`; it was never staged, edited, reverted, or copied into this candidate. Its SHA-256 remained `08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3`, and an independent owning-main collection passed 16 tests. Candidate claims below remain based on the clean ESP checkout at `7e2628a9...`.

## Runtime Matrix

| Lane | Fresh command/evidence | Result |
| --- | --- | --- |
| Deterministic full sessions | `python3 scripts/course_mode_task06_runtime_validation.py --backend-root ... --firmware-root ... --esp-validation-sha 985d55dd... --iterations 2000 --min-duration-seconds 60 --output .../metrics/runtime-soak-60s.json` | PASS: 22 explicit-outcome, measured multi-step session types; 126,710 iterations; 2,787,620 sessions; 60.000111 s; zero failures |
| ESP runtime, reconnect, cache, safety | `python3 -m pytest -q` over Course Mode runtime, embodied dispatcher, forwarder, cache, reconnect, state-machine, and branch-gap suites | PASS: 304 passed, 2 skipped in 62.42 s |
| Firmware action/ACK/cancel/recovery | native embodied/handler harnesses plus firmware Python runtime suites | PASS: 219 + 2,851 native checks; runtime subset 243 passed; privacy subset 253 passed |
| Backend manifest/package/privacy/flags | focused Vitest Course Mode, pilot, rollout, ingest, redaction, malformed-contract suites | PASS: focused 107; privacy/security rerun 106 |
| Timing contracts | `pytest` dispatcher, orchestrator, mastery, and Google Live Course Mode suites | PASS: 82; action ACK timeout 2.0 s, rest settle 0.25 s, soft deadline 540 s, delayed recall at 70 s simulated |
| Contract parity and V1 fallback | backend vector generation/check plus backend/ESP/firmware consumer suites | PASS: backend 130, ESP 169; vector SHA `95e35b3576656c08562d58ac818870fbc4620c9b9ebc1a418fe81a8c861a4ddb` |
| Reliability/resource trend | 60-second bounded wall-clock soak with tracemalloc, FD, thread, and process metrics | PASS: thread delta 0, FD delta 0, heap current delta 544 B against a 1 MiB fail-closed bound, heap peak 5,240 B, max RSS 71,057,408 B |
| Visual sampling | all eight cues sampled at start/middle/end and assembled into a contact sheet | PASS: 24/24 captures at 480x320; no clipping, overlap, unsafe z-order, hidden target, or answer leakage observed |
| Broad regression | complete repository suites in the frozen worktrees | PASS with one resolved contention event: backend 5,916 passed/643 skipped; firmware 1,269 passed/1 skipped; ESP 4,117 passed/8 skipped and one nginx timing failure that passed alone in 4.70 s |
| Static/build | backend typecheck, explicit JSON ESLint, Nest build; ESP compileall; firmware native coverage | PASS: ESLint 1,316 files/0 findings; firmware lines 3,467/3,467 (100%) |

The 22 deterministic journeys cover early knowledge, repetition only, Vietnamese/mixed response, partial speech, silence, low confidence, echo/barge-in behavior, side story, emotional share, refusal, fatigue, child question, reconnect, duplicate tool call, delayed recall success/failure, one-word close, two-word completion, safety pause, technical recovery, visible-answer rejection, and related-story redirect. Every journey asserts an explicit expected outcome and continues through a multi-step opening, interaction/recovery/bridge path, and closing state. Exact journey names and per-capture hashes are in `metrics/runtime-soak-60s.json`.

## Hard Assertions

- Immediate repetition remains non-independent evidence and never produces recall or mastery.
- Assessment opens only after speech/action completion, returned-to-rest ACK, and the 250 ms settle period; cancellation and timeout paths cannot mutate learning evidence.
- Normal misses use neutral support paths; sad, crying, angry, shaming, and visible-answer leakage are rejected by contract and pilot tests.
- Snapshot/reconnect, stale identity, stale generation, and duplicate-operation tests prove zero repeated praise, motion, or durable evidence. The frozen reconnect artifact records `{praise: 0, motion: 0, evidence: 0}`.
- Unsupported capability and disabled flags reject V2 before partial admission; V1 manifest, renderer, forwarder, cache, and contract-vector suites remain unchanged.
- Stop, safety pause, firmware restart simulation, ESP restart/snapshot restore, backend timeout, missing/corrupt asset, power-loss boundary, and resume paths complete or fail closed without deadlock.

## Privacy and Security

Malformed contract/payload cases, bounded fields, authorization defaults, replay identity, redaction, and durable-event allowlists were run fresh. Pilot durable events are scanned against `audio|transcript|utterance|family|storyText|pronunciationScore|confidenceScore|debug`; no forbidden key or serialized value is present. Firmware credential/token redaction suites passed 253 tests. Evidence artifacts were generated from repository fixtures and synthetic labels only.

Production controls remained false: ESP `course_mode_v2_enabled: false`; backend publish enablement is false unless the environment value is exactly lowercase `true`; pilot release state remained draft/unpublished/unassigned/production-disabled. No deployment, publication, assignment, production migration, upload, OTA, flash, or flag enablement occurred.

## Failure Analysis and Reruns

1. Four backend legacy tests supplied non-UUID lesson IDs or incomplete fake query pools after the Course Mode lookup became part of the shared path. Test-only fixtures were corrected; focused rerun passed 69 and the complete backend suite passed 5,916.
2. Two firmware source-contract tests sliced a broader code block or matched a generic active-state store. Assertions were narrowed to the owning lambda and explicit false-store ordering; focused rerun passed 2 and the complete firmware suite passed 1,269.
3. The ESP broad suite had one concurrent local-nginx runtime timeout amid 4,117 passes. The exact test passed alone in 4.70 s; no product or test change was required.
4. A privacy suite was initially launched concurrently with deterministic regeneration and observed the generator's intentional replace-in-place interval. Regeneration completed byte-identically and clean; the privacy suite was rerun serially and passed 106. Both logs are retained.
5. Firmware's detached checkout lacked an ignored/generated ESP JPEG component input. The generated input was restored only to the ignored worktree path for the test; no generated component is committed.

## Reproducibility and Environment

Toolchain: macOS 26.2 arm64; Python 3.14.6; pytest 9.1.1; Node 22.23.2; npm 10.9.8; pnpm 11.19.0; Vitest 2.1.9; Apple clang 21.0.0; CMake 4.3.1; Ninja 1.13.2; ffmpeg/ffprobe 8.1.

The backend dependency manifests have a pre-existing reproducibility defect: `npm ci` reports missing `openapi-types@12.1.3`, and `pnpm install --frozen-lockfile` reports missing `yaml@^2.9.0`. Immutable validation used `pnpm install --no-frozen-lockfile --lockfile=false --ignore-scripts`, followed by local `prisma generate`; no lockfile change is included. This is recorded as a residual packaging defect, not a runtime behavior failure.

`IDF_PATH` is unset and the previously named `idf5.5_py3.14_env` is unavailable, so a fresh ESP-IDF firmware build could not be repeated. The frozen parent handoff had a successful pre-merge build, and the later frozen firmware commits only changed fixture-test portability. Native firmware checks, sanitizer-backed parent evidence, Python regression, and 100% native lesson-line coverage were reproduced here. PostgreSQL-required integration files remained skipped without dedicated test database URLs; pure service/contract/runtime paths were exercised by the passing complete backend suite.

## Evidence Index

- Machine manifest: `docs/qa/artifacts/2026-08-22-course-mode-task06/candidate-manifest.json`
- Soak metrics: `docs/qa/artifacts/2026-08-22-course-mode-task06/metrics/runtime-soak-60s.json`
- Soak process log: `docs/qa/artifacts/2026-08-22-course-mode-task06/logs/runtime-soak-60s.log`
- Visual contact sheet: `docs/qa/artifacts/2026-08-22-course-mode-task06/captures/all-cues-contact-sheet.png` (`6832327aba40098d2bb60bd3f729636b2a858f407c0a9fb62b7c8a07532e553e`)
- Focused/full/static/runtime/privacy/timing logs: `docs/qa/artifacts/2026-08-22-course-mode-task06/logs/`
- ESLint structured output: `docs/qa/artifacts/2026-08-22-course-mode-task06/metrics/backend-eslint.json`

## Independent Review and Commit Handoff

Three independent read-only reviews covered the complete scoped diffs. Backend and firmware reviews found no actionable issue and confirmed that the fixture changes preserve the original assertions. ESP reviews found that SHA identity was reported but not enforced, the validation test depended on disposable worktree paths, ignored evidence would not be committed, the evidence commit could not rerun its own harness, tracked rename sources and dirty/untracked/ignored runtime paths were not fully rejected, retained heap growth was measured but not gated, special journeys self-baselined, isolated transitions were mislabeled as complete sessions, the reconnect journey closed the pre-restore orchestrator, synthetic markers were counted as operations, delayed recall bypassed the orchestrator, and executable journey logic was not bound to the reviewed revision. All findings were fixed: executable validation changes must be committed and pinned by `--esp-validation-sha`, only evidence artifacts may change after that pin, rename sources and unexpected tracked/untracked/ignored runtime inputs fail the gate, generated caches/dependencies are explicitly inventoried, retained heap growth has a 1 MiB fail-closed bound, every special journey has an explicit expected result, every soak unit records its actual opening, scenario, recovery/bridge, and closing actions, reconnect continues entirely on the restored instance, and delayed recall runs through authored `CourseOrchestrator.observe` activities. Generated visual evidence uses repository-relative paths rather than machine-specific absolute paths. The opt-in cross-repository tests passed, and the final 60-second soak passed with all three runtime trees matching their frozen bases and ESP executable validation SHA `985d55ddefdc3fb2ff00e06959929d793b9dceaf`. Security-pattern scans and `git diff --check` were clean.

Task-owned branch commits for parent integration:

| Repository | Branch | Commit |
| --- | --- | --- |
| Backend | `validation/course-mode-task06-regression-fixes` | `1920586d48e5448dcf653dbfa2391c7ef346fcd9` |
| Firmware | `validation/course-mode-task06-regression-fixes` | `df70b5a12c68f5a6ab07f981cb7c10113e7dbc01` |
| ESP report/harness | `validation/course-mode-task06-runtime-evidence` | recorded in final handoff after this evidence commit |

Per the parent integration handoff, Task 06 did not merge owning `main` branches and did not remove worktrees or branches. Parent integration must preserve the protected ESP main file and rerun the required post-merge checks.

## Residual Boundary

This is a software runtime gate. It does not prove physical TFT visibility, bezel/overscan, microphone performance, motor comfort/noise, lighting tolerance, power/thermal behavior, child-learning efficacy, or production readiness. Those remain Task 07 and later gates. Farm v9, T54, protected changes, and all external worktrees were left untouched.

## Verdict

`REVIEW PENDING`

All software runtime lanes required by Task 06 passed on the frozen candidate, subject to the explicitly recorded environment limits. Task 07 is not authorized until the final independent ESP review is recorded clean. No production rollout or child-learning efficacy claim is authorized.
