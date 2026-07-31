# Google Live TVideo Journey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the farm lesson with the complete locked `tvideoJourney.v1` visual vocabulary, editable website authoring, natural Google Live conversation, verified per-cue MJPEG derivatives, and smooth single-stream once/loop playback on the robot.

**Architecture:** Extend the existing renderer-v4 and Google Live stacks rather than creating parallel systems. The backend owns the versioned preset, lesson data, derivative identity, publish gate, and a dedicated pinned Chromium/FFmpeg render-worker image; the ESP server owns conversational lesson authority and attested cue projection; firmware continues to decode one `480x320`, 10 FPS MJPEG MP4 at a time and adds an in-place loop mode. Template version 1 and renderer-v3 remain available for rollback, and rollout stays disabled until attended hardware soak evidence exists.

**Tech Stack:** NestJS/TypeScript/PostgreSQL/Vitest, pinned Chromium/Canvas/FFmpeg/ffprobe, Vue 2, Python/pytest, Google Gemini Live API, ESP-IDF C++ host-native tests.

---

## Repository And File Map

- ESP/admin worktree: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey` on `feature/google-live-tvideo-journey`.
- Backend worktree to create: `/Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey` from `dbcbd4768475633cfc723ab338c5231a45645397`.
- Firmware worktree to create: `/Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey` from `ee125922d63d2f7d93bddca7a52ddffeab599c48`.
- Backend contract: `src/lessons/templates/flattened-mjpeg-cinematic.contract.ts` owns exact template-v1/v2 validation.
- Backend authoring: `src/lessons/tvideo-journey/` owns preset schema, farm data, frame-state evaluation, derivative inputs, and preview DTOs.
- Backend render worker: `src/lessons/derivatives/tvideo-frame-renderer.ts`, `renderer/tvideo-journey/`, and `Dockerfile.render-worker` own deterministic Chromium frames and final FFmpeg encoding.
- Admin: `main/manager-web/src/components/lesson/TVideoJourneyEditor.vue` and focused helpers own path/coaching editing plus four preview modes.
- ESP conversation: `main/tbot-server/core/lesson/conversation_runtime.py` owns objective state, attempts, coaching, bounded context, and progress evidence.
- Existing Google Live extension: `main/tbot-server/core/voice/session_provider/google_live.py` and `plugins_func/functions/lesson_conversation.py` expose allowlisted tools and fence stale turns.
- ESP media: existing flattened cinematic contract, pack, materializer, and runtime files preserve cue identity and playback mode.
- Firmware: `main/lesson_flattened_cinematic_renderer.*` adds template-v2 cue identity and loop behavior without reopening the file or reallocating buffers.
- Validation: farm golden images, deterministic conversation simulations, cross-repository fixtures, credential-gated Live smoke, and attended hardware scripts remain separate gates.

## Non-Negotiable Invariants

- Do not add GIF generation or playback.
- Do not play the three authoring layers concurrently on firmware.
- Do not raise output above 10 FPS.
- Do not persist raw child audio or raw transcripts in progress records.
- Do not let Google Live select filenames, skip steps, or mark mastery directly.
- Do not enable renderer-v4 rollout or claim hardware readiness from software tests.
- Keep renderer-v3 and flattened template-v1 tests green throughout.

### Task 1: Create Cross-Repository Worktrees And Reconfirm Baselines

**Files:**
- Verify: `.gitignore`
- Verify: `main/tbot-server/tests/test_google_live_tool_calls.py`
- Verify: `main/tbot-server/tests/test_flattened_cinematic_contract.py`

- [ ] **Step 1: Verify the current ESP worktree is isolated and ignored**

```bash
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server check-ignore -q .worktrees
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey status --short --branch
```

Expected: exit 0 from `check-ignore`; only this plan is untracked before its commit.

- [ ] **Step 2: Create backend and firmware worktrees from the recorded bases**

```bash
mkdir -p /Users/manhhodinh/Documents/TBOT/.worktrees
git -C /Users/manhhodinh/Documents/TBOT/tbot-backend worktree add \
  /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey \
  -b feature/google-live-tvideo-journey dbcbd4768475633cfc723ab338c5231a45645397
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware worktree add \
  /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey \
  -b feature/google-live-tvideo-journey ee125922d63d2f7d93bddca7a52ddffeab599c48
```

Expected: both worktrees report a clean `feature/google-live-tvideo-journey` branch; the dirty original backend checkout is unchanged.

- [ ] **Step 3: Install only repository-declared dependencies**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey
npm ci --legacy-peer-deps
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/manager-web
npm ci
```

Expected: lockfile-resolved installs finish without modifying tracked lockfiles.

- [ ] **Step 4: Run focused baseline suites**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/tbot-server
pytest -q tests/test_google_live_tool_calls.py tests/test_flattened_cinematic_contract.py
cd /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey
npx vitest run src/lessons/templates/flattened-mjpeg-cinematic.contract.spec.ts \
  src/lessons/derivatives/flattened-cinematic-worker.service.spec.ts
