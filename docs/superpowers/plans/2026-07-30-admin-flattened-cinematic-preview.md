# Admin Flattened Cinematic Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a synchronized side-by-side admin preview comparing renderer-v3 source layers with a flattened `480x320` canvas composite without changing publish output or firmware.

**Architecture:** Keep `projectEspTftPreview()` as the only manifest-to-layer projection. Add a pure composition/synchronization module, a focused canvas Vue component, and a renderer-v3 comparison shell inside the existing exact ESP TFT preview. A parent-owned cinematic clock drives both the existing video layers and the flattened preview so their frames can be compared directly.

**Tech Stack:** Vue 2, JavaScript ES modules, Canvas 2D, HTMLVideoElement, Node assertion scripts, Vue CLI/Webpack.

---

## File Map

- Create `main/manager-web/src/components/lesson/flattened-cinematic-preview.js` - pure layer selection, clock, resync, fit geometry, and chroma-key helpers.
- Create `main/manager-web/src/components/lesson/FlattenedCinematicPreview.vue` - hidden media lifecycle and visible flattened canvas.
- Modify `main/manager-web/src/components/lesson/CinematicVideoLayer.vue` - optional external clock/playback control for the existing three-layer panel.
- Modify `main/manager-web/src/components/lesson/RobotEspTftProjectionPreview.vue` - renderer-v3 comparison layout and shared cinematic playback clock.
- Create `main/manager-web/scripts/check-flattened-cinematic-preview.mjs` - executable behavior and Vue wiring contract tests.
- Modify `main/manager-web/package.json` - expose the focused test command and include it in lesson-studio verification.
- Modify `main/manager-web/scripts/check-robot-lesson-preview.mjs` - protect renderer-v1/v2 behavior and renderer-v3 comparison integration.

### Task 1: Pure Flattening And Synchronization Contract

**Files:**
- Create: `main/manager-web/src/components/lesson/flattened-cinematic-preview.js`
- Create: `main/manager-web/scripts/check-flattened-cinematic-preview.mjs`
- Modify: `main/manager-web/package.json`

- [ ] **Step 1: Write the failing helper contract test**

Create `scripts/check-flattened-cinematic-preview.mjs` with assertions for supported layer selection, order, fit geometry, clock selection, resync tolerance, and chroma alpha:

```js
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const helperSource = await readFile(
  new URL('src/components/lesson/flattened-cinematic-preview.js', root),
  'utf8',
);
const helper = await import(`data:text/javascript;base64,${Buffer.from(helperSource).toString('base64')}`);

const projection = {
  manifestVersion: 'teebot-lesson-renderer.v3',
  stage: { width: 480, height: 320 },
  layers: [
    { id: 'prompt', z: 40, visible: true, text: 'Barn' },
    { id: 'robotOverlay', z: 20, visible: true, mediaType: 'video/mp4', src: 'robot.mp4', bounds: { x: 260, y: 100, width: 200, height: 200, fit: 'contain' }, chromaKey: { color: { r: 255, g: 255, b: 255 }, tolerance: 10, feather: 8 } },
    { id: 'background', z: 0, visible: true, mediaType: 'video/mp4', src: 'background.mp4', bounds: { x: 0, y: 0, width: 480, height: 320, fit: 'cover' }, chromaKey: null },
    { id: 'teachingObject', z: 10, visible: true, mediaType: 'video/mp4', src: 'object.mp4', bounds: { x: 30, y: 110, width: 200, height: 200, fit: 'contain' }, chromaKey: { color: { r: 0, g: 255, b: 0 }, tolerance: 12, feather: 6 } },
  ],
};

assert.deepEqual(helper.flattenableLayers(projection).map((layer) => layer.id), [
  'background', 'teachingObject', 'robotOverlay',
]);
assert.equal(helper.isFlattenableProjection(projection), true);
assert.equal(helper.chooseMasterLayer(helper.flattenableLayers(projection)).id, 'background');
assert.equal(helper.shouldResyncVideo(1.00, 1.079), false);
assert.equal(helper.shouldResyncVideo(1.00, 1.081), true);
assert.deepEqual(
  helper.objectFitRect(640, 480, { x: 0, y: 0, width: 480, height: 320, fit: 'cover' }),
  { sx: 0, sy: 26.666666666666657, sw: 640, sh: 426.6666666666667, dx: 0, dy: 0, dw: 480, dh: 320 },
);

const pixels = new Uint8ClampedArray([0, 255, 0, 255, 0, 270 > 255 ? 255 : 270, 20, 255]);
helper.applyChromaKey(pixels, { color: { r: 0, g: 255, b: 0 }, tolerance: 10, feather: 20 });
assert.equal(pixels[3], 0);
assert.ok(pixels[7] > 0 && pixels[7] < 255);

console.log('flattened cinematic preview helpers PASS');
```

