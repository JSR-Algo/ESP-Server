# Course Mode Operator Attestation Time Binding

Date: 2026-09-05

Status: approved design for the Course Mode software release gate. This does
not authorize deployment, physical-preflight, serial access, firmware flashing,
or robot motion.

## Problem

The release gate currently validates the operator attestation `createdAt` as
canonical RFC 3339 UTC but does not compare it with the candidate validity
window or the gate validation time. A structurally valid attestation dated
before the candidate was frozen or in the future can therefore satisfy the
operator precondition.

## Contract

The canonical release gate accepts an operator attestation only when:

```text
candidate.createdAt <= attestation.createdAt <= now < candidate.expiresAt
```

All timestamps are canonical RFC 3339 UTC values parsed by the existing
candidate-manifest helper. Equality is accepted at candidate creation and the
snapped validation time. Candidate expiry is exclusive: a binding attempt at
or after `candidate.expiresAt` is rejected, matching candidate validation.

The gate snapshots `now` once for each operator-attestation binding attempt.
Callers may provide a fixed aware UTC time for deterministic tests; production
uses the current aware UTC time. Naive or non-UTC injected times fail closed.

## Implementation

Keep the existing attestation schema and generator unchanged. Extend only the
release gate's attestation binding check:

1. Parse candidate `createdAt`, candidate `expiresAt`, and attestation
   `createdAt` with the existing strict parser.
2. Reject a missing or malformed timestamp.
3. Reject attestations before candidate creation or after the snapped
   validation time, and reject binding attempts at or after candidate expiry.
4. Preserve all existing file identity, ownership, permissions, exact-key,
   host, UID, candidate ID, gate SHA, and confirmation checks.

No separate TTL is introduced. A candidate-bound attestation remains usable
for the candidate's validity window, but it can never predate that candidate or
claim a future operator confirmation.

## Verification

Test-first coverage must prove rejection immediately before candidate creation,
at candidate expiry, immediately after candidate expiry, and immediately after
the current validation time. It must prove acceptance at candidate creation,
at the current time, and immediately before expiry. Existing production-gate and operator-
attestation suites must remain green, followed by the complete Course Mode
source gate and independent spec and quality reviews.
