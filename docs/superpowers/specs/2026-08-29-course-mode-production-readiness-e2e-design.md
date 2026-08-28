# Course Mode Production-Readiness E2E Design

## Status

Approved interactively on 2026-08-29 using the layered hard-gate approach.
This qualification targets local/staging services and the physical AC:20 robot.
It does not authorize a production deployment, production course cutover, or
production data mutation.

## Goal

Qualify the `english-6month-4-6` Course Mode candidate as production-ready by
proving the complete 26-lesson path from Admin authoring through persistence,
publication, assignment, ESP orchestration, renderer-v5 firmware, physical child
interaction, progress readback, recovery, soak, and rehearsed rollback.

The final claim is limited to the tested immutable candidate and environment.
The release gate may report `GO` only with zero known P0/P1 defects and every
mandatory gate at `PASS`. It must never translate incomplete, skipped, waived,
or historical evidence into a claim of "100% bug-free."

## Scope

### In scope

- One canonical 26-lesson Course Mode source and renderer-v5 runtime.
- Existing published Admin background, object, and robot assets.
- Backend curriculum compiler, migration 126, materialization, replacement,
  assignment, publication lifecycle, completion, progress, and insights.
- Manager-web Course Mode authoring and immutable published-version behavior.
- ESP public HTTP/WebSocket adapters, outbox, reconnect, dedupe, cache, Google
  Live handoff, safe exit, and evidence collection.
- Firmware renderer-v5 visual state, audio/motion ordering, durable delivery,
  reboot recovery, memory behavior, and physical safe-rest.
- Local/staging PostgreSQL and production-equivalent Docker topology.
- App-only firmware flash at `0x20000` after an approved physical preflight.
- Six representative physical lessons, all 26 happy paths, failure injection,
  a second corpus soak, and a physically rehearsed rollback.

### Out of scope

- Production deploy, production database migration, production publication, or
  production assignment.
- Automatic flashing without an attended operator and explicit point-of-use
  authorization.
- Re-authoring visual assets when an existing approved object/background/robot
  triple can satisfy the contract.
- Subjective claims about guaranteed learning outcomes or absence of all future
  defects.
- Renderer-v4 executable cleanup before v5 cutover and a zero-reference audit.

## Current Baseline and Blockers

The current workspace is not one immutable release candidate:

- The 26-week backend implementation is on
  `feature/course-mode-26week-single-version`, not authoritative backend main.
- Canonical root release scripts omit important Course Mode suites.
- Admin has no browser E2E for Course Mode author/edit/publish/rollback/insights.
- The lifecycle has no first-class, hash-gated `rollback` operation.
- Physical receipt and ledger tools still contain renderer-v4 pilot assumptions.
- The latest physical evidence is red and cannot qualify renderer-v5.
- ESP and firmware contain explicitly preserved dirty files that must either be
  excluded by signed hash-bound exceptions or incorporated into a reviewed
  candidate before the release freeze.
- Darwin cannot atomically execute user-owned verified binaries by file
  descriptor; physical preflight must use an approved immutable execution
  boundary before it can authorize flash.

Historical software and physical reports remain diagnostic inputs only. Every
release assertion requires fresh evidence from the frozen candidate.

## Candidate Identity

Gate G0 creates one signed candidate manifest containing:

- backend, Admin, ESP, and firmware repository paths and exact Git SHAs;
- permitted dirty exceptions with exact relative paths and SHA-256 values;
- backend and ESP container image IDs and content digests;
- firmware binary SHA-256, size, app offset, partition-table digest, board,
  firmware version, and build configuration;
- course ID, course key, curriculum source checksum, replacement snapshot,
  renderer, contract, manifest, and asset-pack identities;
- PostgreSQL migration head, seed/materialization receipt, and database authority;
- exact Git, Docker, and Compose executable identities used by preflight;
- signer fingerprint, creation time, expiry, and evidence directory.

Every gate consumes this manifest. Identity drift invalidates all later evidence
and returns the campaign to G0.

