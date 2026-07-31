# Published Flattened MJPEG Cinematic Design

**Date:** 2026-07-31

## Status

Approved direction: option 1, generate a robot-specific flattened MJPEG MP4 derivative while preserving the three editable source layers in admin.

## Goal

Let an author keep selecting and editing three cinematic video layers while the publish pipeline produces one deterministic `480x320` MJPEG MP4 that the robot can download and play with substantially lower decode and composition cost.

GIF is not part of the design. It has weaker color, timing, seeking, disposal-frame, and size characteristics for this workload, while the firmware already has a bounded MJPEG MP4 parser and reusable JPEG decoder.

## Why Flatten

The existing renderer-v3 firmware does not retain three decoded full-screen frames. It decodes the three streams sequentially into one framebuffer and one foreground scratch buffer, so its active PSRAM requirement is approximately 1.45-1.6 MB plus bounded overhead and fits the required ESP32-S3 N16R8 target.

The primary risk is throughput, not capacity: every displayed frame currently requires three JPEG decodes, two chroma-key passes, composition, and a TFT flush. A flattened stream reduces playback to one JPEG decode and one TFT presentation per frame, removes the foreground scratch buffer, and eliminates runtime layer drift.

## Selected Architecture

```text
Admin edits three normal MP4 source layers
-> source revision requests an asynchronous derivative build
-> worker downloads the exact pinned source versions
-> FFmpeg composes bounds, object-fit, z-order, and chroma key
-> worker emits one 480x320, 10 FPS, no-audio MJPEG MP4
-> ffprobe, byte count, SHA-256, and deterministic identity are verified
-> publish waits until the derivative for the current source revision is ready
-> global lesson generation exposes the flattened asset
-> ESP server syncs one verified file to SD
-> firmware plays the single MJPEG stream
```

The derivative is requested when a cinematic source selection or composition setting changes. Publish does not start a duplicate encode; it blocks with a visible `processing`, `failed`, or `stale` status until the derivative for the exact current revision is verified. This keeps the publish click fast when the author has already waited for preview processing, while still failing closed if the current derivative is unavailable.

## Versioned Contract

Add a new exact renderer identity instead of silently changing renderer-v3:

- manifest/protocol: `teebot-lesson-renderer.v4`;
- template: `flattenedMjpegCinematic` version 1;
- feature: `lessonRendererV4.flattenedMjpegCinematic=true`;
- asset source: `publishedFlattenedDerivative`.

Renderer-v3 remains a compatibility identity during migration. A v4-capable robot consumes only the flattened asset. A robot that advertises v3 but not v4 may receive the existing three-layer renderer-v3 manifest when the lesson explicitly retains compatibility output. The server must never relabel a three-layer pack as v4 or send a v4 command to a v3-only device.

Each v4 phase contains one asset:

```json
{
  "templateId": "flattenedMjpegCinematic",
  "templateVersion": 1,
  "phaseId": "opening",
  "timing": { "durationMs": 9000 },
  "asset": {
    "derivativeId": "sha256-of-build-identity",
    "path": "lessons/derivatives/.../opening.mp4",
    "url": "https://.../opening.mp4",
    "sha256": "64-lowercase-hex",
    "bytes": 1234567,
    "mediaType": "video/mp4",
    "width": 480,
    "height": 320,
    "metadata": {
      "codec": "mjpeg",
      "fps": 10,
      "durationMs": 9000,
      "frameCount": 90,
      "hasAudio": false
    }
  }
}
```

All fields are parsed with exact-key validation. Width, height, codec, FPS, frame count, duration, audio absence, positive bytes, public resolved URL, and SHA-256 are mandatory.

## Source And Derivative Identity

Admin source assets may remain normal browser-friendly MP4, including H.264. They are authoring inputs and are not required to satisfy the robot MJPEG profile.

Each draft lesson has a positive monotonic `cinematicSourceRevision`. The backend increments it in the same transaction as any cinematic layer selection, phase timing, destination rectangle, chroma setting, or other composition mutation. Timestamps are not revision identities. Cloning a new draft initializes its revision from the copied cinematic state and subsequent mutations increment only the new draft.

Source asset compatibility metadata is separate from the robot-output metadata contract. A cinematic source MP4 records its probed codec, width, height, duration, frame count, frame-rate numerator/denominator, audio presence, destination rectangle, and optional chroma key. H.264 and MJPEG source codecs are supported initially; the flattened output remains constrained to MJPEG. The pinned asset-version UUID, logical version ID, SHA-256, and byte size are all part of the source descriptor.

