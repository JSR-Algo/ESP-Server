# Physical Lesson Renderer V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the physical 480x320 robot run one opening fly/land/walk/greet sequence and truthful per-step visual reactions while preserving byte-compatible renderer-v1 behavior.

**Architecture:** `teebot-lesson-renderer.v2` is the only renderer-v2 identity across manifests, capabilities, WebSocket envelopes, cache keys, and audit evidence. The backend publishes immutable v2 manifests and checksums; the ESP server remains default-off, negotiates the exact capability, correlates asynchronous visual ACKs per sequence, and owns physical motion. Firmware owns generation- and transport-epoch-gated LVGL work, cancellation, fallback rendering, and delayed ACK completion. Admin and firmware export deterministic traces from the same pinned contract.

**Tech Stack:** NestJS/TypeScript/Vitest/PostgreSQL, Python/pytest/aiohttp, ESP-IDF C++17/FreeRTOS/LVGL/native clang++ tests, Vue 2/Element UI/Chromium browser harness, Docker/Render/VPS/HIL.

---

## Repository Map and Command Rule

- Backend root: `/Users/manhhodinh/Documents/TBOT/tbot-backend`
- ESP server/admin root: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server`
- Firmware root: `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware`
- Every command below includes an explicit `cd`; do not infer a working directory.
- Preserve unrelated dirty files and never include `tbot-mobile/src/__env__.ts` in renderer commits.
- Do not enable renderer v2 globally until Task 10 records all software, HIL, soak, rollback, and mixed-fleet evidence.

## Frozen Renderer-V2 Wire Contract

The following names and shapes are authoritative for every task:

```json
{
  "manifestVersion": "teebot-lesson-renderer.v2",
  "protocolVersion": "teebot-lesson-renderer.v2",
  "features.renderer": [
    "teebot-lesson-renderer.v1",
    "teebot-lesson-renderer.v2"
  ],
  "features.lessonRendererV2": {
    "openingEntrance": true,
    "visualStateEvents": true,
    "physicalMotionOwner": "server",
    "singleSpriteEntrance": true
  }
}
```

`rendererVersion: 2` is forbidden as a second capability or identity field. UI may display a derived label `Renderer v2`, but persisted and wire contracts use the exact string `teebot-lesson-renderer.v2`.

Every server-to-firmware `lesson_visual_state` uses the normal lesson envelope and this body:

```json
{
  "state": "correct",
  "overlayKey": "celebrate",
  "motionPreset": "celebrate",
  "visualGeneration": 17
}
```

The corresponding firmware ACK uses the inbound firmware sequence as its own envelope sequence and correlates only through `body.acks`:

```json
{
  "type": "lesson_ack",
  "protocolVersion": "teebot-lesson-renderer.v2",
  "assignmentId": "assignment-id",
  "sessionId": "session-id",
  "stepId": "step-id",
  "sequence": 41,
  "body": {
    "acks": 12,
    "accepted": true,
    "degraded": false,
    "degradedReason": null,
    "visualGeneration": 17
  }
}
```

Rules:

- Exactly one of `accepted=true` or `accepted=false` is present.
- `accepted=true` means the requested visual reached its final or documented fallback state; only then may the server dispatch the associated physical motion.
- `accepted=false` means the request was rejected before applying a state; `degradedReason` is required and no physical motion is dispatched.
- A documented fallback ACK is `accepted=true`, `degraded=true`, with one stable reason.
- The stable reasons are `missingOverlay`, `animationStartFailed`, `phaseTimeout`, `reducedMotion`, `unsupportedContract`, `assetIdentityMismatch`, and `insufficientHeap`.
- ACK identity must match assignment, session, step, protocol version, correlated server sequence, visual generation, and current transport epoch. Stale, duplicate, replaced-session, or late ACKs are idempotent no-ops.
- Server waiters are stored per outbound sequence. A single global ACK timer is not used for v2 visual frames.

### Task 1: Freeze Backend V2 Identity, Checksum, ETag, and Backfill

**Files:**
- Modify: `src/lessons/lesson.constants.ts`
- Modify: `src/lessons/lesson-manifest.logic.ts`
- Modify: `src/lessons/lesson-manifest.service.ts`
- Test: `src/lessons/lesson-manifest.logic.spec.ts`
- Test: `src/lessons/lesson-manifest.checksum-parity.spec.ts`
- Test: `src/lessons/lesson-manifest.serve-gate.spec.ts`
- Test: `src/lessons/lesson-manifest.controller.cache.spec.ts`
- Test: `src/lessons/lesson-manifest.controller.validation.spec.ts`
- Create: `src/database/migrations/111_renderer_v2_contract.sql`
- Create: `src/database/migrations/111_renderer_v2_contract.down.sql`
- Create: `scripts/migrate-renderer-v2-manifests.mjs`
- Create: `src/lessons/renderer-v2-backfill.spec.ts`

- [ ] **Step 1: Write failing identity and cache tests**

Assert that v1 remains exactly `teebot-lesson-renderer.v1`, v2 is exactly `teebot-lesson-renderer.v2`, an absent capability remains v1-only, and a v1-only request cannot receive v2. Assert that the same lesson/profile rendered as v1 and v2 has different checksums and strong ETags, and that `If-None-Match` carrying the v1 ETag returns a v2 body rather than `304`.

```ts
expect(V2_LESSON_MANIFEST_VERSION).toBe('teebot-lesson-renderer.v2');
expect(() => assertManifestServableByCapabilities(V2_LESSON_MANIFEST_VERSION, [LESSON_MANIFEST_VERSION])).toThrow();
expect(v2.checksum).not.toBe(v1.checksum);
expect(v2.etag).not.toBe(v1.etag);
```

- [ ] **Step 2: Verify RED**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && npm test -- src/lessons/lesson-manifest.logic.spec.ts src/lessons/lesson-manifest.checksum-parity.spec.ts src/lessons/lesson-manifest.serve-gate.spec.ts src/lessons/lesson-manifest.controller.cache.spec.ts src/lessons/lesson-manifest.controller.validation.spec.ts src/lessons/renderer-v2-backfill.spec.ts
```

