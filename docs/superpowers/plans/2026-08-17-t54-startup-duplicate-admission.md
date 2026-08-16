# T5.4 Startup Duplicate Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve one spoken lesson startup across repeated recognized trigger phrases until SD attestation and renderer start complete.

**Architecture:** Suppress duplicates at the Google Live admission boundary before response-generation mutation, and coalesce duplicate tool calls using a task-origin marker on the connection. Preserve explicit replacement of reconnect/background pulls and leave SD synchronization contracts untouched.

**Tech Stack:** Python 3.11, asyncio, unittest/pytest, Google Live session provider, ESP lesson runtime.

---

### Task 1: Lock the physical regression with RED tests

**Files:**
- Modify: `main/tbot-server/tests/test_google_live_provider_edges.py`
- Modify: `main/tbot-server/tests/test_start_lesson_tool.py`

- [ ] Add a provider test whose fixed duplicate window has elapsed while `lesson_pull_task` remains pending; assert no realtime transition or second tool dispatch occurs.
- [ ] Add a tool test that calls `start_lesson` twice while the first spoken task is pending; assert the same task remains tracked, it is not cancelled, and the pull runs once.
- [ ] Run the two exact tests and verify they fail for the duplicate dispatch/cancellation behavior.

### Task 2: Implement pending-start coalescing

**Files:**
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`
- Modify: `main/tbot-server/plugins_func/functions/start_lesson.py`

- [ ] Add a narrow pending-task predicate to Google Live and suppress the duplicate before `transition_to_lesson_start`.
- [ ] Mark tasks scheduled by `start_lesson` as spoken-start owned and return the normal RECORD response without scheduling/cancelling when that marked task is still running.
- [ ] Clear the marker safely in the owning done callbacks; keep unmarked prior-task cancellation unchanged.
- [ ] Run the exact RED tests and verify they pass.

### Task 3: Verify, evidence, and ship

**Files:**
- Create: `main/tbot-server/docs/qa/ad-hoc/2026-08-17-t54-startup-duplicate-admission.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`
- Modify: `/Users/manhhodinh/Documents/TBOT/robot/docs/qa/ad-hoc/2026-08-16-t54-e2e-live.md`

- [ ] Run focused provider/tool/runtime tests and the full ESP suite.
- [ ] Record physical RED evidence, code diff, GREEN commands, and release hashes.
- [ ] Run the gate, merge to main, push, deploy, smoke, and verify-on-main.
- [ ] Create a fresh no-PIN assignment and repeat the physical lesson plus mid-step power cycle through completion.
- [ ] Remove merged worktrees/branches only after main/deployed physical verification succeeds.

