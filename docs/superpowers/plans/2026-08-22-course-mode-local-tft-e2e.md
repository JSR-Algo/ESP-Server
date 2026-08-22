# Course Mode Local Physical TFT E2E Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a disposable local assignment-backed runtime for the exact `course-mode-pilot-cat-ball@v1` fixture and collect authoritative renderer-v4 evidence from the approved AC:20 physical TFT without mutating production.

**Architecture:** Extend the existing lesson-studio E2E Compose stack with a physical-lab override bound to host port 3000, deterministic fixture seeding, and a fail-closed preflight/readback script. Keep the installed firmware and protected NVS unchanged; use the existing local ESP WebSocket service and capture tooling only after exact backend, assignment, device, renderer, and checksum identities are proven.

**Tech Stack:** Docker Compose, PostgreSQL 16, NestJS/TypeScript, Python 3.11/pytest, aiohttp, ESP lesson runtime, renderer v4, esptool/serial read-only capture.

---

## File map

- `docs/docker/docker-compose.course-mode-physical-tft.yml`: physical-lab-only Compose override and port/network isolation.
- `docs/docker/course-mode-physical-tft/seed-course-mode-pilot.mjs`: import canonical pilot data and create deterministic local synthetic records.
- `docs/docker/course-mode-physical-tft/verify-course-mode-pilot.mjs`: read back and validate exact local database/runtime identities.
- `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`: fail-closed environment, endpoint, device, privacy, and protected-file checks.
- `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`: unit tests for preflight admission and rejection.
- `docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md`: attended run ledger and final TFT result.

### Task 1: Add the isolated physical-lab Compose overlay

**Files:**
- Create: `docs/docker/docker-compose.course-mode-physical-tft.yml`
- Test: `main/tbot-server/tests/test_course_mode_physical_tft_compose.py`

- [ ] **Step 1: Write the failing Compose contract test**

```python
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[3]

def test_physical_tft_overlay_is_local_and_single_device():
    doc = yaml.safe_load((ROOT / "docs/docker/docker-compose.course-mode-physical-tft.yml").read_text())
    backend = doc["services"]["backend"]
    assert backend["ports"] == ["127.0.0.1:3000:3000"]
    env = backend["environment"]
    assert env["LESSON_ROLLOUT_DEVICE_ALLOWLIST"] == "14:c1:9f:d1:ac:20"
    assert env["COURSE_MODE_V2_PUBLISH_ENABLED"] == "true"
    assert env["LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED"] == "true"
    assert "production" not in str(doc).lower()
```

- [ ] **Step 2: Run the test and verify the file is missing**

Run: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python -m pytest -q tests/test_course_mode_physical_tft_compose.py`

Expected: FAIL because `docker-compose.course-mode-physical-tft.yml` does not exist.

- [ ] **Step 3: Add the minimal override**

```yaml
name: tbot-course-mode-physical-tft
services:
  backend:
    ports: ["127.0.0.1:3000:3000"]
    environment:
      COURSE_MODE_V2_PUBLISH_ENABLED: "true"
      LESSON_STUDIO_NEW_ASSIGNMENTS_ENABLED: "true"
      LESSON_ROLLOUT_DEVICE_ALLOWLIST: "14:c1:9f:d1:ac:20"
      TBOT_DEVICE_MINT_SECRET: ${TBOT_DEVICE_MINT_SECRET:?set local-only mint secret}
