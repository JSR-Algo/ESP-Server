# Task 02 Master Prompt: ESP Embodied Dispatcher

```text
You are implementing Task 02 of Course Mode V2 in
/Users/manhhodinh/Documents/TBOT/robot/esp32-server.

Outcome
Convert authoritative Course Mode decisions into session-bound embodied-action
frames without allowing the model or normal MCP tools to control raw servos.

Read first
- docs/course-mode/production-ready/README.md
- docs/course-mode/embodied-interaction.md
- docs/course-mode/runtime-contract.md
- docs/superpowers/plans/2026-08-21-course-mode-v2-esp-runtime.md
- docs/superpowers/plans/2026-08-21-course-mode-v2-firmware-embodied.md
- stable Task 00 fixture and Task 01 decision/turn types
- main/tbot-server/core/lesson/motion_presets.py
- main/tbot-server/core/lesson/runtime.py
- main/tbot-server/core/lesson/forwarder.py

Required work
1. Freeze the exact lesson_embodied_action frame shared with firmware: assignment,
   session, step, sequence, actionId, generation, named intent, focus target, and
   listenWindowPolicy. Do not include raw angles or percentages.
2. Implement an ESP-side dispatcher with one in-flight action, idempotent IDs,
   monotonically increasing generation, bounded ACK timeout, degraded outcomes,
   cancellation, and safe session teardown.
3. Coordinate speech, visual focus, motion, and listening in this order:
   render/focus -> speech -> gesture completes or is cancelled -> REST/LISTEN_STILL
   -> settle interval -> microphone assessment window.
4. Never replay timed-out movement automatically. Motion failure cannot alter
   evidence or block the lesson from continuing with voice/screen fallback.
5. Cancel movement for barge-in, emotional share, safety branch, stop,
   disconnect, restart, or assessment opening.
6. Keep the existing normal lesson-time servo MCP block intact.
7. Add capability negotiation for lessonCourseMode.version=2 and
   embodiedActions=true. Unsupported hardware receives screen/face fallback or
   an explicit unsupported result; never partial V2 state-machine fallback.

Acceptance gates
- Contract tests use the exact Task 00 fixture and match firmware byte semantics.
- Tests cover ACK applied/degraded/rejected, timeout, duplicate, stale session,
  stale generation, supersession, reconnect, teardown, and reduced motion.
- A timing test proves no motion overlaps any assessment-eligible input window.
- PRESENT_LEFT/RIGHT/CENTER carry explicit authored focus IDs and map to the
  visual fixture correctly.
- Existing motion tools and V1 lesson suites remain green.
- Feature remains dark; no deployment or production mutation occurs.

Working method
- Use TDD and apply_patch; make focused commits.
- Do not implement firmware in this task. Produce a frozen wire-contract artifact
  and handoff notes for Task 03.
- Stop on unexpected concurrent edits. Do not modify the Farm v9 worktree.
- Finish with commit SHAs, tests/results, timing evidence, and the exact frame
  examples Task 03 must accept/reject.
```