Expected: FAIL because the v2 identity, v2 identity projection, migration, and backfill do not exist.

- [ ] **Step 3: Implement one authoritative v2 identity**

Add `V2_LESSON_MANIFEST_VERSION = 'teebot-lesson-renderer.v2'`, include it in `SUPPORTED_RENDERER_VERSIONS`, and keep `DEFAULT_RENDERER_CAPABILITIES` v1-only. `buildManifest()` emits `openingEntrance` only when `lesson.manifest_version` is the exact v2 token. Do not emit numeric `rendererVersion`.

- [ ] **Step 4: Make the immutable identity and ETag change with the response**

Add the normalized `openingEntrance`, physical-motion ownership, and visual-state contract version to `buildIdentityProjection()` for v2. Keep the v1 projection byte-identical. Continue deriving the strong ETag from the persisted checksum, but reject a resolved row when its recomputed identity checksum differs from the stored lesson or bundle checksum.

- [ ] **Step 5: Add migration and transactional backfill**

Migration `111_renderer_v2_contract.sql` follows parent-progress migration `110`, inserts all nine built-in step types for renderer v2 into `render_step_types`, and relies on the existing lexical migration discovery without changing `scripts/migrate.js`; its down migration removes only v2 rows. `migrate-renderer-v2-manifests.mjs` accepts `DATABASE_URL`, runs in one transaction, selects the 26 curriculum lessons by their existing curriculum course membership, builds the v2 identity through the shared canonical checksum module, updates `lessons.manifest_version`, `lessons.manifest_checksum`, and matching `asset_bundles.manifest_checksum`, then re-reads and verifies exactly 26 internally consistent rows before commit. Any count/checksum mismatch rolls back and exits non-zero.

- [ ] **Step 6: Verify GREEN and dry-run backfill**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && npm test -- src/lessons/lesson-manifest.logic.spec.ts src/lessons/lesson-manifest.checksum-parity.spec.ts src/lessons/lesson-manifest.serve-gate.spec.ts src/lessons/lesson-manifest.controller.cache.spec.ts src/lessons/lesson-manifest.controller.validation.spec.ts src/lessons/renderer-v2-backfill.spec.ts && npm run typecheck
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && node scripts/migrate-renderer-v2-manifests.mjs --fixture src/lessons/fixtures/renderer-v2-backfill.json --dry-run
```

Expected: PASS; dry-run reports `candidateCount=26`, `updatedCount=0`, `checksumMismatches=0`.

- [ ] **Step 7: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && git add src/lessons/lesson.constants.ts src/lessons/lesson-manifest.logic.ts src/lessons/lesson-manifest.service.ts src/lessons/lesson-manifest.logic.spec.ts src/lessons/lesson-manifest.checksum-parity.spec.ts src/lessons/lesson-manifest.serve-gate.spec.ts src/lessons/lesson-manifest.controller.cache.spec.ts src/lessons/lesson-manifest.controller.validation.spec.ts src/lessons/renderer-v2-backfill.spec.ts src/database/migrations/111_renderer_v2_contract.sql src/database/migrations/111_renderer_v2_contract.down.sql scripts/migrate-renderer-v2-manifests.mjs && git commit -m "feat(lessons): freeze renderer v2 identity"
```

