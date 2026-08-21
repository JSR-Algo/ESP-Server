# Task 06 Master Prompt: Runtime and Integration Validation

```text
You are executing Task 06, the software runtime release gate for Course Mode V2.
Do not deploy production and do not use children as test subjects.

Outcome
Prove that the integrated backend, ESP server, firmware simulator/native layer,
and pilot lesson behave correctly over complete sessions, failures, recovery,
repetition, and sustained operation. Produce a reproducible runtime evidence
bundle for the physical test and independent review tasks.

Read first
- robot/esp32-server/docs/course-mode/production-ready/README.md
- robot/esp32-server/docs/course-mode/measurement-and-validation.md
- all Task 00-05 handoff reports, commit SHAs, fixtures, checksums, and test commands
- existing simulation, E2E, voice, renderer, cache, reconnect, and log-verifier runbooks

Candidate freeze
- Pin exact backend, ESP, firmware, pilot lesson/version, renderer, fixture
  checksum, derivative checksum, flags, and test-tool identities.
- Build from clean isolated worktrees and record all dependency/tool versions.
- Confirm every production feature flag defaults off.

Required runtime lanes
1. Contract parity and V1 regression across backend, ESP, and firmware.
2. At least 20 deterministic full-session journeys covering early knowledge,
   repetition only, Vietnamese/mixed answer, partial speech, silence, low ASR
   confidence, echo, barge-in, side story, emotional share, refusal, fatigue,
   child question, delayed recall success/failure, one-word close, two-word
   completion, safety pause, and graceful technical close.
3. Failure and recovery: duplicate/stale events, stale action generation, ACK
   timeout, missing asset, corrupt cache, reconnect, ESP restart, backend timeout,
   firmware restart simulation, power-loss boundary simulation, and resume.
4. Timing: speech completion, gesture cancellation/settle, microphone opening,
   response latency, branch duration, session soft limits, delayed-recall spacing,
   and stop-to-rest acknowledgement.
5. Reliability: cold/warm cache, repeated sessions, bounded soak, heap/resource
   trend, task/thread leakage, telemetry delivery, idempotency, and retry bounds.
6. Privacy/security: malformed payloads, field bounds, authorization, event/log
   redaction, replay resistance, and proof that raw child content is not durable.
7. Visual runtime sampling at the start/middle/end of every cue for z-order,
   object visibility, focus direction, caption/listening safe zones, and fallback.

Hard assertions
- Immediate repetition never becomes independent recall or mastery.
- No motion command or unsettled state overlaps an assessment window.
- A normal miss never selects sad, crying, angry, or shaming feedback.
- Motion failure cannot mutate learning evidence or deadlock the session.
- Resume cannot replay prompts, celebrations, motion, or durable evidence.
- Unsupported capability fails before partial V2 session admission.
- V1 behavior and contract vectors remain unchanged.

Acceptance gates
- Every required lane has a fresh command, output, artifact path, and verdict.
- Soak duration and iteration count are stated; zero crashes, deadlocks, duplicate
  evidence, unbounded growth, or unrecovered sessions are observed.
- All failures are either fixed and rerun or listed as release blockers.
- The report pins every SHA/checksum and contains no credentials or child data.
- No production deployment, assignment, migration, OTA, or flag enablement occurs.

Deliverables
- Commit a runtime validation report and machine-readable release-candidate manifest.
- Include exact commands, counts, durations, logs, captures, checksums, failures,
  fixes, reruns, residual risks, and a RUNTIME PASS/FAIL verdict.
- RUNTIME PASS authorizes Task 07 physical testing only; it does not authorize
  production rollout or claim child-learning efficacy.

Working method
- Use verification-before-completion and systematic debugging for every failure.
- Apply scoped fixes in the owning repository with regression tests and focused
  commits, then rerun every affected lane.
- Preserve unrelated work and never alter the Farm v9 rollout worktree.
```

