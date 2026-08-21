# Task 02 Firmware Handoff

Task 03 must implement `lesson-embodied-action.v1` exactly as frozen in
`main/tbot-server/tests/fixtures/course-mode/lesson-embodied-action-wire-contract.json`.

## Capability Gate

Firmware advertises the following object in `hello.features`:

```json
{
  "lessonCourseMode": {
    "version": 2,
    "embodiedActions": true,
    "reducedMotion": false,
    "faces": ["neutral", "happy", "thinking", "relaxed"]
  }
}
```

The ESP sends no embodied frame unless `version` is exactly `2` and
`embodiedActions` is exactly `true`. Missing or unsupported capability uses the
existing voice/screen path and records an explicit `unsupported` local result.
`reducedMotion: true` still receives the named intent and authored focus region;
firmware must apply face/focus only and ACK `degraded`.

## Exact Server Frame

```json
{
  "type": "lesson_embodied_action",
  "assignmentId": "assignment-1",
  "sessionId": "session-1",
  "stepId": "cat-meaning-left-right-01",
  "sequence": 17,
  "body": {
    "actionId": "session-1:course-decision-1",
    "actionGeneration": 1,
    "intent": "PRESENT_LEFT",
    "visualFocusRegion": "focus.left.choice",
    "listenWindowPolicy": "complete_before_listening"
  }
}
```

All envelope and body keys are closed. Raw servo, joint, angle, percentage, and
speed fields are forbidden. The 17 accepted intent strings are the exact values
of `core.lesson.embodied_intent.EmbodiedIntent`.

Focus mappings are authored and must not be inferred from generated text:

- `PRESENT_CENTER` -> `focus.center.primary`
- `PRESENT_LEFT` -> `focus.left.choice`
- `PRESENT_RIGHT` -> `focus.right.choice`
- all other intents -> `focus.center.primary`

The three IDs are the exact anchors in the frozen Task 00 renderer-v4 fixture.

## Exact Server Cancel Frame

```json
{
  "type": "lesson_embodied_cancel",
  "assignmentId": "assignment-1",
  "sessionId": "session-1",
  "stepId": "cat-meaning-left-right-01",
  "sequence": 18,
  "body": {
    "actionId": "session-1:course-decision-1",
    "actionGeneration": 1
  }
}
```

Firmware must treat this as an immediate request to stop the matching action,
center the head, lower both arms, and suppress further scheduler output. Stale,
unknown, or already-terminal action identities are idempotent no-ops. The cancel
frame has no model-authored content and never contains servo parameters.

## Exact Firmware ACK

```json
{
  "type": "lesson_ack",
  "assignmentId": "assignment-1",
  "sessionId": "session-1",
  "stepId": "cat-meaning-left-right-01",
  "sequence": 91,
  "body": {
    "acks": 17,
    "embodiedAction": {
      "actionId": "session-1:course-decision-1",
      "actionGeneration": 1,
      "outcome": "applied",
      "returnedToRest": true
    }
  }
}
```

`body.acks` is the server frame sequence. Accepted outcomes are `applied`,
`degraded`, and `rejected`. Firmware must not place `superseded` or `timed_out`
in this ACK; those are ESP-local terminal outcomes and may exist only in separate
firmware lifecycle telemetry.

## Lifecycle Requirements

- Accept only the active assignment/session and a monotonically newer generation.
- Deduplicate `actionId` for the life of the lesson session, including reconnects.
- Keep one action in flight; a newer generation supersedes and returns the older
  action to rest.
- Resolve all servo values inside trusted firmware presets. Never parse them from
  the frame.
- Cancel and restore rest for assessment opening, barge-in, emotional or safety
  branches, stop, disconnect, replacement, and restart.
- Never automatically replay a timed-out action. A repeated action ID is a
  duplicate even after reconnect.
- Do not ACK `returnedToRest: true` until the head is centered, both arms are in
  the safe rest pose, and the action scheduler has stopped producing movement.
- Open assessed microphone input only after that ACK and the ESP settle interval.

## Ordering

The ESP starts the embodied frame before provider speech delivery. Response-plan
commit waits for the terminal ACK, then waits the settle interval, then opens the
assessment window only when `returnedToRest` is true:

```text
authored focus/frame -> speech -> gesture terminal/rest ACK -> settle -> assessment
```

Timeout, rejection, unsupported hardware, and reduced motion never mutate Course
Mode evidence or block voice/screen continuation. A missing rest confirmation
keeps the assessed microphone window closed.
