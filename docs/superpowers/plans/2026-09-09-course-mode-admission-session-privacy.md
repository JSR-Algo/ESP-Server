# Course Mode Admission Session Privacy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the software evidence audit accept only the signed, candidate-bound public admission `sessionId` while preserving all existing secret and privacy protections, then qualify a fresh immutable candidate `.44`.

**Architecture:** Keep the generic privacy scanner unchanged. The software auditor derives the two public admission paths from `candidate.tools.physicalAdmission`, validates the complete signed admission bundle with the existing admission validator at its recorded `checkedAt`, and replaces only each document's top-level `sessionId` with `redacted` for content scanning. Any invalid bundle receives no exemption and remains a blocking `content.secret` finding.

**Tech Stack:** Python 3.11, pytest, strict JSON, Ed25519/cryptography, Git worktrees, Docker Compose, Playwright, PostgreSQL 16, ESP-IDF evidence hashes.

---

### Task 1: Add Failing Public-Admission Privacy Tests

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_software_evidence_audit.py`

- [ ] **Step 1: Add a fixture that installs a valid signed admission bundle**

Use a test-only Ed25519 key, monkeypatch the admission module's pinned public key,
write the input, identity, and 64-byte signature under `evidence/G7-admission`, and
add the exact four-path `tools.physicalAdmission` descriptor to the candidate.
The fixture must use the production `TOP_KEYS`, `IDENTITY_KEYS`, partition table,
robot identity, flash plan, and safety fields rather than a reduced mock schema.

```python
def _install_signed_admission(candidate_path, evidence, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setattr(admission, "PINNED_APPROVAL_PUBLIC_KEY_RAW", public)
    monkeypatch.setattr(
        admission,
        "PINNED_APPROVAL_KEY_FINGERPRINT",
        hashlib.sha256(public).hexdigest(),
    )
    return _write_exact_admission_documents(
        candidate_path=candidate_path,
        evidence=evidence,
        private_key=key,
        signer_fingerprint=hashlib.sha256(public).hexdigest(),
    )
```

- [ ] **Step 2: Add the positive regression test**

```python
def test_candidate_bound_signed_admission_session_id_is_public_evidence(
    evidence_fixture, monkeypatch
):
    candidate, evidence, output = evidence_fixture
    _install_signed_admission(candidate, evidence, monkeypatch)

    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)

    assert result.returncode == 0
    assert report["status"] == "pass"
    assert report["checks"]["secretScan"] is True
    assert "content.secret" not in report["findings"]
```

- [ ] **Step 3: Add fail-closed parameterized tests**

Cover these independent mutations:

```python
@pytest.mark.parametrize("mutation", [
    lambda bundle: bundle["input"].update(password="not-public"),
    lambda bundle: bundle["identity"].update(token="not-public"),
    lambda bundle: bundle["input"].__setitem__("sessionId", str(uuid.uuid4())),
    lambda bundle: bundle["identity"].__setitem__("sessionId", "not-a-uuid"),
    lambda bundle: bundle["identity"].update(extraSession="not-public"),
])
def test_public_admission_exemption_fails_closed(
    evidence_fixture, monkeypatch, mutation
):
    candidate, evidence, output = evidence_fixture
    bundle = _install_signed_admission(candidate, evidence, monkeypatch)
    mutation(bundle)
    _rewrite_and_resign_admission(candidate, bundle)
    result = _run(candidate, evidence, output)
    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert "content.secret" in report["findings"]
```

Also add separate tests proving an unrelated evidence file containing
`{"sessionId":"cab43f0d-62dc-49c4-9d30-e9630d195a44"}` still fails, and a
filename-only `G7-admission/admission-input.json` not bound by the candidate still
fails.

- [ ] **Step 4: Run RED and record the expected failure**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_audit.py \
  -k 'candidate_bound_signed_admission or public_admission_exemption'
```

Expected: the valid signed admission test fails with `content.secret`; all
existing behavior remains unchanged.

- [ ] **Step 5: Commit tests only**

```bash
git add main/tbot-server/tests/test_course_mode_software_evidence_audit.py
git commit -m "test(course-mode): cover public admission session evidence"
```

