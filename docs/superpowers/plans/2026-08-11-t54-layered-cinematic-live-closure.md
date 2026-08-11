# T5.4 Layered Cinematic Live Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship and physically prove a mixed-media three-layer lesson renderer with complete Robot effects, nine-of-nine progress telemetry, and Android progress for the robot-bound child, then close T5.4 through its Ship checklist.

**Architecture:** Add an exact renderer-v5 contract rather than changing v3 or v4 semantics. Firmware reuses the Cinema clock/compositor and decodes two static image layers once while decoding only the Robot MJPEG stream per frame; ESP and backend preserve the typed manifest and SD-pack identity. Progress fixes remain independent: ESP emits one terminal event for passive steps, and mobile switches the household progress context only after a successful assignment.

**Tech Stack:** Python 3.11/pytest, C++17/ESP-IDF/native host tests, NestJS/TypeScript/Jest, React Native/TypeScript/Jest, PostgreSQL lesson APIs, Docker Compose VPS deployment, physical ESP32-S3 robot and Android device.

---

## File Map

- ESP contract: `main/tbot-server/core/lesson/layered_cinematic_contract.py`
- ESP runtime/capability routing: `main/tbot-server/core/lesson/runtime.py`
- ESP SD materialization: `main/tbot-server/core/lesson/sd_pack_materializer.py`
- ESP tests: `main/tbot-server/tests/test_layered_cinematic_contract.py`, `test_lesson_runtime_branch_gaps.py`, `test_lesson_runtime.py`, `test_lesson_sd_pack_materializer.py`
- Firmware renderer: `main/lesson_layered_cinematic_renderer.h`, `main/lesson_layered_cinematic_renderer.cc`
- Firmware handler/capability: `main/lesson_handler.cc`, `main/CMakeLists.txt`
- Firmware tests: `tests/native/lesson_layered_cinematic_renderer_test.cc`, `tests/native/lesson_handler_host_test.cc`, `tests/test_lesson_dispatch_backward_compat.py`
- Backend contract: `src/lessons/templates/layered-cinematic.contract.ts`
- Backend manifest/generation: `src/lessons/lesson.constants.ts`, `src/lessons/lesson-manifest.logic.ts`, `src/lessons/lesson-asset-generation.repository.ts`
- Backend tests: `src/lessons/templates/layered-cinematic.contract.spec.ts`, `src/lessons/lesson-manifest.logic.validation.spec.ts`
- Mobile context: `src/features/course-library/screens/SendToRobotScreen.tsx`
- Mobile test: `tests/e2e/course-library-flow.test.tsx`
- Product/evidence: `robot/docs/product/lesson-render-contract.md`, `robot/docs/product/progress-telemetry.md`, `robot/docs/TEST_MATRIX.md`, `robot/docs/qa/ad-hoc/2026-08-11-t54-e2e-live.md`, `LESSON_PRODUCTION_PLAN.md`, `lesson-prod/t54-e2e-live.md`

### Task 1: Create isolated implementation worktrees and record baselines

**Files:**
- No product files modified.
- Preserve: `tbot-mobile/src/__env__.ts`

- [ ] **Step 1: Create one worktree per clean owning repository**

```bash
git -C robot/esp32-server worktree add robot/esp32-server/.worktrees/t54-layered-cinematic -b lesson-prod/t54-layered-cinematic main
git -C robot/TBOT-Firmware worktree add robot/TBOT-Firmware/.worktrees/t54-layered-cinematic -b lesson-prod/t54-layered-cinematic main
git -C tbot-backend worktree add tbot-backend/.worktrees/t54-layered-cinematic -b lesson-prod/t54-layered-cinematic main
git -C tbot-mobile worktree add tbot-mobile/.worktrees/t54-progress-child -b lesson-prod/t54-progress-child main
```

Expected: four clean worktrees; canonical mobile retains its existing `src/__env__.ts` modification.

- [ ] **Step 2: Run focused clean baselines**

