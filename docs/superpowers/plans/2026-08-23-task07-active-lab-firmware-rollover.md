# Task 07 Active-Lab Firmware Rollover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strictly roll the physical-TFT preflight active-lab firmware identity to reviewed SHA `812f5d3e71d326b350e5b0d1df878d47ac60400e`.

**Architecture:** Change the single authoritative firmware pin in both the preflight and attended-ledger validators and their direct fixtures. Preserve independent caller-supplied lowercase application and bundle-root SHA-256 validation and every unrelated gate.

**Tech Stack:** Python 3, pytest, Markdown Task 07 contracts.

---

### Task 1: Establish The Rollover In A Failing Test

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`

- [ ] **Step 1: Change the valid active-lab fixture to the reviewed SHA**

```python
ACTIVE_LAB_APP = {
    "firmwareSha": "812f5d3e71d326b350e5b0d1df878d47ac60400e",
    "applicationSha256": "c" * 64,
    "bundleRootSha256": "d" * 64,
}
```

Add a mutation asserting superseded SHA `5b6121b7933cda25908cc5bd07f1b494f00728ca`
produces `input.activeLabApp.firmwareSha`. Keep the existing test that supplies
different exact lowercase application and bundle-root SHA-256 values.

- [ ] **Step 2: Run RED**

Run: `python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py`

Expected: failures because the validator still requires the superseded SHA.

### Task 2: Roll The Authoritative Pin And Documentation

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py`
- Modify: `docs/course-mode/task-07-physical-tft-tooling-design.md`
- Modify: `docs/course-mode/task-07-physical-tft-tooling-implementation-plan.md`
- Modify: `docs/qa/ad-hoc/2026-08-21-course-mode-v2-task07-physical-hil.md`
- Modify: `docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md`

- [ ] **Step 1: Replace the single source pin**

Set `ACTIVE_LAB_FIRMWARE_SHA` to
`812f5d3e71d326b350e5b0d1df878d47ac60400e` in both validators.

Do not change application/bundle validation, production identity, NVS,
protected-test, Compose, endpoint, or output gates.

- [ ] **Step 2: Replace authoritative Task 07 references**

Replace only statements identifying the active temporary local-lab firmware
source. Preserve historical context if a reference explicitly describes a past
session rather than the current authoritative pin.

- [ ] **Step 3: Run GREEN**

Run: `python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py`

Expected: all preflight tests pass, including rejection of the old source and
acceptance of caller-supplied exact lowercase app/root hashes.

### Task 3: Verify And Commit

**Files:**
- Verify all Task 1-2 files.

- [ ] **Step 1: Run the expanded Task 07 focused suite**

Run the physical TFT receipt, preflight, ledger, compose, Task 07 evidence,
physical smoke audit, Course Mode contract, and runtime integration modules.

- [ ] **Step 2: Run compile and integrity gates**

Compile the preflight script; run `git diff --check`; verify the branch is based
on `79b03b08edb3365d8a0349a39879be56abad5a8d`; verify canonical main and the
protected test hash remain unchanged; inspect the diff for unrelated gate drift.

- [ ] **Step 3: Commit**

```bash
git add main/tbot-server/scripts/course_mode_physical_tft_preflight.py \
  main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py \
  main/tbot-server/tests/test_course_mode_physical_tft_preflight.py \
  main/tbot-server/tests/test_course_mode_physical_tft_ledger_validate.py \
  docs/course-mode/task-07-physical-tft-tooling-design.md \
  docs/course-mode/task-07-physical-tft-tooling-implementation-plan.md \
  docs/qa/ad-hoc/2026-08-21-course-mode-v2-task07-physical-hil.md \
  docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md
git commit -m "fix(task07): roll active lab firmware identity"
```
