# Course Mode Renderer-v4 Visual Layout Contract

## Status

Task 00 froze this software-only composition baseline for
`courseCompanion.v2`. It is a contract for Tasks 01-09, not a production
rollout, renderer-v5 proposal, or dependency on the Farm v9 worktree.

The machine-readable source is `renderer-v4.course-mode-layout.v1` with
canonical SHA-256
`e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c`.

## Canvas and Layering

The renderer remains `teebot-lesson-renderer.v4` on a `480x320` canvas. The
only valid back-to-front order is:

1. `background`
2. `teachingObject`
3. `robotOverlay`
4. `transientFocusCue`

The Farm v9 pilot geometry is frozen as:

| Layer | X | Y | Width | Height | Z |
| --- | ---: | ---: | ---: | ---: | ---: |
| Background | 0 | 0 | 480 | 320 | 0 |
| Teaching object | 20 | 168 | 95 | 95 | 1 |
| Robot overlay | 118 | 160 | 150 | 150 | 2 |
| Transient focus cue | 0 | 0 | 480 | 320 | 3 |

Every rectangle must remain fully inside the canvas. The teaching object and
robot have zero pixel overlap and a minimum horizontal gap of three pixels.
The teaching object therefore remains fully visible rather than being hidden
under the robot overlay.

## Captions and Listening Cue

The caption-safe rectangle is `(16,16,448,52)`. The listening cue uses
`(282,168,182,52)` with a minimum text height of 24 pixels. It must remain
inside the canvas and must not overlap the teaching object or robot bounds.

Before an assessment window opens, the semantic fixture requires this exact
ordered transition:

```text
speech_complete
-> gesture_settled
-> head_centered
-> arms_lowered
-> motor_stopped
-> assessment_window_open
```

No Task 00 artifact contains raw servo values. Later preset resolution and
firmware safety enforcement own physical values.

## Focus Direction

`PRESENT_CENTER` targets the single teaching object, even though the approved
pilot object is visually left of the canvas center. `PRESENT_LEFT` and
`PRESENT_RIGHT` select explicitly authored focus regions. Runtime or model text
must not infer a direction.

The frozen anchors are:

| Focus region | X | Y |
| --- | ---: | ---: |
| `focus.center.primary` | 67 | 215 |
| `focus.left.choice` | 67 | 215 |
| `focus.right.choice` | 366 | 215 |

Mirroring is limited to authored focus-region selection. Automatic whole-scene
mirroring and direction inference from model wording are invalid.

## Reduced Motion

Every activity preserves its learning meaning without servo motion. Reduced
motion uses the face plus transient focus cue, requires no servo movement, and
does not change evidence eligibility or mastery.

## Machine Contract and Validation

Identical layout fixtures are stored at:

- ESP: `main/tbot-server/tests/fixtures/course-mode/renderer-v4-visual-layout.json`
- Firmware: `tests/fixtures/course-mode/renderer-v4-visual-layout.json`
- Backend: `src/lessons/fixtures/course-mode/renderer-v4-visual-layout.json`

The repository tests reject unknown keys, out-of-canvas rectangles, reordered
layers, object/robot collision, unreadable listening geometry, unsupported
mirroring, and motion-dependent reduced-motion behavior.
