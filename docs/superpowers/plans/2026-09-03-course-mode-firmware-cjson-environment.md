# Course Mode Firmware cJSON Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind the firmware handler's cJSON include/source directory to the candidate-verified ESP-IDF checkout without exposing the real home directory.

**Architecture:** The Python release gate derives one lane-specific `CJSON_DIR` from `tools.espIdf.root` and validates the named filesystem objects before command execution. Firmware source remains immutable; invalid or operator-controlled paths fail closed.

**Tech Stack:** Python 3.11, pytest, POSIX filesystem metadata, Bash host-native firmware tests.

---

### Task 1: Inject Candidate-Bound cJSON Directory

**Files:**
- Modify: `main/tbot-server/tests/test_course_mode_release_gate.py`
- Modify: `main/tbot-server/scripts/course_mode_release_gate.py`

- [ ] **Step 1: Add failing environment tests**

Add tests for the `firmware-handler` lane asserting that `_child_environment`
sets `CJSON_DIR` to `<candidate tools.espIdf.root>/components/json/cJSON`, ignores
a hostile source `CJSON_DIR`, and keeps the variable absent from another
firmware lane. Add fail-closed cases for a missing `cJSON.c`, symlinked cJSON
directory or file, non-regular file, relative root, and noncanonical root.

- [ ] **Step 2: Verify RED**

Run:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q main/tbot-server/tests/test_course_mode_release_gate.py \
  -k 'firmware_handler_cjson or non_handler_never_receives_cjson'
```

Expected: FAIL because the gate does not currently derive or inject
`CJSON_DIR`.

- [ ] **Step 3: Implement the narrow validator**

Add a helper equivalent to:

```python
def _firmware_handler_cjson_dir(candidate: dict, lane: Lane) -> Path | None:
    if lane.name != "firmware-handler":
        return None
    root_value = candidate["tools"]["espIdf"]["root"]
    root = Path(root_value)
    if not root.is_absolute() or root.is_symlink():
        return None
    canonical_root = root.resolve(strict=True)
    if root != canonical_root or not canonical_root.is_dir():
        return None
    cjson = canonical_root / "components/json/cJSON"
    canonical_cjson = cjson.resolve(strict=True)
    source = canonical_cjson / "cJSON.c"
    if cjson != canonical_cjson or cjson.is_symlink() or not canonical_cjson.is_dir():
        return None
    metadata = source.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return None
    return canonical_cjson
```

Catch only expected key/path/type/filesystem errors. In `_child_environment`,
for `firmware-handler`, require the helper result and set `CJSON_DIR` from it.
Do not copy `CJSON_DIR` from `source`; do not change `HOME` or any firmware file.

- [ ] **Step 4: Add a command-level regression**

Use a temporary candidate ESP-IDF fixture containing `cJSON.c` and a temporary
firmware handler command that asserts `HOME == /nonexistent`, reads the exact
candidate-derived `CJSON_DIR`, and exits zero. Run it through the real gate lane
environment without physical interfaces.

- [ ] **Step 5: Verify GREEN and qualify source**

Run the focused tests, the assignment/capsule/process selector, the full
canonical Python suite, combined Node source suites, `py_compile`, Node syntax
checks, `git diff --check`, `git status --short`, and `git fsck --no-progress`.
Expected: all tests exit zero with no skips/failures and the worktree is clean
after commit.

- [ ] **Step 6: Commit and review**

```bash
git add main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_release_gate.py
git commit -m "fix(course-mode): bind firmware handler cjson"
```

Obtain an independent spec review followed by an independent quality/security
review. Resolve every finding test-first and repeat both reviews.

### Task 2: Freeze Candidate `.24` And Resume Qualification

**Files:**
- Preserve: candidate `.23` and all `.23` reports/diagnostics as failure evidence
- Create: candidate `.24`, validator, attestation, Quick, Full, and live-db evidence

- [ ] **Step 1: Freeze `.24`**

Build the exact reviewed admin HEAD using the already approved
`WEB_NODE_IMAGE=node:20` and existing required build arguments/labels. Copy
`.23`, changing only candidate identity/timestamps/evidence root, reviewed admin
SHA, and exact web image reference/ID. Preserve all backend, firmware, database,
curriculum, and tool identities. Validate, attest, and lock artifacts to `0444`.

- [ ] **Step 2: Resume sequential gates**

Run `.24` Quick, then the four firmware-facing host-native lanes, then final
Full and isolated PostgreSQL live-db. The previously successful `.23` isolated
NEW-to-ROLLBACK result remains diagnostic evidence, but `.24` Full must execute
the real assignment NEW and ROLLBACK lanes again. Stop on the first failure.

- [ ] **Step 3: Audit and verdict**

Run the software-only evidence audit and two independent final reviews. Only
then may the result be `SOFTWARE_GO_FOR_ATTENDED_FLASH`; physical actions still
require a fresh point-of-use confirmation.
