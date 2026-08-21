# T5.4 Google Live Lesson Handoff Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Google Live fully quiescent from an accepted lesson-start intent through assignment pull and SD attestation, then transfer to lesson ownership or safely restore conversation.

**Architecture:** Add a generation-tokened provisional handoff gate owned by `ConnectionHandler`, acquired by the Google Live transition and inherited by the nudge/spoken startup task. Audio routing, Live model output, and tool admission consult the global active gate; runtime startup releases the matching token after successful lesson-mode transfer or failed/cancelled startup. Existing session modes and the 15-second exact-condition SD-sync retry remain unchanged.

**Tech Stack:** Python 3.11, asyncio, aiohttp, unittest/pytest, existing Google Live interaction controller and lesson runtime.

---

## File Map

- `main/tbot-server/core/connection.py`: gate state/token API, mic suppression, disconnect cleanup, and provider restoration delegation.
- `main/tbot-server/core/voice/session_provider/google_live.py`: acquire during the hard handoff, preserve manual firmware listening, suppress unrelated tools, and restore Live after failed startup.
- `main/tbot-server/core/voice/google_live/audio_bridge.py`: treat provisional handoff as lesson-owned model-output suppression.
- `main/tbot-server/core/api/lesson_nudge_handler.py`: keep the protected nudge tied to the acquired token and release a replaced connection.
- `main/tbot-server/core/lesson/runtime.py`: capture the inherited handoff token and release it exactly once after success/failure/cancellation.
- `main/tbot-server/plugins_func/functions/start_lesson.py`: ensure the scheduled spoken-start task inherits/coalesces the active gate and does not strand it on scheduling failure.
- `main/tbot-server/tests/test_lesson_nudge_handler.py`: protected-nudge lifetime, replacement, timeout, and non-Google regression.
- `main/tbot-server/tests/test_google_live_provider_edges.py`: firmware manual-stop, provider acquire/release, input/output state, cancellation, and restoration.
- `main/tbot-server/tests/test_google_live_tool_calls.py`: unrelated tool rejection and `start_lesson` coalescing while gated.
- `main/tbot-server/tests/test_lesson_voice_output_discipline.py`: bridge suppression during provisional handoff.
- `main/tbot-server/tests/test_connection_edges.py`: raw mic consumption and disconnect cleanup.
- `main/tbot-server/tests/test_start_lesson_tool.py`: scheduled spoken-start lifetime and failure cleanup.
- `main/tbot-server/tests/test_lesson_runtime.py`: success transfer and all unsuccessful runtime exits release the matching token.

### Task 1: RED — Connection Gate and Realtime Suppression

**Files:**
- Test: `main/tbot-server/tests/test_connection_edges.py`
- Test: `main/tbot-server/tests/test_lesson_voice_output_discipline.py`
- Test: `main/tbot-server/tests/test_google_live_tool_calls.py`

- [ ] **Step 1: Add failing connection-gate tests**

Add tests that construct a minimal `ConnectionHandler`, acquire a handoff token, and prove:

```python
token = conn.begin_lesson_start_handoff(reason="spoken_start")
assert token == conn.lesson_start_handoff_token()
assert conn.lesson_start_handoff_active()
assert await conn._route_audio_message_impl(b"late-mic") is True
voice_provider.handle_audio_bytes.assert_not_awaited()

assert not await conn.release_lesson_start_handoff(token + 1, outcome="stale")
assert conn.lesson_start_handoff_active()
assert await conn.release_lesson_start_handoff(token, outcome="failed", restore_conversation=False)
assert not conn.lesson_start_handoff_active()
```

Add a disconnect test that acquires the gate, invokes connection teardown, and asserts the active token is cleared without reopening the provider.

- [ ] **Step 2: Add failing bridge and tool tests**