cd /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
```

Expected: all baseline suites pass before feature code changes.

### Task 2: Add Flattened Template Version 2 Without Breaking Version 1

**Files:**
- Modify: `src/lessons/templates/flattened-mjpeg-cinematic.contract.ts`
- Modify: `src/lessons/templates/flattened-mjpeg-cinematic.contract.spec.ts`
- Modify: `src/lessons/lesson-manifest.logic.ts`
- Modify: `src/lessons/lesson-manifest.logic.validation.spec.ts`

- [ ] **Step 1: Write failing exact-contract tests**

Add a reusable fixture and assertions equivalent to:

```ts
const cueV2 = {
  templateId: 'flattenedMjpegCinematic',
  templateVersion: 2,
  cueId: 'word-1-listen',
  effect: 'listen',
  stepKey: 'word-1',
  playbackMode: 'loop',
  timing: { durationMs: 1300 },
  asset: assetFor('word-1-listen', 1300),
};

expect(() => validateFlattenedMjpegCinematicPhases([
  cueV2,
  { ...cueV2, cueId: 'word-2-listen', stepKey: 'word-2', asset: assetFor('word-2-listen', 1300) },
])).not.toThrow();
expect(() => validateFlattenedMjpegCinematicPhases([cueV2, cueV2]))
  .toThrow('cue word-1-listen is duplicated');
expect(() => validateFlattenedMjpegCinematicPhase({ ...cueV2, playbackMode: 'repeat' }))
  .toThrow('playbackMode');
expect(() => validateFlattenedMjpegCinematicPhase(v1Fixture())).not.toThrow();
```

Also reject extra keys, unsafe cue IDs, missing step keys, unknown effects, mismatched asset paths, and template-v1 objects containing v2 fields.

- [ ] **Step 2: Run RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey
npx vitest run src/lessons/templates/flattened-mjpeg-cinematic.contract.spec.ts \
  src/lessons/lesson-manifest.logic.validation.spec.ts
```

Expected: FAIL because template version 2 and cue fields are unsupported.

- [ ] **Step 3: Implement the discriminated v1/v2 contract**

Use these public types and exact allowlists:

```ts
export const FLATTENED_MJPEG_CINEMATIC_TEMPLATE_V1 = 1 as const;
export const FLATTENED_MJPEG_CINEMATIC_TEMPLATE_V2 = 2 as const;
export const TVIDEO_EFFECTS = [
  'opening', 'greet', 'teach', 'listen', 'thinking', 'correct',
  'retry-level-1', 'retry-level-2', 'retry-level-3', 'celebrate', 'word-transition',
] as const;
export type TVideoEffect = (typeof TVIDEO_EFFECTS)[number];
export type PlaybackMode = 'once' | 'loop';

export interface FlattenedMjpegCinematicCueV2 {
  templateId: 'flattenedMjpegCinematic';
  templateVersion: 2;
  cueId: string;
  effect: TVideoEffect;
  stepKey: string;
  playbackMode: PlaybackMode;
  timing: { durationMs: number };
  asset: FlattenedMjpegCinematicAsset;
}
```

Validate `cueId` and `stepKey` with `/^[a-z0-9]+(?:-[a-z0-9]+)*$/`, require unique `cueId`, and derive v2 asset paths as `lessons/derivatives/<derivativeId>/<cueId>.mp4`. Leave v1 phase validation byte-for-byte strict.

- [ ] **Step 4: Run GREEN and rollback regressions**

Run the command from Step 2 plus existing renderer-v3 and renderer-v4 generation suites.

Expected: PASS; v1 fixtures still serialize with `phaseId` and no v2 fields.

- [ ] **Step 5: Commit**

```bash
git add src/lessons/templates/flattened-mjpeg-cinematic.contract.* \
  src/lessons/lesson-manifest.logic.ts src/lessons/lesson-manifest.logic.validation.spec.ts
git commit -m "feat(lessons): add TVideo cue contract v2"
```

### Task 3: Define `tvideoJourney.v1`, Farm Authoring Data, And Deterministic Frame State

**Files:**
- Create: `src/lessons/tvideo-journey/tvideo-journey.types.ts`
- Create: `src/lessons/tvideo-journey/tvideo-journey.contract.ts`
- Create: `src/lessons/tvideo-journey/tvideo-journey.contract.spec.ts`
- Create: `src/lessons/tvideo-journey/tvideo-journey.preset.ts`
- Create: `src/lessons/tvideo-journey/tvideo-journey.frame-state.ts`
- Create: `src/lessons/tvideo-journey/tvideo-journey.frame-state.spec.ts`
- Create: `src/lessons/tvideo-journey/fixtures/farm-golden.ts`

- [ ] **Step 1: Write failing authoring-contract tests**

Define a complete farm fixture with `presetId`, `presetVersion`, source asset version IDs, flight/landing/walk keyframes, teaching/object anchors, safe zone, two word steps, Vietnamese meanings, related concepts, question seeds, expected answer, and pronunciation guidance. Assert missing or extra fields, out-of-stage anchors, non-monotonic walk time, unapproved phonemes, more than two contextual turns, and absent robot clips fail closed.

```ts
expect(validateTVideoJourneyAuthoring(farmJourney)).toEqual(farmJourney);
expect(() => validateTVideoJourneyAuthoring({ ...farmJourney, presetVersion: 2 }))
  .toThrow('unsupported preset');
expect(() => validateTVideoJourneyAuthoring(withWalkTimes(farmJourney, [0, 0.7, 0.6, 1])))
  .toThrow('walk keyframe times must increase');
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run src/lessons/tvideo-journey/tvideo-journey.contract.spec.ts \
  src/lessons/tvideo-journey/tvideo-journey.frame-state.spec.ts
```

