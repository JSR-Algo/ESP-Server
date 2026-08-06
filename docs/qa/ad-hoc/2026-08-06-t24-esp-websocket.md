# adhoc-2026-08-06-t24-esp-websocket — ESP WebSocket lifecycle & reconnection (T2.4)

**Repo:** `robot/esp32-server` · **Work dir:** `main/tbot-server` ·
**Branch:** `lesson-prod/t24-esp-websocket` · **Base:** `0f44fa6e` · **Date:** 2026-08-06

Task file: `lesson-prod/t24-esp-websocket.md`. Scope files:
`core/websocket_server.py`, `core/connection.py`, `core/lesson/forwarder.py`,
`core/http_server.py` (+ `core/connection_registry.py`, the registry
`websocket_server.py` supersedes through — see §6).

Google Live and `classic_pipeline` are untouched: nothing in this change reads or
writes a voice-provider path. The one connection-level state added
(`is_superseded`) is set only by the accept path.

---

## 1. Summary

Two defects fixed, both on the reconnect path, both with a failing repro first:

| # | Defect | Severity | Fix |
|---|---|---|---|
| D1 | A device that reconnects leaves its previous `ConnectionHandler` alive with an OPEN socket, its own lesson runtime and its own event forwarder | HIGH | supersede on registry replace: mark synchronously, close the old socket off the accept path |
| D2 | A silently dead peer keeps a RUNNING lesson parked for the 61-minute idle timeout | HIGH | lesson-scoped inbound-silence budget (default 60 s), suspended while an SD pack sync is in flight |

Plus two hardening changes the deep-dive checklist called for: a bounded lesson
event queue (§4.6) and per-connection fault isolation in the HTTP fan-out
endpoints (§4.5).

Repro: `lesson-prod/repros/t24.sh` (+ `lesson-prod/repros/t24/`).
Regression suite: `main/tbot-server/tests/test_ws_reconnect_lifecycle.py` (23 tests).

---

## 2. D1 — stale socket on reconnect (the 2026-07-06 bug class)

### Repro (RED on base `0f44fa6e`)

`tests/test_ws_reconnect_lifecycle.py::RealSocketSupersessionTest::test_reconnect_closes_the_old_socket_and_silences_its_sends`
— two genuine `websockets` clients connect to a genuine `websockets.serve`
acceptor running the real `WebSocketServer._handle_connection` →
`ConnectionHandler.handle_connection`, both carrying `device-id
AA:BB:CC:DD:EE:24`. The test then waits for the first client to observe a close.

```
FAILED tests/test_ws_reconnect_lifecycle.py::RealSocketSupersessionTest::
       test_reconnect_closes_the_old_socket_and_silences_its_sends
E   TimeoutError            # first.recv() never raised ConnectionClosed
```

The first socket stays open. Server-side, `lesson_connections[device_id]` now
points at handler B while handler A is still running its read loop, still holding
`lesson_runtime` and `LessonEventForwarder`, and still has a live socket its
runtime writes to via `LessonRuntime._default_send` → `self.conn.websocket.send`
(`core/lesson/runtime.py:5116-5119`). Nothing evicts it until its own
`timeout_seconds` (≥ 61 min) elapses.

Consequences, all observed in the pre-fix code path:

* two lesson runtimes for one assignment — B's pull-on-connect builds a fresh one
  (`maybe_start_lesson_on_connect`) while A's keeps stepping;
* two `LessonEventForwarder`s POSTing progress for that assignment — the
  duplicate-progress case on the checklist;
* the abandoned socket keeps receiving `lesson_*` frames the child will never see,
  which is the "stale-WS listening state" shape of the 2026-07-06 serial captures
  (`robot/docs/qa/ad-hoc/2026-07-06-esp-listening-stale-ws/`).

This is not an exotic path. The firmware reopens its passive lesson socket on
every drop it detects itself — `passive_lesson_ws_dropped_unexpected -> passive
reconnect`, `passive_liveness_reconnect_pending`,
`SchedulePassiveLessonReconnect` (`TBOT-Firmware/main/application.cc:3875, 4412`)
— so a second socket arriving while the first is still registered is the normal
reconnect, not an edge case.

