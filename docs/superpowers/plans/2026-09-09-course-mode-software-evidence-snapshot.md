# Course Mode Software Evidence Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the racy mutable-path publication protocol with a schema-v2 content-addressed software-evidence snapshot, and require that exact snapshot at both physical-admission enforcement points before candidate `.44` can reach serial inventory.

**Architecture:** A new shared module owns secure bounded capture, canonical subject manifests, snapshot IDs, audit-report validation, and current-tree verification. The auditor captures candidate/evidence/preserved bytes once, evaluates only those records, and atomically publishes a historical snapshot claim; physical admission and the release gate independently compare the current exact subject set and hashes with that claim. Candidate `.44` uses `preservedRoots=[]`, keeps the existing four-key `tools.physicalAdmission` descriptor, and derives the audit path from `evidenceRoot`.

**Tech Stack:** Python 3.11, pytest, strict JSON, SHA-256, Ed25519/cryptography, secure descriptor reads, atomic rename/fsync, Git worktrees, Docker Compose, Playwright, PostgreSQL 16, ESP-IDF.

---

**Scope boundary:** This plan ends with reviewed, merge-ready source. Candidate `.44` cannot be specified immutably until the final merged Admin SHA and rebuilt image ID exist, so its build/qualification/physical commands belong in a second candidate-specific plan generated from those exact values. No serial inventory, reset, flash, or robot motion is part of this plan.

## File Structure

- Create `main/tbot-server/scripts/course_mode_software_evidence_snapshot.py`: secure capture records, canonical schema-v2 subjects, exact-set comparison, audit report validation, and point-of-use verification without serial/network/process side effects.
- Create `main/tbot-server/tests/test_course_mode_software_evidence_snapshot.py`: unit tests for canonical IDs, secure capture, exclusions, preserved-root indexing, and stale detection.
- Modify `main/tbot-server/scripts/course_mode_software_evidence_audit.py`: remove the rejected publication transaction, consume one captured snapshot, emit schema v2, and publish with one atomic rename.
- Modify `main/tbot-server/tests/test_course_mode_software_evidence_audit.py`: replace rejected race-protocol tests with capture-once, manifest, admission-binding, and atomic-publication tests.
- Modify `main/tbot-server/scripts/course_mode_physical_flash_admission.py`: verify the schema-v2 snapshot before the first serial inventory and bind its digest/ID into PASS output.
- Modify `main/tbot-server/tests/test_course_mode_physical_flash_admission.py`: prove stale/missing/v1 audits fail before inventory and PASS includes the new bindings.
- Modify `main/tbot-server/scripts/course_mode_release_gate.py`: bind and recheck the audit around the physical lane and expected result.
- Modify `main/tbot-server/tests/test_course_mode_release_gate.py`: cover pre-lane, during-lane, post-lane, and publication-time audit drift.

### Task 1: Remove the Rejected Publication Protocol

**Files:**
- Modify by revert: `main/tbot-server/scripts/course_mode_software_evidence_audit.py`
- Modify by revert: `main/tbot-server/tests/test_course_mode_software_evidence_audit.py`
- Preserve: `docs/superpowers/specs/2026-09-09-course-mode-software-evidence-snapshot-design.md`

- [ ] **Step 1: Record the clean starting point**

Run:

```bash
git status --short
git log --oneline -8
```

Expected: no worktree changes; `82f951e7` is HEAD above the five rejected implementation commits and `7acc620e`.

- [ ] **Step 2: Revert only the rejected implementation commits without rewriting history**

Run exactly newest to oldest:

```bash
git revert --no-commit 12f2aa3a 83c828a8 7cd97a20 89d834b2 003c23f6
```

Expected: the auditor/test behavior matches `7acc620e`; the approved snapshot design document remains present.

- [ ] **Step 3: Verify the retained admission privacy behavior**

