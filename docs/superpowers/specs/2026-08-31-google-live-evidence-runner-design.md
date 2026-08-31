# Google Live Unified Evidence Runner Design

## Status

Approved interactively on 2026-08-31. This design closes the six evidence
production and provenance blockers discovered while verifying the Google Live
production reliability runbook. It extends the approved 2026-08-28 reliability
design without weakening any existing release contract.

## Goal

Provide one production-safe operator workflow that can create every artifact
required by the exact-candidate Google Live release gate. The workflow must run
the existing conversation, WebSocket, physical, lesson, reconnect, and soak
paths without changing their accepted behavior or silently manufacturing
evidence.

A successful run must prove that all six release layers belong to the same
candidate, were produced by the declared commands and bounded journeys, contain
no prohibited child or credential data, and satisfy the existing lifecycle,
latency, cleanup, resource, and compatibility contracts.

## Current Release Blockers

The implementation must close all of these blockers before the full Task 10
release dry run can pass:

1. Raw server captures do not persist a reusable, bounded standalone log
   verdict with candidate-bound start/end anchors.
2. Production firmware does not send an optional `evidence_journey_id`, so a
   physical connection cannot be bound to an operator-owned evidence window.
3. The candidate-soak CLI validates a supplied 33-execution manifest but cannot
   produce that manifest from real journeys.
4. Production transcript logs contain metadata such as character counts, but
   no privacy-safe proof that ten observed turns matched the ten operator
   expectations and the required post-interrupt/post-lesson utterances.
5. The deterministic report is not bound to the exact canonical pytest node
   manifest, JUnit result, counts, and supporting checksums.
6. `commands.txt` is only manual diagnostic context; no tool executes and
   records the same structured, redacted command invocation.

## Constraints

- Preserve the Google Live model, voice, language, prompt, fallback,
  reconnection, lesson, and ownership behavior already protected by the
  reliability suite.
- The firmware protocol addition is optional and backward compatible. Normal
  robots and non-audit connections must behave exactly as before.
- Never persist raw transcripts, prompts, child audio, audio payloads, cookies,
  credentials, API keys, bearer tokens, session-resumption handles, HMAC keys,
  or raw exception text.
- Never hand-author, splice, or insert evidence markers after a journey.
- Never upgrade raw Task 4 transport evidence from `SKIPPED/PENDING`; only the
  canonical Task 5 bounded correlation may create the WebSocket composite
  `PASS`.
- The runner must not deploy, flash, reset, or otherwise mutate physical
  hardware. Physical execution begins only after explicit operator readiness.
- A retry uses a new UTC `RUN_ID`. A later pass cannot overwrite or erase the
  first failed evidence set.

## Architecture

Add a single operator-facing `google_live_evidence_runner.py` that owns evidence
orchestration but delegates product behavior and validation to the existing
scripts and shared validators.

The runner has six layer commands plus a final aggregation command:

```text
deterministic
real-api
websocket
physical
candidate-soak
finalize
```

`server_regression` is produced from the canonical bounded server-log evidence
created during the WebSocket and physical journeys. `finalize` verifies the
complete evidence tree, creates external checksums, and invokes the existing
read-only release gate.

The runner owns only orchestration concerns:

- candidate identity and UTC run identity;
- structured subprocess invocation and redacted provenance;
- evidence journey identifiers and bounded capture lifecycle;
- atomic artifact writes and supporting checksums;
- terminal layer state and first-failure preservation.

Existing Google Live clients, WebSocket harnesses, analyzers, physical audit,
soak logic, and release validators remain authoritative for their contracts.

## Journey Scope And Bounded Log Evidence

The firmware WebSocket hello gains an optional `evidence_journey_id`. The
synthetic WebSocket harness uses the same field. The server validates its
format, binds it to the authenticated connection and immutable peer identity,
and emits candidate-scoped `reliability_window_start` and
`reliability_window_end` markers during the real connection lifecycle.

