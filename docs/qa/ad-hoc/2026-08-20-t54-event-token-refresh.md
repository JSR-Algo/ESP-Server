# T5.4 Lesson Event Token Expiry Recovery - 2026-08-20

## Scope

This branch owns only `F-T54-62`. It does not merge, deploy, push main, reset or
flash a robot, create assignments, or operate Android.

Branch baseline: ESP `origin/main` at `a8072cab`.

## Root Cause

`config.device_token_client.resolve_device_identity()` caches a 15-minute device
JWT for at most 10 minutes, reserving five minutes for a normal lesson. The pull
path passes that token into `LessonEventForwarder`, which previously retained the
same string for the entire runtime lifetime. A token reused at the inclusive
10-minute cache boundary can therefore expire five minutes into a long lesson.

The backend rejects that batch before persistence with HTTP 401 and canonical
`AUTH_TOKEN_EXPIRED`. The forwarder treated all non-transient 4xx responses as
terminal, so it dead-lettered the event without consulting the mint client again.

## RED Gate

The self-contained repro is:

```text
main/tbot-server/scripts/t54_event_token_refresh_repro.py
```

It advances a controlled monotonic clock from token mint at `0s` to `901s`, then
posts a terminal `lesson_completed` batch. The simulated backend returns
`AUTH_TOKEN_EXPIRED` for the old token and records only successfully authenticated
batches.

Run against the untouched base while executing the script from this branch:

```bash
python scripts/t54_event_token_refresh_repro.py \
  --source-root /path/to/a8072cab/main/tbot-server
```

RED result:

```text
TypeError: LessonEventForwarder.__init__() got an unexpected keyword argument
'token_refresh_fn'
exit 1
```

## Fix And Bounded Behavior

- The runtime gives the forwarder a refresh callback bound to the physical device
  MAC and the trusted mint client.
- Only HTTP 401 with canonical top-level or nested `AUTH_TOKEN_EXPIRED` triggers
  recovery.
- Recovery bypasses even a still-fresh local cache entry, remints the device JWT,
  rejects any changed backend device UUID, updates only the token, and retries the
  exact same batch once.
- A second expiry is not refreshed again. Existing transient transport/5xx queue
  policy remains separate.
- Other 4xx responses remain fail closed with no refresh and no retry.
- The clock-controlled test observes request tokens `jwt-1`, then `jwt-2`, asserts
  both attempts receive the same batch object, and asserts the simulated backend
  persists that batch exactly once.

GREEN gate:

```text
F-T54-62 event token refresh repro: PASS
exit 0
```

## Verification

```text
focused forwarder/runtime/device-token suites:
316 passed, 1 warning

expanded forwarder/runtime/manage-client suite:
458 passed, 1 warning

full relevant lesson/manage/device-token suite:
1501 passed, 2 skipped, 1 warning, 1 failed
failure: test_preload_materializes_verified_asset_pack_to_shared_sd_mount
reason: pre-existing timing-sensitive busy/preload timeout; reproduces on untouched
a8072cab and passes on focused branch rerun

full ESP standard suite:
3866 passed, 3 skipped, 3 warnings, 2 failed
failures:
- test_preload_materializes_verified_asset_pack_to_shared_sd_mount
- test_public_generation_reads_are_uncached_bounded_and_origin_isolated
both pass on focused branch rerun; the asset-cache failure also reproduces on
untouched a8072cab. Neither test imports or exercises the changed token/forwarder
path.

focused rerun of both full-suite failures on branch: 2 passed
same focused rerun on a8072cab: asset-cache failure reproduced, nginx test passed

py_compile for all changed Python files: PASS
git diff --check: PASS
```

The worktree-local copied `.venv311` contains dereferenced, non-executable Python
symlink files, so verification used the existing executable interpreter at
`robot/esp32-server/main/tbot-server/.venv311/bin/python` while running from this
worktree's `main/tbot-server` directory. Ruff is configured in `pyproject.toml` but
is not installed in that environment.

## Handoff

The lane is ready for post-H1 review. The branch must remain unmerged and
undeployed until H1 freezes the T5.4 production baseline.
