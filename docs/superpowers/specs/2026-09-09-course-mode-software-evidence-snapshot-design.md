# Course Mode Software Evidence Snapshot Design

## Goal

Replace the impossible claim that several mutable files remain unchanged at the
instant an audit report is published with a verifiable, content-addressed claim
about the exact bytes that the software audit evaluated.

Candidate `.44` must not proceed to physical admission unless the current
candidate and evidence tree exactly match the subject hashes recorded in a
passing schema-v2 software-audit report.

## Problem

The admission privacy change correctly recognizes the public anti-replay
`sessionId` in two signed G7 documents, but publication-time revalidation cannot
make multiple mutable filesystem paths and one report change atomically.

Any check-before-write or check-after-write design retains a final race:

- an input can change after the last input check;
- the output pathname can change after the last pathname check;
- a valid PASS can briefly become visible before a later failure is detected;
- retry logic can accidentally preserve a stale PASS or overwrite an unrelated
  concurrent replacement.

Adding more publication locks, rereads, or commit bytes only moves the race. It
does not define a durable claim that a later consumer can independently verify.

## Chosen Design

The software audit becomes a claim about a logical content-addressed snapshot.
It captures bounded source bytes once, evaluates only those captured bytes, and
publishes a schema-v2 report containing a canonical manifest of every audited
subject.

The report does not claim that mutable source paths remain current forever.
Instead, downstream physical gates securely reread the current source tree and
require its exact subject set, byte counts, and SHA-256 hashes to match the
report before trusting `status=pass`.

This separates two questions:

1. **Historical audit result:** Did the captured snapshot pass all software
   checks?
2. **Current physical eligibility:** Do the current candidate and evidence
   files still equal that audited snapshot?

A changed source makes the report stale for physical use, but does not rewrite
or invalidate the historical report.

## Snapshot Capture

The auditor captures these scopes:

- `candidate`: the exact candidate manifest bytes;
- `evidence`: every regular file below `evidenceRoot`, excluding the canonical
  `06-software-evidence-audit.json` output;
- `preserved`: every regular file below each explicitly supplied preserved
  root.

The existing limits remain authoritative:

- at most 4,096 filesystem entries;
- at most 64 MiB total captured source bytes;
- at most 8 MiB per file;
- existing archive, expanded-byte, Base64, and JSON limits.

Each source is securely opened without following the final symlink, checked for
regular-file ownership, link count, permissions, and size, and read into a
bounded in-memory record. Evaluation uses that record and never rereads the
same source for parsing or privacy scanning.

Directories, invalid entries, symlinks, devices, insecure metadata, and budget
failures still produce blocking findings. Directory enumeration order must not
affect the snapshot identifier.

## Canonical Subject Manifest

Every captured file produces one subject:

```json
{
  "scope": "evidence",
  "path": "G7-admission/admission-input.json",
  "bytes": 4527,
  "sha256": "64-lowercase-hex",
  "scanPolicy": "physical-admission-top-level-session-id.v1"
}
```

Subject rules:

- `scope` is exactly `candidate`, `evidence`, or `preserved`;
- `path` is a canonical POSIX logical path, never an absolute host path;
- the candidate subject path is its candidate filename;
- evidence paths are relative to `evidenceRoot`;
- preserved paths include a stable preserved-root index followed by the
  root-relative path, for example `0/runtime/tombstone.json`;
- `bytes` is the exact raw byte count;
- `sha256` hashes the exact raw bytes;
- `scanPolicy` records how those bytes were evaluated.

The subjects are sorted by `(scope, path)`. Duplicate `(scope, path)` values are
invalid. `snapshot.id` is the SHA-256 of canonical JSON bytes for the complete
sorted `subjects` array using sorted keys, compact separators, UTF-8, and finite
JSON values.

The report itself and future physical-admission outputs are not subjects. No
audit sidecar is created inside the evidence root.

## Admission Privacy Policy

The generic privacy scanner remains unchanged.

