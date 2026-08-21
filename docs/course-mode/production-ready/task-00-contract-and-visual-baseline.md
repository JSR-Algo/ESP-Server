# Task 00 Master Prompt: Contract and Visual Baseline

```text
You are implementing Task 00 of Course Mode V2 in the TBOT workspace.

Outcome
Freeze one cross-repository fixture and one renderer-v4 visual composition
contract before any runtime code is implemented. The result must remove
ambiguity about schema identity, evidence names, embodied intents, layer order,
focus direction, safe listening posture, checksums, and V1 compatibility.

Repositories
- /Users/manhhodinh/Documents/TBOT/robot/esp32-server
- /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware
- /Users/manhhodinh/Documents/TBOT/tbot-backend

Read first
- robot/esp32-server/docs/superpowers/specs/2026-08-21-course-mode-word-mastery-design.md
- robot/esp32-server/docs/course-mode/runtime-contract.md
- robot/esp32-server/docs/course-mode/embodied-interaction.md
- robot/esp32-server/docs/course-mode/measurement-and-validation.md
- robot/esp32-server/docs/course-mode/production-ready/README.md
- tbot-backend/.worktrees/farm-v9-geometry-rollout-plan/docs/superpowers/plans/2026-08-21-farm-v9-geometry-rollout.md (read-only)

Fixed decisions
- Preset is courseCompanion.v2; never reinterpret tvideoJourney.v1.
- Shared fixture identity: course-mode-pilot-cat-ball, lesson version 1,
  session 00000000-0000-4000-8000-000000000201, primary animals.cat,
  optional secondary toys.ball.
- Canvas is 480x320. Farm v9 baseline uses object (20,168,95,95) and robot
  (118,160,150,150).
- Z-order is background, teaching object, robot overlay, transient focus cue.
- PRESENT_CENTER targets the single teaching object. PRESENT_LEFT and
  PRESENT_RIGHT refer to authored visual focus regions; they do not infer a
  direction from arbitrary model text.
- Before listening: speech completes, transient gesture settles, head centers,
  arms lower, motor activity stops, then the assessment window opens.
- Farm v9 is a pilot fixture only. Do not touch its worktree or production rollout.

Required work
1. Inspect AGENTS.md and repository status. Preserve unrelated dirty work.
2. Define one canonical JSON fixture with exact keys, target/activity IDs,
   evidence names, intent names, visual focus metadata, renderer identity, and
   deterministic canonical checksum rules.
3. Copy the fixture into all three repositories using their existing fixture
   conventions. Add parity tests that fail on semantic or checksum drift.
4. Add a visual-layout contract document and machine-readable test fixture for:
   canvas bounds, z-order, object/robot collision limits, caption-safe area,
   focus anchors, listening visibility, reduced-motion fallback, and mirroring.
5. Keep renderer-v4 compatibility. Do not invent renderer-v5 or require a new
   compositor for this task.
6. Add tests proving V1 fixtures and manifest checksums remain unchanged.
7. Update the Course Mode roadmap with exact fixture paths and test commands.

Acceptance gates
- All repositories parse the identical fixture and agree on its SHA-256.
- Unknown keys, raw servo values, out-of-canvas bounds, invalid z-order,
  unsupported intents, and answer-revealing recall activities fail closed.
- A static composition check proves the object remains fully visible, the robot
  does not cover it, and the listening cue remains readable at 480x320.
- No source file in the Farm v9 worktree is modified.
- No deployment, assignment, migration, or production mutation occurs.

Working method
- Use TDD and apply_patch for edits.
- Make small, repository-local commits; report every commit SHA.
- Stop on unexpected concurrent changes instead of reverting them.
- Run focused tests, then relevant V1 regression tests.
- Finish with changed files, commands/results, unresolved risks, and the exact
  contract version/checksum that Tasks 01-07 must consume.
```