Add the script entry:

```json
"test:flattened-cinematic-preview": "node scripts/check-flattened-cinematic-preview.mjs"
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
export PATH=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH
node scripts/check-flattened-cinematic-preview.mjs
```

Expected: FAIL because `src/components/lesson/flattened-cinematic-preview.js` does not exist.

- [ ] **Step 3: Implement the minimal pure helpers**

Create the helper module with this public contract:

```js
export const CINEMATIC_LAYER_IDS = ['background', 'teachingObject', 'robotOverlay'];
export const CINEMATIC_SYNC_TOLERANCE_SEC = 0.08;

export function flattenableLayers(projection) {
  const layers = Array.isArray(projection && projection.layers) ? projection.layers : [];
  return layers
    .filter((layer) => CINEMATIC_LAYER_IDS.includes(layer.id) && layer.visible && layer.mediaType === 'video/mp4' && layer.src)
    .sort((left, right) => Number(left.z || 0) - Number(right.z || 0));
}

export function isFlattenableProjection(projection) {
  return projection && projection.manifestVersion === 'teebot-lesson-renderer.v3'
    && flattenableLayers(projection).length === CINEMATIC_LAYER_IDS.length;
}

export function chooseMasterLayer(layers) {
  return layers.find((layer) => layer.id === 'background') || layers[0] || null;
}

export function shouldResyncVideo(masterTime, videoTime, tolerance = CINEMATIC_SYNC_TOLERANCE_SEC) {
  return Number.isFinite(masterTime) && Number.isFinite(videoTime)
    && Math.abs(masterTime - videoTime) > tolerance;
}

export function objectFitRect(sourceWidth, sourceHeight, bounds) {
  const fit = bounds.fit || 'fill';
  if (fit === 'fill' || !sourceWidth || !sourceHeight) {
    return { sx: 0, sy: 0, sw: sourceWidth, sh: sourceHeight, dx: bounds.x, dy: bounds.y, dw: bounds.width, dh: bounds.height };
  }
  const scale = fit === 'cover'
    ? Math.max(bounds.width / sourceWidth, bounds.height / sourceHeight)
    : Math.min(bounds.width / sourceWidth, bounds.height / sourceHeight);
  const sw = bounds.width / scale;
  const sh = bounds.height / scale;
  return {
    sx: (sourceWidth - sw) / 2,
    sy: (sourceHeight - sh) / 2,
    sw,
    sh,
    dx: bounds.x,
    dy: bounds.y,
    dw: bounds.width,
    dh: bounds.height,
  };
}

export function applyChromaKey(data, chromaKey) {
  if (!data || !chromaKey || !chromaKey.color) return data;
  const { r, g, b } = chromaKey.color;
  const tolerance = Math.max(0, Number(chromaKey.tolerance) || 0);
  const feather = Math.max(1, Number(chromaKey.feather) || 1);
  for (let offset = 0; offset < data.length; offset += 4) {
    const distance = Math.max(Math.abs(data[offset] - r), Math.abs(data[offset + 1] - g), Math.abs(data[offset + 2] - b));
    if (distance <= tolerance) data[offset + 3] = 0;
    else if (distance < tolerance + feather) data[offset + 3] = Math.round(255 * (distance - tolerance) / feather);
  }
  return data;
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run `node scripts/check-flattened-cinematic-preview.mjs`.

Expected: `flattened cinematic preview helpers PASS`.

- [ ] **Step 5: Commit the pure contract**

```bash
git add main/manager-web/package.json \
  main/manager-web/scripts/check-flattened-cinematic-preview.mjs \
  main/manager-web/src/components/lesson/flattened-cinematic-preview.js
