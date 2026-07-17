# HIL Partial Eviction Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Task 14 HIL orchestrator recover the two expected partial-eviction states through the production eviction tool, preserve exact failure evidence, and publish independently validated matrix artifacts without weakening firmware cleanup.

**Architecture:** Keep firmware and its fail-closed preservation fixture unchanged. Add pure recovery validators to the Python orchestrator, invoke recovery only after scenario inspection proves an expected empty primary leaf, bind a uniform recovery artifact through the fault driver/build identity/release ledger, and write failures to a separate atomic quarantine root.

**Tech Stack:** Python 3.9, pytest, ESP manager MCP transport, JSON/SHA-256 evidence bundles, Docker Compose, real ESP32-S3 HIL.

---

## File And Component Map

- Modify `main/tbot-server/scripts/lesson_studio_task14_hil_storage.py`: recovery decision/validation, live execution, uniform recovery artifact, and failure quarantine writer.
- Modify `main/tbot-server/scripts/lesson_studio_task14_fault_driver.py`: independent exact-layout and recovery semantic validation.
- Modify `main/tbot-server/scripts/lesson_studio_task14_build_identity.py`: matrix and release artifact schema binding.
- Modify `main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py`: orchestrator RED/GREEN tests.
- Modify `main/tbot-server/tests/test_lesson_studio_task14_evidence.py`: fault-driver, failure-bundle, build-identity, and release tests.
- Modify `main/tbot-server/docs/lesson-studio-task14-live-matrix.md`: required failure evidence root and attended commands.
- Preserve `main/tbot-server/data/manager-web/output/` and all existing failed hardware evidence.

### Task 1: Add Pure Recovery Decision And Response Validation

**Files:**
- Modify: `main/tbot-server/scripts/lesson_studio_task14_hil_storage.py`
- Test: `main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py`

- [ ] **Step 1: Write RED tests for the exact recovery contract**

Add table-driven tests that require recovery only for:

```python
PARTIAL_EVICTION_SCENARIOS = {
    "evict-after-unlinks-fail",
    "evict-before-rmdir-fail",
}
```

The accepted retry response must be exactly:

```python
{
    "cacheKey": cache_key,
    "status": "evicted",
    "reason": "evicted",
    "evicted": True,
    "notFound": False,
    "fileCount": 0,
}
```

Add negative cases for wrong/extra fields, wrong cache key, nonzero count,
`notFound=true`, `evicted=false`, and any status/reason other than `evicted`.

- [ ] **Step 2: Run RED tests**

Run:

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/esp32-server-hil-task7-auth-order/main/tbot-server
python3 -m pytest -q tests/test_lesson_studio_task14_hil_storage.py -k 'recovery_contract or recovery_scenario'
```

Expected: FAIL because the recovery helpers and constants do not exist.

- [ ] **Step 3: Implement minimal pure helpers**

Add helpers with fixed return schemas:

```python
PARTIAL_EVICTION_SCENARIOS = frozenset({
    "evict-after-unlinks-fail",
    "evict-before-rmdir-fail",
})

def recovery_not_attempted():
    return {
        "attempted": False,
        "operation": None,
        "reason": None,
        "response": None,
        "inspection": None,
    }

def validate_partial_eviction_retry(value, cache_key):
    value = _exact_fields(value, EVICT_RESPONSE_FIELDS, "recovery eviction")
    require(value == {
        "cacheKey": cache_key,
        "status": "evicted",
        "reason": "evicted",
        "evicted": True,
        "notFound": False,
        "fileCount": 0,
    }, "partial eviction recovery did not converge")
    return value
```

- [ ] **Step 4: Run GREEN tests and commit**

Run the focused command from Step 2, then:

```bash
git diff --check
git add main/tbot-server/scripts/lesson_studio_task14_hil_storage.py \
  main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py