Run:

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_audit.py
python3 -m compileall -q scripts/course_mode_software_evidence_audit.py
git diff --check
```

Expected: the retained privacy tests pass, including exact candidate binding, complete signed admission schema, semantic-string scanning, split Base64, and private-key rejection.

- [ ] **Step 4: Commit the simplification**

```bash
git add main/tbot-server/scripts/course_mode_software_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_software_evidence_audit.py
git commit -m "refactor(course-mode): remove racy audit publication protocol"
```

### Task 2: Build the Shared Snapshot Capture and Verifier

**Files:**
- Create: `main/tbot-server/scripts/course_mode_software_evidence_snapshot.py`
- Create: `main/tbot-server/tests/test_course_mode_software_evidence_snapshot.py`

- [ ] **Step 1: Write canonical-manifest RED tests**

Add tests that construct records in different orders and require identical sorted subjects and snapshot IDs:

```python
def test_snapshot_id_is_canonical_and_order_independent():
    first = snapshot.subject_manifest((
        snapshot.CapturedSubject("evidence", "z.json", b"z", "evidence-privacy.v1"),
        snapshot.CapturedSubject("candidate", "candidate.json", b"c", "candidate-json.v1"),
    ))
    second = snapshot.subject_manifest(tuple(reversed((
        snapshot.CapturedSubject("evidence", "z.json", b"z", "evidence-privacy.v1"),
        snapshot.CapturedSubject("candidate", "candidate.json", b"c", "candidate-json.v1"),
    ))))
    assert first == second
    assert snapshot.snapshot_id(first) == snapshot.snapshot_id(second)
    assert first[0]["scope"] == "candidate"
    assert first[1]["bytes"] == 1
    assert first[1]["sha256"] == hashlib.sha256(b"z").hexdigest()
```

Also require rejection of duplicate `(scope, path)`, absolute paths, `..`, invalid scopes, invalid scan policies, non-integer/negative byte counts, and non-lowercase SHA-256 values when validating report subjects.

- [ ] **Step 2: Run RED for the absent module**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_snapshot.py \
  -k 'canonical or duplicate or subject'
```

Expected: collection fails because `course_mode_software_evidence_snapshot` does not exist.

- [ ] **Step 3: Implement immutable records and canonical hashing**

Create these public contracts:

```python
SCHEMA_VERSION = 2
VALIDATOR = "course-mode-software-evidence.snapshot.v1"
OUTPUT_NAME = "06-software-evidence-audit.json"
FUTURE_PHYSICAL_OUTPUT_NAME = "07-physical-admission-gate.json"
EXPECTED_CHECKS = {
    "candidateIdentity": True,
    "repositoryIdentity": True,
    "imageIdentityAndProvenance": True,
    "firmwareIdentity": True,
    "curriculumIdentity": True,
    "secureFileMetadata": True,
    "validator": True,
    "attestationBinding": True,
    "runtimeContinuity": True,
    "quickGate4of4": True,
    "fullGate20of20": True,
    "liveDbGate21of21": True,
    "terminalLivePostgres": True,
    "secretScan": True,
    "archiveSafety": True,
    "binaryMediaAbsent": True,
    "privateContentAbsent": True,
    "rawPlaywrightAbsent": True,
    "physicalActionsPerformed": False,
    "productionDatabaseUsed": False,
}

@dataclass(frozen=True)
class CapturedSubject:
    scope: str
    path: str
    data: bytes
    scan_policy: str

    def manifest_entry(self) -> dict[str, object]:
        return {
            "scope": self.scope,
            "path": self.path,
            "bytes": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
            "scanPolicy": self.scan_policy,
        }

@dataclass(frozen=True)
class SnapshotCapture:
    subjects: tuple[CapturedSubject, ...]
    findings: tuple[str, ...]
    entry_count: int
    total_bytes: int

@dataclass(frozen=True)
class VerifiedSoftwareAudit:
    audit_sha256: str
    snapshot_id: str
    audit_identity: tuple[int, ...]
    report: dict[str, object]

def subject_manifest(subjects: Sequence[CapturedSubject]) -> list[dict[str, object]]: ...
def snapshot_id(subjects: Sequence[dict[str, object]]) -> str: ...
def capture_candidate(candidate_path: Path) -> tuple[CapturedSubject | None, tuple[str, ...]]: ...
```

