# Direct Three-Layer MP4 Lesson Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admin select three robot-ready public MP4 files per cinematic phase, download the exact files directly to robot SD, and render synchronized effects on the physical TFT.

**Architecture:** Reuse existing versioned shared visual assets and store MP4 playback metadata in compatibility JSON. Admin and robot use the same public MP4 URLs and bytes; there is no export worker, derivative pipeline, asset authentication, or application encryption. Firmware provides the only new media implementation: a constrained MJPEG-in-MP4 reader, reusable JPEG decode, chroma compositing, and a shared frame clock.

**Tech Stack:** Vue 2, NestJS 11, PostgreSQL JSONB, Vitest, Python/pytest, ESP-IDF/PlatformIO C++, ROM JPEG decoder, LVGL, ESP32-S3 PSRAM and SD.

---

### Task 1: Accept Reused SD Asset Attestations

**Files:**
- Modify: `main/tbot-server/core/lesson/sd_pack_sync.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Test: `main/tbot-server/tests/test_lesson_sd_sync_attestation_contract.py`
- Test: `main/tbot-server/tests/test_lesson_runtime.py`

- [ ] Write a failing test where `downloadedCount=1`, `reusedCount=1`, `skippedCount=0`, and `failedCount=0` for a two-asset pack.
- [ ] Run `cd main/tbot-server && python3 -m pytest tests/test_lesson_sd_sync_attestation_contract.py tests/test_lesson_runtime.py -q`; expect rejection because `reusedCount` is not included.
- [ ] Treat missing `reusedCount` as zero and require `downloaded + skipped + reused + failed == assetCount`.
- [ ] Return and safely log `reusedCount` without breaking older firmware responses.
- [ ] Re-run the tests; expect PASS.
- [ ] Commit with `git commit -m "fix(lessons): accept reused SD asset attestations"`.

### Task 2: Expose Public MP4 Metadata from Existing Visual Assets

**Files:**
- Modify: `../tbot-backend/src/lessons/visual-assets/shared-visual-asset.service.ts`
- Modify: `../tbot-backend/src/lessons/visual-assets/shared-visual-asset.controller.ts`
- Modify: `../tbot-backend/src/lessons/authoring/lesson-authoring.service.ts`
- Test: `../tbot-backend/src/lessons/visual-assets/shared-visual-asset.spec.ts`
- Test: `../tbot-backend/src/lessons/authoring/lesson-authoring.service.coverage.spec.ts`

- [ ] Write failing tests requiring `url`, `mime_type`, `compatibility_metadata`, and version identity in asset-list and step visual-ref responses.
- [ ] Add a published MP4 fixture whose compatibility metadata is:

```json
{
  "codec": "mjpeg",
  "fps": 15,
  "durationMs": 3200,
  "frameCount": 48,
  "hasAudio": false,
  "rect": { "x": 0, "y": 0, "width": 480, "height": 320 },
  "chromaKey": null
}
```

- [ ] Run:

```bash
cd ../tbot-backend
npx vitest run src/lessons/visual-assets/shared-visual-asset.spec.ts src/lessons/authoring/lesson-authoring.service.coverage.spec.ts
```

Expected: FAIL because URLs and enriched ref metadata are absent.

- [ ] Reuse `resolveAssetUrl(storage_path)` server-side; do not reconstruct public URLs in Vue.
- [ ] Return compatibility metadata unchanged and resolve the same URL for list, detail, and step hydration.
- [ ] Run `npm run typecheck` and the targeted tests; expect PASS.
- [ ] Commit with `git commit -m "feat(lessons): expose public MP4 visual metadata"`.

### Task 3: Validate Robot-Ready MP4 Versions Without Transcoding

**Files:**
- Modify: `../tbot-backend/src/lessons/visual-assets/shared-visual-asset.logic.ts`
- Modify: `../tbot-backend/src/lessons/visual-assets/shared-visual-asset.service.ts`
- Test: `../tbot-backend/src/lessons/visual-assets/shared-visual-asset.spec.ts`

- [ ] Add failing table tests for valid MP4/MJPEG metadata and rejection of H.264, audio, unsupported FPS, invalid dimensions, missing frame count, invalid chroma key, or non-public URL/path.
- [ ] Run `cd ../tbot-backend && npx vitest run src/lessons/visual-assets/shared-visual-asset.spec.ts`; expect new failures.
- [ ] Add a pure `validateRobotMp4Metadata(category, mimeType, compatibilityMetadata)` function.
- [ ] Require `video/mp4`, `codec=mjpeg`, `hasAudio=false`, FPS 15 or 10, positive duration/frame count, and `frameCount == durationMs * fps / 1000` within one-frame rounding.
- [ ] Require background 480x320 and foreground at most 240x240. Require `chromaKey` for `teachingObject` and `robotPose` categories.
- [ ] Keep existing image validation unchanged for renderer-v1/v2 assets.
- [ ] Run typecheck and tests; expect PASS.
- [ ] Commit with `git commit -m "feat(lessons): validate robot-ready MP4 assets"`.

### Task 4: Repair Production Pickers and Add the Third Layer

**Files:**
- Modify: `main/manager-web/src/apis/module/lesson.js`
- Modify: `main/manager-web/src/components/lesson/SharedAssetPicker.vue`
- Create: `main/manager-web/src/components/lesson/CinematicLayerPicker.vue`
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/src/components/lesson/lesson-step-editor-state.js`
- Test: `main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`
- Test: `main/manager-web/tests/browser/lesson-builder-main.js`
- Test: `main/manager-web/scripts/check-lesson-builder-browser.mjs`