git commit -m "test(e2e): define partial eviction recovery contract"
```

### Task 2: Recover Only After Exact Partial-State Inspection

**Files:**
- Modify: `main/tbot-server/scripts/lesson_studio_task14_hil_storage.py`
- Test: `main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py`

- [ ] **Step 1: Write RED orchestration tests**

Use a fake client and serial monitor to prove:

- Recovery runs once after `inspect-after` for both directory-only scenarios.
- Recovery never runs for the other seven scenarios.
- The initial trigger response remains the injected-failure response.
- Timeline order is exactly:

```text
status-before
inspect-before
stage
arm
trigger
status-after
inspect-after
recovery-trigger
recovery-inspect
cleanup
```

- Recovery inspection requires primary missing, sibling sentinel unchanged, and
  protected entries byte-identical.
- A retry/inspection failure prevents cleanup and scenario PASS publication.

- [ ] **Step 2: Run RED tests**

```bash
python3 -m pytest -q tests/test_lesson_studio_task14_hil_storage.py -k 'run_scenario and recovery'
```

Expected: FAIL because `run_scenario` goes directly from post-fault inspection
to fixture cleanup.

- [ ] **Step 3: Implement the recovery step**

After `validate_preservation_inspections(...)` and before cleanup:

```python
recovery = recovery_not_attempted()
if scenario in PARTIAL_EVICTION_SCENARIOS:
    retry_raw = client.transport.call(
        TRIGGER_TOOLS["evict"], {"cacheKey": cache_key}, 75
    )
    retry = validate_partial_eviction_retry(retry_raw, cache_key)
    events.append("recovery-trigger")
    recovered_inspection = client.inspect(cache_key, sibling)
    validate_recovered_preservation_inspection(
        inspect_before, inspect_after, recovered_inspection
    )
    events.append("recovery-inspect")
    recovery = {
        "attempted": True,
        "operation": "evict",
        "reason": "expected_partial_eviction",
        "response": retry,
        "inspection": recovered_inspection,
    }
```

Add `recovery` to `result.json` and preserve the original `trigger` object.

- [ ] **Step 4: Run GREEN tests and commit**

```bash
python3 -m pytest -q tests/test_lesson_studio_task14_hil_storage.py -k 'recovery or scenario_outcomes'
git diff --check
git add main/tbot-server/scripts/lesson_studio_task14_hil_storage.py \
  main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py
git commit -m "fix(e2e): recover expected partial evictions"
```

### Task 3: Bind Recovery Into Exact Evidence And The Independent Fault Driver

**Files:**
- Modify: `main/tbot-server/scripts/lesson_studio_task14_hil_storage.py`
- Modify: `main/tbot-server/scripts/lesson_studio_task14_fault_driver.py`
- Test: `main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py`
- Test: `main/tbot-server/tests/test_lesson_studio_task14_evidence.py`

- [ ] **Step 1: Write RED exact-layout and semantic tests**

Require `recovery-response.json` in both ordinary and power-loss artifact sets.
Tests must reject:

- Missing or extra recovery artifact.
- Recovery artifact not exactly equal to `result.json.recovery`.
- Attempted recovery on a non-partial scenario.
- Missing conditional recovery timeline events.
- Attempted recovery with null response/inspection.
- Non-attempted recovery with non-null fields.
- Recovery response with false-success fields.

- [ ] **Step 2: Run RED tests**

```bash
python3 -m pytest -q \
  tests/test_lesson_studio_task14_hil_storage.py \
  tests/test_lesson_studio_task14_evidence.py \
  -k 'recovery_response or artifact_layout or hil_storage_scenario'
```

Expected: FAIL because the artifact constants and fault driver do not know the
new file or semantics.

- [ ] **Step 3: Extend all exact artifact owners**

Add `recovery-response.json` to:

```python
ORDINARY_ARTIFACTS
HIL_ORDINARY_REQUIRED
HIL_ORDINARY_ARTIFACTS
```

Power-loss sets inherit it. In the fault driver, parse the file as an exact
object, compare it to `result["recovery"]`, validate scenario-specific semantics,
and enforce conditional event order. Do not relax existing extra-file rejection.

Replace direct PASS-directory publication with a hidden sibling staging
directory. Write the complete bundle there, run the independent fault driver
against staging, write final validator exit/evidence/checksums, fsync files and
the staging directory, and atomically rename to the never-existing final
scenario directory only when validator exit is `0`. Remove staging on failure;
never leave a scenario directory under the matrix root unless it is a fully
validated PASS bundle.

- [ ] **Step 4: Run GREEN tests and commit**

```bash
python3 -m pytest -q \
  tests/test_lesson_studio_task14_hil_storage.py \
  tests/test_lesson_studio_task14_evidence.py