```bash
cd robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server
.venv311/bin/python -m pytest tests/test_lesson_runtime.py tests/test_lesson_runtime_branch_gaps.py tests/test_lesson_sd_pack_materializer.py -q

cd ../../../../TBOT-Firmware/.worktrees/t54-layered-cinematic
python3 -m pytest tests/test_lesson_dispatch_backward_compat.py -q

cd ../../../tbot-backend/.worktrees/t54-layered-cinematic
npm test -- --runInBand src/lessons/lesson-manifest.logic.validation.spec.ts

cd ../../tbot-mobile/.worktrees/t54-progress-child
npm test -- --runInBand tests/e2e/course-library-flow.test.tsx
```

Expected: record exact pass/fail counts before any production edit. Any new failure not present on main is routed before continuing.

### Task 2: Add the strict ESP renderer-v5 contract

**Files:**
- Create: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/core/lesson/layered_cinematic_contract.py`
- Create: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/tests/test_layered_cinematic_contract.py`

- [ ] **Step 1: Write RED tests for the exact three-slot contract**

Add tests that construct a valid phase and assert normalization returns:

```python
{
    "protocolVersion": "teebot-lesson-renderer.v5",
    "templateId": "layeredCinematic",
    "templateVersion": 1,
    "phaseId": "teach",
    "durationMs": 1000,
    "fps": 10,
    "frameCount": 10,
    "playbackMode": "once",
    "layers": [background_jpeg, teaching_object_png, robot_mjpeg_mp4],
}
```

Add parametrized rejection tests for wrong order, duplicate slot, JPEG object, PNG background, still Robot, audio, non-MJPEG codec, invalid rect, invalid chroma key, path traversal, missing checksum, and `durationMs != frameCount * 1000 / fps` outside the existing timing tolerance.

- [ ] **Step 2: Run the tests and observe RED**

```bash
.venv311/bin/python -m pytest tests/test_layered_cinematic_contract.py -q
```

Expected: import failure for `core.lesson.layered_cinematic_contract`.

- [ ] **Step 3: Implement the minimal parser**

Create constants and one public parser:

```python
RENDERER_V5 = "teebot-lesson-renderer.v5"
TEMPLATE_ID = "layeredCinematic"
SLOT_ORDER = ("backgroundScene", "teachingObject", "robotOverlay")

def normalize_layered_cinematic_phase(raw: object) -> dict[str, object]:
    """Return an exact normalized v5 phase or raise LayeredCinematicContractError."""
```

Use the existing cinematic exact-key, integer, rect, SD-path, SHA-256, and chroma validation helpers where available. Keep limits identical to the production Cinema renderer unless the firmware constant is lower.

- [ ] **Step 4: Run contract tests and adjacent parity tests**

```bash
.venv311/bin/python -m pytest tests/test_layered_cinematic_contract.py tests/test_lesson_cinematic_phase_routing.py tests/test_flattened_cinematic_contract.py -q
```

Expected: all pass; v3/v4 tests remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add core/lesson/layered_cinematic_contract.py tests/test_layered_cinematic_contract.py
git commit -m "feat(lesson): validate layered cinematic v5 phases"
```

### Task 3: Negotiate and route renderer v5 in the ESP runtime

**Files:**
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/core/lesson/runtime.py`
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/tests/test_lesson_runtime_branch_gaps.py`

- [ ] **Step 1: Write RED capability and command-routing tests**

Add a firmware hello containing:

```python
{
    "rendererVersions": ["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v5"],
    "features": {
        "lessonRendererV5": {"layeredCinematic": True, "sdAssetPack": True},
    },
}
```

Assert the requested ordered set contains v5 then v1 when rollout is enabled, excludes v5 when either detailed feature is false, accepts a v5 manifest only after strict parsing, and sends `lesson_cinematic_prepare/start/pause/resume/stop` with the v5 phase unchanged.

- [ ] **Step 2: Run the focused test and observe RED**

```bash
.venv311/bin/python -m pytest tests/test_lesson_runtime_branch_gaps.py -k 'v5 or layered' -q
```

Expected: v5 is absent from capability selection or rejected as unsupported.

- [ ] **Step 3: Implement v5 admission and routing**

Add `RENDERER_V5`, `_renderer_v5_advertised()`, `_renderer_v5_enabled()`, strict manifest dispatch to `normalize_layered_cinematic_phase`, and the v5 phase route beside the existing v3/v4 branches. Do not alter the existing meaning or feature requirements of v1-v4.

- [ ] **Step 4: Run the runtime renderer suites**

```bash
.venv311/bin/python -m pytest tests/test_lesson_runtime_branch_gaps.py tests/test_lesson_cinematic_phase_routing.py tests/test_lesson_passive_parity_with_esp.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/lesson/runtime.py tests/test_lesson_runtime_branch_gaps.py
git commit -m "feat(lesson): negotiate layered cinematic renderer v5"
```

### Task 4: Emit passive-step completion exactly once

**Files:**
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/core/lesson/runtime.py`
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/tests/test_lesson_runtime.py`

- [ ] **Step 1: Write RED tests for immediate and dwell completion**

For a passive step, assert one forwarded event with:

```python
{
    "type": "step_completed",
    "sequence": -step_sequence,
    "stepId": step_id,
    "stepType": step_type,
    "result": "success",
    "detail": {"source": "passive_runtime"},
}
```

Cover immediate completion, delayed dwell, stale dwell after next step, duplicate visual-ready callback, reconnect cancellation, and interactive non-regression.

- [ ] **Step 2: Run RED**

```bash
.venv311/bin/python -m pytest tests/test_lesson_runtime.py -k 'passive and completed' -q
```

Expected: passive steps advance but no forwarded `step_completed` exists.

- [ ] **Step 3: Implement one guarded helper**

Add:

```python
def _complete_passive_step(self) -> bool:
    if not self._step_passive or self._step_completed:
        return False
    self._forward({
        "type": "step_completed",
        "sequence": -self._step_seq if isinstance(self._step_seq, int) else None,
        "stepId": self._step_id,
        "stepType": self._step.get("type") if self._step else None,
        "result": "success",
        "detail": {"source": "passive_runtime"},
    })
    self._step_completed = True
    return True
