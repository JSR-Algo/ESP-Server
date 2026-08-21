# Task 01 Master Prompt: ESP Semantic Runtime

```text
You are implementing Task 01 of Course Mode V2 in
/Users/manhhodinh/Documents/TBOT/robot/esp32-server.

Outcome
Build the server-authoritative courseCompanion.v2 semantic runtime that teaches
one or two words through natural conversation and truthful evidence. Keep
tvideoJourney.v1 behavior unchanged.

Read first
- docs/course-mode/production-ready/README.md
- docs/superpowers/plans/2026-08-21-course-mode-v2-esp-runtime.md
- docs/superpowers/specs/2026-08-21-course-mode-word-mastery-design.md
- docs/course-mode/pedagogy-and-mastery.md
- docs/course-mode/conversation-persona.md
- docs/course-mode/runtime-contract.md
- Task 00 fixture and checksum produced in all repositories

Required behavior
- Opening is greet -> check in -> acknowledge -> curiosity -> clue -> elicitation.
- Normal response is acknowledge -> respond -> gently teach/redirect -> ask one
  short question.
- Routine side stories receive a meaningful response before an authored bridge
  returns to the active word. Emotional/safety branches are never forcibly
  redirected to vocabulary.
- Immediate repetition is SUPPORTED_SPEECH, never INDEPENDENT_RECALL.
- MASTERED_TODAY requires meaning, independent recall, transfer, delayed recall,
  confidence eligibility, and an answer-leakage-safe window.
- Low ASR confidence, robot-audio contamination, visible answer text, recent
  modeling, duplicates, or stale generations cannot advance mastery.
- One deeply learned word is preferable to rushing a second word.

Implementation boundary
- Follow the exact file map and task sequence in
  docs/superpowers/plans/2026-08-21-course-mode-v2-esp-runtime.md.
- Add separate V2 parser, mastery aggregate, orchestrator, response-plan
  validator, interpreter adapter, snapshot/resume logic, and privacy-safe event
  forwarding.
- The language model may phrase approved intents but cannot mutate state,
  select arbitrary curriculum, mark mastery, bypass safety, or issue servo data.
- Leave embodied execution behind an interface consumed by Task 02.
- Feature flag LESSON_COURSE_MODE_V2_ENABLED defaults false and parses strictly.

Acceptance gates
- All tests named in the ESP plan pass.
- Add at least 20 deterministic scripted child journeys: knows word early,
  repetition only, Vietnamese answer, partial speech, silence, low confidence,
  unrelated story, emotional share, refusal, fatigue, question, barge-in,
  reconnect, duplicate tool call, delayed recall success/failure, one-word close,
  two-word success, safety pause, and technical recovery.
- Snapshot restore cannot replay prompts, motion requests, celebrations, or
  evidence events.
- Logs and forwarded events contain no raw transcript/audio/free-form story.
- Existing V1 tests and contract vectors pass without fixture edits.
- No deployment or production flag change occurs.

Working method
- Inspect status and AGENTS.md first; preserve unrelated edits.
- Use TDD, small commits, and verification-before-completion.
- If Task 00 contract differs from an older plan example, Task 00 wins; document
  the reconciliation rather than silently diverging.
- Finish with commit SHAs, test evidence, coverage, known limitations, and the
  stable interfaces required by Tasks 02 and 04.
```