```

Compose this after `docker-compose.lesson-studio-e2e.yml`; do not add production URLs, credentials, external volumes, or port 3000 exposure beyond loopback.

- [ ] **Step 4: Validate Compose and test**

Run: `docker compose -f docs/docker/docker-compose.lesson-studio-e2e.yml -f docs/docker/docker-compose.course-mode-physical-tft.yml config --quiet`

Run: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python -m pytest -q tests/test_course_mode_physical_tft_compose.py`

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/docker/docker-compose.course-mode-physical-tft.yml main/tbot-server/tests/test_course_mode_physical_tft_compose.py
git commit -m "test(task07): isolate local physical TFT backend"
```

### Task 2: Materialize the exact canonical pilot and synthetic assignment

The rejected ESP-owned seed commits are superseded. The physical overlay invokes
the reviewed backend-owned compiled materializer, which parses the canonical
persistence fixture, computes the normal manifest checksum, and writes Course Mode
contract/cue rows through backend repositories. The ESP repository contains no
Course Mode seed SQL, checksum implementation, or schema reconstruction.

**Files:**
- Create: `docs/docker/course-mode-physical-tft/seed-course-mode-pilot.mjs`
- Create: `docs/docker/course-mode-physical-tft/seed-course-mode-pilot.test.mjs`
- Reference: `/Users/manhhodinh/Documents/TBOT/tbot-backend/src/lessons/fixtures/course-mode/course-mode-pilot-cat-ball.json`
- Reference: `/Users/manhhodinh/Documents/TBOT/tbot-backend/src/lessons/fixtures/course-mode/pilot/v1/pilot.json`

- [ ] **Step 1: Write failing deterministic-seed tests**

```javascript
import assert from 'node:assert/strict';
import { buildSeedPlan } from './seed-course-mode-pilot.mjs';

const plan = buildSeedPlan({ deviceId: '14:c1:9f:d1:ac:20' });
assert.equal(plan.lessonKey, 'course-mode-pilot-cat-ball');
assert.equal(plan.lessonVersion, 1);
assert.equal(plan.rendererVersion, 'teebot-lesson-renderer.v4');
assert.equal(plan.contractChecksum, 'cf12b1a5f71f0a80a8ee22bb2cdc775ada5b803e26d154e5d29c76b14c9fb264');
assert.equal(plan.deviceId, '14:c1:9f:d1:ac:20');
assert.equal(plan.childData, undefined);
```

- [ ] **Step 2: Run and verify import failure**

Run: `node docs/docker/course-mode-physical-tft/seed-course-mode-pilot.test.mjs`

Expected: FAIL because the seed module does not exist.

- [ ] **Step 3: Implement canonical fixture loading and transactional seed**

Export `buildSeedPlan()` and a CLI. Resolve both fixture paths from `TBOT_BACKEND_WORKTREE`, parse with strict exact identities, and execute one PostgreSQL transaction that creates deterministic local UUIDs for course, lesson, adult synthetic owner, device, and assignment. Insert the Course Mode contract through the repository table used by `CourseModeRepository`; insert authored steps/assets from the canonical pilot package; set only the disposable local lesson row to `published` and assignment to `active`.

Reject if any of these differ: fixture ID, lesson/version, package version, renderer, semantic checksum, layout checksum, profile `espTft`, device ID, or ordered activity identities. Reject fields matching `child`, `transcript`, `utterance`, `audio`, `pronunciation`, `servo`, or free-form story data.

- [ ] **Step 4: Run unit and backend fixture tests**

Run: `node docs/docker/course-mode-physical-tft/seed-course-mode-pilot.test.mjs`

Run: `cd /Users/manhhodinh/Documents/TBOT/tbot-backend && pnpm vitest run src/lessons/course-mode/pilot/course-mode-pilot.spec.ts src/lessons/lesson-manifest.course-mode.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/docker/course-mode-physical-tft/seed-course-mode-pilot.mjs docs/docker/course-mode-physical-tft/seed-course-mode-pilot.test.mjs
git commit -m "feat(task07): seed exact local Course Mode pilot"
```

### Task 3: Add exact readback verification

**Files:**
- Create: `docs/docker/course-mode-physical-tft/verify-course-mode-pilot.mjs`
- Create: `docs/docker/course-mode-physical-tft/verify-course-mode-pilot.test.mjs`

- [ ] **Step 1: Write failing verifier tests**

Test one accepted snapshot and separate failures for renderer drift, checksum drift, non-AC:20 assignment, more than one active assignment, production URL presence, and privacy-key presence. Each rejection must return a stable code such as `RENDERER_IDENTITY`, `CONTRACT_CHECKSUM`, `DEVICE_SCOPE`, `ASSIGNMENT_CARDINALITY`, `PRODUCTION_REFERENCE`, or `PRIVACY_FIELD`.

- [ ] **Step 2: Run and verify failure**

Run: `node docs/docker/course-mode-physical-tft/verify-course-mode-pilot.test.mjs`

Expected: FAIL because the verifier does not exist.

- [ ] **Step 3: Implement verifier and JSON receipt**

The CLI must query the local database and backend manifest endpoint, compare exact identities, and write a canonical JSON receipt containing only IDs, versions, checksums, states, timestamps, and boolean gates. It must never include JWTs, secrets, raw speech, or personal data.

- [ ] **Step 4: Run tests**

Run: `node docs/docker/course-mode-physical-tft/verify-course-mode-pilot.test.mjs`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/docker/course-mode-physical-tft/verify-course-mode-pilot.mjs docs/docker/course-mode-physical-tft/verify-course-mode-pilot.test.mjs
git commit -m "test(task07): verify local pilot identity readback"
```