Expected: FAIL because the journey modules do not exist.

- [ ] **Step 3: Implement the locked preset and normalized schema**

The preset must be code-owned and immutable to admin edits:

```ts
export const TVIDEO_JOURNEY_V1 = Object.freeze({
  id: 'tvideoJourney', version: 1, width: 480, height: 320, fps: 10,
  confettiSeed: 0x54424f54, confettiPieces: 64,
  effects: Object.freeze({
    listen: { durationMs: 1300, playbackMode: 'loop', cardPulseMs: 1300 },
    correct: { durationMs: 1500, playbackMode: 'once', jumpCount: 2 },
    'word-transition': { durationMs: 900, playbackMode: 'once', outMs: 400, inMs: 550 },
  }),
});
```

Store exact cubic-bezier curves, shadow/puff parameters, object bob, UI safe-zone padding, and all other `panel.html` choreography in this module. The authoring DTO stores only scene path/content/media inputs.

- [ ] **Step 4: Implement pure frame-time evaluation**

Expose one deterministic function:

```ts
export function evaluateTVideoFrame(input: TVideoFrameInput): TVideoFrameState {
  const frameIndex = Math.floor(input.timeMs / 100);
  const timeMs = frameIndex * 100;
  return {
    frameIndex,
    timeMs,
    robot: evaluateRobot(input.effect, timeMs, input.scenePath),
    object: evaluateObject(input.effect, timeMs, input.objectAnchor),
    card: evaluateCard(input.effect, timeMs, input.copy, input.progress),
    confetti: evaluateSeededConfetti(input.effect, timeMs, TVIDEO_JOURNEY_V1.confettiSeed),
  };
}
```

Do not read wall clock, `Math.random`, browser dimensions, network fonts, or animation-frame timestamps.

- [ ] **Step 5: Run GREEN and commit**

```bash
npx vitest run src/lessons/tvideo-journey
git add src/lessons/tvideo-journey
git commit -m "feat(lessons): define deterministic TVideo journey preset"
```

Expected: PASS with stable farm snapshots at flight, landing, walk, teach, listen, correct, confetti, and word transition times.

### Task 4: Add A Pinned Chromium Frame Renderer And Dedicated Worker Image

**Files:**
- Create: `renderer/tvideo-journey/index.html`
- Create: `renderer/tvideo-journey/render-entry.mjs`
- Create: `renderer/tvideo-journey/render-entry.spec.ts`
- Create: `src/lessons/derivatives/tvideo-frame-renderer.ts`
- Create: `src/lessons/derivatives/tvideo-frame-renderer.spec.ts`
- Modify: `src/lessons/derivatives/flattened-cinematic-media.ts`
- Modify: `src/lessons/derivatives/flattened-cinematic-media.spec.ts`
- Create: `Dockerfile.render-worker`
- Create: `scripts/render-worker-entrypoint.mjs`
- Modify: `tests/docker-production-dependencies.spec.ts`

- [ ] **Step 1: Write failing renderer-boundary tests**

Assert a complete snapshot becomes exactly `frameCount` PNG frames at `480x320`, with frame time `frameIndex * 100`, all inputs are local verified files, Chromium argv contains `--disable-network`, `--disable-background-networking`, `--font-render-hinting=none`, and the process uses `shell:false`. Assert abort kills Chromium, removes the temporary frame directory, and never promotes partial output.

- [ ] **Step 2: Run RED**

```bash
npx vitest run renderer/tvideo-journey/render-entry.spec.ts \
  src/lessons/derivatives/tvideo-frame-renderer.spec.ts \
  src/lessons/derivatives/flattened-cinematic-media.spec.ts
```

Expected: FAIL because the frame renderer and image are absent.

- [ ] **Step 3: Implement the Chromium executable boundary**

The Node boundary must launch the pinned binary directly:

```ts
spawn(config.chromiumPath, [
  '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
  '--disable-network', '--disable-background-networking', '--font-render-hinting=none',
  `--user-data-dir=${profileDir}`, `file://${rendererHtml}`,
], { shell: false, stdio: ['ignore', 'pipe', 'pipe'], signal });
```

Use Chrome DevTools Protocol to call `window.renderTVideoFrame(snapshot, frameIndex)`, capture the canvas, and write `frame-%06d.png`. Verify the input descriptor SHA-256 before launch and every output dimension after capture.

- [ ] **Step 4: Keep FFmpeg as the final encoder only**

Change the media builder to consume rendered PNG frames:

```ts
const argv = [
  '-framerate', '10', '-i', `${frameDir}/frame-%06d.png`,
  '-an', '-c:v', 'mjpeg', '-q:v', '3', '-pix_fmt', 'yuvj420p',
  '-movflags', '+faststart', temporaryOutput,
];
```

Retain ffprobe verification for MJPEG, `480x320`, 10 FPS, exact frame count/duration, and no audio.

- [ ] **Step 5: Add the worker-only image**

`Dockerfile.render-worker` must use `node:22-bookworm-slim`, install exact Debian packages `chromium`, `ffmpeg`, `fonts-noto-core`, and `fonts-noto-color-emoji`, copy the same built application, set `TBOT_PROCESS_ROLE=render-worker`, and start `scripts/render-worker-entrypoint.mjs`. The existing `Dockerfile` remains distroless and must not contain Chromium or FFmpeg.

- [ ] **Step 6: Build and smoke the image**

```bash
docker build -f Dockerfile.render-worker -t tbot-render-worker:tvideo-v1 .
docker run --rm tbot-render-worker:tvideo-v1 chromium --version
docker run --rm tbot-render-worker:tvideo-v1 ffmpeg -version
```

Expected: both binaries exist only in the worker image; the production API dependency test still confirms the distroless image contract.

- [ ] **Step 7: Commit**

```bash
git add renderer src/lessons/derivatives Dockerfile.render-worker \
  scripts/render-worker-entrypoint.mjs tests/docker-production-dependencies.spec.ts