Canonical JSON must be `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")`; sort by `(scope, path)` and reject duplicate keys.

- [ ] **Step 4: Write secure-capture RED tests**

Create a candidate and evidence tree and assert:

```python
capture = snapshot.capture_snapshot(
    candidate_subject,
    evidence_root,
    preserved_roots=(preserved_a, preserved_b),
    excluded_evidence_paths=(physical_output,),
    evidence_scan_policies={
        admission_input: "physical-admission-top-level-session-id.v1",
        expected_identity: "physical-admission-top-level-session-id.v1",
    },
)
assert capture.findings == ()
assert {(item.scope, item.path) for item in capture.subjects} == {
    ("candidate", candidate_path.name),
    ("evidence", "nested/report.json"),
    ("preserved", "0/runtime/tombstone.json"),
    ("preserved", "1/browser/summary.json"),
}
```

The fixture must also create `06-software-evidence-audit.json`, the candidate-bound physical output, and `07-physical-admission-gate.json`; none may appear as subjects. Add independent tests for symlink, hardlink, writable file/directory, non-owner, file over 8 MiB, more than 4,096 entries, and more than 64 MiB total.

- [ ] **Step 5: Implement one-pass bounded capture**

Implement:

```python
def capture_snapshot(
    candidate_subject: CapturedSubject,
    evidence_root: Path,
    preserved_roots: Sequence[Path] = (),
    excluded_evidence_paths: Sequence[Path] = (),
    evidence_scan_policies: Mapping[Path, str] | None = None,
) -> SnapshotCapture:
    """Capture evidence/preserved bytes once and include the supplied candidate record."""
```

Rules are exact:

- `capture_candidate()` and the tree reader use `O_NOFOLLOW`, require regular file, `st_nlink == 1`, current effective UID ownership, no group/other write bits, stable pre/post descriptor and pathname identity, 8 MiB per file;
- securely validate every directory in the candidate/evidence/preserved ancestry and reject writable or changed directories;
- count every enumerated entry toward 4,096 and every captured byte, including candidate, toward 64 MiB;
- candidate logical path is its filename and policy is `candidate-json.v1`;
- ordinary evidence policy is `evidence-privacy.v1`;
- `evidence_scan_policies` may override only exact canonical evidence-file paths, and the only production override is `physical-admission-top-level-session-id.v1` for the candidate-declared input and expected identity; unknown/outside-root override paths fail capture;
- preserved paths are `<zero-based-root-index>/<root-relative-POSIX-path>` and policy is `preserved-evidence-privacy.v1`;
- exclude only the canonical report, candidate-declared physical output passed by the caller, and `07-physical-admission-gate.json`; do not exclude temporary-file name patterns.

- [ ] **Step 6: Write exact-set verifier RED tests**

Build a valid minimal schema-v2 report from a capture and parameterize mutations:

```python
@pytest.mark.parametrize("mutation", ["change", "add", "remove", "rename"])
def test_verify_current_snapshot_rejects_any_tree_drift(snapshot_fixture, mutation):
    candidate_path, evidence_root, report_path = snapshot_fixture
    mutate_tree(evidence_root, mutation)
    verified, reasons = snapshot.verify_current_software_audit(
        candidate_path, evidence_root, preserved_roots=()
    )
    assert verified is None
    assert "softwareAudit.stale" in reasons
```

Add tests for missing report, schema v1, wrong validator, `status != pass`, nonempty findings, altered checks, invalid snapshot ID, wrong byte count/hash/policy, duplicate subjects, mismatched `candidateId`, malformed `admissionBinding`, and replacement of the audit pathname with a new inode containing identical bytes.

- [ ] **Step 7: Implement report validation and point-of-use verification**

Implement:

```python
def verify_current_software_audit(
    candidate_path: Path,
    evidence_root: Path,
    preserved_roots: Sequence[Path] = (),
) -> tuple[VerifiedSoftwareAudit | None, tuple[str, ...]]:
    """Validate report bytes and require exact equality with the current capture."""
```

