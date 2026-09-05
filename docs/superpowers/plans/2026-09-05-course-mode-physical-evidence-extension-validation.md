# Course Mode Physical Evidence Extension Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore fail-closed physical receipt and G0-G10 evidence validation without relaxing the canonical Course Mode candidate manifest schema.

**Architecture:** Keep `validate_candidate()` as the sole validator for the canonical manifest. Add a narrow physical-candidate adapter that permits exactly `physicalEvidence`, validates a copied projection with the canonical validator, and leaves signed evidence validation to the existing receipt verifier. Reuse the adapter from the legacy evidence auditor and update its shared physical candidate fixture to the current schema.

**Tech Stack:** Python 3.11, pytest, Ed25519 test fixtures, Git, Ruff

---

### Task 1: Specify The Physical Candidate Extension Contract

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py`
- Test: `main/tbot-server/tests/test_course_mode_candidate_manifest.py`

- [ ] **Step 1: Preserve the current integration failure as RED evidence**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_evidence_audit.py::test_gate_specific_candidate_bound_evidence_passes
```

Expected: FAIL because the physical candidate cannot satisfy both the exact canonical `tools` schema and the required `physicalEvidence` extension.

- [ ] **Step 2: Add focused extension-boundary tests**

Add tests that import `_validate_physical_candidate` and prove the adapter is narrow and non-mutating:

```python
def test_physical_candidate_adapter_accepts_only_signed_extension(candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import _validate_physical_candidate

    before = deepcopy(candidate)
    assert _validate_physical_candidate(candidate, now=NOW) == []
    assert candidate == before


def test_physical_candidate_adapter_rejects_unknown_tool_extension(candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import _validate_physical_candidate

    candidate["tools"]["unexpected"] = {}
    assert "tools.keys" in _validate_physical_candidate(candidate, now=NOW)


def test_physical_candidate_adapter_rejects_missing_canonical_tool(candidate: dict) -> None:
    from course_mode_physical_tft_receipt_verify import _validate_physical_candidate

    candidate["tools"].pop("docker")
    assert "tools.keys" in _validate_physical_candidate(candidate, now=NOW)
```

Use the module's fixed UTC test time so the tests do not depend on wall clock.

- [ ] **Step 3: Keep the canonical validator strict**

Add or retain a direct assertion showing an extended physical candidate is not a valid ordinary release manifest:

```python
assert "tools.keys" in validate_candidate(candidate, now=NOW)
```

- [ ] **Step 4: Run the focused tests to verify RED**

Run:

```bash
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py -x
```

Expected: FAIL because `_validate_physical_candidate` does not exist or the shared fixture still lacks current canonical descriptors.

### Task 2: Implement The Narrow Physical Candidate Adapter

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py`
- Modify: `main/tbot-server/scripts/course_mode_evidence_audit.py`
- Test: `main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py`
- Test: `main/tbot-server/tests/test_course_mode_evidence_audit.py`

- [ ] **Step 1: Import the canonical tool key contract**

Extend the existing manifest import:

```python
from course_mode_candidate_manifest import TOOLS_KEYS, validate_candidate
```

- [ ] **Step 2: Implement a non-mutating fail-closed adapter**

Add a private helper near `_physical_identity`:

```python
def _validate_physical_candidate(
    candidate: object, *, now: datetime | None = None,
) -> list[str]:
    if not isinstance(candidate, dict):
        return ["candidate.type"]
    tools = candidate.get("tools")
    if not isinstance(tools, dict):
        return validate_candidate(candidate, now=now)
    if set(tools) != TOOLS_KEYS | {"physicalEvidence"}:
        reasons = set(validate_candidate(candidate, now=now))
        reasons.add("tools.keys")
        return sorted(reasons)
    projected = dict(candidate)
    projected["tools"] = {
        key: value for key, value in tools.items() if key != "physicalEvidence"
    }
    return validate_candidate(projected, now=now)
```

The implementation may use `copy.deepcopy` if tests show nested mutation risk, but it must not mutate the input and must not duplicate canonical validation.

- [ ] **Step 3: Route physical consumers through the adapter**

In `validate_receipt()`, replace the direct call to `validate_candidate()` with `_validate_physical_candidate()` while preserving sorted, privacy-safe reason aggregation.

In `course_mode_evidence_audit.py`, import and call the same adapter because its anchors require `_physical_identity(candidate)`. Do not add another schema projection implementation.

- [ ] **Step 4: Verify adapter boundary behavior**

Run:

```bash
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py
```

Expected after the fixture task below is complete: all tests PASS, including unknown-extension and missing-descriptor rejection.

### Task 3: Upgrade The Shared Signed Physical Candidate Fixture

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py`