git commit -m "feat(lessons): render TVideo cues in pinned Chromium worker"
```

### Task 5: Extend Derivative Identity, Lifecycle, And Publish Gating Per Cue

**Files:**
- Create: `src/database/migrations/117_tvideo_cue_derivatives.sql`
- Create: `src/database/migrations/117_tvideo_cue_derivatives.down.sql`
- Modify: `src/lessons/derivatives/flattened-cinematic-identity.ts`
- Modify: `src/lessons/derivatives/flattened-cinematic-identity.spec.ts`
- Modify: `src/lessons/derivatives/flattened-cinematic-source.ts`
- Modify: `src/lessons/derivatives/flattened-cinematic-worker.service.ts`
- Modify: `src/lessons/derivatives/flattened-cinematic-worker.service.spec.ts`
- Modify: `src/lessons/lesson-asset-generation.repository.ts`
- Modify: `src/lessons/authoring/lesson-authoring.service.ts`
- Modify: `src/lessons/authoring/lesson-authoring.flattened-derivatives.spec.ts`

- [ ] **Step 1: Write failing cue identity tests**

Assert identity changes for preset build, cue ID, effect, playback mode, duration, lesson/step revision, every asset UUID/SHA/byte count, scene path, anchors, safe zone, object fit, chroma, word/copy/progress/coaching UI, and pinned font hash. Assert object key order does not change identity.

```ts
expect(identity({ ...base, cueId: 'word-1-listen' }))
  .not.toBe(identity({ ...base, cueId: 'word-2-listen' }));
expect(identity(reordered(base))).toBe(identity(base));
```

- [ ] **Step 2: Run RED**

```bash
npx vitest run src/lessons/derivatives/flattened-cinematic-identity.spec.ts \
  src/lessons/derivatives/flattened-cinematic-worker.service.spec.ts \
  src/lessons/authoring/lesson-authoring.flattened-derivatives.spec.ts
```

Expected: FAIL because persistence and source descriptors are phase-oriented.

- [ ] **Step 3: Introduce a versioned cue identity DTO**

```ts
export interface TVideoDerivativeIdentityInput {
  identityVersion: 2;
  rendererBuildSha256: string;
  presetId: 'tvideoJourney';
  presetVersion: 1;
  cueId: string;
  effect: TVideoEffect;
  stepKey: string;
  playbackMode: 'once' | 'loop';
  durationMs: number;
  lessonId: string;
  lessonVersion: number;
  sourceRevision: number;
  sources: readonly MaterializedSourceIdentity[];
  scene: TVideoScenePath;
  content: TVideoCueContent;
  fontSha256: string;
}
```

Persist the semantic phase column as a compatibility label but key all current v2 operations by `cue_id`. Migration columns must be nullable only for existing v1 rows and use a check constraint that requires v2 fields together.

- [ ] **Step 4: Wire lifecycle and fail-closed publish**

Request every required cue transactionally, lease one job per worker, preserve prior verified output on failure, and publish only when the exact cue-ID set for the current source revision is ready. Re-check identity under the publish transaction lock before building the v2 manifest.

- [ ] **Step 5: Run GREEN and commit**

```bash
npx vitest run src/lessons/derivatives src/lessons/authoring/lesson-authoring.flattened-derivatives.spec.ts
git add src/database/migrations src/lessons/derivatives src/lessons/authoring \
  src/lessons/lesson-asset-generation.repository.ts
git commit -m "feat(lessons): build and publish current TVideo cues"
```

Expected: v2 repeated effects publish under unique cue IDs; v1 phase generation remains green.

### Task 6: Expose Farm Journey Authoring And Four Admin Preview Modes

**Files:**
- Modify: `src/lessons/authoring/lesson-authoring.controller.ts`
- Modify: `src/lessons/authoring/lesson-authoring.dto.ts`
- Create: `src/lessons/authoring/lesson-authoring.tvideo-journey.spec.ts`
- Create: `main/manager-web/src/components/lesson/tvideo-journey.js`
- Create: `main/manager-web/src/components/lesson/TVideoJourneyEditor.vue`
- Create: `main/manager-web/src/components/lesson/TVideoConversationPreview.vue`
- Modify: `main/manager-web/src/views/LessonEditor.vue`
- Modify: `main/manager-web/src/i18n/en.js`
- Modify: `main/manager-web/src/i18n/vi.js`
- Create: `main/manager-web/scripts/check-tvideo-journey-editor.mjs`
- Modify: `main/manager-web/package.json`

- [ ] **Step 1: Write failing API and UI contract tests**

Cover exact save/load roundtrip for farm path, anchors, media IDs, two steps, Vietnamese meanings, related concepts, question seeds, slow model, segments/phonemes, Vietnamese-L1 guidance, and derivative statuses. UI source assertions must require tabs named `3 Sources`, `Journey Path`, `Conversation`, and `Robot Flattened`, plus branch selectors for target, meaning, related, silence, uncertain, and coaching levels 1-3.

- [ ] **Step 2: Run RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey
npx vitest run src/lessons/authoring/lesson-authoring.tvideo-journey.spec.ts
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/manager-web
node scripts/check-tvideo-journey-editor.mjs
```

