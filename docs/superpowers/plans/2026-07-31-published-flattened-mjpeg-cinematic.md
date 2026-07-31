# Published Flattened MJPEG Cinematic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve three editable cinematic source layers while publishing one verified `480x320`, 10 FPS MJPEG MP4 per phase for smooth single-stream robot playback.

**Architecture:** Add an exact renderer-v4 contract and a leased backend derivative build that invokes FFmpeg without a shell, verifies output with ffprobe, and fences stale source revisions. Extend the global generation/ESP SD pack with one flattened asset per phase, then reuse the firmware MJPEG parser and JPEG decoder through a single-stream renderer while retaining renderer-v3 as an explicit fallback.

**Tech Stack:** NestJS/TypeScript/PostgreSQL/Vitest, FFmpeg/ffprobe, Python/pytest ESP server, ESP-IDF C++ host-native tests, Vue 2 manager admin.

---

## File Structure

- Backend contract: `src/lessons/templates/flattened-mjpeg-cinematic.contract.ts` owns exact renderer-v4 phase validation.
- Backend identity: `src/lessons/derivatives/flattened-cinematic-identity.ts` owns canonical derivative input hashing.
- Backend media boundary: `src/lessons/derivatives/flattened-cinematic-media.ts` owns argv construction, subprocess execution, ffprobe parsing, abort, and output verification.
- Backend persistence: migration `114_flattened_cinematic_derivatives.sql` plus repository/service files own requested/processing/ready/failed/stale state.
- Generation integration: existing lesson authoring, manifest, generation build, repository, and worker files consume only verified derivatives.
- ESP projection: `main/tbot-server/core/lesson/flattened_cinematic_contract.py` converts a verified v4 pack asset to a local SD command.
- Firmware: new `lesson_flattened_cinematic_renderer.*` reuses `lesson_mjpeg_mp4.*` and the existing production JPEG adapter.
- Admin: `LessonEditor.vue` and lesson API/i18n expose derivative status and block publish until current assets are ready.
- Harness: add a high-risk story packet and update `docs/TEST_MATRIX.md` only with evidence actually run.

### Task 1: Isolate Repositories And Record High-Risk Intake

**Files:**
- Create: `robot/docs/stories/initiatives/flattened-mjpeg-cinematic/overview.md`
- Create: `robot/docs/stories/initiatives/flattened-mjpeg-cinematic/design.md`
- Create: `robot/docs/stories/initiatives/flattened-mjpeg-cinematic/execplan.md`
- Create: `robot/docs/stories/initiatives/flattened-mjpeg-cinematic/validation.md`
- Create: `robot/docs/decisions/2026-07-31-flatten-cinematic-on-publish.md`

- [ ] **Step 1: Create clean feature worktrees**

Create dedicated branches from current repository HEADs without touching the dirty backend checkout:

```bash
git -C /Users/manhhodinh/Documents/TBOT/tbot-backend worktree add \
  /Users/manhhodinh/Documents/TBOT/.worktrees/backend-flattened-mjpeg \
  -b feature/flattened-mjpeg-cinematic
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware worktree add \
  /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-flattened-mjpeg \
  -b feature/flattened-mjpeg-cinematic
```

Expected: both new worktrees are clean; the original dirty backend files remain unchanged.

- [ ] **Step 2: Write the high-risk story packet**

Copy the four files from `robot/docs/templates/high-risk-story/`, then state the risk flags: public contract, cross-platform, existing behavior, weak live proof, and multi-domain. Link the approved design at `robot/esp32-server/docs/superpowers/specs/2026-07-31-published-flattened-mjpeg-cinematic-design.md` and list the exact software and attended-hardware gates from that design.

- [ ] **Step 3: Record the architecture decision**

Document: source layers stay editable; GIF is rejected; v4 is exact and does not replace v3; 10 FPS is the initial profile; real-device proof is mandatory before production readiness.

- [ ] **Step 4: Verify intake artifacts**

Run:

```bash
rg -n "TBD|TODO" robot/docs/stories/initiatives/flattened-mjpeg-cinematic \
  robot/docs/decisions/2026-07-31-flatten-cinematic-on-publish.md
```

Expected: no output.

### Task 2: Add The Exact Renderer-v4 Backend Contract