```

Call it only at the two existing passive completion points: after prompt/dwell-zero and after the still-current dwell timer. Keep current liveness/step guards ahead of the helper.

- [ ] **Step 4: Run the full runtime suite**

```bash
.venv311/bin/python -m pytest tests/test_lesson_runtime.py tests/test_lesson_runtime_state_machine_t21.py tests/test_lesson_runtime_branch_gaps.py -q
```

Expected: all pass and event count is exactly one per passive step.

- [ ] **Step 5: Commit**

```bash
git add core/lesson/runtime.py tests/test_lesson_runtime.py
git commit -m "fix(lesson): report passive step completion"
```

### Task 5: Materialize v5 mixed-media packs without weakening replay safety

**Files:**
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/core/lesson/sd_pack_materializer.py`
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/tests/test_lesson_sd_pack_materializer.py`

- [ ] **Step 1: Capture the production replay projection and write RED tests**

Represent both the historical digest-map manifest and incoming rich v5 assets in fixtures. Assert identical key-to-digest projections replay, changed digest returns `mismatch`, and v5 JPEG/PNG/MP4 assets retain media metadata in the committed rich pack.

- [ ] **Step 2: Run RED**

```bash
.venv311/bin/python -m pytest tests/test_lesson_sd_pack_materializer.py -k 'historical or layered or replay' -q
```

Expected: either the production-shaped equivalent projection is not recognized or v5 metadata is dropped.

- [ ] **Step 3: Implement canonical projection only if the captured digests are equivalent**

Normalize stored and incoming assets to `dict[asset_key, sha256]` using exact asset keys. Return `historical` only when the dictionaries are equal. Preserve `mismatch` for any missing, extra, or changed key. Add v5 metadata to the rich manifest writer without changing checksum inputs.

- [ ] **Step 4: Run materializer and runtime suites**

```bash
.venv311/bin/python -m pytest tests/test_lesson_sd_pack_materializer.py tests/test_lesson_runtime_branch_gaps.py -q
```

Expected: all pass; mismatch rejection tests remain green.

- [ ] **Step 5: Commit**

```bash
git add core/lesson/sd_pack_materializer.py tests/test_lesson_sd_pack_materializer.py
git commit -m "feat(lesson): materialize layered cinematic asset packs"
```

### Task 6: Implement the firmware mixed-media renderer

**Files:**
- Create: `robot/TBOT-Firmware/.worktrees/t54-layered-cinematic/main/lesson_layered_cinematic_renderer.h`
- Create: `robot/TBOT-Firmware/.worktrees/t54-layered-cinematic/main/lesson_layered_cinematic_renderer.cc`
- Create: `robot/TBOT-Firmware/.worktrees/t54-layered-cinematic/tests/native/lesson_layered_cinematic_renderer_test.cc`
- Modify: `robot/TBOT-Firmware/.worktrees/t54-layered-cinematic/main/CMakeLists.txt`

- [ ] **Step 1: Write RED host tests for lifecycle and composition**

Define fake ops that count image decodes, Robot frame reads, composites, presents, closes, and lease releases. Assert prepare decodes each image exactly once, frame zero composites background then object then Robot, later ticks decode only Robot, pause freezes time, resume rebases, stop/cancel release everything, and each injected allocation/decode/read/present failure returns a typed failure.

- [ ] **Step 2: Run the native test target and observe RED**

```bash
python3 tests/run_native_host_tests.py --filter lesson_layered_cinematic_renderer
```

Expected: source/header or target is missing.

- [ ] **Step 3: Implement the renderer with reusable operations**

Create `LessonLayeredCinematicPhaseConfig` with two image configs plus one `LessonCinematicLayerConfig` Robot stream. Create `LessonLayeredCinematicRendererOps` that composes existing Cinema stream operations with bounded `decode_jpeg`, `decode_png_rgba`, `alpha_blend`, `copy_background`, and buffer allocation/release callbacks. Reuse `LessonCinematicRect`, `LessonCinematicError`, response types, timer semantics, and chroma compositor.

- [ ] **Step 4: Run native renderer and existing Cinema suites**

```bash
python3 tests/run_native_host_tests.py --filter 'lesson_layered_cinematic_renderer|lesson_cinematic_renderer|lesson_flattened_cinematic_renderer|lesson_chroma_compositor'
```

Expected: all pass with no changed v3/v4 expectations.

- [ ] **Step 5: Commit**

```bash
git add main/lesson_layered_cinematic_renderer.h main/lesson_layered_cinematic_renderer.cc main/CMakeLists.txt tests/native/lesson_layered_cinematic_renderer_test.cc
git commit -m "feat(lesson): render mixed-media cinematic layers"
```

### Task 7: Wire firmware v5 parsing, capability, ACKs, and production decoders

**Files:**
- Modify: `robot/TBOT-Firmware/.worktrees/t54-layered-cinematic/main/lesson_handler.cc`
- Modify: `robot/TBOT-Firmware/.worktrees/t54-layered-cinematic/tests/native/lesson_handler_host_test.cc`
- Modify: `robot/TBOT-Firmware/.worktrees/t54-layered-cinematic/tests/test_lesson_dispatch_backward_compat.py`

- [ ] **Step 1: Write RED handler tests**

Assert hello advertises `teebot-lesson-renderer.v5` and `lessonRendererV5` only when renderer initialization succeeds. Feed a valid v5 prepare message and assert exact slot parsing, one-time JPEG/PNG setup, frame-zero ACK, phase-ready ACK, start/tick/stop routing, and typed failures for wrong media kind or missing SD path.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_lesson_dispatch_backward_compat.py -k 'v5 or layered' -q
python3 tests/run_native_host_tests.py --filter lesson_handler_host
```