## Gate Architecture

### G0: Freeze and source convergence

Create a single reviewed SHA-set. Integrate the 26-week backend work without
duplicating its implementation or retaining runtime dependence on a worktree.
Preserve unrelated user changes. Cross-repository fixtures, schemas, renderer
identifiers, course checksum, and response-mode enumerations must match exactly.

Pass criteria:

- one canonical implementation path per component;
- no hidden worktree/source fallback;
- no unreviewed dirty runtime file;
- signed candidate manifest is internally consistent;
- clean builds are reproducible from the recorded SHAs.

### G1: Static and complete software regression

Run format, lint, typecheck, compile, build, generated-contract parity, dependency
checks, and full test suites for all four components. Canonical release scripts
must invoke all Course Mode suites rather than relying on manually remembered
commands.

Pass criteria:

- zero failures and zero unexplained skips;
- skips requiring credentials, network, or hardware are reported as `SKIPPED`
  and block any higher gate that depends on them;
- no ambient build output or stale generated artifact can make the gate pass;
- tests run successfully from a fresh checkout or equivalent clean source tree.

### G2: Curriculum, migration, and artifact parity

Compile all 26 lessons and validate every activity graph, response mode,
pedagogy, visual triple, timing bound, recovery branch, checksum, and artifact
envelope. Run migration 126 against a real isolated PostgreSQL database, then
materialize all 26 replacements twice to prove idempotency and deterministic
receipts.

Pass criteria:

- exactly 26 lessons, expected activity total, six pedagogies, and eleven
  response classes;
- each lesson is at most 480 seconds and has a bounded terminal outcome;
- no answer leakage, unreachable activity, unbounded retry, missing safety exit,
  duplicate replacement, or ambiguous assignment mapping;
- renderer-v5 manifest, contract, database rows, generated packs, and published
  checksums agree byte-for-byte where the contract requires equality;
- migration forward, repeated-forward, and rollback rehearsal all pass.

### G3: Admin and authorization E2E

Use real browser automation against the local/staging backend and real database.
Cover create, edit, save, reload, validate, asset selection, preview, publish,
clone, immutable published content, projected-step read-only behavior, assignment,
insights, and rollback visibility.

The suite must include Safari/WebKit and Chromium at desktop and mobile widths,
two-admin stale-version conflicts, role/authz/IDOR checks, and API/browser parity.

Pass criteria:

- browser state matches persisted contract after reload;
- Admin preview uses the same renderer-v5 visual identities as the robot;
- unauthorized, stale, malformed, or conflicting writes fail safely;
- no duplicate source/version is created by retry or double submission;
- visual geometry remains within the 480x320 safe area and preserves authored
  entrance timing, layer order, spacing, and post-activity positions.

### G4: Lifecycle, cutover, and rollback fault matrix

Extend the lifecycle with a first-class `rollback` transaction. It must reverse
assignment resolution from a specific signed cutover/archive receipt without
guessing the previous version, resurrecting GC'd data, or accepting checksum
drift.

Test dry-run, materialize, cutover, archive, rollback, GC dry-run, and GC under:

- database failure before and after each transaction boundary;
- process termination and retry;
- concurrent operators;
- stale or mismatched receipts;
- active sessions and assignments;
- missing/corrupt packs;
- partially archived or already rolled-back state.

Pass criteria:

- each operation is idempotent and produces a signed receipt;
- rollback restores the exact prior assignment and publication state;
- no operation leaves a mixed source/replacement state;
- GC cannot remove an artifact referenced by current, rollback, session, or
  evidence state;
- a timed post-archive rollback rehearsal succeeds within the defined recovery
  objective.

### G5: Cross-process backend and ESP E2E

Run real PostgreSQL, backend HTTP, ESP process, authenticated WebSocket, and
public runtime adapters. Do not use fake query pools or private orchestrator
shortcuts. Cover all 26 lessons deterministically, then run representative
cross-process journeys for TPR, picture discovery, story/context, role-play,
checkpoint, and W26 showcase.

