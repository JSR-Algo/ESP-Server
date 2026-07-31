# Google Live TVideo Journey Design

## Status

Approved interactively on 2026-07-31. This document defines the next increment
after the flattened renderer-v4 software path. It does not authorize rollout,
deployment, or unattended hardware mutation.

## Goal

Give lessons the complete visual language of `robot/tvideo-raw-code/panel.html`
without making the learning flow rigid. TeeBot uses Google Live API for natural,
interruptible conversation, while a fail-closed lesson state machine keeps every
turn directed toward the assigned learning objective. The robot continues to
play one verified 480x320, 10 FPS, no-audio MJPEG MP4 stream at a time.

The first release delivers `tvideoJourney.v1` and one fully verified farm lesson
golden. Additional scenes reuse the preset only after receiving a scene-specific
journey path and passing the same preview, render, and hardware gates.

## Product Decisions

- Use one locked TVideo Journey preset rather than an arbitrary effect editor.
- Preserve editable source media and lesson content on the website.
- Render effects into verified per-cue MJPEG MP4 derivatives at publish time.
- Use Google Live API for realtime audio, barge-in, contextual replies, and
  allowlisted lesson tool calls.
- Let the child speak naturally, including in Vietnamese, then bridge to the
  English target word.
- Allow at most two contextual or off-topic turns before guiding the child back
  to the current objective.
- Never say that the child is wrong. Use a three-level coaching ladder and
  recognize effort without falsely recording mastery.
- Do not use GIF, concurrent playback of the three source layers on firmware,
  client-authoritative encoding, or 15 FPS.

## Visual Vocabulary

The preset reproduces the panel exemplar's visual behavior:

- background scene video remains live;
- fly-in from the upper-right and perspective landing;
- landing squash, sparkle puff, and ground-shadow pulse;
- walk-toward path with depth scaling;
- robot alpha acting clips for flight, walking, greeting, teaching, and
  celebration;
- teaching object reveal, idle bob, drop shadow, and word pill;
- progress dots and lesson step card;
- step-card reveal and listening glow;
- correct chip pop;
- celebration jump and deterministic 64-piece confetti burst;
- word-out and next-word-in transition;
- gentle listening, thinking, encouragement, and retry cues that reuse the same
  visual language without negative red error states.

The exact easing, effect intensity, confetti spread, safe-zone rules, and visual
timing live in the versioned preset. Admin users cannot alter them individually.

## Architecture

### Admin authoring

The lesson editor stores and previews:

- `presetId: "tvideoJourney"` and `presetVersion: 1`;
- one background video, teaching-object media, word content, and the approved
  robot alpha clip set;
- a scene-specific normalized journey path containing flight ingress, landing,
  walk keyframes, teaching anchor, object anchor, and the 480x320 safe zone;
- per-step target word, Vietnamese meanings, accepted related concepts,
  question seeds, teaching copy, expected answer, progress index, and
  pronunciation coaching data;
- pronunciation data including a slow model, approved segments or phonemes,
  and relevant Vietnamese-L1 guidance.

The editor exposes four previews:

1. `3 Sources` for editable media;
2. `Journey Path` for flight, landing, walk, object, and safe-zone anchors;
3. `Conversation` for simulated child-answer branches;
4. `Robot Flattened` for the exact 480x320 authoritative output.

Conversation simulation covers English target, Vietnamese meaning, related
answer, silence, uncertain recognition, and each coaching level. Publish remains
blocked until authoring data is valid and every required current derivative is
ready.

### Shared deterministic preset runtime

Admin preview and backend rendering consume the same versioned preset
definition. The definition maps an explicit frame time and cue inputs to visual
state. It does not depend on wall-clock timers, animation-frame scheduling,
network fonts, random values, or browser autoplay timing.

The backend is authoritative. A pinned, sandboxed Chromium runtime renders each
480x320 frame using a fixed 10 FPS clock. All fonts, scripts, images, and clips
are local verified inputs. FFmpeg receives the rendered frame stream and only
performs the final no-audio MJPEG MP4 encoding and validation.

The derivative identity includes every output-affecting input:

- renderer and preset build identity;
- cue identity, effect, playback mode, and exact duration;
- lesson version, source revision, and step identity;
- every source asset identity, SHA-256, byte count, and normalized metadata;
- scene path, anchors, bounds, safe zone, object fit, and chroma settings;
- target word, UI copy, progress state, and coaching visual content;
- pinned fonts and deterministic effect parameters.

Changing any input creates a new derivative identity. Promotion remains
same-directory, symlink-safe, SHA-verified, and atomic.

### Playback contract

Renderer-v4 template version 1 remains supported for rollback. TVideo Journey
uses flattened template version 2 with a unique cue identity separate from its
semantic effect:

