# Course Mode V2 Production-Ready Task Pack

## Purpose

This pack turns the approved `courseCompanion.v2` design into independently
executable engineering tasks. Each task file contains a standalone master prompt
that can be given to a fresh Codex task.

Production-ready in this pack means the software is capability-gated,
recoverable, observable, privacy-safe, regression-tested, hardware-validated,
independently reviewed, and deployable through a reversible canary. It does not mean the learning
method has been proven effective with children. That claim requires the
supervised child pilot in Task 09.

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

## Task 00 Frozen Baseline

Task 00 freezes `courseCompanion.v2.contract.v1` for fixture
`course-mode-pilot-cat-ball`. Its canonical SHA-256 is
`cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264`.
The renderer layout contract is `renderer-v4.course-mode-layout.v1` with
canonical SHA-256
`e61b56d1f8219a86c7f3986e7d5c70b91f512286604b5b206ef11e2c989d275c`.

Canonicalization is `tbot-json-c14n.v1`: remove only the top-level
`contractChecksum`, normalize strings to NFC, recursively sort object keys
lexicographically, preserve array order, serialize UTF-8 JSON with no
insignificant whitespace, and hash the resulting bytes with SHA-256. The JSON
artifacts declare these rules in `checksumRules`.

Semantic fixture paths:

- ESP: `main/tbot-server/tests/fixtures/course-mode/course-mode-pilot-cat-ball.json`
- Firmware: `tests/fixtures/course-mode/course-mode-pilot-cat-ball.json`
- Backend: `src/lessons/fixtures/course-mode/course-mode-pilot-cat-ball.json`

Visual-layout fixture paths:

- ESP: `main/tbot-server/tests/fixtures/course-mode/renderer-v4-visual-layout.json`
- Firmware: `tests/fixtures/course-mode/renderer-v4-visual-layout.json`
- Backend: `src/lessons/fixtures/course-mode/renderer-v4-visual-layout.json`

Focused verification commands:

```bash
cd robot/esp32-server
python3 -m pytest \
  main/tbot-server/tests/test_course_mode_task00_contract.py \
  main/tbot-server/tests/test_lesson_conversation_runtime.py -q

cd robot/TBOT-Firmware
python3 -m pytest \
  tests/test_course_mode_task00_contract.py \
  tests/test_lesson_cinematic_evidence_renderer_contract.py \
  tests/test_lesson_content_contract.py -q

cd tbot-backend
npx vitest run \
  src/lessons/fixtures/course-mode/course-mode-task00.contract.spec.ts \
  src/lessons/lesson-manifest.checksum-parity.spec.ts \
  src/lessons/tvideo-journey/fixtures/farm/farm-goldens.spec.ts \
  src/lessons/lesson-manifest.logic.validation.spec.ts
```

The visual rationale and static composition rules are documented in
[Course Mode renderer-v4 visual layout](../visual-layout-contract.md).

## Dependency Order

| Task | Deliverable | Depends on | May run in parallel |
| --- | --- | --- | --- |
| 00 | Cross-repository contract and visual-layout fixture | None | No |
| 01 | ESP semantic Course Mode runtime | 00 | After 00 only |
| 02 | ESP embodied-action dispatcher | 00, stable Task 01 types | With late Task 01 tests only |
| 03 | Firmware lesson-owned embodied channel | 00, Task 02 wire contract | No |
| 04 | Backend authoring, manifest, evidence, progress | 00, stable Task 01 contract | With Task 03 |
| 05 | Production-quality pilot lesson and assets | 03, 04; Farm v9 evidence optional | No |
| 06 | Runtime, integration, recovery, and soak validation | 01-05 | No |
| 07 | Physical robot HIL, comfort, audio, and safety validation | 03, 05, 06 | No |
| 08 | Independent production-readiness review and GO/NO-GO | 00-07 | No |
| 09 | Controlled canary, educator review, child pilot | 08 | No |

Do not start Task 09 because code is merged. Start it only when Task 08 issues a
signed GO verdict against the runtime and physical evidence from Tasks 06-07.

## Task Files

1. [Task 00: contract and visual baseline](task-00-contract-and-visual-baseline.md)
2. [Task 01: ESP semantic runtime](task-01-esp-semantic-runtime.md)
3. [Task 02: ESP embodied dispatcher](task-02-esp-embodied-dispatcher.md)
4. [Task 03: firmware embodied channel](task-03-firmware-embodied-channel.md)
5. [Task 04: backend authoring and progress](task-04-backend-authoring-progress.md)
6. [Task 05: pilot lesson and asset authoring](task-05-pilot-lesson-authoring.md)
7. [Task 06: runtime and integration validation](task-06-runtime-integration-validation.md)
8. [Task 07: physical robot validation](task-07-physical-robot-validation.md)
9. [Task 08: independent production-readiness review](task-08-production-readiness-review.md)
10. [Task 09: production canary and child pilot](task-09-production-canary-and-pilot.md)

## Definition of Done

Course Mode V2 is technically production-ready only when:

- every task through Task 07 is complete with committed evidence;
- Task 08 independently reproduces the critical gates and issues GO;
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
