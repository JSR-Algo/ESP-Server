# Google Live PR2 stability fixes — evidence (adhoc-2026-05-19-google-live-pr2-stability)

**Plan:** `.omc/plans/google-live-fix-execution-2026-05-19.md` §4 (PR2) and `.omc/plans/google-live-stability-bargein-v2.md` §9 (P2.1–P2.8)
**Generated:** 2026-05-19
**Owning agent:** pr2-stability (team `google-live-fix`)

## Verification matrix row

| Task | AC | PASS / FAIL / PARTIAL | Evidence |
|---|---|---|---|
| adhoc-2026-05-19-google-live-pr2-stability | P2.1 goAway detection | PASS | client.py `_normalize_message` yields `{type:session_expiring, time_left_ms:int}`; `tests/test_google_live_reconnect.py::GoAwayDetectionTest` 5/5 pass |
| adhoc-2026-05-19-google-live-pr2-stability | P2.2 proactive reconnect on session_expiring | PASS | session_provider/google_live.py `_schedule_proactive_reconnect` + `_proactive_reconnect`; `ProactiveReconnectTest` 2/2 pass |
| adhoc-2026-05-19-google-live-pr2-stability | P2.3 deque replay buffer maxlen ~34 | PASS | `_pending_reconnect_audio = deque(maxlen=_get_reconnect_buffer_capacity())`; `ReconnectBufferCapacityTest` 2/2 pass; `MidStreamBlipReconnectTest::test_mid_stream_audio_replays_from_deque_on_successful_reconnect` PASS |
| adhoc-2026-05-19-google-live-pr2-stability | P2.4 resampler state stitching across calls | PASS | `audio_bridge.py:_resample_pcm16` maintains `_input_resampler_state` / `_output_resampler_state`; resets on rate change |
| adhoc-2026-05-19-google-live-pr2-stability | P2.5 recv_timer_reset log on audio chunk | PASS | `client.py:_log_recv_timer_reset` emits debug log only for audio-bearing messages; `RecvTimerResetLogTest` 3/3 pass |
| adhoc-2026-05-19-google-live-pr2-stability | P2.6 retry policy (max_retries=6, backoff_ms=250) | PASS | `config.yaml` already has 6/250; `config_loader.py:GOOGLE_LIVE_DEFAULTS["reconnect"]` synced from 3/500 → 6/250; `test_config_voice_mode_merge.py::test_google_live_missing_fields_get_production_defaults` PASS |
| adhoc-2026-05-19-google-live-pr2-stability | P2.7 classify_error fallback for auth/quota/invalid_config | PASS | `_try_reconnect` short-circuits for `_NON_RETRIABLE_ERROR_CLASSES`; `ClassifyErrorRoutingTest` 4/4 pass; observability log `Google Live classify_error kind=X retry=yes/no` added per plan §8 |
| adhoc-2026-05-19-google-live-pr2-stability | P2.8 unit tests (goAway, mid-stream blip, replay, classify_error) | PASS | `tests/test_google_live_reconnect.py` 19/19 pass (10 pre-existing + 9 new) |
| adhoc-2026-05-19-google-live-pr2-stability | AC1 soak ≥ 9/10 cycles | UNVERIFIABLE | requires physical robot + 10-cycle soak (`scripts/google_live_robot_soak.py`); deferred to PR5 per plan §7 (robot availability risk) |
| adhoc-2026-05-19-google-live-pr2-stability | AC5a auth error → fallback classic_pipeline | PASS | `NonRetriableErrorTest::test_auth_error_skips_reconnect_and_falls_back` PASS; `ClassifyErrorRoutingTest::test_auth_error_logs_no_retry_and_returns_false` PASS |
| adhoc-2026-05-19-google-live-pr2-stability | AC5b 5s network cut → reconnect <8s, no fallback | PARTIAL | unit-level coverage via `MidStreamBlipReconnectTest::test_mid_stream_blip_routes_to_retry_path` (max_retries=3 attempts; classify_error=network, retry=yes). End-to-end iptables-drop test deferred to PR5 |
| adhoc-2026-05-19-google-live-pr2-stability | AC5c quota 429 → fallback | PASS | `ClassifyErrorRoutingTest::test_quota_error_logs_no_retry_and_returns_false` PASS (mock-level) |
| adhoc-2026-05-19-google-live-pr2-stability | AC7 observability events emitted | PARTIAL | All 9 events from plan §8 are present in code (session_expiring, proactive_reconnect, replayed_buffered_audio, user_interrupted, interrupt_debounced, model_output_unblock_timeout, reconnect attempt N, recv_timer_reset, classify_error). Real-stream emission verified via unit tests; soak emission deferred to PR5 |

**Overall verdict:** PR2 implementation complete at unit-test level. AC1 / AC5b end-to-end soak deferred to PR5 (physical robot dependency).

## Files changed (line deltas)

