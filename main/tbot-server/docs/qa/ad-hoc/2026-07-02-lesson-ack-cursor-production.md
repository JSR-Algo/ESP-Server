# 2026-07-02 Lesson ACK Cursor Production Hotfix

## Scope

Production voice-triggered sample lesson on robot `28:84:85:85:1a:80`.

## Root Cause

The robot sent a late ACK for the first `lesson_prepare` after the server had
already retried with sequence `2`. The late stale ACK advanced the server's
firmware inbound cursor. Firmware resets its F->S envelope sequence to `1` on a
new `lesson_prepare`, so the valid retry ACK for `body.acks=2` was then rejected
as a duplicate envelope sequence before correlation reached `body.acks`.

## Fix

`core/lesson/runtime.py` now correlates `lesson_ack` against an outstanding
S->F frame before advancing the inbound F->S sequence cursor. Stale ACKs with a
concrete `body.acks` that no longer maps to an outstanding frame remain
idempotent no-ops and cannot poison the cursor. A narrow empty legacy ACK
fallback is also present only when exactly one frame is outstanding.

## Verification

- RED: `tests/test_lesson_runtime.py::LessonRuntimeTest::test_prepare_ack_timeout_retries_before_failing_lesson` failed with `_preload_task` still `None`.
- GREEN: focused ACK tests passed: retry cursor, duplicate ACK, canonical `body.acks`, and empty legacy ACK.
- Focused suite: `./.venv311/bin/python -m pytest tests/test_lesson_runtime.py tests/test_google_live_tool_calls.py tests/test_start_lesson_tool.py tests/test_sample_lesson.py tests/test_connection_voice_provider_routing.py -q` -> `313 passed, 1 warning`.
- Static checks: `git diff --check` clean; `python -m py_compile core/lesson/runtime.py` clean.
- Container check: `docker run --rm --platform linux/amd64 --entrypoint python local/tbot-server:prod-lesson-ack-cursor-20260702T164011Z -m py_compile core/lesson/runtime.py` clean.
- Production deploy: `local/tbot-server:prod-lesson-ack-cursor-20260702T164011Z` running on both server replicas.
- Public smoke: `bash deploy/smoke-vps.sh --admin-url https://admin.tjbot.vn/ --ota-url https://esp.tjbot.vn/tbot/ota/ --expected-ws-host esp.tjbot.vn --timeout 12` -> `Smoke checks passed`.

## Physical Status

Physical voice re-test is blocked after deployment: metrics report
`connections: 0`, LAN ping to `192.168.0.111` returns 100% packet loss, and no
`/dev/cu.usbmodem*` serial device is present. No firmware flash, OTA install,
serial write, reset, or NVS write was performed.
