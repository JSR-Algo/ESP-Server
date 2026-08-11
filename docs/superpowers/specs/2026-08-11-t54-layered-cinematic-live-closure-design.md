# T5.4 Layered Cinematic Live Closure Design

## Status

Ready for written-spec review before implementation.

## Goal

Close the remaining T5.4 physical-lesson gaps without weakening existing renderer,
progress, cache, or replay-safety contracts. The visible lesson must use the existing Cinema
composition geometry with these three ordered layers:

1. `backgroundScene`: one high-quality JPEG image;
2. `teachingObject`: one high-quality transparent PNG image;
3. `robotOverlay`: one robot-ready MJPEG-in-MP4 video carrying the cinematic motion.

The Robot video must visibly cover the approved effect vocabulary:

`flyIn -> walk/move -> teach -> listen/thinking -> celebrate -> exit`.

Background and teaching-object assets remain independent images. They are not baked into the Robot
video and are not represented as one-frame videos.

## T5.4 Closure Boundary

This design owns the product changes required by the physical evidence already gathered:

- a mixed-media three-layer renderer that preserves the Cinema layout and lifecycle;
- exact capability negotiation for the new renderer contract;
- complete step telemetry for passive as well as interactive steps;
- parent progress opening on the child actually bound to the assigned robot;
- production lesson content that contains real Robot motion rather than still-image fallback;
- regression, hardware, Android, evidence, deployment, and Ship-checklist proof.

It does not own intentional Wi-Fi-loss testing, which the operator deferred to T7.4. It also does
not weaken SD-pack replay mismatch protection or silently repair unrelated historical packs. Any
new unrelated defect is recorded in `LESSON_PRODUCTION_PLAN.md` section 5 with an owning task.

## Compatibility Decision

The renderer identity is new:

- protocol and manifest version: `teebot-lesson-renderer.v5`;
- template: `layeredCinematic`;
- template version: `1`;
- firmware capability: `lessonRendererV5.layeredCinematic=true`;
- SD pack support remains an independently declared capability.

The v3 `directMp4Cinematic` contract continues to mean three synchronized videos. The v4
`flattenedMjpegCinematic` contract continues to mean one pre-composited video. Neither contract is
relabelled or extended. A firmware build that advertises only v1-v4 therefore cannot accept a v5
manifest and fail later while parsing it.

Capability selection remains exact and fail closed. The ESP server requests v5 only when all of
the following are true:

- firmware advertises `teebot-lesson-renderer.v5`;
- `lessonRendererV5.layeredCinematic` is true;
- `lessonRendererV5.sdAssetPack` is true;
- the existing rollout gate enables v5 for that device.

Older renderer capabilities remain available in the ordered capability set for older assignments.

## Phase Contract

Each cinematic phase contains exactly three typed layers in fixed z-order. The image layers have no
frame clock. The Robot layer owns phase duration, FPS, frame count, and playback mode.

```json
{
  "protocolVersion": "teebot-lesson-renderer.v5",
  "templateId": "layeredCinematic",
  "templateVersion": 1,
  "phaseId": "teach",
  "durationMs": 3200,
  "fps": 15,
  "frameCount": 48,
  "playbackMode": "once",
  "layers": [
    {
      "slot": "backgroundScene",
      "mediaKind": "image",
      "mediaType": "image/jpeg",
      "sdPath": "sd://tbot/lessons/.../background.jpg",
      "sha256": "...",
      "bytes": 123456,
      "width": 480,
      "height": 320,
      "rect": { "x": 0, "y": 0, "width": 480, "height": 320 },
      "fit": "cover"
    },
    {
      "slot": "teachingObject",
      "mediaKind": "image",
      "mediaType": "image/png",
      "sdPath": "sd://tbot/lessons/.../object.png",
      "sha256": "...",
      "bytes": 45678,
      "width": 240,
      "height": 240,
      "rect": { "x": 130, "y": 72, "width": 200, "height": 200 },
      "fit": "contain"
    },
    {
      "slot": "robotOverlay",
      "mediaKind": "video",
      "mediaType": "video/mp4",
      "codec": "mjpeg",
      "hasAudio": false,
      "sdPath": "sd://tbot/lessons/.../robot.mp4",
      "sha256": "...",
      "bytes": 567890,
      "width": 240,
      "height": 240,
      "rect": { "x": 160, "y": 64, "width": 220, "height": 220 },
      "chromaKey": { "keyColor": "#00ff00", "tolerance": 20, "featherPx": 1 }
    }
  ]
}
```