```json
{
  "templateId": "flattenedMjpegCinematic",
  "templateVersion": 2,
  "cueId": "word-1-listen",
  "effect": "listen",
  "stepKey": "word-1",
  "playbackMode": "loop",
  "timing": { "durationMs": 1300 },
  "asset": {
    "derivativeId": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    "path": "lessons/derivatives/dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd/word-1-listen.mp4",
    "url": "https://cdn.example.test/lessons/derivatives/dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd/word-1-listen.mp4",
    "sha256": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "bytes": 123456,
    "mediaType": "video/mp4",
    "width": 480,
    "height": 320,
    "metadata": {
      "codec": "mjpeg",
      "fps": 10,
      "durationMs": 1300,
      "frameCount": 13,
      "hasAudio": false
    }
  }
}
```

`cueId` is unique within the lesson and permits repeated teach, listen, result,
and coaching behavior across multiple words. `effect` selects a fixed preset
choreography. `playbackMode` is exactly `once` or `loop`. Loop cues reset the
existing reader at EOF without closing the file, reallocating the framebuffer,
releasing the SD lease, or creating a new lesson session.

ESP sync continues to verify one file per cue and rejects partial, corrupt,
unexpected, or stale cue sets. Firmware remains a single-stream renderer with
one framebuffer and existing session/control fencing.

## Conversation-Driven Lesson Runtime

The lesson runtime, not Google Live, is authoritative for the active lesson,
step, target, attempt, allowed transitions, retry budget, and progress outcome.
Google Live owns natural speech generation and understanding inside that scope.

### Runtime identities

Every lesson tool call binds all of:

- `lessonSessionId`;
- `turnSequenceId`;
- `attemptId`;
- `stepKey`;
- `cueId` where a visual change is requested.

Calls with missing, unknown, mismatched, expired, duplicated, or stale identities
are rejected without changing visual state or progress.

### Allowlisted tools

Google Live receives exact-schema tools for lesson observations and transitions.
The tool layer exposes semantic operations, not arbitrary filenames or device
commands. The initial allowlist is:

- `lesson_child_response`: report `target`, `meaning_vi`, `related`, `silence`,
  or `uncertain` for the current attempt;
- `lesson_pronunciation_outcome`: report `correct`, `retry`, or `uncertain` for
  the current target and coaching level;
- `lesson_context_turn`: request one bounded contextual response while retaining
  the current objective;
- `lesson_visual_reaction`: request an allowed effect for the current state;
- `lesson_continue`: request the next state after the runtime confirms that the
  current state is complete.

The runtime validates each request against a table of legal transitions. Google
Live cannot skip a required speaking attempt, select a different lesson step,
mark mastery directly, or send an asset path to firmware.

### Audio and visual synchronization

- Model audio start selects the permitted talk or teaching loop for the current
  state.
- Model audio completion holds or advances according to runtime state rather
  than video duration.
- Child barge-in stops stale model output, increments the turn sequence, and
  switches immediately to the listening loop.
- Any response or tool event belonging to the interrupted turn is ignored.
- Correct and retry reactions are short once-only cues. A new child utterance
  may interrupt them and open a fresh listening turn.

## Guided Elicitation

The primary interaction is not "repeat after me." TeeBot first asks a short
scene-grounded question intended to elicit the target concept.

For `barn`, an approved seed may be: "Con nhìn gần hàng rào xem, các con vật ngủ
ở đâu nhỉ?"

The runtime handles response classes as follows:

- English target: record speaking evidence and run the correct reaction.
- Vietnamese meaning: record comprehension evidence only, acknowledge it, bridge
  to English, and invite the child to say the target word.
- Related concept: acknowledge the connection, use one concise semantic bridge,
  then elicit or model the target word.
- Silence or uncertain input: narrow the question, optionally offer a simple
  contrast, then model the word.
- Off-topic input: respond naturally for at most two turns, then connect the
  child's topic back to the current scene and target.

Understanding the Vietnamese meaning never counts as English speaking mastery.
Speaking mastery requires an attributable English target attempt accepted by
the lesson outcome gate.

## Gentle Coaching

The system never tells the child that they are wrong and never plays a negative
error animation. A pronunciation retry advances through at most three support
levels:

1. acknowledge effort and invite another listen;
2. model the complete target slowly and ask the child to speak with TeeBot;
3. use approved segment or phoneme guidance, then return to listening.

If the child succeeds at any level, the runtime immediately selects the correct
reaction and short praise. If the third level does not produce an accepted
attempt, TeeBot praises the effort, records `attempted` rather than `mastered`,
continues the lesson, and schedules the target for later review.

Google Live may paraphrase the coaching language, but the target, phonemes,
segments, meaning, and maximum coaching level are authoritative versioned lesson
data. The model cannot invent pronunciation rules.

## Failure Handling

### Google Live unavailable

The runtime selects a thinking cue, attempts a bounded reconnect, and then uses
short curated prompts with the same visual cue system. The fallback may continue
practice but cannot record speaking mastery without an accepted outcome source.

### Invalid or stale model output

Invalid tools receive a typed rejection and a safe recovery instruction. Stale
turns are discarded silently from the child-facing experience and logged with a
stable diagnostic code. No model exception reaches firmware as an unvalidated
command.

### Media or renderer failure

