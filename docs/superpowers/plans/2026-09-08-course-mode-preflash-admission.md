# Course Mode Pre-Flash Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a signed, candidate-bound, non-opening pre-flash admission gate, qualify candidate `.41`, and stop for fresh authorization immediately before physical serial access.

**Architecture:** Preserve schema-v3 `course_mode_physical_tft_preflight.py` as the post-flash receipt validator. Add a smaller `course_mode_physical_flash_admission.py` for facts knowable before physical access, bind it through `tools.physicalAdmission`, and keep the release CLI mode name `physical-preflight` for compatibility.

**Tech Stack:** Python 3.11, strict JSON, Ed25519 via `cryptography`, pytest, Git, Docker/Compose, Playwright, ESP-IDF artifact hashes.

---

### Task 1: Rename the Candidate Extension to Physical Admission

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_candidate_manifest.py`
- Modify: `main/tbot-server/tests/test_course_mode_candidate_manifest.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Write failing naming/command tests**

Require candidates to accept `tools.physicalAdmission` with exactly:

```python
{
    "input": absolute_admission_input,
    "output": absolute_absent_result,
    "expectedIdentity": absolute_signed_identity,
    "expectedIdentitySignature": absolute_64_byte_signature,
}
```

Require `tools.physicalPreflight` and any candidate containing both names to fail.
Require release mode `physical-preflight` to invoke
`scripts/course_mode_physical_flash_admission.py` and name the lane
`physical-flash-admission`.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py -k physical_admission
python3 -m pytest -q tests/test_course_mode_release_gate.py -k physical_preflight
```

Expected: new name rejected and old script/lane still selected.

- [ ] **Step 3: Implement the minimal rename**

Use immutable constants:

```python
PHYSICAL_ADMISSION_KEYS = frozenset({
    "input", "output", "expectedIdentity", "expectedIdentitySignature",
})
```

Rename only the optional descriptor and its reason-code prefix. Preserve all
strict containment, metadata, JSON, size, absent-output, and path-race checks.
Keep `TOOLS_KEYS` equal to the immutable canonical eight-key base.

- [ ] **Step 4: Run full affected suites and commit**

```bash
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py
python3 -m pytest -q tests/test_course_mode_release_gate.py -k physical_preflight
python3 -m pytest -q tests/test_course_mode_physical_tft_receipt_verify.py
git diff --check
git add main/tbot-server/scripts/course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py \
  main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "refactor(course-mode): separate physical admission descriptor"
```

### Task 2: Implement the Non-Opening Admission Validator

**Files:**
- Create: `main/tbot-server/scripts/course_mode_physical_flash_admission.py`
- Create: `main/tbot-server/tests/test_course_mode_physical_flash_admission.py`

- [ ] **Step 1: Define valid signed fixtures and RED test**

The input schema is exactly:

```python
{
    "schemaVersion": 1,
    "sessionId": uuid,
    "checkedAt": canonical_utc,
    "candidate": candidate_identity,
    "robot": robot_identity,
    "serialLease": serial_lease,
    "flashPlan": flash_plan,
    "safety": safety_assertions,
}
```

The signed expected identity contains exactly candidate ID/course/repository/image,
firmware/app/partition, robot/MAC/port, signer, and session fields. The valid test
uses a generated test-only Ed25519 key via monkeypatch and expects `status=pass`,
`physicalActionsPerformed=false`, and `serialOpened=false`.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest -q tests/test_course_mode_physical_flash_admission.py \
  -k valid_signed_admission
```

Expected: import/file-not-found failure because the validator does not exist.

- [ ] **Step 3: Implement strict offline validation**

Reuse the pinned public key and canonical/signature helpers from
`course_mode_physical_tft_preflight.py`. Validate:

```python
APP_OFFSET = "0x20000"
APP_BYTES = 3637200
APP_SHA256 = "782020e2f8ac44bd197f57e9e126c196286a8223005de1e425f8290f12b28dff"
FIRMWARE_SHA = "b54c6ca33e9beb3747b44feceb7c64fea33fe1d6"
ROBOT_MAC = "14:c1:9f:d1:ac:20"
SERIAL_PATH = "/dev/cu.usbmodem1101"
BOARD = "LCDWiki ES3C35P"
TARGET = "esp32s3"
```

Read filesystem/device inventory only with `lstat`, `stat`, and `lsof`; never open
the serial path. Require it to be the sole `/dev/cu.usb*` candidate and have no
holder. Validate the current candidate and repository/image/artifact identities
using existing secure helpers. Reject shell command strings; accept only this
structured operation:

```python
{"operation": "write_flash", "offset": "0x20000", "imageSha256": APP_SHA256,
 "imageBytes": APP_BYTES, "after": "no-reset", "eraseChip": False,
 "mergedImage": False}
```

Require every safety assertion to be exact `True`, including adult observer,
motion clearance, reachable isolation, stable power/LAN, exclusive lease, evidence
readiness, and protected-partition preservation.

- [ ] **Step 4: Add hostile tests**

Cover signature mismatch/malleation, duplicate/nonfinite/oversized JSON, path
replacement, wrong/expired candidate, repository or image drift, app hash/size/
offset drift, missing protected region, extra operation, erase-chip/merged image,
wrong MAC/board/target/port, zero/multiple serial devices, occupied serial, every
false safety assertion, output collision, subprocess timeout, bounded output, and
no secret/private/audio/transcript disclosure.

- [ ] **Step 5: Verify and commit**

