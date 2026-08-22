# Task 07 Session NVS Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate historical NVS installation provenance from the current session baseline and require phase-appropriate exact NVS preservation evidence.

**Architecture:** Keep firmware/application/bundle identity immutable in `productionCandidateTarget`, move the old NVS digest into hard-pinned `historicalInstallationProvenance`, and accept a caller-supplied current `sessionNvsBaseline` in preflight. The ledger carries an explicit monotonic `sessionNvsPreservation.phase`, binds its before-install value to preflight, and requires after-install/after-restore equality only when the corresponding NVS evidence phase claims that evidence exists.

**Tech Stack:** Python 3, JSON schemas enforced by Python validators, pytest, Markdown runbooks.

---

### Task 1: Establish The Preflight Contract In Failing Tests

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`

- [ ] **Step 1: Change the production fixture and add distinct provenance/session fixtures**

```python
PRODUCTION_CANDIDATE_TARGET = {
    "firmwareSha": "3d4a1e2a32359278124c61e56fd459fac618506e",
    "applicationSha256": "84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e",
    "bundleRootSha256": "9ef3729d0faec7b02d867cedb3ab30d110b845b1c0133738c588bba0e0c16be6",
}
HISTORICAL_INSTALLATION_PROVENANCE = {
    "preservedNvsSha256": "a7a87f72416be20388298cb70cfff306ec78e77f0e8b09231d16113f3d82404e",
}
SESSION_NVS_BASELINE = {"beforeInstallSha256": "0" * 64}
```

Add both new objects to `valid_input`, assert they are emitted, and add mutation cases for missing/extra/malformed/uppercase values and historical provenance drift.

- [ ] **Step 2: Run the dedicated preflight tests and capture RED**

Run: `python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py`

Expected: failures because the implementation still requires the four-field production target and rejects the two new schema fields.

### Task 2: Implement The Preflight Separation

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`
- Test: `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`

- [ ] **Step 1: Define separate exact contracts**

```python
EXPECTED_PRODUCTION_CANDIDATE_TARGET = {
    "firmwareSha": "3d4a1e2a32359278124c61e56fd459fac618506e",
    "applicationSha256": "84c999ece0c90eb6e69a410e335c7791f330e9c0fd39c30dfd4162bb7c4cfc6e",
    "bundleRootSha256": "9ef3729d0faec7b02d867cedb3ab30d110b845b1c0133738c588bba0e0c16be6",
}
EXPECTED_HISTORICAL_INSTALLATION_PROVENANCE = {
    "preservedNvsSha256": "a7a87f72416be20388298cb70cfff306ec78e77f0e8b09231d16113f3d82404e",
}
```

Add required dict fields `historicalInstallationProvenance` and `sessionNvsBaseline`.

- [ ] **Step 2: Validate the current session baseline exactly**

Require `sessionNvsBaseline` to contain only `beforeInstallSha256`, with a lowercase 64-hex string. Do not compare it with historical provenance and do not read or infer it from a device.

- [ ] **Step 3: Bind both objects into the redacted output**

```python
"historicalInstallationProvenance": expected["historicalInstallationProvenance"],
"sessionNvsBaseline": expected["sessionNvsBaseline"],
```

- [ ] **Step 4: Run the preflight tests and capture GREEN**

Run: `python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py`

Expected: all preflight tests pass.

### Task 3: Establish Phase-Aware Ledger Preservation In Failing Tests

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_ledger_validate.py`

- [ ] **Step 1: Add the separated objects to fixtures and preflight artifacts**

Use a session digest different from the historical digest and add:

```python
"historicalInstallationProvenance": deepcopy(HISTORICAL_INSTALLATION_PROVENANCE),
"sessionNvsPreservation": {
    "phase": "POST_RESTORE",
    "beforeInstallSha256": SESSION_NVS_SHA256,
    "afterInstallSha256": SESSION_NVS_SHA256,
    "afterRestoreSha256": SESSION_NVS_SHA256,
},
```

The bound preflight artifact contains `sessionNvsBaseline` with only the before-install digest.

- [ ] **Step 2: Add preservation and phase tests**

Test exact acceptance and field-specific rejection for:

```python
assert validate_ledger(equal_complete_ledger)["valid"] is True
assert "sessionNvsPreservation.equality" in validate_ledger(after_install_mismatch)["reasons"]
assert "sessionNvsPreservation.afterRestoreSha256" in validate_ledger(missing_restore)["reasons"]
assert "bindings.preflight.semantic" in validate_ledger(before_preflight_mismatch)["reasons"]
```

Cover `NOT_OBSERVED` all-null acceptance before preflight,
`PRE_INSTALL_BASELINE` before-only acceptance after successful preflight,
`POST_INSTALL` requiring matching `afterInstallSha256`, and `POST_RESTORE`
requiring matching `afterRestoreSha256`. Require `TFT_PASS` to use
`POST_RESTORE`; reject skipped, missing, or future-phase values.

- [ ] **Step 3: Run the dedicated ledger tests and capture RED**

Run: `python3 -m pytest -q tests/test_course_mode_physical_tft_ledger_validate.py`

Expected: failures because the validator does not recognize provenance/preservation fields or phase-aware equality.

### Task 4: Implement Ledger Equality And Semantic Binding

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_ledger_validate.py`
- Test: `main/tbot-server/tests/test_course_mode_physical_tft_ledger_validate.py`