Validation requires exact slot names and order, mandatory byte count and SHA-256, JPEG background,
PNG teaching object, audio-free MJPEG MP4 Robot video, bounded dimensions, valid destination
rectangles, valid chroma-key metadata, and internally consistent Robot duration/FPS/frame count.
Unknown keys, duplicated slots, path traversal, media-kind/type mismatch, oversize assets, or a still
image in `robotOverlay` fail with typed renderer errors. T5.4 does not silently fall back to a Robot
still image.

## Firmware Renderer

The implementation reuses the production Cinema renderer's geometry, RGB565 framebuffer,
chroma compositor, shared clock, frame deadline policy, pause/resume rebasing, command sequence
deduplication, ACK vocabulary, SD lease, and stop/cancel cleanup.

Prepare performs bounded work in this order:

1. validate the exact v5 contract and all three verified SD paths;
2. decode the background JPEG once into a retained RGB565 base buffer;
3. decode the teaching-object PNG once, preserving alpha, into a retained overlay buffer;
4. open and validate the Robot MJPEG MP4 stream;
5. compose frame zero and emit the existing frame-zero/phase-ready ACK sequence.

Each playback tick copies or restores the static base, alpha-composites the teaching object, decodes
one Robot frame, chroma-composites it at the declared rectangle, and presents once. Static assets
are never decoded per video frame. A missed deadline drops or repeats the whole composed frame; no
layer can advance independently.

The PNG/JPEG decoding path must reuse or extract the existing bounded firmware decoding helpers.
It must not add a second unbounded image decoder. Allocation failure, decode failure, invalid image
dimensions, corrupt MP4 metadata, SD read failure, or present failure returns a typed error and
releases all files, buffers, and leases without a watchdog reset.

## Effect Routing

The authored phase vocabulary remains semantic rather than tied to filenames. Runtime routing maps
lesson lifecycle to Robot-video phases:

| Lesson moment | Robot phase |
| --- | --- |
| lesson introduction | `flyIn`, then `walk` |
| passive explanation/model | `teach` |
| response window open | `listen` |
| response processing or near miss | `thinking` |
| accepted answer or lesson completion | `celebrate` |
| lesson stop | `exit` |

The content pack may reuse one verified asset across multiple semantic phases only when the phase
metadata deliberately points to the same immutable asset version. Missing phases are publish-time
errors for the T5.4 acceptance lesson, not runtime still-image degradation.

## ESP Server And Pack Materialization

The ESP server adds a strict v5 parser alongside the existing v1-v4 parsers. It preserves the v5
manifest identity and constructs an SD pack containing the three asset types without conversion.
All bytes are downloaded to staging, checked against declared size and SHA-256, and atomically
activated only after the full generation verifies.

Historical replay handling remains conservative. `PACK_REPLAY_MISMATCH` is investigated by
comparing the exact incoming key-to-digest projection with the stored projection. The fix, if the
same immutable pack is represented in a known historical schema, is a tested canonical projection;
a genuinely different digest remains a mismatch and is quarantined. No code may turn arbitrary
mismatches into replay success.

## Complete Progress Telemetry

The backend correctly counts distinct `step_completed` events. The ESP runtime currently marks
passive steps complete internally but does not forward a terminal progress event for them, which is
why a nine-step physical lesson can finish with only four completed steps in the backend.

When a passive step finishes after render ACK, prompt playback, and optional dwell, the runtime
emits exactly one synthetic `step_completed` event before advancing:

