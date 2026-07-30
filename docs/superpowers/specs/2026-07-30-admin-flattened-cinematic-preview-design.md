# Admin Flattened Cinematic Preview Design

**Date:** 2026-07-30

## Goal

Add a browser-only flattened preview to the admin Lesson Editor so an author can compare the existing three-layer renderer-v3 cinematic with a single `480x320` composite resembling the media derivative a robot-oriented build pipeline may generate later.

This slice validates composition and authoring ergonomics only. It does not generate or persist a video, change publish output, modify backend APIs, or change firmware.

## User Experience

For a renderer-v3 `directMp4Cinematic` manifest, the exact robot preview area shows two synchronized panels:

1. **3 Layers** - the existing layer-based ESP TFT preview.
2. **Robot Flattened** - a canvas composite at the exact `480x320` geometry.

The panels remain side by side on desktop and stack on narrow screens. Shared play, pause, and replay controls keep both views on the same phase clock. Authors can compare crop, placement, chroma-key edges, and synchronization without publishing.

Renderer-v1 and renderer-v2 lessons retain the existing preview unchanged.

## Architecture

### Existing Preview

`RobotEspTftProjectionPreview.vue` remains the authoritative three-layer browser projection. It continues to use `projectEspTftPreview()` and `CinematicVideoLayer.vue` for individual MP4 layers.

### Flattened Preview

Add `FlattenedCinematicPreview.vue` under `manager-web/src/components/lesson/`. It receives the already-normalized projection instead of interpreting the manifest independently. This keeps geometry, visibility, media identity, and chroma metadata owned by `robot-preview-projection.js`.

The component owns:

- one visible `480x320` canvas;
- hidden video elements for the visible MP4 cinematic layers;
- one animation loop driven by `requestAnimationFrame`;
- a shared phase clock derived from the background video when present;
- frame composition in z-order: background, teaching object, robot overlay;
- chroma-key alpha processing for foreground layers;
- loading, degraded, and cross-origin failure states.

Static image layers may be drawn into the same canvas when present, but lesson UI overlays such as prompt text, progress dots, and safe-zone diagnostics remain outside this first slice. The comparison is specifically for the three cinematic media layers that would be baked into one derivative.

### Composition Logic

Extract pure helpers into `flattened-cinematic-preview.js`:

- select supported cinematic layers from a projection;
- normalize and sort layer geometry;
- determine the master clock;
- decide whether a secondary video requires resynchronization;
- apply chroma-key alpha to an `ImageData` buffer;
- report deterministic unsupported or incomplete states.

The Vue component handles DOM/video/canvas lifecycle. The pure module contains behavior that can be tested without a browser media decoder.

## Playback And Synchronization

- Background is the preferred master clock; otherwise the first visible MP4 layer is master.
- All videos are muted, inline, preloaded, and explicitly controlled rather than independently autoplaying.
- Secondary videos are corrected when their time differs from the master by more than 80 ms.
- Replay seeks all videos to zero before playback resumes.
- The canvas updates only when at least one source frame changes.
- A component destroy or source change cancels the animation frame and pauses all owned videos.

This preview favors deterministic visual comparison over audio or lesson-runtime control fidelity.

## Error Handling

- Missing required cinematic layers produce an explicit incomplete-preview message.
- A failed or unsupported source identifies the affected layer.
- Canvas security failures caused by missing CORS headers stop flattened rendering and explain that the layer preview remains available.
- One failed flattened preview never disables editing, validation, the existing exact preview, or publish controls.
- Stale asynchronous media events are ignored after projection/source changes.

## Lesson Editor Integration

Update the exact-renderer preview surface in `LessonEditor.vue` to use a responsive comparison wrapper for renderer v3. The left panel contains the existing `RobotLessonPreview`; the right panel contains `FlattenedCinematicPreview`.

The integration must not duplicate preview generation requests. Both panels consume the same `previewManifest` and projected state.

## Testing

### Unit Tests

- selects only visible background, teaching-object, and robot-overlay media;
- preserves z-order and exact bounds;
- chooses the correct master clock;
- resynchronizes only beyond the 80 ms tolerance;
- applies tolerance and feather values to chroma-key alpha;
- reports incomplete and unsupported inputs deterministically.

### Component And Contract Tests

- renderer v3 shows both comparison panels;
- renderer v1/v2 does not show the flattened panel;
- play, pause, replay, source changes, and destroy clean up media and animation handles;
- CORS/readback failure shows a non-blocking error;
- the existing exact renderer remains present and unchanged.

### Browser Verification

- open a renderer-v3 lesson manifest preview;
- confirm both panels render at a `3:2` aspect ratio and remain usable on desktop and narrow widths;
- verify foreground position and chroma edges match between panels;
- replay repeatedly and confirm visible synchronization does not drift;
- confirm no uncaught browser errors or leaked animation loops.

## Acceptance Criteria

1. A renderer-v3 direct-MP4 lesson shows side-by-side `3 Layers` and `Robot Flattened` previews in the admin Lesson Editor.
2. The flattened canvas is exactly `480x320` internally and uses the same normalized layer bounds and chroma metadata as the existing projection.
3. The three MP4 sources remain synchronized within an 80 ms correction threshold during preview playback.
4. Replay starts every source from the beginning and repeated replay does not create duplicate animation loops.
5. Missing media or CORS failure produces a clear, non-blocking error while the existing preview remains usable.
6. Renderer-v1 and renderer-v2 lesson behavior and layout do not regress.
7. Unit, component contract, and manager-web build checks pass.

## Out Of Scope

- FFmpeg or server-side media rendering;
- recording or downloading the canvas preview;
- generating MJPEG MP4 derivatives;
- changing lesson publish payloads, checksums, manifests, or asset budgets;
- uploading flattened media to storage;
- firmware playback changes;
- flattening lesson UI overlays or physical robot motion.
