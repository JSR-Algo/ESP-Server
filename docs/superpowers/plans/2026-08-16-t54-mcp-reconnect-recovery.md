# T5.4 MCP Reconnect Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make physical mid-lesson reboot recovery wait for complete MCP discovery and clear stale lesson layers on terminal startup failure so the conversation face never covers lesson content.

**Architecture:** Add a connect-time MCP readiness gate before candidate construction while retaining the existing sync-level readiness check as defense in depth. Reuse the existing preload-reset WebSocket protocol for bounded stale-layer cleanup only when there is no previous usable runtime. Preserve `_lesson_pull_lock`, fail-closed SD attestation, and existing assignment lifecycle semantics.

**Tech Stack:** Python 3 asyncio, unittest/pytest, ESP server WebSocket lesson protocol, device MCP, Docker Compose VPS deployment, physical ESP32 firmware and Android verification.

---

## File Map

- Modify `main/tbot-server/core/lesson/runtime.py`: connect-time MCP readiness helper, gate integration, and terminal startup cleanup orchestration.
- Modify `main/tbot-server/tests/test_lesson_runtime.py`: deterministic RED/GREEN coverage for delayed readiness, timeout, cleanup ordering, prior-runtime preservation, and cleanup failure.
- Create `docs/qa/ad-hoc/2026-08-16-t54-mcp-reconnect-recovery.md`: repro, code evidence, test results, deployment identity, and physical proof.
- Modify `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`: route the no-eager-assignment-nudge finding to section 5 and update T5.4 evidence/status only after Ship completes.
- Modify `/Users/manhhodinh/Documents/TBOT/lesson-prod/t54-e2e-live.md`: append recovery evidence and set DONE only after every physical and Ship gate passes.

### Task 1: Create the implementation worktree

**Files:**
- Source: `docs/superpowers/specs/2026-08-16-t54-mcp-reconnect-recovery-design.md`
- Source: `docs/superpowers/plans/2026-08-16-t54-mcp-reconnect-recovery.md`

- [ ] **Step 1: Commit this implementation plan on the design branch**

```bash
git add docs/superpowers/plans/2026-08-16-t54-mcp-reconnect-recovery.md
git commit -m "docs: plan T5.4 MCP reconnect recovery"
```

- [ ] **Step 2: Create an isolated implementation worktree from the plan commit**

```bash
git worktree add -b lesson-prod/t54-mcp-reconnect-recovery \
  .worktrees/t54-mcp-reconnect-recovery \
  lesson-prod/t54-mcp-reconnect-recovery-design
```

- [ ] **Step 3: Confirm the worktree starts clean and includes the approved documents**

```bash
git status --short --branch
test -f docs/superpowers/specs/2026-08-16-t54-mcp-reconnect-recovery-design.md
test -f docs/superpowers/plans/2026-08-16-t54-mcp-reconnect-recovery.md
```

Expected: clean `lesson-prod/t54-mcp-reconnect-recovery` branch and both tests exit zero.

### Task 2: Reproduce delayed MCP discovery at connect time

**Files:**
- Modify: `main/tbot-server/tests/test_lesson_runtime.py`

- [ ] **Step 1: Extend `_RepublishConn` with an MCP-capable delayed client fixture**

Add this fixture near `_RepublishConn`:

```python
class _DelayedReadyMcpClient:
    def __init__(self, *, ready_after_checks: int):
        self.ready_after_checks = ready_after_checks
        self.ready_checks = 0
        self.sync_calls = 0

    async def is_ready(self):
        self.ready_checks += 1
        return self.ready_checks >= self.ready_after_checks
```

Set `conn.features["mcp"] = True`, attach the client, and configure short deterministic `mcp_reconnect_ready_timeout_sec` and `mcp_reconnect_ready_poll_sec` values inside each test.

- [ ] **Step 2: Write the delayed-readiness RED test**

Add to `RepublishOnConnectTest`:

