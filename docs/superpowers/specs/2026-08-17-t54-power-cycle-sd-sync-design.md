# T5.4 Power-Cycle SD Sync Ordering

## Problem

On a physical robot reconnect during an active lesson, the lesson runtime and the
connection-owned cached SD sync run concurrently. The current lesson pack can finish
preloading while the background sync immediately starts mutating another pack. The
runtime then emits `lesson_prepare`; firmware cannot acquire its read/session lease and
reports `CINEMATIC_SD_PATH_MISSING`, even though the lesson files exist and verify.

## Design

Keep the firmware and wire contract unchanged. After the candidate runtime completes
its current-pack preload, but before entering lesson mode or emitting `lesson_prepare`,
the reconnect path waits for the connection-owned `sd_pack_sync_task` when one is
active. No task means no delay. Task failures remain fail-soft because the background
sync wrapper already converts them to a result; cancellation still propagates during
connection teardown.

This ordering preserves conversation mode while waiting, prevents firmware storage
mutation from overlapping renderer admission, and keeps all existing preload,
activation, terminal-replay, and fallback-runtime barriers intact.

## Verification

- Regression test proves `start_protocol` is not called until an active cached SD sync
  task completes.
- Existing lesson runtime and SD pack sync suites remain green.
- Deploy the ESP server, repeat a physical mid-lesson power cycle, and verify reconnect,
  SD sync completion, renderer start, s1-s9 completion, and backend progress.