In the bridge discipline suite, set `conn.lesson_start_handoff_active = lambda: True` and assert transcript/audio/model events return `True` from `_should_drop_lesson_model_output`. In the tool suite, hold the gate and assert `change_volume` returns `LESSON_MODE_TOOL_BLOCKED`, while a duplicate `start_lesson` is admitted only through the existing coalescing path.

- [ ] **Step 3: Run RED tests**

Run:

```bash
.venv311/bin/python -m pytest \
  tests/test_connection_edges.py \
  tests/test_lesson_voice_output_discipline.py \
  tests/test_google_live_tool_calls.py -q
```

Expected: new tests fail because the connection gate API does not exist and provisional handoff is not included in the guards.

- [ ] **Step 4: Commit RED tests**

```bash
git add main/tbot-server/tests/test_connection_edges.py \
  main/tbot-server/tests/test_lesson_voice_output_discipline.py \
  main/tbot-server/tests/test_google_live_tool_calls.py
git commit -m "test: cover provisional lesson handoff guards"
```

### Task 2: GREEN — Connection Gate and Guards

**Files:**
- Modify: `main/tbot-server/core/connection.py`
- Modify: `main/tbot-server/core/voice/google_live/audio_bridge.py`
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`

- [ ] **Step 1: Add generation-tokened gate state**

Initialize the following connection state next to the existing lesson pull fields:

```python
from contextvars import ContextVar

self._lesson_start_handoff_generation = 0
self._lesson_start_handoff_active_token = None
self._lesson_start_handoff_context = ContextVar(
    f"lesson_start_handoff_{id(self):x}", default=None
)
```

Add methods with these contracts:

```python
def begin_lesson_start_handoff(self, *, reason: str) -> int:
    if self._lesson_start_handoff_active_token is None:
        self._lesson_start_handoff_generation += 1
        self._lesson_start_handoff_active_token = self._lesson_start_handoff_generation
    token = self._lesson_start_handoff_active_token
    self._lesson_start_handoff_context.set(token)
    return token

def lesson_start_handoff_token(self):
    token = self._lesson_start_handoff_context.get()
    return token if token == self._lesson_start_handoff_active_token else None

def lesson_start_handoff_active(self) -> bool:
    return self._lesson_start_handoff_active_token is not None

async def release_lesson_start_handoff(
    self, token, *, outcome: str, restore_conversation: bool
) -> bool:
    if token is None or token != self._lesson_start_handoff_active_token:
        return False
    self._lesson_start_handoff_active_token = None
    if self._lesson_start_handoff_context.get() == token:
        self._lesson_start_handoff_context.set(None)
    if restore_conversation:
        restore = getattr(self.voice_provider, "restore_after_lesson_start_handoff", None)
        if callable(restore):
            await restore(outcome=outcome)
    return True
```

Use structured logs for acquire/coalesce/release/stale release. Clear the active token during `_close_connection_owned_mcp_callers()` with `restore_conversation=False`.

- [ ] **Step 2: Consume late mic frames**

At the top of `_route_audio_message_impl()`, before dormant/conversation routing, add:

```python
if self.lesson_start_handoff_active():
    return True
```

This must not set `USER_STREAMING`, update Live activity, or feed classic ASR.

- [ ] **Step 3: Drop provisional model output**

Extend bridge ownership detection:

```python
handoff_active = getattr(self.conn, "lesson_start_handoff_active", None)
in_lesson = in_lesson or (callable(handoff_active) and handoff_active())
```

Retain existing exceptions only for required cancellation/control events; do not allow prompt audio during provisional handoff.

- [ ] **Step 4: Block unrelated tools**

Compute `in_lesson_or_handoff` in `_handle_tool_call_event()`. When only provisional handoff is active, reject every tool except `start_lesson`; allow `start_lesson` to reach the current duplicate/coalescing logic. Preserve lesson-runtime generation checks for durable lesson mode.

- [ ] **Step 5: Run GREEN tests and commit**

Run the Task 1 command. Expected: all selected tests pass.

```bash
git add main/tbot-server/core/connection.py \
  main/tbot-server/core/voice/google_live/audio_bridge.py \
  main/tbot-server/core/voice/session_provider/google_live.py