| File | Delta | Description |
|---|---:|---|
| `core/voice/google_live/client.py` | +45 / -3 | P2.5 — `_log_recv_timer_reset` + `_message_has_audio_chunk` helpers wired into `_next_message`; non-audio messages no-op (avoids spam) |
| `core/voice/session_provider/google_live.py` | +6 / -1 | P2.7 — `classify_error kind=X retry=yes/no` info log added to `_try_reconnect` per plan §8 observability schema |
| `config/config_loader.py` | +2 / -2 | P2.6 — `GOOGLE_LIVE_DEFAULTS["reconnect"]` synced to `max_retries=6, backoff_ms=250` (matches config.yaml; closes drift) |
| `tests/test_google_live_reconnect.py` | +185 / -3 | P2.8 — added `RecvTimerResetLogTest` (3), `ClassifyErrorRoutingTest` (4), `MidStreamBlipReconnectTest` (2); upgraded `_Logger` to record + render loguru-style template args |
| `tests/test_config_voice_mode_merge.py` | +1 / -0 | sync expected reconnect defaults with new 6/250 values (comment explains PR2 P2.6 rationale) |

**Pre-existing files NOT modified** (already implemented P2.1–P2.4, P2.7 short-circuit logic):

- `core/voice/google_live/client.py:_normalize_message` (P2.1 goAway detection — verified pre-existing at line 468–477)
- `core/voice/session_provider/google_live.py:_schedule_proactive_reconnect` + `_proactive_reconnect` (P2.2 — verified at line 254–276)
- `core/voice/session_provider/google_live.py:_pending_reconnect_audio = deque(maxlen=...)` (P2.3 — verified at line 44)
- `core/voice/google_live/audio_bridge.py:_resample_pcm16` (P2.4 — verified at line 484–505)
- `core/voice/session_provider/google_live.py:_try_reconnect` non-retriable short-circuit (P2.7 — verified at line 566–572)
- `config.yaml` reconnect block (P2.6 already at 6/250)

## Commands run + outputs

### Syntax check

```
$ python3 -c "import ast; ast.parse(open('core/voice/google_live/client.py').read()); ast.parse(open('core/voice/session_provider/google_live.py').read()); ast.parse(open('core/voice/google_live/audio_bridge.py').read()); ast.parse(open('config/config_loader.py').read()); ast.parse(open('tests/test_google_live_reconnect.py').read()); print('syntax OK')"
syntax OK
```

### Import smoke

```
$ python -c "from core.voice.google_live.client import GoogleLiveClient; from core.voice.session_provider.google_live import GoogleLiveProvider; from config.config_loader import GOOGLE_LIVE_DEFAULTS; print('imports OK'); print('reconnect defaults:', GOOGLE_LIVE_DEFAULTS['reconnect'])"
imports OK
reconnect defaults: {'enabled': True, 'max_retries': 6, 'backoff_ms': 250, 'backoff_multiplier': 2}
```

### Unit tests (PR2 scope: tests/test_google_live_reconnect.py)

```
$ pytest tests/test_google_live_reconnect.py -v
====================== 19 passed, 1 warning in 0.98s =======================
```

All 19 tests PASS (10 pre-existing + 9 new):

- `GoAwayDetectionTest::test_go_away_with_iso_duration_string_normalized_to_ms` PASS
- `GoAwayDetectionTest::test_go_away_with_plain_seconds_number` PASS
- `GoAwayDetectionTest::test_go_away_with_struct_seconds_and_nanos` PASS
- `GoAwayDetectionTest::test_go_away_without_time_left_field_yields_event_with_none` PASS
- `GoAwayDetectionTest::test_message_without_go_away_does_not_yield_session_expiring` PASS
- `NonRetriableErrorTest::test_auth_error_skips_reconnect_and_falls_back` PASS
- `ProactiveReconnectTest::test_proactive_reconnect_does_not_double_schedule` PASS
- `ProactiveReconnectTest::test_session_expiring_event_schedules_runtime_failure_path` PASS
- `ReconnectBufferCapacityTest::test_capacity_floors_at_one_for_invalid_config` PASS
- `ReconnectBufferCapacityTest::test_capacity_is_derived_from_budget_and_frame_size` PASS
- **NEW** `RecvTimerResetLogTest::test_audio_chunk_message_triggers_recv_timer_reset_log` PASS
- **NEW** `RecvTimerResetLogTest::test_sentinel_false_or_none_does_not_log` PASS
- **NEW** `RecvTimerResetLogTest::test_transcript_only_message_does_not_log_recv_timer_reset` PASS
- **NEW** `ClassifyErrorRoutingTest::test_auth_error_logs_no_retry_and_returns_false` PASS
- **NEW** `ClassifyErrorRoutingTest::test_invalid_config_error_logs_no_retry_and_returns_false` PASS
- **NEW** `ClassifyErrorRoutingTest::test_network_error_logs_retry_yes` PASS
- **NEW** `ClassifyErrorRoutingTest::test_quota_error_logs_no_retry_and_returns_false` PASS
- **NEW** `MidStreamBlipReconnectTest::test_mid_stream_audio_replays_from_deque_on_successful_reconnect` PASS
- **NEW** `MidStreamBlipReconnectTest::test_mid_stream_blip_routes_to_retry_path` PASS

### Full google_live + config suite

```
$ pytest tests/test_google_live_*.py tests/test_config_voice_mode_merge.py -q
3 failed, 135 passed, 1 skipped, 1 warning in 3.84s
```