git commit -m "feat(admin): add flattened cinematic preview helpers"
```

### Task 2: Flattened Canvas Component

**Files:**
- Create: `main/manager-web/src/components/lesson/FlattenedCinematicPreview.vue`
- Modify: `main/manager-web/scripts/check-flattened-cinematic-preview.mjs`

- [ ] **Step 1: Extend the contract test for component behavior**

Read the Vue source and assert the required lifecycle and error surface:

```js
const componentSource = await readFile(
  new URL('src/components/lesson/FlattenedCinematicPreview.vue', root),
  'utf8',
);
for (const required of [
  'data-testid="flattened-cinematic-preview"',
  'width="480"',
  'height="320"',
  'crossorigin="anonymous"',
  'requestAnimationFrame',
  'cancelAnimationFrame',
  'getImageData',
  'applyChromaKey',
  'flattened-preview__error',
]) assert.ok(componentSource.includes(required), `missing flattened preview contract: ${required}`);
```

- [ ] **Step 2: Run the test and verify RED**

Run `node scripts/check-flattened-cinematic-preview.mjs`.

Expected: FAIL because `FlattenedCinematicPreview.vue` does not exist.

- [ ] **Step 3: Implement the minimal component**

Implement a component with props:

```js
props: {
  projection: { type: Object, required: true },
  playing: { type: Boolean, default: false },
  clockMs: { type: Number, default: 0 },
  replayNonce: { type: Number, default: 0 },
}
```

Template shape:

```vue
<section data-testid="flattened-cinematic-preview" class="flattened-preview">
  <div class="flattened-preview__stage">
    <canvas ref="canvas" width="480" height="320" aria-label="Flattened robot cinematic preview" />
    <video
      v-for="layer in layers"
      :key="layer.id"
      :ref="`video-${layer.id}`"
      :src="layer.src"
      crossorigin="anonymous"
      muted
      playsinline
      preload="auto"
      @loadeddata="startRendering"
      @error="failLayer(layer.id)"
    />
  </div>
  <p v-if="error" class="flattened-preview__error" role="alert">{{ error }}</p>
</section>
```

Rendering rules:

- clear the visible canvas;
- seek each video to `clockMs / 1000` when `shouldResyncVideo()` is true;
- draw each video using `objectFitRect()`;
- for chroma layers, draw into a reusable offscreen canvas, call `getImageData()`, apply `applyChromaKey()`, then composite to the visible canvas;
- catch canvas security/readback errors and set `Flattened preview unavailable: media host must allow CORS.`;
- cancel the frame handle and pause videos on source changes and `beforeDestroy`.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run `node scripts/check-flattened-cinematic-preview.mjs`.

Expected: helper and component contracts PASS.

- [ ] **Step 5: Commit the canvas component**

```bash
git add main/manager-web/scripts/check-flattened-cinematic-preview.mjs \
  main/manager-web/src/components/lesson/FlattenedCinematicPreview.vue
