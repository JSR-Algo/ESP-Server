# T5.4 Startup Duplicate Admission Design

## Problem

A physical `w02-feelings` v7 start reproduced `SD_SYNC_REALTIME_BUSY_TIMEOUT`.
The first spoken start entered foreground SD attestation. Fourteen seconds later,
a repeated recognized start intent fell outside the fixed 12-second duplicate
window. It advanced the Google Live response generation and cancelled the first
lesson pull. The replacement pull joined the same cache-key coordinator operation,
which still owned the first pull's admission token and 15-second deadline. That
operation therefore expired in `INTERRUPTING` before the replacement could render.

## Decision

Treat an unfinished spoken lesson-start task as the authoritative duplicate gate.
Google Live must suppress a repeated classified start before calling
`transition_to_lesson_start`, so the active task keeps its response generation and
SD admission token. The `start_lesson` tool must also coalesce repeated tool calls
that target a task it previously scheduled. It must retain the existing behavior
where an explicit spoken start can replace a connect-time/background pull.

The SD coordinator, realtime busy policy, timeout, assignment state machine, and
fail-closed attestation contract remain unchanged.

## State Ownership

`ConnectionHandler.lesson_pull_task` already owns the scheduled task. Add a small
origin marker alongside it:

- `spoken_start`: the task was scheduled by the `start_lesson` tool; repeated tool
  calls reuse it and do not cancel it.
- absent/other: the task may be a reconnect/background pull; an explicit spoken
  start may still supersede it as before.

The marker is cleared only by the done callback when that callback still owns the
tracked task, preventing an older callback from clearing a newer task's marker.

## Tests

1. A classified duplicate after the 12-second window but while the pull is active
   is suppressed before realtime transition and tool dispatch.
2. A second direct `start_lesson` call coalesces with the active spoken task and
   does not cancel or schedule another pull.
3. An explicit start still cancels a pre-existing unmarked background pull.
4. Existing Google Live, start-tool, lesson-runtime, and full ESP suites remain
   green.

## Release Evidence

After RED-to-GREEN verification, gate and merge the ESP branch, deploy the ESP
service, create a fresh no-PIN assignment, and repeat the physical power-cycle run.