- [ ] **Step 1: Replace the four-field production target and add exact provenance**

Remove `preservedNvsSha256` from `EXPECTED_PRODUCTION_CANDIDATE_TARGET`, define `EXPECTED_HISTORICAL_INSTALLATION_PROVENANCE`, and require both exact objects independently.

- [ ] **Step 2: Add phase-aware preservation validation**

Validate the exact four-field shape including `phase`. Enforce the monotonic
matrix: `NOT_OBSERVED` has three null hashes; `PRE_INSTALL_BASELINE` has only a
valid before hash; `POST_INSTALL` has equal before/after-install hashes and null
after-restore; `POST_RESTORE` has three valid equal hashes. Successful preflight
evidence excludes `NOT_OBSERVED`, and `TFT_PASS` requires `POST_RESTORE`.

- [ ] **Step 3: Bind the before-install digest to preflight**

Extend expected preflight fields with `historicalInstallationProvenance` and `sessionNvsBaseline`, require exact historical provenance equality, and require:

```python
preflight["sessionNvsBaseline"] == {
    "beforeInstallSha256": document["sessionNvsPreservation"]["beforeInstallSha256"]
}
```

- [ ] **Step 4: Run both dedicated suites and capture GREEN**

Run: `python3 -m pytest -q tests/test_course_mode_physical_tft_preflight.py tests/test_course_mode_physical_tft_ledger_validate.py`

Expected: all dedicated tests pass.

### Task 5: Update Template And Operator Documentation

**Files:**
- Modify: `docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json`
- Modify: `docs/course-mode/task-07-physical-tft-tooling-design.md`
- Modify: `docs/course-mode/task-07-physical-tft-tooling-implementation-plan.md`
- Modify: `docs/qa/ad-hoc/2026-08-21-course-mode-v2-task07-physical-hil.md`
- Modify: `docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md`

- [ ] **Step 1: Update the BLOCKED template**

Keep `task07Verdict: PHYSICAL_BLOCKED`, split historical provenance from the three-field null session preservation object, and do not place the partial observed `063c238a...` value in committed evidence.

- [ ] **Step 2: Clarify evidence semantics in design and runbooks**

Document that historical `a7a87f...` describes the earlier installation only; operators must supply the exact full current pre-install digest; before/after-install/after-restore must be byte-identical; and no tooling change authorizes device mutation or physical execution.

- [ ] **Step 3: Validate the committed BLOCKED template**

Run: `python3 scripts/course_mode_physical_tft_ledger_validate.py ../../../docs/qa/artifacts/2026-08-22-course-mode-task07/physical-tft-ledger-template.json --repository-root ../../..`

Expected JSON: `valid=true`, `tftVerdict=TFT_BLOCKED`, `task07Verdict=PHYSICAL_BLOCKED`, empty reasons.

### Task 6: Full Focused Verification And Commit

**Files:**
- Verify all files changed by Tasks 1-5.

- [ ] **Step 1: Run the expanded Task 07 regression suite**

Run the physical TFT receipt, preflight, ledger, compose, Task 07 evidence, physical smoke audit, Course Mode contract, and Course Mode runtime integration test modules.

Expected: all tests pass.

- [ ] **Step 2: Run static and repository integrity checks**

Run Python compilation for both scripts, `git diff --check`, `git status --short`, verify canonical main remains `8a76d776...`, and verify the protected test SHA-256 remains `08f77b...`.

- [ ] **Step 3: Review the diff against the design**

Confirm no device, serial, network, Docker-runtime, Farm/T54/T65, production, or external-worktree code was added; firmware identity and `PHYSICAL_BLOCKED` remain strict; and phase rules never require evidence before its physical step.

- [ ] **Step 4: Commit the implementation**

```bash
git add docs main/tbot-server/scripts main/tbot-server/tests
git commit -m "fix(task07): bind session NVS preservation"
```
