# T5.4 MCP Reconnect Recovery Design

## Status

Approved approach A on 2026-08-16. This design is limited to the live T5.4 power-cycle failure observed on the physical robot.

## Problem

During a real mid-lesson power cycle, the ESP server reconnects, pulls the still-RUNNING assignment, and begins SD-pack attestation before firmware MCP discovery is complete. The runtime currently waits up to eight seconds inside `_sync_sd_asset_pack_to_robot()`, but the physical device returned the first paginated `tools/list` response after that deadline and completed discovery later.

The captured order is:

1. WebSocket reconnect and current assignment pull.
2. Lesson runtime candidate binds.
3. The eight-second MCP readiness deadline expires.
4. SD attestation fails closed as `ASSET_PACK_NOT_READY` and the assignment receives `lesson_failed`.
5. MCP tool discovery becomes available afterward.

The firmware retains the lesson framebuffer across this startup failure. When the server returns to conversation ownership, the normal robot face is painted over the stale lesson content. This is the operator-visible T5.4 failure.

## Goals

- Do not invoke the lesson SD-sync MCP tool until MCP discovery is complete.
- Give reboot/reconnect discovery a bounded window that covers the observed physical pagination latency.
- Preserve fail-closed SD attestation if MCP never becomes ready.
- Clear stale lesson layers before conversation rendering resumes after a terminal startup failure.
- Preserve normal spoken lesson-start latency and all existing assignment, SD-pack, renderer-v5, and terminal-event contracts.

## Non-goals

- Resume at the exact pre-reboot step; reconnect continues to restart the assigned lesson from its beginning.
- Change firmware rendering, video assets, background/object composition, or lesson content.
- Change backend assignment state semantics.
- Add an assignment nudge mechanism. The observed lack of an eager nudge for an already-open ESP connection is routed to the campaign findings log.
- Change the deferred intentional Wi-Fi-loss acceptance case.

## Considered Approaches

### A. Reconnect-specific MCP readiness gate and deterministic display cleanup

Wait for MCP readiness in the connect-time recovery path using a separate bounded timeout. Keep the shorter existing runtime sync timeout for ordinary starts. If readiness or subsequent preload still fails terminally with no previous usable runtime, send the existing preload-reset lesson envelope to clear firmware lesson layers before releasing lesson ownership.

This is the selected approach because it addresses both causes without globally slowing every lesson start.

### B. Increase the global SD-sync readiness timeout

This is smaller, but it delays errors for spoken starts and other paths that are not reboot recovery. It also leaves the stale-framebuffer cleanup gap intact.

### C. Retry lesson startup asynchronously after MCP discovery

This can recover without waiting in the initial call, but introduces another runtime owner and retry lifecycle. It risks duplicate sessions, duplicate lifecycle events, and races with a spoken `start_lesson` request.

## Design

### Reconnect readiness gate

Add a small async helper in the lesson runtime module that waits for `conn.mcp_client.is_ready()` with monotonic time, bounded polling, and cancellation-safe awaits. The connect-time assignment recovery path calls it after hello/features are known and before constructing or preloading the runtime candidate.

The gate uses `mcp_reconnect_ready_timeout_sec` with a 20-second production default and the existing 50 ms readiness poll. The captured failing connection needed about ten seconds to deliver its first paginated tool page, so 20 seconds provides measured headroom without creating an unbounded reconnect. Tests use short deterministic values. A missing MCP client remains compatible with devices that did not advertise MCP; an MCP-capable connection with a client that never becomes ready fails closed.

The existing `_sync_sd_asset_pack_to_robot()` readiness check remains as defense in depth. The new gate prevents connect-time recovery from consuming its shorter ordinary-start deadline while firmware is still enumerating tools.

### Candidate and mode ownership

No runtime becomes active and no `sync_to_sd` call occurs before the readiness gate passes. The existing `_lesson_pull_lock` continues to serialize connect-time recovery with spoken `start_lesson`, so the change does not introduce a second retry loop or runtime owner.

If the gate times out, the connection records a specific start status and diagnostic log, creates no active runtime, and does not emit lifecycle success. Assignment handling remains fail closed.

### Stale lesson display cleanup

When a startup candidate fails terminally and there is no previous usable lesson runtime, invoke the existing `request_lesson_preload_reset()` protocol before releasing lesson mode or allowing conversation-face rendering. This sends a `lesson_prepare` envelope with `preloadResetOnly: true`, which gives firmware an explicit opportunity to retire stale lesson layers.

Cleanup is best effort and bounded. A missing ACK must not keep the connection in lesson mode forever or convert a startup failure into a connection failure. The cleanup result is logged, and the server then follows the existing release-to-conversation behavior. When a previous known-good runtime exists, preserve it instead of clearing its display.

## Error Handling

- MCP ready within the reconnect deadline: continue through existing SD attestation and renderer startup.
- MCP readiness timeout: do not call SD sync; log and expose a distinct fail-closed start status; run bounded stale-display cleanup.
- MCP readiness check raises: treat it as a readiness failure, log only the exception type, and run the same cleanup.
- SD attestation fails after MCP is ready: preserve the existing failure code and lifecycle behavior, then run cleanup only when no previous runtime can be restored.
- Cleanup times out or fails: log the outcome and still release ownership so voice connectivity remains available.

## Tests

Add focused async regressions before implementation:

1. Connect-time recovery starts immediately while MCP becomes ready after a simulated delay longer than the ordinary eight-second sync deadline; assert `sync_to_sd` is never called before readiness and the lesson proceeds afterward.
2. MCP never becomes ready; assert bounded completion, no SD-sync invocation, no active runtime, and fail-closed start status.
3. Terminal candidate preload failure with no previous runtime; assert the preload-reset envelope is requested before conversation ownership is restored.
4. Candidate failure with a previous known-good runtime; assert no cleanup clears that runtime's display.
5. Cleanup timeout/failure; assert the connection remains usable and startup still returns without raising.

Run the focused lesson runtime tests, related connection/voice routing tests, the ESP server standard suite, and the T5.4 live probe/capture verifier required by the task file.

## Live Verification

After merge and VPS deployment, create a fresh assignment through the supported no-PIN admin path and begin a fresh capture before the physical run. Verify normal spoken start, renderer-v5 three-layer composition, all Robot video effects, audible prompts, arm actions, progress read-back, and a real mid-lesson power cycle.

The post-reboot evidence must show MCP readiness before SD sync, successful attestation, lesson restart and completion, and no conversation face covering lesson content. CP-7 requires an explicit operator visual confirmation. Terminal conversation must also show microphone resumption with `reason=tts_stop_continue_listening`.

Only after those gates pass may T5.4 run its Ship checklist, re-test on main, remove merged task worktrees, and change status to DONE.

## Out-of-scope Finding

Creating an assignment through the supported production admin endpoint did not nudge the already-open ESP session; the operator had to say `bắt đầu bài học`. Record this under assignment/lifecycle ownership in `LESSON_PRODUCTION_PLAN.md` section 5 rather than changing it in this repair.
