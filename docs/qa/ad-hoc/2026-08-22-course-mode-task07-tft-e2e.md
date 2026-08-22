# Course Mode Task 07 Physical TFT Attended Run Ledger

Date: 2026-08-22 (Asia/Ho_Chi_Minh)

TFT sub-lane verdict: **TFT BLOCKED**

Task 07 verdict: **PHYSICAL BLOCKED**

Task 08 remains locked. This runbook prepares and validates redacted evidence;
none of its Python tools starts a stack, opens serial, contacts the robot, sends
a lesson action, captures media, plays speech, or performs cleanup or rollback.
Every physical or Docker-runtime action requires fresh point-of-use authority
under the
[`Task 07 master prompt`](../../course-mode/production-ready/task-07-physical-robot-validation.md).

## Authority Boundary

Use only an approved internal robot, a named adult operator, and an independent
adult observer. No child may participate and no child data may be used. Before
creating a session directory, the operator must separately confirm the sole
physical lease, clear motion envelope, immediate power isolation, accessible
stop path, reviewed local endpoints, and authorization for that attended
session.

The protected-test authority is the canonical shared ESP main path:

`/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/tests/test_lesson_voice_output_discipline.py`

Its required SHA-256 is
`08f77b5452301224b17b4b333d2d032fff40c06aa2eaea97fa90932dae7d97e3`.
The Task 07 implementation branch is frozen from ESP commit
`ed3c86d016b3196a0a609537ea432aaab661b41a`; its tracked copy is not the hash
authority and must not be edited or substituted.

## Phase 1: Config-Only Exact-Image Build

From the ESP repository root, bind only the reviewed backend worktree and exact
full SHA. Required credentials and local asset routing must already be supplied
through the approved operator process; do not place their values in this ledger.

```bash
export TBOT_BACKEND_WORKTREE=/absolute/path/to/reviewed/backend-worktree
export TBOT_BACKEND_GIT_SHA="$(git -C "$TBOT_BACKEND_WORKTREE" rev-parse HEAD)"
docs/docker/course-mode-physical-tft/up.sh --config-only
```

This compiles the reviewed source, builds the exact SHA-tagged image with the
compiled-materializer, exact-revision, and reviewed-clean-source labels,
verifies that path in-image, and renders Compose. The no-device preflight also
requires the exact sole image reference and binds the inspected immutable image
ID into its result.
It does not start the Compose project.

## Phase 2: No-Device Preflight

Only after attended-session authorization, create the concrete UTC session
directory beneath the Task 07 artifact root:

```bash
TFT_SESSION_DIR="/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-task07/tft-$(date -u +%Y%m%dT%H%M%SZ)-ac20"
mkdir "$TFT_SESSION_DIR"
```

Create `preflight-input.json` with the exact schema in
[`task-07-physical-tft-tooling-design.md`](../../course-mode/task-07-physical-tft-tooling-design.md).
Use the reviewed clean backend root/SHA and its exact SHA-tagged image, the
fixed Compose project and loopback endpoints, the approved concrete local-lab
asset/OTA/WebSocket routes, the five specified synthetic UUIDs, the pinned
immutable three-field `productionCandidateTarget`, the separate
`historicalInstallationProvenance`, a `sessionNvsBaseline` containing the exact
full current pre-install digest, the separate `activeLabApp`, the
canonical protected path/hash above, the concrete session directory, and a UTC
session start. The active lab source must be
`812f5d3e71d326b350e5b0d1df878d47ac60400e`; supply the exact qualified
application and bundle-root SHA-256 values at point of use. Do not copy
preliminary hashes into tooling or substitute the production target identity.
Do not infer a lab IP from current network state and do not add command,
environment, or credential fields.

Do not substitute the historical preserved-NVS digest for the session baseline.
The historical value describes only its retained installation. The preflight
accepts the exact caller-supplied current baseline and performs no device read.

From `main/tbot-server`:

```bash
python3 scripts/course_mode_physical_tft_preflight.py \
  --input "$TFT_SESSION_DIR/preflight-input.json" \
  --output "$TFT_SESSION_DIR/preflight-result.json"
```

The preflight invokes only the five internally fixed read-only Git/image-inspect/
Compose-config commands. It refuses a missing session directory and never
creates one implicitly.

In the attended ledger, update `sessionNvsPreservation.phase` monotonically:
`PRE_INSTALL_BASELINE` after the current baseline is retained, `POST_INSTALL`
only after an exact equal readback, and `POST_RESTORE` only after a second exact
equal readback following restore. Do not populate hashes for observations that
have not occurred. `TFT_PASS` requires `POST_RESTORE`; every verdict remains
bounded by `task07Verdict=PHYSICAL_BLOCKED`.

## Phase 3: Separately Authorized Stack Start

An operator-authorized stack start is outside this tooling. Stop here and return
to the master prompt for fresh point-of-use authorization. Record the operator,
observer, exact image, Compose project, time, and result in the attended ledger;
do not copy a start command into committed evidence.