### Task 2: Validate Pinned Entrance Assets and Audit All Curriculum Manifests

**Files:**
- Modify: `src/lessons/lesson-manifest.logic.ts`
- Modify: `src/lessons/lesson.constants.ts`
- Test: `src/lessons/lesson-manifest.logic.validation.spec.ts`
- Create: `scripts/verify-renderer-v2-manifests.mjs`

- [ ] **Step 1: Write failing validation cases**

Cover duplicate opening entrance, opening on a non-first step, unsupported phase/layout/motion, background identity mismatch, robot-overlay identity mismatch, missing source/SHA/version, missing motion slot, and any v2 manifest whose identity projection omits opening or motion-owner fields.

- [ ] **Step 2: Verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && npm test -- src/lessons/lesson-manifest.logic.validation.spec.ts
```

Expected: FAIL once for each unsupported contract.

- [ ] **Step 3: Implement strict validation and deterministic audit output**

Use named codes `opening-entrance-count`, `opening-entrance-position`, `opening-asset-identity`, `opening-layout-unsupported`, and `motion-preset-unsupported`. The audit script accepts `TBOT_ADMIN_PROXY_KEY`, parent JWT, or device JWT; requests full v2 manifests with `X-Renderer-Capabilities: teebot-lesson-renderer.v2`; and prints one JSON line per lesson containing lesson ID, manifest version, checksum, ETag, opening count, and missing motion slots.

- [ ] **Step 4: Verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && npm test -- src/lessons/lesson-manifest.logic.validation.spec.ts && npm run typecheck
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && node scripts/verify-renderer-v2-manifests.mjs --fixture src/lessons/fixtures/renderer-v2-backfill.json
```

Expected: PASS with `lessonCount=26`, `invalidCount=0`; an intentionally incomplete fixture exits non-zero.

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && git add src/lessons/lesson.constants.ts src/lessons/lesson-manifest.logic.ts src/lessons/lesson-manifest.logic.validation.spec.ts scripts/verify-renderer-v2-manifests.mjs && git commit -m "feat(lessons): validate renderer v2 assets and motion"
```

### Task 3: Add Default-Off Server Negotiation and Per-Sequence Visual ACK Waiters

**Files:**
- Modify: `main/tbot-server/config/config_loader.py`
- Modify: `main/tbot-server/config/manage_api_client.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Test: `main/tbot-server/tests/test_lesson_runtime.py`
- Test: `main/tbot-server/tests/test_lesson_runtime_branch_gaps.py`
- Test: `main/tbot-server/tests/test_lesson_rollout_controls.py`
- Test: `main/tbot-server/tests/test_config_loader_lesson_env_overrides.py`

- [ ] **Step 1: Write failing rollout and waiter tests**

Add `LESSON_RENDERER_V2_ENABLED`, parsed into `lesson.renderer_v2_enabled`, defaulting to `false`. Enabling requires exactly one normalized device identifier in `lesson.rollout_device_allowlist`. Assert no v2 manifest request, start field, or visual frame is emitted when the flag is absent, false, the device is not allowlisted, or firmware does not advertise the exact v2 token.

Test independent waiters for sequences 12 and 13: ACK 13 must resolve only waiter 13; ACK 12 must later resolve waiter 12. Cover timeout, retry, negative ACK, duplicate ACK, wrong assignment/session/step/version/generation, pause, stop, replacement, disconnect, and runtime close.