**Files:**
- Create: `src/lessons/templates/flattened-mjpeg-cinematic.contract.ts`
- Create: `src/lessons/templates/flattened-mjpeg-cinematic.contract.spec.ts`
- Modify: `src/lessons/lesson.constants.ts`
- Modify: `src/lessons/lesson-manifest.logic.ts`
- Modify: `src/lessons/lesson-manifest.logic.validation.spec.ts`

- [ ] **Step 1: Write failing exact-contract tests**

Add tests that accept one phase with `templateId=flattenedMjpegCinematic`, template version 1, a public resolved asset, lowercase SHA-256, positive bytes, `480x320`, MJPEG, 10 FPS, no audio, and matching duration/frame count. Add one rejection test per invalid field plus extra keys and renderer-v3/v4 identity confusion.

- [ ] **Step 2: Run the focused tests and observe RED**

```bash
npx vitest run src/lessons/templates/flattened-mjpeg-cinematic.contract.spec.ts \
  src/lessons/lesson-manifest.logic.validation.spec.ts
```

Expected: FAIL because the v4 constants and validator do not exist.

- [ ] **Step 3: Implement minimal v4 types and validators**

Add `LESSON_MANIFEST_VERSION_V4 = 'teebot-lesson-renderer.v4'`, exact feature parsing, and pure `validateFlattenedMjpegCinematicPhase(s)` functions. Do not relax renderer-v3 validation.

- [ ] **Step 4: Run GREEN and regression tests**

Run the focused command plus:

```bash
npx vitest run src/lessons/templates/direct-mp4-cinematic.contract.spec.ts \
  src/lessons/lesson-manifest.checksum.spec.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lessons/templates/flattened-mjpeg-cinematic.contract.* \
  src/lessons/lesson.constants.ts src/lessons/lesson-manifest.logic.ts \
  src/lessons/lesson-manifest.logic.validation.spec.ts
git commit -m "feat(lessons): define flattened cinematic renderer v4"
```

### Task 3: Persist Derivative Identity And Lifecycle

**Files:**
- Create: `src/database/migrations/114_flattened_cinematic_derivatives.sql`
- Create: `src/database/migrations/114_flattened_cinematic_derivatives.down.sql`
- Create: `src/lessons/derivatives/flattened-cinematic-identity.ts`
- Create: `src/lessons/derivatives/flattened-cinematic-identity.spec.ts`
- Create: `src/lessons/derivatives/flattened-cinematic.repository.ts`
- Create: `src/lessons/derivatives/flattened-cinematic.repository.spec.ts`

- [ ] **Step 1: Write failing identity tests**

Build a complete canonical input fixture and assert the identity is stable across object key order but changes for every source version/SHA, rect, object-fit, chroma, phase duration, FPS, output size, encoder profile, and quality change.

- [ ] **Step 2: Run RED**

```bash
npx vitest run src/lessons/derivatives/flattened-cinematic-identity.spec.ts
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement canonical hashing**

Use an explicit ordered DTO and SHA-256. Reject unknown/missing composition values before hashing; never hash arbitrary JSON directly.

- [ ] **Step 4: Write migration/repository tests first**

Specify a table keyed by `derivative_id` with lesson/version/phase/source revision, status enum check, attempt/lease fields, output identity fields, timestamps, and normalized error code. Require one current request per lesson version/phase/revision and ensure a stale lease cannot mark a newer request ready.

- [ ] **Step 5: Implement migration and repository**

Repository operations: request-or-reuse, get phase statuses, lease next due job, renew/abort, commit verified output if source revision still matches, mark bounded failure, and recover expired leases.

- [ ] **Step 6: Run GREEN**

```bash
npx vitest run src/lessons/derivatives/flattened-cinematic-identity.spec.ts \
  src/lessons/derivatives/flattened-cinematic.repository.spec.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/database/migrations/114_flattened_cinematic_derivatives* \
  src/lessons/derivatives
