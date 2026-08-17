# T5.4 Parent Progress and Completion Closure Design

## Context

The definitive physical renderer-v5 run proves the child-facing lesson completes all
nine steps, restores conversation mode, renders the three-layer scene, plays audio,
and applies MCP motion. Two production data-path defects still prevent strict T5.4
closure:

- Renderer-v5 sends per-step `lesson_prepare` / `lesson_start` frames, but the ESP
  runtime only emits `step_started` telemetry for legacy `lesson_step` ACKs. Parent
  Progress therefore receives `currentStep=null` and `positionPercent=0` while the
  lesson is visibly running.
- A power-cycle recovery can reuse a lesson session whose first
  `lesson_completed` row was persisted before the completion authority guard could
  flip the assignment. A later replay is a duplicate row. The backend currently
  makes lifecycle side effects conditional on a new insert, so it never retries the
  now-valid state transition and leaves the assignment `RUNNING`.

## Decision

Fix each defect at the layer that owns its invariant.

### Renderer-v5 step visibility

The accepted ACK for a renderer-v5 `lesson_start` frame is the authoritative point
at which a step is visible. At that point the runtime emits one `step_started` event
for the current `(assignmentId, sessionId, stepId)` and forwards the existing safe
telemetry fields (`stepType`, retry count, degraded state, and memory values when
present).

The runtime records which visual generation has already produced the event. Repeated
ACKs or retries for the same accepted generation do not produce duplicates. A new
accepted generation for the same step may produce another event only when it
represents an intentional re-render; backend event sequencing/dedup remains the wire
authority. Legacy `lesson_step` behavior remains unchanged.

### Idempotent authoritative completion

`lesson_completed` insertion remains idempotent. Completion projection is evaluated
when either:

1. the completion row is newly inserted, or
2. the completion row is a duplicate for the same assignment/session and the session
   now satisfies the existing authority guard (`started_at IS NOT NULL`).

The assignment flip remains a guarded SQL update inside the same transaction and
continues to allow only active states. A bare event without `sessionId`, a session
without `started_at`, a stale session, or a session owned by another assignment/device
must not complete anything. Rewards, notifications, course advancement, and metrics
run only when the assignment actually flips, preserving exactly-once side effects.

This makes replay repair the projection without weakening the anti-forgery contract.

## Rejected Alternatives

### Derive current progress from `step_completed`

This reports the step that just ended rather than the step currently visible, and it
weakens the existing Parent Progress contract. It also does not repair completion.

### Mint a new lesson session after recovery

This avoids the stranded duplicate by changing identity, but breaks the established
same-session recovery contract and fragments one physical lesson across sessions.

### Accept terminal events without the start guard

This would make completion easy to recover but would allow a session-bound forged or
out-of-order bare completion to release an assignment. The authority guard is retained.

## Data Flow

1. Firmware accepts and displays a renderer-v5 `lesson_start` generation.
2. ESP validates the ACK against assignment, session, step, sequence, and visual
   generation.
3. ESP forwards exactly one `step_started` for that accepted generation.
4. Backend persists the event and Parent Progress projects the active step and percent.
5. On terminal completion, ESP forwards/replays `lesson_completed` for the same session.
6. Backend inserts or recognizes the existing completion row, verifies `started_at`,
   atomically flips the assignment to `COMPLETED`, and performs side effects only for
   the successful flip.

## Testing

### ESP

- A renderer-v5 accepted `lesson_start` ACK emits one `step_started` with the current
  step and telemetry.
- A duplicate ACK for the same generation emits no second event.
- A rejected, stale, mismatched, or `lesson_prepare` ACK emits no `step_started`.
- Legacy `lesson_step` ACK behavior remains green.

### Backend

- A newly inserted started-plus-completed lifecycle completes the assignment.
- A completion inserted before `started_at`, followed by authoritative start and a
  duplicate completion replay, completes the assignment.
- The repair path advances the course and emits reward/notification effects only once.
- Duplicate completion without authoritative start remains guard-blocked.
- Cross-device, cross-assignment, and stale-session guards remain green.

### Production closure

After focused and full suites pass, merge both repositories through the project gate,
deploy backend and ESP, then run a fresh physical lesson. Capture Parent Progress after
each step and require the visible `currentStep`/percent to advance within the checklist
SLA. Require terminal read-back `COMPLETED`, the 101/101 verifier, audible audio, three
visible layers, applied MCP motion, and restored conversation face. Record evidence,
verify again on `main`, remove task worktrees/branches, and only then mark T5.4 `DONE`.
