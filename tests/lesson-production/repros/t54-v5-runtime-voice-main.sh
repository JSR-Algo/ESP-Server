#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

cd main/tbot-server
python3 -m pytest -q \
  tests/test_lesson_cinematic_phase_routing.py::test_v5_step_entry_uses_typed_cinematic_frames_not_legacy_lesson_step \
  tests/test_lesson_cinematic_phase_routing.py::test_v5_authored_effect_prepares_and_starts_exact_phase \
  tests/test_lesson_cinematic_phase_routing.py::test_v5_terminal_completion_emits_typed_cinematic_stop \
  tests/test_google_live_audio_bridge_edges.py::GoogleLiveAudioBridgeEdgeTest::test_lesson_transition_stop_does_not_reopen_realtime_listening \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_start_asr_fallback_keeps_quiet_frames_inside_forwarded_turn \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_start_asr_fallback_detaches_each_turn_before_asr_delay \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_transition_uses_terminal_voice_stop \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_transition_settles_stale_model_speaking_after_terminal_stop \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_transition_late_echo_log_does_not_re_latch_busy_state \
  tests/test_gemini_asr_provider.py::GeminiASRProviderTest::test_gemini_asr_http_request_does_not_block_event_loop

python3 - <<'PY'
from config.config_loader import GOOGLE_LIVE_DEFAULTS

assert GOOGLE_LIVE_DEFAULTS["lesson_child_input_speech_rms_threshold"] == 650
print("T54 renderer-v5 runtime and live voice: PASS")
PY
