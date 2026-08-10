# T6.1 Soak Blocker Fixes Design

## Goal

Remove the two blockers found by the T6.1 Docker soak so the complete release-sized soak can run:

1. WebSocket churn must not retain manager web/API sockets or monotonically grow ESP file
   descriptors.
2. The T5.3 simulation stack must provide a production-valid SD materialization file limit.

The final acceptance gate remains the existing T6.1 checklist: at least 20 consecutive lessons,
at least one hour idle, WebSocket churn, bounded generation and ingest load, and SD
materialize/evict cycles, followed by the task Ship checklist.

## Root Cause

`ManageApiClient._ensure_async_client()` caches one `httpx.AsyncClient` per event-loop identity in
the class-level `_async_clients` map. Each simulated WebSocket connection can execute private
configuration loading on a distinct event loop. The loop ends, but its cached client remains
reachable and its outbound manager-API sockets remain established. The client is configured with
`max_keepalive_connections=0`, so retaining it provides no intended connection-reuse benefit.

The SD materializer rejects an empty `LESSON_SD_MAX_FILE_BYTES`. Production packaging documents
and requires `33554432`, but the T5.3 simulation compose does not set the variable for the ESP
container.

## Runtime Design

Replace the per-event-loop client cache with a request-scoped client:

- `_ensure_async_client()` constructs and returns a fresh configured `httpx.AsyncClient`.
- `_async_request()` owns that client and closes both the response and client in `finally`, on
  success, business errors, HTTP failures, cancellation, and retryable network failures.
- Existing retry behavior remains unchanged; each retry gets a new client.
- `safe_close()` remains compatible for process shutdown and singleton reset, but no longer has
  normal request clients to drain.

This keeps ownership local and deterministic. Closing the global client from WebSocket teardown
was rejected because concurrent connections can share a loop and would be able to close one
another's transport.

## Simulation Fixture Design

Set `LESSON_SD_MAX_FILE_BYTES=33554432` in the T5.3 ESP service environment. This matches the
production release example and the asset-cache default. Keep the value explicit in the simulation
contract so the real internal materialize endpoint is exercised instead of silently relying on a
different fallback path.

## Tests

Use strict red-green development:

1. Replace the cache-oriented unit expectation with tests proving every request owns a distinct
   client and closes it on success and failure. Run them before changing production code and
   observe failure caused by the current cache/lifecycle behavior.
2. Add a compose contract test requiring the T5.3 simulation ESP service to set the bounded file
   limit. Run it before editing compose and observe failure.
3. After implementation, run the focused manager-client, connection, SD materializer, compose,
   and T6.1 driver suites.
4. Rebuild/restart the simulation ESP image and repeat the isolated WebSocket churn reproduction.
   File descriptors must return within the documented allowance and no manager-API sockets may
   accumulate.
5. Run the complete T6.1 soak at N>=20, idle>=3600 seconds, WS churn>=100, generation requests>=20,
   and SD cycles>=10. Every report check must pass.

## Shipping

Record red/green evidence and full-soak output in the T6.1 evidence file. Resolve F-T61-01 and
F-T61-02 in the production plan only after their live reproductions pass. Then rebase and verify
the branch, merge through the repository gate, skip deployment because the changes affect the
local/simulation ESP service rather than production deployment artifacts, verify again from
`main` using `verify-on-main.sh`, and remove the task worktree and branch before setting T6.1 DONE.
