# Course Mode V2 Production-Ready Task Pack

## Purpose

This pack turns the approved `courseCompanion.v2` design into independently
executable engineering tasks. Each task file contains a standalone master prompt
that can be given to a fresh Codex task.

Production-ready in this pack means the software is capability-gated,
recoverable, observable, privacy-safe, regression-tested, hardware-validated,
and deployable through a reversible canary. It does not mean the learning
method has been proven effective with children. That claim requires the
supervised child pilot in Task 07.

## Fixed Product Contract

- Learner: Vietnamese-speaking child aged 3-5.
- Session: approximately 8-10 minutes.
- Curriculum: one primary English word and one optional secondary word.
- Success: meaning, independent recall, second context, and delayed recall.
- Conversation: acknowledge side stories, respond briefly, then return through
  a natural authored bridge; safety and emotional needs take priority.
- Body behavior: face, head, arms, visual focus, and speech express one teaching
  decision; the robot becomes still before opening an assessed listening window.
- Feedback: no disappointed face for a normal miss; strong celebration is
  reserved for strong independent evidence.
- Compatibility: `tvideoJourney.v1` remains unchanged. V2 is selected only by
  explicit contract, server flag, backend flag, and device capability.

## Visual Pilot Baseline

Farm Journey v9 is the visual-layout pilot fixture, not a runtime dependency.
Its approved renderer-v4 geometry on a `480x320` canvas is:

```text
Teaching object: left 20,  top 168, width 95,  height 95
Robot overlay:   left 118, top 160, width 150, height 150
```

The active Farm v9 rollout belongs to Codex task
`01a018d4-b776-77b2-a013-595958dcf9f3` in the backend worktree
`tbot-backend/.worktrees/farm-v9-geometry-rollout-plan`. Course Mode tasks must
not edit, reset, deploy, or republish that worktree. They may consume its final
geometry, renderer, checksum, and physical evidence after those artifacts exist.

## Dependency Order

| Task | Deliverable | Depends on | May run in parallel |
| --- | --- | --- | --- |
| 00 | Cross-repository contract and visual-layout fixture | None | No |
| 01 | ESP semantic Course Mode runtime | 00 | After 00 only |
| 02 | ESP embodied-action dispatcher | 00, stable Task 01 types | With late Task 01 tests only |
| 03 | Firmware lesson-owned embodied channel | 00, Task 02 wire contract | No |
| 04 | Backend authoring, manifest, evidence, progress | 00, stable Task 01 contract | With Task 03 |
| 05 | Production-quality pilot lesson and assets | 03, 04; Farm v9 evidence optional | No |
| 06 | Cross-repository adversarial QA and hardware gates | 01-05 | No |
| 07 | Controlled canary, educator review, child pilot | 06 | No |

Do not start Task 07 because code is merged. Start it only when Task 06 produces
a signed release-candidate evidence bundle with no unresolved release blocker.

## Task Files

1. [Task 00: contract and visual baseline](task-00-contract-and-visual-baseline.md)
2. [Task 01: ESP semantic runtime](task-01-esp-semantic-runtime.md)
3. [Task 02: ESP embodied dispatcher](task-02-esp-embodied-dispatcher.md)
4. [Task 03: firmware embodied channel](task-03-firmware-embodied-channel.md)
5. [Task 04: backend authoring and progress](task-04-backend-authoring-progress.md)
6. [Task 05: pilot lesson and asset authoring](task-05-pilot-lesson-authoring.md)
7. [Task 06: release-candidate QA](task-06-release-candidate-qa.md)
8. [Task 07: production canary and child pilot](task-07-production-canary-and-pilot.md)

## Definition of Done

Course Mode V2 is technically production-ready only when:

- every task through Task 06 is complete with committed evidence;
- all three feature/capability gates default to off;
- V1 regression vectors remain byte-for-byte compatible;
- the same pilot manifest passes backend, ESP, and firmware contract tests;
- no servo movement overlaps child assessment;
- stop, disconnect, restart, and stale-session paths restore a safe rest pose;
- raw audio, transcript, free-form family stories, and pronunciation scores are
  absent from durable progress events and logs;
- physical tests pass for audio contamination, motion comfort, power,
  temperature, cache recovery, and lesson completion;
- rollback is rehearsed before canary enablement.

