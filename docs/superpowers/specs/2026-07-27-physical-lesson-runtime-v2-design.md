# Physical Lesson Runtime V2 Design

## Status

Approved concept: option B, "cinematic entrance plus per-step reactions".

This document is the detailed design for review before implementation planning.

## Goal

Every published curriculum lesson should produce a truthful, repeatable physical robot experience:

- the robot overlay flies in, lands, walks toward the child, and greets once when the lesson starts;
- later lesson steps do not replay the entrance;
- teaching, listening, thinking, encouragement, retry, celebration, and completion states use the authored visual and motion data;
- the admin preview and the physical 480x320 TFT follow the same renderer contract;
- missing optional effects degrade safely without stopping audio or lesson interaction;
- old firmware continues to run the current static renderer.
- the parent mobile app shows which lesson and activity the child is currently doing;
- the parent receives a meaningful, privacy-safe learning report after the session;
- course progress across all 26 lessons is visible from the same parent experience.

## Non-Goals

- Rendering HTML, CSS, MP4, or GIF content directly on the ESP32.
- Adding a large multi-frame sprite atlas in the first renderer-v2 release.
- Letting firmware infer lesson outcomes from local speech or transcript state.
- Replacing the existing SD-first and HTTPS-fallback asset materialization flow.
- Replaying the cinematic entrance on every lesson step.
- Reimplementing backend safe-speaking thresholds or attempt logic in firmware.
- Streaming raw audio, transcript text, or sensitive pronunciation data to a parent screen.
- Building a separate analytics domain that duplicates lesson assignments, sessions, and progress events.

## Current Production Gap

The authored and previewed experience is richer than the physical runtime:

- NestJS manifests preserve `entrance`, `templateProjection`, `scene`, and the motion matrix.
- The first step is validated as `flyIn`; later steps are validated as `none`.
- The admin preview animates entrance and response paths with browser CSS.
- The ESP server currently omits `entrance` from the runtime step body.
- Firmware validates the TVideo projection but hard-codes atlas availability to false, never advances the phase state machine, and renders only the final arrived pose.
- Firmware and the ESP server can both dispatch `present`, creating ambiguous ownership if all existing gates are enabled.
- Listening, thinking, and branch-result overlays do not have a dedicated runtime visual-state event.

The result is a preview that promises behavior the robot does not currently perform.

## Design Principles

1. One manifest is the source of truth.
2. One component owns each side effect.
3. Entrance completion is asynchronous and generation-gated.
4. A visual failure must not terminate an otherwise playable lesson.
5. Capability negotiation must keep renderer v1 compatible.
6. The admin must label physical parity honestly.
7. Asset identities remain pinned by version and SHA-256.

## Ownership

### NestJS Backend

NestJS owns authored and derived lesson truth:

- normalize the first-step TVideo projection into a lesson-level `openingEntrance`;
- validate exactly one opening entrance for a renderer-v2 lesson;
- require all later step entrances to be `none`;
- preserve per-step scene, teaching content, robot overlay, and motion slots;
- validate motion names against the server-supported allowlist;
- produce the same normalized renderer-v2 projection for admin preview and runtime delivery.

### ESP Server

The ESP server owns lesson orchestration and physical motion decisions:

- negotiate renderer and motion capabilities;
- send the opening entrance once per lesson session;
- wait for the firmware entrance completion ACK before advancing the passive first step;
- send explicit visual-state updates for listen, think, and branch results;
- remain the only owner of slave/UART physical motion dispatch;
- dispatch each physical motion once, after the corresponding visual state is installed;
- continue owning attempts, thresholds, timeouts, and safe-speaking branch selection.

### Firmware

Firmware owns the TFT renderer and render lifecycle:

- animate the opening robot overlay using LVGL;
- install background, object, word, prompt, progress, and robot-overlay layers;
- apply explicit runtime visual states without inferring outcomes;
- ACK only after the requested visual state is installed;
- cancel animations safely on stop, pause, disconnect, replacement, or error;
- degrade to a stable arrived pose when animation cannot run;
- never independently dispatch renderer-v2 physical motion presets.