Expected: both fail because authoring fields and components are absent.

- [ ] **Step 3: Implement exact backend DTOs and preset endpoint**

Add `GET /lessons/authoring/presets/tvideoJourney/1` returning the locked preset plus build hash, and extend lesson draft save/load with `tvideoJourney`. Reject arbitrary effect parameters and any preset other than version 1.

- [ ] **Step 4: Implement the admin editor**

Use normalized percentage coordinates over a fixed `480x320` preview. Journey path editing may change only ingress, landing, walk points, teaching/object anchors, and safe zone. Conversation preview must call a pure branch simulator and show the resulting question, bridge/coaching intent, cue ID/effect, and progress outcome without calling Google Live.

```js
export const CONVERSATION_BRANCHES = Object.freeze([
  'target', 'meaning_vi', 'related', 'silence', 'uncertain',
  'retry_level_1', 'retry_level_2', 'retry_level_3',
]);
```

The Robot Flattened preview loads the same preset payload/build hash used by the backend and advances only on 100 ms frame boundaries.

- [ ] **Step 5: Test and commit the backend authoring API**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey
npx vitest run src/lessons/authoring/lesson-authoring.tvideo-journey.spec.ts
git add src/lessons/authoring src/lessons/tvideo-journey
git commit -m "feat(lessons): expose TVideo journey authoring"
```

Expected: exact DTO and preset endpoint tests pass.

- [ ] **Step 6: Build, test, and commit the admin editor**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/manager-web
node scripts/check-tvideo-journey-editor.mjs
npm run build
git add src/components/lesson src/views/LessonEditor.vue src/i18n/en.js src/i18n/vi.js \
  scripts package.json
git commit -m "feat(admin): author and preview TVideo lesson journeys"
```

Expected: focused admin script and production build pass; publish button remains disabled for stale/missing cues.

### Task 7: Add The Authoritative Conversational Lesson State Machine

**Files:**
- Create: `main/tbot-server/core/lesson/conversation_runtime.py`
- Create: `main/tbot-server/core/lesson/conversation_contract.py`
- Create: `main/tbot-server/tests/test_lesson_conversation_runtime.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`

- [ ] **Step 1: Write failing deterministic state tests**

Test English target, Vietnamese meaning then bridge, related concept then bridge, silence, uncertain recognition, success at coaching levels 1-3, three unsuccessful attempts, one/two off-topic turns, forced return on the third, interruption during model speech/reaction, duplicate calls, reordered calls, cross-session calls, and cross-attempt calls.

```py
runtime = LessonConversationRuntime(farm_step(), session_id="lesson-s1")
first = runtime.open_attempt()
assert first.cue_id == "word-1-listen"
bridge = runtime.child_response(identity(first), response_class="meaning_vi")
assert bridge.next_intent == "bridge_to_english"
assert bridge.mastery is False
```

- [ ] **Step 2: Run RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/tbot-server
pytest -q tests/test_lesson_conversation_runtime.py
```

Expected: FAIL because the conversation runtime does not exist.

- [ ] **Step 3: Implement identities and legal transitions**

```py
@dataclass(frozen=True)
class LessonToolIdentity:
    lesson_session_id: str
    turn_sequence_id: int
    attempt_id: str
    step_key: str
    cue_id: str | None = None

class ConversationState(StrEnum):
    ASKING = "asking"
    LISTENING = "listening"
    BRIDGING = "bridging"
    COACHING = "coaching"
    REACTING = "reacting"
    COMPLETE = "complete"
```

The runtime owns the current target, attempt, coaching level, contextual turn count, cue allowlist, and progress outcome. Reject stale/mismatched identities with stable codes and no mutation.

- [ ] **Step 4: Implement guided elicitation and gentle coaching**

Use deterministic intents, not fixed child-facing sentences: `scene_question`, `bridge_vietnamese`, `bridge_related`, `narrow_question`, `slow_model`, `segment_model`, `praise_effort_continue`. Level 3 failure records `attempted`, never `mastered`, and schedules review.

- [ ] **Step 5: Integrate with the existing LessonRuntime and commit**

Create the conversation runtime only for validated v2 TVideo steps. Route accepted semantic transitions through existing cinematic command fencing and progress forwarding; do not duplicate lesson session ownership.

```bash
pytest -q tests/test_lesson_conversation_runtime.py tests/test_flattened_cinematic_contract.py
git add main/tbot-server/core/lesson main/tbot-server/tests/test_lesson_conversation_runtime.py
git commit -m "feat(lesson): add guided conversational learning runtime"
```

### Task 8: Extend Existing Google Live Tools And Turn Fencing

**Files:**
- Create: `main/tbot-server/plugins_func/functions/lesson_conversation.py`
- Modify: `main/tbot-server/core/voice/google_live/interaction_controller.py`
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`
- Modify: `main/tbot-server/tests/test_google_live_tool_calls.py`
- Create: `main/tbot-server/tests/test_google_live_lesson_conversation.py`

- [ ] **Step 1: Write failing tool-schema and stale-event tests**