### Fix

`core/connection_registry.py` — `replace()` now returns the entry it displaced
(`None` for a first bind or a no-op rebind). The registry still owns only the
mapping; socket lifecycle stays with the caller.

`core/websocket_server.py` — `_handle_connection` supersedes whatever came back:

* `_supersede_connection()` calls `displaced.mark_superseded()` **synchronously**,
  so by the time the new handler is registered nothing owned by the old one can
  put another byte on the wire;
* the socket close is scheduled as a task (`_close_superseded_connection`, code
  `1001`, reason `superseded by newer connection`) because a half-open peer drags
  the closing handshake out to its timeout, which must not delay the socket that
  is actually alive. Supersede tasks are tracked and drained by `drain()`.

`core/connection.py` — `ConnectionHandler.mark_superseded()` sets `is_superseded`,
sets `stop_event`, and swaps `self.websocket` for `_SupersededWebSocket`, whose
`send`/`ping` raise `SupersededConnectionError` and whose `close` is a no-op. The
real socket is captured by the caller before marking, so teardown still closes it.
`mark_superseded` is idempotent.

The old handler's own `finally` then runs the normal teardown
(`_save_and_close` → `close`), and `remove_if_current` correctly refuses to evict
the replacement.

### GREEN

```
tests/test_ws_reconnect_lifecycle.py::RealSocketSupersessionTest::
    test_reconnect_closes_the_old_socket_and_silences_its_sends PASSED
```

asserting, in order: (1) the first client observes `ConnectionClosed` with code
`1001` and its socket refuses further sends; (2) the displaced handler reports
`is_superseded` and raises `SupersededConnectionError` on any send; (3) the
surviving socket is untouched and still carries a `lesson_step` frame to client B.

---

## 3. D2 — silent peer death during a lesson

### Repro (RED on base)

`tests/test_ws_reconnect_lifecycle.py::LessonPeerSilenceTest::test_running_lesson_with_a_silent_peer_closes_inside_the_budget`
— a handler with `lesson_runtime.state == "RUNNING"` and 120 s of inbound silence,
driven through the real `_check_timeout` loop:

```
E   AssertionError: 0 != 1        # nothing closed the socket
```

`ConnectionHandler.timeout_seconds` is
`max(close_connection_no_voice_time + 60, 61*60)` — with the shipped
`close_connection_no_voice_time: 900` the 61-minute floor always wins, so the
configured value is inert and a half-open socket holds a running lesson for up to
an hour. `websockets.serve(..., ping_interval=None)` deliberately disables
protocol keepalive (attended SD sync monopolises the device's websocket callback
for minutes), so there is no other liveness signal.

### Why a tighter budget is safe *during a lesson* (firmware contract)

* `PassiveWebsocketLiveness` pings every **2 s** and fails its own pong probe at
  **10 s** (`TBOT-Firmware/main/protocols/passive_websocket_liveness.h:52-54`),
  polled on every clock tick while the passive lesson channel is open
  (`application.cc:917-941`).
* The one window in which the firmware deliberately goes silent — hashing an SD
  asset pack — is **refused outright while a lesson runtime is active**:
  `BeginLessonAssetSyncQuiet()` returns false when `lesson_runtime_active_` is set
  (`application.cc:3934-3952`). Lesson-active and sync-quiet cannot overlap.
* Any voice turn inside a lesson produces continuous inbound audio frames.

So under a RUNNING/PRELOADING lesson runtime, inbound silence past a few ping
periods means the peer is gone, not busy. 60 s is 30 ping periods of margin.

### Fix

`core/connection.py`:

* `_lesson_peer_silence_timeout_sec()` reads `lesson.peer_silence_timeout_sec`
  (default `60.0`; `0`/non-numeric disables the watchdog);
* `_lesson_peer_silence_watchdog_armed()` requires `_lesson_runtime_active()` and
  **no** in-flight `sd_pack_sync_task` (the server-side half of the firmware's
  sync-quiet window);
* `_check_timeout()` closes on either budget, logging `lesson_peer_silent` with
  device/session/idle/budget for the peer-silence case and leaving the original
  `Connection timeout` line for the long idle timeout.