- [ ] **Step 2: Verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && pytest -q main/tbot-server/tests/test_lesson_runtime.py main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_lesson_rollout_controls.py main/tbot-server/tests/test_config_loader_lesson_env_overrides.py
```

Expected: FAIL because the v2 flag and sequence-keyed waiter registry do not exist.

- [ ] **Step 3: Implement exact capability negotiation**

Parse `features.renderer` as a string-or-list set for backward compatibility. Request a v2 manifest only when all three gates are true: exact v2 capability, `renderer_v2_enabled=true`, and exact allowlist match. Otherwise request and emit v1 only. Include `openingEntrance` and v2 runtime controls in `lesson_start.body` only after those gates pass.

- [ ] **Step 4: Implement per-sequence waiter ownership**

Use `_visual_ack_waiters: dict[int, Future[VisualAckResult]]` and `_visual_ack_timeout_tasks: dict[int, Task]`. Register the future before sending, resolve only from a matching `body.acks`, validate the frozen ACK schema, and remove/cancel that sequence in `finally`. A visual timeout retries once with a new outbound sequence and the same visual generation; after the second timeout return a rejected result and fail the transition without cancelling unrelated waiters. Lifecycle frame timeout behavior remains unchanged.

- [ ] **Step 5: Implement deterministic teardown**

Pause cancels visual waiters and keeps the current step uncompleted. Stop/error/disconnect/replacement/close cancels all waiters, increments the server visual generation, and prevents motion dispatch from any resolved-but-stale coroutine. Resume does not replay the opening entrance: firmware restores the last arrived/fallback state and the server resends the current step visual state with a new sequence and generation.

- [ ] **Step 6: Verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && pytest -q main/tbot-server/tests/test_lesson_runtime.py main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_lesson_rollout_controls.py main/tbot-server/tests/test_config_loader_lesson_env_overrides.py
```

Expected: PASS; v1 negative tests observe no v2 query/header/body/frame.

- [ ] **Step 7: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && git add main/tbot-server/config/config_loader.py main/tbot-server/config/manage_api_client.py main/tbot-server/core/lesson/runtime.py main/tbot-server/tests/test_lesson_runtime.py main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_lesson_rollout_controls.py main/tbot-server/tests/test_config_loader_lesson_env_overrides.py && git commit -m "feat(server): gate renderer v2 and correlate visual acks"
```

### Task 4: Add Firmware V2 Capability, Parser, and Async Queue Ownership

**Files:**
- Modify: `main/protocols/websocket_protocol.cc`
- Modify: `main/lesson_handler.h`
- Modify: `main/lesson_handler.cc`
- Modify: `main/application.h`
- Modify: `main/application.cc`
- Test: `tests/native/lesson_handler_host_test.cc`
- Test: `tests/native/lesson_tvideo_template_host_test.cc`
- Test: `tests/native/lesson_transport_epoch_gate_host_test.cc`

- [ ] **Step 1: Write failing capability, parser, and queue tests**

Assert hello advertises both exact renderer tokens and the structured v2 features. Assert malformed v2 start/visual contracts fail closed. Add queue tests proving completion and timeout items carry transport epoch, visual generation, server sequence, session identity, and completion result without owning a dangling `cJSON*` or frame payload.

- [ ] **Step 2: Verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_handler_test.sh && ./scripts/run_host_native_lesson_tvideo_template_test.sh && ./scripts/run_host_native_lesson_transport_epoch_gate_test.sh
```

Expected: FAIL on absent v2 capability, fields, queue item variants, and transport-epoch rejection.

- [ ] **Step 3: Implement parser and session state**

Move renderer constants to `lesson_handler.h` as `kLessonRendererV1` and `kLessonRendererV2`. Add `opening_entrance_consumed`, `entrance_active`, `visual_generation`, current transport epoch, pending server sequence, pending step identity, and pending ACK state. V1 parsing and fixtures remain byte-identical.

- [ ] **Step 4: Implement typed lesson-worker events**

Extend `LessonQueueItemKind` with `kVisualCompleted` and `kVisualTimedOut`. Store completion data by value in `LessonQueueItem`; only `kFrame` owns/frees `payload`. LVGL/main-task callbacks enqueue typed items, and `LessonMessageTask` accepts them only when both `LessonTransportEpochGate::WorkerAcceptFrame()` and the current visual generation match. `kAbandonTransport` remains queue-front control and invalidates all older completion events before storage/session teardown.

- [ ] **Step 5: Verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_handler_test.sh && ./scripts/run_host_native_lesson_tvideo_template_test.sh && ./scripts/run_host_native_lesson_transport_epoch_gate_test.sh
```

Expected: PASS without changing renderer-v1 hello/parser fixtures.

- [ ] **Step 6: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && git add main/protocols/websocket_protocol.cc main/lesson_handler.h main/lesson_handler.cc main/application.h main/application.cc tests/native/lesson_handler_host_test.cc tests/native/lesson_tvideo_template_host_test.cc tests/native/lesson_transport_epoch_gate_host_test.cc && git commit -m "feat(firmware): add renderer v2 async protocol ownership"
```

