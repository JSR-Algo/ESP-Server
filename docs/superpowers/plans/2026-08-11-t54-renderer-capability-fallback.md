# T5.4 Renderer Capability Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an allowlisted multi-renderer robot fetch an assigned lower-renderer lesson without disabling its higher-renderer rollout lane.

**Architecture:** Keep the backend exact capability serve gate unchanged. Change the ESP manifest request from a single highest enabled renderer to an ordered set of every enabled renderer the firmware advertises, plus advertised baseline v1. The runtime continues selecting behavior from the returned manifest's exact `manifestVersion` and retains every existing checksum, feature, asset, and wire validation gate.

**Tech Stack:** Python 3.11, `unittest`/pytest, httpx-compatible manifest client, T0.4 shell repro gate, Docker/VPS deployment scripts.

---

### Task 1: Pin the production failure with RED tests

**Files:**
- Modify: `main/tbot-server/tests/test_lesson_runtime_branch_gaps.py`
- Modify: `main/tbot-server/tests/test_lesson_runtime.py`

- [ ] **Step 1: Change the helper capability matrix to require compatible fallbacks**

Replace the `cases` tuple in `CinematicCapabilitySelectionTest.test_requests_only_enabled_exact_advertised_renderer_lanes` with:

```python
cases = (
    (
        "v2 plus baseline",
        ["teebot-lesson-renderer.v1", "teebot-lesson-renderer.v2"],
        True,
        False,
        False,
        ["teebot-lesson-renderer.v2", "teebot-lesson-renderer.v1"],
    ),
    ("v3 only", [RENDERER_V3, RENDERER_V4], False, True, False, [RENDERER_V3]),
    ("v4 only", [RENDERER_V3, RENDERER_V4], False, False, True, [RENDERER_V4]),
    (
        "all compatible rollout lanes",
        [
            "teebot-lesson-renderer.v1",
            "teebot-lesson-renderer.v2",
            RENDERER_V3,
            RENDERER_V4,
        ],
        True,
        True,
        True,
        [
            RENDERER_V4,
            RENDERER_V3,
            "teebot-lesson-renderer.v2",
            "teebot-lesson-renderer.v1",
        ],
    ),
    (
        "disabled cinematic",
        [RENDERER_V3, RENDERER_V4],
        False,
        False,
        False,
        ["teebot-lesson-renderer.v1"],
    ),
)
```

- [ ] **Step 2: Add a production-shaped pull-on-connect regression**

Add this test to `LessonPullOnConnectCapabilityTest` in `main/tbot-server/tests/test_lesson_runtime.py`:

```python
async def test_v4_rollout_keeps_v2_and_v1_fallbacks_for_assigned_v2_manifest(self):
    from core.lesson.runtime import maybe_start_lesson_on_connect

    conn = _RepublishConn()
    conn.features = {
        "lesson": True,
        "renderer": [
            "teebot-lesson-renderer.v1",
            "teebot-lesson-renderer.v2",
            "teebot-lesson-renderer.v3",
            "teebot-lesson-renderer.v4",
        ],
        "lessonRendererV3": {"directMp4Cinematic": True, "sdAssetPack": True},
        "lessonRendererV4": {"flattenedMjpegCinematic": True, "sdAssetPack": True},
    }
    conn.config["lesson"].update({
        "renderer_v2_enabled": True,
        "renderer_v4_enabled": True,
    })
    assignment = {
        "assignmentId": FIX["frames"]["lesson_prepare"]["assignmentId"],
        "assignmentVersion": 1,
        "lessonId": FIX["frames"]["lesson_prepare"]["lessonId"],
        "lessonVersion": 3,
        "manifestChecksum": _manifest_checksum(),
        "profile": "espTft",
        "state": "ASSIGNED",
    }
    manifest = _build_manifest()
    manifest["manifestVersion"] = "teebot-lesson-renderer.v2"
    manifest["openingEntrance"] = {
        "template": "tvideoFlyWalk",
        "preset": "flyLandWalkGreet",
        "policy": "oncePerLessonSession",
        "layoutPreset": "centerRoad",
        "phases": [
            "hidden", "flyIn", "landFar", "settle", "walkToward",
            "arriveNear", "greetIdle", "revealTeachingContent",
        ],
        "backgroundAssetKey": "scene.farm",
        "robotAssetKey": "robotOverlay.teach",
        "fallback": "staticGreet",
    }
    undo = self._patch_backend(assignment, manifest)
    try:
        runtime = await maybe_start_lesson_on_connect(conn)
    finally:
        undo()

    self.assertIsNotNone(runtime)
    self.assertEqual(
        self.manifest_calls[0]["renderer_capabilities"],
        [
            "teebot-lesson-renderer.v4",
            "teebot-lesson-renderer.v2",
            "teebot-lesson-renderer.v1",
        ],
    )
    self.assertEqual(runtime.negotiated_version, "teebot-lesson-renderer.v2")
```

- [ ] **Step 3: Run both tests and verify RED**

Run:

```bash
/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python \
  -m pytest \
  main/tbot-server/tests/test_lesson_runtime_branch_gaps.py::CinematicCapabilitySelectionTest::test_requests_only_enabled_exact_advertised_renderer_lanes \
  main/tbot-server/tests/test_lesson_runtime.py::LessonPullOnConnectCapabilityTest::test_v4_rollout_keeps_v2_and_v1_fallbacks_for_assigned_v2_manifest \
  -q
```

Expected: both tests fail because `_requested_renderer_capabilities()` returns only v4 or v2.

- [ ] **Step 4: Commit the RED tests**

```bash
git add main/tbot-server/tests/test_lesson_runtime_branch_gaps.py main/tbot-server/tests/test_lesson_runtime.py
git commit -m "test(lessons): reproduce rollout renderer mismatch"
```

### Task 2: Return the ordered compatible renderer set

**Files:**
- Modify: `main/tbot-server/core/lesson/runtime.py`

- [ ] **Step 1: Implement the minimal capability-set change**

Replace `_requested_renderer_capabilities()` with:

```python
def _requested_renderer_capabilities(
    advertised: List[str],
    *,
    renderer_v2_enabled: bool,
    renderer_v3_enabled: bool,
    renderer_v4_enabled: bool,
) -> List[str]:
    enabled = (
        (RENDERER_V4, renderer_v4_enabled),
        (RENDERER_V3, renderer_v3_enabled),
        (RENDERER_V2, renderer_v2_enabled),
    )
    advertised_set = set(advertised)
    requested = [
        renderer
        for renderer, rollout_enabled in enabled
        if rollout_enabled and renderer in advertised_set
    ]
    if PROTOCOL_VERSION in advertised_set:
        requested.append(PROTOCOL_VERSION)
    return requested or [PROTOCOL_VERSION]
```

- [ ] **Step 2: Run the RED tests and verify GREEN**

Run the exact Task 1 Step 3 command.

Expected: 2 passed.

- [ ] **Step 3: Run adjacent renderer and pull suites**

```bash
/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python \
  -m pytest \
  main/tbot-server/tests/test_lesson_runtime_branch_gaps.py \
  main/tbot-server/tests/test_lesson_runtime.py \
  main/tbot-server/tests/test_lesson_cinematic_phase_routing.py \
  main/tbot-server/tests/test_lesson_rollout_controls.py \
  -q
```

Expected: all collected tests pass with only known dependency warnings.

- [ ] **Step 4: Commit the implementation**

```bash
git add main/tbot-server/core/lesson/runtime.py
git commit -m "fix(lessons): retain compatible renderer fallbacks"
```

### Task 3: Add the T0.4 repro and task evidence

**Files:**
- Create: `/Users/manhhodinh/Documents/TBOT/lesson-prod/repros/t54-renderer-compat.sh`
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/docs/qa/ad-hoc/2026-08-11-t54-e2e-live.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`

- [ ] **Step 1: Create a source-level behavior repro**

Create the repro with this content:

```bash
#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

PYTHON=/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python
"$PYTHON" -m pytest \
  main/tbot-server/tests/test_lesson_runtime_branch_gaps.py::CinematicCapabilitySelectionTest::test_requests_only_enabled_exact_advertised_renderer_lanes \
  main/tbot-server/tests/test_lesson_runtime.py::LessonPullOnConnectCapabilityTest::test_v4_rollout_keeps_v2_and_v1_fallbacks_for_assigned_v2_manifest \
  -q
```

Make it executable.

- [ ] **Step 2: Record RED/GREEN commands and the F-T54-21 fix summary**

Append the branch, commits, exact failing base behavior, passing tip output, and no-wire-contract
decision to `robot/docs/qa/ad-hoc/2026-08-11-t54-e2e-live.md`. Update F-T54-21 in the plan findings
log to `CLAIMED` until the T0.4 gate passes.

- [ ] **Step 3: Verify formatting and script syntax**

```bash
bash -n /Users/manhhodinh/Documents/TBOT/lesson-prod/repros/t54-renderer-compat.sh
git diff --check
```

Expected: both commands exit 0.

### Task 4: Re-verify, gate, and merge

**Files:**
- Evidence updates only after commands complete.

- [ ] **Step 1: Rebase the task branch onto current main**

```bash
git fetch --all --prune
git rebase main
```

Expected: clean rebase with the design, RED test, and implementation commits retained.

- [ ] **Step 2: Run the focused and standard task suites at branch tip**

```bash
/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python \
  -m pytest main/tbot-server/tests/test_lesson_runtime_branch_gaps.py \
  main/tbot-server/tests/test_lesson_runtime.py -q

/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python \
  -m pytest main/tbot-server/tests -q
```

Expected: both commands pass; record exact counts.

- [ ] **Step 3: Run the T0.4 RED-to-GREEN gate and merge**

```bash
bash /Users/manhhodinh/Documents/TBOT/lesson-prod/scripts/merge-task.sh \
  t54-renderer-compat \
  /Users/manhhodinh/Documents/TBOT/robot/esp32-server \
  lesson-prod/t54-renderer-compat