### Admin Website

The admin owns authoring feedback, not runtime truth:

- project the normalized renderer-v2 contract;
- show the cinematic entrance once and per-step visual reactions afterward;
- keep the exact TFT projection visible alongside the cinematic design reference;
- warn when selected or connected firmware supports only renderer v1;
- never label browser-only CSS or video behavior as exact physical output.

### Parent Mobile App

The mobile app owns the parent-facing presentation:

- show the active child and active lesson session;
- update the current activity and progress near real time;
- show a completed-session learning report;
- show course position, completed lessons, and the suggested next lesson;
- use existing household/child authorization and React Query cache ownership;
- fall back to polling when the realtime connection is unavailable;
- never display raw transcript or audio content.

## Renderer Capability Contract

Firmware advertises a renderer capability in its existing hello/capabilities payload:

```json
{
  "lessonRenderer": {
    "version": 2,
    "openingEntrance": true,
    "visualStateEvents": true,
    "singleSpriteEntrance": true,
    "atlasEntrance": false,
    "physicalMotionOwner": "server"
  }
}
```

Renderer v1 devices omit these fields or report version 1. The server must not send renderer-v2-only messages unless the connected device advertises support.

## Normalized Manifest Contract

The backend derives a lesson-level opening contract from the first manifest step:

```json
{
  "rendererVersion": 2,
  "openingEntrance": {
    "template": "tvideoFlyWalk",
    "preset": "flyLandWalkGreet",
    "policy": "oncePerLessonSession",
    "layoutPreset": "centerRoad",
    "phases": [
      "hidden",
      "flyIn",
      "landFar",
      "settle",
      "walkToward",
      "arriveNear",
      "greetIdle",
      "revealTeachingContent"
    ],
    "backgroundAssetKey": "scene.farm",
    "robotAssetKey": "robotOverlay.teach",
    "fallback": "staticGreet"
  }
}
```

Step contracts retain the authored state and motion matrix:

```json
{
  "stepKey": "s2",
  "robotState": "listening",
  "scene": {
    "backgroundScene": {},
    "teachingObject": {},
    "robotOverlay": {}
  },
  "motion": {
    "present": "presentLeft",
    "listen": "listen",
    "thinking": "thinking",
    "correct": "celebrate",
    "nearMiss": "encourage",
    "incorrect": "tryAgain",
    "completion": "goodbye"
  }
}
```

The backend rejects renderer-v2 manifests when:

- the opening contract is missing from a lesson that requires it;
- more than one step requests an entrance;
- an opening phase, layout preset, asset reference, or motion name is unsupported;
- pinned background or overlay identity disagrees with the referenced scene asset;
- a required asset does not have a valid version, SHA-256, and SD/online source.

## Runtime Wire Protocol

### Lesson Prepare

`lesson_prepare.body.runtimeControls` adds explicit negotiated ownership:

```json
{
  "rendererVersion": 2,
  "openingEntranceEnabled": true,
  "visualStateEventsEnabled": true,
  "motionPresetsEnabled": true,
  "physicalMotionOwner": "server"
}
```

### Lesson Start

For renderer v2, `lesson_start.body` contains the normalized opening contract:

```json
{
  "openingEntrance": {
    "preset": "flyLandWalkGreet",
    "policy": "oncePerLessonSession",
    "layoutPreset": "centerRoad",
    "backgroundAssetKey": "scene.farm",
    "robotAssetKey": "robotOverlay.teach",
    "fallback": "staticGreet"
  }
}
```

The server waits for a completion ACK before sending or auto-advancing the first passive step. A duplicate `lesson_start` for the same session and sequence must be acknowledged without replaying the entrance.

### Lesson Step

`lesson_step.body` keeps the stable scene and authored step data. It does not carry a replayable entrance. In renderer-v2 server-owned-motion mode, firmware does not dispatch `motion.present` itself.

### Visual State Event

