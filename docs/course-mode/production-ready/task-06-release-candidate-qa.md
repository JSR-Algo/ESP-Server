# Task 06 Master Prompt: Release-Candidate QA

```text
You are executing Task 06, the adversarial release-candidate gate for Course
Mode V2. Do not add features unless a reproduced blocker requires a scoped fix.

Outcome
Produce evidence that the integrated backend, ESP server, firmware, pilot
lesson, and physical robot are safe and reversible enough for a controlled
canary. A test failure is a blocker, not something to waive silently.

Read first
- robot/esp32-server/docs/course-mode/production-ready/README.md
- robot/esp32-server/docs/course-mode/measurement-and-validation.md
- all Task 00-05 handoff evidence and commit SHAs
- existing lesson production, rollout, rollback, HIL, and log-verification runbooks

Candidate construction
- Pin exact backend, ESP, firmware, pilot lesson/version, renderer, fixture
  checksum, derivative checksum, and configuration identities.
- Build from clean worktrees. Confirm all feature flags default off.
- Verify the device advertises the exact complete V2 capability before admission.

Required QA lanes
1. Contract parity and V1 regression across all repositories.
2. Twenty-plus scripted child journeys plus adversarial variants: noisy room,
   echo, barge-in, silence, mixed language, unrelated story, repeated question,
   wrong ASR, refusal, distress, fatigue, rapid answers, disconnect, restart,
   duplicate frames, stale generations, missing ACK, missing asset, corrupt cache,
   backend timeout, and power cycle.
3. Visual QA at 480x320 for every cue: z-order, crop, object legibility, focus,
   caption-safe area, transitions, reduced motion, and listening stillness.
4. Physical HIL: microphone contamination, settle latency, servo noise,
   temperature, current draw, repeated-session wear signal, face/head/arm comfort,
   and safe stop/rest behavior.
5. Privacy/security: event/log capture inspection, malformed payload fuzzing,
   replay/idempotency, authorization, bounded fields, redaction, and retention.
6. Reliability: soak multiple sessions, reconnect/resume, cache cold/warm paths,
   resource/heap monitoring, telemetry delivery, and rollback rehearsal.
7. Educator review: natural language, patience, truthful praise, pedagogical
   progression, Vietnamese support, and graceful one-word close.

Acceptance gates and release blockers
- Any V1 regression or fixture drift.
- Any servo movement during assessment.
- Any normal miss producing sad/shaming behavior.
- Any unsupported mastery event or duplicated durable evidence.
- Any raw child content in durable logs/events.
- Any unsafe stop/restart pose or capability downgrade into partial V2.
- Any unreadable/covered target object or incorrect robot focus direction.
- Missing physical HIL, rollback proof, educator approval, or reproducible build.

Deliverables
- A committed QA report with every command, result, artifact checksum, capture
  path, pass/fail verdict, blocker owner, and retest evidence.
- A release-candidate manifest that pins all identities but contains no secrets.
- A rehearsed rollback procedure that disables new V2 admission/assignment and
  defines what happens to an already active session.
- A final GO/NO-GO verdict. GO only means eligible for Task 07 canary.

Working method
- Use verification-before-completion and systematic debugging for failures.
- Make fixes in the owning repository with tests and focused commits; rebuild
  and rerun every affected lane afterward.
- Do not deploy production or assign a child/device in this task.
```
