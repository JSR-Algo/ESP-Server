# Course Mode Physical Evidence Extension Validation

## Context

The canonical Course Mode candidate manifest has an exact `tools` schema. The
physical receipt verifier also requires a signed `tools.physicalEvidence`
binding. After the candidate toolchain schema was hardened, these requirements
became mutually exclusive: the manifest validator rejects `physicalEvidence`,
while the receipt verifier rejects its absence.

This blocks valid physical evidence verification and also leaves the legacy
evidence auditor's shared physical-candidate fixture stale.

## Decision

Keep `course_mode_candidate_manifest.validate_candidate()` unchanged and
strict. A release candidate must continue to contain exactly the canonical tool
descriptors and must reject `physicalEvidence` or any other extra key.

Add a physical-candidate validation helper in the physical receipt verifier.
It accepts exactly the canonical tool keys plus one `physicalEvidence` key,
projects a copy without that extension through the existing strict manifest
validator, and separately relies on the existing signed physical-identity
validation for the extension. It rejects missing canonical descriptors,
unknown extra keys, malformed extension values, and all existing identity or
signature failures.

The evidence auditor will use this same helper because its G0-G10 format is
anchored to the signed physical identity. No second schema implementation or
general-purpose manifest relaxation will be introduced.

## Data Flow

1. Receive the physical candidate and current validation time.
2. Require `tools` to contain exactly the canonical manifest keys plus
   `physicalEvidence`.
3. Copy the candidate and remove only `tools.physicalEvidence` from the copy.
4. Run the existing strict candidate validator on the projected candidate.
5. Validate the signed physical evidence with the existing identity, digest,
   repository-binding, and signature checks.
6. Aggregate deterministic fail-closed reasons in the receipt verifier or
   evidence auditor.

The original candidate object is never mutated.

## Compatibility And Security

- The ordinary candidate manifest schema remains byte-for-byte unchanged.
- Unknown `tools` extensions remain rejected.
- Missing or invalid canonical tool descriptors remain rejected by the strict
  validator.
- Only physical verification paths accept the single signed extension.
- Existing path containment, dirty-exception binding, hash, signature, and
  timestamp rules remain in force.
- No robot, serial port, firmware flash, database, or external service is
  accessed by this change.

## Tests

Update the shared physical candidate fixture to the current candidate schema
and signed candidate-binding shape. Add focused regressions proving:

- a valid physical extended candidate passes receipt and evidence validation;
- the ordinary manifest validator still rejects `physicalEvidence`;
- an unknown extra tool key is rejected;
- a missing canonical descriptor is rejected;
- malformed or invalid signed physical evidence still fails closed.

Run the physical receipt suite, legacy evidence audit suite, candidate manifest
suite, the combined 796-test source gate, lint/compile checks, and Node contract
tests before rebuilding the candidate image.
