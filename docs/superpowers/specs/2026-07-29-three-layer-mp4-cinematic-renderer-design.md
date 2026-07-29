# Direct Three-Layer MP4 Lesson Renderer Design

## Status

Approved simplified architecture. Asset encryption/decryption and per-device download authorization are removed. Firmware still decodes MP4 because compressed video must be decoded before it can be displayed.

## Goal

An administrator uploads or selects three public MP4 files for each cinematic lesson phase:

1. `background`
2. `teachingObject`
3. `robotOverlay`

The admin preview and the physical robot use the exact same MP4 bytes. The robot downloads the files directly from the Internet to SD and renders the effects represented by the admin `CINEMATIC DESIGN REFERENCE`.

## Simple Architecture

```text
Admin selects/uploads three robot-ready MP4 files
-> NestJS stores versioned asset rows and one phase manifest
-> public URLs are served directly from the configured TBOT asset host
-> ESP server forwards the manifest without media conversion
-> robot downloads the exact MP4 files to SD
-> firmware decodes and composites the three layers
```

There is no export worker, derivative-generation pipeline, signed URL, device claim, bearer token, cookie, or application-level encryption for cinematic files.

## Required MP4 Format

The uploaded file is already robot-ready:

| Property | Requirement |
| --- | --- |
| Container | MP4 |
| Video codec | Motion JPEG |
| Audio | none |
| Default FPS | 15 |
| Allowed fallback FPS | 10 |
| Background dimensions | 480x320 |
| Foreground dimensions | at most 240x240 |
| Frame dependency | every frame independently decodable |

The backend validates metadata when the asset is registered. It does not transcode or rewrite the file. Unsupported MP4 files are rejected with a clear admin error.

`teachingObject` and `robotOverlay` use a fixed chroma-key background. Their metadata includes key color, tolerance, feather radius, and destination rectangle.

## Phase Model

The initial phase set is:

```text
flyIn -> landFar -> settle -> walkToward -> greet
teach <-> listen <-> thinking
correct | nearMiss | incorrect | retry
celebrate -> completion
```

Every phase contains exactly three MP4 assets with equal `durationMs`, `fps`, and `frameCount`.

```json
{
  "phaseId": "flyIn",
  "durationMs": 3200,
  "fps": 15,
  "frameCount": 48,
  "layers": {
    "background": {
      "assetVersionId": "...",
      "url": "https://esp.tjbot.vn/lessons/.../background.mp4",
      "sdPath": "sd://tbot/lessons/.../background.mp4",
      "sha256": "...",
      "bytes": 123456
    },
    "teachingObject": {
      "assetVersionId": "...",
      "url": "https://esp.tjbot.vn/lessons/.../object.mp4",
      "sdPath": "sd://tbot/lessons/.../object.mp4",
      "sha256": "...",
      "bytes": 45678,
      "rect": { "x": 130, "y": 90, "width": 200, "height": 200 },
      "chromaKey": { "keyColor": "#00ff00", "tolerance": 20, "featherPx": 1 }
    },
    "robotOverlay": {
      "assetVersionId": "...",
      "url": "https://esp.tjbot.vn/lessons/.../robot.mp4",
      "sdPath": "sd://tbot/lessons/.../robot.mp4",
      "sha256": "...",
      "bytes": 56789,
      "rect": { "x": 160, "y": 70, "width": 220, "height": 220 },
      "chromaKey": { "keyColor": "#00ff00", "tolerance": 20, "featherPx": 1 }
    }
  }
}
```

SHA-256 and byte size remain mandatory. They ensure the file downloaded to SD is exactly the registered file and not a partial transfer. They are not access-control or encryption mechanisms.

## Admin Website

The lesson editor loads all three picker libraries from the existing NestJS versioned visual-asset API. It no longer fetches:

- `/backgrounds/backgrounds-manifest.json`
- `/teachobjects/teachobjects-manifest.json`

Each picker displays MP4 preview, title, key, version, FPS, dimensions, and status. Loading, empty, and error states remain visible instead of hiding the complete picker section.

Selection is persisted by `assetVersionId` through the existing step visual-reference endpoint. Reloading the lesson must restore all three selections.

The current production draft lesson `4053bc28-da88-4a1c-a4cb-a461c8cf1ca0` is an explicit acceptance case.

