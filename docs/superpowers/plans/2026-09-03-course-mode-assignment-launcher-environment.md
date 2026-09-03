# Course Mode Assignment Launcher Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the canonical Course Mode launcher preserve the exact ten assignment variables required by the Python release gate without weakening the `env -i` trust boundary.

**Architecture:** Keep one canonical launcher and one Python gate. Add an explicit shell allowlist at the launcher-to-gate boundary, prove it with a black-box temporary-repository probe, then qualify and review the new committed source before freezing a new candidate and restarting release gates from Quick.

**Tech Stack:** POSIX shell, Python 3.11, pytest, Git, Docker, JSON release evidence.

**Constraint:** Modify the existing main checkout only; do not create another launcher, gate version, implementation worktree, or physical-device action.

---

## File Map

- Modify `tests/test_course_robot_e2e_gates_script.py`: black-box regression for the launcher environment boundary.
- Modify `scripts/course_robot_e2e_gates.sh`: explicitly forward the ten existing assignment variables through `env -i`.
- Generate candidate `.27` and its evidence root only after the source fix is committed and independently approved.

### Task 1: Preserve the Assignment Environment Through the Canonical Launcher

**Files:**
- Modify: `tests/test_course_robot_e2e_gates_script.py`
- Modify: `scripts/course_robot_e2e_gates.sh:72`

- [ ] **Step 1: Write the failing black-box launcher test**

Add this test immediately before `_shell_fixture`:

```python
def test_canonical_gate_forwards_exact_assignment_environment(tmp_path: Path) -> None:
    fixture = _shell_fixture(tmp_path)
    probe = fixture / "main/tbot-server/scripts/course_mode_release_gate.py"
    allowed = (
        "LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME",
        "LESSON_STUDIO_E2E_RESOURCE_PREFIX",
        "TASK4_ASSIGNMENT_RUNTIME_ROOT",
        "JWT_PUBLIC_KEY",
        "TBOT_DEVICE_MINT_SECRET",
        "LESSON_ASSET_ORIGIN_BASE",
        "ROBOT_ESP_BASE_URL",
        "LESSON_STUDIO_E2E_BACKEND_HOST_PORT",
        "LESSON_STUDIO_E2E_WEB_HOST_PORT",
        "TASK4_ASSIGNMENT_MEDIA_HOST_PORT",
    )
    rejected = (
        "LESSON_STUDIO_E2E_UNRELATED_SECRET",
        "TASK4_ASSIGNMENT_DEBUG_TOKEN",
        "TBOT_DEVICE_MINT_SECRET_BACKUP",
    )
    probe.write_text(
        "import json, os\n"
        f"keys = {allowed + rejected!r}\n"
        "print(json.dumps({key: os.environ[key] for key in keys if key in os.environ}, sort_keys=True))\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=fixture, check=True)
    subprocess.run(
        ["git", "commit", "-m", "assignment environment probe"],
        cwd=fixture,
        check=True,
        capture_output=True,
    )
    source = {
        **os.environ,
        **{name: f"value-for-{name.lower()}" for name in allowed},
        **{name: f"must-not-forward-{name.lower()}" for name in rejected},
    }

    result = subprocess.run(
        [str(fixture / "scripts/course_robot_e2e_gates.sh"), "--candidate", "unused.json"],
        cwd=fixture,
        env=source,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {name: source[name] for name in allowed}
```

- [ ] **Step 2: Run the new test and verify RED**

Run:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q tests/test_course_robot_e2e_gates_script.py \
  -k forwards_exact_assignment_environment
