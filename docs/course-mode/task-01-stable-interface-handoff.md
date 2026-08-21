# Task 01 Stable Interface Handoff

Task 01 freezes the server-authoritative semantic boundary consumed by Tasks 02 and 04.

## Task 02: embodied dispatcher input

- Import `EmbodiedIntent` from `core.lesson.embodied_intent`.
- Resolve only the 17 frozen Task 00 intent names; reject unknown names.
- Consume `CourseDecision.embodied_intent` and `decision_id` idempotently.
- Do not mutate `CourseOrchestrator`, `WordMastery`, evidence, targets, or word/session state.
- Do not accept raw servo values from Course Mode decisions or model tools.

## Task 04: authoring and progress input

- Publish semantic contract `courseCompanion.v2.contract.v1` with canonical checksum
  `cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264`.
- Preserve the exact Task 00 target/activity identities and ordered activity mapping.
- Persist only `word_evidence_recorded` fields produced by
  `core.lesson.forwarder.serialize_word_evidence_event`.
- Confidence is the bounded `low | medium | high` band. Raw transcript, utterance,
  audio, pronunciation score, and free-form child story are forbidden.
- Treat `(lessonSessionId, observationId)` and `decisionId` as idempotency identities.

## Provider tools

The V2-only Google Live tools are `course_observe_child`, `course_open_context`,
`course_close_context`, `course_apply_response_plan`, and `course_continue`. They
remain separate from the frozen `tvideoJourney.v1` tool schemas and cannot submit
mastery or evidence levels directly.