A new explicit runtime frame updates visual state without pretending that the lesson advanced:

```json
{
  "type": "lesson_visual_state",
  "sessionId": "...",
  "stepSequence": 2,
  "visualSequence": 5,
  "body": {
    "state": "nearMiss",
    "robotOverlay": {
      "assetKey": "robotOverlay.thinking"
    },
    "motionPreset": "encourage"
  }
}
```

Supported state names are:

- `teach`
- `listen`
- `thinking`
- `correct`
- `nearMiss`
- `incorrect`
- `retry`
- `celebrate`
- `completion`

Firmware installs the overlay/state and ACKs `visualSequence`. The server dispatches the associated physical motion once after a successful or degraded visual ACK. Firmware never derives a result state from local transcript data.

## Firmware Entrance Renderer

### First Release Strategy

The first renderer-v2 release uses the pinned arrived-pose PNG as one LVGL sprite. The sprite is moved and scaled across the existing TVideo geometry:

```text
hidden -> flyIn -> landFar -> settle -> walkToward
       -> arriveNear -> greetIdle -> revealTeachingContent
```

This deliberately avoids an atlas in the first release. It matches the current browser preview, which also animates a single image, while avoiding unnecessary PSRAM and decoder pressure.

### LVGL API Boundary

The display layer exposes an asynchronous API similar to:

```cpp
StartLessonRobotEntrance(plan, completion);
CancelLessonRobotEntrance();
ApplyLessonVisualState(state, completion);
```

Only the LVGL/Application task mutates LVGL objects. Timer callbacks and the lesson worker must not call LVGL directly.

### Completion and ACK Ordering

The lesson worker:

1. downloads/decodes and installs the required image;
2. starts the LVGL entrance;
3. records a pending ACK with session, step, asset, and visual generations;
4. returns to its queue without blocking;
5. receives an internal entrance-complete or entrance-timeout event;
6. rejects stale events using generation checks;
7. reveals teaching content;
8. sends the lesson ACK;
9. allows the server to continue the lesson.

ACK must not be sent when the entrance merely starts. Passive-step ACK currently permits server auto-advance, so early ACK would hide or interrupt the entrance.

## Cancellation and Session Safety

Firmware maintains at least:

- `opening_entrance_consumed`
- `entrance_active`
- `visual_generation`
- `pending_step_sequence`
- `pending_visual_sequence`
- `pending_ack`

Cancellation increments `visual_generation` before removing LVGL animations. Late completion events then fail the generation check.

Cancellation is required for:

- fresh `lesson_prepare`;
- `lesson_pause`;
- `lesson_stop`;
- lesson error;
- transport disconnect or abandonment;
- a new step replacing an active visual;
- render failure that removes lesson content.

Pause cancels and consumes the opening entrance. Resume renders the current step at the arrived pose instead of replaying the cinematic sequence.

## Asset Strategy

The existing global generation remains the delivery mechanism.

Renderer-v2 assets continue to contain:

- `sdPath` and `localPath` for SD-first rendering;
- HTTPS `onlineUrl` and `url` fallback;
- pinned SHA-256, size, media type, version, and criticality.

The opening background and robot overlay must be included in the pack and identity-checked against the normalized opening contract.

The current 26 curriculum packs already contain teach, listening, thinking, and celebrate overlays. The implementation plan must add a production manifest audit for state/motion metadata because the public generation index intentionally contains asset metadata, not full manifest steps.

## Degraded Behavior

Degradation is deterministic and never leaves the lesson queue blocked:

1. Valid contract and asset: run the full single-sprite entrance.
2. Animation unavailable or cannot start: snap to arrived pose and reveal content.
3. Optional robot overlay unavailable: keep the last valid robot pose.
4. Robot overlay cannot render: keep background, object, word, and prompt.
5. Critical scene failure: ACK degraded and let server policy decide whether audio-only continuation is allowed.

Stable degraded reasons include:

- `missingOverlay`
- `animationStartFailed`
- `phaseTimeout`
- `reducedMotion`
- `unsupportedContract`
- `assetIdentityMismatch`
- `insufficientHeap`