Each deterministic lesson covers correct, near, incorrect, silence, Vietnamese,
help, ASR unavailable, fatigue/refusal, branch, disconnect/resume, and safety.

Fault matrix:

- duplicate, delayed, missing, and out-of-order delivery/ACK events;
- same assignment across multiple sessions;
- ESP restart, backend restart, and WebSocket reconnect;
- completion POST `429`/`5xx`, retry, and replay;
- asset timeout, checksum mismatch, stale cache, SD unavailable/full/corrupt;
- disk-floor enforcement and evidence/log retention;
- Google Live/ASR/TTS unavailable and lesson handoff recovery.

Pass criteria:

- exactly-once logical progress and completion;
- no stuck active session, duplicate response, stale audio, or replayed motion;
- resume continues from durable state without skipping or repeating a completed
  activity;
- backend, ESP, parent progress, and insights report the same authoritative
  totals;
- every failure ends in recovery or an authored safe exit.

### G6: Firmware host, HIL, and resource qualification

Run renderer-v5 native tests, handler contract tests, backward compatibility,
sanitizers, memory guardrails, protocol fuzzing, and HIL tests against the exact
firmware binary.

Cover static background/object retention, requested entrance replay, layer
geometry, degraded visual fallback, delivery recovery, NVS errors, SD/cache
errors, audio drain, motion ownership, stop/rest, disconnect, reboot, and
outcome reconciliation.

Pass criteria:

- no crash, watchdog reset, sanitizer error, use-after-free, or unbounded
  allocation;
- background and object remain visible with correct layer order and authored
  positions across activity transitions;
- each physical motion is dispatched at most once after the associated visual
  state, and firmware never independently infers learning outcome;
- persistent delivery recovery remains correct across reboot and NVS failure;
- heap, file descriptor, thread/task, and cache usage remain bounded.

### G7: Physical AC:20 qualification

Physical execution is attended and serialized under one robot lease. Before
flash, verify MAC `14:c1:9f:d1:ac:20`, board/partition/security identity, app
binary hash, NVS baseline, generated-assets boundary, stable power/LAN, emergency
stop, motion clearance, and evidence capture.

Only the application partition at `0x20000` may be written. Bootloader,
partition table, OTA data, NVS, PHY init, reserved space, and generated assets
must remain untouched. Read back the app and NVS before boot; the app hash must
match and pre-boot NVS must remain byte-identical.

Physical sequence:

1. Rehearse known-good rollback and complete a W1 smoke.
2. Run six representative lessons covering the six pedagogies and distribute
   normal, silence/help, ASR unavailable, disconnect/resume, cache recovery, and
   controlled power-cycle paths across them.
3. Run all 26 lessons on happy path with a fresh assignment/session per lesson.
4. Run the attended fault matrix at activity boundaries.
5. Run a second 26-lesson corpus in a different order, with a 60-minute idle,
   100 WebSocket reconnects, and 10 SD cache cycles.

Pass criteria:

- 26/26 lessons complete or take their explicitly authored safe exit;
- zero unexplained crash, watchdog, OOM, reset, identity drift, content mismatch,
  duplicate/lost progress, stale pixel, wrong semantic visual, unsafe motion,
  runaway audio, or privacy event;
- correct TFT layer order, geometry, entrance frequency, spacing, persistence,
  audio, motion, stop, and safe-rest behavior;
- no monotonic heap, task, descriptor, thermal, or cache growth;
- completion and progress read back correctly from the backend after every run.

### G8: Security and supply-chain gate

Require signed expected identity, operator-managed signing key, immutable trusted
tool execution, secret redaction, dependency/license audit, image digest pinning,
SBOM, artifact checksum manifest, and absence of credentials/raw child audio in
evidence.

On Darwin, user-owned temporary executable copies are not an atomic trust
boundary. Physical preflight must either use root-owned, non-writable approved
Git/Docker/Compose implementations or replace those executable dependencies with
a reviewed API/library boundary. A race-prone copy must fail closed.

