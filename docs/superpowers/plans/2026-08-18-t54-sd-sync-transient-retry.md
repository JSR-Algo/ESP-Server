# T5.4 Assignment SD Sync Transient Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retry the single transient firmware SD-sync busy rejection within the existing foreground timeout without weakening fail-closed error handling.

**Architecture:** Keep the existing foreground admission deadline as the sole retry budget. The foreground operation loops around `call_sync_once()`, retrying only the exact firmware busy/worker-unavailable exception after the configured poll delay; all unrelated errors escape immediately into the existing terminal handling.

**Tech Stack:** Python 3, asyncio, unittest/pytest, MCP lesson runtime.

---

### Task 1: Add regression coverage

**Files:**
- Modify: `main/tbot-server/tests/test_lesson_runtime.py`

- [x] **Step 1: Add a transient-success test**

Add an async test whose patched `call_mcp_tool` raises
`Exception("lesson asset sync busy or worker unavailable")` on its first call and
returns the existing valid firmware attestation on its second call. Configure a short
foreground timeout and poll, then assert the runtime returns `True` and made exactly
two calls.

- [x] **Step 2: Add an unknown-error negative test**

Add an async test whose patched `call_mcp_tool` always raises
`Exception("unexpected sync failure")`. Assert the runtime returns `False` and the
tool was called exactly once.

- [x] **Step 3: Run the focused tests and verify RED**

Run:

```bash
python3 -m pytest -q tests/test_lesson_runtime.py -k 'transient_firmware_busy or unrelated_mcp_error'
```

Expected: the transient-success test fails because only one call is made; the
unknown-error test passes.

### Task 2: Implement the bounded retry

**Files:**
- Modify: `main/tbot-server/core/lesson/runtime.py`
- Test: `main/tbot-server/tests/test_lesson_runtime.py`

- [x] **Step 1: Add the narrow exception classifier**

Inside `_sync_sd_asset_pack_to_robot()`, classify only exceptions whose string contains
`lesson asset sync busy or worker unavailable` as retryable firmware admission races.

- [x] **Step 2: Loop inside the existing foreground deadline**

After the existing busy guard becomes idle, call `call_sync_once()`. If the exact
transient exception occurs, re-check the existing deadline, sleep for at most
`busy_poll`, and return to the existing busy guard. If the deadline expires, raise the
existing realtime busy timeout error. Re-raise every other exception unchanged.

- [x] **Step 3: Run the focused tests and verify GREEN**

Run:

```bash
python3 -m pytest -q tests/test_lesson_runtime.py -k 'transient_firmware_busy or unrelated_mcp_error'
```

Expected: 2 passed.

- [x] **Step 4: Run targeted regression suites**

Run:

```bash
python3 -m pytest -q tests/test_lesson_runtime.py tests/test_lesson_voice_nonregression.py tests/test_lesson_nudge_handler.py
```

Expected: all collected tests pass.

- [x] **Step 5: Run full server verification**

Run from `main/tbot-server`:

```bash
python3 -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit the isolated fix**

```bash
git add main/tbot-server/core/lesson/runtime.py main/tbot-server/tests/test_lesson_runtime.py docs/superpowers/plans/2026-08-18-t54-sd-sync-transient-retry.md
git commit -m "fix: retry transient lesson SD sync admission"
```

### Task 3: Deploy and run strict physical closeout

**Files:**
- Preserve evidence under the existing T5.4 evidence tree in a new pass directory.

- [ ] **Step 1: Build and deploy only the isolated ESP-server branch**

Use the repository's established production image/deploy workflow, record the immutable
image tag/digest, and confirm the VPS container is healthy. Do not merge or clean any
worktree.

- [ ] **Step 2: Run the strict pre-assignment sequence**

Keep Parent Today continuously foreground for more than 900 seconds, start the
identity-pinned capture with `--reset-on-start` before the reset, wait for Wi-Fi,
WebSocket, and cached SD sync readiness, then start the four-worker collector and
answer helper.

- [ ] **Step 3: Create exactly one fresh assignment and complete it**

Create one direct no-PIN `w02-feelings` v7 assignment, use the spoken trigger
`bắt đầu bài học`, and use the protected nudge only if the documented 30-second
automatic handoff timeout expires.

- [ ] **Step 4: Verify every acceptance artifact**

Require assignment/session IDs, automatic `s1.png`-`s9.png` and XML, exactly nine
`captures.tsv` rows with `11,22,33,44,56,67,78,89,100`, exactly one canonical
`step_started` per step with raw latency calculations, audio/three-layer/MCP motion
evidence, persisted completion, assignment `COMPLETED`, runtime `CONVERSATION` with
normal face, definitive renderer verifier `101/101`, and production probe exit `0`.
If any item fails, freeze evidence and leave T5.4 `IN_PROGRESS`.
