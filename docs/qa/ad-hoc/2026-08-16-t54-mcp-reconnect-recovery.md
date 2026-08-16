# T5.4 MCP reconnect recovery verification

- Date: 2026-08-16
- Status: **IN_PROGRESS** — branch/code verification is complete and green at `0b357419d3911c2c50dca1a638da344315ab49b1`; deployment and the physical CP-7/power-cycle re-test are pending.
- Branch: `lesson-prod/t54-mcp-reconnect-recovery`
- Verification cwd: `main/tbot-server`

## Physical reproduction

Capture: `/Users/manhhodinh/Documents/TBOT/.codex_tmp/lesson-live-20260816-t54-final-main/esp-server.log`

- Assignment `25b31ff2-6889-431b-8f19-6083268e4a01` selected `w02-feelings` version 5.
- At `17:15:49`, the lesson session was bound while MCP discovery had initialized but had not returned its tools pages.
- At `17:15:50`, the server sent the first `tools/list` request. The lesson path nevertheless continued toward SD attestation.
- At `17:15:58`, SD sync failed closed with `ASSET_PACK_NOT_READY`, immediately followed by `lesson_failed` for the bound session.
- At `17:15:59`, only after the failure, the first MCP tools page arrived and the continuation request was sent. The original connection was superseded at `17:16:14`; the subsequent connection completed all MCP pages and logged `MCP client ready` at `17:16:17`, immediately before its first `self.lesson_assets.sync_to_sd` call.

Root cause: connect-time assignment pull could create/preload a lesson candidate before the reconnecting device's MCP discovery had reached ready. The runtime therefore attempted the MCP-dependent SD path against an incomplete tool catalog. The failure path also needed an explicit stale-layer reset before releasing lesson mode when no prior runtime existed.

## RED and fixes

| Commit | Evidence |
| --- | --- |
| `93bfd1c3` | Added delayed-ready, timeout, and readiness-error regressions proving preload must wait for MCP discovery and timeout/error must produce no preload or runtime. |
| `0bb0cd80` | Strengthened the RED timeout/error cases to require reconnect candidate cleanup. |
| `078b9fbf` | Corrected the fixtures to advertise MCP capability, ensuring the regressions exercise the production gate. |
| `caa3d911` | Added `_wait_for_mcp_reconnect_ready`: MCP-capable reconnects poll `is_ready()`, default to a bounded 20-second timeout and 50 ms poll, return `MCP_DISCOVERY_TIMEOUT` on timeout/error, and do not enter GC/preload until ready. |
| `85ce15b8` | Added RED coverage for reset-before-release, reset failure still releasing, MCP timeout cleanup, and preservation of an existing runtime/layers. |
| `0b357419` | Centralized failed-start cleanup: clear stale lesson layers through `request_lesson_preload_reset` before release when no prior runtime exists, preserve a previous runtime without reset/release, and apply cleanup consistently to readiness, space, preload, activation, `LessonError`, and unexpected failure paths. |

The branch diff from the pre-change planning tip is limited to `core/lesson/runtime.py` and `tests/test_lesson_runtime.py`; the verification/evidence commit adds only this document.

## Verification

The interactive shell initially had no `python` executable on `PATH`. The existing canonical checkout interpreter at `robot/esp32-server/main/tbot-server/.venv311/bin` was prepended to `PATH`, after which the exact commands below ran against this worktree and cwd.

| Command | Result | Status |
| --- | --- | --- |
| `python -m pytest tests/test_lesson_runtime.py -q` | 273 passed, 0 failed, 1 warning in 17.77 s | PASS |
| `python -m pytest tests/test_lesson_conversation_integration.py tests/test_connection_voice_provider_routing.py -q` | 97 passed, 0 failed, 1 warning in 1.20 s; voice-routing contributes 33 tests | PASS |
| `python -m pytest tests -q` | 3,836 passed, 3 skipped, 0 failed, 3 warnings in 159.78 s | PASS |
| `git diff --check` before evidence edit | Exit 0, no output | PASS |
| `git status --short` before evidence edit | Clean | PASS |
| `git diff --check` after evidence edit | Exit 0, no output | PASS |

The standard suite was run from the required `main/tbot-server` cwd. The cwd-sensitive false-failure condition tracked by F-T54-26 did not reproduce, and there is no new lesson/reconnect failure or unchanged failing baseline to route from this run.

## Preserved invariants

- No SD sync, GC, or preload begins before MCP readiness on an MCP-capable reconnect.
- MCP readiness waiting is bounded; the production default is 20 seconds.
- Timeout, missing readiness support, and readiness exceptions fail closed with `MCP_DISCOVERY_TIMEOUT`; they do not preload or install a candidate runtime.
- A terminal startup failure with no prior runtime resets lesson layers before releasing lesson mode; reset failure does not prevent release.
- A prior usable runtime remains installed and its layers are not reset while a replacement candidate is being prepared or fails.
- Existing conversation/voice routing and the prior lesson runtime suite remain green.
- No secret, environment/config file, wire protocol, renderer/content, mobile, firmware, backend, or database change is included.

## Deployment and live verification

**PENDING.** This branch has not been deployed by this verification task. The physical CP-7 and real mid-lesson power-cycle flow must be rerun after an authorized deployment, with logs proving MCP readiness precedes the first SD sync/preload and that failure cleanup clears stale layers without damaging a prior runtime. T5.4 remains **IN_PROGRESS**; this document does not claim DONE.

## Assignment notification finding

F-T54-51 records a separate live handoff observation: supported production assignment creation for an already-open ESP session did not eagerly notify/start the assignment. It remained waiting until the operator spoke `bắt đầu bài học`. No PIN was required, and this notification gap is not caused or fixed by the MCP reconnect readiness change.
