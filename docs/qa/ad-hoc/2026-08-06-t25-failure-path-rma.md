# adhoc-2026-08-06-t25-failure-path-rma — T2.5 Failure-path RMA lane

**Campaign:** lesson production-readiness (`/Users/manhhodinh/Documents/TBOT/LESSON_PRODUCTION_PLAN.md`)
**Date:** 2026-08-06 · **Depends on:** T0.1 (DONE, confirmed at session start)
**Branch:** `lesson-prod/t25-failure-path-rma` (`robot/esp32-server`), tip `6fcf2278`
**Gate:** `t25 | VERIFIED` (twice; final run at tip `6fcf2278`) — `lesson-prod/GATE_LOG.md`
**Merged to main:** `efb4fbe3` (merge #11, no conflicts)

**Deliverables**
- Matrix: `robot/docs/failure-path-matrix.md`
- Lease design: `robot/docs/lesson-liveness-lease-design.md`
- Harness: `robot/esp32-server/main/tbot-server/tests/reconnect_storm.py`
- Suite: `robot/esp32-server/main/tbot-server/tests/test_reconnect_storm.py` (51 tests)
- Repro: `lesson-prod/repros/t25.sh`

---

## Summary

The stale-WS listening incident of 2026-07-06 was not a one-off. It is the visible
instance of a structural gap: **four components hold "this device is in a lesson right
now" state, and none of them can ask whether the session that set it is still alive.**

The cross-component grep inventory (matrix §2, 25 state sites across the four repos)
found one concrete defect and one concrete design hole:

1. **Defect, fixed here.** `ConnectionRegistry.replace()` swapped the map entry for a
   duplicate connect and dropped the displaced `ConnectionHandler` on the floor. That
   handler kept its `lesson_runtime`, kept its websocket, and kept writing lesson frames
   through `LessonRuntime._default_send` — to a socket the device had already abandoned,
   racing frames from the live session. Nothing reaped it until `_check_timeout`, which
   is long enough for an entire lesson to run inside the window.

2. **Design hole, addressed by the lease.** Even with the socket fixed, the guarantee is
   server-local. A backend or mobile consumer still cannot tell fresh state from a ghost.
   The liveness lease makes the same guarantee transitive.

A third finding is larger than this lane and is routed, not fixed: **a lesson that dies
after `lesson_started` never reaches a terminal state anywhere** (F-T25-01). The ESP now
*classifies* that teardown as `scrap` and emits it as telemetry, so the production rate
becomes measurable — but the `lesson_assignments.state` transition is backend-owned.

All matrix cells were re-verified against the four repos at close-out; one claim had gone
stale mid-session and is corrected in place (see the F-T25-01 note below).

---

## What was checked (cross-component grep inventory)

Every site that sets or clears listening / session-active state, in all four components.
Full table with line numbers: `robot/docs/failure-path-matrix.md` §2. Headlines:

| Component | Sites | What the inventory showed |
| --- | --- | --- |
| `robot/esp32-server` | 10 state families | Registry install/release was identity-guarded on *release* but not on *displacement*. `LessonRuntime.on_replaced()` exists with **zero production callers** (F-T25-09). Terminal events and pending SD work are already Redis-durable. |
| `tbot-backend` | 5 | `lesson_assignments.state` transitions are **entirely event-driven** — no timer, no reaper. The watchdog detects stalls but only logs them. `lesson_sessions` is durable and monotonic — the natural deep home for the epoch. |
| `tbot-mobile` | 4 | Holds no authoritative child-session state (`lesson-session.api.ts` is all throw-stubs). The parent projection's `projectionRevision` is already a monotonic freshness token — the closest existing analogue to the lease. |
| `TBOT-Firmware` | 4 | `lesson_runtime_active_` is cleared **only** by lesson protocol frames, never by a websocket close — deliberate, so passive reconnect can resume, but it means the robot holds lesson state the server has forgotten. `LessonTransportEpochGate` is already an epoch gate, but RAM-local and server-blind. |

Documentation was not trusted: `ARCHITECTURE-learning.md` is known-stale, so every cell was
derived from code.

---

## Repro (RED)

`lesson-prod/repros/t25.sh` embeds its test rather than reading it from the repo tree, so
the *same* test runs on the pre-patch base and on the fix branch — a behavioural RED, not
"the new file does not exist yet".

At base `0f44fa6e`:

```
_______________ test_superseded_socket_receives_no_lesson_frames _______________
E   AssertionError: STALE SOCKET RECEIVED A LESSON FRAME after being superseded:
E   assert ['{"type": "l...bb763213a3"}'] == []
E     Left contains one more item: '{"type": "lesson_step", "sessionId": "ea8cc044-6b7f-471f-ad14-bfbb763213a3"}'
_______________ test_epoch_ledger_survives_the_process_it_guards _______________
E   ModuleNotFoundError: No module named 'core.lesson.liveness_lease'
============================== 2 failed in 11.66s ==============================
```

Full log: `lesson-prod/repros/t25.red.log`.

## Fix

Line numbers are as they stand on `main` at close-out (`e39c7a34`), after T2.4's merge
shifted them.

| File | Change |
| --- | --- |
| `core/connection_registry.py:37` | `replace()` returns the displaced handler instead of discarding it; adds `is_current()`. |
| `core/websocket_server.py:244` | Issue a liveness lease on accept; on displacement call `_scrap_superseded_connection`. (Named `issue`, not `mint` — see the naming note below.) |
| `core/websocket_server.py:320` | Sets `superseded_by` **synchronously** (`:347`) — before the function returns, therefore before the winning connection can emit anything — then emits a `scrap` disposition and schedules the slow teardown *behind* that guard. Ordering is the point: `close()` awaits voice-provider teardown and a forwarder drain, which is far too slow to be the barrier. |
| `core/lesson/runtime.py:5281` | `_default_send` refuses to write from a superseded connection. **The asserted invariant.** |
| `core/lesson/runtime.py:1346` | `_teardown_disposition()` / `_emit_teardown_disposition()` — classify every runtime teardown as restock / refurbish / scrap. |
| `core/lesson/liveness_lease.py` | New. Epoch ledgers (Redis + in-memory), `Lease`, `classify_lease`, `attach_lease` / `read_lease`, `emit_disposition`. |
| `core/connection.py:272` | Initialise `liveness_lease` / `superseded_by`. |

**Behaviour change (T2.4 kept and extended it):** a superseded connection is now actively closed
rather than lingering until its socket closes or `_check_timeout` fires. Side benefit:
`_active_device_connections` no longer stays inflated against the audio-admission accept
cap for the zombie's whole lifetime. Filed in §5 of the plan.

## Re-run (GREEN)

```
$ cd main/tbot-server && python3 -m pytest tests/test_reconnect_storm.py -q
51 passed in 14.98s

$ lesson-prod/repros/t25.sh
_t25_stale_socket_repro_test.py ..                                       [100%]
2 passed in 32.74s
REPRO PASS: T2.5 stale-socket invariant holds and the epoch ledger is restart-durable.

$ lesson-prod/scripts/gate.sh t25 robot/esp32-server lesson-prod/t25-failure-path-rma
GATE PASS: t25 VERIFIED (RED@base rc=1, GREEN@tip rc=0). Logged to GATE_LOG.md
```

Adjacent existing suites (regression check on the four touched files):

```
$ python3 -m pytest tests/test_websocket_server_edges.py tests/test_connection_edges.py \
    tests/test_lesson_runtime.py tests/test_activity_lease.py \
    tests/test_websocket_server_manager_bootstrap.py -q
386 passed in 43.44s
```

Full-suite attribution is in "Full suite — delta analysis" below.

---

## Reconnect-storm harness

`tests/reconnect_storm.py` wraps the real `WebSocketServer._handle_connection` with a
fake-ESP32 websocket client and a fake-mobile consumer, replays the recorded happy-path
lesson transcript, and injects faults at each lifecycle stage.

**Real** (the code under test): `_handle_connection`, `ConnectionRegistry`,
`core.lesson.liveness_lease`, `LessonRuntime._default_send`.
**Faked**: the voice stack only. A full `ConnectionHandler` boots VAD/ASR/LLM/TTS and a
Google-Live provider; none of it participates in teardown ownership, and booting it would
make a 24-permutation storm untestable.

* Stages: `connect · prepare · preload · start · step · terminal`
* Faults: `ws_drop` (half-open — the shape of the 2026-07-06 incident) ·
  `duplicate_connect` · `out_of_order_resume` · `server_restart`
* Transcript: `tests/fixtures/lesson_transcript_happy.json`, vendored from
  `lesson-protocol.v1.json` `happyThread` so the storm runs in a single-repo CI checkout,
  and parity-pinned to the canonical fixture by
  `test_transcript_matches_canonical_happy_thread` (skips when the robot repo is absent).

**Single asserted invariant** (`ReconnectStormHarness.assert_invariant`): *no consumer
holds listening/session state without a live lease* — and, pre-lease, *no stale socket
ever receives lesson messages meant for a newer one.* Both halves run on the same traffic,
so the pre-lease half stays a regression net while the lease rolls out.

**CI:** `.github/workflows/ci.yml:38` runs `python -m pytest` over the whole tests
directory, so the harness runs in CI with no workflow change.

Two bugs were caught during development, both worth recording:

* **Harness bug.** The first version tested `handler.superseded_by` at *assert* time
  rather than at *send* time, so it flagged frames that had been sent legitimately before
  the supersede. `DeliveredFrame.was_stale` now captures the state at the moment of the
  write. Worth naming because a harness that mis-attributes is worse than no harness.
* **Real regression, caught by an existing contract test.** The lease ledger method was
  originally called `mint()`. `tests/test_public_global_generation_acceptance_contract.py`
  forbids the identifier `mint` anywhere in a closed list of production files including
  `core/websocket_server.py` — the public global-generation surface must not reach
  device-identity minting. The guard is name-based and the collision was accidental, but
  the right response was to rename (`issue()`), not to widen the guard: `mint` is reserved
  vocabulary for identity tokens and reusing it for leases would have made the contract
  unenforceable by inspection.

---

## Liveness lease — epoch-persistence decision

**Decision: Redis (`INCR` on `{ns}:lesson-liveness-epoch:{device_id}`), no TTL on the key.
The in-memory ledger is dev/test only and declares `durable = False`.**

The load-bearing risk named in the task is real: an in-process counter resets on the exact
failure it guards (server restart mid-lesson), and stale state looks fresh again. Redis
satisfies "survives the process it guards" on evidence, not assertion —
`deploy/docker-compose.prod.yml:165-183`: a **separate container**, `--appendonly yes`,
host-mounted volume at `/opt/tbot/redis/data`. It already backs `RedisTerminalReplayStore`
and `RedisLessonSdPendingStore`, so it is not a new dependency.

Backend Postgres (`lesson_sessions`) is strictly more durable and is the deeper source of
truth, but was rejected **for the mint path**: minting happens on websocket accept, and a
backend round-trip there makes device admission depend on backend availability. This is a
deferral, not a dismissal — rollout phase 4 reconciles the Redis epoch into
`lesson_sessions`.

**Residual risk, stated plainly:** deleting the Redis AOF volume restarts the counter;
consumers holding higher epochs then classify fresh leases as `ABORT` (epoch went
backwards). Loud and recoverable rather than silent — the correct trade — but **the Redis
data volume must be in the backup set**, and phase 4 removes the risk entirely. Asserted
by `test_in_memory_ledger_is_declared_non_durable` /
`test_redis_ledger_survives_a_server_restart`.

Rollout is incremental by construction: the wire field is additive, an **absent** lease
classifies as `ACCEPT` (old firmware and un-migrated consumers behave exactly as today),
and a **present-but-malformed** lease classifies as `ABORT` (once a consumer understands
leases it must not silently degrade to blind trust). Six phases, each independently
shippable and gated on data from the phase before it — see the design doc §6.

---

## Disposition telemetry

Every teardown path emits one structured line:

```
lesson_disposition {"event":"lesson_disposition","disposition":"scrap",
  "reason":"closed_mid_flight_running","component":"esp32-server","deviceId":"…",
  "assignmentId":"…","sessionId":"…","sessionEpoch":412,"runtimeState":"RUNNING",
  "atMs":1754470000123}
```

| Path | Disposition |
| --- | --- |
| duplicate connect supersede (`websocket_server.py:315`) | `scrap` / `duplicate_connect_superseded` |
| runtime teardown, `COMPLETED`/`FAILED` | `restock` / `terminal_*` |
| runtime teardown, `IDLE`/`PRELOADING`/`READY` | `refurbish` / `closed_before_start_*` |
| runtime teardown, `RUNNING`/`PAUSED` | `scrap` / `closed_mid_flight_*` |

`scrap` + `closed_mid_flight_*` is the number to alert on: it is exactly the production
rate of F-T25-01. Emission is exception-safe by construction
(`test_disposition_emit_never_raises`) — telemetry must never break a teardown path.

---

## Done criteria — honest status

| Criterion | Status |
| --- | --- |
| Matrix complete, every cell has disposition + code path + test ID | **Met.** 7 failure rows × 6 lifecycle stages, no empty cells. 11 cells are marked ✗ — the *intended* disposition is named and the code path identified, but the implementation does not reach it. Each ✗ carries a findings-log row with an owning task. Calling those cells "complete" would be false; calling them "undefined" would also be false — they are defined and unimplemented. |
| Harness runs in CI | **Met.** `.github/workflows/ci.yml:38` collects `tests/` wholesale. |
| Lease design documented, epoch-persistence decision made | **Met.** Redis, with the rejected alternatives and the residual risk written down. |
| Stale-socket invariant has a failing-then-passing regression test | **Met.** Behavioural RED at base, GREEN at tip, gate VERIFIED. |

Eleven rows were appended to `LESSON_PRODUCTION_PLAN.md` §5 rather than fixed here, per the
surgical-scope rule: nine new findings (F-T25-01, -04, -06 … -12), one behaviour-change note
for T2.4, and one campaign-tooling finding for T0.4. Three further matrix cells (F-T25-02,
-03, -05) are pre-existing findings already in the log from T0.2 / T1.4 — cross-referenced
by the matrix, not duplicated.

Two of the new findings are HIGH and both are the same shape — backend and robot
disagreeing about whether a lesson is running, in opposite directions:

* **F-T25-01** — a lesson that dies after `lesson_started` never reaches a terminal state.
  Backend says `RUNNING` forever; the robot and the ESP session are gone. Re-verified at
  backend `f6e63c3` on close-out: a concurrent backend lane narrowed this while T2.5 was in
  flight — the watchdog's old `NOT EXISTS … lesson_started` filter became a latest-event
  LATERAL join, so the stall is now *detected*. It is still only `logger.warn` (zero `UPDATE`
  statements in the file), so nothing transitions. The remediation half stands.
* **F-T25-12** — the admin assignment cancel is DB-only and never reaches the device
  (`admin-lesson-assignment-operation.service.ts` imports no websocket/ESP dependency).
  Backend says `CANCELLED`; the robot keeps teaching the child.

Neither is fixable inside T2.5's scope, and a clean ESP-side teardown does not help with
either. They should be read as this lane's main output alongside the fix.

## Full suite — delta analysis

All from `main/tbot-server`.

| Run | Result |
| --- | --- |
| Branch tip `6fcf2278` | **16 failed, 3497 passed, 11 skipped** (17m31s) |
| Merge-base `0f44fa6e`, the 19 failing node IDs from an earlier branch run replayed on a pristine base worktree | **15 failed, 4 passed** |
| Storm suite, branch | 51 passed |
| Adjacent suites, branch | 386 passed |

**Attribution of the 16:**

* **13 are pre-existing**, reproduced failing on a pristine merge-base worktree in the same
  session: `test_benchmark_google_live_audio_runtime` (1),
  `test_flattened_cinematic_contract` (1), `test_google_live_client` (1),
  `test_http_server` nginx proxies (2), `test_lesson_studio_e2e_compose` (1),
  `test_scaleout_deploy_topology` (3), `test_tvideo_farm_cross_repo_fixture` (4). This is
  the family the T0.1 baseline already routed to T2.x owners.
* **3 are load-induced timing flakes**, not regressions:
  `test_device_mcp_admin_handler::test_stalled_cleanup_is_bounded_and_falls_back_before_cancellation`
  and two members of the `test_lesson_runtime::test_sd_asset_pack_sync_*` family. Evidence:
  they assert sub-second budgets (`assertLess(elapsed, 0.2)`,
  `asyncio.wait_for(..., timeout=0.5)`); the machine was running 12 concurrent pytest
  processes from sibling lanes; the `test_stalled_cleanup` one was **reproduced failing on
  the unmodified base checkout** (`0.49 not less than 0.2`); each passes 5–6/6 in isolation
  on the branch; and **a different member of the `sd_asset_pack_sync` family failed on each
  of the two full runs** — the signature of a flake, not of a regression.
* One genuine regression *was* introduced and caught before the final commit — the `mint`
  identifier collision described above. It is fixed, not waived.

Two failures present in an earlier branch run
(`test_manager_web_lesson_derivatives_runtime`, `test_nginx_generation_cache_runtime`)
passed in the final run — both are docker/nginx runtime tests, also environment-dependent.

---

## Ship checklist

1. **Re-verify at tip.** Storm suite 51 passed; adjacent suites 386 passed; full suite
   analysed above — no failure attributable to T2.5.
2. **Merge to main via the gate.** `merge-task.sh t25` → gate re-run at tip `6fcf2278`
   (RED@base rc=1, GREEN@tip rc=0) → merged as `efb4fbe3` with a merge commit, no squash.
   Auto-merged `core/lesson/runtime.py` against T2.3's changes with no conflicts. Merge #11
   recorded (no integration re-gate this round; that fires on multiples of 5). **Not
   pushed** — `merge-task.sh` deliberately leaves pushing as a human step.
3. **Deploy — DEFERRED to T7.3**, consistent with the operator decision already recorded
   for T2.3 (`cbff8a9c`). T2.1 / T2.2 / T2.4 are still IN_PROGRESS on this repo, so a
   per-task VPS restart would bounce production repeatedly for one batched wave of ESP
   changes. Nothing in this session ran `deploy/backup-db.sh`, `deploy-vps.sh`,
   `smoke-vps.sh`, the post-restart MCP port-pin re-check, or the robot (MAC …`ac:20`)
   reconnect confirmation.
   **Deferral risk, stated:** the stale-socket fix is a *production* behaviour change —
   until it ships, a duplicate connect still leaves a zombie handler emitting lesson frames
   on the abandoned socket. That is the live bug, and it stays live until T7.3.
4. **Re-test on main.** At main `efb4fbe3` (immediately after the T2.5 merge, before any
   sibling lane touched the checkout): storm suite **51 passed**, repro **2 passed**
   (`REPRO PASS`). Those are the valid main-tip results for this task.

   A subsequent full-suite run on main is **not attributable** and is recorded here only
   for completeness: it reported *16 failed / 3517 passed*, but the shared `main` checkout
   moved under it — the T2.2 merge (`c9a48f35`) and a T2.3 doc commit landed mid-run, and
   by the end the T2.4 session had an **in-progress, unresolved merge** of
   `lesson-prod/t24-esp-websocket` sitting in the working tree (`UU` on
   `core/connection_registry.py` and `core/websocket_server.py`, conflict markers in the
   file). One of the 16,
   `test_public_global_generation_acceptance_contract`, fails on main purely because
   `ast.parse` chokes on `<<<<<<< HEAD` in `websocket_server.py`. Not a T2.5 regression, and
   not T2.4's final state either — that merge was still being resolved. The authoritative
   delta for this task is the **branch-tip** full suite above, which ran against a stable
   tree.

   **Resolved — T2.4 merged on top, cleanly and additively.** `lesson-prod/t24-esp-websocket`
   conflicted with T2.5 in exactly the two files T2.5 changed; the T2.4 session resolved it
   at `b53d69e6`. Verified at main `e39c7a34` (after T2.4 *and* T4.1 merged):

   | Check | Result |
   | --- | --- |
   | `tests/test_reconnect_storm.py` + `tests/test_ws_reconnect_lifecycle.py` + `tests/test_public_global_generation_acceptance_contract.py` | **77 passed** |
   | `lesson-prod/repros/t25.sh` | **2 passed** — `REPRO PASS` |
   | Adjacent suites (the 5 above) | **386 passed** |

   All three T2.5 load-bearing pieces survived: `replace()` still returns the displaced
   handler (`connection_registry.py:48`), `superseded_by` is still set synchronously
   (`websocket_server.py:347`), and the `_default_send` guard is intact
   (`runtime.py:5288`).

   The resolution also **widened** the guard rather than merely preserving it. T2.4 added
   `ConnectionHandler.mark_superseded()`, which swaps `self.websocket` for a
   `_SupersededWebSocket` stand-in. T2.5's `superseded_by` gates the *lesson* path; T2.4's
   stand-in catches every other writer on that handler — TTS, ping, admin nudge. Both land
   synchronously in `_scrap_superseded_connection` before the winner can emit. The two
   mechanisms compose; neither is redundant.
5. **Remove the worktree.** Verified clean, verified `lesson-prod/t25-failure-path-rma` is
   an ancestor of `main`, then `git worktree remove`. The branch ref was kept while T2.4 was
   mid-merge against these files, then deleted once that merge landed. There is no remote to
   delete from — nothing in this campaign has been pushed.
6. **Close out.** Status set to DONE here, in `lesson-prod/t25-failure-path-rma.md`, and in
   `LESSON_PRODUCTION_PLAN.md` §2.

### Incident during the ship checklist (filed as a finding)

The T2.5 evidence file was silently destroyed mid-session and had to be recovered from
`stash@{0}^3`. Cause: `merge-task.sh:33`'s every-5-merges integration re-gate runs
`git stash --include-untracked -q` in each repro's declared repo. A sibling lane's merge
fired that re-gate and stashed this session's untracked file out of the shared checkout,
with no indication to either session. Routed to **T0.4** — the re-gate should run in a
throwaway worktree, as `gate.sh` already does, rather than mutating a checkout that other
lanes are working in.
