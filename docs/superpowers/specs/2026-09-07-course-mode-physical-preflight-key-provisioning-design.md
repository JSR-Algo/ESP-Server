# Course Mode Physical Preflight Key Provisioning Design

## Goal

Provision the currently fail-closed Course Mode physical preflight trust boundary,
issue a new immutable candidate `.41`, and preserve the rule that no robot access
occurs until a signed, candidate-bound preflight passes.

Candidate `.40` remains immutable historical software evidence. It must not be
edited, relabeled, or used for physical flashing because its admin source pins no
operator public key and its manifest contains no `tools.physicalPreflight` bundle.

## Operator Key

Create one Ed25519 key pair in the operator-controlled directory:

```text
/Users/manhhodinh/.tbot-operator/course-mode-preflight
```

The directory must be mode `0700`. The private PEM and raw public-key file must be
regular, non-symlink files owned by UID 501, with mode `0600` and link count one.
Creation must use exclusive file creation and fail if either target already exists.

The private key never enters a repository, build context, candidate manifest,
evidence bundle, command transcript, shell trace, or cloud-synced directory. Only
the 32-byte raw public key and its SHA-256 fingerprint may enter source control.

## Source Pinning

Change only the existing trust constants in
`main/tbot-server/scripts/course_mode_physical_tft_preflight.py`:

- replace the `None` public-key placeholder with the reviewed 32-byte key;
- replace `unprovisioned` with the matching SHA-256 fingerprint;
- retain every existing signature, canonical-JSON, path, ownership, and immutable
  tool check unchanged.

Add a focused regression test that fails against the unprovisioned source and
passes only when the checked-in raw public key is exactly 32 bytes and its digest
equals the pinned fingerprint. Existing hostile signature tests remain mandatory.

## Candidate `.41`

Commit the public pin before generating `.41`. Rebuild and qualify the admin web
image from that exact commit. Backend and firmware identities may remain unchanged
only if fresh validation proves their repositories and artifacts are clean and
byte-identical to the approved inputs.

Generate a new `course-mode-2026-09-07.41` candidate rather than mutating `.40`.
Its evidence root contains a `G7-preflight` directory with:

- reviewed expected physical identity JSON;
- detached 64-byte Ed25519 signature over canonical JSON bytes;
- physical preflight input JSON;
- a not-yet-existing output path reserved for the gate result.

The manifest binds those four paths under `tools.physicalPreflight`. All input and
signature files must be UID 501-owned regular non-symlink files, link count one,
read-only after finalization, and descendants of the candidate evidence root.

The expected identity binds candidate, course, repository SHAs, image IDs,
firmware SHA, app SHA-256/size/offset, partition map, robot MAC
`14:c1:9f:d1:ac:20`, and the attended session identity. The signature is generated
offline from canonical bytes without printing private material.

## Qualification Flow

Run the same candidate-bound qualification sequence used for `.40`:

1. candidate validation and operator attestation;
2. quick gate;
3. full gate;
4. isolated PostgreSQL live-db gate;
5. runtime NEW and ROLLBACK journeys in Chromium and WebKit;
6. continuity, privacy, immutable-evidence, and independent software audit;
7. signed `physical-preflight` mode.

Any changed source, image identity, dependency tree, timestamp violation, skipped
required lane, audit finding, or non-PASS preflight invalidates `.41`.

## Physical Boundary

Software qualification and key provisioning do not themselves authorize serial,
USB reset, flash, HIL, or motion. The operator's fresh point-of-use confirmation
for MAC `14:C1:9F:D1:AC:20` and `/dev/cu.usbmodem1101` remains bound to this attended
session. Immediately before robot access, recheck the sole serial lease, candidate
expiry, binary hashes, safety observer, motion clearance, reachable power
isolation, and stable power/LAN.

Only the application binary may be written at `0x20000`, using `--after no-reset`
until readback completes. Bootloader, partition table, NVS, OTA data, PHY init,
reserved, and generated-assets partitions must remain unchanged. Any mismatch,
unexpected motion, overheating, reset loop, or identity drift requires immediate
stop and power isolation.

## Verification and Recovery

Before candidate flash, rehearse the known-good rollback application as required by
the production-readiness plan. Capture pre/post application readback and NVS
readback; NVS must be byte-identical. After candidate write, require exact app
readback, post-boot board/MAC/version evidence, and an attended Course Mode E2E run.

The private key is backed up only by the human operator outside the workspace. If
it is lost or suspected compromised, replace the public pin through a new reviewed
commit and fully qualify another candidate. Never weaken signature verification or
copy the private key into release artifacts to recover a blocked run.