```

Expected: one assertion failure showing the ten canonical variables are absent from the probe output. The test must not fail from fixture setup or Git bootstrap verification.

- [ ] **Step 3: Add the minimal explicit allowlist**

Insert these assignments into the existing `exec /usr/bin/env -i` invocation after `COURSE_MODE_ADMIN_E2E_READY` and before the Python command:

```sh
  LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME="${LESSON_STUDIO_E2E_COMPOSE_PROJECT_NAME-}" \
  LESSON_STUDIO_E2E_RESOURCE_PREFIX="${LESSON_STUDIO_E2E_RESOURCE_PREFIX-}" \
  TASK4_ASSIGNMENT_RUNTIME_ROOT="${TASK4_ASSIGNMENT_RUNTIME_ROOT-}" \
  JWT_PUBLIC_KEY="${JWT_PUBLIC_KEY-}" \
  TBOT_DEVICE_MINT_SECRET="${TBOT_DEVICE_MINT_SECRET-}" \
  LESSON_ASSET_ORIGIN_BASE="${LESSON_ASSET_ORIGIN_BASE-}" \
  ROBOT_ESP_BASE_URL="${ROBOT_ESP_BASE_URL-}" \
  LESSON_STUDIO_E2E_BACKEND_HOST_PORT="${LESSON_STUDIO_E2E_BACKEND_HOST_PORT-}" \
  LESSON_STUDIO_E2E_WEB_HOST_PORT="${LESSON_STUDIO_E2E_WEB_HOST_PORT-}" \
  TASK4_ASSIGNMENT_MEDIA_HOST_PORT="${TASK4_ASSIGNMENT_MEDIA_HOST_PORT-}" \
```

Do not forward by prefix, use `env`, or add validation to the shell script.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Step 2 command again.

Expected: `1 passed`; the probe output contains all ten allowed values unchanged and none of the three rejected values.

- [ ] **Step 5: Run the complete launcher regression file**

Run:

```bash
/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11 \
  -m pytest -q tests/test_course_robot_e2e_gates_script.py
```

Expected: every test passes with no skips or warnings caused by the change.

- [ ] **Step 6: Commit the implementation**

```bash
git diff --check
git add scripts/course_robot_e2e_gates.sh tests/test_course_robot_e2e_gates_script.py
git commit -m "fix: forward assignment environment in course gate"
```

Expected: one implementation commit touching only the launcher and its test.

### Task 2: Qualify and Independently Review the Committed Fix

**Files:**
- Verify: `scripts/course_robot_e2e_gates.sh`
- Verify: `tests/test_course_robot_e2e_gates_script.py`
- Verify: `main/tbot-server/scripts/course_mode_release_gate.py`

- [ ] **Step 1: Verify the committed launcher is clean and executable**

```bash
git status --short
git diff HEAD^ --check
test -x scripts/course_robot_e2e_gates.sh
scripts/course_robot_e2e_gates.sh --list-lanes >/private/tmp/course-mode-assignment-launcher-lanes.json
/usr/bin/jq -e '.full | index("admin-course-mode-assignment-new") != null and index("admin-course-mode-assignment-rollback") != null' \
  /private/tmp/course-mode-assignment-launcher-lanes.json
```

Expected: Git status is empty, the script is executable, and both stateful assignment lanes remain in canonical Full inventory.

- [ ] **Step 2: Run release-gate and candidate-manifest regression suites**

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q \
  tests/test_course_robot_e2e_gates_script.py \
  main/tbot-server/tests/test_course_mode_candidate_manifest.py \
  main/tbot-server/tests/test_course_mode_release_gate.py \
  main/tbot-server/tests/test_course_mode_operator_attestation.py
```

Expected: all tests pass and the assignment tests are not skipped.

