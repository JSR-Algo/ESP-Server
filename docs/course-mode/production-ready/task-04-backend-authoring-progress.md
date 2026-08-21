# Task 04 Master Prompt: Backend Authoring and Progress

```text
You are implementing Task 04 of Course Mode V2 in
/Users/manhhodinh/Documents/TBOT/tbot-backend.

Outcome
Author, validate, version, publish-gate, serve, and measure courseCompanion.v2
without changing TVideo V1 semantics or storing sensitive child conversation.

Read first
- ../robot/esp32-server/docs/course-mode/production-ready/README.md
- ../robot/esp32-server/docs/superpowers/plans/2026-08-21-course-mode-v2-backend-authoring-progress.md
- ../robot/esp32-server/docs/course-mode/runtime-contract.md
- ../robot/esp32-server/docs/course-mode/measurement-and-validation.md
- Task 00 fixture/checksum and stable Task 01 evidence schema
- current lesson authoring, manifest canonicalization, event ingest, assignment,
  and parent progress code/tests

Required work
- Implement the separate src/lessons/course-mode domain, migrations, repository,
  exact validator, authoring endpoints, canonical checksum, manifest projection,
  publish flag, privacy-safe evidence ingest, and parent-facing projection from
  the approved backend plan.
- COURSE_MODE_V2_PUBLISH_ENABLED defaults false. Draft storage may work while
  publishing/serving remains blocked.
- Validate one primary and optional secondary target, interactive opening,
  meaning/practice/transfer/delayed activities, approved facts, approved visual
  focus, named embodied intents, answer-leakage flags, and age/session policy.
- Reject raw servo values, unsupported faces/intents, overclaiming reward text,
  arbitrary generated facts, invalid visual geometry, or recall activities that
  reveal the answer.
- Durable events may include stable IDs, evidence level, support category,
  confidence band, timestamps, and review recommendation. They must exclude raw
  transcript, audio, pronunciation score, free-form story, family detail, and
  unrestricted model output.
- Keep published versions immutable and use existing version/cache/checksum
  semantics. Never mutate Farm v8/v9 as part of this task.

Acceptance gates
- Exact Task 00 fixture round-trips with the same checksum as ESP and firmware.
- Migration up/down and repository transaction tests pass.
- Authoring, manifest, feature-gate, event allowlist, deduplication, progress,
  privacy, and V1 regression tests pass.
- Logs/error responses redact contract content that may contain child-facing text.
- Flag-off behavior is explicit and safe; no partially served V2 manifest exists.
- No deployment occurs, no database migration is applied to production, and no
  content is published.

Working method
- Follow the backend plan task-by-task with TDD and small commits.
- Inspect AGENTS.md/status first. Preserve unrelated changes and all worktrees,
  especially .worktrees/farm-v9-geometry-rollout-plan.
- Use the repository's current next migration number if 122 is occupied; update
  all references consistently and explain the choice.
- Finish with commit SHAs, test/typecheck/lint results, migration identity,
  privacy evidence, API examples, and Task 05 authoring prerequisites.
```
