# Google Live Evidence Profile Remediation Design

**Status:** Approved for planning

**Date:** 2026-09-01

## Problem

The unified evidence plan currently applies the physical transcript-proof
finalization contract to every enrolled journey. That contract correctly
requires an ordered transcript plan ending in `post_lesson`, followed by the
causally related output-idle event. It cannot be satisfied by ordinary
candidate-soak stages such as conversation, quiet, reopen, or reconnect without
inventing transcript events or changing the approved 33-execution workload.

The candidate-soak validator also requires one trusted `journeyType` for every
bounded log report, while the production reliability start marker does not
currently emit a stage claim. Deriving the stage from client hello, a log
consumer, or a journey identifier would create an untrusted or ambiguous
release anchor.

The remediation must preserve the existing physical transcript proof, exact
candidate workload, normal conversation flow, model/configuration/fallback
behavior, privacy rules, and bounded cleanup lifecycle.

## Decision

Evidence enrollment gains two authenticated, immutable claims:

- `journeyType`: the exact evidence stage represented by the bounded window.
- `proofProfile`: the finalization proof contract for that journey.

The claims are accepted only through the authenticated evidence control plane,
stored in the server-owned enrollment registry, bound to the claimed connection,
and emitted by the server. They are never accepted from robot/client hello and
cannot be replaced after registration.

## Supported Claims

`journeyType` is restricted to:

```text
conversation
bargein
quiet
quiet_padding
reopen
reconnect
lesson
conversation_after_lesson
physical
websocket
```

`proofProfile` is restricted to:

```text
physical-transcript
candidate-lifecycle
```

The control API remains backward compatible. An enrollment that omits both new
fields receives:

```text
journeyType=physical
proofProfile=physical-transcript
```

Supplying only one of the two fields is rejected. Explicit combinations are
validated against a fixed compatibility table:

- `physical` uses `physical-transcript`.
- Candidate-soak journey types use `candidate-lifecycle`.
- `websocket` uses `candidate-lifecycle`; raw transport remains
  `SKIPPED/PENDING` until bounded server-log correlation authorizes the
  composite result.

Unknown fields, unknown values, incompatible combinations, and attempts to
change either claim fail closed.

## Registry And Public Views

The registry stores both claims as immutable enrollment state. Claims are
included in safe snapshots and terminal tombstones because they contain no
secret or transcript material. They are included in exact claim matching so a
cached or retried finalization cannot be reused across profiles or stages.

The existing privacy boundary remains unchanged:

- no transcript, normalized text, HMAC, expected MAC, or key is exposed;
- no credential, cookie, session handle, or raw exception is persisted;
- key zeroization and bounded tombstones retain their existing behavior.

## Finalization Profiles

### Physical Transcript

`physical-transcript` retains the current strict behavior:

- the transcript plan contains `1..64` ordered expectations;
- mismatch, duplicate, reorder, wrong phase, or extra proof is sticky failure;
- the final expectation is `post_lesson`;
- the causally bound response must emit matching output start and output idle;
- invalid proof terminalizes only after provider cleanup is verified;
- incomplete cleanup remains retryable and emits no terminal marker or
  tombstone.

No Task 4 transcript requirement is weakened or made optional for this profile.

### Candidate Lifecycle

`candidate-lifecycle` carries no transcript expectations and does not execute
the transcript matcher. Its finalization requires:

- an active exact registry claim for device, client, journey, claims, and
  candidate identity;
- a server-issued bounded reliability window;
- provider finalization status `PASS`;
- zero pending evidence-owned tasks;
- a valid Live connection-transition ledger;
- exactly-once provider cleanup under the existing bounded cancellation policy.

It cannot obtain PASS from transcript state, client assertions, synthetic log
anchors, or raw WebSocket transport observations. Stage-specific behavior,
latency, interruption, reconnect, quiet-window, lesson, and resource verdicts
remain the responsibility of the canonical bounded log analyzer and candidate
soak replay validator.

## Trusted Log Binding

After a successful hello ACK, the server emits the reliability start marker
with:

```text
journeys=<registry journeyType>
```

The value comes only from the claimed registry enrollment. The hello journey ID
selects an already authenticated claim but cannot provide or override the type.
The analyzer continues requiring exactly one recognized claimed journey. A
missing, duplicate, unknown, malformed, or conflicting claim fails the bounded
report.

The marker remains post-ACK and server-issued. Existing exact window identity,
peer binding, candidate identity, connection IDs, timestamps, and reconnect
ledger checks remain authoritative.

## Candidate-Soak Producer

Each of the exact 33 executions registers a unique
`candidate-soak.<RUN_ID>.<sequence>` enrollment with:

- its exact stage as `journeyType`;
- `proofProfile=candidate-lifecycle`;
- the exact candidate identity;
- no transcript text, HMAC key, MAC, or fabricated transcript plan.

Quiet padding uses its own unique `quiet_padding` enrollment and monitored
bounded window. Only reconnect may change the server connection ID, through the
existing trusted same-device transition.

The default barge-in producer uses the existing audio helper. The producer does
not replace audio interruption with text input. Monitor windows must support
the required padding rather than failing merely because padding is needed.

The manifest remains in memory until all 33 executions, padding windows,
resource samples, and exactly-once cleanup are complete. Failure, cancellation,
invalid scope, or analyzer failure leaves no partial `journey-evidence.json`.
Replay remains a separate command and independently validates exact equality
before writing the layer report.

## Error Handling

- Invalid or incompatible enrollment claims return deterministic safe 4xx
  errors without echoing request material.
- A claim conflict or reuse remains rejected even after terminalization.
- Candidate finalization with failed, pending, malformed, timed-out, or
  cancelled cleanup remains nonterminal and retryable where the existing
  lifecycle permits retry.
- A bounded analyzer report without the exact trusted journey type fails the
  execution and prevents manifest publication.
- Cleanup is invoked exactly once; cancellation still attempts bounded cleanup
  without masking caller cancellation.
- A failed run is immutable. Retrying the overall evidence workflow requires a
  new UTC `RUN_ID`.

## Testing

Implementation follows RED-GREEN-REFACTOR and adds tests for:

- backward-compatible default physical claims;
- strict field/combo validation and immutable registry ownership;
- safe snapshots/tombstones containing claims but no secrets;
- physical transcript behavior remaining unchanged;
- candidate lifecycle finalization without transcript fabrication;
- cleanup failure/pending/timeout/cancellation remaining nonterminal;
- trusted `journeys=` marker emission and client-hello spoof rejection;
- analyzer rejection of missing, unknown, duplicate, and foreign types;
- exact per-stage candidate enrollment across 33 executions and quiet padding;
- existing audio-helper barge-in path and monitor padding;
- atomic manifest behavior, replay equivalence, resource sampling, duration,
  gaps, reused windows, and exactly-once cleanup;
- regressions across Task 3 teardown and Task 4 transcript/AEC/causality suites.

Every implementation stage requires independent spec review, independent code
quality review, and fresh controller verification before Task 5 is complete.

## Non-Goals

- No model, prompt, voice, language, lesson, fallback, or normal client change.
- No trust in robot/client claims beyond the existing journey ID lookup.
- No transcript fabrication for candidate soak.
- No synthetic production anchors or hand-authored reports.
- No automatic deployment, firmware flashing, hardware reset, reconnect, or
  physical robot control.