## Backend

The existing shared visual asset version remains the source of identity. MP4 technical metadata is stored in existing JSON compatibility metadata, avoiding a new media subsystem:

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

Foreground versions include `chromaKey`. The backend returns resolved public URLs and metadata to the admin.

Publish validation requires:

- exactly three slots for each required phase;
- `video/mp4` and `codec=mjpeg`;
- no audio;
- supported dimensions and FPS;
- equal duration, FPS, and frame count across the phase;
- published version IDs, URL, byte size, and SHA-256;
- chroma-key metadata for foreground layers.

The manifest checksum includes all phase and file identity fields. Changing an asset version changes the lesson checksum and triggers the existing global generation/SD synchronization workflow.

## Public Direct Download

- Asset URLs are public HTTP or HTTPS URLs.
- Robot sends no bearer token, device token, claim, cookie, signed query string, or decryption key.
- The same URL can be downloaded using a plain browser or `curl`.
- Firmware writes incoming bytes unchanged to a staging file.
- Firmware verifies byte count and SHA-256, then atomically renames the file into the lesson SD directory.
- Failed or incomplete downloads never replace the last verified file.

## ESP Server

The ESP server keeps its current responsibilities:

- fetch the published manifest;
- materialize/cache public MP4 bytes unchanged;
- construct the SD pack;
- wait for robot SD attestation;
- send phase commands only when firmware advertises the new renderer capability;
- own interactive branch selection and `lesson_completed` reporting.

The SD attestation accepts `downloadedCount`, `skippedCount`, optional `reusedCount`, and `failedCount` so verified duplicate bytes do not block readiness.

## Firmware

Firmware implements a deliberately constrained MP4 player:

- one MJPEG video track;
- no audio, fragments, edit lists, or multiple tracks;
- bounded MP4 metadata and sample tables;
- streamed reads from verified SD files;
- reusable JPEG workspace and PSRAM buffers;
- one master frame index shared by all three videos.

For each frame, firmware decodes background, teaching object, and robot overlay; chroma-keys the two foregrounds; composites into one RGB565 framebuffer; and presents once to TFT.

The three layers always advance together. If a frame misses its deadline, firmware repeats or drops the entire triplet rather than letting layers drift independently.

Pause freezes the shared clock. Resume rebases it. Stop/error closes all video files before releasing the lesson SD lease.

## Performance Gate

Fifteen FPS is the desired profile, not an unconditional promise. The current TFT bus and software rotation may limit full-screen throughput. Hardware tests measure sustained SD read, three JPEG decodes, composition, panel flush, heap, PSRAM, watchdog, tearing, and dropped triplets.

If 15 FPS cannot remain stable, the lesson must use uploaded 10 FPS MP4 files for all three layers. Firmware does not transcode or automatically create them.

## Fallback

- Missing or corrupt MP4: retry the public URL.
- Still unavailable: remain on a friendly preparation/error screen.
- Unsupported codec or insufficient memory: report a typed renderer failure and do not start the phase.
- Static fallback is allowed only when explicitly enabled and pinned in the manifest.
- No failure path may crash or watchdog-reset the robot.

## Testing and Release Gate

- Admin test: all three MP4 pickers load from NestJS and persist selection after reload.
- Backend test: reject invalid codec, audio, dimensions, timing mismatch, missing layer, or unpublished version.
- Direct-download test: plain unauthenticated GET returns exact bytes and matching SHA-256.
- SD test: all required files are verified and attested before lesson start.
- Firmware host tests: MP4 parser bounds, reusable JPEG decode, chroma-key pixels, shared clock, pause/resume/cancel, and safe errors.
- Hardware test: complete lesson with cinematic phases, no layer drift, crash, or watchdog reset.
- E2E: admin publish -> manifest checksum changes -> robot syncs SD -> firmware plays effects -> backend records `lesson_completed`.

## Acceptance Criteria

- The supplied production lesson displays usable background, teaching-object, and robot-overlay pickers.
- Admin and robot use the exact same MP4 bytes.
- Cinematic asset downloads are public and require no authentication or application decryption.
- The robot verifies and loads every required MP4 from SD before playback.
- The physical lesson shows the approved phase effects with three synchronized layers.
- The backend records lesson completion after the physical lesson ends.