The end marker is emitted only after terminal output and connection-owned
cleanup evidence are available. Reconnects use the existing validated Live
connection transition ledger; they do not create a second unrelated window.
Duplicate starts, duplicate ends, foreign journey identifiers, mismatched peer
hashes, missing terminal cleanup, or untrusted connection transitions fail the
layer.

The bounded log report becomes a persisted output of the canonical analyzer.
Temporary synthesized anchors remain test-only compatibility behavior and
cannot authorize a production release.

## Privacy-Safe Transcript Match Proof

Expected operator utterances are supplied through a protected local input or
stdin and never through recorded command arguments. At run start, the runner
creates an ephemeral HMAC key held only in memory.

For each expected and observed transcript, the trusted in-process proof
component:

1. applies one versioned Unicode and whitespace normalization algorithm;
2. computes a run-scoped HMAC for comparison;
3. records only the journey ID, turn slot, character count, timestamps, and
   `matched: true/false`;
4. discards the normalized text, source text, and HMAC key.

The persisted report must not contain reusable transcript digests. It contains
only the exact expected slot count, observed slot count, match count, mismatch
count, ordering proof, and post-interrupt/post-lesson match verdicts. Missing,
duplicate, reordered, or unmatched slots fail closed.

The proof component is part of the candidate code and is covered by
deterministic tests. Enabling raw transcript logging or fabricating legacy log
lines is forbidden.

## Candidate Soak Producer

The runner exposes the existing in-process candidate journey execution through
a production CLI path. It performs the exact Task 6 sequence:

- ten ordinary Vietnamese turns;
- ten mid-output interruption turns;
- ten newest-intent validation turns;
- one reconnect journey;
- one bounded lesson entry/interactive/exit journey;
- one final post-lesson conversation journey.

This yields exactly 33 ordered executions. Every execution receives a unique
journey ID, candidate-bound evidence scope, a single bounded UTC window, trusted
latency proof, resource samples, and cleanup evidence. Required quiet padding
uses real monitored windows bound to the final trusted connection lineage; gaps
greater than the approved bound do not count toward the 1,800-second duration.

The producer writes the replay manifest only after all execution entries and
the exactly-once final cleanup record are complete. The existing replay mode
then independently validates the produced manifest before it can become the
`candidate_soak` layer.

## Deterministic Coverage Binding

The deterministic command owns a canonical, versioned pytest node manifest. It
runs exactly those nodes and produces:

- `deterministic/node-manifest.txt`;
- `deterministic/pytest.xml`;
- `deterministic/report.json`.

The report includes the manifest schema, SHA-256, exact node count, executed
test count, passed/failed/skipped/error counts, JUnit SHA-256, and candidate
identity. The release gate independently parses the manifest and JUnit,
requires exact node-set and count equality, rejects duplicate or unexpected
nodes, verifies every supporting checksum, and requires zero failures, errors,
or unapproved skips.

Changing the compatibility matrix requires an explicit manifest update and
review; an unrelated passing test file cannot satisfy the layer.

## Structured Command Provenance

All evidence-producing commands are represented as structured command specs,
not shell strings. A command spec contains a stable command ID, executable,
non-secret arguments, secret-source descriptors, input paths, output paths, and
the expected exit policy.

The runner uses the same object to execute the subprocess and to write a
canonical provenance entry. Secret values are injected through environment,
stdin, or in-process token minting. The provenance entry records placeholders
such as `<env:GOOGLE_API_KEY>` and never the expanded value. Protected absolute
paths are represented by stable evidence-relative labels.

Each entry records start/end UTC timestamps, exit code, candidate identity,
input/output artifact checksums, and the command-spec digest. Manual shell
commands may remain in operator notes but cannot satisfy release provenance.

The release gate requires the exact command IDs needed for the six layers,
verifies their order and artifact bindings, and applies the shared recursive
privacy policy to the provenance file.

## Artifact And State Model

Each layer follows this state machine:

```text
PENDING -> RUNNING -> PASS | FAIL | SKIPPED
```

Terminal states are immutable within a `RUN_ID`. The first failure is retained
with a safe classification, bounded timestamps, and the checksums of evidence
available at failure time.