Expected: v5 capability and handler route are absent.

- [ ] **Step 3: Implement handler integration**

Add exact v5 dispatch before the generic unsupported branch, parse three slots into the new phase config, reuse the existing bounded JPEG decoder and `lodepng` path, configure the production renderer, and route lifecycle ACKs through the existing cinematic error namespace.

- [ ] **Step 4: Run firmware host and Python contract suites**

```bash
python3 tests/run_native_host_tests.py --filter 'lesson_handler_host|lesson_layered_cinematic_renderer'
python3 -m pytest tests/test_lesson_dispatch_backward_compat.py tests/test_lesson_cinematic_evidence_renderer_contract.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add main/lesson_handler.cc tests/native/lesson_handler_host_test.cc tests/test_lesson_dispatch_backward_compat.py
git commit -m "feat(lesson): advertise and handle renderer v5"
```

### Task 8: Publish strict v5 manifests from the backend

**Files:**
- Create: `tbot-backend/.worktrees/t54-layered-cinematic/src/lessons/templates/layered-cinematic.contract.ts`
- Create: `tbot-backend/.worktrees/t54-layered-cinematic/src/lessons/templates/layered-cinematic.contract.spec.ts`
- Modify: `tbot-backend/.worktrees/t54-layered-cinematic/src/lessons/lesson.constants.ts`
- Modify: `tbot-backend/.worktrees/t54-layered-cinematic/src/lessons/lesson-manifest.logic.ts`
- Modify: `tbot-backend/.worktrees/t54-layered-cinematic/src/lessons/lesson-manifest.logic.validation.spec.ts`
- Modify: `tbot-backend/.worktrees/t54-layered-cinematic/src/lessons/lesson-asset-generation.repository.ts`