## Phase 4: Materializer Receipts

After the separately authorized local stack is ready, the operator captures the
one-shot materializer receipt as `materialize-first.json`, repeats only the
approved idempotent materialization procedure, and captures
`materialize-rerun.json`. Receipt capture is outside these validators.

Validate both retained receipts offline:

```bash
python3 scripts/course_mode_physical_tft_receipt_verify.py \
  "$TFT_SESSION_DIR/materialize-first.json" \
  --rerun-receipt "$TFT_SESSION_DIR/materialize-rerun.json"
```

Any identity, schema, checksum, cue-count, conversation, or semantic-rerun
mismatch is a stop condition.

## Phase 5: Attended Capture and Trigger

The visual capture and adult-operated lesson trigger are explicitly outside
this tooling. Return to the master prompt and obtain fresh point-of-use authority.
Do not automate a spoken trigger, lesson action, serial session, or robot
control. Both adults must observe every cue directly and bind at least one
redacted image/video frame to each cue row; logs alone are insufficient.

## Phase 6: Offline Ledger Validation

Copy the committed BLOCKED template to the authorized session directory as
`tft-ledger.json`, then complete it with hashes and relative paths only after
reviewing redaction. Validate from `main/tbot-server`:

```bash
python3 scripts/course_mode_physical_tft_ledger_validate.py \
  "$TFT_SESSION_DIR/tft-ledger.json" \
  --repository-root /Users/manhhodinh/Documents/TBOT/robot/esp32-server
```

`TFT_PASS` is only a visual TFT sub-lane result. The validator always emits
`task07Verdict=PHYSICAL_BLOCKED`; it cannot unlock Task 08.

## Phase 7: Evidence Review and Task-Owned Cleanup

Review receipt/preflight bindings, every artifact byte count and SHA-256,
semantic JSON identity, redaction, direct cue frames, ordered runtime markers,
two-adult agreement, UTC session chronology, truthful physical-action states,
and the final safe-rest observations. A matching hash does not make arbitrary
preflight or receipt content valid: the ledger validator parses preflight and
uses the shared receipt verifier for both semantically equal receipts. Cleanup
of only task-owned Compose resources
may occur after evidence review and separate authorization. Cleanup is outside
this tooling; do not copy a cleanup command into committed evidence.

## Stop Categories

- Source/image/configuration: dirty or mismatched source, missing exact image or
  materializer label, stale/default image, wrong project/resource identity,
  unexpected service/profile/volume, or non-loopback backend exposure.
- Fixture/scope: wrong lesson, renderer, checksum, cue order, UUID, AC:20 scope,
  assignment cardinality, protected hash, or local endpoint authority.
- Isolation/privacy: production-like route, credential exposure, production
  publication/assignment/flag evidence, unauthorized microphone packet marker,
  or retained speech/private content.
- Runtime/device: lease conflict, reset without app-ready, wrong OTA/WebSocket
  path, wrong-device or unauthenticated connection, fallback content, missing
  asset, degraded rendering, timeout, crash, watchdog, or incomplete capture.
- Safety: motion during assessment, unexpected/forceful motion, collision,
  binding, failure to center/lower/rest, unusual heat, odor, vibration, unstable
  power, or inability to stop, isolate power, or rollback immediately.
- Evidence: missing direct visual proof, adult disagreement, path/symlink escape,
  missing artifact, byte/hash drift, or failed redaction.

Any physical stop requires the human safe-state procedure in the master prompt:
end the test, restore safe pose/power state, preserve evidence, mark `TFT_FAIL`,
and do not waive the issue. The validators only record the fail-closed result;
they never attempt the physical response.

An early `TFT_FAIL` ledger records only the marker/cue prefix actually observed.
It must record `stopPhase`, named operator/session identity, privacy outcomes, a
nonempty stop category, and explicit safe-state and power-isolation outcomes.
For each preflight or receipt artifact unavailable at that phase, add exactly one
`unavailableEvidence` entry with a stable nonempty reason. A stop before
preflight requires no preflight/receipt artifact; after preflight, first receipt,
and rerun receipt, each newly available binding becomes mandatory. Every
artifact that exists remains subject to path containment, byte/hash, redaction,
privacy, JSON parsing, and semantic identity checks. Physical-action fields must
also match the stop phase: no stack start before it occurred and no attended
capture/trigger outside `DURING_ATTENDED_CAPTURE`. Operator timestamps must be
ISO-8601 UTC with end at or after start. Do not invent remaining cue rows or
claim a successful final rest that was not observed.

No acoustic, current, voltage, temperature, motor-noise, leakage, stop-latency,
or comfort threshold is defined here. Those measurements remain
`NOT_MEASURED`, `MEASURED_PENDING_APPROVED_LIMIT`, or
`EVALUATED_AGAINST_APPROVED_LIMIT` with an authority reference. Calibrated
instruments, approved limits, E-stop/TP_EN evidence, rollback, recovery, and all
remaining Task 07 lanes remain blockers.
