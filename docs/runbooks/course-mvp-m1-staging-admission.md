# Separate M1 staging qualification

M1 staging candidates explicitly declare `qualificationProfile: "m1-staging"`.
Their firmware evidence declares `profile: "m1-staging"` and uses
`stagingConfigAudit` and `stagingArtifactAudit` in place of production audit
claims. All other source, toolchain, artifact, reproducibility, safety, corpus
and software requirements remain mandatory.

The candidate validator, canonical gate, operator-attestation creator and signed
physical admission CLI default to production. Select `--profile m1-staging`
explicitly for an M1 staging candidate. Production invocations reject staging
evidence. Staging gate receipts retain the profile on PASS and BLOCKED paths,
including report publication failures. Lane selection is unchanged.

The admin-browser lane stages the pinned Chromium and WebKit cache as well as
the robot-preview browser. For the explicit M1 profile, the gate sets
`TBOT_MJPEG_REPLAY_CANDIDATE_BROWSER=1`: the mounted MJPEG replay checks use
the frozen Chromium/WebKit pair and retain all replay assertions. Missing cache
bindings fail before launch, and a failed candidate launch never falls back to
host Chrome. Standalone and ordinary production-profile replay retain their
existing Chrome/WebKit behavior; that host-Chrome diagnostic is not frozen
staging-browser evidence.

## Pinned firmware

The separate staging physical policy binds firmware commit
`7edf23ac4e09a745700396330b06fd26c929e05c`, application SHA-256
`6cdf24124d3c7469d1c2c3644300cff64f5b1a99cb305712c93e33d19c31ac0e`
(3846880 bytes), and firmware manifest SHA-256
`ac798559639e9e6beb2183958af6ca65b9ca36c132e8e69499fc3424fca6ebe2`.
Evidence lives in `M1/runs/20260921T094320Z/firmware-build-b/manifest.json`.
Two independent clean staging builds have identical BIN, ELF and sdkconfig;
configuration/artifact audits also pass for the ordinary production profile.

Signed input and identity candidate bindings must both carry the staging profile.
The physical policy permits only the pinned app at `0x20000`, within `0x3f0000`
bytes. It protects the separate inactive slot at `0x410000` as well as all
previously protected regions and requires `preserveInactiveApplication: true`.
Production pins and document schemas remain unchanged. Current-session partition
readback, actual attended safety and protected-region preservation must still be
established; software pins do not establish those physical facts.

The staging policy pins `/dev/cu.usbmodem101`, observed for USB serial
`14:C1:9F:D1:AC:20` in M1. The production default remains
`/dev/cu.usbmodem1101`. Inventory, signed robot/lease documents, receipt checks
and publication-time exclusivity checks all use the selected policy. A missing,
renumbered or occupied port fails admission; this pin is not a live observation.
The software evidence auditor uses the same explicit profile and still requires
the expected identity's Ed25519 signature and every safety assertion.

## Qualification and physical boundary

After source changes, rebuild and refreeze the exact firmware/images/dependency
trees/portal before running quick, full and live-db. Pass
`COURSE_MODE_NATIVE_TEST_SCRATCH_ROOT` through the canonical shell launcher for
the backend native lane. Existing confirmation of the trusted operator account
may be reused; an attestation still needs the current candidate binding.

Fresh signed physical admission must pass after the required software gates and
bind their current snapshot. Point-of-use authorization and attended safety are
separate from this software migration. Preserve the known-good app, Wi-Fi,
ordinary-profile credentials, NVS, inactive slot and original assets. No lesson
assignment or shared SD activation/eviction belongs to M1.

The legacy TFT lesson-journey receipt is not a connection receipt and its
historical source/materializer/Compose bindings remain unchanged. Never relabel
that evidence or claim M1 establishes a completed lesson, smooth teaching or
original-quality display. Record actual M1 boot identity, authenticated staging
connection and robot-origin asset accessibility separately, with current hashes
and observations. Physical authentication and restore remain unproved until
performed on the admitted candidate.