### Task 2: Implement Narrow Candidate-Bound Redaction

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_software_evidence_audit.py`
- Test: `main/tbot-server/tests/test_course_mode_software_evidence_audit.py`

- [ ] **Step 1: Add canonical JSON and descriptor helpers**

Add helpers with these responsibilities and signatures:

```python
def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _candidate_admission_paths(
    candidate: object, evidence_root: Path,
) -> dict[str, Path] | None:
    # Require exactly input/output/expectedIdentity/expectedIdentitySignature.
    # Require canonical absolute paths inside evidence_root and an absent or
    # regular candidate-bound output path. Return None on any mismatch.
```

- [ ] **Step 2: Validate the signed static admission bundle**

```python
def _public_admission_scan_payloads(
    candidate: object, evidence_root: Path,
) -> dict[Path, bytes]:
    # Secure-read input, identity, and signature.
    # Strict-parse both JSON documents.
    # Require a canonical top-level UUID shared by both documents.
    # Verify Ed25519 over canonical identity bytes.
    # Call admission.validate_documents with now=input.checkedAt,
    # devices=[admission.SERIAL_PATH], holders=[], inventory_error=None so the
    # complete static schema is checked without opening or inventorying serial.
    # Return canonical copies with only the top-level sessionId set to
    # "redacted"; return {} for every invalid condition.
```

The helper must import `course_mode_physical_flash_admission` lazily and must not
call `collect_serial_inventory`, `main`, subprocesses, Docker, or serial APIs.

- [ ] **Step 3: Use sanitized bytes only for the two exact scanned paths**

Before walking the evidence root, compute:

```python
public_admission_payloads = _public_admission_scan_payloads(
    candidate, evidence_root
)
```

Inside the evidence loop, preserve byte accounting and metadata checks using the
original file, but call the generic scanner with:

```python
scan_data = public_admission_payloads.get(path, data)
content_findings, member_count = scan_evidence_payload(
    scan_data,
    path.name,
    _budget=archive_budget,
    _base64_state=base64_state,
)
```

- [ ] **Step 4: Run GREEN and full affected suites**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_audit.py
python3 -m pytest -q tests/test_course_mode_physical_flash_admission.py
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py \
  -k physical_admission
python3 -m compileall -q scripts/course_mode_software_evidence_audit.py
git diff --check
```

Expected: all tests pass with no skips, warnings, or new privacy exceptions.

- [ ] **Step 5: Commit the implementation**

```bash
git add main/tbot-server/scripts/course_mode_software_evidence_audit.py
git commit -m "fix(course-mode): recognize signed admission session evidence"
```

### Task 3: Independent Review and Merge

**Files:**
- Review: `main/tbot-server/scripts/course_mode_software_evidence_audit.py`
- Review: `main/tbot-server/tests/test_course_mode_software_evidence_audit.py`

- [ ] **Step 1: Run an independent specification review**

Require explicit confirmation that the implementation exempts only the two
candidate descriptor paths, only the top-level canonical UUID, and only after
complete schema and Ed25519 verification.

- [ ] **Step 2: Run an independent security/code-quality review**

Require no Critical/Important finding for path containment, symlink/hardlink
handling, TOCTOU, signature verification, malformed JSON, secret bypass,
base64/archive scanning, or accidental physical access.

- [ ] **Step 3: Resolve findings and rerun Task 2 verification**

Every fix must receive a regression test first. Repeat both reviews until they
approve.

- [ ] **Step 4: Merge without rewriting history**

```bash
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server merge \
  --no-ff fix/course-mode-admission-session-privacy-44 \
  -m "merge: harden course-mode admission privacy audit"
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server diff --check HEAD^ HEAD
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server status --short
```

Expected: merge succeeds and main is clean.

### Task 4: Create Immutable Candidate `.44`

**Files:**
- Create: `task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-09.44.json`
- Create: `task-artifacts/course-mode-production-readiness/course-mode-2026-09-09.44/G7-admission/*`
- Create: candidate-bound Admin web image for the merged Admin SHA

- [ ] **Step 1: Rebuild only the changed Admin image**

Use the `.43` Docker provenance, exact immutable base-image digests, clean
Git-produced build context, `--pull=false`, platform `linux/arm64`, and the tag:

```text
local/tbot-server-web:course-mode-physical-tft-${ADMIN_SHA}
```

Require exact OCI revision/source labels, expected static assets, smoke PASS, and
record the immutable image ID. Reuse the unchanged `.43` backend image and
firmware artifacts only after re-verifying their exact IDs and hashes.