git diff --check
git add main/tbot-server/scripts/lesson_studio_task14_hil_storage.py \
  main/tbot-server/scripts/lesson_studio_task14_fault_driver.py \
  main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py \
  main/tbot-server/tests/test_lesson_studio_task14_evidence.py
git commit -m "test(e2e): bind HIL recovery evidence"
```

### Task 4: Add Atomic Failure Quarantine Evidence

**Files:**
- Modify: `main/tbot-server/scripts/lesson_studio_task14_hil_storage.py`
- Test: `main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py`
- Test: `main/tbot-server/tests/test_lesson_studio_task14_evidence.py`

- [ ] **Step 1: Write RED failure-bundle tests**

Add `--failure-evidence-dir` only to `run-scenario` and `run-matrix`, not
`preflight`. Before hardware access, resolve both roots strictly and reject
symlinks, equality, ancestor/descendant overlap, and aliases that resolve to the
same location. Add tests for a required argument and a fixed bundle:

```text
command.txt
serial.log
server.log
timeline.log
build-manifest.json
failure.json
last-responses.json
SHA256SUMS
```

`failure.json` has exactly:

```json
{
  "status": "FAIL",
  "artifactVersion": 1,
  "scenario": "evict-after-unlinks-fail",
  "phase": "cleanup",
  "errorCode": "FIXTURE_CLEANUP_REFUSED",
  "utcStart": "2026-07-17T09:00:00Z",
  "utcFailure": "2026-07-17T09:00:01Z",
  "completedEvents": ["status-before", "inspect-before"]
}
```

`last-responses.json` has fixed keys `statusBefore`, `inspectBefore`, `stage`,
`arm`, `trigger`, `statusAfter`, `inspectAfter`, `recovery`, and `cleanup`; every
value is the last validated bounded object or JSON null. Define a closed
phase-to-stable-error-code mapping; never serialize raw exception text. Require
`utcStart <= utcFailure`, bounded log/JSON sizes, and capture each response only
after its validator succeeds.

Require atomic rename, new timestamped directory, collision refusal, redaction,
stable error codes, exact checksums, no matrix report eligibility, and retention
of the original scenario exception when bundle writing also fails.

- [ ] **Step 2: Run RED tests**

```bash
python3 -m pytest -q tests/test_lesson_studio_task14_hil_storage.py \
  tests/test_lesson_studio_task14_evidence.py -k 'failure_evidence or quarantine'
```

Expected: FAIL because no failure-evidence argument or writer exists.

- [ ] **Step 3: Implement the quarantine writer**

Create a fixed payload builder that accepts only already-redacted bounded data:

```python
def write_failure_bundle(root, scenario, context):
    final = choose_never_existing_timestamped_path(root, scenario)
    staging = create_hidden_sibling_directory(root, scenario)
    payloads = build_failure_payloads(context)
    assert_artifacts_sanitized(payloads, context.secrets)
    write_fsync_and_checksum(staging, payloads)
    atomic_rename_and_fsync_parent(staging, final)
    return final
```

Move all post-preflight setup, including build-identity load, live connection
attestation, secret lookup, transport/client creation, and optional serial
monitor start, inside one guarded boundary with nullable context fields. Call the
quarantine writer for any failure in that boundary after best-effort fixture
cleanup. Preserve the original exception if cleanup or evidence writing also
fails, clean hidden staging best-effort, and never create files under the PASS
matrix root.

- [ ] **Step 4: Run GREEN tests and commit**

```bash
python3 -m pytest -q tests/test_lesson_studio_task14_hil_storage.py \
  tests/test_lesson_studio_task14_evidence.py -k 'failure_evidence or quarantine'
git diff --check
git add main/tbot-server/scripts/lesson_studio_task14_hil_storage.py \
  main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py \
  main/tbot-server/tests/test_lesson_studio_task14_evidence.py