It must secure-read `evidence_root / OUTPUT_NAME`, retain its complete stable descriptor/path identity tuple in `VerifiedSoftwareAudit.audit_identity`, strict-parse duplicate-free finite JSON, require the exact top-level key set (including `admissionBinding` for a candidate that declares physical admission), exact schema/validator/check values, `status == "pass"`, `findings == []`, `checkedFileCount == len(subjects)`, and recompute `snapshot.id`. Call `capture_candidate()` exactly once, strict-parse that record, require matching `candidateId`, derive the four-key physical descriptor and excluded physical output, then pass the same record to `capture_snapshot()` with the caller-provided preserved roots and exact input/identity scan-policy overrides before comparing the complete sorted subject arrays. Validate `admissionBinding` has exactly six fields, require the exact `top-level-canonical-uuid.v1` session policy and admission scan policies, require candidate/input/identity/signature hashes to equal matching subjects, and recompute `signedCanonicalIdentitySha256` from strict-parsed expected identity canonical JSON. Return stable reason codes beginning `softwareAudit.` and perform no serial, subprocess, Docker, network, or file writes.

- [ ] **Step 8: Run the shared-module suite and commit**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_snapshot.py
python3 -m compileall -q scripts/course_mode_software_evidence_snapshot.py
git diff --check
git add scripts/course_mode_software_evidence_snapshot.py \
  tests/test_course_mode_software_evidence_snapshot.py
git commit -m "feat(course-mode): add content-addressed evidence snapshots"
```

Expected: all new tests pass with no hardware or external-service access.

### Task 3: Refactor the Auditor to Capture Once and Emit Schema V2

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_software_evidence_audit.py`
- Modify: `main/tbot-server/tests/test_course_mode_software_evidence_audit.py`

- [ ] **Step 1: Write schema-v2 and exact-subject RED tests**

Extend the passing fixture assertions:

```python
assert report["schemaVersion"] == 2
assert report["validator"] == "course-mode-software-evidence.snapshot.v1"
assert report["checkedFileCount"] == len(report["snapshot"]["subjects"])
assert report["snapshot"]["algorithm"] == "sha256"
assert report["snapshot"]["id"] == snapshot.snapshot_id(
    report["snapshot"]["subjects"]
)
assert report["snapshot"]["subjects"] == sorted(
    report["snapshot"]["subjects"], key=lambda item: (item["scope"], item["path"])
)
```

For every fixture file, compare its raw byte count and SHA-256 to its subject. Assert the candidate subject exists, the report itself is absent, and `checkedFileCount` changes exactly when an included file is added.

- [ ] **Step 2: Write capture-once admission RED tests**

Monkeypatch the shared secure-read primitive to count reads by path. Run a valid signed admission audit and require candidate, input, expected identity, and signature each be read exactly once. Mutate the on-disk admission input after its capture callback and prove the emitted historical subject/`admissionBinding.inputSha256` still describe and evaluate the captured bytes, while a subsequent `verify_current_software_audit()` returns `softwareAudit.stale`.

- [ ] **Step 3: Run RED**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_audit.py \
  -k 'schema_v2 or subject or capture_once or historical_snapshot'
```

Expected: failures show schema v1 and repeated direct reads.

- [ ] **Step 4: Replace direct traversal with the shared capture**

At the beginning of `audit()`:

1. Canonicalize paths and validate the fixed output path.
2. Call `capture_candidate()` once and strict-parse that retained record.
3. Derive the exact four admission paths from that candidate.
4. Pass the same candidate record to `capture_snapshot(candidate_subject, evidence_root, preserved_roots, (admission_output,), admission_scan_policies)` once; it must not reopen the candidate. Set `admission_scan_policies` only after the descriptor paths are exact/canonical/inside-root; otherwise use no overrides and fail the declared admission bundle closed.
5. Build lookup maps from `(scope, logical path)` to `CapturedSubject`.
6. Load all required evidence JSON from captured records, never `_read_secure_file()`.
7. Validate/sanitize the signed admission input and identity from their captured records and scan all other semantic strings.
8. Run preserved manifest/sanitization checks and all privacy/archive scans from captured bytes.

Change `_public_admission_scan_payloads` to accept captured raw bytes rather than paths and return:

```python
@dataclass(frozen=True)
class PublicAdmissionScan:
    input_payloads: tuple[bytes, ...]
    identity_payloads: tuple[bytes, ...]
    binding: dict[str, str]
