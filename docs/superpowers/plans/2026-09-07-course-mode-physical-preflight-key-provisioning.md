# Course Mode Physical Preflight Key Provisioning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Provision the Course Mode Ed25519 operator trust boundary, issue candidate `.41`, qualify it through all software gates, and obtain a signed physical-preflight PASS before any serial access or flash.

**Architecture:** Keep the private operator key outside every repository and evidence root, commit only a 32-byte public key pin and its digest, and extend the strict candidate schema with one optional `physicalPreflight` descriptor. Candidate `.41` is rebuilt from committed identities and binds immutable signed preflight inputs; `.40` remains unchanged historical evidence.

**Tech Stack:** Python 3.11, `cryptography` Ed25519, strict JSON, pytest, Git, Docker/Compose, Playwright Chromium/WebKit, ESP-IDF 5.5.4, esptool.

---

### Task 1: Admit a Strict Physical-Preflight Candidate Descriptor

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_candidate_manifest.py`
- Modify: `main/tbot-server/tests/test_course_mode_candidate_manifest.py`

- [ ] **Step 1: Write failing descriptor tests**

Add tests that clone the normal valid candidate fixture, add this exact shape, and require validation success:

```python
candidate["tools"]["physicalPreflight"] = {
    "input": str(evidence / "G7-preflight/preflight-input.json"),
    "output": str(evidence / "G7-preflight/preflight-result.json"),
    "expectedIdentity": str(evidence / "G7-preflight/expected-identity.json"),
    "expectedIdentitySignature": str(evidence / "G7-preflight/expected-identity.sig"),
}
```

The test creates valid JSON inputs, a 64-byte signature, no output file, and secure file metadata. Add rejection cases for missing/extra keys, relative paths, paths outside `evidenceRoot`, symlinks, hardlinks, writable inputs, malformed JSON, non-64-byte signatures, and a pre-existing output path.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py -k physical_preflight
```

Expected: the valid descriptor fails with `tools.keys` because the validator does not yet admit `physicalPreflight`.

- [ ] **Step 3: Implement the minimal strict validator**

Keep the existing required keys and admit only the optional descriptor:

```python
REQUIRED_TOOLS_KEYS = {
    "docker", "dockerCompose", "nodeInstalls", "playwrightBrowsers",
    "robotPreviewBrowser", "node", "pythonTestRuntime", "espIdf",
}
PHYSICAL_PREFLIGHT_KEYS = {
    "input", "output", "expectedIdentity", "expectedIdentitySignature",
}
```

Require `set(tools)` to equal either `REQUIRED_TOOLS_KEYS` or
`REQUIRED_TOOLS_KEYS | {"physicalPreflight"}`. Validate all descriptor paths as
absolute descendants of the resolved `evidenceRoot`; securely read the two JSON
inputs, require a 64-byte signature, and require the output path not to exist.
Reuse the module's secure regular-file, link-count, ownership, size, strict-JSON,
and path-replacement protections rather than adding a second policy.

- [ ] **Step 4: Run focused and full validator tests**

```bash
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py
python3 -m pytest -q tests/test_course_mode_release_gate.py -k physical_preflight
```

Expected: both commands exit zero.

- [ ] **Step 5: Commit the schema change**

```bash
git add main/tbot-server/scripts/course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py
git commit -m "feat(course-mode): bind signed physical preflight inputs"
```

### Task 2: Provision and Pin the Operator Public Key

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`
- Create outside Git: `/Users/manhhodinh/.tbot-operator/course-mode-preflight/course-mode-preflight-ed25519.pem`
- Create outside Git: `/Users/manhhodinh/.tbot-operator/course-mode-preflight/course-mode-preflight-ed25519.raw.pub`

- [ ] **Step 1: Add a failing checked-in pin test**

```python
def test_production_operator_public_key_is_provisioned_and_self_consistent():
    assert preflight.PINNED_APPROVAL_PUBLIC_KEY_RAW is not None
    assert len(preflight.PINNED_APPROVAL_PUBLIC_KEY_RAW) == 32
    assert hashlib.sha256(preflight.PINNED_APPROVAL_PUBLIC_KEY_RAW).hexdigest() == (
        preflight.PINNED_APPROVAL_KEY_FINGERPRINT
    )
    assert preflight.PINNED_APPROVAL_KEY_FINGERPRINT != "unprovisioned"