Assert exact declarations for `lesson_child_response`, `lesson_pronunciation_outcome`, `lesson_context_turn`, `lesson_visual_reaction`, and `lesson_continue`. Every schema requires `lessonSessionId`, `turnSequenceId`, `attemptId`, and `stepKey`; visual reaction additionally requires `cueId`. Reject additional properties.

- [ ] **Step 2: Run RED**

```bash
pytest -q tests/test_google_live_tool_calls.py tests/test_google_live_lesson_conversation.py
```

Expected: FAIL because lesson tools and lesson-bound turn identity are absent.

- [ ] **Step 3: Extend the existing interaction controller**

Add optional lesson identity to its snapshots rather than creating another controller:

```py
def bind_lesson_attempt(self, *, lesson_session_id, attempt_id, step_key):
    self.lesson_session_id = lesson_session_id
    self.attempt_id = attempt_id
    self.step_key = step_key
    self.turn_sequence_id += 1
    return self.snapshot(reason="lesson_attempt")
```

On barge-in, cancel the current response ID, increment both normal turn and lesson turn sequence, switch the runtime to listening, and ignore every later audio/tool event from the cancelled response.

- [ ] **Step 4: Register semantic tool handlers**

Handlers resolve `conn.lesson_runtime.conversation`, validate exact identities, call one allowlisted semantic operation, and return typed `{accepted, code, nextIntent, cueId}` responses. They never accept paths, lesson IDs chosen by the model, or mastery flags.

- [ ] **Step 5: Add lesson-scoped system instruction**

In lesson mode instruct Google Live to converse briefly, avoid saying “wrong”, allow at most two contextual turns, bridge Vietnamese meaning to English, use only supplied pronunciation data, and invoke tools instead of claiming progress. Keep general-chat instruction unchanged outside lesson mode.

- [ ] **Step 6: Run GREEN and commit**

```bash
pytest -q tests/test_google_live_tool_calls.py tests/test_google_live_lesson_conversation.py \
  tests/test_lesson_conversation_runtime.py
git add main/tbot-server/core/voice main/tbot-server/plugins_func/functions/lesson_conversation.py \
  main/tbot-server/tests
git commit -m "feat(voice): guide TVideo lessons through Google Live tools"
```

### Task 9: Preserve Cue Identity And Playback Mode Through ESP Sync

**Files:**
- Modify: `main/tbot-server/core/lesson/flattened_cinematic_contract.py`
- Modify: `main/tbot-server/core/lesson/sd_pack_mcp_payload.py`
- Modify: `main/tbot-server/core/lesson/sd_pack_sync.py`
- Modify: `main/tbot-server/core/lesson/sd_pack_materializer.py`
- Modify: `main/tbot-server/core/lesson/global_generation_poller.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Modify: `main/tbot-server/tests/test_flattened_cinematic_contract.py`
- Modify: `main/tbot-server/tests/test_lesson_sd_pack_sync.py`

- [ ] **Step 1: Write failing template-v2 projection tests**

Assert repeated `listen` effects with different cue IDs materialize to unique keys `flattenedCinematic.<cueId>`, and projection preserves `cueId`, `effect`, `stepKey`, `playbackMode`, derivative ID, SHA, bytes, metadata, and exact SD path. Reject missing/extra/stale cues and retain v1 fixtures.

- [ ] **Step 2: Run RED**

```bash
pytest -q tests/test_flattened_cinematic_contract.py tests/test_lesson_sd_pack_sync.py
```

Expected: FAIL because current code keys assets by `phaseId` and only permits template version 1.

- [ ] **Step 3: Implement a discriminated v1/v2 validator**

For v2 return this exact command shape:

```py
{
    "templateId": "flattenedMjpegCinematic",
    "templateVersion": 2,
    "cueId": cue_id,
    "effect": effect,
    "stepKey": step_key,
    "playbackMode": playback_mode,
    "durationMs": duration_ms,
    "fps": 10,
    "frameCount": frame_count,
    "asset": {
        "derivativeId": derivative_id,
        "cueId": cue_id,
        "sdPath": sd_path,
        "sha256": source["sha256"],
        "bytes": source["bytes"],
        "mediaType": "video/mp4",
        "width": 480,
        "height": 320,
    },
}
```

Do not coerce v1 phases into v2 cues.

- [ ] **Step 4: Update runtime commands and ACK matching**

Use `cueId` as the v2 control identity and keep `phaseId` for v1. A helper `cinematic_identity_key(command)` must return exactly one of them and fail if both or neither appear.

- [ ] **Step 5: Run GREEN and commit**

```bash
pytest -q tests/test_flattened_cinematic_contract.py tests/test_lesson_sd_pack_sync.py \
  tests/test_lesson_passive_parity_with_esp.py