`config.yaml` documents the key under `lesson:`.

Detection budget for a dead peer mid-lesson: **≤ 70 s** (60 s budget + the loop's
10 s poll) versus **3660 s** before.

### GREEN

| Test | Asserts |
|---|---|
| `test_running_lesson_with_a_silent_peer_closes_inside_the_budget` | 120 s silence under a RUNNING lesson → socket closed |
| `test_silence_inside_the_budget_is_left_alone` | 5 s silence → no close |
| `test_sd_pack_sync_in_flight_suspends_the_watchdog` | 600 s silence with `sd_pack_sync_task` pending → no close |
| `test_no_lesson_runtime_falls_back_to_the_long_idle_timeout` | 120 s silence, no runtime → no close |
| `test_the_long_idle_timeout_still_fires_without_a_lesson` | 4000 s silence, no runtime → closed (61-min path intact) |
| `test_budget_is_configurable_and_disablable` | `0` and garbage disable; default is 60 |
| `test_preloading_lesson_counts_as_an_active_runtime` | PRELOADING arms the watchdog |

---

## 4. Deep-dive case checklist

| # | Case | Verdict | Evidence |
|---|---|---|---|
| 1 | Disconnect at every lifecycle stage × resume behaviour | **PASS (matrix in §5)** | `ReconnectMatrixTeardownTest`, `ReconnectMatrixResumeTest` |
| 2 | Duplicate connection: old socket superseded, closed, can never receive lesson messages | **FIXED (D1)** | §2 |
| 3 | Half-open TCP: heartbeat detects within budget; state not stuck LISTENING | **FIXED (D2)** | §3 |
| 4 | Server restart mid-lesson: pending store restores what the contract promises | **PASS** | §4.4 |
| 5 | Send to closed/stale socket → handled, no unhandled exception in broadcast paths | **FIXED** | §4.5 |
| 6 | Slow consumer: backpressure or bounded queue, no unbounded memory | **FIXED** | §4.6 |
| 7 | Frame-size budget enforced for the largest real step | **PASS (pre-existing)** | §4.7 |
| 8 | Out-of-order resume (old hello after new session) rejected by epoch/handshake | **PASS (D1 closes the remaining hole)** | §4.8 |
| 9 | Idle timeout tuned: no false disconnect during the longest legal quiet step | **FIXED (D2)** | §3 |
| 10 | Forwarder retries: no duplicate progress POSTs after a WS flap | **FIXED (D1) + PASS** | §4.10 |

### 4.4 Server restart mid-lesson

Terminal lifecycle batches are persisted before the POST is attempted
(`forwarder._store_terminal_batch`, called from `_run` *before* `_post`) into
`RedisTerminalReplayStore` when `REDIS_URL` is set, else the in-process
`MemoryTerminalReplayStore` (`forwarder.py:276-359`). On reconnect,
`maybe_start_lesson_on_connect` loads `(device_id, assignment_id)` from that store
and replays before considering a restart; a still-pending terminal event *blocks*
restart (`TERMINAL_REPLAY_PENDING`) rather than starting the lesson over
(`runtime.py:6076-6146`). Backend ingest dedups null-sequence lifecycle rows by
`(assignment_id, event_type)`, so at-least-once replay is safe.

What survives a restart is therefore exactly what the contract promises: the
terminal outcome, not mid-step position — **only when Redis is configured**. With
the memory store a process restart loses the pending batch. That is the documented
shape of the contract, not a defect introduced here; the SD-pack `pending_store`
recovery half is T2.2's.

### 4.5 Broadcast paths tolerate a connection that is falling over

`core/http_server.py` fans out over every live connection in three endpoints. A
connection being torn down (or superseded) could raise out of `alarm.snapshot()`
/ `alarm.reset()` and 500 the endpoint for every other device. Now each
per-connection read is isolated (`_alarm_snapshot`, guarded `reset`), and the
reset sweep iterates a snapshot tuple rather than the live dict.
`BroadcastResilienceTest` (3 tests) pins this with one exploding and one healthy
connection.

