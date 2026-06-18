# P1-orchestrator - Task Evidence

**Date:** 2026-06-17
**Task type:** AD-HOC
**Owning repo:** `robot/esp32-server/main/tbot-server`

## Task-Start

**Task ID:** P1-orchestrator

**Goal:** Implement the audio-channel model so one `SessionMode` owns audio at a time: `DORMANT`, `CONVERSATION`, or `LESSON`. Idle Live closes to zero-cost dormant; lesson start suspends Live; lesson terminal releases the channel; per-device/household Live-minute budget degrades to TTS-only or friendly break.

**Files expected in scope:**

- `core/connection.py` - orchestrator state and audio-channel ownership.
- `core/voice/session_provider/google_live.py` - lazy Live open, idle close, budget admission/degrade, Live usage accounting.
- `core/voice/live_admission.py` - in-memory Redis-compatible budget/resumption contract.
- `core/lesson/runtime.py` - lesson start/terminal channel transitions.
- `tests/test_session_orchestrator.py` - focused FSM/budget regression tests.
- Existing Google Live/lesson tests as regression coverage.

**Acceptance criteria:**

- AC1: `SessionMode {DORMANT, CONVERSATION, LESSON}` owns the single audio channel; exactly one mode holds it.
- AC2: idle >= configured N seconds closes Live and returns to `DORMANT`; next inbound audio lazily re-opens Live.
- AC3: lesson start suspends Live, persists the resumption handle through the configured store interface, enters `LESSON`, and lesson terminal returns to `DORMANT`/`CONVERSATION`.
- AC4: per-device/household daily Live-minute budget plus reconnect-storm gate; over budget degrades to EdgeTTS/friendly break instead of opening Live.

**Verification plan:**

- Red/green focused pytest for orchestrator FSM transitions and budget degrade.
- Targeted Google Live and lesson regression tests.
- Syntax check on changed Python files.
- Grep/audit for mode and budget hooks.

## Read-Before-Code

Required docs read from workspace root:

- `docs/system-design/production-unified-runtime.md` section 4.
- `docs/system-design/production-unified-runtime.md` section 7.

Reality checks against current code:

- There is no current `SessionMode` owner. `ConnectionHandler` starts Google Live eagerly on connection and the lesson runtime can interleave over an open Live socket.
- `core/voice/live_admission.py` already exists with an in-memory budget store and resumption-store stub. This satisfies the STOP-IF dependency path: Redis is not present, so the implementation must stay behind this interface and document the Redis dependency.
- Config currently has `server.audio_admission`; `LiveAdmissionGate.from_config()` reads `live_admission`. The implementation must support the existing config key without silently assuming the brief's names.
- `LessonRuntime` has no terminal callback into `ConnectionHandler`, so terminal state cannot release audio ownership yet.

## Evidence-Collect

### RED

```bash
python3 -m pytest tests/test_session_orchestrator.py -q
```

Initial result: RED. Collection failed on `ImportError: cannot import name 'SessionMode' from 'core.connection'`, after adjusting the test harness to use the repo's existing connection import stubs instead of failing early on the optional local `mcp` dependency.

```bash
./.venv311/bin/pytest tests/test_google_live_reconnect.py::ReconnectAdmissionAccountingTest::test_reconnect_attempt_records_against_admission_gate_before_live_open -q
```

Result: RED. The new reconnect-storm acceptance check failed because reconnect attempts were not recorded against `live_admission_gate` before reopening Live: `records == []`, expected `["device-1"]`.

### GREEN / Regression

Commands run from `/Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server`:

```bash
python3 -m pytest tests/test_session_orchestrator.py tests/test_lesson_runtime.py tests/test_lesson_voice_nonregression.py -q
```

Result: PASS - 62 passed, 1 warning.

```bash
python3 -m pytest tests/test_voice_consent_gate.py tests/test_google_live_reconnect.py tests/test_google_live_provider_fallback.py -q
```

Result: PASS - 66 passed.

```bash
python3 -m pytest tests/test_google_live_event_mapping.py tests/test_google_live_bargein.py -q
```

Result: PASS - 89 passed.

```bash
./.venv311/bin/pytest tests/test_session_orchestrator.py tests/test_google_live_reconnect.py tests/test_google_live_event_mapping.py tests/test_lesson_runtime.py tests/test_product_toolset.py -q
```

Result: PASS - 110 passed, 1 warning (`pydub` imports deprecated stdlib `audioop`).

```bash
./.venv311/bin/pytest -q
```