```

The binding must contain exact lowercase hashes:

```python
{
    "candidateSha256": hashlib.sha256(candidate_raw).hexdigest(),
    "inputSha256": hashlib.sha256(input_raw).hexdigest(),
    "expectedIdentitySha256": hashlib.sha256(identity_raw).hexdigest(),
    "signatureSha256": hashlib.sha256(signature_raw).hexdigest(),
    "signedCanonicalIdentitySha256": hashlib.sha256(
        _canonical_json_bytes(identity_document)
    ).hexdigest(),
    "sessionPolicy": "top-level-canonical-uuid.v1",
}
```

Assign only the input and expected-identity subjects `physical-admission-top-level-session-id.v1`; the signature remains `evidence-privacy.v1`. A declared but invalid/incomplete bundle adds `content.secret` and omits `admissionBinding`.

- [ ] **Step 5: Assemble the exact report shape**

Return an ordinary dictionary, not `_AuditReport`:

```python
report = {
    "schemaVersion": 2,
    "validator": snapshot.VALIDATOR,
    "candidateId": candidate_id,
    "snapshot": {
        "algorithm": "sha256",
        "id": snapshot.snapshot_id(subjects),
        "subjects": subjects,
    },
    "checkedArchiveMemberCount": checked_archive_members,
    "checkedFileCount": len(subjects),
    "checks": checks,
    "findings": sorted(findings),
    "status": "pass" if not findings else "fail",
}
if admission_scan is not None:
    report["admissionBinding"] = admission_scan.binding
```

Delete `_AuditReport`, candidate/admission publication rereads, stale-PASS rewriting, output binding/adoption, invalid-first-byte commit, and all related helpers/tests. Keep generic privacy scanning unchanged.

- [ ] **Step 6: Run GREEN and commit**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_audit.py
python3 -m pytest -q tests/test_course_mode_software_evidence_snapshot.py
python3 -m pytest -q tests/test_course_mode_physical_flash_admission.py
python3 -m pytest -q tests/test_course_mode_candidate_manifest.py -k physical_admission
python3 -m compileall -q scripts/course_mode_software_evidence_audit.py
git diff --check
git add scripts/course_mode_software_evidence_audit.py \
  tests/test_course_mode_software_evidence_audit.py
git commit -m "feat(course-mode): publish schema-v2 evidence snapshots"
```