- [ ] **Step 1: Reuse the current candidate descriptor shape**

Bring the fixture in line with `test_course_mode_candidate_manifest.py::candidate`: create secure executable, Node/npm/npx, node-install tree, Playwright browser, Python runtime, ESP-IDF, firmware evidence manifest, Docker/Compose, two-image, and migration descriptors using temporary paths and deterministic bytes. Continue monkeypatching only external trust checks needed by the isolated fixture.

The fixture must have exactly these base tool keys before adding the extension:

```python
{
    "docker", "dockerCompose", "nodeInstalls", "playwrightBrowsers",
    "robotPreviewBrowser", "node", "pythonTestRuntime", "espIdf",
}
```

- [ ] **Step 2: Update current candidate sections**

Use exact current shapes:

```python
"images": {
    "lessonStudioBackend": {"reference": backend_ref, "id": backend_id},
    "lessonStudioWeb": {"reference": web_ref, "id": web_id},
},
"firmware": {
    "appPath": str(app), "appOffset": "0x20000", "appBytes": app.stat().st_size,
    "appSha256": sha256(app), "elfSha256": sha256(elf),
    "partitionBytes": 1024, "freeBytes": 1024 - app.stat().st_size,
    "evidenceManifestPath": str(evidence_manifest),
    "evidenceManifestSha256": sha256(evidence_manifest),
},
"database": {
    "engineImage": "postgres:16-alpine", "engineImageId": postgres_id,
    "migrationHead": migration.name, "migrationHeadSha256": sha256(migration),
},
```

- [ ] **Step 3: Re-sign the exact current candidate binding**

Ensure `physical_identity["candidateBinding"]` contains the current full `images`, `firmware`, and `database` values expected by `_physical_identity()`. Write canonical JSON bytes, sign them with the fixture Ed25519 key, update both hash-bound dirty exceptions, and only then attach the `physicalEvidence` extension to `tools`.

- [ ] **Step 4: Verify receipt and evidence suites GREEN**

Run:

```bash
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py
```

Expected: both complete suites PASS without deselection or expected failure.

- [ ] **Step 5: Commit the contract repair**

```bash
git add \
  main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/scripts/course_mode_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py
git commit -m "fix(course-mode): validate signed physical candidate extension"
```

### Task 4: Review And Requalify The Source

**Files:**
- Verify: all files changed by Tasks 1-3

- [ ] **Step 1: Run formatting and syntax checks**

Run:

```bash
uvx ruff check \
  main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/scripts/course_mode_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py
"$PY311" -I -s -m py_compile \
  main/tbot-server/scripts/course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/scripts/course_mode_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_physical_tft_receipt_verify.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py
git diff --check HEAD^ HEAD
```

Expected: all commands exit 0.

- [ ] **Step 2: Run independent spec and quality reviews**

The spec reviewer verifies exact extension cardinality, canonical validator reuse, no input mutation, and no manifest relaxation. The quality reviewer checks fail-closed malformed input behavior, deterministic reasons, signature binding, fixture fidelity, and missing regression coverage. Fix every finding and repeat both reviews until approved.

- [ ] **Step 3: Run the complete Python source gate**

Run:

```bash
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_software_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_evidence_sanitize.py \
  main/tbot-server/tests/test_course_mode_operator_attestation.py \
  tests/test_course_robot_e2e_gates_script.py
```

Expected: all collected tests PASS. If the repair adds tests, the final count
will be greater than 796 and the report must use the actual collected count.

- [ ] **Step 4: Run Node source contracts**

Run:

```bash
node --test main/manager-web/scripts/task4-assignment-fixture.test.cjs
node --test \
  main/manager-web/scripts/lesson-studio-compose.test.cjs \
  main/manager-web/scripts/lesson-studio-e2e-environment.test.cjs \
  main/manager-web/scripts/page-errors-helper.test.cjs \
  main/manager-web/scripts/reset-lesson-studio-e2e-state.test.cjs \
  main/manager-web/scripts/task4-assignment-fixture.test.cjs
```

Expected: assignment fixture `38/38 PASS`; combined current contract suite `66/66 PASS` unless new tests intentionally change the count.

- [ ] **Step 5: Require a clean reviewed HEAD before candidate rebuild**

Run:

```bash
git status --short
git rev-parse HEAD
git diff --check HEAD~2 HEAD
```

Expected: clean worktree, committed reviewed SHA, and no whitespace errors. Only then rebuild the `linux/arm64` web image and assemble candidate `.40`.