- [ ] **Step 3: Run the canonical Python qualification set**

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
"$PY311" -m pytest -q
```

Expected: the canonical repository test total passes with zero failures. Record the exact pass count rather than assuming the previous `636/636` total is unchanged.

- [ ] **Step 4: Dispatch spec-compliance review**

Provide the approved design, implementation commit SHA, and exact diff to an independent reviewer. Require confirmation that:

1. Exactly the ten variables in the design are forwarded.
2. `env -i` remains in place.
3. No prefix-based or ambient-environment forwarding exists.
4. The black-box test proves both preservation and filtering.

Every finding must be fixed by the implementer and re-reviewed before Step 5.

- [ ] **Step 5: Dispatch code-quality and security-boundary review**

Provide the spec-approved commit after spec review. Require checks for POSIX-shell quoting, empty-value behavior, accidental secret output, test brittleness, and unintended launcher changes. Every finding must be fixed and re-reviewed.

### Task 3: Freeze the Successor Candidate and Restart Canonical Gates

**Files:**
- Generate: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.27.json`
- Generate: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.27/00-candidate-validator.json`
- Generate: `/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.27/00-operator-attestation.json`
- Generate: Quick, firmware-facing, Full, and later live-db reports beneath the `.27` evidence root.

- [ ] **Step 1: Verify exact source identities and clean trees**

```bash
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server status --short
git -C /Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342 status --short
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock status --short
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server rev-parse HEAD
git -C /Users/manhhodinh/Documents/TBOT-candidate-worktrees/backend-c2a4e342 rev-parse HEAD
git -C /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/course-mode-ed76-portable-lock rev-parse HEAD
```

Expected: every status is empty; record all three exact SHAs. The admin SHA must include the launcher fix and both review loops.

- [ ] **Step 2: Build and inspect the committed web image**

Use the existing reviewed Course Mode image build workflow against the clean admin commit. Inspect the result with the trusted Docker binary and require:

```text
org.opencontainers.image.revision=<exact new admin SHA>
org.opencontainers.image.source=https://github.com/JSR-Algo/ESP-Server.git
com.tbot.course-mode.build-source=reviewed-clean-git-worktree
```

Record the immutable image ID. Do not reuse `.26`'s web image because its revision is `0ffd10ab`.

- [ ] **Step 3: Freeze candidate `.27` from `.26`**

Copy the `.26` candidate document, then update only values proven to have changed:

- candidate ID and evidence root;
- actual UTC creation/expiry timestamps;
- admin repository SHA;
- web image reference, immutable ID, revision, and matching provenance;
- any candidate-bound source/tree digest that the validator proves changed because of the committed launcher and test.

Keep backend SHA `bb6c484e69b71759462b5915138a262d82068b84`, firmware SHA `b54c6ca33e9beb3747b44feceb7c64fea33fe1d6`, and unchanged reviewed tool descriptors unless direct validation proves drift.

- [ ] **Step 4: Validate and attest `.27`**

```bash
PY311=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/python-test-runtime-standalone-v2/bin/python3.11
CANDIDATE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/candidates/course-mode-2026-08-31.27.json
EVIDENCE=/Users/manhhodinh/Documents/TBOT/task-artifacts/course-mode-production-readiness/course-mode-2026-08-31.27
mkdir -p "$EVIDENCE"
"$PY311" main/tbot-server/scripts/course_mode_candidate_manifest.py "$CANDIDATE" > "$EVIDENCE/00-candidate-validator.json"
/usr/bin/jq -e '.status == "pass" and .reasons == []' "$EVIDENCE/00-candidate-validator.json"
"$PY311" main/tbot-server/scripts/course_mode_operator_attestation.py \
  --candidate "$CANDIDATE" \
  --output "$EVIDENCE/00-operator-attestation.json" \
  --confirm-trusted-operator-account \
  --confirm-untrusted-automation-stopped
```

Expected: validator PASS, immutable attestation created, and neither file contains secrets.

- [ ] **Step 5: Run gates only through the canonical launcher**

Export the isolated `.27` assignment ports/project/runtime values and the two secrets without printing them. Run Quick and Full release evidence only through the canonical launcher:

```bash
scripts/course_robot_e2e_gates.sh --candidate "$CANDIDATE" --mode quick --report "$EVIDENCE/01-quick-gate.json"
scripts/course_robot_e2e_gates.sh --candidate "$CANDIDATE" --mode full --report "$EVIDENCE/03-full-gate.json"
```

After Quick PASS and before Full, use the existing candidate-bound custom-lane API to run `firmware-renderer`, `firmware-handler`, `firmware-backward-compatibility`, and `cross-contract-parity` in that order, writing `02-firmware-facing.json`. This focused diagnostic is not a replacement for a canonical launcher mode or for Full.

Stop on the first non-PASS verdict and preserve its immutable report. Never substitute a direct Python invocation for Quick or Full canonical release evidence.

- [ ] **Step 6: Continue post-Full qualification only after Full PASS**

Verify all 20 Full lanes appear in canonical order, assignment resources are cleaned, and no `.27` test ports remain bound. Then run the isolated PostgreSQL 16 live-db gate with two distinct loopback databases, perform the software evidence audit, and obtain two final independent reviews.

Only after every software gate and review passes may the state become `SOFTWARE_GO_FOR_ATTENDED_FLASH`. Obtain a fresh point-of-use user confirmation before any flash, serial/HIL access, robot reset, reboot, power-cycle, or motion.