git commit -m "feat(lessons): persist flattened cinematic derivatives"
```

### Task 4: Build And Verify MJPEG Derivatives

**Files:**
- Create: `src/lessons/derivatives/flattened-cinematic-media.ts`
- Create: `src/lessons/derivatives/flattened-cinematic-media.spec.ts`
- Create: `src/lessons/derivatives/flattened-cinematic-worker.service.ts`
- Create: `src/lessons/derivatives/flattened-cinematic-worker.service.spec.ts`
- Modify: `src/lessons/lessons.module.ts`
- Create: `src/lessons/fixtures/flattened-cinematic/README.md`

- [ ] **Step 1: Write failing argv/probe/abort tests**

Assert argv is an array passed with `shell:false`; contains exact scale/crop/chromakey/overlay/fps/no-audio/MJPEG filters; rejects paths outside configured roots; parses only MJPEG `480x320`, 10 FPS, no audio, exact duration/frame count; abort kills the process and deletes temporary output.

- [ ] **Step 2: Run RED**

```bash
npx vitest run src/lessons/derivatives/flattened-cinematic-media.spec.ts
```

Expected: FAIL because the media boundary does not exist.

- [ ] **Step 3: Implement the subprocess boundary**

Use `spawn(executable, argv, { shell: false })`, bounded stdout/stderr, a configurable timeout, `AbortSignal`, unique temp paths, atomic rename, streaming SHA-256, byte count, and ffprobe JSON parsing.

- [ ] **Step 4: Write failing worker lifecycle tests**

Cover reuse, lease, success, source-changed conflict, retryable failure, terminal failure, shutdown abort, stale output cleanup, and last-published preservation.

- [ ] **Step 5: Implement the worker**

Keep derivative processing separate from global pack generation. Register only when background workers are enabled; bound concurrency to one initially.

- [ ] **Step 6: Run GREEN and a real fixture smoke**

```bash
npx vitest run src/lessons/derivatives/flattened-cinematic-media.spec.ts \
  src/lessons/derivatives/flattened-cinematic-worker.service.spec.ts
ffmpeg -version
ffprobe -version
```

Generate one tiny fixture in a temporary directory and verify metadata plus representative decoded pixels. Do not commit generated binaries unless an existing fixture policy explicitly permits it.

- [ ] **Step 7: Commit**

```bash
git add src/lessons/derivatives src/lessons/lessons.module.ts \
  src/lessons/fixtures/flattened-cinematic/README.md
git commit -m "feat(lessons): generate flattened MJPEG cinematics"
```

### Task 5: Wire Authoring Status, Publish Gate, And v4 Generation

**Files:**
- Modify: `src/lessons/authoring/lesson-authoring.service.ts`
- Modify: `src/lessons/authoring/lesson-authoring.controller.ts`
- Modify: `src/lessons/authoring/lesson-authoring.dto.ts`
- Create: `src/lessons/authoring/lesson-authoring.flattened-derivatives.spec.ts`
- Modify: `src/lessons/lesson-asset-generation-build.ts`
- Modify: `src/lessons/lesson-asset-generation.repository.ts`
- Modify: `src/lessons/lesson-asset-generation.repository.postgres.spec.ts`
- Modify: `src/lessons/lesson-asset-generation.contract.ts`

- [ ] **Step 1: Write failing authoring and publish tests**

Assert a source/composition change requests a new identity and marks the prior one stale; status API returns exact per-phase states; publish rejects non-ready current phases with a typed 409; ready current phases publish v4; failed work leaves the prior published version unchanged.

- [ ] **Step 2: Run RED**

```bash
npx vitest run src/lessons/authoring/lesson-authoring.flattened-derivatives.spec.ts
```

Expected: FAIL because status/request/publish behavior is absent.

- [ ] **Step 3: Implement request and status wiring**

Add current-revision derivative status to the admin lesson response or a focused endpoint following existing DTO/controller conventions. Request work transactionally when cinematic identity changes.

- [ ] **Step 4: Implement the publish gate and v4 manifest**

Lock the current lesson revision, load verified derivatives, re-check identity inside the transaction, build exact v4 phases, and reject any missing/stale/non-ready phase. Preserve the current immutable publish semantics.

- [ ] **Step 5: Write failing generation pack tests**

Assert v4 discovery includes one asset per phase, checksums change with derivative identity, v3 remains unchanged, and stale/non-MJPEG outputs fail closed.

- [ ] **Step 6: Implement generation integration and run GREEN**

```bash
npx vitest run src/lessons/authoring/lesson-authoring.flattened-derivatives.spec.ts \
  src/lessons/lesson-asset-generation-build.spec.ts \
  tests/lesson-asset-generation.repository.postgres.spec.ts \
  src/lessons/lesson-asset-generation.contract.spec.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/lessons/authoring src/lessons/lesson-asset-generation* \
  src/lessons/lesson-manifest.logic.ts