Physical motion failure does not roll back an installed visual state. Visual failure does not silently cause duplicate physical motion.

## Performance Budget

- No per-frame decode or network I/O.
- Animate LVGL bounds/transforms only.
- Target 15-25 FPS; 60 FPS is not required.
- Keep existing encoded and decoded image limits.
- Avoid a sprite atlas in renderer-v2.0.
- Record heap checkpoints before decode, after install, after entrance completion, and after cancel.
- A watchdog may enqueue a timeout event but must not touch LVGL or session state directly.
- Reuse installed overlays through the existing layer state when asset identity matches.

## Admin Experience

The lesson editor shows two distinct surfaces:

1. `Cinematic design reference`: the website-style creative preview.
2. `Exact robot renderer`: the 480x320 renderer-v2 projection.

The cinematic iframe must not hide the exact robot preview. The exact preview displays:

- renderer version;
- entrance policy;
- current phase/state;
- motion owner;
- degraded or unsupported features;
- whether the selected firmware can reproduce the authored effect.

Step detail fields (`subject`, `helperText`, and `l1TransferHint`) remain editable and persist with the cinematic UI enabled.

## Parent Learning Progress

### Existing Foundation

The product already has the core data path:

- assignments and lesson sessions identify child, device, lesson, course, and lifecycle;
- append-only progress events record lesson and step activity;
- the ESP runtime emits lesson-started, step-started, interactive step-completed, and terminal summary events;
- parent mobile screens already show Today, History, course insights, and the active child;
- the mobile app already has session-observer reconnect primitives and push-token registration.

The current observer is session-scoped and cannot be reused as a child progress subscription. The design reuses only its transport/reconnect patterns and adds a separate child-scoped client, path, and frame validator.

### Parent Read Model

NestJS exposes one parent-owned aggregate endpoint:

```text
GET /v1/mobile/children/:childId/learning-status
```

Authorization uses the same household membership rules as child lesson progress. Accepted guardians with access to the child can read the status. Device identity or admin proxy keys are not valid substitutes for parent authorization.

The response contains three sections:

```json
{
  "activeLearning": {
    "assignmentId": "...",
    "sessionId": "...",
    "deviceId": "...",
    "courseId": "...",
    "courseTitle": "English Journey",
    "lessonId": "...",
    "lessonTitle": "Farm Friends",
    "state": "RUNNING",
    "startedAt": "2026-07-27T08:00:00Z",
    "currentStep": {
      "stepId": "s4",
      "stepNumber": 4,
      "total": 9,
      "activityTitle": "Listen and say BARN",
      "phase": "listening",
      "subject": "barn"
    },
    "positionPercent": 44,
    "activeDurationSec": 210
  },
  "recentSessions": [],
  "courseProgress": []
}
```

`activeLearning` is nullable and state-aware. An assignment may be `ASSIGNED`, `PRELOADING`, or `READY` before a session or current step exists, so `sessionId`, `startedAt`, and `currentStep` are nullable until the lesson starts. `recentSessions` contains parent-safe, paginated completed-session summaries and always includes child, assignment, session, course, lesson, terminal state, and report-availability identifiers. `courseProgress` contains the current lesson position, completed lesson count, total lesson count, percentage, and backend-authoritative suggested next lesson.

The mobile Axios base URL already ends in `/v1`; mobile client methods therefore call `/mobile/children/...`, not `/v1/mobile/children/...`.

### Accurate Live Progress

The current SQL count of `step_completed` can undercount passive steps because passive steps auto-advance after TFT ACK without emitting that event.

Renderer-v2 progress uses these rules:

1. `step_started` is emitted only after firmware has installed and ACKed the visual step.
2. Live current position is derived from the latest committed `step_started` joined to version-pinned authored step order.
3. The API converts zero-based database indexes to one-based `stepNumber`.
4. `positionPercent` means the furthest authored position reached, not percentage learned or mastered.
5. Terminal completion forces position to 100 percent and uses `lesson_completed.summary.stepsCompleted` as authoritative.
6. Completed/succeeded/outcome counts remain separate from position.