### Task 5: Implement Asynchronous Entrance, Cancellation, and Truthful Fallbacks

**Files:**
- Modify: `main/display/lvgl_display/lvgl_display.h`
- Modify: `main/display/lcd_display.cc`
- Modify: `main/lesson_handler.cc`
- Modify: `main/application.cc`
- Modify: `main/lesson_tvideo_template.cc`
- Test: `tests/native/lesson_handler_host_test.cc`
- Create: `tests/native/lesson_visual_animation_host_test.cc`
- Create: `scripts/run_host_native_lesson_visual_animation_test.sh`

- [ ] **Step 1: Write failing phase, delayed-ACK, and cancellation tests**

Cover entry, land, walk interpolation, arrive, greet, reveal, no ACK before reveal/fallback, duplicate start without replay, later-step static arrive, and cancellation during every phase. Cover prepare, pause, stop, error, disconnect, transport replacement, lesson replacement, timeout, and object destruction racing a completion callback.

- [ ] **Step 2: Verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_visual_animation_test.sh
```

Expected: FAIL because the async display API and completion events do not exist.

- [ ] **Step 3: Implement the display boundary**

Add `StartLessonRobotEntrance(plan, completion)`, `CancelLessonRobotEntrance()`, and `ApplyLessonVisualState(state, completion)`. LVGL callbacks update only object transforms and visibility. They do not decode assets, access network/session state, send ACKs, allocate memory per frame, or retain raw lesson-handler pointers.

- [ ] **Step 4: Implement generation-gated completion and ACK ordering**

Start through the Application/LVGL task and immediately return the lesson worker to its queue. Completion enqueues a typed event. The lesson worker revalidates transport epoch, session, step, server sequence, and visual generation; reveals content; records fallback telemetry; then sends exactly one ACK. Pause/stop ACKs are sent only after invalidating the pending visual generation, so an interrupted visual ACK can never appear after the control ACK.

- [ ] **Step 5: Implement precise fallback behavior**

- `missingOverlay`: clear/hide the overlay, retain background/object/word/caption, reveal content, ACK accepted and degraded; do not claim an arrived overlay pose.
- `insufficientHeap`: skip animation and optional overlay decode, retain already verified background/object layers, reveal content, ACK accepted and degraded.
- `animationStartFailed`, `phaseTimeout`, or `reducedMotion`: snap an available overlay to arrived pose, reveal content, ACK accepted and degraded.
- `unsupportedContract` or `assetIdentityMismatch`: apply the safe static v1-compatible scene when its verified assets exist; otherwise reject with `accepted=false` and the stable reason.

- [ ] **Step 6: Verify cancellation and stale-event rejection**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_visual_animation_test.sh && ./scripts/run_host_native_lesson_handler_test.sh && ./scripts/run_host_native_lesson_coverage.sh
```

Expected: PASS; each request produces at most one ACK, no stale callback revives a layer, and resume restores static arrived/fallback state without replaying the opening.

- [ ] **Step 7: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && git add main/display/lvgl_display/lvgl_display.h main/display/lcd_display.cc main/lesson_handler.cc main/application.cc main/lesson_tvideo_template.cc tests/native/lesson_handler_host_test.cc tests/native/lesson_visual_animation_host_test.cc scripts/run_host_native_lesson_visual_animation_test.sh && git commit -m "feat(firmware): animate and cancel renderer v2 visuals"
```

### Task 6: Orchestrate Visual State Then Exactly-One Physical Motion

**Files:**
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Modify: `main/tbot-server/core/lesson/motion_presets.py`
- Test: `main/tbot-server/tests/test_lesson_safe_speaking_template.py`
- Test: `main/tbot-server/tests/test_lesson_e2e_flow.py`
- Test: `main/tbot-server/tests/test_lesson_runtime_branch_gaps.py`
- Test: `main/tbot-server/tests/test_robot_motion_tools.py`

- [ ] **Step 1: Write failing state-order and exactly-once tests**

Assert v2 emits `lesson_visual_state` for teach, listen, thinking, correct, nearMiss, incorrect, retry, celebrate, and completion. Physical motion dispatch occurs once only after a matching accepted ACK. Timeout, rejected ACK, stale ACK, duplicate ACK, pause, stop, replacement, and cancellation dispatch zero motion. V1 keeps the current `body.motion.present` behavior and receives no visual-state frames.

- [ ] **Step 2: Verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && pytest -q main/tbot-server/tests/test_lesson_safe_speaking_template.py main/tbot-server/tests/test_lesson_e2e_flow.py main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_robot_motion_tools.py
```