### Task 4: Restore a Small Atomic Report Publication Boundary

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_software_evidence_audit.py`
- Modify: `main/tbot-server/tests/test_course_mode_software_evidence_audit.py`

- [ ] **Step 1: Write interrupted-publication RED tests**

Preinstall a valid immutable prior report, monkeypatch the write loop and `os.replace` separately, and require:

```python
assert json.loads(output.read_bytes()) == prior_report
assert not list(output.parent.glob(f".{output.name}.*"))
```

Add a success test that records call order and requires file fsync before rename, `0444` before rename, and parent-directory fsync after rename. Do not assert source rereads because schema v2 makes no publication-time current-state claim.

- [ ] **Step 2: Implement the sole validity boundary**

Use this behavior:

```python
def _write_output(path: Path, report: dict[str, object]) -> bool:
    payload = json.dumps(
        report, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8") + b"\n"
    parent_fd = descriptor = None
    temporary_name = None
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_name = Path(temporary)
        _write_all(descriptor, payload)
        os.fchmod(descriptor, 0o444)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_uid != os.geteuid() or metadata.st_size != len(payload):
            return False
        os.replace(temporary_name, path)
        temporary_name = None
        os.fsync(parent_fd)
        return True
    except OSError:
        return False
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            with contextlib.suppress(FileNotFoundError):
                temporary_name.unlink()
        if parent_fd is not None:
            os.close(parent_fd)
```

Validate the output path is exactly `evidence_root / OUTPUT_NAME` before calling it. Do not create directories, mutate an earlier report on failure, or add a sidecar.

- [ ] **Step 3: Run publication tests and commit**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_software_evidence_audit.py \
  -k 'output or publish or interrupted or atomic'
python3 -m pytest -q tests/test_course_mode_software_evidence_audit.py
git diff --check
git add scripts/course_mode_software_evidence_audit.py \
  tests/test_course_mode_software_evidence_audit.py
git commit -m "fix(course-mode): publish snapshot reports atomically"
```

### Task 5: Enforce the Snapshot Before Physical Serial Inventory

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_flash_admission.py`
- Modify: `main/tbot-server/tests/test_course_mode_physical_flash_admission.py`

- [ ] **Step 1: Write pre-inventory RED tests**

Monkeypatch `collect_serial_inventory` to raise if called. Parameterize missing audit, schema v1, bad report hash/ID, candidate mismatch, each G7 hash mismatch, other evidence change, add/remove/rename, writable file, hardlink, symlink, and replacement of the audit file by a new inode with identical bytes. Every case must return `1`, print a fail payload containing a `softwareAudit.*` reason, and leave both `physicalActionsPerformed` and `serialOpened` false.

- [ ] **Step 2: Write PASS binding RED test**

Install a valid schema-v2 report and assert the published result contains:

```python
assert result["softwareAuditSha256"] == hashlib.sha256(audit_path.read_bytes()).hexdigest()
assert result["softwareSnapshotId"] == audit["snapshot"]["id"]
```

- [ ] **Step 3: Verify RED**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_physical_flash_admission.py \
  -k software_audit
```

Expected: the current admission script reaches inventory and lacks the two result fields.

- [ ] **Step 4: Verify before any hardware-facing call**

After candidate bytes are loaded, parsed, and proven equal to the signed candidate hash, but before `_candidate_external_binding`, `collect_serial_inventory`, or other physical/runtime inspection, call:

```python
verified_audit, audit_reasons = software_snapshot.verify_current_software_audit(
    Path(binding["path"]), evidence_root, preserved_roots=()
)
if verified_audit is None:
    _failure(audit_reasons)
    return 1
```

Candidate `.44` formally uses no preserved roots. Keep the exact four-key descriptor unchanged. Add `softwareAuditSha256` and `softwareSnapshotId` to the PASS payload from `verified_audit`.

- [ ] **Step 5: Recheck the snapshot at commit time**

Extend `commit_safe` so it invokes `verify_current_software_audit()` again and requires the same `audit_identity`, `audit_sha256`, and `snapshot_id` as the initial verified object. This recheck remains read-only and precedes final publication of the physical result; byte-identical pathname replacement still fails.

- [ ] **Step 6: Run GREEN and commit**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_physical_flash_admission.py
python3 -m pytest -q tests/test_course_mode_software_evidence_snapshot.py
python3 -m compileall -q scripts/course_mode_physical_flash_admission.py
git diff --check
git add scripts/course_mode_physical_flash_admission.py \
  tests/test_course_mode_physical_flash_admission.py
git commit -m "fix(course-mode): require current software snapshot for admission"
```

### Task 6: Bind the Snapshot Through the Release Gate

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Write release-binding RED tests**

Add fixtures that generate a valid audit report and parameterize:

- missing/v1/failed/malformed audit before lane launch;
- evidence add/remove/rename/change before lane launch;
- audit replacement after `_physical_admission_binding()` and before command execution;
- byte-identical audit replacement on a new inode at every boundary;
- evidence or audit drift while the physical command runs;
- drift after lane success but before release report publication;
- physical result with wrong `softwareAuditSha256` or `softwareSnapshotId`.

Each must block `physical-flash-admission`; pre-lane failures must prove `run_bounded_command` was never called.

- [ ] **Step 2: Extend `PhysicalAdmissionBinding`**

Add immutable fields:

```python
@dataclass(frozen=True)
class PhysicalAdmissionBinding:
    descriptor: tuple[tuple[str, str], ...]
    evidence_root_identity: tuple[int, ...]
    input_identities: tuple[tuple[str, tuple[int, ...], str], ...]
    output_parent_identity: tuple[int, ...]
    software_audit_identity: tuple[int, ...]
    software_audit_sha256: str
    software_snapshot_id: str
    expected_result: bytes
```

Inside `_physical_admission_binding()`, derive `candidate_path` from the signed G7 input (and require `expected_candidate_path` when supplied), call the shared verifier with `preserved_roots=()`, and return `None` on any failure. Retain `audit_identity`, `audit_sha256`, and `snapshot_id` in the binding; add the two hash/ID fields, but not host inode metadata, to `expected_result`.

- [ ] **Step 3: Recheck around every physical-lane boundary**

Require `_physical_admission_binding(candidate, ...) == physical_admission_binding`:

1. before staging/launching the physical lane;
2. immediately before `run_bounded_command`;
3. immediately after it returns;
4. inside `_physical_admission_result_valid()`;
5. before a PASS release report is atomically published.

The equality comparison must cover audit identity, SHA, and snapshot ID. Preserve current source-runtime, candidate, serial lease, and physical-result binding checks.

- [ ] **Step 4: Include the shared verifier in isolated physical execution**

Add `scripts/course_mode_software_evidence_snapshot.py` to `PHYSICAL_ADMISSION_SOURCE_PATHS`, increase the expected record count from three to four, export its bytes through a dedicated `TBOT_ADMISSION_SNAPSHOT_B64` environment variable, and load it in `PHYSICAL_ADMISSION_BOOTSTRAP` before loading `course_mode_physical_flash_admission.py`. Keep the environment-size bound enforced.

- [ ] **Step 5: Run GREEN and commit**

```bash
cd main/tbot-server
python3 -m pytest -q tests/test_course_mode_release_gate.py \
  -k physical_flash_admission
python3 -m pytest -q tests/test_course_mode_release_gate.py
python3 -m pytest -q tests/test_course_mode_physical_flash_admission.py
python3 -m compileall -q scripts/course_mode_release_gate.py
git diff --check
git add scripts/course_mode_release_gate.py tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): bind software snapshot through release gate"
```

### Task 7: Independent Reviews and Full Software Verification

**Files:**
- Review all files changed since `82f951e7`

- [ ] **Step 1: Run a fresh specification-compliance review**

The reviewer must explicitly check every section of `docs/superpowers/specs/2026-09-09-course-mode-software-evidence-snapshot-design.md`, especially capture-once behavior, exact exclusions, admission binding, schema v2, stale semantics, pre-inventory enforcement, and `preservedRoots=[]` for `.44`.

- [ ] **Step 2: Fix every spec gap test-first and repeat review**

For each finding: add a failing regression test, run it RED, make the smallest production change, run GREEN, and return to the same reviewer until approved.

- [ ] **Step 3: Run a separate security/code-quality review**

Require no Critical or Important finding for path containment, symlink/hardlink/ownership/permission checks, duplicate JSON keys, non-finite JSON, hash ambiguity, exact-set comparison, admission exemption scope, partial publication, side effects before verification, or isolated-source bootstrap completeness.

- [ ] **Step 4: Fix every quality/security finding test-first and repeat review**

Do not proceed with an open Critical or Important issue.

- [ ] **Step 5: Run all affected suites from a clean state**

```bash
cd main/tbot-server
python3 -m pytest -q \
  tests/test_course_mode_software_evidence_snapshot.py \
  tests/test_course_mode_software_evidence_audit.py \
  tests/test_course_mode_physical_flash_admission.py \
  tests/test_course_mode_release_gate.py \
  tests/test_course_mode_candidate_manifest.py
python3 -m compileall -q \
  scripts/course_mode_software_evidence_snapshot.py \
  scripts/course_mode_software_evidence_audit.py \
  scripts/course_mode_physical_flash_admission.py \
  scripts/course_mode_release_gate.py
git diff --check
git status --short
```

Expected: all tests pass, compile succeeds, diff check is clean, and only deliberate plan progress edits remain if checkboxes were updated.