git commit -m "feat(admin): render flattened cinematic canvas"
```

### Task 3: Shared Playback Control For Existing Video Layers

**Files:**
- Modify: `main/manager-web/src/components/lesson/CinematicVideoLayer.vue`
- Modify: `main/manager-web/scripts/check-flattened-cinematic-preview.mjs`

- [ ] **Step 1: Add failing source-layer synchronization assertions**

Require optional control props and cleanup wiring:

```js
const layerSource = await readFile(new URL('src/components/lesson/CinematicVideoLayer.vue', root), 'utf8');
for (const required of [
  'controlled: { type: Boolean, default: false }',
  'playing: { type: Boolean, default: true }',
  'clockMs: { type: Number, default: 0 }',
  'replayNonce: { type: Number, default: 0 }',
  'shouldResyncVideo',
  'syncPlayback',
]) assert.ok(layerSource.includes(required), `source layer missing sync contract: ${required}`);
```

- [ ] **Step 2: Run the test and verify RED**

Run `node scripts/check-flattened-cinematic-preview.mjs`.

Expected: FAIL on the first missing control prop.

- [ ] **Step 3: Add optional externally controlled playback**

- Keep existing autoplay behavior when `controlled === false`.
- When controlled, remove autonomous looping behavior, watch `playing`, `clockMs`, and `replayNonce`, and call `syncPlayback()`.
- `syncPlayback()` seeks only when drift exceeds 80 ms, calls `video.play()` when playing, and pauses otherwise.
- Swallow rejected `play()` promises because browser autoplay policy is represented by the visible preview error/control state rather than an uncaught rejection.
- Preserve existing chroma rendering and source-change cleanup.

- [ ] **Step 4: Run focused and existing preview tests**

```bash
node scripts/check-flattened-cinematic-preview.mjs
node scripts/check-robot-lesson-preview.mjs
```

Expected: both PASS.

- [ ] **Step 5: Commit playback control**

```bash
git add main/manager-web/src/components/lesson/CinematicVideoLayer.vue \
  main/manager-web/scripts/check-flattened-cinematic-preview.mjs
git commit -m "feat(admin): synchronize cinematic source layers"
```

### Task 4: Renderer-v3 Side-by-Side Integration

**Files:**
- Modify: `main/manager-web/src/components/lesson/RobotEspTftProjectionPreview.vue`
- Modify: `main/manager-web/scripts/check-flattened-cinematic-preview.mjs`
- Modify: `main/manager-web/scripts/check-robot-lesson-preview.mjs`

- [ ] **Step 1: Add failing integration assertions**

Assert that the exact preview imports/registers the flattened component, renders labeled comparison panels only for renderer v3, passes the shared clock to both renderers, and retains legacy stage markup:

```js
const exactSource = await readFile(new URL('src/components/lesson/RobotEspTftProjectionPreview.vue', root), 'utf8');
for (const required of [
  "import FlattenedCinematicPreview from './FlattenedCinematicPreview.vue';",
  'isFlattenableProjection',
  'data-testid="cinematic-preview-comparison"',
  '3 Layers',
  'Robot Flattened',
  ':projection="projection"',
  ':clock-ms="cinematicClockMs"',
  ':replay-nonce="cinematicReplayNonce"',
]) assert.ok(exactSource.includes(required), `exact preview missing comparison contract: ${required}`);
assert.ok(exactSource.includes('data-testid="esp-tft-stage"'), 'legacy exact stage must remain');
```

Extend `check-robot-lesson-preview.mjs` to assert renderer-v1/v2 still project normally and only the v3 projection satisfies `isFlattenableProjection()`.

- [ ] **Step 2: Run tests and verify RED**

```bash
node scripts/check-flattened-cinematic-preview.mjs
node scripts/check-robot-lesson-preview.mjs
```

Expected: flattened integration assertions FAIL; existing projection assertions remain PASS until the integration section.

- [ ] **Step 3: Implement comparison layout and shared clock**

In `RobotEspTftProjectionPreview.vue`:

- import/register `FlattenedCinematicPreview` and `isFlattenableProjection`;
- add `cinematicPlaying`, `cinematicClockMs`, `cinematicReplayNonce`, `cinematicFrameHandle`, and `cinematicStartedAt`;
- render the existing `.stage-shell` and flattened component inside `data-testid="cinematic-preview-comparison"` when the projection is flattenable;
- label panels `3 Layers` and `Robot Flattened`;
- pass `controlled`, `playing`, `clockMs`, and `replayNonce` to each renderer-v3 `CinematicVideoLayer`;
- add Play/Pause and Replay controls scoped to the cinematic comparison;
- advance `cinematicClockMs` with `requestAnimationFrame`, wrap by the current phase duration from the manifest metadata, and stop/cancel on destroy or projection change;
- keep all existing lesson-play, response-path, safe-zone, degraded-state, and timeline controls unchanged;
- add responsive CSS: two columns above `1100px`, one column below it, both stages horizontally scroll-safe, and no width overflow in Lesson Editor.

- [ ] **Step 4: Run focused UI contract tests**

```bash
node scripts/check-flattened-cinematic-preview.mjs
node scripts/check-robot-lesson-preview.mjs
node scripts/check-lesson-editor-ui-contracts.mjs
```

Expected: all PASS.

- [ ] **Step 5: Commit renderer integration**

```bash
git add main/manager-web/src/components/lesson/RobotEspTftProjectionPreview.vue \
  main/manager-web/scripts/check-flattened-cinematic-preview.mjs \
  main/manager-web/scripts/check-robot-lesson-preview.mjs