Expected: FAIL because result visual frames and the ACK-gated orchestration helper do not exist.

- [ ] **Step 3: Implement one orchestration helper**

```py
async def _apply_visual_then_motion(self, state, overlay_key, preset):
    generation = self._next_visual_generation()
    ack = await self._send_visual_state_and_wait(state, overlay_key, preset, generation)
    if not self._visual_transition_is_current(generation):
        return False
    if ack.accepted and preset:
        await self._dispatch_motion_once(preset, generation)
    return ack.accepted
```

Deduplicate motion by `(assignmentId, sessionId, stepId, visualGeneration, preset)`. Renderer-v2 firmware never executes `body.motion.present`; renderer-v1 retains existing behavior.

- [ ] **Step 4: Verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && pytest -q main/tbot-server/tests/test_lesson_safe_speaking_template.py main/tbot-server/tests/test_lesson_e2e_flow.py main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_robot_motion_tools.py
```

Expected: PASS with one motion for each accepted transition and none for every rejected or stale transition.

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && git add main/tbot-server/core/lesson/runtime.py main/tbot-server/core/lesson/motion_presets.py main/tbot-server/tests/test_lesson_safe_speaking_template.py main/tbot-server/tests/test_lesson_e2e_flow.py main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_robot_motion_tools.py && git commit -m "feat(server): orchestrate renderer v2 visual states"
```

### Task 7: Make Admin Preview Truthful and Restore the Browser Gate

**Files:**
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/src/components/lesson/RobotEspTftProjectionPreview.vue`
- Modify: `main/manager-web/src/components/lesson/robot-preview-projection.js`
- Modify: `main/manager-web/scripts/check-lesson-builder-browser.mjs`
- Modify: `main/manager-web/tests/browser/lesson-builder-main.js`
- Test: `main/manager-web/scripts/check-robot-lesson-preview.mjs`

- [ ] **Step 1: Preserve and extend the RED browser regression**

Require subject/helper/L1 fields, cinematic reference and exact TFT preview visible together, exactly one opening entrance, every visual-state path, the exact manifest identity string, `physicalMotionOwner=server`, capability status, and degraded warnings.

- [ ] **Step 2: Verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web && npm run test:lesson-builder-browser
```

Expected: FAIL because the cinematic conditional hides the TFT preview and v2 state controls are absent.

- [ ] **Step 3: Implement dual preview and compatibility display**

Render cinematic reference and exact TFT projection as separately labeled sections. Derive the human label from `manifestVersion`; do not add numeric renderer identity. Display the static fallback matching each stable degradation reason.

- [ ] **Step 4: Verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web && npm run test:lesson-builder-browser && npm run test:tvideo-template && node scripts/check-robot-lesson-preview.mjs && npm run build
```

Expected: PASS with both previews and all renderer-v2 compatibility fields visible.

- [ ] **Step 5: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && git add main/manager-web/src/views/LessonEditor.vue main/manager-web/src/components/lesson/RobotEspTftProjectionPreview.vue main/manager-web/src/components/lesson/robot-preview-projection.js main/manager-web/scripts/check-lesson-builder-browser.mjs main/manager-web/tests/browser/lesson-builder-main.js main/manager-web/scripts/check-robot-lesson-preview.mjs && git commit -m "feat(admin): show truthful renderer v2 preview"
```

### Task 8: Add Cross-Repository Trace and ACK Parity

**Files:**
- Create: `main/manager-web/tests/fixtures/renderer-v2-manifest.json`
- Create: `main/manager-web/scripts/check-renderer-v2-trace.mjs`
- Create: `tests/native/lesson_renderer_trace_host_test.cc`
- Create: `scripts/run_host_native_lesson_renderer_trace_test.sh`

- [ ] **Step 1: Create one version-pinned fixture and failing trace comparison**

Compare manifest/protocol version, phase name, integer bounds, content visibility, overlay key, visual generation, state name, motion name, ACK accepted/degraded fields, and degradation reason at each boundary.