For the socket-send side, `_SupersededWebSocket.send` raises
`SupersededConnectionError` (a `ConnectionResetError`), which the lesson HTTP
handlers already treat as a send failure — `lesson_nudge_handler.py:217` and the
SD handlers wrap sends in `except Exception`.

### 4.6 Bounded lesson event queue

`LessonEventForwarder._queue` was unbounded: a backend that stops answering while
a long lesson keeps producing events grows it without limit. `enqueue()` now sheds
at `max_queue_size` (default 512) — but **never** a terminal lifecycle batch,
which is what reconnect replay is built around — and counts what it shed in
`dropped_events_total` / `dead_letters` instead of dropping silently.
`ForwarderBackpressureTest` pins both halves.

### 4.7 Frame-size budget

Enforced at 16 KiB in `core/lesson/runtime.py:94` for `lesson_step`
(`:3848`) and every outbound frame (`:5011` → `_fail_oversized_frame`), with the
`lesson_wire_frame_size_budget` checkpoint asserted by the robot harness
(`robot/tests/test_lesson_e2e_log_verify.py:161`). Pre-existing and owned by T2.1;
untouched here.

### 4.8 Out-of-order resume

Lesson frames carry `assignmentId`/`sessionId` and the runtime drops frames whose
identity does not match the session. The remaining hole was the *socket*: before
D1 an old socket could still deliver a late `hello` (or receive a nudge resolved
against the stale handler) after the device had moved on. With supersession the
old socket is closed and its handler refuses sends, so a late frame on it cannot
be processed. `test_superseded_handler_teardown_never_evicts_its_replacement`
pins the registry half — the loser's teardown must not unregister the winner.

### 4.10 Duplicate progress POSTs after a WS flap

Two independent guarantees now hold:

* only one forwarder is live per device — the superseded handler's teardown closes
  its runtime (and with it the runtime's forwarder) and its safety forwarder;
  `ReconnectMatrixTeardownTest` asserts this for all five lifecycle stages;
* terminal replay is idempotent backend-side by `(assignment_id, event_type)`
  (§4.4), and the forwarder clears the stored batch on the first 2xx.

---

## 5. Reconnect matrix

**Disconnect column** — what the server releases, per lifecycle stage. Asserted by
`ReconnectMatrixTeardownTest::test_every_lifecycle_stage_closes_runtime_forwarder_and_pull`
over the runtime's own `S_*` states (`core/lesson/runtime.py:256-263`) —
"prepare" is `IDLE`, since `lesson_prepare` is emitted before the runtime leaves
that state:

| Stage at disconnect | `lesson_pull_task` | `lesson_runtime` | forwarders | registry entry | socket |
|---|---|---|---|---|---|
| IDLE (prepare sent) | cancelled | `close()`, set `None` | `aclose()` | released via `remove_if_current` | closed |
| PRELOADING | cancelled | `close()`, set `None` | `aclose()` | released | closed |
| READY | cancelled | `close()`, set `None` | `aclose()` | released | closed |
| RUNNING (mid-step) | cancelled | `close()`, set `None` | `aclose()` | released | closed |
| PAUSED | cancelled | `close()`, set `None` | `aclose()` | released | closed |

Teardown is uniform across stages — no stage leaks a runtime. A pending terminal
batch is *not* discarded: it lives in the terminal replay store (§4.4).

**Resume column** — what pull-on-connect does with the assignment it re-reads.
Asserted by `ReconnectMatrixResumeTest`:

| Assignment state on reconnect | Resume behaviour | Status code |
|---|---|---|
| `ASSIGNED` | fresh runtime, restart from `lesson_prepare` | — |
| `PRELOADING` | fresh runtime, restart from `lesson_prepare` | — |
| `READY` | fresh runtime, restart from `lesson_prepare` | — |
| `PAUSED` | fresh runtime, restart from `lesson_prepare` | — |
| `COMPLETED` / `CANCELLED` / `FAILED` | no restart | `ASSIGNMENT_TERMINAL` |
| any, with a pending terminal event in the replay store | replay only, no restart | `TERMINAL_REPLAYED` / `TERMINAL_REPLAY_PENDING` |
| no current assignment | nothing | `NO_CURRENT_ASSIGNMENT` |

**Reconnect is a restart of the assignment, not a mid-step resume.** There is no
step cursor on the wire: the server re-pulls `assignment/current` and drives
prepare → preload → start from the top. Step-level progress already ingested is
preserved backend-side and deduped by `(assignmentId, sequence)`; the child
re-sees the lesson from step 1. That is a product decision, not a bug — but it is
undocumented, so it is routed as a finding (§7, F3).

**Not covered here:** the live-hardware half of the matrix (does the *robot* come
back cleanly from each stage, and is the re-render correct?). Queued in
`lesson-prod/HW_QUEUE.md` and re-checked by T5.3 (sim E2E) / T5.4 (live).

---

## 6. Scope note

`core/connection_registry.py` is not in the task's scope list, but it is the
mechanism `core/websocket_server.py` supersedes through: `replace()` must report
the displaced entry under the per-device lock, or the read-then-replace is racy.
The change is additive (a return value where there was `None`) and touches nothing
T2.1/T2.2/T2.3 own. No file under `core/lesson/` other than `forwarder.py` was
modified.

---

## 7. Findings routed out of scope

Appended to `LESSON_PRODUCTION_PLAN.md` §5:

* **F1 → T2.2/T5.1 (MED):** terminal-event replay durability across a server
  restart depends on `REDIS_URL`; with the default `MemoryTerminalReplayStore` a
  restart loses the pending terminal batch, so the "server restart mid-lesson"
  contract holds only in the Redis-configured deployment. Needs either a
  documented requirement or a disk-backed fallback.
* **F2 → T6.2 (LOW):** no metric or counter is emitted when a connection is
  superseded or when the peer-silence watchdog fires; both are exactly the signals
  an operator needs to see reconnect storms. Log lines only today
  (`Superseding previous websocket`, `lesson_peer_silent`).
* **F3 → T3.2/T5.2 (MED):** reconnect restarts the assignment from
  `lesson_prepare`; the mobile lesson-session state machine and the protocol docs
  do not state this, and no wire field carries a step cursor. Product decision +
  doc alignment needed before T5.4.

Already-open findings owned by T2.4 that this task did **not** address, because
both live outside its scope files:

* firmware never raises `PROTOCOL_SEQUENCE_ERROR` (2026-08-06, T0.2 contract
  drift, HIGH) — the decision is firmware-vs-doc, `lesson_handler.cc`;
* backend WS gateway heartbeat 30 s / one missed pong vs. the 15000/45000/3
  contract (2026-08-06, T0.2 contract drift, MED) — `tbot-backend`
  `websocket.gateway.ts`, a different repo from this task's work dir.

Both remain OPEN in §5 with T2.4 as owner.

---

## 8. Verification

Command per `LESSON_PRODUCTION_PLAN.md` §1 for this repo: `pytest` in
`main/tbot-server`.

### Full suite — branch tip vs. a pristine base worktree

Both runs are full `python3 -m pytest -q` over the same checkout tree, run
sequentially on the same machine:

| Run | Result |
|---|---|
| base `0f44fa6e` (detached worktree) | **18 failed, 3444 passed, 11 skipped** (26:10) |
| branch `lesson-prod/t24-esp-websocket` | **14 failed, 3471 passed, 12 skipped** (18:43) |

**The branch's 14 failures are a strict subset of the base's 18.** No test fails
on the branch that passes on base. The 4 that fail only on base are
load-sensitive (the base run overlapped other suites on this machine):
`test_device_mcp_admin_handler::test_stalled_cleanup_is_bounded_after_dispatch_timeout`
(asserts `elapsed < 0.2 s`), `test_google_live_provider_edges::test_lesson_child_transcript_timeout_allows_retry_audio`,
`test_lesson_asset_cache::test_evict_removes_only_current_materialized_asset_pack`,
`test_nginx_generation_cache_runtime::test_generation_cache_collapses_cloudflared_burst_and_preserves_http_semantics`.

The 14 shared failures are the pre-existing T0.1 baseline set (nginx proxy/cache
config, lesson-studio compose DNS race, scaleout deploy topology, tvideo farm
cross-repo fixtures, flattened cinematic contract, google_live connect-config,
benchmark script) — none in this task's scope files, all already routed in
`LESSON_PRODUCTION_PLAN.md` §5.

`+27 passed` = the 24 new regression tests plus the 3 base flakes that passed here.

### Repro gate

`lesson-prod/repros/t24.sh` on the pristine base worktree:

```
FAILED tests/test_ws_reconnect_lifecycle.py::LessonPeerSilenceTest::
       test_running_lesson_with_a_silent_peer_closes_inside_the_budget
FAILED tests/test_ws_reconnect_lifecycle.py::RealSocketSupersessionTest::
       test_reconnect_closes_the_old_socket_and_silences_its_sends
2 failed, 18 deselected
```

Same repro on the branch tip: `2 passed`. Formal RED→GREEN row in
`lesson-prod/GATE_LOG.md`.

### New regression file

`tests/test_ws_reconnect_lifecycle.py` — **24 passed**.

### Gate

```
gate[t24] RED phase @ base ebdc935671968601b31ffa3f0d3999ed78d0d5f5
gate[t24] GREEN phase @ tip 63f9396e270d34984f902340db15c9b2f4b2b7be
GATE PASS: t24 VERIFIED (RED@base rc=1, GREEN@tip rc=0). Logged to GATE_LOG.md
```

### Merge conflict with T2.5, and how it was resolved

T2.5 landed an **overlapping supersession implementation** on main while this
branch was in gate (`efb4fbe3`). Convergent, not contradictory — both were
written against the same registry seam:

| | T2.5 | T2.4 (this task) |
|---|---|---|
| Registry | `replace()` returns the displaced entry (byte-identical to this branch's) + `is_current()` | `replace()` returns the displaced entry |
| Synchronous guard | `superseded_by`, checked by `LessonRuntime._default_send` — drops lesson frames | `mark_superseded()` swaps in a stand-in socket — refuses **every** writer (TTS, ping, admin nudge), not just the runtime |
| Socket | `superseded.close(websocket)` — full handler teardown | explicit `close(1001)` on the raw socket |
| Extras | liveness lease, SCRAP disposition | task tracked for `drain()` |

**Both kept.** One merged `_scrap_superseded_connection`: capture the real
socket → stamp `superseded_by` → `mark_superseded()` → emit the SCRAP
disposition → schedule a close that sends 1001 on the raw socket first (the
device gets a decisive close without waiting on voice-provider teardown) and
then runs the full handler teardown. Capturing the socket **before** marking is
load-bearing: `mark_superseded` swaps in a stand-in whose `close()` is a no-op,
so reading `superseded.websocket` afterwards would lose the socket that needs
closing. T2.4's separate `_supersede_tasks` set was dropped in favour of T2.5's
`_connection_tasks`, which `drain()` already covers.

Post-resolution, run together: `test_ws_reconnect_lifecycle` (24) +
`test_reconnect_storm` (51, T2.5's) + `test_websocket_server_edges` (7) —
**82 passed**.

### Re-test on main after merge (`b53d69e6`)

**15 failed, 3554 passed, 7 skipped** (5:27). Same pre-existing baseline set as
above plus `test_manager_web_lesson_derivatives_runtime` — also a T0.1 baseline
failure, in `manager-web`, which this task does not touch. No failure in any
T2.4 scope file.

<!-- VERIFY-RESULTS -->

---

## 9. Files changed

| File | Change |
|---|---|
| `core/connection_registry.py` | `replace()` returns the displaced connection |
| `core/websocket_server.py` | `_supersede_connection` / `_close_superseded_connection`; supersede tasks tracked and drained |
| `core/connection.py` | `SupersededConnectionError`, `_SupersededWebSocket`, `is_superseded`, `mark_superseded()`; lesson peer-silence watchdog in `_check_timeout` |
| `core/lesson/forwarder.py` | bounded queue (`max_queue_size`), terminal batches exempt |
| `core/http_server.py` | per-connection fault isolation in the three fan-out endpoints |
| `config.yaml` | `lesson.peer_silence_timeout_sec` documented |
| `tests/test_ws_reconnect_lifecycle.py` | new — 23 regression tests |