Object-fit is deterministic rather than another free authoring field in the first release: background uses `cover`; teaching-object and robot-overlay use `contain`. These derived values are included in the canonical identity and used identically by admin preview and FFmpeg.

The derivative build identity is a canonical SHA-256 over:

- renderer/template/encoder profile versions;
- phase ID and exact duration;
- source asset version IDs, source SHA-256 values, and source technical metadata;
- normalized z-order, destination rectangles, and object-fit mode;
- chroma key color, tolerance, and feather values;
- output width, height, FPS, pixel format, and MJPEG quality setting.

Changing any input creates a different derivative identity and output path. The worker may reuse an already verified derivative with the same identity, but it must not reuse output based only on lesson ID or phase ID.

## Media Worker

The existing lesson asset generation worker remains responsible for generation leasing, stale-source fencing, retry, and atomic commit. Media composition is isolated behind a derivative builder interface so manifest generation can be tested without invoking FFmpeg.

The production implementation invokes a pinned FFmpeg/ffprobe executable profile. It must:

- read only the three pinned source versions for the leased revision;
- normalize timestamps and loop/trim inputs to the exact phase duration;
- scale and crop using the same object-fit geometry as the admin preview;
- apply chroma key to the teaching-object and robot layers;
- overlay in background, teaching-object, robot order;
- output `480x320`, 10 FPS, MJPEG, no audio, independently decodable frames;
- write to a temporary path and atomically promote only after verification;
- terminate promptly on worker abort or application shutdown.

Before invoking FFmpeg, a trusted source materializer resolves each pinned storage path to an HTTPS URL whose origin is present in `FLATTENED_CINEMATIC_SOURCE_ORIGINS`. It disables credentials and cross-origin redirects, downloads to a worker-owned cache root with bounded size/time, verifies the registered byte count and SHA-256, and atomically promotes the verified local source. A cache hit is reusable only when the complete pinned identity matches. Local mounted storage may be supported through a separately configured realpath-confined root. Arbitrary public URL fetching is forbidden.

The first release is fixed at 10 FPS. Fifteen FPS is a later encoder profile and requires real-device soak evidence before promotion.

## Publish State And Admin UX

For each cinematic phase, admin shows one derivative state:

- `not-requested`: current inputs have no build request yet;
- `processing`: a current-revision job is queued or leased;
- `ready`: verified derivative identity matches current inputs;
- `failed`: current-revision build failed with an actionable error;
- `stale`: a prior derivative exists but does not match current inputs.

Changing a layer or composition setting immediately marks the old derivative stale and requests a new build. The existing `3 Layers` and `Robot Flattened` browser comparison remains available while processing. Publish is enabled only when every required phase is `ready` for the exact draft revision. A failure does not delete or replace the last published verified lesson version.

## Generation And SD Sync

The global lesson asset generation checksum includes the v4 manifest and flattened asset identity. The pack contains one cinematic file per phase instead of three.

The ESP server continues to download public bytes, verify byte count and SHA-256, stage to a temporary file, and atomically rename after verification. Existing generation leases, stale-generation rejection, reuse by checksum, and SD attestation remain in force. A partial or corrupt v4 download cannot replace a verified cached file or activate the new generation.

## Firmware

Add a single-stream cinematic mode that reuses the existing constrained MJPEG MP4 parser, JPEG workspace, timing, pause/resume/cancel lifecycle, and typed error reporting.

The v4 renderer allocates:

- one `480x320` RGB565 framebuffer;
- one bounded JPEG input buffer;
- one reusable JPEG decoder workspace;
- bounded MP4 sample tables and file state.

It does not allocate the foreground scratch buffer and does not run chroma composition. Each target frame maps to one MP4 sample. Missed deadlines drop or repeat a complete flattened frame. Pause freezes the clock; resume rebases it; stop/error closes the file before releasing the SD lease.

The existing renderer-v3 code remains available during migration and shares parser/decoder primitives. V4 capability advertisement is separate and is emitted only when the single-stream production renderer initializes successfully on the supported PSRAM target.

## Error Handling