- `stepId` and `stepType` are the current authored values;
- `result` is `success`;
- `detail.source` is `passive_runtime`;
- the dedup sequence uses the same stable negative per-step namespace as ESP-generated interactive
  completions and never advances the firmware progress cursor.

The event is guarded by current runtime, assignment, session, step ID, and step sequence. Republish,
retry, stale ACK, cancelled dwell, reconnect, duplicate callback, and terminal cleanup cannot emit a
second completion. Interactive behavior and firmware-originated completion handling remain unchanged.

## Parent Progress Child Identity

Assignments are correctly created for the child bound to the selected robot. Parent progress
screens read `household.activeChild`. A successful assignment therefore establishes the assigned
child as the active progress context before navigation:

1. resolve the robot-bound `effectiveChildId`;
2. create or resume the assignment for that child;
3. call `setActiveChild(effectiveChildId)` after success only;
4. invalidate the exact child progress query key;
5. navigate using the same child ID.

Failed, stale, offline, or conflicting assignments that are not resumed do not change the active
child. Manual child selection continues to work. Progress screens remain keyed by `activeChild`, so
there is one consistent household-wide child context rather than route-only special cases.

## Content Quality

The T5.4 acceptance lesson is republished with high-quality 480x320 background JPEGs, transparent
teaching-object PNGs sized for the declared rectangle, and Robot MJPEG MP4 assets for all required
effects. Literal placeholder prompts such as `try từ này`, `TeeBot will model it: từ này`, and
`guess: từ này!` are rejected from the acceptance content. Content fixes stay data-driven; runtime
does not hard-code lesson-specific Vietnamese words.

## Testing

Implementation follows RED-GREEN-REFACTOR per owning repository.

- ESP server: v5 exact parser, capability negotiation, checksum/pack projection, phase routing,
  passive completion exactly once, stale/reconnect guards, and v1-v4 non-regression.
- Firmware host tests: contract parsing, JPEG/PNG one-time decode, alpha and chroma pixels, Robot
  clock, frame-zero ACK, pause/resume/stop/cancel, allocation/decode/SD failures, and cleanup.
- Mobile: assignment success switches to the effective child and invalidates its progress; failure
  and unrelated conflicts preserve the previous child; progress screens query the switched child.
- Backend/content: published v5 manifest validation, exact three-slot shape, asset metadata,
  required phase vocabulary, and placeholder-copy rejection for the acceptance lesson.
- Physical: normal-distance trigger, audible audio, visible three-layer composition and every Robot
  effect, UART motion, nine distinct completed steps, backend assignment `COMPLETED`, Android progress
  visible within SLA, power-cycle recovery, terminal stop, and no degraded renderer marker.

## Release And Evidence

Each owning repository records the failing repro, minimal fix, and passing focused/full commands.
Deployable changes are merged through the plan's gate, deployed through the documented backend/ESP
and firmware procedures, and freshly smoked. The final physical capture and operator video are
archived under `robot/docs/evidence/` and linked from the T5.4 evidence document.

Only after the live verifier and every non-deferred deep-dive box pass is the T5.4 Ship checklist
run: re-verify at rebased tips, merge to main, deploy, re-test from throwaway main worktrees, remove
only clean merged worktrees, and set both status locations to `DONE`.

## Alternatives Rejected

### Three videos

This was the initial interpretation, but it wastes two continuous decoders on static content and
increases SD bandwidth, frame synchronization risk, and PSRAM pressure without improving the
approved visual result.

### Flatten all layers into one video

This matches v4 but loses independent high-quality scene/object assets and makes simple content
changes require regenerating the Robot animation. The operator explicitly chose independent image
layers.

### Treat images as one-frame videos

This preserves a superficially uniform schema but adds MP4 parsing and stream state for immutable
pixels, obscures validation, and creates avoidable lifecycle failure modes.

### Extend renderer v3 silently

Old v3 firmware would advertise support, receive a mixed-media manifest it does not understand, and
fail after admission. A new exact v5 capability makes compatibility observable and safe.