git commit -m "feat: add provisional lesson handoff guards"
```

### Task 3: RED/GREEN — Acquire and Restore Google Live Handoff

**Files:**
- Test: `main/tbot-server/tests/test_google_live_provider_edges.py`
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`

- [ ] **Step 1: Write failing transition tests**

Add tests asserting `transition_to_lesson_start()`:

```python
assert await provider.transition_to_lesson_start()
conn.begin_lesson_start_handoff.assert_called_once_with(reason="lesson_start_intent")
bridge.stop_output_for_lesson.assert_awaited_once()
assert bridge.sent[-1]["continue_listening"] is False
assert bridge.sent[-1]["listen_mode"] == "manual"
assert provider._interaction.state is InteractionState.LISTENING
assert conn.lesson_start_handoff_active()
```

Add timeout coverage proving the matching token is released and Live is restored; add `restore_after_lesson_start_handoff()` tests proving it clears `client_abort`, selects `LISTENING`, and opens a user audio window only for a connected provider.

- [ ] **Step 2: Run focused RED tests**

```bash
.venv311/bin/python -m pytest tests/test_google_live_provider_edges.py -q
```

Expected: new acquire/restore assertions fail.

- [ ] **Step 3: Implement acquire and restoration**

At the beginning of `transition_to_lesson_start()`, call the connection acquire API and retain its returned token. Continue using `_begin_user_interrupt("lesson_start_intent")`, which already selects `stop_output_for_lesson()` and therefore sends manual/no-continue firmware state. If the bounded realtime transition fails, release that exact token with `restore_conversation=True` before returning `False`.

Add:

```python
async def restore_after_lesson_start_handoff(self, *, outcome: str) -> None:
    stop_event = getattr(self.conn, "stop_event", None)
    if stop_event is not None and stop_event.is_set():
        return
    if self._client is not None and not getattr(self._client, "connected", False):
        return
    self._interaction.transition(InteractionState.LISTENING)
    self.conn.client_abort = False
    await self._open_user_audio_window("lesson_start_failed")
```

Do not call this method on successful transfer to `SessionMode.LESSON`.

- [ ] **Step 4: Remove premature reopen after spoken dispatch**

In `_dispatch_lesson_start_intent()`, keep the handoff gate held after the tool schedules the pull. Remove the unconditional post-dispatch transition/`client_abort` clear/`_open_user_audio_window("lesson_start")`; runtime cleanup now owns those actions. If tool dispatch fails before a pull task exists, release the inherited token with restoration.

- [ ] **Step 5: Run tests and commit**

Run the focused provider suite plus `tests/test_google_live_bargein.py`. Expected: pass.

```bash
git add main/tbot-server/tests/test_google_live_provider_edges.py \
  main/tbot-server/core/voice/session_provider/google_live.py
git commit -m "fix: hold Google Live through lesson startup"
```

### Task 4: RED/GREEN — Protected Nudge and Spoken Task Lifetime

**Files:**
- Test: `main/tbot-server/tests/test_lesson_nudge_handler.py`
- Test: `main/tbot-server/tests/test_start_lesson_tool.py`
- Modify: `main/tbot-server/core/api/lesson_nudge_handler.py`
- Modify: `main/tbot-server/plugins_func/functions/start_lesson.py`

- [ ] **Step 1: Write failing nudge lifetime tests**

Block `maybe_start_lesson_on_connect()` on an event and assert the connection gate remains active until it completes. For reconnect replacement, assert the old connection's token is released without restoration before transitioning the replacement. For a transition timeout, assert no pull runs and the transition itself releases its token.

- [ ] **Step 2: Write failing spoken scheduling tests**

