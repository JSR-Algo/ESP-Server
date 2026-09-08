# Course Mode Admission Session Privacy Design

## Goal

Allow the course-mode software evidence audit to accept the public anti-replay
`sessionId` fields in the two candidate-bound physical-admission documents while
continuing to reject actual credentials, tokens, private keys, and untrusted
session fields everywhere else.

## Problem

Candidate `.43` completed Quick `4/4`, Full `20/20`, live PostgreSQL `21/21`,
NEW/ROLLBACK `2/2`, post-rollback Chromium/WebKit `2/2`, and runtime continuity.
The final software audit nevertheless reports `content.secret` because the
generic JSON secret scanner treats every non-empty key normalized to `sessionid`
as confidential.

The admission schema deliberately requires a public UUID `sessionId` in:

- `tools.physicalAdmission.input`
- `tools.physicalAdmission.expectedIdentity`

Those documents are signed, candidate-bound anti-replay inputs rather than
credentials. Removing or redacting the UUID would invalidate the admission
contract and signature chain.

## Chosen Design

Keep `course_mode_evidence_privacy.py` strict and unchanged for general evidence.
Add a narrowly scoped admission-document path in
`course_mode_software_evidence_audit.py`:

1. Resolve the two allowed files from the validated candidate descriptor, not
   from filenames discovered in the evidence tree.
2. Require both paths to be canonical descendants of the current evidence root
   and to match the exact files being scanned.
3. Parse each document with the existing strict JSON rules: bounded size,
   duplicate-key rejection, finite JSON, secure regular-file metadata, and no
   symlink/path aliasing.
4. Validate the complete public admission schemas using the existing admission
   validator helpers or equivalent shared strict helpers.
5. Require `sessionId` to be a canonical UUID and identical in the input,
   expected identity, and candidate-bound signed session.
6. Scan a copy of the validated JSON with only that exact `sessionId` value
   replaced by `redacted`. Continue scanning all other keys and values normally.

No global allowlist is added. A `sessionId` in any other file, an unexpected
session field, a malformed or mismatched UUID, or any real secret remains a
blocking `content.secret` finding.

## Security Boundaries

- Do not exempt files merely because they are named `admission-input.json` or
  `expected-identity.json`.
- Do not exempt arbitrary keys containing `session`.
- Do not suppress regex scanning, bearer detection, private-key detection,
  archive scanning, base64 recursion, transcript checks, or binary-media checks.
- Do not log document contents or secret fixture values.
- Do not mutate `.43` evidence to manufacture a pass. Source changes invalidate
  `.43` and require a fresh candidate `.44` with a new signature/attestation and
  fresh qualification reports.

## Tests

Use red-green-refactor coverage for:

- valid candidate-bound admission input and expected identity with the same UUID
  pass the secret scan;
- the same documents still fail when they contain password, token, bearer,
  private-key, or other secret material;
- a `sessionId` in any unrelated evidence file still fails;
- filename-only impersonation outside the candidate descriptor still fails;
- mismatched, malformed, duplicate, or extra session fields fail closed;
- symlink, alias, metadata, and containment protections remain enforced;
- the full software-audit and evidence-privacy test suites remain green.

## Release Flow

After review and merge:

1. Build only the affected Admin image if candidate image identity requires it.
2. Create candidate `.44`; never rewrite `.43`.
3. Generate fresh candidate-bound G7 identity/input/signature and operator
   attestation without exposing the private key.
4. Rerun Quick, Full, live-db, runtime NEW/ROLLBACK, post-rollback Chromium and
   WebKit, continuity inspection, and software audit.
5. Obtain independent spec and security reviews.
6. Refresh G7 admission within its 300-second freshness window only when the
   exact `/dev/cu.usbmodem1101` device is present and unheld.
7. Stop and request a new complete `.44` flash authorization before any serial
   open, reset, readback, or app-only write.