- [ ] Add failing assertions that `LessonEditor.vue` contains neither `/backgrounds/backgrounds-manifest.json` nor `/teachobjects/teachobjects-manifest.json`.
- [ ] Add browser fixtures for `scene`, `teachingObject`, and `robotPose` MP4 assets and assert loading, error, empty, video preview, and selected-version states.
- [ ] Run:

```bash
cd main/manager-web
npm run test:lesson-editor-ui
npm run test:lesson-builder-browser
```

Expected: FAIL because static manifests are still used and robot overlay has no picker.

- [ ] Extend `normalizeVisualAsset()` with `url`, codec, FPS, duration, frame count, compatibility, rectangle, and chroma-key fields.
- [ ] Implement `CinematicLayerPicker.vue` with slot/category mapping:

```js
const SLOT_CATEGORY = {
  backgroundScene: 'scene',
  teachingObject: 'teachingObject',
  robotOverlay: 'robotPose',
};
```

- [ ] Use `<video muted playsinline preload="metadata">` for MP4 preview and persist selection by `assetVersionId` through `Api.lesson.setVisualRef()`.
- [ ] Hydrate all selections from `selectedStep.visualRefs`. Published lessons show an immutable-version message and do not silently alter only local preview.
- [ ] Make loading/error/empty states visible even when the returned asset array is empty.
- [ ] Run:

```bash
npm run test:lesson-editor-ui
node scripts/check-lesson-step-editor-state.cjs
npm run test:lesson-builder-browser
npm run test:lesson-visual-library-ui
npm run test:lesson-visual-library-browser
npm run build
```

Expected: PASS.

- [ ] Commit with `git commit -m "fix(admin): load cinematic MP4 layers from backend"`.

### Task 5: Add the Simple Three-Layer Phase Manifest

**Files:**
- Modify: `../tbot-backend/src/lessons/lesson.constants.ts`
- Create: `../tbot-backend/src/lessons/templates/direct-mp4-cinematic.contract.ts`
- Modify: `../tbot-backend/src/lessons/lesson-manifest.logic.ts`
- Modify: `../tbot-backend/src/lessons/authoring/esptft-publish-budget.logic.ts`
- Test: `../tbot-backend/src/lessons/templates/direct-mp4-cinematic.contract.spec.ts`
- Test: `../tbot-backend/src/lessons/lesson-manifest.logic.validation.spec.ts`
- Test: `../tbot-backend/src/lessons/authoring/esptft-publish-budget.logic.spec.ts`

- [ ] Write failing tests for a phase containing exactly `background`, `teachingObject`, and `robotOverlay` MP4 assets.
- [ ] Add negative tests for missing layer, timing mismatch, unpublished ref, wrong codec, audio, missing SHA/URL, or absent chroma metadata.
- [ ] Run:

```bash
cd ../tbot-backend
npx vitest run src/lessons/templates/direct-mp4-cinematic.contract.spec.ts src/lessons/lesson-manifest.logic.validation.spec.ts src/lessons/authoring/esptft-publish-budget.logic.spec.ts
```

Expected: FAIL because espTft currently rejects all MP4 assets.

- [ ] Add a renderer-v3 capability and a direct MP4 phase type; keep renderer-v1/v2 contracts frozen.
- [ ] Permit MP4 only when it comes from three validated visual refs with robot-ready metadata. Continue rejecting arbitrary video in renderer-v1/v2.
- [ ] Include phase metadata and all file identities in manifest checksum projection.
- [ ] Use the approved timing source: fly-in 3200 ms, far beat 800 ms, and walking 5000 ms.
- [ ] Run typecheck, targeted tests, and `npm run build`; expect PASS.
- [ ] Commit with `git commit -m "feat(lessons): publish direct MP4 cinematic phases"`.

### Task 6: Include Public MP4 Files in Existing Generation and SD Packs