- [ ] **Step 1: Write RED contract and manifest tests**

Create a valid three-layer fixture matching the approved JSON and assert exact normalized output. Add rejection cases matching the ESP parser. Assert manifest version v5 is served only when the device capability set contains v5, and its generation checksum changes when any asset identity, digest, rect, or chroma field changes.

- [ ] **Step 2: Run RED**

```bash
npm test -- --runInBand src/lessons/templates/layered-cinematic.contract.spec.ts src/lessons/lesson-manifest.logic.validation.spec.ts
```

Expected: missing contract and unsupported renderer failures.

- [ ] **Step 3: Implement the exact TypeScript contract**

Export `LAYERED_CINEMATIC_RENDERER = 'teebot-lesson-renderer.v5'`, `LAYERED_CINEMATIC_TEMPLATE = 'layeredCinematic'`, typed layer unions for JPEG background, PNG object, and MJPEG Robot video, plus `assertLayeredCinematicPhase(input: unknown)`. Wire it into publish validation, manifest generation, capability admission, generation persistence, and checksum projection.

- [ ] **Step 4: Run lesson contract suites**

```bash
npm test -- --runInBand src/lessons/templates/layered-cinematic.contract.spec.ts src/lessons/lesson-manifest.logic.validation.spec.ts src/lessons/course-enrollment.v3-playability.spec.ts src/lessons/lesson-assignment.shared-assets.spec.ts
```

Expected: all pass and v3/v4 behavior remains green.

- [ ] **Step 5: Commit**

```bash
git add src/lessons/templates/layered-cinematic.contract.ts src/lessons/templates/layered-cinematic.contract.spec.ts src/lessons/lesson.constants.ts src/lessons/lesson-manifest.logic.ts src/lessons/lesson-manifest.logic.validation.spec.ts src/lessons/lesson-asset-generation.repository.ts
git commit -m "feat(lessons): publish layered cinematic v5 manifests"
```

### Task 9: Keep Android progress on the assigned robot's child

**Files:**
- Modify: `tbot-mobile/.worktrees/t54-progress-child/src/features/course-library/screens/SendToRobotScreen.tsx`
- Modify: `tbot-mobile/.worktrees/t54-progress-child/tests/e2e/course-library-flow.test.tsx`

- [ ] **Step 1: Write RED assignment-context tests**

Render with active child A and a selected robot bound to child B. For successful course assignment, successful lesson assignment, and resumable matching conflict, assert `setActiveChild(B)` occurs before navigation and the invalidated query key is `['lesson-progress', 'child', B]`. Assert offline, failed creation, and unrelated conflict never call `setActiveChild`.

- [ ] **Step 2: Run RED**

```bash
npm test -- --runInBand tests/e2e/course-library-flow.test.tsx
```

Expected: navigation targets child B but household active child remains A.

- [ ] **Step 3: Implement one post-success helper**

Inside `SendToRobotScreen`, add:

```ts
const activateAssignmentChild = React.useCallback((childId: string) => {
  setActiveChild?.(childId);
  void queryClient?.invalidateQueries({ queryKey: ['lesson-progress', 'child', childId] });
}, [queryClient, setActiveChild]);
```

Call it after a new assignment or accepted resume is confirmed and before navigating. Replace duplicated successful-path invalidations; leave failure paths unchanged.

- [ ] **Step 4: Run mobile focused suites**

```bash
npm test -- --runInBand tests/e2e/course-library-flow.test.tsx tests/e2e/parent-settings.test.tsx
npm run typecheck
```

Expected: all pass; no edit to `src/__env__.ts` in the worktree.

- [ ] **Step 5: Commit**

