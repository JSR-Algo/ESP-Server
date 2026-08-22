# Course Mode Task 07 Evidence Validator Design

## Scope

Add an offline-only validator and a redacted template for the eventual Task 07
physical evidence package. The tool reads JSON from disk and writes a validation
result; it never enumerates ports, opens serial, executes commands, changes
production state, or controls hardware.

## Contract

- Accept the current `PHYSICAL_BLOCKED` state with explicit deferred blockers.
- Reject `PHYSICAL_PASS` unless every required physical lane passes with
  timestamps, measurements, and checksum-pinned capture references.
- Require exact candidate Git identities and explicit candidate-install,
  readback, and rollback command evidence for a PASS.
- Require physical rollback rehearsal, verified stop path, and verified safe
  rest for a PASS.
- Require a verified zero-uplink safe-idle preflight and no open unauthorized
  microphone-uplink finding for a PASS.
- Keep PASS locked while the only pinned candidate predates the microphone-uplink
  remediation; require a separately reviewed replacement artifact identity.
- Pin the rollback manifest, exact split-image flash maps, and both candidate and
  rollback readback regions and sizes.
- Require lane-specific numeric measurements with contract units and an approved
  authority instead of accepting a generic PASS boolean.
- Store executed commands as argument arrays with authorization, timestamp, and
  exit code. Reject shell command strings, shell interpreters, chip erase, and
  merged-image flashing.
- Keep the connected robot untouched; generated fixtures are synthetic and
  contain no real child data or full device identity.

## Files

- `main/tbot-server/scripts/course_mode_task07_evidence_validate.py`: pure
  validation library and CLI.
- `main/tbot-server/tests/test_course_mode_task07_evidence_validate.py`: PASS,
  BLOCKED, identity, capture, lane, rollback, and unsafe-command coverage.
- `docs/qa/artifacts/2026-08-22-course-mode-task07/physical-evidence-template.json`:
  current redacted BLOCKED template for future attended capture.
- `docs/qa/artifacts/2026-08-22-course-mode-task07/software-readiness.md` and the
  Task 07 HIL report: record the offline validator and unchanged physical gate.

## Error Handling

Malformed JSON or an invalid document exits nonzero and emits a deterministic
JSON result. Validation errors name the exact missing or unsafe invariant. The
validator never auto-fills evidence and never upgrades a verdict.

## Verification

Run the focused validator tests, validate the committed BLOCKED template, run
the existing Task 07-adjacent physical-audit unit tests, compile the script, and
perform independent review of the complete scoped diff.