- [ ] **Step 2: Verify RED independently in each repository**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && node main/manager-web/scripts/check-renderer-v2-trace.mjs
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_renderer_trace_test.sh
```

Expected: both commands exit non-zero until both exporters emit the shared schema.

- [ ] **Step 3: Implement deterministic exporters**

Use integer TFT coordinates and named phase boundaries. Do not compare browser animation frames or wall-clock timing. Both exporters write normalized JSON to stdout; the manager script invokes the firmware trace script with its explicit firmware root and compares the two parsed documents.

- [ ] **Step 4: Verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && FIRMWARE_ROOT=/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware node main/manager-web/scripts/check-renderer-v2-trace.mjs
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_renderer_trace_test.sh
```

Expected: PASS with zero trace differences.

- [ ] **Step 5: Commit separately**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && git add main/manager-web/tests/fixtures/renderer-v2-manifest.json main/manager-web/scripts/check-renderer-v2-trace.mjs && git commit -m "test(admin): add renderer v2 trace parity"
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && git add tests/native/lesson_renderer_trace_host_test.cc scripts/run_host_native_lesson_renderer_trace_test.sh && git commit -m "test(firmware): add renderer v2 trace parity"
```

### Task 9: Add Quantitative Firmware Memory and Soak Gates

**Files:**
- Modify: `tests/native/lesson_visual_animation_host_test.cc`
- Create: `main/lesson_renderer_memory_probe.h`
- Create: `main/lesson_renderer_memory_probe.cc`
- Create: `scripts/run_host_native_lesson_memory_test.sh`
- Create: `docs/evidence/renderer-v2-memory.md`

- [ ] **Step 1: Write failing memory-accounting tests**

Track internal free heap, largest internal block, PSRAM free bytes, live decoded layers, live LVGL animations, animation contexts, and per-frame allocation count. Run 100 start/cancel cycles and 100 completed entrances.

- [ ] **Step 2: Verify RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_memory_test.sh
```

Expected: FAIL because renderer-specific counters and thresholds are absent.

- [ ] **Step 3: Implement measurement without changing rendering ownership**

The probe reads allocator/LVGL state at phase boundaries and exposes test-only counters. It must not allocate from animation callbacks. Production logging is one compact line at start, peak, completion, and cancellation.

- [ ] **Step 4: Enforce numeric pass thresholds**

Host/native and HIL evidence must satisfy all conditions:

- Per-frame dynamic allocations: `0`.
- Live decoded lesson layers after settle: at most `3`.
- Live LVGL animations and animation contexts 500 ms after cancel/complete: `0`.
- Internal-heap loss after 100 completed cycles: at most `4096` bytes.
- PSRAM loss after 100 completed cycles: at most `8192` bytes.
- Largest internal free block after cycles: at least `65536` bytes.
- Minimum internal free heap during entrance: at least `49152` bytes.
- No queue-full, allocation-failed, watchdog, decoder leak, or OOM marker.

- [ ] **Step 5: Verify GREEN**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_memory_test.sh && ./scripts/run_host_native_lesson_visual_animation_test.sh && ./scripts/run_host_native_lesson_coverage.sh
```

Expected: PASS and emit a machine-readable threshold report copied into `docs/evidence/renderer-v2-memory.md` with firmware commit SHA and toolchain version.

- [ ] **Step 6: Commit**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && git add tests/native/lesson_visual_animation_host_test.cc main/lesson_renderer_memory_probe.h main/lesson_renderer_memory_probe.cc scripts/run_host_native_lesson_memory_test.sh docs/evidence/renderer-v2-memory.md && git commit -m "test(firmware): gate renderer v2 memory stability"
```

### Task 10: Build, Flash, HIL, Soak, Roll Back, and Roll Out

**Files:**
- Create: `docs/evidence/renderer-v2-hil.md` in the ESP server repository
- Create: `docs/evidence/renderer-v2-hil.md` in the firmware repository
- Modify only deployment manifests required by the tested images.

- [ ] **Step 1: Run all software gates**