Result: PASS - 485 passed, 4 skipped, 1 warning (`tests/test_aec_processor.py` imports deprecated stdlib `audioop`).

```bash
./.venv311/bin/pytest tests/test_google_live_reconnect.py::ReconnectAdmissionAccountingTest::test_reconnect_attempt_records_against_admission_gate_before_live_open -q
```

Result: PASS - 1 passed.

```bash
python3 -m py_compile core/voice/session_provider/google_live.py core/connection.py core/lesson/runtime.py core/voice/live_admission.py core/voice/session_orchestrator.py tests/test_session_orchestrator.py
```

Result: PASS - exit 0.

```bash
./.venv311/bin/python -m py_compile core/voice/session_provider/google_live.py core/connection.py core/lesson/runtime.py core/voice/live_admission.py core/voice/session_orchestrator.py tests/test_session_orchestrator.py tests/test_google_live_reconnect.py
```

Result: PASS - exit 0.

```bash
git diff --check -- core/voice/session_orchestrator.py core/connection.py core/voice/session_provider/google_live.py core/voice/live_admission.py core/lesson/runtime.py tests/test_session_orchestrator.py docs/qa/ad-hoc/2026-06-17-P1-orchestrator.md
```

Result: PASS - exit 0.

```bash
git diff --check -- core/voice/session_orchestrator.py core/connection.py core/voice/session_provider/google_live.py core/voice/live_admission.py core/lesson/runtime.py tests/test_session_orchestrator.py tests/test_google_live_reconnect.py docs/qa/ad-hoc/2026-06-17-P1-orchestrator.md
```

Result: PASS - exit 0.

## AC Audit

- AC1: `core/voice/session_orchestrator.py` defines `SessionMode`; `core/connection.py` stores `session_mode` and `audio_channel_owner`, routes audio through `_route_audio_message`, and refuses voice routing while `LESSON` owns the channel. Covered by `tests/test_session_orchestrator.py` mode tests.
- AC2: `GoogleLiveProvider.start_session()` stays dormant for real orchestrated connections; `_ensure_live_open_for_audio()` opens lazily on first inbound audio; `_idle_close_loop()` closes Live after configured idle timeout and returns to dormant. Covered by `test_orchestrated_start_is_dormant_until_first_audio` and `test_idle_timeout_closes_live_and_returns_to_dormant`.
- AC3: `ConnectionHandler.enter_lesson_mode()` persists the resumption handle via `live_resumption_store`, closes Live resources, and sets `LESSON`; `LessonRuntime` calls `enter_lesson_mode()` before start and `release_lesson_mode()` on completed/failed terminal paths. Covered by `test_enter_lesson_suspends_live_and_owns_audio_channel`, `test_lesson_terminal_releases_audio_channel_to_dormant`, and existing lesson runtime tests.
- AC4: `LiveAdmissionGate` enforces device/household daily budgets and reconnect-storm limits; `GoogleLiveProvider` degrades to classic/EdgeTTS on budget exhaustion, records reconnect attempts against the admission gate before each reopen, and sends a friendly break for reconnect storms without opening Live. Covered by `test_daily_budget_exhaustion_degrades_to_tts_only`, `test_household_daily_budget_exhaustion_degrades_to_tts_only`, `test_reconnect_storm_is_rate_limited_before_live_open`, `test_over_budget_degrades_to_classic_without_opening_live`, `test_reconnect_attempt_records_against_admission_gate_before_live_open`, and `test_reconnect_storm_returns_friendly_break_without_opening_live`.

## Critique-Before-Close

- Root cause closed: Live was opened eagerly and lesson runtime had no explicit ownership boundary. The connection now owns the channel mode and provider/lesson code respect it.
- Redis dependency: runtime state remains behind the store interface. Local/default operation uses `InMemoryLiveAdmissionStore`; multi-replica operation can supply `RedisLiveStateStore` via the existing Redis client contract for resumption, Live budget, and reconnect-window accounting.
- Cost behavior: orchestrated connections are dormant until audio, Live usage is recorded when resources close, and idle close clears the billable socket. Legacy test fakes without `session_mode` keep eager behavior so existing provider tests remain stable.
- Lesson behavior: lesson start closes Live before `lesson_prepare/start/step` frames run, so lesson frames no longer interleave over a billing Live socket.
- Remaining risk: Redis is supported through the `RedisLiveStateStore` contract, but deployment still needs a real Redis URL and multi-replica rollout wiring. The full local suite passes; the only warning is the pre-existing stdlib `audioop` deprecation.