Call the registered `start_lesson` while a handoff token is current. Inside the scheduled pull, assert `lesson_start_handoff_token()` equals the acquired token, proving asyncio context inheritance. Assert duplicates retain the same task/token. Simulate `loop.create_task()` failure and assert the active token is released/restored rather than stranded.

- [ ] **Step 3: Run RED tests**

```bash
.venv311/bin/python -m pytest \
  tests/test_lesson_nudge_handler.py \
  tests/test_start_lesson_tool.py -q
```

Expected: lifetime/replacement/scheduling cleanup tests fail.

- [ ] **Step 4: Implement nudge replacement cleanup**

In `_transition_current_connection()`, after a successful transition capture the connection token. If the connection is replaced, release that token with `restore_conversation=False` before resolving and transitioning the current connection. The final active connection keeps its token through the awaited runtime pull.

- [ ] **Step 5: Implement scheduling cleanup**

Keep the existing task coalescing. Wrap task creation so a synchronous scheduling error releases the current handoff through a small async cleanup scheduled on the active loop, then returns the existing friendly failure response. Do not release from the normal done callback; runtime owns the terminal release.

- [ ] **Step 6: Run tests and commit**

Run the Task 4 command. Expected: pass.

```bash
git add main/tbot-server/tests/test_lesson_nudge_handler.py \
  main/tbot-server/tests/test_start_lesson_tool.py \
  main/tbot-server/core/api/lesson_nudge_handler.py \
  main/tbot-server/plugins_func/functions/start_lesson.py
git commit -m "fix: preserve handoff across lesson start dispatch"
```

### Task 5: RED/GREEN — Runtime Transfer and Fail-safe Release

**Files:**
- Test: `main/tbot-server/tests/test_lesson_runtime.py`
- Modify: `main/tbot-server/core/lesson/runtime.py`

- [ ] **Step 1: Add failing runtime lifecycle tests**

Cover these exact exits with a matching inherited token:

```python
# success
assert events.index("enter_lesson_mode") < events.index("release_success")
release.assert_awaited_once_with(token, outcome="lesson_started", restore_conversation=False)

# refused preload / backend failure / unrelated MCP failure
release.assert_awaited_once_with(token, outcome=expected_outcome, restore_conversation=True)

# cancellation
task.cancel()
with pytest.raises(asyncio.CancelledError):
    await task
release.assert_awaited_once_with(token, outcome="cancelled", restore_conversation=True)
```

Add a stale-token race: cancellation of an older task cannot clear a newer gate generation.

- [ ] **Step 2: Run focused RED tests**

```bash
.venv311/bin/python -m pytest tests/test_lesson_runtime.py -q
```

Expected: lifecycle release assertions fail.

- [ ] **Step 3: Implement single-owner runtime cleanup**

At `maybe_start_lesson_on_connect()` entry, capture `handoff_token = conn.lesson_start_handoff_token()` when available. Wrap the serialized implementation call so:

```python
started = False
outcome = "lesson_start_failed"
try:
    runtime = await _maybe_start_lesson_on_connect_impl(conn)
    started = runtime is not None and normalize_session_mode(conn.session_mode) == SessionMode.LESSON
    status = getattr(conn, "lesson_start_status", None) or {}
    outcome = "lesson_started" if started else (status.get("code") or "lesson_start_failed")
    return runtime
except asyncio.CancelledError:
    outcome = "cancelled"
    raise
finally:
    if handoff_token is not None:
        await conn.release_lesson_start_handoff(
            handoff_token,
            outcome=outcome,
            restore_conversation=not started,
        )
```

Keep release outside any branch-specific teardown so every return and exception is covered. Preserve the existing lock, activity lease, preload ordering, exact transient retry string, and 15-second deadline.

- [ ] **Step 4: Run tests and commit**

Run the focused runtime suite. Expected: pass.