The implementation may additionally emit synthetic passive-step completion events, but the parent read model must not depend on that change for correct live percentage.

### Realtime Parent Channel

NestJS provides a separate parent-authenticated, child-scoped WebSocket namespace/gateway. It must not broaden the existing device-only gateway. The mobile app implements a dedicated parent-progress client while reusing proven reconnect primitives.

Subscription requirements:

- authenticate with the parent JWT;
- authorize household access to `childId` before subscribing;
- fan out only after the progress event transaction commits;
- include nullable `sessionId`, durable `projectionRevision`, event occurrence time, and publish time;
- support reconnect with a fresh aggregate fetch;
- never replay another child's events through a stale subscription;
- remove the previous child subscription immediately when the active child or household changes;
- re-check membership on subscribe/resubscribe and handle token expiry or membership revocation;
- use shared broker/outbox fan-out so a commit on one NestJS replica reaches a parent socket on another replica.

`projectionRevision` is a backend-owned durable read-model/outbox ordinal. Firmware/runtime event sequence is not a mobile cursor because it can reset, be synthetic, or be absent.

The primary frame is:

```json
{
  "type": "lesson.progress.updated",
  "childId": "...",
  "sessionId": "...",
  "projectionRevision": 12,
  "occurredAt": "2026-07-27T08:04:10Z",
  "publishedAt": "2026-07-27T08:04:10.250Z",
  "activeLearning": {
    "lessonTitle": "Farm Friends",
    "state": "RUNNING",
    "currentStep": {
      "stepNumber": 4,
      "total": 9,
      "activityTitle": "Listen and say BARN",
      "phase": "listening",
      "subject": "barn"
    },
    "positionPercent": 44,
    "activeDurationSec": 210
  }
}
```

Mobile uses one canonical aggregate key, `['parent-learning-status', childId]`. Valid frames update that cache. Legacy child-progress/dashboard queries are migrated to or invalidated from this aggregate instead of becoming independent truths. On reconnect, foreground resume, projection-revision gap, malformed frame, household/child switch, or notification receipt, mobile invalidates and refetches the aggregate endpoint. A bounded polling fallback keeps the Today screen useful when realtime is unavailable.

Every live status displayed to a parent must have a committed source. Assignment lifecycle comes from assignment/session state; current activity comes from committed `step_started` joined to authored metadata. Entrance, thinking, feedback, pause, resume, and failure require new privacy-safe `runtime_phase_changed` or lifecycle events emitted after the corresponding accepted transition/visual ACK and ingested before parent publication.

The backend defines idempotent event-to-state transitions for assigned, preloading, ready, running, paused, resumed, completed, abandoned, and failed sessions. `lesson_failed` must transition the session/assignment to a terminal failure state; pause and resume must update active-duration accounting. Only committed pause/failure transitions marked as parent-attention-worthy may trigger notifications.

### Mobile Live Experience

The parent Today screen shows:

- child name and optional avatar, with name/initial fallback;
- live status: preparing, robot entrance, teaching, listening, thinking, feedback, paused, completed, or failed;
- course and lesson title;
- current activity title and child-safe subject label;
- current step / total steps and percentage;
- accumulated active learning duration, excluding paused/offline time;
- last update time and an offline/reconnecting indicator.

The screen deliberately does not show live transcript, microphone audio, raw confidence, or a word-by-word surveillance feed.

### Completed Session Report

The history row opens a session-scoped report:

```text
GET /v1/mobile/children/:childId/learning-sessions/:sessionId/report
```

The first report contract contains only evidence the system can prove:

- lesson and course identity;
- version-pinned authored objective when available;
- duration and completion state;
- authored words/structures presented or reached;
- activities attempted and terminal categorical outcomes;
- accepted outcome, final response class, and total attempts where persisted;
- content to review next;
- reward/XP already produced by the existing reward pipeline;
- suggested next lesson.