Reports and provenance are written to temporary files, flushed, `fsync`ed, and
atomically replaced. External checksums are generated only after all required
files are closed. Output paths are rejected if they alias, symlink to, or share
an inode with any input evidence or checksum manifest.

The final evidence tree remains:

```text
task-artifacts/google-live/$RUN_ID/
  deterministic/report.json
  deterministic/node-manifest.txt
  deterministic/pytest.xml
  server-regression/report.json
  real-api/report.json
  websocket-e2e/report.json
  physical/report.json
  candidate-soak/report.json
  timeline.log
  commands.jsonl
  commands.txt
  checksums.sha256
  release-verdict.json
```

`commands.txt` is a human-readable redacted projection of `commands.jsonl`.
Only the structured JSONL provenance is release-authoritative.

## Failure Handling

- Authentication, configuration, quota, protocol, timeout, network, provider,
  cleanup, resource, identity, privacy, and evidence-integrity failures retain
  their safe classification.
- Cancellation and timeout paths run cleanup exactly once and prove zero owned
  Live sessions, receive loops, WebSockets, and background tasks.
- A failed layer does not continue into a dependent physical or release step.
- The runner does not automatically rerun a failed layer. The operator records
  the environmental condition and starts a new `RUN_ID` after classification.
- Malformed or contradictory external evidence returns deterministic failure
  JSON rather than an exception or partial success.

## Testing Strategy

Implementation follows test-driven development and the existing per-task
review workflow.

### Deterministic tests

- Optional firmware hello field parsing, validation, and backward compatibility.
- Server scope ownership, real lifecycle anchors, reconnect transitions,
  duplicate/foreign marker rejection, and exactly-once cleanup.
- HMAC normalization and match behavior, missing/reordered slots, key disposal,
  recursive privacy scans, and proof reports containing no transcript or digest.
- Exact 33-execution soak production, monitored duration, padding, resources,
  latency, ownership, replay equivalence, and cancellation cleanup.
- Exact node-manifest/JUnit/report binding and checksum tamper cases.
- Structured command execution/provenance equality, secret injection,
  redaction, path normalization, exit policies, and atomic writes.
- Release-gate rejection for missing, skipped, contradictory, foreign,
  unchecksummed, aliased, or privacy-unsafe supporting artifacts.

### End-to-end tests

A deterministic synthetic run creates all six layer shapes without credentials
or hardware, finalizes checksums, and obtains `PASS`. Mutating any report,
manifest, JUnit file, command entry, identity field, window, transition, cleanup
record, or supporting checksum changes the release verdict to `FAIL`.

Real Google API, authenticated WebSocket, and physical robot runs remain
explicit opt-in gates. No automated test flashes, deploys, or resets hardware.

## Delivery And Review

The remediation is split into independently reviewed tasks:

1. Optional firmware/server journey scope and persisted bounded log verdict.
2. Privacy-safe transcript match proof.
3. Production candidate-soak evidence producer.
4. Deterministic manifest/JUnit binding.
5. Structured command provenance runner.
6. Unified orchestration and release-gate integration.
7. Runbook update and full Task 10 verification.

Every task requires an implementer TDD cycle, independent spec review,
independent code-quality review, and controller verification. No task advances
with an open finding.

## Success Criteria

- All six documented blockers are closed by checked-in, tested code.
- The unified runner can produce the complete evidence layout without
  hand-authored markers or reports.
- Existing Google Live and classic-pipeline behavior remains unchanged outside
  opt-in evidence mode.
- No prohibited content appears in any report, log index, checksum manifest, or
  command provenance artifact.
- The exact-candidate release gate fails on every missing, skipped, mismatched,
  contradictory, unbound, or tampered layer and supporting artifact.
- Task 10 deterministic and regression suites pass before any operator performs
  real or physical execution.

## Non-Goals

- Automatic deployment, firmware flashing, hardware reset, or remote robot
  control.
- Persisting raw speech or transcripts for later review.
- Changing Google Live prompts, model selection, voice, language, fallback, or
  lesson behavior.
- Treating environmental unavailability or a raw transport observation as a
  product PASS.