```

Expected: RED on base, GREEN on tip, gate `VERIFIED`, then a no-squash merge commit on main.

### Task 5: Deploy and prove the production manifest boundary

**Files:**
- Modify evidence and plan status after deployment verification.

- [ ] **Step 1: Back up the VPS database**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server
ssh -i /Users/manhhodinh/.ssh/tbot_vps_ed25519 -p 22701 root@160.187.240.56 \
  'cd /opt/tbot/current && TBOT_BACKUP_DIR=/opt/tbot/backups bash deploy/backup-db.sh'
```

Expected: timestamped backup completes before container replacement.

- [ ] **Step 2: Build, package, and deploy a unique server tag**

```bash
TAG="t54-renderer-compat-$(date -u +%Y%m%d%H%M%S)"
DEPLOY_ENV="$(mktemp /tmp/t54-renderer-compat-env.XXXXXX)"
chmod 600 "$DEPLOY_ENV"
scp -P 22701 -i /Users/manhhodinh/.ssh/tbot_vps_ed25519 \
  root@160.187.240.56:/opt/tbot/.env "$DEPLOY_ENV"
bash deploy/build-local.sh --tag "$TAG" --platform linux/amd64 \
  --server-image dinhmanh11/tbot-server --web-image dinhmanh11/tbot-server-web \
  --server-base-image dinhmanh11/tbot-server-base --fast-google-live \
  --server-requirements-file main/tbot-server/requirements-google-live.txt
bash deploy/package-release.sh --tag "$TAG"
bash deploy/deploy-vps.sh --host 160.187.240.56 --user root --port 22701 \
  --key /Users/manhhodinh/.ssh/tbot_vps_ed25519 --tag "$TAG" \
  --env-file "$DEPLOY_ENV"
rm -f "$DEPLOY_ENV"
```

Expected: server container uses the new tag and deploy smoke exits 0.

- [ ] **Step 3: Run public and device-authenticated production checks**

```bash
bash deploy/smoke-vps.sh \
  --admin-url https://admin.tjbot.vn/ \
  --ota-url https://esp.tjbot.vn/tbot/ota/ \
  --expected-ws-host esp.tjbot.vn
```

Run the existing device-authenticated manifest probe from the evidence procedure and require HTTP
200 with renderer v2 and checksum
`350c181c60e86b0ca384775b00d746cf59b3fbcf6f67fc1ed693c6be89251548` while v4 remains enabled.

- [ ] **Step 4: Re-test merged main in a throwaway worktree**

```bash
bash /Users/manhhodinh/Documents/TBOT/lesson-prod/scripts/verify-on-main.sh \
  /Users/manhhodinh/Documents/TBOT/robot/esp32-server -- \
  /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server/.venv311/bin/python \
  -m pytest main/tbot-server/tests/test_lesson_runtime_branch_gaps.py \
  main/tbot-server/tests/test_lesson_runtime.py -q
```

Expected: merged main passes the renderer regression suites.

### Task 6: Resume and close the physical T5.4 checklist

**Files:**
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/docs/qa/ad-hoc/2026-08-11-t54-e2e-live.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/lesson-prod/t54-e2e-live.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`
- Create physical artifacts under: `/Users/manhhodinh/Documents/TBOT/robot/docs/evidence/`

- [ ] **Step 1: Re-run preflight and the read-only live probe**

Run `lesson_e2e_live_capture.py --preflight` and `tbot_live_e2e_probe.sh` with the stable production
endpoint variables. Require preflight `ok=true` and a successful authenticated synthetic WS walk.

- [ ] **Step 2: Capture a full human-spoken physical lesson**

Run the 240-second capture command from the T5.4 checklist with the exact assignment, lesson,
course, child, backend, and device expectations. After app-ready, speak `bắt đầu bài học` at normal
distance and answer all four interactive steps. Require `lesson-e2e-report.json` `ok=true`.

- [ ] **Step 3: Record manual physical acceptance**

Record video showing audible prompts, all three visible layers, and arm/MCP motion. Record the
normal speaking distance and confirm no reset, panic, blank screen, or immediate pronunciation
scoring. Archive the video or its evidence reference beside the capture artifacts.

- [ ] **Step 4: Run the power-cycle recovery leg**

Power-cycle during the lesson, then run boot attestation and post-reseat recovery checks from the
physical checklist. Require reconnection, assignment recovery, and a verifier-green resumed or
cleanly restarted session according to the existing T2.4/T3.4 contract.

- [ ] **Step 5: Verify parent-app progress SLA**

Observe the parent app after each interactive step, record timestamps, and require each persisted
progress update within the checklist SLA. Archive screenshots or video evidence.

- [ ] **Step 6: Complete Ship cleanup and set DONE**

After all five remaining boxes pass, update evidence, set T5.4 DONE in both status locations, verify
the renderer branch is merged and clean, remove
`/Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/t54-renderer-compat`, and delete the
local/remote task branch. Do not remove the worktree if it is dirty or unmerged.