```python
async def test_connect_waits_for_mcp_discovery_before_sd_sync(self):
    import core.lesson.runtime as runtime_module

    conn = _RepublishConn()
    conn.features["mcp"] = True
    conn.config["lesson"].update({
        "asset_delivery_mode": "sd_pack",
        "mcp_reconnect_ready_timeout_sec": 0.1,
        "mcp_reconnect_ready_poll_sec": 0.001,
    })
    conn.mcp_client = _DelayedReadyMcpClient(ready_after_checks=3)
    sync_ready_checks = []

    async def preload_after_ready(_runtime):
        sync_ready_checks.append(conn.mcp_client.ready_checks)
        return True

    undo = self._patch_backend(
        self._assignment(lesson_version=3, assignment_version=1),
        _build_manifest(),
    )
    try:
        with patch.object(
            runtime_module.LessonRuntime,
            "preload_only",
            new=preload_after_ready,
        ), patch.object(
            runtime_module.LessonRuntime,
            "start_protocol",
            new=AsyncMock(return_value=None),
        ):
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
    finally:
        undo()

    self.assertIsNotNone(result)
    self.assertGreaterEqual(conn.mcp_client.ready_checks, 3)
    self.assertEqual(sync_ready_checks, [conn.mcp_client.ready_checks])
```

- [ ] **Step 3: Run the delayed-readiness test and confirm RED**

```bash
cd main/tbot-server
python -m pytest tests/test_lesson_runtime.py::RepublishOnConnectTest::test_connect_waits_for_mcp_discovery_before_sd_sync -q
```

Expected: FAIL because connect-time recovery constructs/preloads the candidate before the new MCP readiness gate exists.

- [ ] **Step 4: Write the never-ready timeout RED test**

```python
async def test_connect_mcp_discovery_timeout_fails_closed_without_preload(self):
    import core.lesson.runtime as runtime_module

    conn = _RepublishConn()
    conn.features["mcp"] = True
    conn.config["lesson"].update({
        "asset_delivery_mode": "sd_pack",
        "mcp_reconnect_ready_timeout_sec": 0,
        "mcp_reconnect_ready_poll_sec": 0.001,
    })
    conn.mcp_client = _DelayedReadyMcpClient(ready_after_checks=999)
    preload = AsyncMock(return_value=True)
    undo = self._patch_backend(
        self._assignment(lesson_version=3, assignment_version=1),
        _build_manifest(),
    )
    try:
        with patch.object(runtime_module.LessonRuntime, "preload_only", new=preload):
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
    finally:
        undo()

    self.assertIsNone(result)
    preload.assert_not_awaited()
    self.assertIsNone(conn.lesson_runtime)
    self.assertEqual(conn.lesson_start_status["code"], "MCP_DISCOVERY_TIMEOUT")
```

- [ ] **Step 5: Run the timeout test and confirm RED**

```bash
python -m pytest tests/test_lesson_runtime.py::RepublishOnConnectTest::test_connect_mcp_discovery_timeout_fails_closed_without_preload -q
```

Expected: FAIL because `MCP_DISCOVERY_TIMEOUT` and the connect-time gate do not exist.

- [ ] **Step 6: Write the readiness-exception RED test**

```python
async def test_connect_mcp_discovery_error_fails_closed(self):
    import core.lesson.runtime as runtime_module

    conn = _RepublishConn()
    conn.features["mcp"] = True
    conn.config["lesson"]["mcp_reconnect_ready_timeout_sec"] = 0.1
    conn.mcp_client = _DelayedReadyMcpClient(ready_after_checks=1)
    conn.mcp_client.is_ready = AsyncMock(side_effect=RuntimeError("discovery failed"))
    undo = self._patch_backend(
        self._assignment(lesson_version=3, assignment_version=1),
        _build_manifest(),
    )
    try:
        result = await runtime_module.maybe_start_lesson_on_connect(conn)
    finally:
        undo()

    self.assertIsNone(result)
    self.assertEqual(conn.lesson_start_status["code"], "MCP_DISCOVERY_TIMEOUT")
    self.assertIsNone(conn.lesson_runtime)
```

- [ ] **Step 7: Run the readiness-exception test and confirm RED**

```bash
python -m pytest tests/test_lesson_runtime.py::RepublishOnConnectTest::test_connect_mcp_discovery_error_fails_closed -q
```

Expected: FAIL because the connect-time readiness error is not yet converted into a bounded fail-closed status.