git commit -m "feat(admin): compare layered and flattened cinematics"
```

### Task 5: Build And Browser Verification

**Files:**
- Modify: `main/manager-web/package.json`
- Test: `main/manager-web/scripts/check-flattened-cinematic-preview.mjs`
- Test: `main/manager-web/scripts/check-robot-lesson-preview.mjs`
- Test: `main/manager-web/scripts/check-lesson-editor-ui-contracts.mjs`

- [ ] **Step 1: Add the focused test to the lesson-studio command**

Insert `npm run test:flattened-cinematic-preview` before `check-robot-lesson-preview.mjs` in `test:lesson-studio` so future lesson-studio verification cannot omit the new preview.

- [ ] **Step 2: Run the full relevant contract suite**

```bash
export PATH=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH
node scripts/check-flattened-cinematic-preview.mjs
node scripts/check-robot-lesson-preview.mjs
node scripts/check-lesson-editor-ui-contracts.mjs
node scripts/check-lesson-builder-browser.mjs
```

Expected: all commands exit 0 and print PASS/OK markers.

- [ ] **Step 3: Run the production build**

Use the installed local CLI directly to avoid the environment's pnpm policy wrapper:

```bash
export PATH=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH
node node_modules/@vue/cli-service/bin/vue-cli-service.js build
```

Expected: exit 0 with a generated production bundle. Existing bundle-size warnings are acceptable; compile errors are not.

- [ ] **Step 4: Verify in a browser against a renderer-v3 fixture or live lesson**

Run the dev server:

```bash
export PATH=/Users/manhhodinh/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin:$PATH
node node_modules/@vue/cli-service/bin/vue-cli-service.js serve
```

Verify:

1. Generate the manifest preview for a renderer-v3 direct-MP4 lesson.
2. Confirm `3 Layers` and `Robot Flattened` appear side by side on desktop.
3. Confirm both stack without horizontal page overflow at a narrow viewport.
4. Play, pause, and replay at least three times; confirm no visible drift greater than one 10 FPS frame.
5. Confirm crop, rect placement, and chroma edges match.
6. Confirm a forced CORS/media failure affects only the flattened panel.
7. Confirm the browser console has no uncaught errors.

- [ ] **Step 5: Review the final diff**

```bash
git status --short
git diff --check HEAD~3..HEAD
git diff --stat HEAD~3..HEAD
```

Expected: only the planned manager-web feature/test files and this plan are changed; no generated `dist/`, dependency lockfile, or unrelated workspace files are staged.

- [ ] **Step 6: Commit verification wiring**

```bash
git add main/manager-web/package.json
git commit -m "test(admin): gate flattened cinematic preview"
```

## Final Verification

- [ ] `node scripts/check-flattened-cinematic-preview.mjs` passes.
- [ ] `node scripts/check-robot-lesson-preview.mjs` passes.
- [ ] `node scripts/check-lesson-editor-ui-contracts.mjs` passes.
- [ ] `node scripts/check-lesson-builder-browser.mjs` passes.
- [ ] Vue production build exits 0.
- [ ] Desktop and narrow browser previews match the approved side-by-side/stacked layout.
- [ ] Renderer-v1/v2 preview behavior remains unchanged.
- [ ] No publish, backend, database, manifest checksum, or firmware files are modified.