The report distinguishes `presented`, `attempted`, `accepted`, and `needsReview`. It must not claim durable mastery from one categorical runtime outcome. Intermediate near-miss, retry, and timeout counts are shown only after versioned attempt/branch events are persisted explicitly.

The report is a projection of lesson-runtime sessions and progress events. It must not reuse legacy conversational `learning_sessions` summaries or analytics rows that do not contain lesson outcomes. Report fields such as objective, activity title, and vocabulary tags originate only from version-pinned authored metadata, never arbitrary runtime payload.

The report query first asserts child ownership, then constrains `sessionId` through an assignment belonging to that same child. Supplying another child's session UUID must not disclose whether the session exists.

### Privacy and Data Minimization

Parent progress stores and displays only:

- authored goal, prompt label, subject, and vocabulary tags;
- categorical lesson state and outcome;
- step/course position;
- duration, attempts, and reward totals.

It excludes:

- raw audio;
- raw or reconstructed transcript;
- child voice recordings;
- unrestricted pronunciation confidence;
- arbitrary server debug payloads.

Existing event-ingest scrubbing remains mandatory. Realtime frames are built from committed, scrubbed read models rather than forwarded runtime payloads.

### Completion Notifications

After the completion transaction commits, the backend writes durable outbox work keyed by session, notification type, and guardian. An idempotent worker then:

1. projects the final session report independently of course-advance success;
2. retries/refetches course enrollment advancement and next-lesson state;
3. publishes the parent realtime completion frame through shared fan-out;
4. resolves currently authorized guardians and sends at most one push per outbox key.

The deep-link contract is `TJBot://parent/children/:childId/sessions/:sessionId/report`. Foreground receipt invalidates the active child's canonical aggregate/report queries. Cold, background, and foreground notification taps navigate to a dedicated parent report route and re-check child authorization.

Notifications are sent for completion, pause requiring attention, and terminal failure. Step-by-step push notifications are explicitly excluded.

### Mobile UI Reuse

Implementation should extend the current parent surfaces:

- `ParentTodayScreen` for live status;
- `ParentHistoryScreen` for tappable session rows;
- a dedicated `ParentSessionReportScreen`, rather than child-facing reward/XP CTAs in the current `LessonSummaryScreen`;
- the existing progress and course-insight screens for 26-lesson course position;
- reconnect primitives from the existing realtime client plus a new child-scoped parent client;
- the existing notification linking configuration.

The current hardcoded words-practiced content must be replaced with the session report contract before it is presented as real learning evidence.

The first renderer-v2 mobile release is scoped to the active household. Multi-household selection is separate work unless the implementation plan explicitly includes it.

The parent report navigation route requires `{ childId, sessionId }`. Every History row carries the exact identifiers needed to open that route; the client never guesses a session from lesson title, date, or device.

## Compatibility and Rollout

### Renderer V1

- Receives existing prepare/start/step frames.
- Renders static arrived content.
- Does not receive visual-state events.
- Does not block deployment of new manifests when the backend can project a v1-compatible scene.

### Renderer V2

- Receives opening entrance on lesson start.
- Receives explicit visual-state events.
- Reports server-owned physical motion.
- Uses single-sprite LVGL entrance and static state overlays.

### Rollout Order

1. Land tests and normalized backend contract behind a disabled capability.
2. Land server protocol support with v1 behavior unchanged.
3. Land firmware renderer-v2 support and flash the test robot.
4. Run local and HIL parity tests.
5. Enable renderer-v2 for the connected test robot only.
6. Publish a new global asset generation only if manifest or asset identities change.
7. Deploy server/admin production changes.
8. Expand renderer-v2 capability after soak evidence passes.

## Verification

### Backend

- Normalize exactly one lesson-level opening entrance.
- Reject entrance after step zero.
- Validate layout, phases, motion allowlist, and pinned asset identities.
- Audit all 26 production manifests for the complete motion matrix.

### ESP Server

