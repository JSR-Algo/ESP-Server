# Three-Layer MP4 Cinematic Renderer Design

## Status

Approved design. This document is the implementation-planning baseline for making the physical robot render the admin `CINEMATIC DESIGN REFERENCE` through three independent MP4 layers.

## Goal

Every lesson visual phase uses three synchronized MP4 assets:

1. `background`
2. `teachingObject`
3. `robotOverlay`

The admin preview, published manifest, SD asset pack, and physical 480x320 TFT must derive from one cinematic timeline. Fly-in, landing, walking, greeting, teaching, listening, thinking, feedback, retry, celebration, and completion must render as video phases rather than static overlays during the normal path.

## Current Production Gaps

- The production lesson editor requests `/backgrounds/backgrounds-manifest.json` and `/teachobjects/teachobjects-manifest.json`, but both URLs currently fall through to the admin SPA `index.html`.
- The production web image does not package or mount the background and teaching-object libraries.
- The affected lesson `4053bc28-da88-4a1c-a4cb-a461c8cf1ca0` is an editable `draft` at version 3, but the background and object pickers remain hidden because both client libraries normalize to empty arrays.
- The admin cinematic reference uses MP4 media and browser animation, while renderer v2 on firmware rejects authored video motion and renders static poster/overlay assets.
- Admin and firmware currently use different phase timings and geometry implementations, so visual parity cannot be claimed.

## Decisions

- All three authored visual layers are MP4.
- Robot derivatives also remain MP4 files.
- Robot MP4 files use Motion JPEG so the ESP32-S3 can decode independent frames without a software H.264 pipeline.
- `teachingObject` and `robotOverlay` use a fixed chroma-key background. Chroma-key parameters are versioned manifest data.
- Video is segmented by interactive phase rather than stored as one fixed full-lesson movie.
- Audio is not embedded in these visual MP4 files. Lesson speech and sound remain runtime-owned so interactive branches can change without seeking or resynchronizing embedded audio.
- The current `CINEMATIC DESIGN REFERENCE` timeline becomes the timing source of truth.
- Static posters are explicit fallback assets only. They are not used in the normal cinematic path.

## Non-Goals

- Decoding three simultaneous H.264 streams in software on ESP32-S3.
- Flattening the three authored layers into one robot video.
- Embedding lesson narration into visual MP4 files.
- Letting firmware infer lesson outcomes or choose branches.
- Allowing a missing video to be silently replaced by an unrelated or stale cached asset.
- Replacing the existing lesson assignment, completion, or parent-progress domains.

## Cinematic Phase Model

The initial supported phase set is:

```text
flyIn -> landFar -> settle -> walkToward -> greet
teach <-> listen <-> thinking
correct | nearMiss | incorrect | retry
celebrate -> completion
```

Each phase is a bundle containing exactly three synchronized files:

```text
phase: flyIn
|- background.mp4
|- teachingObject.mp4
`- robotOverlay.mp4
```

All three files in a bundle must have identical:

- `durationMs`
- `fps`
- `frameCount`
- `timelineEpoch`
- phase identity and version

The renderer changes phase only at a frame boundary. Interactive outcome selection remains owned by the ESP lesson runtime; firmware renders the phase requested by the server.

## Video Profiles

### Authoring Profile

The admin may use high-quality H.264 MP4 sources for review and editing. Authoring sources are immutable, versioned assets.

### ESP TFT Profile

The robot derivative contract is:

| Property | Requirement |
| --- | --- |
| Container | MP4 |
| Codec | Motion JPEG |
| Default FPS | 15 |
| Degraded derivative | 10 FPS |
| Background frame | 480x320 |
| Object frame | at most 240x240 |
| Robot overlay frame | at most 240x240 |
| Audio tracks | forbidden |
| Frame dependency | every frame independently decodable |

Object and robot-overlay videos are encoded over a fixed chroma-key color. The manifest pins `keyColor`, `tolerance`, and feather radius. Export validation rejects content whose foreground colors collide materially with the selected key color.

The initial cinematic timings are sourced from the reference timeline: 3.2 seconds for fly-in, a 0.8-second far beat, and 5 seconds for walking. The exporter records exact frame-derived durations; admin, backend, ESP server, and firmware must not redefine them independently.

## Asset and Manifest Contract

Each phase layer has both online and SD identities:

```json
{
  "phaseId": "flyIn",
  "phaseVersion": 1,
  "timelineEpoch": "tvideoFlyWalk.v2",
  "durationMs": 3200,
  "fps": 15,
  "frameCount": 48,
  "layers": {
    "background": {
      "assetKey": "scene.playground-park.flyIn@v3",
      "mediaType": "video/mp4",
      "codec": "mjpeg",
      "onlineUrl": "https://cdn.example/lesson-visuals/scene.playground-park/flyIn-v3.mp4",
      "sdPath": "sd://lessons/shared/scene.playground-park/flyIn-v3.mp4",
      "sha256": "...",
      "bytes": 123456
    },
    "teachingObject": {
      "assetKey": "object.robot.flyIn@v2",
      "mediaType": "video/mp4",
      "codec": "mjpeg",
      "onlineUrl": "https://cdn.example/lesson-visuals/object.robot/flyIn-v2.mp4",
      "sdPath": "sd://lessons/shared/object.robot/flyIn-v2.mp4",
      "sha256": "...",
      "bytes": 45678,
      "rect": { "x": 150, "y": 92, "width": 180, "height": 180 },
      "chromaKey": { "keyColor": "#00ff00", "tolerance": 20, "featherPx": 1 }
    },
    "robotOverlay": {
      "assetKey": "robotOverlay.alive.flyIn@v4",
      "mediaType": "video/mp4",
      "codec": "mjpeg",
      "onlineUrl": "https://cdn.example/lesson-visuals/robotOverlay.alive/flyIn-v4.mp4",
      "sdPath": "sd://lessons/shared/robotOverlay.alive/flyIn-v4.mp4",
      "sha256": "...",
      "bytes": 56789,
      "rect": { "x": 170, "y": 80, "width": 200, "height": 200 },
      "chromaKey": { "keyColor": "#00ff00", "tolerance": 20, "featherPx": 1 }
    }
  }
}
```

The backend rejects publish when:

- a required phase is absent;
- a phase does not contain exactly the three required layers;
- the three assets disagree on FPS, duration, frame count, phase version, or timeline epoch;
- a robot derivative is not MP4/Motion JPEG;
- an audio track is present;
- online URL, SD path, SHA-256, byte size, geometry, or chroma-key metadata is missing or invalid;
- the selected asset version is not published;
- the reference timeline and exported timing differ by more than one frame.

## Admin Website

The asset pickers must use the backend versioned visual library as their source of truth. They must not depend on manually deployed static JSON manifests.

For each layer the editor provides:

- video thumbnail/hover preview;
- title, asset key, version, profile, codec, FPS, dimensions, and publication state;
- compatibility and chroma-key warnings;
- persisted selection through `assetVersionId` on the lesson draft;
- an explicit loading, empty, or API-error state rather than silently hiding the picker.

The `CINEMATIC DESIGN REFERENCE` player uses the same phase manifest that will be published. It displays all three video layers, the active phase, frame number, timing, and selected asset identities. Reloading the editor must reproduce the persisted selection.

The current production picker bug is fixed as part of this work by removing the unavailable `/backgrounds/backgrounds-manifest.json` and `/teachobjects/teachobjects-manifest.json` dependency. The production image must still serve any preview media referenced by backend storage URLs.

## Backend and Export Pipeline

The backend owns immutable asset identity and the normalized cinematic timeline. A generation/export worker:

1. accepts the three approved authoring MP4 sources for a phase;
2. validates duration, geometry, foreground/key-color collision, and allowed content;
3. transcodes the ESP derivatives to MP4/Motion JPEG at 15 FPS and 10 FPS;
4. validates exact frame counts and the absence of audio tracks;
5. calculates SHA-256 and byte size;
6. stores immutable files under versioned keys;
7. publishes the derivative metadata atomically only after all three layers pass;
8. invalidates affected lesson generation so robots receive a new manifest/checksum.

Partial phase publication is forbidden. A failed layer leaves the previous published phase version active.

## SD Synchronization

The existing SD-first asset flow is extended to accept versioned MP4/Motion JPEG assets.

- Firmware or the ESP server downloads every required layer before lesson start.
- Downloads use staging files, size limits, SHA-256 verification, and atomic rename.
- A cached asset is reused only when its pinned identity and SHA-256 match.
- A manifest/checksum change automatically schedules new assets regardless of claim state, while transport authentication follows the separately approved simple-security policy.
- The lesson does not enter `READY` until all critical cinematic video assets are attested.
- Online URLs remain available as recovery sources, but normal playback reads from SD.

## Firmware Renderer

Firmware owns decoding, chroma-key compositing, and TFT presentation. For each render tick it:

1. decodes the current background frame into the 480x320 framebuffer;
2. decodes the current teaching-object frame into a bounded scratch buffer;
3. removes chroma-key pixels and composites the object into its manifest rectangle;
4. reuses the scratch buffer for the robot-overlay frame;
5. removes chroma-key pixels and composites the robot overlay;
6. presents the completed framebuffer to TFT;
7. advances the shared frame clock.

The implementation must not retain three full decoded frames simultaneously. Decoder and scratch-buffer allocation occurs during lesson prepare, not in the frame loop. The main watchdog is fed during decode and SD reads.

The renderer advertises capabilities including MP4/Motion JPEG support, maximum layer dimensions, supported FPS values, chroma-key support, and maximum concurrent cinematic layers. The ESP server sends this contract only to devices that explicitly advertise all required capabilities.

## Runtime Synchronization

The ESP server remains the owner of interaction and phase selection. Runtime commands identify:

- lesson session and generation;
- step and visual sequence;
- `phaseId` and phase version;
- common starting frame and monotonic start time;
- the three pinned layer identities.

Firmware ACKs phase installation only after all files are open, headers are validated, decoder buffers are ready, and frame zero can be decoded. Duplicate commands for the same generation and visual sequence are idempotent.

The three layers share one integer frame counter. There are no independent video clocks, which prevents cumulative drift.

## Error Handling and Degradation

- Missing or corrupt critical video triggers online retry and SHA-256 verification.
- While assets are being repaired, the robot remains on a friendly preparation screen and does not start an incomplete cinematic phase.
- Decoder overload first selects the published 10 FPS derivative for all three layers together.
- A single layer may not independently drop frames or change FPS.
- If no compatible derivative is available, the renderer stops the phase safely and reports a typed failure; it must not crash, reset, or continue with mismatched layers.
- Static fallback is used only when the manifest explicitly sets `allowStaticFallback: true` and pins the fallback identities.
- Runtime diagnostics expose safe counters and error codes without logging tokens, device identifiers, Wi-Fi data, or private lesson content.

## Security and Resource Limits

- Only HTTPS online URLs from the configured lesson asset origins are accepted.
- Redirects are revalidated against the origin allowlist.
- Every file has per-asset and per-pack byte limits.
- MP4 metadata is bounded before allocation; invalid dimensions, frame count, codec, or atom sizes are rejected.
- SD paths are normalized and contained within the lesson asset root.
- Credentials are never stored in lesson manifests or asset URLs.

## Testing

### Admin

- Changing background, teaching object, and robot overlay persists after reload.
- Loading, empty, and error states remain visible and actionable.
- The affected production draft lesson displays usable pickers.
- The cinematic preview consumes the normalized three-layer phase manifest.

### Backend and Export

- Contract tests require exactly three synchronized MP4 layers per phase.
- Publish tests reject timing, codec, checksum, identity, geometry, audio-track, or chroma-key violations.
- Export tests prove the 15 FPS and 10 FPS derivatives have exact frame counts.
- Regeneration tests prove a new asset version changes lesson checksum and SD pack identity.

### ESP Server

- Runtime tests verify capability negotiation, phase command projection, retries, idempotency, typed failures, and completion recording.
- SD tests verify staging, limits, SHA-256, atomic commit, reuse, and updated-asset fanout.

### Firmware

- Native tests cover MP4 parsing, JPEG frame decode, chroma-key tolerance, clipping, compositing order, shared frame clock, phase transitions, downgrade, cancellation, and watchdog feeding.
- Host fixtures include malformed and adversarial MP4 metadata.
- Hardware tests run a complete lesson repeatedly with heap, PSRAM, frame-time, SD latency, and watchdog monitoring.

### Visual Parity

Golden frames are captured from the normalized cinematic timeline and the physical TFT at fixed phase/frame markers.

- position tolerance: at most 2 pixels;
- timing tolerance: at most one frame;
- layer order and clipping: exact;
- normal path: no static replacement;
- no cumulative phase drift.

### End to End

The release gate is:

```text
admin selects and publishes three-layer MP4 visuals
-> backend exports and publishes robot derivatives
-> latest manifest and checksum fan out
-> robot syncs all assets to SD
-> firmware plays the complete interactive cinematic lesson
-> lesson completion reaches the backend
-> backend records lesson_completed
```

## Rollout

1. Land the backend asset and manifest contract behind a renderer capability/version gate.
2. Fix the admin picker data source and add three-layer authoring/preview support.
3. Add exporter derivatives and validation without advertising them to current firmware.
4. Add firmware MP4/Motion JPEG decode and compositing behind a disabled capability flag.
5. Enable one hardware allowlisted robot and run repeated visual-parity and endurance tests.
6. Enable new lessons only after the release gate passes.
7. Keep renderer v1/static manifests available for older firmware until explicitly retired.

## Acceptance Criteria

- Background, object, and robot-overlay pickers work on production for editable drafts.
- Every normal cinematic phase uses exactly three synchronized MP4 assets.
- The physical output uses effects equivalent to the admin `CINEMATIC DESIGN REFERENCE`.
- Admin and firmware consume one timing and geometry source of truth.
- New admin asset versions automatically produce a new manifest/checksum and new SD synchronization.
- A complete physical lesson runs without crash, watchdog reset, layer drift, or stale assets.
- The backend records `lesson_completed` after the physical session finishes.