- [ ] **Step 8: Commit the RED tests**

```bash
git add main/tbot-server/tests/test_lesson_runtime.py
git commit -m "test(lesson): reproduce MCP reconnect discovery race"
```

### Task 3: Implement the bounded connect-time readiness gate

**Files:**
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Test: `main/tbot-server/tests/test_lesson_runtime.py`

- [ ] **Step 1: Add a reusable readiness helper near `maybe_start_lesson_on_connect`**

```python
async def _wait_for_connect_mcp_ready(conn: Any, lesson_cfg: Dict[str, Any]) -> bool:
    features = getattr(conn, "features", {}) or {}
    if not bool(features.get("mcp")):
        return True
    mcp_client = getattr(conn, "mcp_client", None)
    is_ready = getattr(mcp_client, "is_ready", None)
    if not callable(is_ready):
        return False
    timeout = max(
        0.0,
        _finite_float_or_default(
            lesson_cfg.get("mcp_reconnect_ready_timeout_sec", 20.0), 20.0
        ),
    )
    poll = max(
        0.001,
        _finite_float_or_default(
            lesson_cfg.get("mcp_reconnect_ready_poll_sec", 0.05), 0.05
        ),
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            if await is_ready():
                return True
        except Exception:
            return False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(poll, remaining))
```

- [ ] **Step 2: Gate MCP-capable connect recovery after hello/features and before backend candidate work**

Insert after the renderer capability gate in `_maybe_start_lesson_on_connect_impl`:

```python
if not await _wait_for_connect_mcp_ready(conn, lesson_cfg):
    _set_lesson_start_status(
        conn,
        "MCP_DISCOVERY_TIMEOUT",
        "Robot chưa hoàn tất kết nối điều khiển bài học.",
    )
    _log("warning", "lesson connect recovery MCP discovery timed out")
    return None
```

This placement guarantees no manifest candidate, runtime, preload, or SD-sync MCP call is created before discovery completes.

- [ ] **Step 3: Run both readiness tests GREEN**

```bash
cd main/tbot-server
python -m pytest \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_connect_waits_for_mcp_discovery_before_sd_sync \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_connect_mcp_discovery_timeout_fails_closed_without_preload \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_connect_mcp_discovery_error_fails_closed \
  -q
```

Expected: `3 passed`.

- [ ] **Step 4: Run existing readiness defense regression**

```bash
python -m pytest tests/test_lesson_runtime.py::LessonRuntimeTest::test_sd_asset_pack_waits_for_mcp_discovery_before_prepare -q
```

Expected: `1 passed`; the inner sync readiness defense remains intact.

- [ ] **Step 5: Commit the readiness implementation**

```bash
git add main/tbot-server/core/lesson/runtime.py main/tbot-server/tests/test_lesson_runtime.py
git commit -m "fix(lesson): wait for MCP discovery on reconnect"
```

### Task 4: Reproduce stale lesson-layer cleanup failures

**Files:**
- Modify: `main/tbot-server/tests/test_lesson_runtime.py`

- [ ] **Step 1: Write the cleanup-order RED test**

Add a test that patches `LessonRuntime.preload_only` to return `False`, provides `request_lesson_preload_reset` and `release_lesson_mode` recorders, and asserts reset happens first:

```python
async def test_terminal_startup_failure_clears_layers_before_release(self):
    import core.lesson.runtime as runtime_module

    conn = _RepublishConn()
    order = []

    async def reset(**_kwargs):
        order.append("reset")
        return True

    async def release_lesson_mode(*, reason):
        order.append(f"release:{reason}")

    conn.request_lesson_preload_reset = reset
    conn.release_lesson_mode = release_lesson_mode
    undo = self._patch_backend(
        self._assignment(lesson_version=3, assignment_version=1),
        _build_manifest(),
    )
    try:
        with patch.object(
            runtime_module.LessonRuntime,
            "preload_only",
            new=AsyncMock(return_value=False),
        ):
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
    finally:
        undo()

    self.assertIsNone(result)
    self.assertEqual(order, ["reset", "release:lesson_start_refused"])
```

- [ ] **Step 2: Write the previous-runtime preservation RED test**