- Send opening entrance only to renderer-v2 devices.
- Wait for entrance ACK before passive auto-advance.
- Send visual-state events for listen, think, and every branch result.
- Dispatch present/listen/result physical motions exactly once.
- Preserve renderer-v1 frames unchanged.
- Handle degraded and timeout ACKs without queue deadlock.
- Emit progress only from committed runtime transitions; `step_started` follows TFT ACK.
- Preserve terminal summary counts for passive and interactive steps.

### Firmware Native Tests

- First entrance does not ACK before completion.
- Phase trace follows entry, land, walk interpolation, arrive, greet, and reveal.
- Entrance runs once per lesson session.
- Duplicate start and duplicate sequence do not replay entrance.
- Later steps render at arrived bounds.
- Visual-state events replace overlays and ACK the correct visual sequence.
- Stop, pause, error, disconnect, and replacement cancel animation.
- Late callbacks are rejected by generation.
- Missing overlay, no LVGL, insufficient heap, start failure, and timeout degrade correctly.
- One hundred start/cancel cycles do not leak heap or image references.

### Cross-Repository Parity

Use one published manifest fixture to compare admin and firmware traces at every phase boundary:

- phase name;
- robot bounds;
- teaching-content visibility;
- selected overlay asset;
- state/motion name;
- degraded reason.

### Hardware-in-the-Loop

- Record the physical 480x320 TFT entrance and verify it occurs once.
- Verify teach, listen, think, correct, near-miss, retry, and celebrate states.
- Stop and pause during fly-in and walk; confirm no late overlay reappears.
- Test SD asset, HTTPS fallback, and lost network after SD materialization.
- Run a 60-minute soak while monitoring heap, queue pressure, reconnects, AFE/wake-word, and UART motion.
- Confirm no crash, watchdog reset, OOM, duplicate motion, or unexpected reconnect.

### Parent Backend and Mobile

- Enforce household access for aggregate, report, and realtime subscription.
- Return active session, current authored step, total steps, percentage, and elapsed time.
- Prove passive steps do not undercount live progress.
- Distinguish reached-position percentage from completed/succeeded outcome counts.
- Publish realtime frames only after database commit.
- Assign durable projection revisions and deliver updates across multiple backend replicas.
- Remove stale subscriptions on child/household switch and reject revoked or expired membership.
- Recover from reconnect, projection-revision gap, foreground resume, and invalid frames by refetching.
- Render live lesson/activity state on Parent Today.
- Open the correct session report from History and push deep links.
- Show presented/reached content, observed categorical outcomes, review suggestions, rewards, and backend-authoritative next lesson without unsupported mastery claims.
- Prove no transcript, audio, or unsanitized runtime payload reaches parent APIs or realtime frames.
- Keep polling fallback functional when realtime is unavailable.
- Test aggregate/report normalizers, out-of-order/duplicate/gapped frames, stale child subscriptions, polling lifecycle, History navigation, unauthorized session IDs, and cold/background/foreground notification taps.
- Prove completion outbox retries do not duplicate guardian notifications.

## Definition of Done

- All 26 curriculum lessons have valid renderer-v2 entrance and motion contracts.
- Admin cinematic and exact robot previews are both visible and clearly labeled.
- Admin and firmware traces match at all renderer-v2 phase boundaries.
- The physical robot runs entrance once and does not repeat it between steps.
- Each interaction outcome produces the correct visual state and one physical motion.
- Renderer-v1 fallback remains functional.
- Production asset generation is ready and all connected robots report current.
- Firmware passes native, HIL, restart, and 60-minute soak verification.
- Production server/admin containers show no fatal, panic, OOM, or restart regression.
- Parent mobile shows the active lesson and current activity near real time.
- Completed sessions produce a privacy-safe learning report and course-progress update.
- Push notifications deep-link to the correct child/session report.

## Open Implementation Detail

The implementation plan must choose the smallest compatible internal-event integration point for LVGL completion. The design requires asynchronous, generation-gated completion owned by the lesson worker; it does not require a specific queue enum or callback type.