The narrow G7 policy retains the sound behavior already developed:

1. Resolve the four admission paths only from
   `candidate.tools.physicalAdmission`.
2. Require canonical absolute paths inside the current evidence root.
3. Require exact descriptor keys: `input`, `output`, `expectedIdentity`, and
   `expectedIdentitySignature`.
4. Strict-parse the captured input and expected-identity JSON bytes with
   duplicate-key rejection, finite values, depth/size bounds, and complete
   production schemas.
5. Require the same canonical top-level UUID `sessionId` in both documents.
6. Verify the Ed25519 signature over canonical expected-identity bytes.
7. Require the signed candidate path and SHA-256 to name and hash the captured
   candidate bytes.
8. Call the existing pure admission validator at the input's recorded
   `checkedAt`, using the expected serial identity values without inventorying
   or opening serial.
9. Scan canonical JSON with only the exact top-level `/sessionId` value changed
   to `redacted`.
10. Separately scan every other decoded semantic string leaf so escaped
    newlines cannot hide bearer values, Base64 content, private keys, or other
    private material.

The input, expected identity, and signature must be the same captured records
used both for validation and for the subject hashes. Missing, malformed,
unexpected, or incomplete declared admission evidence receives no exemption
and produces a blocking finding.

## Report Schema V2

The canonical report shape is:

```json
{
  "schemaVersion": 2,
  "validator": "course-mode-software-evidence.snapshot.v1",
  "candidateId": "course-mode-2026-09-09.44",
  "snapshot": {
    "algorithm": "sha256",
    "id": "64-lowercase-hex",
    "subjects": []
  },
  "admissionBinding": {
    "candidateSha256": "64-lowercase-hex",
    "inputSha256": "64-lowercase-hex",
    "expectedIdentitySha256": "64-lowercase-hex",
    "signatureSha256": "64-lowercase-hex",
    "signedCanonicalIdentitySha256": "64-lowercase-hex",
    "sessionPolicy": "top-level-canonical-uuid.v1"
  },
  "checkedArchiveMemberCount": 0,
  "checkedFileCount": 0,
  "checks": {},
  "findings": [],
  "status": "pass"
}
```

Additional rules:

- `validator` and `schemaVersion` are exact constants;
- `admissionBinding` is present only when a valid declared admission bundle was
  captured;
- a candidate that declares admission but lacks a valid binding must fail;
- `checkedFileCount` equals the number of subjects;
- all existing positive checks remain true for PASS;
- `physicalActionsPerformed` and `productionDatabaseUsed` remain false;
- no self-hash is stored inside the report;
- the physical gate computes the report file hash and records it as
  `softwareAuditSha256` in its own result.

## Report Publication

Report publication returns to a small ordinary atomic-file pattern:

1. Serialize the complete report to a temporary regular file in the output
   directory.
2. Flush and `fsync` the temporary file.
3. Set mode `0444` and validate its metadata.
4. Atomically rename it to `06-software-evidence-audit.json`.
5. `fsync` the output directory.

The validity boundary is the atomic rename of a complete JSON document. The
auditor performs no publication-time source rereads and makes no current-state
claim after capture.

If a later run fails before publication, a previous report may remain. This is
safe because every physical consumer must compare current subjects against the
report before accepting it. A stale report is historical evidence, not current
authorization.

The complex mutable-path transaction machinery introduced after the semantic
privacy implementation is removed: no `_AuditReport` binding object, no
publication-time candidate/admission rereads, no first-byte commit marker, no
in-place stale-PASS rewriting, and no output inode adoption protocol.

## Current-Snapshot Verification

A shared pure verifier validates a report against current files. It must:

1. Secure-read and strict-parse the schema-v2 audit report.
2. Require `status=pass`, `findings=[]`, exact validator/schema fields, and the
   expected check values.
3. Recompute `snapshot.id` from the canonical subjects array.
4. Securely enumerate the current candidate, evidence root, and configured
   preserved roots using the same inclusion/exclusion and metadata rules.