```bash
git add src/features/course-library/screens/SendToRobotScreen.tsx tests/e2e/course-library-flow.test.tsx
git commit -m "fix(progress): activate the robot-bound child after assignment"
```

### Task 10: Build and publish the T5.4 acceptance content

**Files:**
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/core/lesson/runtime.py`
- Modify: `robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server/tests/test_lesson_runtime.py`
- Modify: the published `w02-feelings` lesson through the supported backend authoring/generation API; do not edit production database rows directly.

- [ ] **Step 1: Locate the authoritative content source and capture RED**

```bash
rg -n 'w02-feelings|try từ này|TeeBot will model it|guess: từ này' tbot-backend robot/esp32-server
```

Add runtime tests asserting missing authored vocabulary produces the neutral English fallback `the word`
and never emits literal Vietnamese placeholder text. Add a backend/API read-back assertion that the
acceptance lesson has all seven Robot effect phases, v5 mixed-media assets, and no missing
atlas/degraded route.

- [ ] **Step 2: Run the owning test and observe RED**

```bash
cd robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server
.venv311/bin/python -m pytest tests/test_lesson_runtime.py -k 'target_vocab or placeholder' -q
```

Expected: the runtime currently returns `từ này` when vocabulary metadata is absent.

- [ ] **Step 3: Add verified production assets and metadata**

Change the runtime fallback returned by `_target_vocab_word` from `từ này` to `the word` while
preserving real authored vocabulary. Through the supported backend authoring/generation API,
register high-quality 480x320 JPEG backgrounds, transparent PNG teaching objects, and audio-free
MJPEG MP4 Robot assets for `flyIn`, `walk`, `teach`, `listen`, `thinking`, `celebrate`, and `exit`.
Store immutable IDs, public URLs, byte sizes, SHA-256 values, dimensions, FPS, duration, frame count,
rects, and chroma metadata. Republish `w02-feelings` with real authored target words.

- [ ] **Step 4: Generate, publish, and read back**

Run the repository's existing lesson generation/publish command for `w02-feelings`, then fetch the published manifest through the supported admin/API route with renderer v5 capability. Expected: strict v5 manifest, complete phase vocabulary, exact assets, new generation checksum, no placeholder copy.

- [ ] **Step 5: Commit code/fixture changes; do not commit generated secrets or temporary media**

```bash
cd robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server
git add core/lesson/runtime.py tests/test_lesson_runtime.py
git commit -m "fix(lesson): avoid placeholder vocabulary prompts"
```

Record the backend authoring request, generated lesson version, asset IDs, generation checksum, and
read-back response in the evidence document; these are production data evidence rather than source
files to commit.

### Task 11: Update living contracts and run repository verification

**Files:**
- Modify: `robot/docs/product/lesson-render-contract.md`
- Modify: `robot/docs/product/progress-telemetry.md`
- Modify: `robot/docs/TEST_MATRIX.md`
- Modify: relevant repository evidence files.

- [ ] **Step 1: Document v5 and passive completion semantics**

Add exact capability/template/media requirements, lifecycle/ACK compatibility, image decode-once behavior, Robot effect routing, passive `step_completed` source/dedup semantics, and active-child assignment behavior.

- [ ] **Step 2: Run full suites at branch tips**

```bash
cd robot/esp32-server/.worktrees/t54-layered-cinematic/main/tbot-server && .venv311/bin/python -m pytest -q
cd robot/TBOT-Firmware/.worktrees/t54-layered-cinematic && python3 -m pytest tests -q && python3 tests/run_native_host_tests.py
cd tbot-backend/.worktrees/t54-layered-cinematic && npm run lint && npm run typecheck && npm test -- --runInBand
cd tbot-mobile/.worktrees/t54-progress-child && npm run lint && npm run typecheck && npm test -- --runInBand
```

Expected: all required suites pass, with only already-documented baseline exclusions reproduced against pristine main.

- [ ] **Step 3: Commit documentation and evidence**

Use one docs commit in the repository that owns each document. Record exact commands, counts, commit IDs, and any baseline comparison.

### Task 12: Merge, deploy, flash, and run physical T5.4

**Files:**
- Modify evidence/status files after verification.

- [ ] **Step 1: Rebase each clean branch on latest main and re-run focused suites**

Use non-interactive `git rebase main`. Resolve only task-owned conflicts; preserve unrelated user changes.

- [ ] **Step 2: Merge through the T0.4 gate and push main**

Use `lesson-prod/scripts/merge-task.sh` for each repository supported by the gate. Use merge commits, never squash. Confirm each branch is an ancestor of main after merge.

- [ ] **Step 3: Deploy backend and ESP server**

For backend, monitor the Render deploy and `/v1/health`. For ESP server, run the documented `backup-db.sh`, deploy with `--no-deps` and `TBOT_SERVER_REPLICAS=1`, then `smoke-vps.sh` and MCP-pin checks.

- [ ] **Step 4: Build and flash firmware**

Use the documented firmware build/flash command for the attached ESP32-S3. Reboot and verify hello advertises v5, PSRAM/SD initialize, Wi-Fi connects, and the passive lesson WebSocket opens.

- [ ] **Step 5: Assign the republished lesson from Android without a PIN**

Select the connected robot, assign the lesson directly, and confirm the app switches to the robot-bound child. Do not add or require a PIN gate.

- [ ] **Step 6: Run the live verifier and physical checklist**

```bash
python robot/scripts/lesson_e2e_live_capture.py --container current-tbot-esp32-server-1
bash robot/scripts/tbot_live_e2e_probe.sh
```

Confirm normal-distance trigger recognition, audible prompts, background/object images, every Robot effect, UART motion, nine distinct step completions, assignment `COMPLETED`, Android progress within SLA, power-cycle recovery, clean `lesson_stop`, and no degraded renderer marker. Intentional Wi-Fi kill remains the documented operator-deferred N/A item.

- [ ] **Step 7: Archive evidence**

Copy verifier JSON/logs, serial/server logs, backend read-back, Android screenshots, and operator
video into `robot/docs/evidence/t54-live-20260811-final/`. Update
`robot/docs/qa/ad-hoc/2026-08-11-t54-e2e-live.md` with repro, diffs, deploy identities, physical
observations, and final pass counts.

### Task 13: Run the T5.4 Ship checklist and close status

**Files:**
- Modify: `/Users/manhhodinh/Documents/TBOT/lesson-prod/t54-e2e-live.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`

- [ ] **Step 1: Re-verify merged main from throwaway worktrees**

```bash
bash lesson-prod/scripts/verify-on-main.sh robot/esp32-server -- main/tbot-server/.venv311/bin/python -m pytest -q
bash lesson-prod/scripts/verify-on-main.sh robot/TBOT-Firmware -- python3 -m pytest tests -q
bash lesson-prod/scripts/verify-on-main.sh tbot-backend -- npm test -- --runInBand
bash lesson-prod/scripts/verify-on-main.sh tbot-mobile -- npm test -- --runInBand
```

Expected: required suites pass on merged main. Re-run production smoke because code was deployed.

- [ ] **Step 2: Remove only clean merged task worktrees and branches**

Verify each new task worktree is clean and run:

```bash
git -C robot/esp32-server merge-base --is-ancestor lesson-prod/t54-layered-cinematic main
git -C robot/TBOT-Firmware merge-base --is-ancestor lesson-prod/t54-layered-cinematic main
git -C tbot-backend merge-base --is-ancestor lesson-prod/t54-layered-cinematic main
git -C tbot-mobile merge-base --is-ancestor lesson-prod/t54-progress-child main
```

Then remove those four worktrees and delete their local/remote task branches. Preserve the
previously known dirty/unmerged T5.4 worktrees until separately merged or explicitly retired with
evidence.

- [ ] **Step 3: Set T5.4 DONE**

Change both status locations to `DONE`, link the final evidence directory/document, list merged main commit IDs, deploy identities, physical assignment ID, verifier result, Android progress proof, and deferred Wi-Fi-loss ownership in T7.4.

- [ ] **Step 4: Final consistency check**

```bash
rg -n '^\*\*Status:\*\*|\| T5\.4 \|' lesson-prod/t54-e2e-live.md LESSON_PRODUCTION_PLAN.md
git -C robot/esp32-server status --short --branch
git -C robot/TBOT-Firmware status --short --branch
git -C tbot-backend status --short --branch
git -C tbot-mobile status --short --branch
```

Expected: both status locations say DONE; canonical repositories are on main; the pre-existing mobile `src/__env__.ts` modification remains untouched.