```python
async def test_failed_candidate_does_not_clear_previous_runtime_layers(self):
    import core.lesson.runtime as runtime_module

    conn = _RepublishConn()
    prior = _PinnedRuntime(
        assignment_id="old-assignment",
        lesson_version=2,
        assignment_version=1,
    )
    conn.lesson_runtime = prior
    reset = AsyncMock(return_value=True)
    conn.request_lesson_preload_reset = reset
    undo = self._patch_backend(
        self._assignment(lesson_version=3, assignment_version=1),
        _build_manifest(),
    )
    try:
        with patch.object(
            runtime_module.LessonRuntime,
            "preload_only",
            new=AsyncMock(return_value=False),
        ):
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
    finally:
        undo()

    self.assertIs(result, prior)
    reset.assert_not_awaited()
```

- [ ] **Step 3: Write the cleanup-failure resilience RED test**

```python
async def test_layer_cleanup_failure_still_releases_connection(self):
    import core.lesson.runtime as runtime_module

    conn = _RepublishConn()
    released = []
    conn.request_lesson_preload_reset = AsyncMock(side_effect=RuntimeError("reset failed"))

    async def release_lesson_mode(*, reason):
        released.append(reason)

    conn.release_lesson_mode = release_lesson_mode
    undo = self._patch_backend(
        self._assignment(lesson_version=3, assignment_version=1),
        _build_manifest(),
    )
    try:
        with patch.object(
            runtime_module.LessonRuntime,
            "preload_only",
            new=AsyncMock(return_value=False),
        ):
            result = await runtime_module.maybe_start_lesson_on_connect(conn)
    finally:
        undo()

    self.assertIsNone(result)
    self.assertEqual(released, ["lesson_start_refused"])
```

- [ ] **Step 4: Run the three cleanup tests and confirm RED**

```bash
cd main/tbot-server
python -m pytest \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_terminal_startup_failure_clears_layers_before_release \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_failed_candidate_does_not_clear_previous_runtime_layers \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_layer_cleanup_failure_still_releases_connection \
  -q
```

Expected: at least the cleanup-order test FAILs because startup failure currently closes the runtime and returns without reset/release in that branch.

- [ ] **Step 5: Commit the RED cleanup tests**

```bash
git add main/tbot-server/tests/test_lesson_runtime.py
git commit -m "test(lesson): reproduce stale display after startup failure"
```

### Task 5: Implement bounded stale-layer cleanup

**Files:**
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Test: `main/tbot-server/tests/test_lesson_runtime.py`

- [ ] **Step 1: Add a best-effort cleanup helper inside `_maybe_start_lesson_on_connect_impl`**

```python
async def _clear_failed_candidate_display(reason: str) -> None:
    if republish_previous is not None:
        return
    reset = getattr(conn, "request_lesson_preload_reset", None)
    if callable(reset):
        try:
            cleared = await reset(
                assignment_id=str(assignment.get("assignmentId") or ""),
                lesson_id=str(assignment.get("lessonId") or ""),
                profile=str(profile),
            )
            _log("info" if cleared else "warning", f"lesson startup display reset {reason} cleared={cleared}")
        except Exception as exc:
            _log("warning", f"lesson startup display reset failed {reason}: {type(exc).__name__}")
    release_lesson = getattr(conn, "release_lesson_mode", None)
    if callable(release_lesson):
        await release_lesson(reason=reason)
```

Keep this helper after `republish_previous`, `assignment`, and `profile` are resolved so it has exact protocol identity and can preserve a prior runtime.

- [ ] **Step 2: Invoke cleanup in every terminal candidate failure with no fallback**

Call the helper before returning from:

```python
if not preloaded or runtime.state == S_FAILED:
    ...
    await _clear_failed_candidate_display("lesson_start_refused")
    return republish_previous
```

Also replace the duplicated direct `release_lesson_mode` blocks in the `LessonError` and generic exception handlers with the helper, preserving their existing reason strings.

- [ ] **Step 3: Run cleanup regressions GREEN**

```bash
cd main/tbot-server
python -m pytest \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_terminal_startup_failure_clears_layers_before_release \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_failed_candidate_does_not_clear_previous_runtime_layers \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_layer_cleanup_failure_still_releases_connection \
  -q
```