git commit -m "feat(e2e): quarantine failed HIL evidence"
```

### Task 5: Update Build Identity, Release Ledger, And Runbook

**Files:**
- Modify: `main/tbot-server/scripts/lesson_studio_task14_build_identity.py`
- Modify: `main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py`
- Modify: `main/tbot-server/tests/test_lesson_studio_task14_evidence.py`
- Modify: `main/tbot-server/docs/lesson-studio-task14-live-matrix.md`

- [ ] **Step 1: Write RED matrix/release tests**

Require the build-identity validator and `hil-matrix-pass` release receipt to
reject old/missing recovery artifacts, mismatched recovery hashes, quarantine
paths, and self-authored summaries. Require the runbook to pass distinct:

```bash
--evidence-dir "$EVIDENCE_ROOT/storage-hil"
--failure-evidence-dir "$EVIDENCE_ROOT/storage-hil-failures"
```

- [ ] **Step 2: Run RED tests**

```bash
python3 -m pytest -q tests/test_lesson_studio_task14_hil_storage.py \
  tests/test_lesson_studio_task14_evidence.py -k 'matrix or release or runbook'
```

- [ ] **Step 3: Implement exact identity and documentation changes**

Update `HIL_ORDINARY_ARTIFACTS`, inherited power artifacts, scenario records,
and release validation without adding backward compatibility for old bundles.
Update every live `run-scenario`/`run-matrix` command with a fresh quarantine
root and the current `/dev/cu.usbmodem1101` port.

- [ ] **Step 4: Run GREEN tests and commit**

```bash
python3 -m pytest -q tests/test_lesson_studio_task14_hil_storage.py \
  tests/test_lesson_studio_task14_evidence.py
git diff --check
git add main/tbot-server/scripts/lesson_studio_task14_build_identity.py \
  main/tbot-server/tests/test_lesson_studio_task14_hil_storage.py \
  main/tbot-server/tests/test_lesson_studio_task14_evidence.py \
  main/tbot-server/docs/lesson-studio-task14-live-matrix.md
git commit -m "docs(e2e): require recoverable HIL matrix evidence"
```

### Task 6: Full Software Gates And Independent Review

**Files:**
- Modify only if review exposes a real defect.

- [ ] **Step 1: Run full ESP server verification**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/esp32-server-hil-task7-auth-order/main/tbot-server
python3 -m pytest -q
python3 -m compileall core config scripts
ruff check scripts/lesson_studio_task14_hil_storage.py \
  scripts/lesson_studio_task14_fault_driver.py \
  scripts/lesson_studio_task14_build_identity.py --select E9,F63,F7,F82
npm run test:lesson-studio
git diff --check
```

Expected: all tests pass, compileall/ruff exit 0, and the worktree is clean.