### Task 4: Add fail-closed physical TFT preflight

**Files:**
- Create: `main/tbot-server/scripts/course_mode_physical_tft_preflight.py`
- Create: `main/tbot-server/tests/test_course_mode_physical_tft_preflight.py`

- [ ] **Step 1: Write failing Python tests**

```python
def test_accepts_exact_local_lane(tmp_path):
    result = evaluate(make_exact_snapshot(tmp_path))
    assert result["result"] == "pass"

def test_rejects_production_assignment(tmp_path):
    snapshot = make_exact_snapshot(tmp_path)
    snapshot["backendUrl"] = "https://tbot-backend-8wmh.onrender.com/v1"
    assert evaluate(snapshot)["errors"] == ["PRODUCTION_REFERENCE"]
```

Also cover missing port, wrong USB suffix, port owner, protected SHA drift, wrong OTA/WS URL, wrong fixture/renderer/checksum, missing David/operator assertions, and output directory outside `task-artifacts/course-mode-task07`.

- [ ] **Step 2: Run and verify failure**

Run: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python -m pytest -q tests/test_course_mode_physical_tft_preflight.py`

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement pure evaluator and CLI adapters**

Keep validation in `evaluate(snapshot)` and isolate filesystem, Docker, HTTP, USB, `lsof`, and hash collection in adapter functions. Output only canonical JSON. Require exact AC:20 device, loopback backend port 3000, local OTA/WS endpoints, reviewed candidate SHA, protected test SHA, renderer v4, pilot v1, David present, operator present, clear motion envelope, and immediate power isolation.

- [ ] **Step 4: Run tests and privacy scan**

Run: `/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python -m pytest -q tests/test_course_mode_physical_tft_preflight.py tests/test_course_mode_contract.py tests/test_course_mode_runtime_integration.py`

Run: `rg -n -i "transcript|utterance|raw.?speech|audio.?data|pronunciation.?score|servo.?data" main/tbot-server/scripts/course_mode_physical_tft_preflight.py`

Expected: tests PASS; scan finds only explicit forbidden-key validation strings.

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/scripts/course_mode_physical_tft_preflight.py main/tbot-server/tests/test_course_mode_physical_tft_preflight.py
git commit -m "feat(task07): gate physical TFT capture preflight"
```

### Task 5: Start the isolated stack and prove runtime admission

**Files:**
- Modify: `docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md`

- [ ] **Step 1: Record immutable inputs**

Record ESP, backend, firmware SHAs; candidate bundle root; application SHA; NVS SHA; protected SHA; device suffix; operator; observer; local IP; UTC start time.

- [ ] **Step 2: Start only the task-owned local backend**

Run Compose with a new project name and fresh named volumes, local-only mint secret, backend host port 3000, and existing backend image. Do not stop or recreate unrelated containers.