git commit -m "feat(lessons): publish verified flattened cinematics"
```

### Task 6: Show Derivative Readiness In Admin

**Files:**
- Modify: `main/manager-web/src/apis/module/lesson.js`
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/src/i18n/en.js`
- Modify: `main/manager-web/src/i18n/vi.js`
- Create: `main/manager-web/scripts/check-flattened-derivative-status.mjs`
- Modify: `main/manager-web/package.json`

- [ ] **Step 1: Write the failing admin contract test**

Assert all five states render, phase/layer failures remain actionable, source changes invalidate ready state, polling stops on destroy/source epoch change, and publish is disabled unless every required current phase is ready.

- [ ] **Step 2: Run RED**

```bash
node scripts/check-flattened-derivative-status.mjs
```

Expected: FAIL because status UI and API parsing are absent.

- [ ] **Step 3: Implement minimal status UI**

Place readiness beside the existing `3 Layers`/`Robot Flattened` comparison. Poll only while processing, preserve existing publish reconciliation, and do not let preview failure masquerade as derivative build failure.

- [ ] **Step 4: Run GREEN and build**

```bash
node scripts/check-flattened-derivative-status.mjs
node scripts/check-flattened-cinematic-preview.mjs
node scripts/check-lesson-editor-ui-contracts.mjs
node node_modules/@vue/cli-service/bin/vue-cli-service.js build
```

Expected: PASS; only accepted size/cache warnings.

- [ ] **Step 5: Commit**

```bash
git add main/manager-web/src main/manager-web/scripts \
  main/manager-web/package.json
git commit -m "feat(admin): gate publish on flattened media readiness"
```

### Task 7: Extend ESP Pack Projection And Capability Routing

**Files:**
- Create: `main/tbot-server/core/lesson/flattened_cinematic_contract.py`
- Create: `main/tbot-server/test/test_flattened_cinematic_contract.py`
- Modify: `main/tbot-server/core/lesson/global_generation_poller.py`
- Modify: `main/tbot-server/core/lesson/global_generation_sync.py`
- Modify: `main/tbot-server/core/lesson/lesson_handler.py`
- Modify: existing focused generation/sync/handler tests

- [ ] **Step 1: Write failing v4 projection tests**

Require exact v4 identity, one asset, verified pack readiness, local SD containment, matching SHA/bytes/metadata, and a command containing the local SD path. Reject URL/credential syntax, traversal, extra keys, and v3/v4 confusion.

- [ ] **Step 2: Run RED**

```bash
python -m pytest -q main/tbot-server/test/test_flattened_cinematic_contract.py
```

Expected: FAIL because the v4 projector does not exist.

- [ ] **Step 3: Implement projection and pack routing**

Reuse current fail-closed helpers and typed errors. V4 packs contain one media file per phase; v3 pack shape remains unchanged.

- [ ] **Step 4: Write capability routing tests first**

Assert v4 is selected only for devices advertising the exact feature, v3 compatibility is explicit, and unsupported devices receive no cinematic command.

- [ ] **Step 5: Implement and run GREEN**

Run the new tests plus current global generation, sync, session, and lesson handler suites.

- [ ] **Step 6: Commit**

```bash
git add main/tbot-server/core/lesson main/tbot-server/test
git commit -m "feat(esp): sync and project flattened cinematics"
```

### Task 8: Add Firmware Single-Stream Playback

**Files:**
- Create: `main/lesson_flattened_cinematic_renderer.h`
- Create: `main/lesson_flattened_cinematic_renderer.cc`
- Create: `tests/native/lesson_flattened_cinematic_renderer_test.cc`
- Create: `scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh`
- Modify: `main/lesson_handler.cc`
- Modify: `tests/native/lesson_handler_host_test.cc`
- Modify: `main/CMakeLists.txt`

- [ ] **Step 1: Write failing renderer lifecycle tests**

Cover exact config, prepare, play, frame selection, missed deadline drop/repeat, pause/resume/replay/cancel, sequence fencing, file/decode/present failures, PSRAM failure, repeated cleanup, and no foreground scratch allocation.

- [ ] **Step 2: Run RED**

```bash
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
```

Expected: FAIL because the files do not exist.

- [ ] **Step 3: Implement minimal single-stream renderer**