- Source download/probe failure: derivative remains failed; publish stays blocked.
- Unsupported source timeline or media: return a phase/layer-specific authoring error.
- FFmpeg exit, timeout, or abort: remove temporary output and retry only under bounded worker policy.
- Source revision changes during build: stale job cannot commit and is safely discarded or reused only by its exact identity.
- Verification mismatch: never promote the derivative.
- Publish during processing/stale/failed state: reject with a typed conflict containing phase statuses.
- Robot missing/corrupt asset: retain the previous verified generation and report typed sync failure.
- Unsupported v4 capability: use explicit v3 compatibility output when available; otherwise do not start the lesson.
- Decode, PSRAM, or file failure: show a friendly robot error and never crash or watchdog-reset.

## Security And Operations

- FFmpeg arguments are constructed from validated values, never shell-concatenated user strings.
- Inputs and outputs are restricted to configured storage roots and public asset URL policy.
- Worker concurrency is bounded because video encoding is CPU and disk intensive.
- Logs include derivative identity, lesson/version/phase, attempt, elapsed time, input bytes, output bytes, and normalized failure code, but not secrets.
- Temporary files are uniquely named and cleaned after success, failure, abort, and startup recovery.

## Testing

### Backend Unit And Integration

- canonical build identity changes for every source, geometry, chroma, timing, and encoder input;
- exact v4 contract accepts only verified `480x320` 10 FPS MJPEG/no-audio assets;
- stale jobs cannot overwrite a newer revision;
- ready derivative reuse is identity-based;
- publish rejects processing, failed, missing, or stale phases;
- failed builds preserve the last published verified version;
- FFmpeg argument construction and ffprobe parsing are covered without a shell;
- a small real fixture produces the expected metadata, checksum, and representative frame pixels.

### ESP Server

- v4 pack discovery includes one cinematic asset per phase;
- checksum reuse, staging, verification, atomic activation, and attestation pass;
- v3 and v4 capability routing is exact and fail-closed;
- corrupt or partial flattened media never activates.

### Firmware Host

- v4 manifest parsing rejects extra/missing/invalid fields;
- single-stream prepare/play/pause/resume/replay/cancel lifecycle passes;
- sample timing and dropped-frame behavior are deterministic;
- buffer allocation, file closure, and repeated phase cleanup do not leak;
- renderer-v3 regression tests remain green.

### Browser And End To End

- admin source edit marks the derivative stale, then shows processing and ready;
- publish is blocked until all current derivatives are ready;
- browser comparison remains synchronized and usable during processing;
- publish changes the generation checksum;
- robot syncs one flattened file, plays the phase, and completion reaches the backend.

### Hardware Release Gate

On an ESP32-S3 N16R8 robot, run repeated 10 FPS phases and a full lesson while recording:

- frame deadlines and dropped/repeated frames;
- TFT flush time;
- free/largest internal heap and PSRAM trend;
- watchdog, reset, decode, file, and SD error markers;
- pause/resume/cancel and generation rollback behavior.

Ten FPS is production-approved only after the attended run shows smooth subjective playback, no crash/watchdog reset, no monotonic leak beyond the existing bound, and no corrupted activation. Fifteen FPS remains disabled until a separate soak passes.

## Migration

1. Add v4 contracts and derivative storage without changing v3 behavior.
2. Generate v4 derivatives and expose admin readiness while continuing to publish v3 compatibility output.
3. Add ESP sync and firmware v4 capability behind exact negotiation.
4. Run software E2E and attended hardware proof at 10 FPS.
5. Prefer v4 for capable robots after proof; retain v3 rollback until a later removal decision.

## Acceptance Criteria

1. Authors edit the same three source layers and see their synchronized layered/flattened browser preview.
2. Current H.264 source assets can be used as inputs without manual GIF or MJPEG preparation.
3. Every publishable cinematic phase has one verified `480x320`, 10 FPS, no-audio MJPEG MP4 derivative tied to the exact current source revision.
4. Publish fails closed for missing, processing, failed, stale, or mismatched derivatives and preserves the last verified published version.
5. V4-capable robots download and play one flattened file per phase; v3 remains an explicit compatibility fallback.
6. Software tests and builds pass across backend, ESP server, manager admin, and firmware.
7. Production readiness is not claimed until real-device playback and memory/watchdog evidence pass.

## Out Of Scope

- GIF generation or playback;
- arbitrary resolution/FPS/codec profiles;
- client-side browser recording as the authoritative derivative;
- audio tracks;
- removing renderer-v3 in the first release;
- claiming 15 FPS before hardware validation.