Pass criteria:

- no unresolved P0/P1 security finding;
- signer public key/fingerprint is pinned and the private key remains outside
  repositories/evidence with restrictive permissions;
- all evidence is redacted and hash-addressed;
- public asset URL exposure is either explicitly re-accepted by the owner for
  this release or replaced by a tested scoped/expiring access design.

### G9: Evidence audit and release rehearsal

Aggregate every gate into a machine-readable release report. Each journey must
include exact identities, timestamps, assignment/session/course/lesson/version,
contract/manifest/asset checksums, verifier result, database terminal readback,
bounded logs, resource counters, visual capture references, and artifact hashes.

The auditor rejects missing, contradictory, duplicated, stale, out-of-order, or
wrong-candidate evidence. Historical evidence cannot fill a missing current gate.

### G10: Rollback and final verdict

Rehearse rollback from the fully qualified candidate to the known-good baseline.
Verify app readback, NVS preservation, boot, network, display, audio, motion,
safe-rest, backend assignment resolution, and one complete W1 lesson. Restore the
candidate only through the same reviewed process if further qualification is
required.

Final verdict rules:

- `GO`: G0-G10 all pass, zero open P0/P1, no required skip or waiver.
- `CONDITIONAL-GO`: only for explicitly accepted P2/P3 risks that do not affect
  safety, identity, correctness, privacy, rollback, or observability. This status
  does not authorize production deployment without separate approval.
- `NO-GO`: any P0/P1, required skip, identity drift, red physical journey,
  unproven rollback, or contradictory evidence.

## Failure Handling

Stop the current gate on the first unexplained anomaly. Preserve logs, receipts,
device state, database snapshot, and artifact hashes before retrying. A retry is
allowed only after the failure is classified and the test declares whether the
same identity/state must be reused or recreated.

Immediate physical rollback triggers include unsafe motion/audio/privacy,
partition or NVS drift, wrong/corrupt content, unrecoverable session, repeated
crash/reset, duplicate completion, monotonic memory/resource growth, or any
preflight/evidence verifier failure.

## Evidence Layout

Each run writes to a timestamped, candidate-bound directory:

```text
task-artifacts/course-mode-production-readiness/<candidate-id>/<gate>/
  candidate.json
  report.json
  timeline.log
  commands.txt
  checksums.sha256
  receipts/
  database/
  device/
  visuals/
```

`report.json` uses `PASS`, `FAIL`, `SKIPPED`, or `BLOCKED` per check and includes
one primary failure classification. Reports must not contain secrets, raw child
audio, full transcripts, or signing private material.

## Execution Order and Parallelism

G0 is serial. After G0, independent software lanes in G1-G3 may run in parallel.
G4-G6 begin only when their upstream contracts are green. Physical G7 begins
only after G0-G6 and G8 preflight protections pass. G9 and G10 are serial final
gates.

Use one implementation subagent per bounded task with spec review followed by
quality/security review. Multiple implementation agents must not edit the same
repository concurrently. Physical work uses one lease holder and one evidence
authority.

## Estimated Qualification Time

- Candidate convergence and canonical gates: 4-8 engineering hours, excluding
  merge conflicts or newly discovered defects.
- Admin, lifecycle rollback, and cross-process gaps: 8-16 engineering hours.
- Physical preflight and rollback rehearsal: 1-2 lab hours.
- Six representative lessons: 2-3 lab hours.
- Full 26-lesson run: 5-6 lab hours.
- Fault matrix: 4-6 lab hours.
- Second-corpus soak, idle, reconnect, and cache cycles: 10-12 elapsed lab hours.
- Evidence audit and final verdict: 2-3 hours.

The expected physical qualification campaign is 24-32 elapsed lab hours across
two or three attended days after the software candidate is green.

## Design Acceptance

This design is complete when the user approves this written specification. The
next step is a task-by-task implementation plan using test-driven development,
frequent commits, subagent implementation, independent spec review, and
independent quality/security review.
