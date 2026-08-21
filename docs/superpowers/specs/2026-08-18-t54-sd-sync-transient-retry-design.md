# T5.4 Assignment SD Sync Transient Retry

## Problem

The connection-level lesson-start admission check can permit an assignment SD sync
while Google Live is transitioning to a passive state, but firmware can briefly retain
voice detection and reject the sync with `lesson asset sync busy or worker unavailable`.
The lesson runtime currently treats that transient rejection as terminal and fails the
assignment with `ASSET_PACK_NOT_READY`, even when a subsequent sync attempt would be
safe and successful.

## Design

Keep the firmware, MCP contract, and existing 15-second foreground-busy deadline
unchanged. Inside the existing foreground operation, retry only an exception whose
message contains the firmware condition `lesson asset sync busy or worker unavailable`.
Before every retry, re-run the existing lesson SD-sync busy guard, wait using the
configured foreground busy poll interval, and stop when the already-established
deadline expires. Deadline exhaustion continues to map to
`SD_SYNC_REALTIME_BUSY_TIMEOUT`.

Every other MCP exception remains fail-closed and is not retried. The existing
`MCP tools disabled during lesson` preload-reset recovery remains unchanged.

## Verification

- A regression test proves the exact transient firmware rejection is retried and a
  later valid attestation succeeds.
- A negative regression test proves an unrelated MCP error is attempted once and
  remains terminal.
- Lesson runtime, voice non-regression, and nudge-handler suites remain green.
- The isolated image is deployed and the full strict T5.4 physical closeout is rerun
  with one fresh assignment and preserved evidence.