3 remaining failures are PRE-EXISTING (verified by running suite at task start before any edits — same 3 failures appeared with the same `AssertionError: 0 != 1` traces). They all live in `tests/test_google_live_provider_fallback.py` (an untracked file added in prior session) and have a root cause unrelated to PR2:

- `test_send_failure_after_start_falls_back_to_classic_provider`
- `test_receive_failure_after_start_falls_back_to_classic_provider`
- `test_new_audio_interrupts_active_live_response_and_forwards_latest_input`

Root cause: `_DummyConn` fixture does not set `google_live.reconnect`, so `_get_live_config` injects `GOOGLE_LIVE_DEFAULTS["reconnect"]` (now `enabled=True, max_retries=6`). The provider attempts retries instead of falling back immediately, racing the test's `await asyncio.sleep(0)`. These tests need to set `reconnect.enabled=False` on their `_DummyConn` fixture to assert immediate fallback. Out-of-scope for PR2 (test-fixture drift, not production code).

### Smoke (Google Live websocket)

SKIPPED — no `GOOGLE_API_KEY` in environment. Real-stream smoke deferred to PR5.

## Critique-before-close (6 honesty questions)

1. **Root cause vs symptom** — Solved real issues: (a) GOOGLE_LIVE_DEFAULTS drift was a hidden footgun where manager-api configs would silently get OLD 3/500 retry policy; (b) missing classify_error observability log made the existing retry/fallback decision invisible in production logs; (c) missing recv_timer_reset log made the 60s timeout reset behavior unverifiable from logs. Did NOT do "while I'm here" patches outside PR2 scope. Did NOT modify the pre-existing `test_google_live_provider_fallback.py` failures (out of scope per plan).

2. **Code vs docs** — Plan §9 lists P2.1–P2.8. My delta against the plan:
   - P2.1, P2.2, P2.3, P2.4, P2.7 (short-circuit logic) were ALREADY implemented in this worktree by prior sessions — verified by reading source.
   - P2.5 (recv_timer_reset log) — implemented.
   - P2.6 (retry policy 6/250) — config.yaml was already correct; config_loader defaults were stale → synced.
   - P2.7 (classify_error log) — added per plan §8 observability event.
   - P2.8 (unit tests) — extended existing file with 9 new tests covering recv_timer_reset, mid-stream blip, classify_error routing for all 4 kinds.
   No drift from plan.

3. **Test quality** — Tests exercise real branches: `RecvTimerResetLogTest` proves the log fires on audio chunk and skips on transcript-only, `ClassifyErrorRoutingTest` covers all 4 classify_error outcomes (auth, quota, invalid_config, network), `MidStreamBlipReconnectTest` proves the deque replays in order and the retry budget is consumed. Not ceremonial — `has_message` checks rendered loguru-style template content, not arbitrary substrings. The `_AlwaysFailNetworkClient` is a realistic stand-in for websocket close exceptions.

4. **Drift** — Closed config drift (config.yaml ↔ config_loader.py reconnect defaults). `test_config_voice_mode_merge.py` was updated in lockstep with a comment explaining the rationale. No new drift introduced. Plan v2.1 §17 already reflects the data-informed priority (PR3 deferred, AEC verdict OPTIONAL).

5. **Cold review** — A principal engineer would accept this diff. The implementation is minimal: 1 helper method + 1 log line + 1 config sync + 9 unit tests. No new abstractions. Existing P2.1–P2.4 work is verified, not duplicated. Pre-existing test failures are explicitly attributed and out-of-scope-flagged. The observability log uses the EXACT pattern from plan §8 (`Google Live classify_error kind={} retry={}`).

6. **Reproducibility** — Anyone with `.venv311` activated and the repo at this commit can run `pytest tests/test_google_live_reconnect.py -v` and get the same 19/19 PASS. The full suite command `pytest tests/test_google_live_*.py tests/test_config_voice_mode_merge.py -q` will yield `135 passed, 3 failed (pre-existing)`. No machine-specific dependencies.

## Status: DONE (PR2 unit-test scope) / PARTIAL (overall plan)

- **DONE** at the unit-test level for P2.1–P2.8 — all plan steps implemented; 19/19 PR2 tests pass; observability logs match plan §8 exactly.
- **PARTIAL** at the AC1/AC5b end-to-end level — physical-robot soak (`scripts/google_live_robot_soak.py`) is PR5's responsibility per plan §9 execution order. Cannot mark AC1 PASS without 10-cycle soak.

## Pre-existing issues observed (out of scope, flagged for follow-up)

1. `tests/test_google_live_provider_fallback.py` — 3 tests assume reconnect disabled by default; need explicit `reconnect.enabled=False` in `_DummyConn` fixture or test-level config override.
2. `tests/test_raise_left_arm.py` — ImportError `ToolType` from `plugins_func.register` (collection error). Unrelated to voice mode.
3. `tests/test_google_live_tool_calls.py` — 5 failures appeared in cross-file collection ordering but PASS when run in isolation; likely shared mock-state pollution. Unrelated to PR2.