- [ ] **Step 2: Re-run unchanged firmware safety tests**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/tbot-firmware-production-lesson-studio-continued
scripts/run_host_native_lesson_storage_hil_fixture_test.sh
scripts/run_host_native_lesson_asset_cache_evict_test.sh
python3 -m pytest tests/test_lesson_storage_hil_contract.py -q
git diff --check
```

Require no firmware diff and preserve build pair `ba472a3`.

- [ ] **Step 3: Dispatch independent spec and quality reviews**

Review the complete diff from `8ff16352` through implementation HEAD. Any
finding returns to the owning task with a RED/GREEN regression and re-review.

### Task 7: Rebuild ESP Server And Run Fresh Hardware Evidence

**Files:**
- Evidence only under a new `/Users/manhhodinh/Documents/TBOT/.codex_tmp/` root.

- [ ] **Step 1: Build and recreate the ESP server image**

```bash
cd /Users/manhhodinh/Documents/TBOT/.worktrees/esp32-server-hil-task7-auth-order
export TBOT_DEVICE_MINT_SECRET="$(docker inspect tbot-esp32-server \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | sed -n 's/^TBOT_DEVICE_MINT_SECRET=//p')"
test -n "$TBOT_DEVICE_MINT_SECRET"
export TBOT_SERVER_IMAGE_TAG="hil-$(git rev-parse --short=12 HEAD)"
./deploy/build-local.sh --tag "$TBOT_SERVER_IMAGE_TAG" --only server --no-latest
export LESSON_RUNTIME_ENABLED=true
export LESSON_MOTION_PRESETS_ENABLED=true
export LESSON_PLAYFUL_INTERACTIONS_ENABLED=true
export LESSON_ROLLOUT_DEVICE_ALLOWLIST=28:84:85:85:1a:80
export LESSON_STORAGE_HIL_DEVICE_ALLOWLIST=28:84:85:85:1a:80
export COURSE_BACKEND_URL=http://192.168.100.209:13100/v1
export LESSON_ASSET_ORIGIN_BASE=http://192.168.100.209:18102/tvideo-demo
export LESSON_ASSET_PUBLIC_BASE_URL=http://192.168.100.209:18102/tvideo-demo
export LESSON_ASSET_DELIVERY_MODE=sd_pack
export TBOT_PUBLIC_WEBSOCKET_URL=ws://192.168.100.209:8000/tbot/v1/
docker compose -f main/tbot-server/docker-compose.yml up -d --force-recreate
docker inspect tbot-esp32-server --format '{{.Config.Image}} {{.State.Status}}'
```

Require the exact new image, every exported LAN endpoint/feature flag, exact MAC
allowlists, nonempty secret without printing it, and one exact MAC/UUID
connection. Fail on stale `192.168.1.25` or `192.168.100.230` values.

- [ ] **Step 2: Run fresh automated partial-eviction smoke**

Define and create non-overlapping new roots, then enter the script directory:

```bash
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export PASS_ROOT="/Users/manhhodinh/Documents/TBOT/.codex_tmp/task14-live-$RUN_ID/storage-hil-smoke"
export FAIL_ROOT="/Users/manhhodinh/Documents/TBOT/.codex_tmp/task14-live-$RUN_ID/storage-hil-smoke-failures"
test ! -e "$PASS_ROOT" && test ! -e "$FAIL_ROOT"
mkdir -p "$PASS_ROOT" "$FAIL_ROOT"
cd /Users/manhhodinh/Documents/TBOT/.worktrees/esp32-server-hil-task7-auth-order/main/tbot-server
```

Run:

```bash
python3 scripts/lesson_studio_task14_hil_storage.py run-scenario \
  --scenario evict-after-unlinks-fail \
  --device-id 28:84:85:85:1a:80 \
  --device-uuid fce7bec8-8478-4ab4-817f-7b87c41c1f91 \
  --serial-port /dev/cu.usbmodem1101 \
  --esp-base-url http://127.0.0.1:8003 \
  --asset-url http://192.168.100.209:18102/tvideo-demo/esp-tft/barn-192.png \
  --asset-sha256 0bc9825de6b18c76990127d0ced5ff8c93dfd0bd931aa5689b3ff46e9d812679 \
  --asset-bytes 42986 \
  --build-manifest /Users/manhhodinh/Documents/TBOT/.worktrees/tbot-firmware-production-lesson-studio-continued/build-task14-hil-ba472a3-v2/lesson-storage-hil-build.json \
  --evidence-dir "$PASS_ROOT" \
  --failure-evidence-dir "$FAIL_ROOT"
```

Reset without NVS erase using the exact safe command and record it:

```bash
/Users/manhhodinh/.espressif/python_env/idf5.5_py3.9_env/bin/python \
  /Users/manhhodinh/esp/esp-idf-v5.5.2/components/esptool_py/esptool/esptool.py \
  --chip esp32s3 --port /dev/cu.usbmodem1101 read_mac \
  > "$PASS_ROOT/post-scenario-reset-read-mac.log" 2>&1
```

Wait for one exact connection, run orchestrator `preflight`, require controller
idle and inspection residue missing, then repeat for
`evict-before-rmdir-fail`. Require recovery attempted, initial partial outcome
preserved, retry exact-evicted, cleanup clean, numeric sequences, validator 0,
and no quarantine bundle.

- [ ] **Step 3: Run the complete fresh nine-scenario matrix**

After operator readiness for SD/power scenarios, run `run-matrix` with new PASS
and failure roots. Require all nine scenario bundles, checksums, independent
fault-driver PASS, and `hil-matrix-report.json` PASS before recording the
`hil-matrix-pass` release receipt.
