# Course Mode Operator Attestation Time Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject operator attestations that predate the candidate, claim a future confirmation time, or are bound at or after candidate expiry.

**Architecture:** Preserve the existing attestation schema and generator. Extend the canonical release gate binding with strict UTC comparisons using the manifest parser, while allowing a fixed aware UTC `now` only for deterministic tests.

**Tech Stack:** Python 3.11, pytest, Ruff, Git

---

### Task 1: Specify The Temporal Boundary

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Test: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Make the fixture timestamp candidate-relative**

Update `_operator_attestation_payload()` so its default `createdAt` is the
candidate's `createdAt`. This removes the existing deliberately future-dated
fixture while preserving every other exact binding field.

```python
"createdAt": candidate["createdAt"],
```

- [ ] **Step 2: Add failing boundary tests**

Add focused tests that call `_operator_attestation_binding(..., now=NOW)` and
prove these cases:

```python
@pytest.mark.parametrize("created_at", [
    "2026-08-30T23:59:59Z",
    "2026-09-08T00:00:01Z",
    "2026-09-15T00:00:01Z",
])
def test_operator_attestation_rejects_time_outside_candidate_or_now(...):
    ...
    assert gate._operator_attestation_binding(candidate, source, now=NOW) is None


@pytest.mark.parametrize("created_at", [
    "2026-08-31T00:00:00Z",
    "2026-09-08T00:00:00Z",
])
def test_operator_attestation_accepts_candidate_and_current_time_boundaries(...):
    ...
    assert gate._operator_attestation_binding(candidate, source, now=NOW) is not None
```

Use the candidate fixture's actual `createdAt` and `expiresAt`; do not weaken
the fixture or mock the timestamp parser.

Add an isolated expiry-boundary test proving an attestation at the current time
is accepted one second before candidate expiry, while a binding attempt exactly
at candidate expiry is rejected.

- [ ] **Step 3: Verify RED**

Run:

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'operator_attestation and (outside_candidate_or_now or current_time_boundaries)' -x
```

Expected: the out-of-window cases FAIL because the current binding accepts any
well-formed UTC timestamp.

### Task 2: Implement Fail-Closed Time Binding

**Files:**
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`
- Test: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Add the deterministic validation-time parameter**

Extend the private helper without changing callers:

```python
def _operator_attestation_binding(
    candidate: dict,
    source: Mapping[str, str],
    *,
    now: datetime | None = None,
) -> OperatorAttestationBinding | None:
```

- [ ] **Step 2: Snapshot and validate UTC time**

At the start of the binding attempt, use one aware UTC value and fail closed on
invalid injected values:

```python
_DATETIME_TYPE = datetime

try:
    validation_now = now if now is not None else datetime.now(timezone.utc)
    if (
        not isinstance(validation_now, _DATETIME_TYPE)
        or validation_now.tzinfo is None
        or validation_now.utcoffset() != timedelta(0)
    ):
        return None
    validation_now = _DATETIME_TYPE(
        validation_now.year,
        validation_now.month,
        validation_now.day,
        validation_now.hour,
        validation_now.minute,
        validation_now.second,
        validation_now.microsecond,
        tzinfo=timezone.utc,
        fold=validation_now.fold,
    )
except Exception:
    return None
```

Capture the built-in datetime type before tests replace the module clock. Guard
clock acquisition and normalization so spoofed types and hostile timezone
implementations fail closed, then use only the stable UTC copy in comparisons.

- [ ] **Step 3: Enforce the exact temporal contract**

Parse all three values with `_manifest._parse_rfc3339_utc` and reject unless:

```python
candidate_created <= attestation_created <= validation_now < candidate_expires
```

Candidate expiry remains exclusive, consistent with candidate validation. Do
not add a TTL or change the attestation schema/generator.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'operator_attestation or operator_precondition'
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_operator_attestation.py
```

Expected: all selected tests PASS.

- [ ] **Step 5: Run static checks and commit**

```bash
uvx ruff check \
  main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
"$PY311" -I -s -m py_compile \
  main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git diff --check
git add \
  main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): bind operator attestation time"
```

### Task 3: Review And Requalify

**Files:**
- Verify: `main/tbot-server/scripts/course_mode_release_gate.py`
- Verify: `main/tbot-server/tests/test_course_mode_release_gate.py`

- [ ] **Step 1: Run independent reviews**

Require spec review for inclusive creation/current boundaries, exclusive
candidate expiry, strict UTC, schema preservation, and no TTL. Require quality review for deterministic time use,
fail-closed malformed inputs, repeated binding checks, and regression coverage.
Fix every finding and repeat until both approve.

- [ ] **Step 2: Run the complete source gates**

```bash
"$PY311" -I -s -m pytest -q \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_software_evidence_audit.py \
  main/tbot-server/tests/test_course_mode_evidence_sanitize.py \
  main/tbot-server/tests/test_course_mode_operator_attestation.py \
  tests/test_course_robot_e2e_gates_script.py
node --test main/manager-web/scripts/task4-assignment-fixture.test.cjs
node --test \
  main/manager-web/scripts/lesson-studio-compose.test.cjs \
  main/manager-web/scripts/lesson-studio-e2e-environment.test.cjs \
  main/manager-web/scripts/page-errors-helper.test.cjs \
  main/manager-web/scripts/reset-lesson-studio-e2e-state.test.cjs \
  main/manager-web/scripts/task4-assignment-fixture.test.cjs
```

Expected: every collected test passes with no skips or expected failures.

- [ ] **Step 3: Require a clean reviewed HEAD**

```bash
git status --short
git rev-parse HEAD
git diff --check HEAD~2 HEAD
```

Expected: clean worktree and no whitespace errors. Rebuild the exact
`linux/arm64` web image from this new reviewed HEAD before creating candidate
`.40`; the image built from `037b14dc` is cache evidence only.