Expected: `3 passed`.

- [ ] **Step 4: Run related candidate and conversation ownership tests**

```bash
python -m pytest tests/test_lesson_runtime.py -q -k \
  "connect_preload or candidate_preload or start_protocol_crash or start_refused or sd_pack_pre_prepare_failure or mcp_discovery"
python -m pytest tests/test_connection_voice_provider_routing.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the cleanup implementation**

```bash
git add main/tbot-server/core/lesson/runtime.py main/tbot-server/tests/test_lesson_runtime.py
git commit -m "fix(lesson): clear stale display on startup failure"
```

### Task 6: Verify branch tip and write code evidence

**Files:**
- Create: `docs/qa/ad-hoc/2026-08-16-t54-mcp-reconnect-recovery.md`

- [ ] **Step 1: Run the complete lesson runtime and related suites**

```bash
cd main/tbot-server
python -m pytest tests/test_lesson_runtime.py -q
python -m pytest tests/test_lesson_conversation_integration.py tests/test_connection_voice_provider_routing.py -q
```

Expected: all pass with no new skips or failures.

- [ ] **Step 2: Run the ESP server standard suite from its required working directory**

```bash
cd main/tbot-server
python -m pytest tests -q
```

Expected: green, or only the explicitly documented unchanged environment baseline. Any new lesson/reconnect failure blocks merge.

- [ ] **Step 3: Write the evidence document**

Record:

```markdown
# T5.4 MCP Reconnect Recovery Evidence

## Repro
- Capture path and exact reconnect/MCP/ASSET_PACK_NOT_READY timestamps.

## Fix
- Connect-time readiness gate, 20-second bounded default.
- No SD sync before MCP ready.
- Preload-reset cleanup before conversation ownership on terminal startup failure.

## Tests
- Exact commands, pass counts, commit hashes, and unchanged baseline details.

## Deployment
- Backup path, deployed image/commit, smoke results, and MCP pin verification.

## Physical Re-test
- Assignment ID, capture path, renderer-v5 visual confirmation, power-cycle timestamps,
  post-reboot attestation/completion, Android progress, CP-7 operator confirmation,
  and `mic_loop_resumed ... reason=tts_stop_continue_listening` evidence.
