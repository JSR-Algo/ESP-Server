# Google Live PR4 barge-in correctness — evidence (adhoc-2026-05-19-google-live-pr4-bargein)

**Plan:** `.omc/plans/google-live-fix-execution-2026-05-19.md` §5 (PR4) and `.omc/plans/google-live-stability-bargein-v2.md` §9 (P4.1–P4.6) + §7.1, §7.3, §7.4, §7.5
**Generated:** 2026-05-19
**Owning agent:** pr4-bargein (team `google-live-fix`)
**Builds on:** PR2 stability landed (see `2026-05-19-google-live-pr2.md`)

## Verification matrix row

| Task | AC | PASS / FAIL / PARTIAL | Evidence |
|---|---|---|---|
| adhoc-2026-05-19-google-live-pr4-bargein | P4.1 debounce 200ms in `_begin_user_interrupt` (MAJOR #1 fix) | PASS | Pre-existing in `core/voice/session_provider/google_live.py:950-964`; `_DEBOUNCED_INTERRUPT_REASONS = {"audio_input", "transcript_barge_in"}`; emits log `Google Live interrupt_debounced reason={} age_ms={:.0f}` per plan §8. `_last_interrupt_at = 0.0` initialized at `__init__` line 46. Tests: `InterruptDebounceTest` 3/3 pass; `TranscriptBargeInTest::test_repeated_transcripts_are_debounced_via_provider` pass |
| adhoc-2026-05-19-google-live-pr4-bargein | P4.2 guarded `end_audio_stream()` after `client.interrupt()` (MAJOR #4 fix) | PASS | Pre-existing in `google_live.py:989-1000`: guards on `getattr(self._client, "connected", False)` + `hasattr(end_audio_stream)`; `RuntimeError` swallowed with info log. `client.connected` is a normal attribute (`client.py:14`). Tests: `EndAudioStreamGuardTest` 3/3 pass (connected/disconnected/RuntimeError branches) |
| adhoc-2026-05-19-google-live-pr4-bargein | P4.3 `_unblock_timer_task` lifecycle + `bridge.close()` cancel hook (MAJOR #3 fix) | PASS | Pre-existing in `audio_bridge.py`: `_unblock_timer_task=None` (line 48), `_schedule_unblock_timeout` (232), `_unblock_after` (239), `_cancel_unblock_timer` (254), `close()` (229). `session_provider._close_live_resources` already calls `await self._bridge.close()` at line 350-354. Tests: `UnblockTimerLifecycleTest` 4/4 pass (schedule, cancel-on-allow, close-cancels-timer, zero-timeout-disables) |
| adhoc-2026-05-19-google-live-pr4-bargein | P4.4 1.5s auto-unblock if no user transcript | PASS | `_get_unblock_timeout_sec` defaults 1.5s (`audio_bridge.py:265`); `stop_output` schedules timer (line 219); transcript handler (line 86) calls `allow_model_output()` which cancels timer (line 227). Log `Google Live model_output_unblock_timeout after {:.0f} ms` per plan §8. Tests: covered by `UnblockTimerLifecycleTest::test_stop_output_schedules_unblock_timer` (50ms timeout fires) and `test_allow_model_output_cancels_pending_unblock_timer` |
| adhoc-2026-05-19-google-live-pr4-bargein | P4.5 config tune (4500 / 0.30 / server_side_vad_enabled:false) | PASS | `config.yaml:147-156` updated: `barge_in_rms_threshold:4500` (was 5000), `barge_in_min_input_duration_sec:0.30` (already 0.30, kept), `server_side_vad_enabled:false` (new explicit flag preserving plan §5). `config/config_loader.py:GOOGLE_LIVE_DEFAULTS` synced to match (PR2 drift lesson). Tests: `BargeInConfigTuneTest` 2/2 pass + `test_config_voice_mode_merge.py` updated to assert new defaults |
| adhoc-2026-05-19-google-live-pr4-bargein | P4.6 unit tests | PASS | `tests/test_google_live_bargein.py` 21/21 pass (19 pre-existing + 2 new `BargeInConfigTuneTest` cases) |
| adhoc-2026-05-19-google-live-pr4-bargein | Plan §7.5 — DO NOT add manual `activity_end` | PASS | Verified: no `activity_end` in source; `client.interrupt()` (`client.py:206-211`) uses `send_client_content(turns=[], turn_complete=False)` + guarded `end_audio_stream()` per plan §7.5. `_get_live_config()` force-overrides `disable_server_side_interruptions=True` (line 725) regardless of yaml — RMS-VAD remains the single trigger |
| adhoc-2026-05-19-google-live-pr4-bargein | AC2 barge-in latency ≤350ms log + ≤500ms tts.state=stop, 8/10 trials | UNVERIFIABLE | Requires physical robot + synthetic-audio injection via `scripts/voice_mode_websocket_audio_bargein.py` (PR5 scope). Unit-level evidence: debounce + end_audio_stream + timer lifecycle all verified |
| adhoc-2026-05-19-google-live-pr4-bargein | AC4 no false positive during 120s monologue, 3/3 trials | UNVERIFIABLE | Requires physical robot + 120s soak (PR5). Unit-level: debounce blocks rapid duplicates; timer auto-unblock prevents stuck state |
| adhoc-2026-05-19-google-live-pr4-bargein | AC7 observability events | PASS | Plan §8 events present and exercised: `interrupt_debounced` (google_live.py:958), `model_output_unblock_timeout` (audio_bridge.py:247). PR2 events preserved (session_expiring, proactive_reconnect, classify_error, recv_timer_reset, replayed_buffered_audio, user_interrupted, reconnect attempt N) |

**Overall verdict:** PR4 implementation complete at unit-test level. AC2/AC4 end-to-end deferred to PR5 (physical robot dependency — same posture as PR2 AC1/AC5b).

## Files changed (line deltas)

| File | Delta | Description |
|---|---:|---|
| `config.yaml` | +6 / -1 | P4.5 — `barge_in_rms_threshold 5000→4500`, added `server_side_vad_enabled:false` flag (preserving plan §5), comment explaining tuning rationale |
| `config/config_loader.py` | +5 / -2 | P4.5 — `GOOGLE_LIVE_DEFAULTS` synced: `barge_in_rms_threshold 5000→4500`, `barge_in_min_input_duration_sec 0.42→0.30`, added `server_side_vad_enabled:False`. Comment notes PR2 drift lesson |
| `tests/test_config_voice_mode_merge.py` | +5 / -2 | Updated `test_google_live_missing_fields_get_production_defaults` and `test_google_live_missing_section_gets_production_defaults` to assert new tuned values + new `server_side_vad_enabled` default |
| `tests/test_google_live_bargein.py` | +26 / -0 | Added `BargeInConfigTuneTest` (2 cases): asserts `config.yaml` + `GOOGLE_LIVE_DEFAULTS` match PR4 tune. Prevents regression to 5000/0.42 |

**Pre-existing files NOT modified** (PR4 code already implemented in this worktree by prior sessions — VERIFY, do not re-implement per teammate-message instruction):

- `core/voice/session_provider/google_live.py:46` — `_last_interrupt_at = 0.0` initialized
- `core/voice/session_provider/google_live.py:948-1014` — `_begin_user_interrupt` with debounce + guarded `end_audio_stream` + `_get_interrupt_debounce_sec`
- `core/voice/session_provider/google_live.py:350-354` — `await self._bridge.close()` hook in `_close_live_resources`
- `core/voice/google_live/audio_bridge.py:48,219,229-268` — `_unblock_timer_task`, `_schedule_unblock_timeout`, `_unblock_after`, `_cancel_unblock_timer`, `close()`, `_get_unblock_timeout_sec`
- `core/voice/google_live/client.py:14` — `connected = False` attribute (used by P4.2 guard)
- `core/voice/google_live/client.py:206-211` — `interrupt()` uses `send_client_content(turns=[], turn_complete=False)` (plan §7.5)
- `core/voice/google_live/client.py:83-89` — `end_audio_stream()` already raises `RuntimeError` when not connected (used by P4.2 try/suppress)

## Commands run + outputs

### Syntax check

```
$ python -c "import ast; ast.parse(open('core/voice/session_provider/google_live.py').read()); ast.parse(open('core/voice/google_live/audio_bridge.py').read()); ast.parse(open('core/voice/google_live/client.py').read()); ast.parse(open('config/config_loader.py').read()); ast.parse(open('tests/test_google_live_bargein.py').read()); ast.parse(open('tests/test_config_voice_mode_merge.py').read()); print('syntax OK')"
syntax OK
```

### Import smoke + config sanity

```
$ python -c "import yaml; data = yaml.safe_load(open('config.yaml')); gl=data['google_live']; print('barge_in_rms_threshold=', gl['barge_in_rms_threshold']); print('barge_in_min_input_duration_sec=', gl['barge_in_min_input_duration_sec']); print('server_side_vad_enabled=', gl['server_side_vad_enabled']); print('disable_server_side_interruptions=', gl['disable_server_side_interruptions']); print('barge_in=', gl['barge_in'])"
barge_in_rms_threshold= 4500
barge_in_min_input_duration_sec= 0.3
server_side_vad_enabled= False
disable_server_side_interruptions= False
barge_in= False

$ python -c "from core.voice.session_provider.google_live import GoogleLiveProvider; from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge; from core.voice.google_live.client import GoogleLiveClient; from config.config_loader import GOOGLE_LIVE_DEFAULTS; print('imports OK'); print('defaults barge_in_rms_threshold=', GOOGLE_LIVE_DEFAULTS['barge_in_rms_threshold']); print('defaults barge_in_min_input_duration_sec=', GOOGLE_LIVE_DEFAULTS['barge_in_min_input_duration_sec']); print('defaults server_side_vad_enabled=', GOOGLE_LIVE_DEFAULTS['server_side_vad_enabled'])"
imports OK
defaults barge_in_rms_threshold= 4500
defaults barge_in_min_input_duration_sec= 0.3
defaults server_side_vad_enabled= False
```

Note: `config.yaml` keeps `disable_server_side_interruptions: false` at the file level, but `session_provider/google_live.py:_get_live_config()` line 725 **force-overrides this to `True`** regardless of yaml (code comment confirms: "For TBOT we always want server-side interruptions filtered"). Plan §5 invariant is preserved. The new `server_side_vad_enabled: false` flag is a forward-looking explicit knob — not yet wired into the trigger path (consistent with plan §5 deferring that decision until AC2/AC4 fail).

### Unit tests — PR4 bargein

```
$ pytest tests/test_google_live_bargein.py -v
====================== 21 passed, 1 warning in 1.47s =======================
```

All 21 cases PASS (19 pre-existing + 2 new `BargeInConfigTuneTest`):

- `InterruptDebounceTest::test_rapid_audio_input_interrupts_are_debounced` PASS (P4.1)
- `InterruptDebounceTest::test_explicit_user_interrupt_is_not_debounced` PASS (P4.1 — explicit reason bypasses debounce)
- `InterruptDebounceTest::test_audio_input_interrupt_allowed_after_debounce_window` PASS (P4.1 — debounce window respected)
- `EndAudioStreamGuardTest::test_end_audio_stream_called_after_interrupt_when_connected` PASS (P4.2)
- `EndAudioStreamGuardTest::test_end_audio_stream_skipped_when_client_disconnected` PASS (P4.2 — disconnected guard)
- `EndAudioStreamGuardTest::test_end_audio_stream_runtime_error_is_swallowed` PASS (P4.2 — RuntimeError swallowed with info log)
- `UnblockTimerLifecycleTest::test_stop_output_schedules_unblock_timer` PASS (P4.3 + P4.4 — timer fires and unblocks)
- `UnblockTimerLifecycleTest::test_allow_model_output_cancels_pending_unblock_timer` PASS (P4.3 — transcript path cancels timer)
- `UnblockTimerLifecycleTest::test_close_cancels_unblock_timer` PASS (P4.3 — bridge.close cancels)
- `UnblockTimerLifecycleTest::test_zero_timeout_disables_auto_unblock` PASS (P4.4 — 0 timeout disables)
- `TranscriptBargeInTest::*` 6/6 PASS (debounce wired through transcript path)
- `InterruptionOutputAgeGuardTest::*` 3/3 PASS (interruption output-age guard)
- **NEW** `BargeInConfigTuneTest::test_config_yaml_barge_in_thresholds_match_pr4_tune` PASS (asserts config.yaml has 4500 / 0.30 / false)
- **NEW** `BargeInConfigTuneTest::test_google_live_defaults_match_pr4_tune` PASS (asserts GOOGLE_LIVE_DEFAULTS synced)

### Regression check — PR2 must not break

```
$ pytest tests/test_google_live_reconnect.py -v
====================== 19 passed, 1 warning in 0.92s =======================
```

PR2 baseline 19/19 PASS — no regression.

### Config merge

```
$ pytest tests/test_config_voice_mode_merge.py -v
====================== 4 passed in 0.03s =======================
```

All 4 PASS with updated assertions matching new tuned defaults.

### Full sweep

```
$ pytest tests/test_google_live_*.py tests/test_config_voice_mode_merge.py -q
3 failed, 137 passed, 1 skipped, 1 warning in 3.58s
```

**137 passed** (up from PR2 baseline 135 — my 2 new `BargeInConfigTuneTest` cases). **3 failed** are the EXACT same pre-existing failures documented in PR2 evidence (`tests/test_google_live_provider_fallback.py`), out-of-scope for PR4. No new failures introduced.

Pre-existing failures (verified unchanged from PR2 baseline):

- `test_google_live_provider_fallback.py::test_send_failure_after_start_falls_back_to_classic_provider`
- `test_google_live_provider_fallback.py::test_receive_failure_after_start_falls_back_to_classic_provider`
- `test_google_live_provider_fallback.py::test_new_audio_interrupts_active_live_response_and_forwards_latest_input`

Root cause (per PR2 evidence): `_DummyConn` fixture doesn't set `reconnect.enabled=False`, so the provider retries instead of falling back — test-fixture drift, not production code.

### Smoke (Google Live websocket)

SKIPPED — no `GOOGLE_API_KEY`; physical-robot real-stream smoke is PR5 scope.

## Critique-before-close (6 honesty questions)

1. **Root cause vs symptom** — The 4 MAJOR bugs are all fixed: (a) debounce blocks rapid double-fire `user_interrupted` (MAJOR #1); (b) `end_audio_stream` is guarded by `client.connected` + `RuntimeError` suppression (MAJOR #4 race); (c) `_unblock_timer_task` has full lifecycle with `bridge.close()` cancel (MAJOR #3 task leak); (d) threshold dropped 5000→4500 per baseline data, with `0.30s` min duration (already at 0.30 from prior session). Plan §7.5 invariant preserved — no manual `activity_end`; `disable_server_side_interruptions` force-overridden True in `_get_live_config()`. Bulk of P4.1–P4.4 was pre-existing — verified via Read, NOT re-implemented (teammate-message lesson "much was pre-implemented").

2. **Code vs docs** — Plan v2.1 §9 P4.1–P4.6 + §7.1, §7.3, §7.4, §7.5 each map to specific source locations documented in the "Files NOT modified" section above. P4.5 was the only delta: `config.yaml`, `config_loader.py`, and the 2 new assertion tests. No drift from plan.

3. **Test quality** — Tests exercise real branches: `InterruptDebounceTest` covers debounce inside / outside / explicit-reason; `EndAudioStreamGuardTest` covers connected / disconnected / RuntimeError paths separately; `UnblockTimerLifecycleTest` covers all 4 timer transitions (schedule, cancel-on-allow, close-cancels, zero-disables). New `BargeInConfigTuneTest` reads the on-disk yaml — catches any future drift between config.yaml and GOOGLE_LIVE_DEFAULTS.

4. **Drift** — Closed the only drift I introduced: `config.yaml` ↔ `GOOGLE_LIVE_DEFAULTS` synced in lockstep (PR2 lesson). `test_config_voice_mode_merge.py` updated to assert the new values, preventing regression. No new drift introduced. The added `server_side_vad_enabled:false` flag is forward-looking — not currently wired into the trigger path; that wiring is gated on AC2/AC4 outcomes per plan §5.

5. **Cold review** — A principal engineer would accept this diff. Minimal change: only 4 files touched (2 prod config, 2 test). All 4 MAJOR plan bugs verified fixed via pre-existing code I read carefully. The remaining `disable_server_side_interruptions: false` in yaml (the actual yaml string) might look concerning but is intentionally overridden in `_get_live_config()` line 725 — comment in that file explains why. PR2 19/19 regression-clean. 137/140 full sweep PASS, same 3 pre-existing fails. No `--no-verify`, no skipped CI hooks.

6. **Reproducibility** — Anyone with `.venv311` activated and the repo at this commit can reproduce: `pytest tests/test_google_live_bargein.py -v` → 21/21 PASS; `pytest tests/test_google_live_reconnect.py -v` → 19/19 PASS; `pytest tests/test_google_live_*.py tests/test_config_voice_mode_merge.py -q` → 137 passed, 3 failed (same 3 pre-existing). No machine-specific deps.

## Status: DONE (PR4 unit-test scope) / PARTIAL (overall plan)

- **DONE** at unit-test level for P4.1–P4.6 — all plan steps implemented (P4.1–P4.4 pre-existing & verified; P4.5 newly tuned; P4.6 tests added). 21/21 PR4 tests pass; PR2 19/19 unchanged; observability logs match plan §8.
- **PARTIAL** at AC2/AC4 end-to-end level — physical-robot soak (`scripts/google_live_robot_soak.py`) is PR5's responsibility per plan §9 execution order. Cannot mark AC2/AC4 PASS without ≥8/10 + 3/3 trial runs on real device.

## Pre-existing issues observed (out of scope, flagged for follow-up)

1. `tests/test_google_live_provider_fallback.py` — 3 tests need explicit `reconnect.enabled=False` in `_DummyConn` fixture (PR2 follow-up, unchanged).
2. `config.yaml:117` has `disable_server_side_interruptions: false` at the yaml level but `_get_live_config()` force-overrides to `True`. The yaml value is effectively dead — future cleanup could remove it, but doing so now would risk surprising config-merge users; defer to a separate cleanup PR.
3. New `server_side_vad_enabled` flag is currently informational — it documents plan §5's decision but is not yet wired into the trigger path. Wire-in is gated on AC2/AC4 PR5 outcomes per plan.
