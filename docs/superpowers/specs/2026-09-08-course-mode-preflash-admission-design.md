# Course Mode Pre-Flash Admission Design

## Status and Scope

This design supersedes the pre-flash portions of
`2026-09-07-course-mode-physical-preflight-key-provisioning-design.md`. The
operator-key design remains valid. The existing
`course_mode_physical_tft_preflight.py` remains unchanged in purpose: despite its
historical name, schema v3 verifies a completed flash transaction and is therefore
a post-flash receipt validator.

No application write, serial open, USB reset, physical memory read, HIL action, or
robot motion may occur until the new admission gate returns PASS and the operator
then gives a fresh candidate-specific flash authorization.

## Two Distinct Phases

### Pre-flash admission

Add `main/tbot-server/scripts/course_mode_physical_flash_admission.py`. It performs
only non-mutating checks available before serial access:

- candidate ID, validity interval, operator attestation, and exact repository SHAs;
- signed admission identity and pinned Ed25519 signer fingerprint;
- exact admin/backend image IDs and provenance labels;
- firmware SHA, app SHA-256, byte size, offset, partition capacity, and manifest;
- an explicit app-only operation plan at `0x20000`;
- protected bootloader, partition table, NVS, OTA data, PHY init, reserved, and
  generated-assets regions;
- exactly one approved `/dev/cu.usbmodem1101` character device and no current
  serial owner, without opening the device;
- robot board `LCDWiki ES3C35P`, target `esp32s3`, and MAC
  `14:c1:9f:d1:ac:20` from signed expected identity and prior reviewed inventory;
- adult observer, clear motion space, reachable power isolation, stable power/LAN,
  sole lease, evidence readiness, and prohibition on erase-chip/merged-image use.

It does not require or accept application/NVS readbacks, materialization receipts,
assignment snapshots, visual packs, runtime session artifacts, or claims about
post-write state.

### Post-flash receipt

Keep `course_mode_physical_tft_preflight.py` as the schema-v3 post-flash verifier.
It runs only after the attended app-only transaction has produced real partition
snapshot, pre/post application readbacks, pre/post NVS readbacks, firmware receipt,
assignment snapshot, materialization receipt, and visual-pack evidence. No dummy,
copied, zero-filled, or historical readback may satisfy a current session.

The post-flash tool should be renamed in user-facing documentation to “physical
flash receipt validator”; its filename is retained to minimize source churn and
preserve existing integrations and test imports.

## Signed Admission Documents

Candidate `.41` contains a `G7-admission` directory with three finalized read-only
inputs and one absent output path:

```text
admission-input.json
expected-admission-identity.json
expected-admission-identity.sig
admission-result.json
```

The signature covers canonical JSON bytes of `expected-admission-identity.json`.
The expected identity binds candidate/course, repositories, image IDs, firmware
and app identity, partition plan, robot identity, serial path, and operator safety
session ID. The admission input references those immutable facts and contains the
current safety assertions. Both JSON files and the signature are UID 501-owned,
regular, non-symlink, link-count-one, read-only files beneath the evidence root.

The private key remains only at
`/Users/manhhodinh/.tbot-operator/course-mode-preflight/course-mode-preflight-ed25519.pem`.
It is never added to a candidate, evidence bundle, Docker context, command output,
or repository.

## Candidate and Release-Gate Contract

Replace the worktree-only optional `tools.physicalPreflight` manifest extension
with `tools.physicalAdmission`. It has exactly these keys:

```json
{
  "input": "/absolute/evidence/G7-admission/admission-input.json",
  "output": "/absolute/evidence/G7-admission/admission-result.json",
  "expectedIdentity": "/absolute/evidence/G7-admission/expected-admission-identity.json",
  "expectedIdentitySignature": "/absolute/evidence/G7-admission/expected-admission-identity.sig"
}
```

Candidate validation keeps the already implemented strict path, containment,
read-only metadata, strict-JSON, signature-size, absent-output, and path-race
protections. The compatibility export `TOOLS_KEYS` remains the immutable canonical
eight-key base and must not include `physicalAdmission`.

Keep the CLI mode name `physical-preflight` for compatibility, but make its only
lane `physical-flash-admission` and execute the new admission script. The lane must
be candidate-bound and operator-attestation-bound. A missing descriptor, existing
output, expired candidate, repository drift, signature mismatch, unsafe device
inventory, or failed safety assertion returns BLOCKED/FAIL before serial access.

## Stale Historical Locks

Do not carry schema-v3 historical constants into pre-flash admission. The new tool
must derive current repository cleanliness from candidate `.41`; it must not
require the obsolete dirty exception for
`test_lesson_voice_output_discipline.py`. It must validate the backend image and
materializer provenance recorded by `.41`, not hard-code the former
`course-mode-local-materializer.js` path when the reviewed image uses
`course-mode-v5-identity-materializer.js`.

The schema-v3 receipt validator can be migrated to current identities only after
the admission gate is complete and before post-flash receipt generation, using
separate failing tests. That migration must not weaken historical receipt
validation or reinterpret old evidence.

## Execution Sequence

1. Update manifest and release-gate tests from `physicalPreflight` to
   `physicalAdmission` and preserve all security regression coverage.
2. Implement the new admission validator test-first, including hostile signatures,
   path races, malformed inputs, expired timestamps, unexpected devices, occupied
   serial ports, unsafe assertions, wrong hashes, protected-range drift, and
   forbidden command shapes.
3. Commit and independently review the source changes.
4. Build the admin image from the exact committed source and create candidate
   `.41` with signed admission documents.
5. Run candidate validation, operator attestation, quick, full, live-db, runtime,
   browser, continuity, privacy, and software evidence audit gates.
6. Recheck point-of-use safety and run `physical-preflight`, which now performs
   only the non-opening admission checks.
7. Freeze and audit admission evidence, show the exact `.41` identities to the
   operator, and request fresh authorization for the app-only transaction.
8. Only after that authorization, acquire the serial lease and execute the
   separately reviewed rollback/readback/flash procedure.
9. Run the schema-v3 post-flash receipt validator against real current-session
   artifacts before any Course Mode lesson journey.

## Failure and Recovery

All validation is fail-closed and emits bounded, deterministic, redacted reason
codes. Admission never kills a competing serial process. It reports the holder and
stops. Any mismatch or loss of safety conditions invalidates the admission result;
a new signed input and new PASS are required.

After serial access begins, identity mismatch, partition drift, unexpected motion,
overheating, reset loop, or unstable power/LAN requires immediate stop and reachable
power isolation. Recovery uses the separately qualified known-good application and
still preserves every protected partition.