Reuse `LessonMjpegMp4Reader`, the existing reusable JPEG production adapter, one framebuffer, one JPEG input buffer, and the existing command lifecycle patterns. Do not copy the three-layer compositor.

- [ ] **Step 4: Write failing handler/capability tests**

Assert v4 capability is separate, malformed v4 commands fail closed, session/sequence ownership matches v3, and v3 commands still work.

- [ ] **Step 5: Implement handler wiring and run GREEN**

```bash
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
./scripts/run_host_native_lesson_cinematic_renderer_test.sh
./scripts/run_host_native_lesson_mjpeg_mp4_test.sh
./scripts/run_host_native_lesson_handler_test.sh
```

Expected: PASS.

- [ ] **Step 6: Build the target firmware**

Use the repository's documented ESP-IDF environment and `lcdwiki-es3c35p` target. Record binary size, partition headroom, and SHA-256. Do not flash without an available attended robot and an explicit safe flash path.

- [ ] **Step 7: Commit**

```bash
git add main/lesson_flattened_cinematic_renderer.* main/lesson_handler.cc \
  main/CMakeLists.txt tests/native scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
git commit -m "feat(firmware): play flattened MJPEG cinematics"
```

### Task 9: Cross-Repository Verification And Evidence

**Files:**
- Modify: `robot/docs/stories/initiatives/flattened-mjpeg-cinematic/validation.md`
- Modify: `robot/docs/stories/initiatives/flattened-mjpeg-cinematic/execplan.md`
- Modify: `robot/docs/TEST_MATRIX.md`

- [ ] **Step 1: Run backend gates**

Run focused derivative/v4 suites, typecheck, lint, build, migration tests, OpenAPI validation, and the existing renderer-v3 regression slices. Record exact commit and counts.

- [ ] **Step 2: Run admin and ESP server gates**

Run the flattened preview/status contracts, lesson editor contracts, production manager build, ESP v3/v4 projection, global generation, sync, sessions, and handler tests.

- [ ] **Step 3: Run firmware host and target gates**

Run the four host-native commands from Task 8 plus relevant firmware pytest and target build. Record binary identity and memory estimate without claiming live playback.

- [ ] **Step 4: Run software E2E**

Use a deterministic small three-layer fixture: edit source identity, observe stale -> processing -> ready, publish v4, fetch the public generation, materialize one flattened phase asset, and verify the local firmware command fixture references it.

- [ ] **Step 5: Run browser preview**

Open the admin feature branch, verify desktop/mobile comparison and derivative states, and confirm no console/request errors. Preserve a local preview URL for the user when possible.

- [ ] **Step 6: Run attended hardware only if available**

Flash the exact recorded firmware, play repeated 10 FPS phases/full lesson, capture frame/drop/TFT/heap/PSRAM/watchdog/reset/SD markers, and validate rollback. If no attended robot is available, mark every hardware row `PENDING`; do not infer PASS from host tests.

- [ ] **Step 7: Update evidence and commit each repository**

Update only evidence actually observed. Run `git diff --check` and ensure no original dirty-user files were altered.

### Task 10: Final Review And Branch Handoff

- [ ] **Step 1: Run spec-compliance review**

Compare every acceptance criterion in the design with code/tests/evidence. Fix omissions before quality review.

- [ ] **Step 2: Run code-quality review**

Review subprocess security, stale-job fencing, exact contracts, allocation lifetime, cleanup, concurrency, and regression coverage. Fix all important findings and re-run affected gates.

- [ ] **Step 3: Run final whole-feature verification**

Use the `verification-before-completion` skill and record fresh command output from every claimed passing gate.

- [ ] **Step 4: Present merge choices**

Use the `finishing-a-development-branch` skill. Keep backend/firmware/admin commits separate and clearly state that hardware production readiness remains pending unless attended evidence passed.

## Plan Self-Review

- Every design requirement maps to a task: exact v4 contract, deterministic derivative identity, FFmpeg safety, stale-job fencing, publish readiness, admin state, one-file SD sync, single-stream firmware, v3 fallback, software E2E, and attended hardware proof.
- Renderer-v3 is never silently mutated or removed.
- GIF and 15 FPS are explicitly excluded from the first release.
- Original dirty backend files are protected by a clean worktree.
- No production-readiness claim is permitted without real-device evidence.
