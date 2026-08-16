# T5.4 Power-Cycle SD Sync Ordering — 2026-08-17

## Reproduction

Physical capture:

```text
/Users/manhhodinh/Documents/TBOT/.codex_tmp/lesson-live-20260817-power-cycle-display-fix
assignmentId=6f230b1c-6ddc-447a-969c-52f6d3d743ce
pre-reboot sessionId=f3822f12-5fa0-4856-96de-a64f875648d3
post-reboot sessionId=048458ea-f9d4-4032-934c-1cc6e4101f04
lesson=w02-feelings v7
manifestChecksum=418dfd660ef4254418229d86f8e77890d29573793b1a8b7a211d31dcfdf0ef27
```

The robot was power-cycled during the s2 child-response window. The server observed
the disconnect, then reconnected the same firmware build
`34cf64b3e1420aed2f204dfe6d943f9a9e3061cdb5b5d09945bee37fa8490661` and recovered
the same RUNNING assignment.

The reconnect current-pack preload passed:

```text
lesson_preload_ready assetCount=9 downloadedCount=0 skippedCount=9 failedCount=0
checksum_verified assetCount=9
```

At the same time, the connection-owned on-connect sync started processing 24 cached
packs. Immediately after current-pack preload, it dispatched its next firmware storage
mutation before the runtime emitted `lesson_prepare`. Firmware therefore could not
acquire the cinematic read/session lease and replied:

```text
inbound lesson_error code=CINEMATIC_SD_PATH_MISSING
lesson_failed
```

The background task later completed `packs=24 synced=24 failed=0`. This proves the
renderer assets existed; the failure was storage-admission ordering, not a missing SD
file. The finding is tracked as `F-T54-56`.

## RED

The new runtime regression holds an active `sd_pack_sync_task`, completes current-pack
preload, and asserts that `start_protocol` has not run. Before the fix it fails with:

```text
AssertionError: lesson protocol started while cached SD sync was active
```

T0.4 independently reproduces this on ESP main `ad4f3286`.

## Fix

Commits:

```text
8a0501cc docs(lesson): design reconnect SD sync barrier
a758d154 fix(lesson): serialize reconnect behind SD sync
30ebdc5a test(lesson): harden reconnect sync ordering probe
```

After a candidate runtime preloads its current lesson pack, the reconnect path now
awaits the connection-owned cached SD sync before entering lesson mode or emitting
`lesson_prepare`. No sync task means no delay. Connection teardown cancellation still
propagates, while background-sync exceptions remain fail-soft.

## Passing Verification

```text
focused RED→GREEN regression: 1 passed
runtime + SD sync suites: 317 passed
ESP standard suite at code tip 30ebdc5a: 3849 passed, 8 skipped
python py_compile: PASS
T0.4 gate t54-power-cycle-sd-sync: RED on ad4f3286, GREEN on 30ebdc5a
code review: no findings; LSP tool unavailable, py_compile + focused pytest substituted
```

## Remaining Physical Gate

This fix must be merged and deployed to the VPS, then the exact mid-lesson power-cycle
must be repeated. T5.4 remains IN_PROGRESS until the post-deploy physical run resumes
renderer-v5, completes s1-s9, and posts parent-app progress.