5. Recompute the exact subject set, byte counts, and SHA-256 values.
6. Require equality with the report; additions, removals, renames, or content
   changes return a stale/mismatch reason.
7. Require the report's `admissionBinding` values to equal the matching subject
   hashes and the canonical signed-identity hash.

The verifier never opens serial, inventories USB, starts Docker, accesses the
network, or changes source files.

## Downstream Enforcement

### Physical Admission

`course_mode_physical_flash_admission.py` derives the audit path as:

```text
Path(candidate["evidenceRoot"]) / "06-software-evidence-audit.json"
```

Before serial inventory or any physical action, it invokes the shared verifier
for the candidate and current evidence root. Any invalid, missing, or stale
report returns a blocking `softwareAudit.*` reason.

On PASS, the physical-admission result adds:

```json
{
  "softwareAuditSha256": "sha256-of-exact-audit-report-bytes",
  "softwareSnapshotId": "snapshot.id"
}
```

The four-key `tools.physicalAdmission` descriptor remains unchanged.

### Release Gate

`course_mode_release_gate.py` validates and captures the same software-audit
file before starting the `physical-flash-admission` lane. Its physical binding
stores the audit file identity, SHA-256, and snapshot ID and rechecks them before
and after the lane.

The expected physical result includes `softwareAuditSha256` and
`softwareSnapshotId`. A copied, stale, mutated, or mismatched report cannot
satisfy the lane.

## Compatibility

- Schema-v1 reports remain immutable historical artifacts but cannot authorize
  a new physical admission.
- Candidate `.43` is never rewritten or upgraded in place.
- Candidate `.44` must be created after the final merged Admin SHA and must
  receive a complete new qualification run.
- The existing four-key physical-admission descriptor is preserved to avoid a
  candidate-manifest schema expansion.
- No `.sha256` sidecar is added under the evidence root because it would become
  another audited subject and destabilize rerun counts.

## Testing

### Auditor

- Schema-v2 shape, exact validator string, subject count, and snapshot ID.
- Deterministic snapshot ID under different directory enumeration orders.
- Subject hashes and byte counts equal exact captured raw bytes.
- Parsing and scanning use each captured admission record exactly once.
- Only top-level input/identity `sessionId` is exempted.
- Password, token, bearer, private key, split Base64, duplicate key, nested or
  unrelated session field, malformed UUID, invalid signature, symlink,
  hardlink, insecure metadata, alias, and outside-root cases fail closed.
- Files added, removed, renamed, or changed after capture do not alter the
  historical report but make current verification stale.
- The output report is excluded from the subject set.
- Interrupted temporary writes never expose partial new JSON.

### Physical Admission

- Missing or schema-v1 audit fails before serial inventory.
- Wrong report hash, snapshot ID, candidate subject, every G7 subject, or any
  other evidence subject fails.
- Added, removed, renamed, writable, linked, or replaced current files fail.
- PASS output includes exact `softwareAuditSha256` and `softwareSnapshotId`.
- `physicalActionsPerformed=false` and `serialOpened=false` remain guaranteed.

### Release Gate

- The physical binding rejects an invalid or stale audit before launching the
  lane.
- Audit drift before the lane, during the lane, and before report publication
  fails closed.
- Expected-result mismatches for audit SHA or snapshot ID fail.
- Existing candidate, G7, serial-lease, and protected-partition checks remain
  unchanged.

## Security Boundary

This design protects against accidental drift, stale/copy reuse, partial
publication, untrusted filesystem objects, and source changes detected at
point-of-use. It does not claim a simultaneous filesystem snapshot across
multiple mutable paths and does not defend against compromise of the trusted
operator account that can replace the auditor, all inputs, and all reports.

A literal same-instant filesystem snapshot would require a producer-owned
immutable generation or platform filesystem snapshot and is outside candidate
`.44` scope.
