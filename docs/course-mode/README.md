# Course Mode Design Package

This folder contains the detailed product contracts for the child-friendly,
word-mastery-focused Course Mode approved on 2026-08-21.

Read in this order:

1. [Approved design](../superpowers/specs/2026-08-21-course-mode-word-mastery-design.md)
2. [Pedagogy and mastery](pedagogy-and-mastery.md)
3. [Conversation persona and redirection](conversation-persona.md)
4. [Embodied interaction: face, head, and arms](embodied-interaction.md)
5. [Runtime and authoring contract](runtime-contract.md)
6. [Measurement, safety, and validation](measurement-and-validation.md)
7. [Renderer-v4 visual layout contract](visual-layout-contract.md)
8. [Production-ready task pack](production-ready/README.md)

The package defines a proposed `courseCompanion.v2` preset. It does not alter
the current `tvideoJourney.v1` runtime and is not an implementation plan.

## Product Decisions Captured

- Target learner: Vietnamese-speaking children aged 3-5.
- Session length: approximately 8-10 minutes.
- Word count: one primary and one optional secondary word.
- Available interaction: voice, screen visuals, and robot movement.
- Language policy: balanced Vietnamese-English support that fades as the child
  understands.
- Teaching path: understand, pronounce, independently name, use/transfer, and
  recall later.
- Primary success: independent naming without an immediately preceding model.
- Conversation policy: listen and respond to side stories, then redirect through
  a relevant bridge; emotional and safety needs take priority over the lesson.
- Personality: warm, patient, brief, specific, and non-judgmental.
- Embodied teaching: coordinated face, head, arms, screen focus, and speech;
  still listening posture before child assessment.

## Implementation Planning

The production-ready task pack splits delivery into contract, semantic runtime,
embodied dispatch, firmware, backend, pilot authoring, QA, and controlled canary
tasks. Each task contains a standalone master prompt and explicit release gates.

## Explicitly Not Yet Done

- No runtime code, backend schema, lesson authoring UI, or progress projection
  has been changed.
- The implementation task breakdown exists, but no task is complete merely
  because its plan or prompt has been written.
- No production data retention policy has been changed.
- No pilot with children has been conducted.