```

- [ ] **Step 2: Run the test and verify RED**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py \
  -k production_operator_public_key_is_provisioned
```

Expected: FAIL because `PINNED_APPROVAL_PUBLIC_KEY_RAW` is `None`.

- [ ] **Step 3: Create the private key without exposing it**

Use `umask 077`, `os.open(..., O_CREAT | O_EXCL, 0o600)`, and
`Ed25519PrivateKey.generate()` exactly as documented in
`main/tbot-server/docs/course-mode-physical-preflight-signing.md`. Require the
parent directory to be `0700`, both files to be UID 501-owned regular non-symlink
files with link count one and mode `0600`, and the public key to be 32 bytes.
Print only the public hex and SHA-256 fingerprint.

- [ ] **Step 4: Pin only the public identity**

Use `apply_patch` to replace only the two existing constant assignments. Set
`PINNED_APPROVAL_PUBLIC_KEY_RAW` to `bytes.fromhex()` containing the exact 64
lowercase public-key hex characters printed in Step 3, and set
`PINNED_APPROVAL_KEY_FINGERPRINT` to the exact 64 lowercase SHA-256 characters
printed in Step 3. Never print or read the PEM into command output.

- [ ] **Step 5: Run signature tests and secret scan**

```bash
python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py
git diff --check
git diff -- main/tbot-server/scripts/course_mode_physical_tft_preflight.py \
  main/tbot-server/tests/test_course_mode_physical_tft_preflight.py
```

Expected: tests PASS; diff contains one public key, one public fingerprint, and the
focused test, with no PEM/private bytes.

- [ ] **Step 6: Commit the public pin**

```bash
git add main/tbot-server/scripts/course_mode_physical_tft_preflight.py \
  main/tbot-server/tests/test_course_mode_physical_tft_preflight.py
git commit -m "chore(course-mode): provision physical preflight signer"
```

### Task 3: Rebuild the Admin Image and Freeze Candidate `.41`

**Files:**
- Generate: `task-artifacts/course-mode-production-readiness/final-candidate-images/web-<admin-sha>/`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/G7-preflight/`
- Generate: `task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-07.41.json`

- [ ] **Step 1: Verify all three repositories and firmware artifacts**

Require clean worktrees and exact backend/firmware identities:

```bash
git status --short
git -C /Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342 status --short
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock status --short
shasum -a 256 /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/final-candidate-firmware-ed76-portable-lock/candidate-b54c6ca-attestation-20260831/xiaozhi.bin
```

Expected: all statuses empty and app digest
`782020e2f8ac44bd197f57e9e126c196286a8223005de1e425f8290f12b28dff`.

- [ ] **Step 2: Build and inspect the exact admin web image**

Use the same reviewed clean-worktree image build procedure recorded for `.40`, but
tag it with the new admin HEAD. Require `linux/arm64` and exact revision/source/
build-source labels before recording the image ID.

- [ ] **Step 3: Create the candidate evidence root securely**

Create `course-mode-2026-09-07.41/G7-preflight` with mode `0500` only after all
inputs have been written. Generate a fresh UUID session ID and timestamps in
canonical RFC3339 UTC. Set exclusive expiry to seven days after creation.

- [ ] **Step 4: Materialize exact preflight JSON from trusted evidence**

Build `expected-identity.json` and `preflight-input.json` using the existing schema
from `course_mode_physical_tft_preflight.py` and the approved Course Mode evidence.
Bind MAC `14:c1:9f:d1:ac:20`, the new admin SHA/image ID, backend SHA
`bb6c484e69b71759462b5915138a262d82068b84`, firmware SHA
`b54c6ca33e9beb3747b44feceb7c64fea33fe1d6`, app digest/size/offset, exact
partition boundaries, renderer `teebot-lesson-renderer.v5`, and the 26-lesson
curriculum checksum. Do not invent readback evidence: every referenced artifact
must already exist and rehash correctly; otherwise stop with a named blocker.

- [ ] **Step 5: Sign canonical identity bytes**

Canonicalize with `sort_keys=True`, separators `(",", ":")`, UTF-8,
`ensure_ascii=False`, and `allow_nan=False`. Sign using the private PEM and write
exactly 64 bytes to `expected-identity.sig` using exclusive creation. Command
output may contain only the identity digest, signature digest, and public signer
fingerprint.

- [ ] **Step 6: Generate and validate `.41`**

Copy `.40` structurally, update candidate ID/times/evidence root/admin SHA/web
image, and add the four `tools.physicalPreflight` paths. Run:

```bash
cd main/tbot-server
python3 scripts/course_mode_candidate_manifest.py \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-07.41.json
```

Expected: JSON `status` is `pass` and `reasons` is empty.

### Task 4: Requalify Candidate `.41`

**Files:**
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/00-operator-attestation.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/02-*.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/03-quick-gate.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/04-full-gate.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/05-live-db-gate.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/06-software-evidence-audit.json`