**Files:**
- Modify: `../tbot-backend/src/lessons/lesson-asset-generation.repository.ts`
- Modify: `main/tbot-server/core/lesson/asset_cache.py`
- Modify: `main/tbot-server/core/lesson/sd_pack_materializer.py`
- Modify: `main/tbot-server/core/lesson/sd_pack_mcp_payload.py`
- Test: `../tbot-backend/src/lessons/lesson-asset-generation.repository.spec.ts`
- Test: `main/tbot-server/tests/test_lesson_asset_cache.py`
- Test: `main/tbot-server/tests/test_lesson_sd_pack_materializer.py`
- Test: `main/tbot-server/tests/test_lesson_sd_pack_mcp_payload.py`

- [ ] Write failing tests proving all three versioned MP4 refs enter the generation pack and preserve their exact bytes.
- [ ] Assert a plain public URL succeeds with no authorization, cookie, claim, signed parameter, or decryption step.
- [ ] Assert MP4 bypasses legacy JPEG normalization, streams to a staging file, verifies byte count/SHA-256, and atomically commits.
- [ ] Run backend and ESP targeted tests; expect failures at current blanket video gates.
- [ ] Narrow the backend generation and ESP cache exceptions to renderer-v3 validated MP4 assets only.
- [ ] Keep per-file and total-pack size limits and local path containment.
- [ ] Run:

```bash
cd ../tbot-backend && npx vitest run src/lessons/lesson-asset-generation.repository.spec.ts
cd ../robot/esp32-server/main/tbot-server && python3 -m pytest tests/test_lesson_asset_cache.py tests/test_lesson_sd_pack_materializer.py tests/test_lesson_sd_pack_mcp_payload.py -q
```

Expected: PASS.

- [ ] Commit backend and ESP changes separately with focused messages.

### Task 7: Project Cinematic Commands Through the ESP Runtime

