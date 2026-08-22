# Task 07 Session NVS Baseline Design

## Problem

The physical-TFT tooling currently stores the NVS digest from a historical
candidate installation inside `productionCandidateTarget`. That makes the
historical mutable NVS state look like part of immutable production firmware
identity and incorrectly rejects a later session whose pre-install NVS bytes
have legitimately changed.

## Contract

The tooling separates three concepts:

1. `productionCandidateTarget` contains only the reviewed firmware SHA,
   application SHA-256, and bundle-root SHA-256. These values remain hard-pinned.
2. `historicalInstallationProvenance` records the already-established historical
   preserved-NVS SHA-256. It remains hard-pinned evidence about that installation,
   not a prerequisite for the current session.
3. The preflight accepts `sessionNvsBaseline.beforeInstallSha256` as an exact
   caller-supplied lowercase SHA-256 observed for the current authorized session.
   The preflight does not read a device or infer this value.

The attended ledger records `sessionNvsPreservation` with an explicit `phase`
enum plus `beforeInstallSha256`, `afterInstallSha256`, and
`afterRestoreSha256`. The phases are `NOT_OBSERVED`, `PRE_INSTALL_BASELINE`,
`POST_INSTALL`, and `POST_RESTORE`. Values accumulate monotonically: each phase
requires all evidence from prior phases, forbids evidence from later phases, and
requires every present after-value to equal the before-install value. The ledger
also requires its before-install value to equal the value in the bound preflight
result. `TFT_PASS` requires `POST_RESTORE`.

## Blocked And Early-Stop Evidence

The committed `TFT_BLOCKED` template uses `NOT_OBSERVED` and leaves all session NVS values `null`
because no current-session readback is committed. Historical provenance remains
present and explicitly historical. A `PRE_PREFLIGHT` stop may also leave all
session values null. Once preflight evidence is claimed, the NVS phase must be at
least `PRE_INSTALL_BASELINE`. Any ledger that claims `POST_INSTALL` or
`POST_RESTORE` must carry the corresponding exact equal readback. Complete
attended evidence requires `POST_RESTORE` and all three equal values.

No schema state may change `task07Verdict` from `PHYSICAL_BLOCKED`. The tooling
does not authorize installation, restore, device access, serial access, network
access, Docker activity, or any physical mutation.

## Failure Behavior

Validation fails closed for:

- missing, extra, uppercase, malformed, or partially populated NVS fields;
- historical provenance drift;
- treating the historical digest as the current baseline;
- disagreement between ledger and bound preflight before-install values;
- any before/after-install/after-restore mismatch;
- any weakening of the immutable firmware/application/bundle target.

The validator reports field-specific reasons so an operator can distinguish
schema defects, provenance drift, preflight binding drift, and preservation
failure without exposing NVS contents.

## Tests

Tests first establish that the existing contract rejects a legitimate distinct
session baseline. New tests then require:

- preflight acceptance of an arbitrary exact lowercase current-session baseline;
- continued rejection of production firmware identity drift and historical
  provenance drift;
- ledger acceptance only when all three session digests are exact and equal;
- semantic binding of the ledger before-install digest to preflight evidence;
- rejection of mismatches, missing fields, malformed hashes, and conflation;
- validity of the null-valued BLOCKED template with `PHYSICAL_BLOCKED` intact.