```bash
python3 -m pytest -q tests/test_course_mode_physical_flash_admission.py
python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py \
  tests/test_course_mode_physical_tft_receipt_verify.py
python3 -m compileall -q scripts/course_mode_physical_flash_admission.py
rg -n -i "open\(|serial\.Serial|write_flash|erase_flash|transcript|raw.?speech|audio.?data" \
  scripts/course_mode_physical_flash_admission.py
git diff --check
git add main/tbot-server/scripts/course_mode_physical_flash_admission.py \
  main/tbot-server/tests/test_course_mode_physical_flash_admission.py
git commit -m "feat(course-mode): add signed preflash admission gate"
```

The source scan may contain literal operation names only in rejection/validation
logic; it must contain no serial-opening or esptool execution path.

### Task 3: Integrate Candidate-Bound Admission

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Write RED integration tests**

Require `physical_preflight_command(candidate)` to consume only candidate evidence
paths, call the new validator, and never contain `flash`, `esptool`, `build`,
`deploy`, or serial-monitor tokens except the filename’s descriptive `flash` word.
Require attestation binding before/after the lane and fail closed on path drift.

- [ ] **Step 2: Run RED, implement, and run GREEN**

```bash
python3 -m pytest -q tests/test_course_mode_release_gate.py -k physical_preflight
```

Update lane command/script/name without changing quick/full/live-db inventories.
Expected GREEN: all physical-preflight selections pass.

- [ ] **Step 3: Run broad integration and commit**

```bash
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py \
  tests/test_course_mode_physical_flash_admission.py \
  tests/test_course_mode_physical_tft_preflight.py \
  tests/test_course_mode_physical_tft_receipt_verify.py \
  tests/test_course_mode_release_gate.py
git diff --check
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "feat(course-mode): run admission before physical access"
```

### Task 4: Review, Merge, and Build the `.41` Admin Image

**Files:**
- Generate: `task-artifacts/course-mode-production-readiness/final-candidate-images/web-` followed by the first eight characters of the verified merged admin SHA

- [ ] **Step 1: Run independent spec and quality/security reviews**

Resolve every Critical/Important finding and rerun affected tests before merge.

- [ ] **Step 2: Merge the reviewed worktree commits to `main`**

Require both source trees clean, merge without rewriting `.40`, and verify the
exact resulting admin SHA. Do not amend historical commits.

- [ ] **Step 3: Resolve missing base images reproducibly**

Read the exact `.40` Docker build provenance. If a required base is absent, pull
the exact digest recorded by that evidence, inspect platform `linux/arm64`, and
record the digest. Never pull and trust a mutable tag without digest comparison.

- [ ] **Step 4: Build and inspect the web image**

Build from a clean Git-produced context at the merged admin SHA with `--pull=false`,
the approved base digests, revision/source/build-source labels, and tag
`local/tbot-server-web:course-mode-physical-tft-<admin-sha>`. Require smoke PASS,
static assets present, platform `linux/arm64`, and exact labels/image ID.

### Task 5: Create and Qualify Candidate `.41`

**Files:**
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-08.41/G7-admission/`
- Generate: `task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-08.41.json`
- Generate: `.41` software evidence reports

- [ ] **Step 1: Create and sign admission identity**

Generate canonical expected identity and admission input from exact merged source,
image, firmware, app, partition, robot, port, and safety facts. Sign canonical
identity bytes using the operator PEM without printing it. Record only identity,
signature, and public-key digests. Finalize inputs/signature read-only.

- [ ] **Step 2: Generate and validate candidate**

Create `.41` with a seven-day exclusive expiry and `tools.physicalAdmission` paths.
Run candidate validation and require no reasons. Issue a fresh immutable operator
attestation within the candidate interval.

- [ ] **Step 3: Run full software qualification**

Require quick `4/4`, full `20/20`, live-db `21/21`, NEW/ROLLBACK runtime PASS,
Chromium/WebKit post-rollback PASS, four canonical read-only mounts, assignment
flags false, privacy PASS, software audit PASS/no findings, no production DB, and
`physicalActionsPerformed=false`.

- [ ] **Step 4: Clean only `.41` runtime resources**

Remove `.41` containers/networks/volumes and confirm its ports closed. Preserve
unrelated `.36` and all historical evidence.

### Task 6: Run Admission and Stop Before Serial Access

**Files:**
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-08.41/G7-admission/admission-result.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-08.41/07-physical-admission-gate.json`
- Generate: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-08.41-attended-physical-handoff.md`

- [ ] **Step 1: Recheck point-of-use facts without opening serial**

Rehash app/manifest/candidate, recheck expiry, exact single device path, no holder,
and all operator safety conditions.

- [ ] **Step 2: Run candidate-bound `physical-preflight` mode**

Require lane `physical-flash-admission` PASS, verified Ed25519 fingerprint, and
explicit `physicalActionsPerformed=false`, `serialOpened=false`.

- [ ] **Step 3: Freeze/audit admission evidence**

Require UID 501, regular non-symlink, link count one, read-only metadata; scan for
private keys, secrets, audio, transcripts, and unbounded command output. Ensure the
operator PEM is absent from workspace/build contexts/evidence.

- [ ] **Step 4: Request fresh `.41` flash authorization and stop**

Present exact candidate/admin/backend/firmware/image/app/admission hashes, MAC,
port, app-only operation, protected partitions, observer/safety statements, and
stop conditions. Do not open serial, reset, read flash, write flash, or move the
robot until the operator repeats that complete `.41` authorization.
