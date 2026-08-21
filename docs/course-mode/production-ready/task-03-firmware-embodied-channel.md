# Task 03 Master Prompt: Firmware Embodied Channel

```text
You are implementing Task 03 of Course Mode V2 in
/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware.

Outcome
Add a lesson-owned, session-bound face/head/arm execution channel that is safe,
quiet before listening, idempotent, recoverable, and independent from normal MCP
servo calls.

Read first
- ../esp32-server/docs/course-mode/production-ready/README.md
- ../esp32-server/docs/course-mode/embodied-interaction.md
- ../esp32-server/docs/superpowers/plans/2026-08-21-course-mode-v2-firmware-embodied.md
- Task 00 canonical fixture and Task 02 frozen wire contract
- main/application.cc and main/application.h
- main/lesson_handler.cc and main/lesson_handler.h
- main/lesson_motion_presets.cc and main/lesson_motion_presets.h
- main/robot_uart.cc and main/robot_uart.h

Required behavior
- Preserve the current block on normal servo MCP commands during lesson runtime.
- Accept only named intents through a valid active lesson token/session/generation.
- Resolve safe servo percentages inside firmware; reject raw authored servo data.
- Coordinate approved faces, head direction, arms, hold time, return-to-rest, and
  settle-before-listening values.
- LISTEN_STILL means center head, both arms lowered, no ongoing servo command,
  relaxed attentive face, and an acknowledged settled state.
- Normal misses use TRY_DIFFERENT_WAY with neutral/thinking/relaxed/playful
  presentation; never sad/crying/angry/disappointed feedback.
- Strong both-arm celebration is limited to independent/delayed mastery evidence
  and at most two high-energy celebrations per session.
- Stop, pause, disconnect, safety, stale generation, timeout, restart, or power
  recovery restores rest within two seconds when hardware is responsive.
- Reduced-motion mode uses face and screen cues only.

Implementation boundary
- Follow the exact TDD sequence and file map in the firmware plan.
- Extend LessonHandler/Application through explicit lesson-authorized methods;
  never reopen unrestricted MCP access.
- Advertise lessonCourseMode version 2 only after initialization of the complete
  embodied action path succeeds.
- Emit bounded, typed ACK outcomes without child content.

Acceptance gates
- Native parser/lifecycle/handler tests pass under ASan and UBSan.
- Lesson coverage remains 100% for the required target or does not regress from
  the repository's stronger current gate.
- Tests cover duplicate/stale/mismatch, one-servo failure, face failure, timeout,
  supersession, assessment-open rejection, reconnect, restart, and teardown.
- Physical HIL measures settle latency, motor noise entering microphone input,
  current draw, servo temperature, and safe return pose.
- Existing renderer-v4, V1 lesson, UART, and cinematic tests pass.
- No OTA, production assignment, or flag enablement occurs without a separate
  explicit authorization.

Working method
- Inspect status/AGENTS.md and preserve concurrent work.
- Use TDD, apply_patch, focused commits, and verification-before-completion.
- If physical hardware is unavailable, record this as a release blocker; do not
  claim the task production-ready based on native tests alone.
- Finish with commit SHAs, test/HIL evidence, observed timing/noise/temperature,
  and capability payload consumed by Tasks 05-07.
```