```

- [ ] **Step 4: Route the assignment-nudge finding**

Append a section-5 row to `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md` assigning ownership to T1.2/T2.4/T7.4. State that supported assignment creation left an already-open ESP session idle until spoken `start_lesson`; do not modify assignment code in this branch.

- [ ] **Step 5: Commit evidence and finding reference**

```bash
git add docs/qa/ad-hoc/2026-08-16-t54-mcp-reconnect-recovery.md
git commit -m "docs(qa): record T5.4 reconnect recovery verification"
```

The root campaign plan is outside this repository and is tracked separately; preserve its existing content and add only the new row.

### Task 7: Review, gate, merge, and deploy

**Files:**
- Review all branch changes.
- Use `lesson-prod/GATE_LOG.md` through the repository gate tooling.

- [ ] **Step 1: Rebase the implementation branch on current ESP server main**

```bash
git fetch origin
git rebase main
```

Expected: clean rebase. Resolve only branch-owned files; stop on unrelated conflicts.

- [ ] **Step 2: Re-run Task 6 verification at the rebased tip**

Expected: same green result; update evidence with the exact tip commit.

- [ ] **Step 3: Run the T0.4 gate and merge protocol**

From `/Users/manhhodinh/Documents/TBOT`, use the existing gate scripts for task slug `t54-mcp-reconnect-recovery`, record RED then GREEN as required, and merge with a merge commit rather than squash.

- [ ] **Step 4: Push ESP server main**

```bash
git -C /Users/manhhodinh/Documents/TBOT/robot/esp32-server push origin main
```

Expected: remote main contains the merge commit.

- [ ] **Step 5: Deploy the ESP server safely to VPS**

Run the existing deployment sequence from the merged main:

```bash
deploy/backup-db.sh
deploy/deploy-vps.sh
deploy/smoke-vps.sh
```

Use the service-only deployment safeguards documented in `deploy/README.md`; never recreate the production database or unrelated containers. Record backup path, image, container, health, and MCP pin results.

### Task 8: Repeat the physical T5.4 acceptance run

**Files:**
- Update: `docs/qa/ad-hoc/2026-08-16-t54-mcp-reconnect-recovery.md`
- Update: `/Users/manhhodinh/Documents/TBOT/robot/docs/qa/ad-hoc/2026-08-16-t54-e2e-live.md`

- [ ] **Step 1: Start a fresh capture before assignment and reboot activity**

```bash
python scripts/lesson_e2e_live_capture.py
bash scripts/tbot_live_e2e_probe.sh
```

Use `/dev/cu.usbmodem1101` and production container `current-tbot-esp32-server-1`. Do not reuse the interrupted capture as final evidence.

- [ ] **Step 2: Create a fresh supported no-PIN assignment**

Use the current production admin proxy key through the documented supported endpoint without printing or storing the secret. Record assignment, lesson/version, child, and device IDs.

- [ ] **Step 3: Verify the normal start and complete visual/audio path**

Have the operator say `bắt đầu bài học` from normal distance. Confirm static high-quality background, static high-quality teaching object, animated Robot video, fly-in, walk, listen, teach, thinking, celebrate, exit, audible prompts, and physical arm action.

- [ ] **Step 4: Power-cycle during an active lesson step**

Record disconnect, boot, Wi-Fi/WebSocket reconnect, MCP-ready, SD-attestation, lesson restart, and completion timestamps. Require logs to show MCP ready before the first lesson `sync_to_sd` call.

- [ ] **Step 5: Obtain explicit CP-7 operator confirmation**

The operator must confirm that the conversation face never appears over or covers lesson content before or after reboot. A renderer ACK alone does not satisfy this checkpoint.

- [ ] **Step 6: Verify Android progress and terminal conversation**

Confirm parent progress updates within SLA and server/serial evidence contains:

```text
mic_loop_resumed ... reason=tts_stop_continue_listening
```

- [ ] **Step 7: Assemble and verify the fresh capture**

Expected: live capture verifier all green for applicable checkpoints, with intentional Wi-Fi loss remaining N/A by the documented T7.4 decision.

### Task 9: Re-test on main and close T5.4

**Files:**
- Update: `/Users/manhhodinh/Documents/TBOT/lesson-prod/t54-e2e-live.md`
- Update: `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`
- Update: evidence documents from Task 8.

- [ ] **Step 1: Re-run task verification against main in a throwaway worktree**

```bash
bash /Users/manhhodinh/Documents/TBOT/lesson-prod/scripts/verify-on-main.sh \
  /Users/manhhodinh/Documents/TBOT/robot/esp32-server -- \
  bash -lc 'cd main/tbot-server && python -m pytest tests/test_lesson_runtime.py tests/test_lesson_conversation_integration.py tests/test_connection_voice_provider_routing.py -q'
```

Expected: green on main, not only on the feature branch.

- [ ] **Step 2: Re-run production smoke and record deployed-main identity**

Expected: healthy VPS container running the merged main commit, with MCP pins unchanged.

- [ ] **Step 3: Remove only the merged implementation and design worktrees**

Before removal, verify clean status and ancestry:

```bash
git merge-base --is-ancestor lesson-prod/t54-mcp-reconnect-recovery main
git worktree remove .worktrees/t54-mcp-reconnect-recovery
git branch -d lesson-prod/t54-mcp-reconnect-recovery
git merge-base --is-ancestor lesson-prod/t54-mcp-reconnect-recovery-design main
git worktree remove .worktrees/t54-mcp-reconnect-recovery-design
git branch -d lesson-prod/t54-mcp-reconnect-recovery-design
```

Do not remove any older T5.4 worktree unless it is clean, merged, and explicitly covered by the T5.4 Ship cleanup audit.

- [ ] **Step 4: Set T5.4 DONE only after all evidence is present**

Update both status locations with the final evidence link, merged/deployed commit, physical CP-7 confirmation, power-cycle proof, main re-test, and worktree cleanup result.

- [ ] **Step 5: Commit and push final closeout documentation in its owning repository**

Expected: T5.4 reads DONE in both files and every Ship checklist item has concrete evidence.