- [ ] **Step 1: Issue the fresh operator attestation**

```bash
python3 scripts/course_mode_operator_attestation.py \
  --candidate /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-07.41.json \
  --output /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/00-operator-attestation.json \
  --confirm-trusted-operator-account --confirm-untrusted-automation-stopped
```

Require `candidate.createdAt <= attestation.createdAt <= validation_now < candidate.expiresAt`.

- [ ] **Step 2: Run quick, full, and live-db gates**

Set `COURSE_MODE_OPERATOR_ATTESTATION` to the `.41` attestation and use
`scripts/course_robot_e2e_gates.sh --candidate ... --mode ... --report ...`.
Expected exact results: quick `4/4`, full `20/20`, live-db `21/21` with terminal
lane `live-postgres`, zero skipped required lanes, and PASS verdicts.

- [ ] **Step 3: Run runtime NEW, ROLLBACK, browser, and continuity checks**

Use the same isolated Compose orchestration and four read-only canonical mounts
as `.40`. Require NEW and ROLLBACK PASS, Chromium and WebKit PASS after rollback,
assignment flags false after cleanup, image IDs equal `.41`, and no production DB.

- [ ] **Step 4: Run the software evidence audit**

```bash
python3 scripts/course_mode_software_evidence_audit.py \
  --candidate /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-07.41.json \
  --evidence-root /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41 \
  --output /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/06-software-evidence-audit.json
```

Expected: `status=pass`, no findings, `physicalActionsPerformed=false`, and
`productionDatabaseUsed=false`.

- [ ] **Step 5: Remove only `.41` runtime resources**

Remove `.41` Compose containers/networks/volumes and confirm its bound ports are
closed. Preserve unrelated stacks and all `.40`/`.36` historical evidence.

### Task 5: Run Signed Physical Preflight and Stop Before Flash

**Files:**
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/G7-preflight/preflight-result.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41-attended-physical-handoff.md`

- [ ] **Step 1: Revalidate point-of-use invariants**

Confirm candidate unexpired, app and manifest hashes unchanged,
`/dev/cu.usbmodem1101` is the sole discovered serial device, no process owns it,
and the recorded operator confirmation still names MAC `14:C1:9F:D1:AC:20`, one
LCDWiki ES3C35P, adult observer, clear motion area, reachable power isolation, and
stable power/LAN.

- [ ] **Step 2: Run candidate-bound physical-preflight mode**

```bash
COURSE_MODE_OPERATOR_ATTESTATION=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/00-operator-attestation.json \
  scripts/course_robot_e2e_gates.sh \
  --candidate /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-07.41.json \
  --mode physical-preflight \
  --report /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-07.41/07-physical-preflight-gate.json
```

Expected: physical lane exit zero, preflight result `PASS`, signature verified,
and signer fingerprint equals the committed public pin.

- [ ] **Step 3: Audit and freeze pre-flash evidence**

Rehash all `.41` evidence, verify regular-file/non-symlink/link-count/ownership
rules, scan for secrets/private key/audio/transcripts, and set finalized evidence
inputs to `0444`. Verify the private PEM is absent from the workspace and Docker
build contexts.

- [ ] **Step 4: Issue the attended flash handoff**

Record exact candidate/admin/backend/firmware/image/app identities, preflight
result hash, port/MAC, allowed app-only command shape, protected partitions, stop
conditions, and rollback order. Do not claim production GO or bug-free status.

- [ ] **Step 5: Stop at the flash authorization boundary**

Do not open serial, reset, run esptool, flash, read physical memory, or cause robot
motion within this implementation plan. Present the verified `.41` preflight
result and request a fresh, exact candidate-flash authorization immediately before
the subsequent attended physical execution.