- [ ] **Step 3: Seed and verify**

Run the seed CLI once, rerun it to prove idempotency, then run the verifier. Expected: exactly one active AC:20 assignment and exact pilot identities.

- [ ] **Step 4: Run preflight**

Run the Python preflight with David/operator/safety assertions and timestamped output directory. Expected: PASS before any lesson trigger.

- [ ] **Step 5: Establish robot local connectivity**

Use only a reviewed provisioning route. If the candidate cannot be directed to the local OTA/WS endpoint without NVS patching, stop and record `LOCAL_ENDPOINT_UNAVAILABLE`; do not claim TFT evidence.

- [ ] **Step 6: Verify runtime metrics**

Require AC:20 authenticated connection, renderer-v4 capability, app-ready, zero privacy markers, and no unexpected motion before triggering.

### Task 6: Capture every TFT cue and close safely

**Files:**
- Modify: `docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md`
- Output: `task-artifacts/course-mode-task07/tft-<UTC>-ac20/`

- [ ] **Step 1: Start redacted capture**

Run `robot/scripts/lesson_e2e_live_capture.py --preflight` with the exact local OTA/WS URLs, serial port, lesson ID, lesson version, renderer version, and output directory. Retain no audio or raw content.

- [ ] **Step 2: Trigger the assignment-backed lesson**

After authenticated app-ready evidence, use the scoped internal nudge or have the adult operator say `bắt đầu bài học`. Do not use the built-in sample lesson.

- [ ] **Step 3: Inspect each authored cue**

For every cue, record timestamp, cue/activity ID, expected visual state, operator verdict, and image/video frame reference. Inspect background, cat, ball, robot pose, caption, listening indicator, crop, overlap, z-order, focus anchors, and reduced-motion behavior.

- [ ] **Step 4: Complete and verify rest**

Require ordered ACKs, completion, stop, quiescence, stable screen, centered head, lowered arms, no chatter/binding/vibration/odor/heat/power instability, and zero privacy markers.

- [ ] **Step 5: Validate and independently review evidence**

Run the existing capture validator plus the new preflight/verifier against the final bundle. Mark TFT `PASS` only when every cue has direct visual evidence; otherwise mark `BLOCKED` or `FAIL` with stable reasons.

- [ ] **Step 6: Clean up task-owned local resources**

Stop only the dedicated Compose project. After evidence validation, remove only its containers and named volumes. Confirm existing ESP lab containers, Farm v9/T54/T65 worktrees, and protected file remain untouched.

- [ ] **Step 7: Commit the redacted report**

```bash
git add docs/qa/ad-hoc/2026-08-22-course-mode-task07-tft-e2e.md
git commit -m "docs(task07): record physical Course Mode TFT E2E"
```

### Task 7: Post-run regression and integration

**Files:**
- Verify all files changed by Tasks 1-6.

- [ ] **Step 1: Run focused ESP tests**

Run the new Compose/preflight tests plus Course Mode contract/runtime, renderer-v4, SD pack, privacy, and capture-validator suites.

- [ ] **Step 2: Run backend tests**

Run Course Mode pilot, manifest, assignment, mint-token, and PostgreSQL repository suites on backend main.

- [ ] **Step 3: Run firmware tests**

Run Course Mode fixture, lesson handler, renderer-v4 visual-layout, lifecycle, privacy/uplink authorization, and native renderer suites on firmware main.

- [ ] **Step 4: Verify repository hygiene**

Run `git diff --check`, protected SHA verification, branch containment checks, and inspect every changed path. Confirm no production state or unrelated worktree changed.

- [ ] **Step 5: Request independent review**

Review design compliance, local/production isolation, seed correctness, privacy, visual evidence completeness, cleanup, and residual Task 07 blockers.

- [ ] **Step 6: Merge only after all gates pass**

Merge reviewed commits into ESP main without reset/force/discard, rerun the post-merge gates, record final main SHA, and remove only this clean fully merged task-owned worktree and branch.
