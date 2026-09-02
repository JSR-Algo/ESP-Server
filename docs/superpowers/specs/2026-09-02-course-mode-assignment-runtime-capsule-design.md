# Course Mode Assignment Runtime Capsule Design

## Context

The candidate-bound Course Mode Full gate runs every lane from a fresh private
snapshot and removes that snapshot immediately after the lane. The Task 4
assignment flow is intentionally split into two ordered lanes:

1. `admin-course-mode-assignment-new` creates and assigns lesson v9.
2. `admin-course-mode-assignment-rollback` cancels v9 and assigns v8.

PostgreSQL and the Compose project persist between those lanes. The derivative
media directory does not: `TASK4_ASSIGNMENT_RUNTIME_ROOT` is currently rebased
under each lane snapshot, so successful cleanup of NEW deletes the files whose
metadata remains in PostgreSQL. ROLLBACK then receives an empty runtime and
fails in `bootstrap.cjs verify-new` before Playwright with `ENOENT` on the first
derivative.

Candidate `.21` is immutable evidence of this failure. Fourteen earlier lanes
passed, NEW passed, and ROLLBACK failed before any rollback mutation.

## Decision

The release gate will create one private assignment runtime capsule for the
ordered NEW-to-ROLLBACK sequence. Both assignment lanes will receive the same
capsule identity and path. Per-lane source snapshots remain separate and
immutable; only generated runtime media and TLS material cross the lane
boundary.

The capsule is owned by the gate process, not by Docker and not by either lane.
It is created only when the selected lane sequence contains an assignment lane,
and it is removed after the last selected assignment lane or when the gate exits
early. Existing copy-only derivative materialization and the secure,
identity-checked deleter remain unchanged.

## Rejected Alternatives

### Rebuild media during ROLLBACK

ROLLBACK could rematerialize all derivatives before verification. This would
make each lane independent, but it would stop testing the real persisted
NEW-to-ROLLBACK handoff and could hide missing or corrupted rollout artifacts.

### Store media in a Docker volume

A project-scoped volume would survive container recreation, but it would move
artifact ownership and cleanup into Docker. Host-side provenance, inode checks,
retained-path reporting, and fail-closed cleanup would become harder to audit.

## Ownership And Data Flow

Before the first assignment lane, the gate creates a private temporary capsule
with a captured device/inode identity. The capsule contains the existing
`media/` and `tls/` subdirectories expected by Task 4 orchestration.

For each assignment lane:

- Candidate repositories and tools are staged as they are today.
- `TASK4_ASSIGNMENT_RUNTIME_ROOT` is replaced with the capsule's exact absolute
  path rather than a path inside the disposable lane snapshot.
- NEW creates TLS material, copies derivative bytes, publishes v9, and verifies
  the generated and served files.
- NEW's lane snapshot is removed, while the capsule and Compose/PostgreSQL state
  remain available.
- ROLLBACK reuses the same capsule, verifies NEW state, performs cancel/create,
  and verifies v9 `CANCELLED` plus v8 `ASSIGNED`.

Non-assignment lanes never receive the capsule path. The operator-provided
runtime root remains a namespace input used to derive the requested isolated
assignment scope; it is not used as an ambient writable directory by the child.

## Isolation And Authority

- The capsule must be beneath a gate-created private temporary parent and must
  never overlap a candidate repository, report, operator attestation, or the
  operator-provided source runtime path.
- The gate snapshots the assignment environment before execution. Later
  mutation of the source mapping cannot redirect the capsule.
- Only the two assignment lanes receive write access to the capsule.
- The capsule path is injected by the parent gate after candidate/environment
  validation; child input cannot select an arbitrary host path.
- Source assets, fixture scripts, candidate images, Docker/Compose executables,
  browser binaries, backend, firmware, and repository SHAs remain
  candidate-bound exactly as before.
- The design introduces no hard-link fallback and no exception in the secure
  deleter.

## Cleanup And Failure Semantics

The gate attempts capsule cleanup exactly once through its owned-resource
cleanup path after the final assignment lane, or during unwind if any earlier
step fails. Cleanup validates the original capsule identity and applies the same
fail-closed rules used for staged trees.

If capsule cleanup succeeds, no capsule path is retained. If cleanup cannot be
proven, the report becomes `BLOCKED` with `failedLane: "cleanup"`,
`retainedOwner: "current-process"`, and the exact capsule path in
`retainedPaths`. A test failure remains attributable to its assignment lane only
when all gate-owned cleanup succeeds.

The gate must not delete or broadly enumerate unrelated Docker resources. The
existing exact Compose project cleanup remains a separate orchestration
responsibility.

## Verification

Implementation follows test-first development.

Unit and integration-style release-gate tests will prove:

- NEW and ROLLBACK receive the same capsule path and identity.
- The capsule survives NEW lane cleanup while the NEW lane snapshot does not.
- A file created by NEW is readable by ROLLBACK.
- The capsule is removed after ROLLBACK success.
- NEW failure, ROLLBACK failure, snapshot failure, and early gate exit all
  attempt capsule cleanup.
- Capsule identity/path tampering fails closed and reports the exact retained
  path.
- A mutable source environment cannot redirect the capsule between lanes.
- Non-assignment and single-lane test selections do not leak a capsule.
- Copy-only inode/source-isolation tests and secure-deleter tests remain green.

The focused release-gate suite, related Node assignment tests, full release-gate
suite, candidate build/validator, Quick, Full, live-db, software evidence audit,
and two independent reviews remain required before issuing
`SOFTWARE_GO_FOR_ATTENDED_FLASH`.

## Evidence And Candidate Handling

Candidate `.21`, its Full failure report, and the observed Docker/DB state remain
historical evidence and must not be overwritten or relabeled as passing. The
fix requires a new admin commit, candidate manifest, image identity, evidence
directory, Quick report, Full report, and live-db report.

No production deployment, production database mutation, firmware flash, serial
operation, HIL action, reboot, or robot motion is authorized by this design.