git add main/tbot-server/core/lesson main/tbot-server/tests
git commit -m "feat(lesson): sync TVideo cues with playback modes"
```

### Task 10: Add Firmware Template-v2 Once/Loop Playback Without Reallocation

**Files:**
- Modify: `main/lesson_flattened_cinematic_renderer.h`
- Modify: `main/lesson_flattened_cinematic_renderer.cc`
- Modify: `tests/native/lesson_flattened_cinematic_renderer_test.cc`
- Modify: `scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh`
- Modify: `main/lesson_handler.cc`

- [ ] **Step 1: Write failing native tests**

Add tests proving: v1 completes at EOF; v2 `once` completes at EOF; v2 `loop` maps elapsed frames modulo frame count; crossing the seam does not call open/close/allocate/free; pause/resume preserves loop phase; a new cue closes/releases the previous session exactly once; stale cue commands are rejected.

```cpp
EXPECT_EQ(renderer.Tick(1299).type, LessonCinematicResponseType::kCommandApplied);
EXPECT_EQ(renderer.Tick(1300).type, LessonCinematicResponseType::kCommandApplied);
EXPECT_EQ(fake.decode_indices.back(), 0u);
EXPECT_EQ(fake.open_calls, 1u);
EXPECT_EQ(fake.allocate_calls, 1u);
```

- [ ] **Step 2: Run RED**

```bash
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
```

Expected: FAIL because template v2, cue identity, and loop mode are unsupported.

- [ ] **Step 3: Extend the config and exact contract**

```cpp
enum class LessonCinematicPlaybackMode : std::uint8_t { kOnce, kLoop };

struct LessonFlattenedCinematicPhaseConfig {
    const char* renderer_id = nullptr;
    const char* template_id = nullptr;
    std::uint16_t template_version = 0;
    const char* cue_id = nullptr;
    const char* effect = nullptr;
    const char* step_key = nullptr;
    LessonCinematicPlaybackMode playback_mode = LessonCinematicPlaybackMode::kOnce;
    // Existing timing and asset fields remain.
};
```

Keep v1 parsing and fingerprints unchanged; v2 fingerprints include cue/effect/step/playback mode.

- [ ] **Step 4: Implement in-place looping**

In `Tick`, use `frame % metadata_.frame_count` only for v2 loop mode. Do not close the `LessonMjpegMp4File`, release the SD lease, destroy the JPEG workspace, allocate a framebuffer, or emit phase-complete at the seam.

- [ ] **Step 5: Run native, static, and memory regressions**

```bash
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
./scripts/run_host_native_lesson_mjpeg_mp4_test.sh
./scripts/run_host_native_lesson_memory_test.sh
pytest -q tests/test_lesson_heap_boundary_contract.py tests/test_lesson_sd_sync_attestation_contract.py
```

Expected: PASS with one open stream and one framebuffer across at least 1,000 loop seams.

- [ ] **Step 6: Commit**

```bash
git add main/lesson_flattened_cinematic_renderer.* main/lesson_handler.cc \
  tests/native/lesson_flattened_cinematic_renderer_test.cc \
  scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
git commit -m "feat(firmware): loop TVideo cues in place"
```

### Task 11: Store Minimal Structured Evidence And Add Safe Fallback

**Files:**
- Modify: `main/tbot-server/core/lesson/conversation_runtime.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Modify: `main/tbot-server/tests/test_lesson_conversation_runtime.py`
- Modify: `main/tbot-server/docs/google-live-mode.md`
- Modify: `main/tbot-server/scripts/google_live_robot_soak.py`

- [ ] **Step 1: Write failing privacy/progress/fallback tests**

Assert progress contains only lesson/version/step, structured outcome, attempt count, final coaching level, and timing. Assert no audio bytes, transcript, utterance text, or model prose appears. On Live timeout, select thinking, attempt bounded reconnect, then use curated prompts; fallback cannot record speaking mastery.

- [ ] **Step 2: Run RED**

```bash
pytest -q tests/test_lesson_conversation_runtime.py -k 'privacy or fallback or reconnect'
```

Expected: FAIL until evidence and fallback behavior are explicit.

- [ ] **Step 3: Implement the minimal evidence DTO**

```py
@dataclass(frozen=True)
class SpeakingEvidence:
    outcome: Literal["mastered", "attempted", "comprehended"]
    attempt_count: int
    final_coaching_level: int
    elapsed_ms: int
    step_key: str
    lesson_version: int
```

Never attach raw model/child content to this object or progress telemetry.

- [ ] **Step 4: Implement bounded reconnect and curated fallback**

Use one reconnect attempt per interruption window, typed diagnostics, and fixed short prompts sourced from validated lesson content. Keep visual state at thinking/listening unless the authoritative runtime accepts a transition.

- [ ] **Step 5: Run GREEN and commit**

```bash
pytest -q tests/test_lesson_conversation_runtime.py tests/test_google_live_lesson_conversation.py
git add main/tbot-server/core/lesson main/tbot-server/docs/google-live-mode.md \
  main/tbot-server/scripts/google_live_robot_soak.py main/tbot-server/tests
git commit -m "feat(lesson): record private-safe speaking evidence"
```

### Task 12: Produce Farm Visual Goldens And Cross-Repository Fixtures