- [ ] **Step 2: Generate fresh signed admission inputs**

Create a new UUID session and current canonical UTC `checkedAt`. Bind the exact
`.44` candidate identity, merged Admin SHA/image ID, unchanged backend and
firmware identities, robot MAC `14:c1:9f:d1:ac:20`, board
`LCDWiki ES3C35P`, target `esp32s3`, port `/dev/cu.usbmodem1101`, and app-only
operation at `0x20000`. Sign only canonical expected-identity bytes using the
operator key outside workspace/evidence/build contexts; never print the key.

- [ ] **Step 3: Validate and attest `.44`**

```bash
python3 main/tbot-server/scripts/course_mode_candidate_manifest.py \
  /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-09.44.json
python3 main/tbot-server/scripts/course_mode_operator_attestation.py \
  --candidate /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-09.44.json \
  --output /Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-09.44/00-operator-attestation.json \
  --confirm-trusted-operator-account \
  --confirm-untrusted-automation-stopped
```

Expected validator: `status=pass`, `reasons=[]`. Lock candidate, signature,
inputs, validator report, and attestation to `0444`.

### Task 5: Qualify `.44` End to End

**Files:**
- Create: `.44/02-runtime-*.json`
- Create: `.44/03-quick-gate.json`
- Create: `.44/04-full-gate.json`
- Create: `.44/05-live-db-gate.json`
- Create: `.44/06-software-evidence-audit.json`

- [ ] **Step 1: Run candidate-bound Quick, Full, and live-db gates**

From the exact `.44` Admin repository path, with isolated fixture credentials and
nonstandard loopback ports, require Quick `4/4`, Full `20/20`, and live-db
`21/21` with terminal `live-postgres`. No required skips, retained paths,
cleanup failures, or production database variables are allowed.

- [ ] **Step 2: Produce post-rollback runtime evidence in one process**

Invoke `run_gate(..., lanes=...)` for NEW then ROLLBACK, immediately followed by
Chromium desktop then WebKit desktop. Inspect the surviving base stack and require
four canonical read-only mounts, candidate image IDs, healthy backend/web,
assignment flags false, correct asset origin, no production DB, and
`manualRecreateAfterRollback=false`.

- [ ] **Step 3: Run the fixed software evidence audit**

```bash
CANDIDATE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-09-09.44.json
EVIDENCE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-09-09.44
python3 scripts/course_mode_software_evidence_audit.py \
  --candidate "$CANDIDATE" \
  --evidence-root "$EVIDENCE" \
  --output "$EVIDENCE/06-software-evidence-audit.json"
```

Require `status=pass`, `findings=[]`, every positive check true,
`physicalActionsPerformed=false`, and `productionDatabaseUsed=false`. Lock and
hash all canonical reports.

- [ ] **Step 4: Clean only `.44` runtime resources**

Use the exact `.44` Compose project and runtime paths. Stop/remove only its
containers, network, volumes, temporary PostgreSQL cluster, and generated test
runtime; verify its backend/web/media/PostgreSQL ports are closed. Preserve all
candidate reports and historical `.43` evidence.

### Task 6: Stop at Fresh Physical Authorization Boundary

**Files:**
- Create only after point-of-use checks: `.44/G7-admission/admission-result.json`
- Create only after point-of-use checks: `.44/07-physical-admission-gate.json`

- [ ] **Step 1: Run non-opening readiness checks**

Using only glob, `lstat`/`stat`, hashes, Git/Docker inspection, and trusted `lsof`,
require exactly `/dev/cu.usbmodem1101`, a character device with no holder. Refresh
the signed admission input so `checkedAt` is less than 300 seconds old.

- [ ] **Step 2: Run candidate-bound physical admission only**

Run mode `physical-preflight` and require one PASS lane named
`physical-flash-admission`, `physicalActionsPerformed=false`, and
`serialOpened=false`. This step must not open serial, reset USB, invoke esptool,
or move the robot.

- [ ] **Step 3: Present exact `.44` identities and stop**

Present candidate/admin/backend/firmware/image/app/admission hashes, MAC, board,
target, exact port, offset, bytes, app-only operation, protected partitions,
safety assertions, and stop conditions. Obtain a fresh complete `.44` flash
authorization before any serial open, reset, readback, or write.
