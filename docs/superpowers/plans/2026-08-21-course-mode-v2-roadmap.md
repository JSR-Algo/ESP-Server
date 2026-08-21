# Course Mode V2 Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver `courseCompanion.v2` as a child-friendly, mastery-based Course Mode while preserving all `tvideoJourney.v1` behavior.

**Architecture:** Implement three gated vertical slices in dependency order. The ESP server first owns truthful word evidence and adaptive session orchestration. Firmware then adds a session-bound embodied-action channel that cannot overlap child assessment. Backend finally authors/publishes V2 contracts and ingests privacy-safe word evidence. V2 remains dark unless the lesson, server, and device all advertise capability.

**Tech Stack:** Python 3/Pytest/Google Live tools, C++17/ESP-IDF/cJSON/host-native tests, NestJS/TypeScript/PostgreSQL/Vitest, JSON lesson manifests, existing lesson WebSocket protocol.

---

## Source Specs

- `docs/superpowers/specs/2026-08-21-course-mode-word-mastery-design.md`
- `docs/course-mode/pedagogy-and-mastery.md`
- `docs/course-mode/conversation-persona.md`
- `docs/course-mode/embodied-interaction.md`
- `docs/course-mode/runtime-contract.md`
- `docs/course-mode/measurement-and-validation.md`
- `docs/course-mode/production-ready/README.md`

## Production Task Pack

The executable backlog and standalone master prompts live under
`docs/course-mode/production-ready/`. That pack adds an explicit contract and
visual-baseline task before the three implementation phases, followed by pilot
authoring, adversarial QA, and a separately authorized production canary.

Farm Journey v9 is the renderer-v4 visual pilot baseline only. Its approved
`480x320` geometry is object `(20,168,95,95)` and robot `(118,160,150,150)`.
Course Mode must not depend on, edit, or silently join the separate Farm v9
production rollout. It may reuse final read-only evidence and geometry contracts.

## Delivery Order

1. [ESP server mastery and orchestration](2026-08-21-course-mode-v2-esp-runtime.md)
2. [Firmware embodied interaction](2026-08-21-course-mode-v2-firmware-embodied.md)
3. [Backend authoring and progress](2026-08-21-course-mode-v2-backend-authoring-progress.md)

Do not run phases 2 and 3 in parallel with phase 1. Their contracts consume the
authoritative types and wire decisions established by phase 1.

## Cross-Repository Contract Freeze

Before implementation, create one shared fixture in each repository with these
identities:

```json
{
  "presetId": "courseCompanion",
  "presetVersion": 2,
  "lessonId": "course-mode-pilot-cat-ball",
  "lessonVersion": 1,
  "lessonSessionId": "00000000-0000-4000-8000-000000000201",
  "primaryTargetId": "animals.cat",
  "secondaryTargetId": "toys.ball"
}
```

The fixture checksum, target order, activity IDs, evidence names, embodied
intent names, and event payload fields must remain byte-for-byte consistent.

## Release Controls

Use all three gates:

```text
Backend: COURSE_MODE_V2_PUBLISH_ENABLED=false
ESP server: LESSON_COURSE_MODE_V2_ENABLED=false
Firmware capability: lessonCourseMode.version=2, embodiedActions=true
```

Selection rule:

```text
V2 authored + backend publish enabled + server enabled + device capable -> V2
anything else -> existing V1 path or explicit unsupported error
```

Never reinterpret a V1 manifest as V2 and never fall back from a partially
started V2 session into the V1 state machine.

## Global Acceptance Gates

- [ ] V1 conversation/runtime regression suites remain green without fixture changes.
- [ ] Supported repetition cannot emit `INDEPENDENT_RECALL` or `MASTERED_TODAY`.
- [ ] Every accepted independent recall passes the answer-leakage gate.
- [ ] Routine side conversation can return naturally without resetting evidence.
- [ ] Safety routes never redirect to vocabulary.
- [ ] No servo action overlaps an assessed child speech window.
- [ ] All stop/restart/disconnect paths return face, head, and arms to rest.
- [ ] Backend strips raw transcript, audio, scores, and free-form family content.
- [ ] Twenty scripted child journeys pass end to end.
- [ ] Physical hardware validates noise, power, temperature, ACK, and comfort behavior.
- [ ] Rollback disables new V2 assignment while active sessions follow the established completion policy.

## Final Cross-Repository Verification

Run from the repository named above each command.

```bash
# robot/esp32-server/main/tbot-server
python3 -m pytest \
  tests/test_course_mode_contract.py \
  tests/test_word_mastery.py \
  tests/test_course_orchestrator.py \
  tests/test_google_live_course_mode.py \
  tests/test_course_mode_e2e_journeys.py -q

# robot/TBOT-Firmware
bash scripts/run_host_native_lesson_embodied_action_test.sh
bash scripts/run_host_native_lesson_handler_test.sh
bash scripts/run_host_native_lesson_coverage.sh --txt --print-summary
python3 -m pytest \
  tests/test_lesson_embodied_action_contract.py \
  tests/test_lesson_content_contract.py -q

# tbot-backend
npx vitest run \
  src/lessons/course-mode/course-mode.contract.spec.ts \
  src/lessons/course-mode/course-mode.repository.spec.ts \
  src/lessons/lesson-manifest.course-mode.spec.ts \
  src/lessons/lesson-event-ingest.course-mode.spec.ts \
  src/lessons/parent-learning-progress.course-mode.spec.ts
npm run typecheck
npm run lint
```

Expected: every command passes; firmware lesson coverage remains 100%; V1
contract vectors and existing lesson suites are unchanged.

## Documentation Closeout

- [ ] Update `docs/course-mode/README.md` from design-only to implemented slice status.
- [ ] Add exact test evidence and known limitations to the high-risk initiative validation file.
- [ ] Update `LESSON_PRODUCTION_PLAN.md` with separate software, physical, educator, privacy, and child-pilot evidence.
- [ ] Do not claim child learning quality until supervised child validation is complete.
