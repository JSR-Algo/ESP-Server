# Task 05 Master Prompt: Pilot Lesson and Asset Authoring

```text
You are implementing Task 05 of Course Mode V2 across the TBOT backend and
robot repositories after Tasks 00-04 are complete.

Outcome
Create one production-quality, unpublished pilot lesson that proves natural
teaching, visual focus, embodied behavior, truthful mastery, recovery, and
privacy end to end for the words cat and ball.

Read first
- robot/esp32-server/docs/course-mode/production-ready/README.md
- all approved Course Mode design documents
- completed Task 00 fixture and Tasks 01-04 contracts
- final evidence from Codex task 01a018d4-b776-77b2-a013-595958dcf9f3 if
  available; consume it read-only

Pilot content
- Primary: animals.cat. Secondary: toys.ball, optional at runtime.
- Include multiple opening questions, likely child-topic bridges, meaning
  contrasts, joint speaking, independent visual naming, second context, delayed
  recall, movement/story transition, warm close, refusal/fatigue alternatives,
  and Vietnamese support that fades naturally.
- Every robot turn is one or two short sentences and asks at most one question.
- Every branch has an authored return bridge or safe close.
- Never encode "wrong", shame, pressure, false mastery, or disappointed faces.

Visual and embodied composition
- Use renderer-v4 at 480x320 and the Task 00 z-order/safe-zone contract.
- Use the approved Farm v9 object/robot geometry as the initial composition
  baseline, then adjust only through a new pilot lesson version if visual QA
  proves a word-specific need.
- Keep target object legible, avoid robot/object overlap, preserve caption and
  listening-cue safe areas, and author explicit CENTER/LEFT/RIGHT focus anchors.
- Pair each activity with an approved intent and a LISTEN_STILL transition.
- Provide reduced-motion and missing-motion fallbacks that retain teaching meaning.

Required work
1. Author source JSON/assets through existing versioned authoring paths.
2. Add deterministic fixtures and validators for all branches and visual bounds.
3. Generate derivatives in a non-production environment; verify cue count,
   duration, dimensions, checksum, cache identity, and visual frame samples.
4. Add scripted audio/ASR journeys for all major paths without real child data.
5. Exercise snapshot/reconnect and prove no repeated praise/motion/evidence.
6. Produce educator-readable lesson transcript maps and asset provenance/license
   records. Do not include private child data.

Acceptance gates
- All authored branches terminate, return, pause safely, or close; none loop.
- Visual samples pass at start/middle/end of each cue and remain readable on the
  physical TFT under representative lighting.
- Robot gaze/arm direction matches the authored focus object.
- Assessment never starts until the robot is settled and target text is hidden.
- Independent/delayed evidence cannot be earned in modeled/revealed paths.
- Content, pedagogy, child-safety, and Vietnamese/English reviews are signed.
- The pilot remains unpublished and unassigned until Task 06 passes.

Working method
- Use new lesson/version identities; never modify a published lesson in place.
- Do not alter or deploy the active Farm v9 rollout.
- Preserve unrelated work, use TDD for validators, and commit by repository.
- Finish with lesson/version/checksum, derivative evidence, visual captures,
  review records, test commands/results, and known content risks.
```

