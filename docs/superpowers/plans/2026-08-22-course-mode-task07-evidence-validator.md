# Course Mode Task 07 Evidence Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic offline validator and BLOCKED evidence template for the Task 07 physical gate.

**Architecture:** A dependency-free Python CLI validates a JSON evidence package using fail-closed PASS rules. A committed redacted template represents the current deferred physical blockers without touching hardware.

**Tech Stack:** Python 3, pytest, JSON, Markdown

---

### Task 1: Define Validation Behavior

**Files:**
- Create: `main/tbot-server/tests/test_course_mode_task07_evidence_validate.py`

- [x] Write tests for a valid BLOCKED template, a complete PASS package, incomplete lanes, missing rollback rehearsal, invalid capture hashes, unsafe command records, and short Git SHAs.
- [x] Run `python3 -m pytest -q tests/test_course_mode_task07_evidence_validate.py` from `main/tbot-server` and confirm failure because the validator does not exist.

### Task 2: Add Offline Template and Validator

**Files:**
- Create: `docs/qa/artifacts/2026-08-22-course-mode-task07/physical-evidence-template.json`
- Create: `main/tbot-server/scripts/course_mode_task07_evidence_validate.py`

- [x] Add a redacted `PHYSICAL_BLOCKED` template containing all seven required lanes, exact candidate identities, deferred physical blockers, empty capture/command records, and false physical safety/rollback assertions.
- [x] Implement `validate_document(document)` with deterministic errors and strict PASS gates.
- [x] Add a CLI accepting one JSON path and emitting the validation result as JSON without executing any recorded command.
- [x] Run the focused tests and confirm all pass.

### Task 3: Document and Verify

**Files:**
- Modify: `docs/qa/artifacts/2026-08-22-course-mode-task07/software-readiness.md`
- Modify: `docs/qa/ad-hoc/2026-08-21-course-mode-v2-task07-physical-hil.md`

- [x] Record the new deterministic capture contract and the fact that it does not reduce physical-only blockers.
- [x] Run the validator against the BLOCKED template and expect exit `0` with `valid: true` and verdict `PHYSICAL_BLOCKED`.
- [x] Run `python3 -m pytest -q tests/test_course_mode_task07_evidence_validate.py tests/test_physical_smoke_audit.py`.
- [x] Run `python3 -m compileall -q scripts/course_mode_task07_evidence_validate.py` and `git diff --check`.
- [ ] Independently review the complete diff, commit only task-owned files on `main`, and repeat the focused/static checks on the committed result.