Missing, processing, failed, stale, corrupt, partial, or mismatched cue media
blocks publish. A failed rebuild never replaces the last verified derivative or
published lesson. At runtime, ESP and firmware retain or restore the last
attested state and return a typed failure.

### Privacy and safety

- Google credentials remain server-side and are never sent to admin or robot.
- Child audio and raw transcripts are transient and are not written into lesson
  progress records.
- Progress stores only the minimum structured evidence: outcome, attempt count,
  final coaching level, timing, step identity, and versioned lesson identity.
- Prompts prohibit requesting sensitive personal information and retain the
  existing child-safety and output-moderation gates.

## Testing and Acceptance

### Contract and state tests

- template-v2 exact keys, identities, playback modes, cue uniqueness, and v1
  compatibility;
- legal and illegal state transitions;
- duplicate, stale, reordered, cross-session, and cross-attempt tool calls;
- barge-in and reconnect sequence fencing;
- correct, retry, uncertain, and fallback progress semantics.

### Conversation simulations

Run deterministic simulations for:

- English target on first attempt;
- Vietnamese meaning followed by successful English bridge;
- related concept followed by semantic bridge;
- silence and uncertain recognition;
- success at each coaching level;
- three unsuccessful coaching levels;
- one and two off-topic turns;
- attempted third off-topic turn forced back to the target;
- child interruption during model speech and during visual reaction;
- Google Live timeout, reconnect, malformed tool, and stale response.

CI uses mocked Google Live events. Any real Google Live smoke is explicit,
credential-gated, and uses synthetic or adult test audio rather than child data.

### Visual and media proof

- farm golden frames and representative pixels match the approved TVideo panel
  exemplar at key flight, landing, walk, teaching, listening, correct, confetti,
  and word-transition times;
- the same complete input snapshot produces byte-identical normalized metadata
  and the same output SHA-256;
- different paths, copy, assets, preset builds, or coaching UI produce different
  derivative identities;
- loop seams do not close/reopen the file or visibly hold the first frame;
- every output remains 480x320, 10 FPS MJPEG MP4 without audio.

### Cross-repository proof

- admin preview and backend render agree at sampled frame times;
- publish remains blocked until the exact cue set is current and ready;
- generation and ESP materialization preserve cue order, identity, SHA, bytes,
  playback mode, and metadata;
- firmware uses one framebuffer, supports uninterrupted loop cues, rejects stale
  controls, and releases every file and SD lease on transition or terminal exit;
- renderer-v3 and renderer-v4 template-v1 compatibility suites remain green.

### Hardware gate

On an attended ESP32-S3 N16R8 robot, validate:

- farm journey at 10 FPS with real Google Live audio;
- child-simulated barge-in and turn cancellation latency;
- Vietnamese bridge, related-answer guidance, success, and three-level coaching;
- seamless talk and listen loops;
- cold and warm SD cache;
- frame, decode, TFT, heap, PSRAM, watchdog, reset, and lifecycle metrics;
- repeated conversation and cue transitions without memory growth or stuck state.

Software tests cannot waive the hardware gate. Renderer rollout remains disabled
until the attended conversation soak passes. Fifteen FPS remains prohibited.

## Initial Delivery Scope

The first delivery includes:

- versioned `tvideoJourney.v1` preset;
- flattened template version 2 and v1 compatibility;
- Google Live allowlisted lesson tools and conversation state integration;
- admin path, conversation-goal, coaching-data, branch-preview, and flattened
  readiness UI;
- backend deterministic Chromium frame renderer and existing FFmpeg MJPEG
  encoding/promotion boundary;
- ESP cue materialization and firmware once/loop playback;
- farm source assets, journey path, conversation data, and visual goldens;
- software, live-API smoke, and attended hardware validation harnesses.

Additional scene paths and curriculum-wide enablement are separate follow-on
work. Arbitrary animation editing, arbitrary model-generated learning targets,
camera input, GIF, audio embedded in cinematic files, 15 FPS, and removal of
existing fallback renderers are out of scope.

## Acceptance Criteria

1. A teacher can configure the farm target, Vietnamese bridge, related concepts,
   coaching data, media, and scene path and preview every required branch.
2. Google Live can conduct natural, interruptible conversation while the lesson
   runtime prevents invalid objective or progress transitions.
3. A child can answer in Vietnamese, discuss a related idea, ask a short question,
   and still be guided to produce the English target within the bounded flow.
4. Unaccepted attempts receive up to three gentle coaching levels without
   negative language or false mastery.
5. Every published cue is an exact current verified 480x320, 10 FPS, no-audio
   MJPEG MP4 derivative, and firmware plays only one file at a time.
6. The farm output reproduces the approved TVideo visual vocabulary and passes
   deterministic golden comparison.
7. Stale Live events, malformed tools, unavailable media, corrupt downloads, and
   failed renders cannot advance progress or replace verified state.
8. Existing renderer-v3 and renderer-v4 template-v1 behavior remains available
   for explicit rollback.
9. Rollout stays disabled until the attended ESP32-S3 Google Live conversation
   and cinematic soak passes.