```bash
cd /Users/manhhodinh/Documents/TBOT/tbot-backend && npm test -- src/lessons/lesson-manifest.logic.spec.ts src/lessons/lesson-manifest.logic.validation.spec.ts src/lessons/lesson-manifest.checksum-parity.spec.ts src/lessons/lesson-manifest.serve-gate.spec.ts src/lessons/lesson-manifest.controller.cache.spec.ts src/lessons/renderer-v2-backfill.spec.ts && npm run typecheck && npm run build
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && pytest -q main/tbot-server/tests/test_lesson_runtime.py main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_lesson_rollout_controls.py main/tbot-server/tests/test_lesson_safe_speaking_template.py main/tbot-server/tests/test_lesson_e2e_flow.py main/tbot-server/tests/test_robot_motion_tools.py
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web && npm run test:lesson-studio && npm run test:lesson-builder-browser && npm run build
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && ./scripts/run_host_native_lesson_handler_test.sh && ./scripts/run_host_native_lesson_tvideo_template_test.sh && ./scripts/run_host_native_lesson_transport_epoch_gate_test.sh && ./scripts/run_host_native_lesson_visual_animation_test.sh && ./scripts/run_host_native_lesson_renderer_trace_test.sh && ./scripts/run_host_native_lesson_memory_test.sh && source "$HOME/esp/esp-idf/export.sh" && idf.py build
```

Expected: every command passes. Record backend, server, admin, and firmware commit SHAs plus Node, Python, ESP-IDF, compiler, and image identities.

- [ ] **Step 2: Detect and flash the test robot**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && PORT="${TBOT_SERIAL_PORT:-$(ls /dev/cu.usbmodem* | head -n 1)}" && test -n "$PORT" && printf '%s\n' "$PORT" > docs/evidence/renderer-v2-serial-port.txt && idf.py -p "$PORT" flash monitor 2>&1 | tee docs/evidence/renderer-v2-serial.log
```

Expected: one detected port, exact v2 capability, SD mount, production WebSocket, no queue drop, crash, watchdog, or OOM. Record firmware SHA and image digest in both HIL evidence files.

- [ ] **Step 3: Capture objective HIL scenarios**

Use filenames `renderer-v2-<scenario>-<UTC timestamp>.log`, `.json`, and `.mp4`. Capture opening success, every response state, pause during fly, stop during walk, disconnect during greet, transport replacement, SD-first, HTTPS fallback, missing overlay, insufficient heap injection, animation-start failure injection, phase timeout, and network loss after materialization.

Each scenario passes only when:

- Phase boundary timing is within `max(100 ms, 10%)` of the authored duration.
- Exactly one opening occurs per lesson session and zero openings occur after resume.
- Each accepted visual generation produces exactly one ACK and at most one UART motion dispatch.
- Rejected, stale, cancelled, or timed-out generations produce zero UART motion dispatches.
- Pause/stop control ACK appears after generation invalidation and no later visual ACK appears for the interrupted generation.
- Queue drops, stale callbacks applied, watchdogs, crashes, and container restarts are all `0`.
- Network reconnect completes within `30 s` and does not replay the entrance.
- All Task 9 heap/PSRAM thresholds hold on hardware.

- [ ] **Step 4: Run the 60-minute mixed-fleet soak**

Keep one allowlisted v2 robot and at least one v1-only fixture/device active. Sample heap and queue metrics every 60 seconds. Pass only with reconnect count `<= 2`, container restarts `0`, queue drops `0`, duplicate motion count `0`, v1 visual-state frame count `0`, and no regression in AFE/wake-word operation.

- [ ] **Step 5: Prove rollback before rollout**

Disable `LESSON_RENDERER_V2_ENABLED`, remove the test robot from the allowlist, reconnect it, and verify the server requests/serves v1 only and emits no `lesson_visual_state`. Roll back the firmware image to the recorded prior SHA and rerun prepare/start/step/stop smoke. Record commands, timestamps, image digests, and pass/fail output.

- [ ] **Step 6: Deploy capability-gated canary**

Deploy server/admin with v2 still globally disabled. Enable it for exactly one test robot through `LESSON_RENDERER_V2_ENABLED=true` and a one-device allowlist. Expand the allowlist only after software gates, HIL, 60-minute soak, mixed-fleet v1 evidence, and rollback evidence all pass.

- [ ] **Step 7: Commit evidence separately in each repository**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server && git add docs/evidence/renderer-v2-hil.md && git commit -m "docs: record renderer v2 server HIL evidence"
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware && git add docs/evidence/renderer-v2-hil.md docs/evidence/renderer-v2-serial.log docs/evidence/renderer-v2-serial-port.txt && git commit -m "docs: record renderer v2 firmware HIL evidence"
```