```bash
git add main/tbot-server/tests/test_lesson_runtime.py \
  main/tbot-server/core/lesson/runtime.py
git commit -m "fix: transfer or release lesson handoff ownership"
```

### Task 6: Regression Verification and Isolated Deployment

**Files:**
- Verify only; no merge or worktree cleanup.

- [ ] **Step 1: Run targeted regression suites**

```bash
.venv311/bin/python -m pytest \
  tests/test_lesson_nudge_handler.py \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_bargein.py \
  tests/test_google_live_tool_calls.py \
  tests/test_lesson_voice_output_discipline.py \
  tests/test_connection_edges.py \
  tests/test_start_lesson_tool.py \
  tests/test_lesson_runtime.py -q
```

Expected: all pass, including the pre-existing transient retry and unrelated-error fail-closed cases.

- [ ] **Step 2: Run the repository's full relevant verification**

Use the server's documented full pytest command from this isolated worktree. Expected: exit 0. Run `git diff --check` and verify `git status --short --branch` contains only intentional committed work.

- [ ] **Step 3: Review the diff against the approved spec**

Confirm no early `SessionMode.LESSON`, no widened retry matcher, no new timeout, no classic-path change, and no unguarded gate exit. Record exact test counts and commit tip.

- [ ] **Step 4: Deploy from the isolated worktree**

Use the repository's documented ESP-server production deployment procedure with the isolated branch tip. Do not deploy mobile/backend, merge, clean worktrees, reset the robot, create an assignment, or navigate Android during this step.

- [ ] **Step 5: Verify deployed identity and health**

Confirm the deployed process/image identifies the isolated fix tip and production health checks pass before touching the robot.

### Task 7: Strict H1 Physical Run and Conditional C1

**Files:**
- Update evidence under `robot/docs/evidence/t54-live-20260819-final-closeout/`.
- Update `LESSON_PRODUCTION_PLAN.md` and the required closeout surfaces only from frozen evidence.

- [ ] **Step 1: Re-read physical runbooks and verify exclusivity**

Read the four required H1 files completely. Confirm ADB `efc5314f`, robot serial ownership, deployed ESP identity/health, Wi-Fi/WebSocket, cached SD readiness, and no competing deploy/reset/assignment/Android session.

- [ ] **Step 2: Establish a fresh Parent dwell**

Keep Parent Today continuously foreground for more than 15 minutes. Restart the timer if focus changes. Do not manually refresh Parent.

- [ ] **Step 3: Arm capture before reset**

Start `lesson_e2e_live_capture.py` with `--reset-on-start` and exact identity checks before robot reset. Wait for robot Wi-Fi/WebSocket and cached SD readiness, then start the four-worker Parent collector and answer helper.

- [ ] **Step 4: Run exactly one fresh assignment**

Create one direct no-PIN `w02-feelings` v7 assignment. Use spoken `bắt đầu bài học`; use protected nudge only after the documented 30-second automatic handoff timeout.

- [ ] **Step 5: Freeze and audit H1 evidence**

Require assignment/session IDs, automatic s1-s9 PNG/XML, `captures.tsv` exactly nine rows with `11,22,33,44,56,67,78,89,100`, canonical single `step_started` s1-s9 and raw latency table, audio/three layers/MCP motion, `lesson_completed persisted=true`, assignment `COMPLETED`, return to `CONVERSATION` with normal face, renderer 101/101, and production probe exit 0.

- [ ] **Step 6: Route failure or execute C1**

If any item fails, preserve all evidence, add/update the exact finding, and leave T5.4 `IN_PROGRESS`; do not run another assignment or C1. Only if every H1 item is proven green, execute the C1 Ship checklist in `lesson-prod/t54-e2e-live.md`, fill accepted IDs/latencies, close findings justified by evidence, verify at tip, merge with the required no-squash gate, deploy if required, verify main in a throwaway worktree, and mark both status surfaces `DONE` only after every C1 gate passes.