**Files:**
- Create: `main/tbot-server/core/lesson/cinematic_contract.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Test: `main/tbot-server/tests/test_lesson_runtime.py`
- Test: `main/tbot-server/tests/test_lesson_runtime_branch_gaps.py`

- [ ] Write failing tests for capability gating, exact three-layer SD URLs, shared timing fields, idempotent sequences, pause/resume/stop, and typed phase-ready ACKs.
- [ ] Assert renderer-v1/v2 firmware never receives renderer-v3 commands.
- [ ] Run `cd main/tbot-server && python3 -m pytest tests/test_lesson_runtime.py tests/test_lesson_runtime_branch_gaps.py -q`; expect failure.
- [ ] Implement a pure `project_cinematic_phase()` that validates exact fields and requires local SD URLs after sync.
- [ ] Send phase identity, duration, FPS, frame count, rectangles, chroma metadata, and three SD paths. Do not send credentials.
- [ ] Wait for phase-ready ACK before lesson progression and keep branch selection server-owned.
- [ ] Re-run tests; expect PASS.
- [ ] Commit with `git commit -m "feat(lessons): forward direct MP4 cinematic phases"`.

### Task 8: Implement the Constrained Firmware MP4 Player

**Files:**
- Create: `../TBOT-Firmware/main/lesson_mjpeg_mp4.h`
- Create: `../TBOT-Firmware/main/lesson_mjpeg_mp4.cc`
- Modify: `../TBOT-Firmware/main/display/lvgl_display/jpg/jpeg_to_image.c`
- Modify: `../TBOT-Firmware/main/display/lvgl_display/jpg/jpeg_to_image.h`
- Create: `../TBOT-Firmware/tests/native/lesson_mjpeg_mp4_test.cc`
- Create: `../TBOT-Firmware/tests/native/lesson_jpeg_reuse_test.cc`
- Create: `../TBOT-Firmware/scripts/run_host_native_lesson_mjpeg_mp4_test.sh`
- Create: `../TBOT-Firmware/scripts/run_host_native_lesson_jpeg_reuse_test.sh`

- [ ] Write failing tests for one valid MJPEG video track plus truncated atoms, oversized lengths, H.264 codec, audio track, fragments, multiple tracks, invalid offsets, and excessive samples.
- [ ] Write a failing test proving repeated JPEG frame decode performs no allocation after decoder initialization.
- [ ] Run the two new host scripts; expect compilation failure because APIs do not exist.
- [ ] Implement bounded MP4 sample-table parsing and streamed frame reads from the existing lesson SD lease.
- [ ] Add a caller-owned reusable JPEG workspace and RGB565 output API while preserving static-image callers.
- [ ] Run:

```bash
cd ../TBOT-Firmware
./scripts/run_host_native_lesson_mjpeg_mp4_test.sh
./scripts/run_host_native_lesson_jpeg_reuse_test.sh
./scripts/run_host_native_jpeg_test.sh
./scripts/verify_rom_jpeg_build.sh
```

Expected: PASS.

- [ ] Commit with `git commit -m "feat(firmware): decode direct MJPEG MP4 lessons"`.

### Task 9: Composite Three Video Layers on One Clock

**Files:**
- Create: `../TBOT-Firmware/main/lesson_chroma_compositor.h`
- Create: `../TBOT-Firmware/main/lesson_chroma_compositor.cc`
- Create: `../TBOT-Firmware/main/lesson_cinematic_renderer.h`
- Create: `../TBOT-Firmware/main/lesson_cinematic_renderer.cc`
- Modify: `../TBOT-Firmware/main/display/lcd_display.h`
- Modify: `../TBOT-Firmware/main/display/lcd_display.cc`
- Modify: `../TBOT-Firmware/main/lesson_handler.cc`
- Modify: `../TBOT-Firmware/main/mcp_server.cc`
- Test: `../TBOT-Firmware/tests/native/lesson_chroma_compositor_test.cc`
- Test: `../TBOT-Firmware/tests/native/lesson_cinematic_renderer_test.cc`
- Test: `../TBOT-Firmware/tests/native/lesson_handler_host_test.cc`

- [ ] Write failing pixel tests for chroma tolerance, feathering, clipping, RGB565 output, and background -> object -> robot order.
- [ ] Write failing lifecycle tests for prepare, frame-zero ready, whole-triplet frame drop/repeat, pause/resume clock rebase, duplicate command, cancel, missing file, decode timeout, insufficient PSRAM, and safe stop before SD lease release.
- [ ] Add renderer-v3 capability fields only when initialization succeeds.
- [ ] Allocate one 480x320 RGB565 framebuffer, reusable decode buffers, and one foreground scratch path in PSRAM during prepare; allocate nothing in the steady-state frame loop.
- [ ] Decode the three streams at one integer frame index, composite once, and atomically present one framebuffer through `LcdDisplay`.
- [ ] Keep current static renderer rejection for arbitrary video; route only validated renderer-v3 commands to the player.
- [ ] Run:

```bash
cd ../TBOT-Firmware
./scripts/run_host_native_lesson_handler_test.sh
./scripts/run_host_native_lesson_visual_animation_test.sh
./scripts/run_host_native_lesson_renderer_trace_test.sh
./scripts/run_host_native_lesson_memory_test.sh
./scripts/run_host_native_sd_fat_session_guard_test.sh
./build-lcdwiki.sh --no-flash
python3 scripts/assert_lcdwiki_prod_config.py sdkconfig
```

Expected: all tests/build checks PASS.

- [ ] Commit with `git commit -m "feat(firmware): render synchronized MP4 lesson layers"`.

### Task 10: Deploy and Verify Production Admin

**Files:**
- Verify: `Dockerfile-web`
- Verify: `deploy/docker-compose.prod.yml`
- Verify: backend production deployment configuration

- [ ] Run backend typecheck/tests/build, admin lesson-studio tests/build, ESP targeted pytest, and firmware no-flash build.
- [ ] Build and deploy immutable backend/admin images from committed revisions; do not hot-copy files.
- [ ] Verify a plain unauthenticated `curl` can download a registered MP4 and its SHA-256 matches backend metadata.
- [ ] In authenticated Safari, open the supplied production lesson and confirm all three pickers appear, preview MP4, save successful version IDs, and persist after reload.
- [ ] Confirm the browser no longer requests either missing static manifest URL.

### Task 11: OTA and Physical End-to-End Gate

**Files:**
- No source changes unless a defect is reproduced and returned to the relevant TDD task.

- [ ] Confirm robot connectivity through APIs/logs/metrics only; never open `/dev/cu.usbmodem101`.
- [ ] OTA the committed firmware and verify renderer-v3 capability after reconnect.
- [ ] Publish/assign a lesson whose required phase assets are public robot-ready MP4 files.
- [ ] Verify direct SD sync: `downloaded + skipped + reused == assetCount`, `failed == 0`, checksum match, and ready attestation.
- [ ] Run fly-in, landing, walking, greeting, teach/listen/thinking, feedback, retry, celebration, and completion.
- [ ] Measure stable FPS, whole-triplet drops, SD latency, heap, PSRAM, panel tearing, watchdog, and reconnect behavior. Use uploaded 10 FPS assets for all three layers if 15 FPS is not stable.
- [ ] Confirm no layer drift, crash, watchdog reset, or unapproved static fallback.
- [ ] Confirm backend records `lesson_completed`.

---

## Self-Review

- The plan uses existing versioned visual assets and compatibility JSON; it adds no export worker, derivative system, credential flow, or application encryption.
- Admin and robot use the same MP4 bytes and public URL.
- SHA-256 remains only to prove exact complete file delivery.
- Renderer-v1/v2 remain unchanged; renderer-v3 is capability gated.
- Every approved requirement maps to a testable task: production picker repair, three slots, public direct download, SD sync, firmware MP4 playback, synchronized compositing, full cinematic phases, and `lesson_completed`.