**Files:**
- Create: `src/lessons/tvideo-journey/fixtures/farm/README.md`
- Create: `src/lessons/tvideo-journey/fixtures/farm/input.json`
- Create: `src/lessons/tvideo-journey/fixtures/farm/golden-index.json`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/opening-flight.png`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/landing-impact.png`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/walk-midpoint.png`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/teach-barn.png`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/listen-barn.png`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/correct-chip.png`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/confetti-apex.png`
- Create: `src/lessons/tvideo-journey/fixtures/farm/goldens/word-transition-hay.png`
- Create: `main/tbot-server/tests/fixtures/tvideo_farm_manifest_v2.json`
- Create: `tests/fixtures/tvideo_farm_command_v2.json`
- Create: `docs/validation/tvideo-farm-software.md`

- [ ] **Step 1: Render candidate frames from the approved farm sources**

Capture exact frames for opening flight, landing impact, mid-walk, teaching, listening, correct chip, confetti apex, and word transition. Each golden index entry records cue ID, frame index, source snapshot SHA, renderer build SHA, and PNG SHA.

- [ ] **Step 2: Add deterministic comparison tests**

Use zero tolerance for frame metadata and a documented per-channel pixel tolerance only where Chromium rasterization requires it. Require the same input snapshot to produce identical normalized metadata and output MJPEG SHA twice in the pinned worker image.

- [ ] **Step 3: Add cross-repository fixture tests**

Backend emits `tvideo_farm_manifest_v2.json`; ESP projects it into the exact firmware command fixture; firmware native test parses and executes that command. Compare cue order, identity, playback mode, SHA, bytes, and metadata at every boundary.

- [ ] **Step 4: Run the complete software proof**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey
npx vitest run src/lessons/tvideo-journey src/lessons/derivatives src/lessons/authoring
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/tbot-server
pytest -q tests/test_google_live_tool_calls.py tests/test_google_live_lesson_conversation.py \
  tests/test_lesson_conversation_runtime.py tests/test_flattened_cinematic_contract.py \
  tests/test_lesson_sd_pack_sync.py
cd /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey
./scripts/run_host_native_lesson_flattened_cinematic_renderer_test.sh
```

Expected: all software proof passes; documentation explicitly says hardware gate pending.

- [ ] **Step 5: Commit fixtures in their owning repositories**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/backend-google-live-tvideo-journey
git add src/lessons/tvideo-journey/fixtures
git commit -m "test(lessons): add farm TVideo visual goldens"
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey
git add main/tbot-server/tests/fixtures/tvideo_farm_manifest_v2.json \
  docs/validation/tvideo-farm-software.md
git commit -m "test: add farm TVideo ESP fixture"
cd /Users/manhhodinh/Documents/TBOT/.worktrees/firmware-google-live-tvideo-journey
git add tests/fixtures/tvideo_farm_command_v2.json
git commit -m "test: add farm TVideo firmware fixture"
```

### Task 13: Run Admin Preview, Credential-Gated Live Smoke, And Attended Hardware Gate

**Files:**
- Modify: `main/tbot-server/scripts/google_live_robot_soak.py`
- Create: `main/tbot-server/scripts/tvideo_farm_preview.py`
- Create: `docs/validation/tvideo-farm-hardware.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/docs/TEST_MATRIX.md`

- [ ] **Step 1: Start the admin preview for user review**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/manager-web
npm run serve -- --host 127.0.0.1 --port 8090
```

Expected: the farm lesson opens with all four preview tabs and publish remains blocked until current derivatives are ready.

- [ ] **Step 2: Run credential-gated Google Live smoke with adult/synthetic audio**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-tvideo-journey/main/tbot-server
python scripts/google_live_robot_soak.py --scenario tvideo-farm --audio-source synthetic --duration-sec 180
```

Expected: if credentials are absent, exit with `SKIP_GOOGLE_LIVE_CREDENTIALS`; if present, exercise target, Vietnamese bridge, related answer, retry, and barge-in without persisting raw audio/transcripts.

- [ ] **Step 3: Perform attended ESP32-S3 N16R8 soak**

Record cold/warm SD runs, 10 FPS metrics, barge-in latency, seamless listen/talk loops, decode/TFT/heap/PSRAM/watchdog/reset/lifecycle metrics, three coaching levels, reconnect, and repeated cue transitions. Require no memory growth, stuck cue, watchdog, or reset.

- [ ] **Step 4: Keep rollout disabled unless the attended gate passes**

Do not change `LESSON_RENDERER_V4_ENABLED`, device allowlists, or production defaults in this task. If hardware evidence is incomplete, write `PENDING_ATTENDED_HARDWARE` in `docs/validation/tvideo-farm-hardware.md` and leave the matrix hardware row pending.

- [ ] **Step 5: Final verification and repository commits**

Run each repository's focused suites, production admin build, backend build, ESP static suites, and firmware native suites. Commit validation documents only with evidence actually observed.

```bash
git status --short
git log -1 --oneline
```

Expected: each feature worktree contains only intentional commits; original dirty worktrees remain untouched.

## Completion Checkpoints

- Checkpoint A after Tasks 2-5: backend can author, identify, render, verify, and publish template-v2 farm cues in the dedicated worker image.
- Checkpoint B after Tasks 6-9: admin and Google Live can preview/drive the authoritative bounded conversation while ESP preserves exact cue identity.
- Checkpoint C after Tasks 10-12: firmware loops without reopening/reallocation and cross-repository farm fixtures pass.
- Checkpoint D after Task 13: admin preview is available; rollout remains disabled unless attended hardware evidence passes.

## Self-Review Checklist

- Every approved visual effect maps to the preset/frame-state/golden tasks.
- Every conversation branch and gentle coaching level maps to deterministic runtime tests.
- Every Google Live tool includes all required identities and stale-event fencing.
- Chromium runtime ownership is explicit and separate from the distroless API image.
- Template-v1 and renderer-v3 compatibility are tested in backend, ESP, and firmware.
- No task introduces GIF, multi-layer firmware playback, raw child recording, 15 FPS, or automatic rollout.
- Hardware validation remains an attended gate and cannot be inferred from software proof.
