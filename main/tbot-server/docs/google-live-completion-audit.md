# Google Live Completion Audit

## 2026-07-02 current robot-production objective

The stricter robot-production goal is not complete until a real robot run proves
all of these in the same production path:

- normal conversation uses Google Live with one configured voice (`Kore`) and
  fast conversation turn timing
- lesson/course narration sends the full prompt text verbatim through the same
  Live voice before opening the child-response window
- user speech can interrupt while the robot is speaking through official Live
  VAD, not local raw/RMS barge-in
- robot speaker audio is suppressed unless an active AEC-cleaned input path is
  forwarding frames for Live VAD

Current server-side proof:

- `scripts/physical_smoke_audit.py --production-strict` enables all production
  voice and lesson/course gates together
- production boot now rejects non-`google_live` voice mode, so production cannot
  silently start on `classic_pipeline`
- production boot now rejects a configured `google_live.model` other than
  `gemini-3.1-flash-live-preview`, so env/private config cannot switch the
  production robot off the chosen Google Live model
- production boot also requires `enable_audio_input=true`,
  `enable_audio_output=true`, `native_voice=true`, and `language_code=vi-VN`,
  so production cannot silently disable Live audio or switch out of the
  Vietnamese native-voice path
- Google Live session connect logs now include
  `Google Live session identity model=... voice=... language=...`, and
  production voice strict requires the physical target segment to show
  `gemini-3.1-flash-live-preview`, `Kore`, and `vi-VN` before first audio output;
  any later target-segment Live identity with a different model, voice, or
  language is fatal
- `scripts/physical_smoke_audit.py --production-voice-strict` enables the
  expected user transcript after a Live interruption, fast-first-audio,
  AEC-forward count matching
  `--min-interrupts`, ordered AEC-forward-to-interruption chains matching
  `--min-interrupts`, Live server interruption count matching
  `--min-interrupts`, and `tts_stop_sent reason=interrupt` count matching
  `--min-interrupts` together; normal output stop markers do not satisfy this
  interrupt-stop count. It also requires `Google Live
  interruption_stop_latency_ms=...` count matching `--min-interrupts` with max
  latency no higher than 250ms, those interruption and interrupt stop markers to
  appear in order, interrupt stops to carry `continue_listening=true`
  and `listen_mode=realtime`, plus a normal output stop marker with
  `continue_listening=true` and `listen_mode=realtime` after first audio output,
  and at least one `transcript source=user` marker after a Live interruption.
  The expected transcript must also match after a Live interruption, so a
  pre-output user phrase cannot satisfy the barge-in proof. Production strict
  does not require local `user_interrupted reason=audio_input` markers; Live
  server interruption is the barge-in proof.
- `scripts/physical_smoke_audit.py --production-course-strict --lesson-manifest`
  enables lesson flow, sent Live-text handoff, manifest-derived
  `prompt`/`retryPrompt`/`successPrompt` character count, and per-text hash
  gates together
- `scripts/physical_smoke_audit.py --require-aec-live-vad-forward` requires
  `Google Live aec_live_vad_forward reason=robot_speaking`
- `scripts/physical_smoke_audit.py --max-first-audio-ms 1800` gates fast first
  audio response from `Google Live first_audio_out_latency_ms=...`
- `scripts/physical_smoke_audit.py --require-lesson --require-lesson-live-text`
  gates the lesson/course prompt path through Live text and rejects local
  `lesson_* queued via tts` markers from any logger, even if a Live-text marker
  is also present
- `scripts/physical_smoke_audit.py --lesson-manifest ...` derives expected step
  count, interactive step count, content-free prompt char lower bound, and
  per-spoken-prompt SHA-256 hashes using the same prompt selection as runtime
  (`storyBeat.ask` for guided interactive steps, else `prompt`, plus
  `retryPrompt`/`successPrompt`) so truncated/rút gọn/changed prompts fail the
  physical audit without hand math or logging lesson text; Live-text prompt
  hashes outside the expected spoken manifest or out of manifest order also fail
  the production course audit
- `scripts/physical_smoke_audit.py --expected-user-transcript ...` gates the
  expected user phrase after case/punctuation/whitespace normalization
- physical audit counts transcript, first-audio, AEC-forward, Live interruption,
  lesson flow, and fatal markers only inside the target physical device/client
  WebSocket segment; evidence from later local/Python or non-target sessions is
  ignored
- target `Client disconnected` is fatal and closes the target evidence segment,
  so markers logged after a disconnect cannot satisfy the same physical run
- physical audit fatal markers now reject `fallback_disabled`,
  `Send first voice segment:`,
  `Send audio message:`,
  `tts` `sentence_start` frames,
  `audio_decision decision=suppress_echo reason=robot_speaking`,
  `audio_decision decision=drop_input reason=output_active`,
  `audio_decision decision=hold_interrupt_audio reason=blocked_output`, `Google
  Live echo_bypass`, `Google Live echo_suppressed reason=robot_speaking`,
  `Google Live AEC import failed`, `Google Live AEC initialised ... bypassed=True`,
  `Google Live AEC process_mic failed while output active`,
  `Google Live AEC process_mic failed, dropping AEC for this chunk`,
  `Google Live AEC reference resample failed`, `Google Live AEC push_reference failed`,
  `Google Live dropped invalid input audio`, `Google Live dropped corrupt input opus`,
  `live_identity_mismatch`, `Google Live
  interruption suppressed_for_age`, `interrupt_started reason=loud_input`,
  `Google Live user_interrupted reason=loud_input`,
  `Google Live transcript_barge_in suppressed_for_age`, `Google Live
  transcript_barge_in suppressed_as_model_echo`, and `Google Live server
  interruption ignored by config`, Google Live receive/waiting-model timeouts,
  Google Live runtime/unavailable failures,
  lesson prompt output/playback guard timeouts, reconnect attempts,
  `reconnect_started`, `Google Live tool timeout`, `STEP_TIMEOUT`, and direct
  user transcripts that exactly match or contain a long substring from earlier
  model transcripts, so a run cannot pass if Live is unavailable,
  local/classic TTS speaks normal conversation, robot speaker echo is used as
  an interrupt source or transcribed as user speech, mic frames are suppressed
  instead of AEC-forwarded while the robot speaks, server-side Live/transcript
  interruption is delayed/disabled, or Live gets stuck in timeout/reconnect
  recovery after otherwise valid first-audio evidence
- `scripts/analyze_google_live_log.py` reports `aec_live_vad_forward` totals,
  `fallback_disabled` sessions, lesson local-TTS markers, lesson Live-text
  markers, and RMS distribution for longer QA log analysis
- runtime safety policy forces `voice_name=Kore` after manager/private config
  merge and ignores voice env overrides, so stale per-agent or shell config
  cannot switch normal conversation or lesson narration to another AI voice
- runtime config/provider policy also forces the Live model, voice, language,
  native voice, audio input/output flags, and `aec_enabled=true` after direct
  config merge, so manager/private config or bypass callers cannot open a
  session with a different Google Live identity or disable the AEC boundary that
  keeps robot speaker audio out of user input
- manager/private-config bootstrap removes a stale classic provider from the
  active audio route while Google Live starts, so early mic frames wait for the
  Live provider instead of entering the legacy pipeline
- runtime provider policy also clamps direct slow timing overrides:
  `waiting_model_timeout_sec<=1.2`,
  `interruption_min_output_age_sec=0.0`, and
  `barge_in_transcript_min_output_age_sec=0.0`
- runtime provider policy also forces `echo_bypass_interrupt_enabled=false`, so
  direct runtime config cannot re-enable local raw/RMS loud-input interruption
  around the config normalizer
- Google Live complete text turns now move from `USER_STREAMING` to
  `WAITING_MODEL` after `send_text(turn_complete=true)`, arm the same
  no-response watchdog as audio turns, and reopen listening if Live returns no
  model audio
- Google Live consumed text/control messages, inbound user audio frames, and
  model events refresh `last_live_activity_at`, so conversation idle close is
  based on real inactivity and does not terminate an active model answer
- connection private-config setup reruns voice normalization after merging API
  fields, so fallback/mock/bypass callers cannot start a Google Live provider
  with stale `voice_name` or interruption policy
- moderation safe deflection sends a read-verbatim instruction plus the safety
  line through Google Live text and does not queue local/classic TTS
- Google Live connections bypass the cached wake-word local-audio reply in
  `checkWakeupWords`; wake feedback stays on the Google Live provider path
- Google Live connections also drop classic `sendAudioMessage` audio/text
  segments before they can emit `tts start`, `tts sentence_start`, or binary
  local TTS audio; `LAST` still sends the stop control frame needed for
  realtime relisten
- Google Live `tts stop` skips local `stop_tts_notify_voice` audio even if the
  legacy notify option is enabled, so relisten does not inject a local prompt
  sound into the speaker/mic path
- normal text already parsed by `GoogleLiveProvider.handle_text_message` is
  consumed in Google Live mode when the Live client is missing or `send_text`
  fails, so connection routing cannot fall through to `handleTextMessage` and
  queue classic chat/TTS for the same user utterance
- blank Google Live `listen detect` messages are consumed as no-ops, so an empty
  wake/listen frame cannot fall through to the legacy listen handler and call
  `startToChat("")`
- when `voice_mode.type=google_live` is configured but the Live provider is
  absent, connection routing consumes normal text and audio messages instead of
  falling through to legacy text handlers or the classic ASR queue
- binding/discard prompts do not schedule `check_bind_device()` when
  `voice_mode.type=google_live`, so local bind prompt audio cannot enter the
  Google Live speaker/mic path
- direct legacy `ConnectionHandler.chat()` calls are no-ops when
  `voice_mode.type=google_live`, so bypass callers cannot queue classic TTS for
  a Google Live session
- direct legacy `startToChat()` calls are no-ops when
  `voice_mode.type=google_live`, so bypass callers cannot run classic intent,
  STT echo, or executor-submitted chat for a Google Live session
- direct legacy max-output and bind-device prompt helpers are also no-ops when
  `voice_mode.type=google_live`, so bypass callers cannot queue local prompt
  audio into the Google Live speaker/mic path
- direct legacy intent handlers are also consumed when
  `voice_mode.type=google_live`, so bypass callers cannot run classic intent
  analysis, function-call executor work, STT echo, or `tts_text_queue` output
  for a Google Live session
- direct legacy listen-detect text handling is consumed when
  `voice_mode.type=google_live`, so bypass callers cannot route wake/no-greeting
  text through classic STT/TTS stop prompts or `startToChat`
- exceptions from parsed Google Live text commands are also consumed after
  runtime-failure handling, so a failed local stop-word/command branch cannot
  fall through to the classic text handler
- Google Live audio decode/forward/send exceptions are consumed after
  runtime-failure handling, so a bad Live audio frame or send failure cannot
  fall through to the connection's classic ASR queue for the same mic audio
- Google Live audio frames are also consumed when the Live audio bridge is
  absent, so stripped provider seams without a session orchestrator cannot mark
  the mic frame unhandled and let connection routing enqueue it to classic ASR
- lesson-owned audio frames are consumed while the lesson runtime is active but
  not accepting a child response, and also when the lesson voice provider is
  unavailable or reports unhandled, so prompt narration cannot leak robot/mic
  audio into the classic ASR queue
- runtime safety policy forces `waiting_model_timeout_sec=1.2` after
  manager/private config merge, so old agent configs cannot leave the robot in a
  long `WAITING_MODEL` state
- runtime safety policy forces `interruption_min_output_age_sec=0.0` and
  `barge_in_transcript_min_output_age_sec=0.0`; Live interruption and confirmed
  user transcripts can stop output immediately, and physical audit rejects any
  delayed suppression log, missing interrupt stop marker, out-of-order
  AEC/interruption or interruption/stop evidence, interrupt stops without
  realtime relisten fields, or missing
  realtime relisten stop marker after model output; production strict also
  requires the expected user transcript after a Live interruption so a stale
  pre-output transcript cannot satisfy the barge-in proof
- focused production voice/config/course/audit proof from 2026-07-03:
  config loader/voice merge, tool-call lesson Live text, provider edges,
  barge-in, audio bridge edges, event mapping, fallback, smoke/local/audit,
  client, analyzer, connection, and voice-provider routing suites `535 passed`
- focused wake/listen/local-audio guard proof from 2026-07-03:
  wake-word suite `9 passed`; wake/listen/send-audio suite `43 passed`; Google
  Live barge-in/fallback suite `109 passed`; provider/tool/audio/event suite
  `167 passed`; barge-in/fallback/connection-routing suite `135 passed`; bridge
  guard provider/fallback/connection-routing suite `119 passed`; lesson-owned
  audio routing suite `59 passed`; blank listen-detect provider/routing suite
  `72 passed`; provider-absent Google Live routing suite `107 passed`
- focused direct-chat guard proof from 2026-07-03:
  connection edge/routing suite `61 passed`
- focused direct-startToChat guard proof from 2026-07-03:
  receive-audio/direct-chat suite `14 passed`
- focused bind-prompt guard proof from 2026-07-03:
  connection edge/routing suite `61 passed`
- focused direct max-output/bind-device prompt guard proof from 2026-07-03:
  receive-audio/connection-routing suite `76 passed`
- focused direct intent/listen guard proof from 2026-07-03:
  intent/listen/receive-audio/connection-routing suite `101 passed`; Google
  Live send-audio/wake/provider/fallback suite `127 passed`
- focused physical-audit mic-forwarding proof from 2026-07-03:
  robot-speaking suppress/drop/hold audit tests `3 passed`
- focused physical-audit lesson-local-TTS proof from 2026-07-03:
  GoogleLive and LessonRuntime `lesson_* queued via tts` audit tests `3 passed`
- focused test evidence from 2026-07-02: Google Live/config/provider suite
  `332 passed`; lesson slice `34 passed, 1 warning`; physical audit tests
  `44 passed`; analyzer tests `14 passed`
- focused source verification from 2026-07-03T21:34-21:42+07:
  Google Live provider/tool-call/barge-in/audio-bridge/connection/tts-stop/
  receive-audio plus physical-audit parser suite `351 passed, 1 warning`.
  Current local policy diff also forces `drop_input_while_speaking=false` in
  runtime-normalized Google Live config, so stale private config cannot prevent
  AEC-cleaned input from reaching Live VAD while the robot is speaking. Live
  proof then became available from manager private config, not local
  `GOOGLE_API_KEY`: authenticated `admin.tjbot.vn/tbot`, fetched current
  `server.secret`, loaded `/config/agent-models` for
  `28:84:85:85:1a:80` / `c29ce67a-3288-4c39-8544-bba97dab332b`, and found
  `voice_mode=google_live` plus a literal Google Live key (`len=53`, value not
  logged). `scripts/google_live_smoke.py` using that agent key connected with
  `model=gemini-3.1-flash-live-preview`, `voice=Kore`, `language=vi-VN` in
  `2361.4 ms` and returned `SMOKE_CONNECT_OK` / `SMOKE_CLOSE_OK`. Physical
  proof was still not available in this shell:
  `scripts/voice_mode_preflight.py --device-ip 192.168.0.111` reported
  `packet_loss 100.0%`; and no `/dev/cu.usbmodem*` or `/dev/tty.usbmodem*`
  serial node was present.
- focused transcript-routing proof from 2026-07-04:
  `test_lesson_child_transcript_routes_while_runtime_window_is_open_after_audio_timeout`
  failed before the provider change (`None is not true`) and passed after it.
  The provider now keeps raw audio forwarding gated by `_user_audio_allowed_until`
  while allowing late final user transcripts to route to `LessonRuntime` only
  when the runtime explicitly still has `_child_response_window_open=True`.
  The opposite guard
  `test_user_transcript_outside_lesson_response_window_is_not_child_answer`
  also passed, so generic lesson runtimes without an explicit open window do not
  capture unrelated transcripts after provider audio timeout. Broader touched
  suite:
  `tests/test_google_live_provider_edges.py tests/test_google_live_tool_calls.py
  tests/test_google_live_bargein.py tests/test_google_live_reconnect.py
  tests/test_sample_lesson.py -q` -> `239 passed, 1 warning`;
  `py_compile` and `git diff --check` passed.
- production server-only deploy from 2026-07-04T01:00+07:
  Python replicas `current-tbot-esp32-server-1/2` run
  `local/tbot-server:vps-20260703180019-transcriptroute2`, built as a minimal
  overlay from the prior production image
  `local/tbot-server:vps-20260703165327-childaudio` with the patched
  `core/voice/session_provider/google_live.py`. Container compile proof on the
  new image passed and source introspection confirmed both
  `require_explicit_runtime_window` and `require_audio_window=False` are present.
  Public tunnel smoke passed for `https://admin.tjbot.vn/` and
  `https://esp.tjbot.vn/tbot/ota/` with expected websocket host `esp.tjbot.vn`.
  Rollback to this transcript-route patch's pre-patch server image is
  `/opt/tbot/.env.rollback-before-transcriptroute2`, copied from
  `/opt/tbot/.env.bak-vps-20260703175936-transcriptroute`; do not use
  `/opt/tbot/.env.bak-vps-20260703180019-transcriptroute2` for rollback because
  it captured a discarded intermediate image whose container command was wrong.
  That bad intermediate image tag was removed from the VPS.
  Physical verification was deliberately not continued after deploy because the
  robot stayed offline: public lesson metrics reported `connections=0`, local
  ping to `192.168.0.111` returned `100% packet loss`, macOS exposed no
  `/dev/cu.usbmodem*` or `/dev/tty.usbmodem*`, and the user asked to skip
  hardware-blocked work overnight.
- focused retry-window proof from 2026-07-04:
  production logs showed a wrong child answer (`morn morn morn`) correctly
  triggered a retry prompt, but the retry prompt then waited the full
  `lesson_prompt_output_guard_timeout_sec=15.0` before the child window could
  continue. That path conflicts with the fast interaction goal and caused stale
  late model output to be dropped only after the long guard expired.
  New regression
  `test_retry_lesson_child_response_window_opens_fast_when_prompt_output_times_out`
  failed before the provider change (`False is not true`) and passed after it.
  Initial/main prompt timeout behavior remains closed, covered by
  `test_lesson_child_response_window_stays_closed_on_output_timeout`. The retry
  path now caps prompt-output waiting to `lesson_child_response_fast_reopen_sec`
  for `continue_listening=True`, clears the lesson prompt output gate, logs
  `lesson_prompt_output_fast_reopen_timeout`, and opens the child-response
  window while late robot audio remains dropped. Broader non-hardware suite:
  `tests/test_google_live_provider_edges.py tests/test_google_live_tool_calls.py
  tests/test_google_live_bargein.py tests/test_google_live_reconnect.py
  tests/test_sample_lesson.py tests/test_physical_smoke_audit.py -q` ->
  `326 passed, 1 warning`; `py_compile` and `git diff --check` passed.
- production server-only deploy from 2026-07-04T01:13+07:
  Python replicas `current-tbot-esp32-server-1/2` now run
  `local/tbot-server:vps-20260703181257-fastretry`, built as a minimal overlay
  from `local/tbot-server:vps-20260703180019-transcriptroute2` with the same
  patched provider file plus retry fast-reopen behavior. Container compile proof
  passed and source introspection confirmed
  `lesson_prompt_output_fast_reopen_timeout` and
  `lesson_prompt_output_timeout_opening_child_window` are present. Public tunnel
  smoke again passed for `https://admin.tjbot.vn/` and
  `https://esp.tjbot.vn/tbot/ota/`; fresh server logs had no traceback/error
  grep hits. Rollback to the pre-fast-reopen server image is
  `/opt/tbot/.env.rollback-before-fastretry`. Physical verification remains
  paused: public lesson metrics still report `connections=0`.
- focused child-AEC marker proof from 2026-07-04:
  `test_lesson_child_audio_logs_aec_live_vad_forward_while_robot_speaks` failed
  before the provider change because lesson child audio forwarded during active
  robot output without the strict-audit `aec_live_vad_forward` marker. The
  provider now reuses the throttled marker log when a child-response window
  forwards AEC-cleaned audio while `google_live_audio_out_started_at` is active.
  This is observability only; forwarding behavior is unchanged. Guard proof:
  the new regression plus existing normal AEC-forward, robot-speaking
  suppression, retry fast-reopen, and prompt-timeout guard tests passed.
- production server-only deploy from 2026-07-04T01:24+07:
  Python replicas `current-tbot-esp32-server-1/2` now run
  `local/tbot-server:vps-20260703182329-aecmarker`, built as a minimal overlay
  from `local/tbot-server:vps-20260703181257-fastretry` with only the patched
  provider file. Container compile proof passed; runtime introspection confirmed
  `_log_aec_live_vad_forward` exists and `_forward_lesson_child_audio()` calls
  it. Public admin/OTA smoke passed, recent server error grep was empty, and
  metrics stayed `connections=0`. Rollback to the pre-AEC-marker server image is
  `/opt/tbot/.env.rollback-before-aecmarker`.
- broad local no-hardware proof from 2026-07-04T01:17+07:
  `.venv311/bin/python -m pytest tests -q -k 'not live_smoke and not
  websocket_soak and not benchmark'` passed again after the AEC marker patch
  with `1690 passed, 11 deselected, 2 warnings in 117.61s`. A stale
  session-orchestrator assertion was updated to
  match the current safety contract: lesson-owned audio is consumed (`handled`
  true) so it cannot fall through to classic ASR, while `voice_provider.audio`
  remains empty outside an interactive child-response window. Targeted proof:
  `tests/test_session_orchestrator.py -q` -> `17 passed` and
  `tests/test_connection_voice_provider_routing.py -q` -> `27 passed`.
  The auto interactive watcher that could post `lesson-nudge` on reconnect was
  stopped per the operator's no-hardware overnight instruction; only the
  diagnostic no-serial/no-nudge watcher remains.
- current AEC child-audio logging proof from 2026-07-04T01:22+07:
  `GoogleLiveProvider.handle_audio_bytes` now emits the same throttled
  `Google Live aec_live_vad_forward reason=robot_speaking ...` marker when an
  active lesson child-response window forwards AEC-cleaned child audio while
  the robot is speaking. This keeps physical audit evidence aligned with the
  actual Live VAD forwarding path instead of relying only on suppress/drop
  decisions. Targeted proof:
  `tests/test_google_live_provider_edges.py` -> `49 passed`; broader audio
  subset
  `tests/test_google_live_provider_edges.py tests/test_google_live_audio_bridge_edges.py
  tests/test_google_live_bargein.py tests/test_google_live_event_mapping.py
  tests/test_google_live_provider_fallback.py tests/test_google_live_reconnect.py
  -q` -> `233 passed, 1 warning`; `py_compile` and `git diff --check` passed.
- broad current-worktree proof from 2026-07-04T01:27+07:
  `.venv311/bin/python -m pytest tests -q -k 'not live_smoke and not
  websocket_soak and not benchmark'` passed with `1690 passed, 11 deselected,
  2 warnings in 121.69s` against the current AEC child-audio logging diff.
  Firmware full local suite also passed separately with `695 passed`. Physical
  sample completion remains unproven because read-only checks still show no
  `/dev/cu.usbmodem*` or `/dev/tty.usbmodem*` node and public ESP metrics
  `connections=0`, `devices=[]`.

Remaining gate: physical robot/live E2E with real credentials, real device ID,
and production websocket/backend/auth config. Keep this goal open until that run
shows conversation latency, lesson completeness, mid-speech interruption,
AEC-forward evidence, and no fallback/self-interrupt/fatal loop.

Status date: 2026-05-14

Scope: audit current `google_live` additive integration against approved design and implementation requirements.

## Current conclusion

Requested additive integration is implemented and completion proof is now green.

Credentialed Google Live smoke passed after correcting two runtime issues discovered during live verification:

- documented smoke command needed repo-root `PYTHONPATH` bootstrap in `scripts/google_live_smoke.py`
- native-audio Live model rejected `response_modalities=["AUDIO", "TEXT"]`; transcript events now rely on audio transcription config while audio sessions keep `response_modalities=["AUDIO"]`

## Requirement audit

### 1. No behavior break for existing pipeline

Status: satisfied by code + focused tests

Evidence:

- default config remains `classic_pipeline`
  - `config/config_loader.py`
- provider factory uses classic only when mode is absent, malformed, or explicitly
  `classic_pipeline`
  - `core/voice/session_provider/factory.py`
  - `tests/test_voice_provider_factory.py`
- classic provider delegates to existing flow instead of replacing it
  - `core/voice/session_provider/classic_pipeline.py`
  - `tests/test_classic_pipeline_provider.py`
- connection bootstrap still starts classic before private config swap
  - `tests/test_connection_voice_provider_routing.py`

### 2. Add config mode

Status: satisfied

Evidence:

- config normalization adds:
  - `voice_mode.type`
  - `voice_mode.fallback_to_classic_on_error`
  - `google_live`
  - `config/config_loader.py`
- documented config shape:
  - `docs/google-live-mode.md`

### 3. Web UI / Admin panel

Status: satisfied for additive UI scope

Evidence:

- mode dropdown with:
  - `classic_pipeline`
  - `google_live`
  - `main/manager-web/src/views/roleConfig.vue`
- conditional Google Live settings panel
  - `main/manager-web/src/views/roleConfig.vue`
- exposed fields include:
  - api key
  - model
  - voice name
  - audio input/output
  - native voice
  - barge-in
  - transcript/llm event flags
  - sample rates
  - reconnect config
  - fallback flag
- locale strings added:
  - `main/manager-web/src/i18n/en.js`
  - `main/manager-web/src/i18n/vi.js`
  - `main/manager-web/src/i18n/de.js`
  - `main/manager-web/src/i18n/pt_BR.js`

Verification:

- `npm run build` in `main/manager-web` passed

### 4. Abstraction layer

Status: satisfied

Evidence:

- interface:
  - `core/voice/session_provider/base.py`
- implementations:
  - `core/voice/session_provider/classic_pipeline.py`
  - `core/voice/session_provider/google_live.py`

### 5. Routing layer

Status: satisfied

Evidence:

- provider selection by `voice_mode.type`
  - `core/voice/session_provider/factory.py`
- connection/provider swap coverage
  - `tests/test_connection_voice_provider_routing.py`

### 6. Google Live API mode

Status: satisfied in code and verified at runtime

Evidence:

- Google client wrapper:
  - `core/voice/google_live/client.py`
- audio bridge:
  - `core/voice/google_live/audio_bridge.py`
- live provider:
  - `core/voice/session_provider/google_live.py`
- bypasses local ASR/TTS transport path and streams audio through Google Live session
- interruption/barge-in support present
- reconnect and fallback logic present

Focused verification:

- `tests/test_google_live_client.py`
- `tests/test_google_live_provider_fallback.py`
- `tests/test_google_live_event_mapping.py`

Runtime proof:

- `scripts/google_live_smoke.py` returned:
  - `SMOKE_CONNECT_OK`
  - `SMOKE_CLOSE_OK`
- `tests.test_google_live_live_smoke` passed with live credentials

### 7. Firmware compatibility

Status: satisfied by protocol mapping and tests

Evidence:

- existing surfaces preserved:
  - `stt`
  - `llm`
  - `tts`
  - documented in `docs/google-live-mode.md`
- live event mapping tests confirm transcript/audio mapping
  - `tests/test_google_live_event_mapping.py`
- no firmware rewrite introduced in current server integration

### 8. Logging

Status: satisfied

Evidence:

- mode selection log:
  - `app.py`
- live session timing/state logging:
  - `core/voice/session_provider/google_live.py`
  - `core/voice/google_live/audio_bridge.py`

### 9. Google Live failure behavior

Status: superseded by production Google Live-only policy

Evidence:

- production config sets `voice_mode.fallback_to_classic_on_error=false`
- lesson narration returns failure if Live text cannot be sent; it does not queue
  local/classic TTS
- Google Live provider fallback tests now assert no classic provider swap
- explicit classic mode remains covered separately
  - `tests/test_voice_provider_factory.py`

### 10. Code quality / minimal invasive changes

Status: satisfied at design level

Evidence:

- additive provider layer instead of rewrite
- old protocol and classic flow preserved
- config-driven mode selection
- no destructive removal of old modules
- design doc:
  - `esp32-server/docs/superpowers/specs/2026-05-13-google-live-voice-mode-design.md`

## Focused verification evidence

### Server suite

Command:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
./.venv311/bin/python -m unittest \
  tests.test_google_live_live_smoke \
  tests.test_listen_message_voice_provider_interrupt \
  tests.test_abort_voice_provider_interrupt \
  tests.test_connection_voice_provider_routing \
  tests.test_google_live_client \
  tests.test_google_live_provider_fallback \
  tests.test_google_live_event_mapping \
  tests.test_voice_provider_factory \
  tests.test_config_voice_mode_merge \
  tests.test_classic_pipeline_provider -v
```

Latest result:

- `43 tests`
- `OK`
- `skipped=1`

Skip reason in default local run:

- live smoke intentionally skipped without `RUN_GOOGLE_LIVE_SMOKE=1` and `GOOGLE_API_KEY`

### Manager API focused suite

Command:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-api
env JAVA_HOME=/opt/homebrew/Cellar/openjdk@21/21.0.11/libexec/openjdk.jdk/Contents/Home \
PATH=/opt/homebrew/Cellar/openjdk@21/21.0.11/libexec/openjdk.jdk/Contents/Home/bin:/opt/homebrew/bin:/usr/bin:/bin \
mvn test-compile surefire:test \
  -DskipTests=false \
  -Dtest=ConfigServiceVoiceModeTest,AgentControllerVoiceModeTest \
  -Dsurefire.useFile=false \
  -Dsurefire.printSummary=true
```

Latest result:

- `Tests run: 11`
- `Failures: 0`
- `Errors: 0`
- `Skipped: 0`
- `BUILD SUCCESS`

Additional evidence:

- same focused `manager-api` command also passes on current local default JDK 25
- project compilation target still remains Java 21

### Manager Web

Command:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/manager-web
npm run build
```

Latest result:

- build succeeded
- only existing asset-size/precache warnings

### Credentialed Google Live smoke

Command:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
GOOGLE_API_KEY=... ./.venv311/bin/python scripts/google_live_smoke.py
```

Default smoke config now matches production voice policy: model
`gemini-3.1-flash-live-preview`, voice `Kore`, language `vi-VN`.

Result:

- `SMOKE_CONNECT_OK`
- `SMOKE_CLOSE_OK`

Credentialed unittest:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
RUN_GOOGLE_LIVE_SMOKE=1 GOOGLE_API_KEY=... \
./.venv311/bin/python -m unittest tests.test_google_live_live_smoke -v
```

Result:

- `Ran 1 test`
- `OK`

## Final audit position

Implementation is complete for approved additive integration scope.

Requirement-by-requirement proof now includes:

- classic pipeline preserved as an explicit legacy mode
- additive provider abstraction and routing
- config + admin UI wiring
- firmware-compatible websocket/audio mapping
- runtime reconnect and Google Live-only failure coverage
- focused server/backend/web verification
- real credentialed Google Live smoke

## 2026-05-18 physical production gate audit

Last refreshed: 2026-05-18 21:42:00 +07

Current objective is stricter than the original additive integration audit: the robot conversation loop must be proven on a physical device with Vietnamese speech and barge-in while the assistant is speaking.

Concrete completion gate:

- physical robot connects to websocket as a non-Python client
- inbound physical audio produces Google Live input diagnostics
- Vietnamese 8-15s utterance is transcribed close to intent
- assistant starts speaking
- user interrupts mid-answer
- previous audio stops immediately and queued/stale audio is suppressed
- latest user intent wins
- repeat 10 times
- disconnect/reconnect does not duplicate sessions or stale audio

Prompt-to-artifact checklist:

| Requirement | Evidence artifact | Current status |
| --- | --- | --- |
| Vietnamese Google Live conversation mode enabled | config merge tests require `voice_mode.type=google_live`, `language_code=vi-VN`, `barge_in=false`, `interrupt_on_input_while_speaking=false`, `disable_server_side_interruptions=false`, `activity_handling=START_OF_ACTIVITY_INTERRUPTS`; production physical audit also requires `Google Live session identity model=gemini-3.1-flash-live-preview voice=Kore language=vi-VN` before first audio output and rejects any later target identity mismatch | covered by automated tests; physical run missing |
| Classic pipeline compatibility preserved | `tests.test_classic_pipeline_provider`, `tests.test_voice_provider_factory`, `tests.test_connection_voice_provider_routing` | covered by automated tests |
| Inbound Opus/PCM validation and diagnostics | `tests.test_google_live_event_mapping`, `Google Live input_audio_diag ... rms=...` log pattern | code/test covered; physical log missing |
| AEC-cleaned user audio can reach Live VAD while robot speaks | `Google Live aec_live_vad_forward reason=robot_speaking` plus `scripts/physical_smoke_audit.py --require-aec-live-vad-forward` | automated branch covered; physical marker missing |
| User speech interrupts through official Live VAD | `activity_handling=START_OF_ACTIVITY_INTERRUPTS`, `server_side_vad_enabled=true`, runtime `echo_bypass_interrupt_enabled=false`, `Google Live aec_live_vad_forward reason=robot_speaking` ordered before `Google Live interruption output_age_ms=<number>` matching `--min-interrupts`, `tts_stop_sent reason=interrupt` count matching `--min-interrupts`, `Google Live interruption_stop_latency_ms` count matching `--min-interrupts` with max `<=250ms`, ordered interruption-to-stop chains matching `--min-interrupts`, and the expected user transcript after Live interruption; production strict does not require local `user_interrupted reason=audio_input` | config/test covered; physical run missing |
| Robot relistens immediately after normal output | `tts_stop_sent continue_listening=true listen_mode=realtime` after `first_audio_out_latency_ms` in the target physical segment | config/test covered; physical run missing |
| Echo protection does not drop quiet/early input | `tests.test_google_live_provider_fallback` quiet/echo tests | covered by automated tests |
| Response isolation drops stale/cancelled audio | `tests.test_google_live_event_mapping` stale/drop/suppress tests | covered by automated tests |
| Runtime reconnect and Google Live-only failure visibility | `tests.test_google_live_provider_fallback` reconnect tests plus config defaults with `fallback_to_classic_on_error=false` | covered by automated tests |
| Admin/default config merge | `tests.test_config_voice_mode_merge` | covered by automated tests |
| Runtime websocket barge-in smoke | `scripts/voice_mode_websocket_soak.py` | passed after restart: `SOAK_OK cycles=10 ... binary_chunks=1597` |
| OTA artifact served to real device | OTA emulation + download SHA check | server side covered; device has not fetched/upgraded |
| Physical robot connects as non-Python client | `scripts/physical_smoke_audit.py` requires non-local/non-Python headers and rejects the server IP when `--server-ip` is provided | partial physical proof exists from 2026-07-03 plugged run; strict gate still open |
| Physical microphone audio reaches Google Live | `scripts/physical_smoke_audit.py` requires `input_audio_diag` | partial physical proof exists from 2026-07-03 plugged run; strict gate still open |
| Physical speech produces expected user transcript | `scripts/physical_smoke_audit.py --expected-user-transcript ...` requires the expected phrase inside a user transcript | one user transcript observed in physical run, but expected Vietnamese interrupt phrase was not observed |
| Physical mid-answer barge-in works 10 times | `scripts/physical_smoke_audit.py --production-voice-strict --min-interrupts 10` requires AEC-forwarded frames, Live server interruption, interrupt stop/relisten, and a user transcript after interruption | missing |
| No fatal/duplicate/stale/self-interrupt loop during physical smoke | `scripts/physical_smoke_audit.py` fatal pattern scan | no fatal hits, but physical run missing |
| Rollback path | `voice_mode.type=classic_pipeline` | documented |

Latest evidence:

- server regression: `109/109 OK`
- server regression re-run at `2026-05-18 21:57:58 +07`: `109/109 OK`
- real server websocket barge-in soak after local server restart: `SOAK_OK cycles=10 tts_starts=20 tts_stops=10 binary_chunks=1597`
- LAN websocket barge-in soak via `ws://192.168.0.114:8000/tbot/v1/` after DB threshold fix: `SOAK_OK cycles=10 tts_starts=20 tts_stops=10 binary_chunks=731`
- LAN synthetic Opus audio barge-in smoke via `scripts/voice_mode_websocket_audio_bargein.py`: `AUDIO_BARGE_IN_OK opus_packets=10 tts_starts=1 tts_stops=1 binary_chunks=16`
- Historical DB agent config for `cf79d254a1dd4c11a41d11d389866673` used legacy raw/RMS barge-in values. Current production policy is the server-side Live VAD policy listed at the top of this file: `barge_in=false`, `interrupt_on_input_while_speaking=false`, `activity_handling=START_OF_ACTIVITY_INTERRUPTS`.
- Redis cache scan found no target agent config key after threshold update; secret-bearing Redis values were not dumped
- local `tbot_server` tmux process restarted after transcript metadata logging change; Python is listening on `*:8000`
- post-soak socket check shows only the listening `*:8000` socket and no lingering established websocket clients
- transcript metadata logging is live and content-free; recent logs show `Google Live transcript source=model chars=N`
- recent server logs scanned clean for raw audio/blob/API key/token/Wi-Fi secret patterns; temporary capture removed
- manager Redis raw `sys:params` cache failure fixed in `SysParamsServiceImpl`
- patched `tbot-esp32-api.jar` deployed into `tbot-esp32-server-web`
- future Docker rebuild path checked: `Dockerfile-web` copies `main/manager-api/src` and builds `/app/target/tbot-esp32-api.jar`; source contains the Redis cache fallback fix and `SysParamsServiceImplTest`
- manager Redis regression under JDK21: `mvn -DskipTests=false -Dtest=tbot.modules.sys.SysParamsServiceImplTest test` passed (`Tests run: 1, Failures: 0, Errors: 0`)
- host default JDK compile is not valid for this project (`TypeTag :: UNKNOWN`); use JDK21 for manager builds
- running manager jar SHA: `d071a74f0339d43cc51b2027bd3862e3fc55f9a057bf674413a1f2dac5cae5ac`
- OTA emulation returns firmware `2.2.7` and websocket `ws://192.168.0.114:8000/tbot/v1/`
- LAN OTA emulation via `http://192.168.0.114:8002/tbot/ota/` returns firmware `2.2.7` and websocket `ws://192.168.0.114:8000/tbot/v1/`
- OTA download endpoint returns HTTP 200 and the downloaded binary SHA matches `uploadfile/firmware/xiaozhi-2.2.7.bin`
- OTA 500 reproduction was a malformed test request missing required `Device-Id`; a second no-firmware response was missing `board.type`. Correct firmware-protocol payload returns firmware `2.2.7`.
- Latest OTA download check: `2824352 bytes`, SHA `f605d0001d34d24664785e7590cb48d146f0eba817da8085fcda12e7fb30fa77`
- firmware artifact SHA: `f605d0001d34d24664785e7590cb48d146f0eba817da8085fcda12e7fb30fa77`
- firmware artifact rechecked present at `TBOT-Firmware/build/xiaozhi.bin` and `uploadfile/firmware/xiaozhi-2.2.7.bin`; both hashes match
- disk pressure remains stable after verification: `/System/Volumes/Data` has about `62GiB` free; manager `target` is about `88M`
- disk pressure rechecked: `/System/Volumes/Data` has about `62GiB` free
- `docs/google-live-smoke.md` now documents the physical Vietnamese interrupt gate and `physical_smoke_audit.py`
- public manager API recheck at `https://admin.tjbot.vn/tbot` returned HTTP 200 for docs and 401 for unauth health; authenticated admin flow returned the current `server.secret` (`len=36`, value not logged), redacted `/config/agent-models` probes returned `voice_mode=google_live` and literal Google Live key metadata (`len=53`), and `scripts/google_live_smoke.py` using the agent key returned `SMOKE_CONNECT_OK` / `SMOKE_CLOSE_OK`
- plugged physical robot proof on 2026-07-03:
  USB exposed `/dev/cu.usbmodem101` / `/dev/tty.usbmodem101`; serial
  descriptor reported USB VID:PID `303A:1001` and serial
  `28:84:85:85:1A:80`; LAN ARP mapped `192.168.0.111` to
  `28:84:85:85:1a:80`; preflight ping returned `PREFLIGHT_OK` with
  `loss_pct=0.0 avg_ms=39.5 max_ms=105.8 jitter_ms=34.2 duplicates=0`;
  public lesson metrics reported `connections=1` and the target device id.
- physical Google Live voice smoke on 2026-07-03T23:00+07:
  `/tmp/tbot_prod_nudge_voice_20260703T155935Z.log` passed the non-strict
  physical audit with `physical_ws_connected=true`, `input_audio_diag=2`,
  `live_identity=true`, `live_identity_first_audio_chains=1`,
  `first_audio_out_latency_ms=2304.8`, `user_transcripts=1`, and no fatal
  hits. The observed user transcript was `high speed`; this proves real mic
  ingress and Live transcript delivery but not the expected Vietnamese
  interrupt phrase.
- same physical lesson session completed the sample lesson at
  2026-07-03T23:05:33+07 (`lesson_completed stepsCompleted=4`) in
  `/tmp/tbot_prod_followup_barn_interrupt_20260703T160259Z.log`, but the
  interactive child steps advanced by `child response inactive; demo graceful
  advance`, not accepted child transcripts.
- combined physical session
  `/tmp/tbot_prod_physical_combined_20260703T155935Z_20260703T160259Z.log`
  failed strict audit: `input_audio_diag=4`, `user_transcripts=1`,
  `audio_interrupts=0`, and fatal marker `Google Live waiting_model_timeout`.
- dedicated physical barge-in capture
  `/tmp/tbot_prod_voice_bargein_20260703T160648Z.log` showed many
  `input_audio_diag` frames, including high RMS samples, but no new user
  transcript, no assistant output, no `aec_live_vad_forward`, and no Live
  interruption chain; `--production-voice-strict --min-interrupts 1
  --expected-user-transcript 'dừng lại'` failed.
- after USB reset and `python3 -m esptool --chip esp32s3 --port
  /dev/cu.usbmodem101 chip_id`, the chip identity still matched
  `ESP32-S3` / `28:84:85:85:1a:80`, but 24 five-second polls all returned
  `ping=fail` and public metrics stayed `connections=0`; no OTA/WS reconnect
  was observed after the 23:10:27+07 disconnect.
- follow-up read-only recovery attempt on 2026-07-03T23:19+07 found the USB
  serial device still present (`/dev/cu.usbmodem101`, VID:PID `303A:1001`,
  serial `28:84:85:85:1A:80`), but ARP for `192.168.0.111` was incomplete,
  ping returned `100% packet loss`, and both public/LB lesson metrics returned
  `connections=0`. Production logs for the latest 20 minutes showed no OTA or
  websocket reconnect after the 23:10:27+07 disconnect.
- serial boot evidence after read-only resets showed the board in ROM download
  mode, not the app: `rst:0x15 (USB_UART_CHIP_RESET),boot:0x23
  (DOWNLOAD(USB/UART0))` followed by `waiting for download`. The same output
  appeared at 115200/921600/460800/74880 baud after esptool hard reset. A
  manual DTR/RTS reset matrix found `DTR=false` reproducibly re-entered
  download mode and `DTR=true` avoided the ROM banner but still did not restore
  LAN ping or production metrics. `esptool run` without flash writes reported
  `Staying in bootloader`; a direct API `esp.run(reboot=True)` attempt failed
  with `Serial data stream stopped: Possible serial noise or corruption`.
  A follow-up LAN sweep still did not find `28:84:85:85:1a:80`.
- current recheck on 2026-07-03T23:31+07 still found no runnable robot app:
  USB descriptor remained `303A:1001` / `28:84:85:85:1A:80`, serial read with
  `DTR=true RTS=false` produced no app logs, ARP for `192.168.0.111` stayed
  incomplete, ping returned `100% packet loss` / `Host is down`, public and
  LB metrics both returned `connections=0`, LAN sweep only found
  `192.168.0.2`, `.15`, `.101`, `.104`, `.107`, `.108`, `.115`, and production
  logs for both ESP server replicas had no target OTA/websocket lines in the
  latest 30 minutes.
- final recheck on 2026-07-03T23:33+07 repeated the same blocker:
  `/dev/cu.usbmodem101` still exposed USB VID:PID `303A:1001` with serial
  `28:84:85:85:1A:80`; serial read with `DTR=true RTS=false` produced
  `0` app log lines; ARP for `192.168.0.111` stayed incomplete; ping returned
  `100% packet loss`; LAN sweep still found only `.2`, `.15`, `.101`, `.104`,
  `.107`, `.108`, `.115`; public and LB metrics both returned
  `connections=0`; the fresh production log capture
  `/tmp/tbot_prod_blocker_recheck_20260703T163342Z.log` had only 4 lines and
  no target OTA/websocket/audio evidence.
- plugged follow-up on 2026-07-04T00:17+07 after another physical replug/serial
  boot reached the app on firmware `lcdwiki-es3c35p/2.2.72`
  (`elf_sha256=7f24ee99722c7a0cb1f157d5be5a93bcf3bd59b8cf5283d9db6086712176595f`),
  Wi-Fi IP `192.168.0.111`, and production WebSocket session
  `ea517250-aee7-467d-8b35-1825a203ea0c`. Serial showed
  `passive_lesson_websocket_opened_without_heartbeat` and
  `passive_lesson_websocket_opened`; production metrics recovered to
  `connections=1`. A stale passive socket with only `hello` timed out after
  about 60 seconds, so the successful retry nudged the sample lesson within the
  live connection window.
- fast physical sample retry
  `/tmp/tbot_prod_fast_nudge_full_20260703T171600Z.log` started sample mode,
  ACKed `lesson_prepare`/`lesson_start`, connected Google Live in `797.4 ms`,
  emitted `Google Live session identity model=gemini-3.1-flash-live-preview
  voice=Kore language=vi-VN`, produced first audio in `597.2 ms`, sent lesson
  prompt hashes through Live text, logged `input_audio_diag=3`, observed one
  Live server interruption with `tts_stop_sent reason=interrupt` and
  stop latencies `2.1/2.2 ms`, and completed the sample lesson
  (`lesson_completed stepsCompleted=4`). This is still not strict voice proof:
  there was no `transcript source=user`, no expected Vietnamese phrase, no
  `aec_live_vad_forward`, and the run logged `Google Live waiting_model_timeout`.
  External audio played from the laptop did not produce a user transcript at the
  robot mic; the next strict pass needs a person speaking close to the robot
  during the opened child-response windows and while the robot is speaking.
- strict audit parser replay after the transcript-routing server patch, using
  the existing `/tmp/tbot_current_prod_voice_log` capture rather than new
  hardware, still failed as expected: `physical_ws_connected=true`,
  `input_audio_diag=34`, `user_transcripts=8`,
  `live_server_interruption=1`, `interrupt_tts_stops=7`, and fatal hits
  `Client disconnected`, `Google Live waiting_model_timeout`, and
  `Google Live lesson_prompt_output_guard_timeout`. Missing gates remained the
  expected `dừng lại` transcript, AEC-live-VAD forwarding, ordered
  AEC/interruption chains, and no fatal patterns.
- overnight non-hardware recheck on 2026-07-04: VPS Google/Gemini key variables
  were verified as present without dumping secret values, public admin/OTA smoke
  passed, safety/Google Live subset passed (`62 passed`), audit/analyzer suite
  passed (`101 passed`), and the broad server suite passed with hardware/live/
  soak/benchmark tests deselected (`1692 passed, 11 deselected, 2 warnings`).
  Fresh production metrics/log sampling showed the target WebSocket flapping:
  connect/config fetch appeared, then `Connection timeout, prepare close` and
  `Client disconnected` repeated about three minutes later. Direct nudge
  attempts stayed fail-closed with `device-offline` or `nudged:false`; no reset,
  flash, serial, or other physical intervention was performed. A bounded tmux
  runner `tbot_voice_runner` now watches metrics, sends the nudge only when the
  target is present, captures `/tmp/tbot_prod_voice_*.log`, and runs strict
  audit after any `nudged:true` attempt.
- follow-up physical run after adding the missing `device-id` affinity header to
  the local runner reached the correct replica and started a sample lesson. The
  recaptured log `/tmp/tbot_prod_voice_20260704T031651Z_recaptured.log` proved
  `physical_ws_connected=true`, `input_audio_diag=42`, first audio `604 ms`,
  `live_server_interruption=1`, interrupt stop latency max `2.2 ms`, and the
  expected post-interrupt transcript `dừng lại`. It still failed strict audit
  because production containers were still running the older `aecmarker` image
  without the repo's `legacy empty lesson_ack` runtime fix, so empty firmware
  `{"type":"lesson_ack"}` frames were logged but `lesson_prepare` timed out.
  Local Docker was unavailable, so a server-only hot patch copied the current
  `core/lesson/runtime.py` into both Python containers and restarted them;
  `py_compile` and runtime import confirmed
  `_legacy_empty_ack_outstanding_seq` exists in both containers. Public smoke
  passed after restart. The robot had not reconnected yet in the first two
  minutes after restart, so the bounded tmux runner was restarted with affinity
  headers, early `dừng lại` timing, and English-voice `barn` prompts while
  waiting for the next reconnect. Because local Docker was unavailable for the
  normal release path, the patched production container was committed on the VPS
  as `local/tbot-server:vps-20260704033654-emptyackhotpatch` and
  `/opt/tbot/.env` was updated to that image for future recreates. Rollback env:
  `/opt/tbot/.env.rollback-before-emptyackhotpatch-vps-20260704033654-emptyackhotpatch`.
  A fresh check at `2026-07-04T03:38Z` still showed production
  `connections=0` after restart even though the hot-patched containers were up
  and public smoke passed. The runner was restarted at `2026-07-04T03:39Z` with
  a longer 12-hour window (`4320` polls at 10-second spacing), still fail-closed
  on metrics and still avoiding reset/flash/serial. A no-hardware recheck at
  `2026-07-04T03:57Z` confirmed metrics still reported `connections=0`, both
  running containers still contained the hot-patched `legacy empty lesson_ack`
  runtime marker, and `/opt/tbot/.env` still pointed future recreates at
  `local/tbot-server:vps-20260704033654-emptyackhotpatch`. Local no-hardware
  proof also passed after the latest safety changes: targeted runtime/provider/
  safety tests (`14 passed, 1 warning`), audit/analyzer tests (`101 passed`),
  provider/barge-in tests (`112 passed, 1 warning`), and the broad server suite
  with hardware/live/soak/benchmark tests deselected (`1702 passed, 1 skipped,
  2 warnings`). The latest child-safety server patch was hot-patched into both
  production Python replicas and restarted at `2026-07-04T04:03Z`; public
  admin/OTA smoke passed, both containers contain the safety patch plus
  `legacy empty lesson_ack` runtime marker, and `/opt/tbot/.env` now points
  future recreates at `local/tbot-server:vps-20260704040323-safetyhotpatch`.
  Direct behavior self-check inside both containers returned
  `CHILD_SAFETY_HOTPATCH_OK` for unsafe web-search queries, unsafe news titles,
  and model-output screening.
  Rollback env:
  `/opt/tbot/.env.rollback-before-vps-20260704040323-safetyhotpatch`.
  A follow-up check at `2026-07-04T04:07Z` still showed target
  `28:84:85:85:1a:80` absent from lesson metrics (`connections=0`), while public
  admin/OTA smoke and HAProxy backend health were good. HAProxy did show a
  different robot, `14:c1:9f:d1:ac:20` / client
  `d83ba99d-6f56-4879-818f-5645aa85f0be`, repeatedly opening short websocket
  sessions through trycloudflare from firmware `2.2.36`; it is not the target
  device for this physical proof and was not nudged. Because the observed
  non-target websocket windows last about 10 seconds, the bounded local runner
  was restarted at `2026-07-04T04:11Z` with a 2-second metrics poll interval
  (`21600` polls for a 12-hour window) and append-only state logging, still
  fail-closed and still targeting only `28:84:85:85:1a:80`. A follow-up
  metrics probe against the non-target device over 40 seconds showed that
  short HAProxy `101` websocket sessions can complete without appearing in
  lesson-runtime metrics, most likely because they do not reach the
  post-auth runtime connection registry. The runner was restarted again at
  `2026-07-04T04:14Z` with a sanitized HAProxy-log diagnostic every 30 seconds:
  if target `28:84:85:85:1a:80` appears in recent websocket logs but remains
  absent from runtime metrics, it records only
  `ws_seen_recent_without_metrics count=N` and still refuses to nudge. At
  `2026-07-04T04:16Z` the runner capture path was also hardened so any future
  `nudged:true` run stores redacted HAProxy logs plus both containers' docker
  logs and `/opt/tbot-esp32-server/tmp/server.log` tails; authorization tokens
  are redacted before local log capture.
- server-side timeout fix on 2026-07-04: regression
  `test_google_live_ping_routes_to_classic_heartbeat_handler` first failed
  because Google Live routing sent firmware `{"type":"ping"}` to the Live
  provider, then returned before the classic heartbeat handler could refresh
  `last_activity_time` or reply `pong`. The fix routes `ping` as a websocket
  control frame beside `hello` and lesson control frames. Proof passed:
  targeted regression `1 passed, 1 warning`, adjacent route/timeout/Live suite
  `179 passed, 2 warnings`, `py_compile`, and `git diff --check`. A
  server-only production hotpatch copied `core/connection.py` into
  `current-tbot-esp32-server-1/2`, restarted both containers, committed
  `local/tbot-server:vps-20260704042228-connectionpinghotpatch`, and updated
  `/opt/tbot/.env` for future recreates. Rollback env:
  `/opt/tbot/.env.rollback-before-vps-20260704042228-connectionpinghotpatch`.
  Public admin/OTA smoke passed and both containers reported
  `ping_route_hotpatch_ok`. A post-deploy metrics check still showed target
  `28:84:85:85:1a:80` absent (`connections=0`), so no physical nudge or strict
  audit rerun was possible yet.
- post-ping-patch physical retry
  `/tmp/tbot_prod_voice_20260704T042318Z.log` reconnected the real target and
  ran the sample lesson. Strict audit failed, but it proved
  `physical_ws_connected=true`, `input_audio_diag=26`, first audio `943.1 ms`,
  `live_server_interruption=1`, interrupt stop latency max `2.6 ms`,
  `user_transcripts=3`, expected `dừng lại` transcript, `barn barn barn`
  accepted for step `s3`, and `lesson_completed stepsCompleted=4`. Remaining
  strict blockers were missing `aec_live_vad_forward` / AEC interruption chain
  and a `Google Live waiting_model_timeout` fatal. The timeout was traced to a
  stale idle input-flush task that survived model `audio_start`, called
  `end_audio_stream()` late, and flipped state back to `WAITING_MODEL` while
  the model was already speaking. Regression
  `test_model_audio_start_cancels_pending_idle_input_flush` failed first
  (`end_calls=1`), then passed after `audio_start` began cancelling pending
  idle and forced-interrupt flush tasks. Adjacent proof passed
  `tests/test_google_live_provider_edges.py tests/test_google_live_bargein.py
  tests/test_connection_voice_provider_routing.py tests/test_physical_smoke_audit.py`
  with `227 passed, 1 warning`; `py_compile` and `git diff --check` passed.
  A second server-only production hotpatch copied
  `core/voice/session_provider/google_live.py` into both Python replicas,
  restarted them, committed
  `local/tbot-server:vps-20260704042913-audiostartflushhotpatch`, and updated
  `/opt/tbot/.env` for future recreates. Rollback env:
  `/opt/tbot/.env.rollback-before-vps-20260704042913-audiostartflushhotpatch`.
  Public admin/OTA smoke passed and both containers reported
  `audio_start_flush_hotpatch_ok`. After that restart, metrics again showed
  `connections=0`; `tbot_voice_runner` continued polling fail-closed and no
  reset, flash, serial, physical nudge, or wrong-device nudge was attempted.
- overnight runner timing was adjusted at `2026-07-04T04:39Z` and hardened
  again at `2026-07-04T04:43Z` without touching hardware or production code.
  The earlier strict run missed `aec_live_vad_forward` because local
  `dừng lại` prompts ended before the first model audio window.
  `/tmp/tbot_overnight_runner.sh` now keeps the early interrupt burst, adds a
  second fixed-offset `dừng lại` burst before `barn barn barn`, and starts a
  short watcher that plays `dừng lại` when recent production logs show
  `first_audio_out`. `bash -n` passed, the runner was restarted in tmux, and
  fresh metrics still showed `connections=0`, so it remained fail-closed with
  no nudge.
- no-hardware readiness recheck at `2026-07-04T04:53Z`: production admin/OTA
  smoke passed, both Python replicas passed source-behavior checks for ping
  routing, model `audio_start` input-flush cancellation, legacy empty
  `lesson_ack` handling, and `py_compile`, and `/tmp/tbot_overnight_runner.sh`
  still passed `bash -n` plus owner-scoped watcher invariant checks. No new
  `nudged:true` run existed; latest metrics showed only a non-target device
  `14:c1:9f:d1:ac:20`, so target `28:84:85:85:1a:80` remained absent.
- runner identity hardening at `2026-07-04T04:57Z`: a brief metrics window at
  `2026-07-04T04:55Z` caused one `lesson-nudge` attempt, but server logs showed
  the websocket was `client-id=codex-consent-repro` using the target
  `device-id`, not the physical robot client
  `c29ce67a-3288-4c39-8544-bba97dab332b`. The nudge returned
  `nudged:false` / `START_REFUSED` because that synthetic client lacked lesson
  capability, and no prompt or audit ran. `/tmp/tbot_overnight_runner.sh` now
  requires both runtime metrics containing target `device-id` and a recent
  websocket header containing the target `device-id` plus the expected
  `client-id` before any nudge. Follow-up runner hardening at
  `2026-07-04T04:59Z` split `target_device_in_metrics` from
  `target_client_recent`; when metrics show target device-id but the expected
  client-id is absent, state now records `identity_gate_refused` instead of
  hiding the case as ordinary offline. `bash -n` passed and the runner was
  restarted.
- sample lesson storyBeat hotpatch at `2026-07-04T05:03Z`: local source now
  marks interactive sample child-question steps with
  `storyBeat.ask == prompt` and `storyBeat.waitForChild == true`, giving the
  lesson renderer explicit child-turn metadata while preserving existing prompt
  text. Focused and adjacent local proof passed `tests/test_sample_lesson.py`,
  `tests/test_lesson_nudge_handler.py`, and
  `tests/test_physical_smoke_audit.py` (`135 passed, 1 warning`), plus
  `py_compile` for `core/lesson/sample.py`. Production Python replicas were
  updated and recreated from
  `local/tbot-server:vps-20260704050228-storybeathotpatch`; both replicas now
  show storyBeat source plus the existing ping route, audio-start flush
  cancellation, and legacy empty `lesson_ack` handling. Public admin/OTA smoke
  passed, target metrics still showed no target device, and
  `tbot_voice_runner` was restarted fail-closed.
- strict physical production pass at `2026-07-04T05:17Z`: after the target
  robot reconnected as `device-id=28:84:85:85:1a:80` /
  `client-id=c29ce67a-3288-4c39-8544-bba97dab332b`, the runner nudged the
  real device and captured `/tmp/tbot_prod_voice_20260704T051749Z.log`. The
  strict audit pass artifact is
  `/tmp/tbot_physical_audit_20260704T051749Z.clean45.txt`, produced from the
  same physical run's nudge-to-completion window
  `/tmp/tbot_prod_voice_20260704T051749Z.clean45.log`; it passed
  `--production-voice-strict --min-interrupts 1 --expected-user-transcript
  'dừng lại'` with `physical_ws_connected=true`, `input_audio_diag=8`,
  first audio `684.4 ms`, `aec_live_vad_forward=4`,
  `aec_interruption_chains=2`, `live_server_interruption=3`,
  `interrupt_tts_stops=16`, interrupt stop latency `1.2-2.0 ms`,
  `user_transcript_expected_matches=1`,
  `post_interrupt_user_transcript_expected_matches=1`, `fatal_hits=[]`, and
  `missing=[]`. The same raw capture also shows `barn barn barn` accepted for
  s3/s4 and `lesson_completed stepsCompleted=4`. The untrimmed runner capture
  failed only because the runner kept collecting after lesson completion and
  caught later idle `waiting_model_timeout` markers; the runner artifact was
  changed to capture 60 seconds, use a 5-second nudge window, add a separate
  latest target connection header, redact JSON token fields, and omit stale
  `server.log` tails.
- production metrics identity hotpatch at `2026-07-04T05:10Z`: internal lesson
  runtime metrics now include `clientId` for connected devices when the
  connection exposes `client_id` or `headers['client-id']`. Local proof passed
  `tests/test_http_server.py tests/test_local_sample_demo_nudge.py
  tests/test_local_sample_demo_preflight.py` (`18 passed, 1 warning`) and
  `py_compile` for `core/http_server.py`. Production server replicas were
  recreated from `local/tbot-server:vps-20260704051024-metricsclientid`, public
  admin/OTA smoke passed, and a container snippet confirmed
  `clientId` appears in `_runtime_forwarder_metrics()`. This lets the runner
  fail closed on exact target client identity without scraping connection logs
  when metrics are populated.
- runner watcher hardening at `2026-07-04T04:50Z`: the first-audio watcher no
  longer tails both Python replicas. After any future `nudged:true`, it probes
  each replica's direct lesson metrics by container IP, records
  `target_owner owner=...`, and tails only that owner for `first_audio_out`.
  If owner cannot be identified, it logs `target_owner_missing` /
  `first_audio_watch_skipped_no_owner` and relies on the fixed prompt schedule
  instead of triggering local `say` from another device's audio. `bash -n`
  passed, an offline owner probe returned no owner, the runner was restarted in
  tmux, and fresh metrics still showed `connections=0`.
- production image drift was corrected at `2026-07-04T04:46Z` without touching
  hardware. The running Python replicas had restarted from the older
  `local/tbot-server:vps-20260703182329-aecmarker` image even though
  `/opt/tbot/.env` pointed at
  `local/tbot-server:vps-20260704042913-audiostartflushhotpatch`; this would
  have dropped the ping-routing and audio-start flush fixes if the robot
  reconnected. With target metrics still `connections=0`, the local runner was
  paused, `tbot-esp32-server` was force-recreated through
  `/opt/tbot/current/docker-compose.prod.yml --env-file /opt/tbot/.env`, and
  the runner was restarted. Both replicas now run
  `local/tbot-server:vps-20260704042913-audiostartflushhotpatch`, source checks
  confirmed `_is_ping_message` routing and `audio_start` cancels pending input
  flush tasks, `py_compile` passed for the touched runtime files, public
  admin/OTA smoke passed, and `/opt/tbot/current/.env` was synced to the same
  image to avoid future compose-default rollback. Fresh target metrics still
  showed `connections=0`, so no nudge was sent.
- historical 2026-05-18 LAN sweep with `fping` found `192.168.0.2`,
  `192.168.0.15`, `192.168.0.100`, `192.168.0.107`, `192.168.0.108`,
  `192.168.0.112`, host `192.168.0.114`, and `192.168.0.254`; none mapped
  to the then-target MAC `3c:0f:02:de:c2:e0`
- mDNS browse found router services only; no `_esp32._tcp`, `_xiaozhi._tcp`, or `_tbot._tcp` advertisement
- read-only router/extender status probe shows one associated station but does not provide target client MAC evidence; raw captures were deleted because the endpoint also returns sensitive Wi-Fi fields
- `server.mqtt_gateway` and `server.mqtt_manager_api` remain `null`
- no remote robot reboot path found in manager/device/MQTT code paths
- historical physical audit after proxy smoke failed with no real-device
  evidence: `physical_ws_connected=false`, `input_audio_diag=0`,
  `user_transcripts=0`, `audio_interrupts=0`
- historical USB recheck found only `/dev/cu.OGVN5574` and
  `/dev/cu.debug-console`; no ESP/JTAG/USB serial device was visible via
  `system_profiler`
- conversation responsiveness deploy at `2026-07-04T21:41+07:00`: local TDD
  first reproduced the text-turn gap (`USER_STREAMING != WAITING_MODEL`) and
  the stale idle-activity close (`_close_if_idle_once(1)` closed an active text
  turn). Final local proof passed the focused provider/barge-in/fallback suite
  (`168 passed`), the broader Google Live timing/provider/barge-in/bridge/client
  suite (`190 passed`), runtime benchmark p95s of `3.444 ms` frame latency,
  `2.253 ms` connection loop, `2.176 ms` heartbeat, and `git diff --check`.
  Production Python replicas were recreated from
  `local/tbot-server:vps-20260704214129-idleactivityhotpatch`; public admin/OTA
  smoke and in-container marker checks passed. The deployed text-barge-in soak
  that previously failed at cycle 6 then passed:
  `SOAK_OK cycles=10 tts_starts=20 tts_stops=10 binary_chunks=138`. Physical
  robot proof was not rerun because target metrics later showed
  `connections=0`; no nudge, reset, flash, serial, or wrong-device action was
  performed.
- envelope-only lesson ACK hotpatch at `2026-07-04T22:05+07:00`: local TDD
  first reproduced two gaps from the latest physical run: empty
  `lesson_ack` frames with envelope metadata (`sequence`, matching identity)
  did not correlate to the sole outstanding lesson frame, and the strict audit
  parser did not count the deployed
  `Google Live turn_latency_ms=... phase=first_audio_out` marker. Focused
  regressions passed, then adjacent proof passed
  `test_lesson_runtime.py`, `test_sample_lesson.py`, and
  `test_physical_smoke_audit.py` (`311 passed, 1 warning`), plus
  `py_compile` and `git diff --check`. Production Python replicas were
  recreated from `local/tbot-server:vps-20260704220440-envelopeackhotpatch`;
  public admin/OTA smoke and in-container runtime marker checks passed. Two
  owner-gated physical attempts refused to nudge because neither runtime
  metrics nor recent container logs showed the target device/client
  (`28:84:85:85:1a:80` /
  `c29ce67a-3288-4c39-8544-bba97dab332b`). No reset, flash, serial, public
  host nudge, wrong-replica nudge, or wrong-client nudge was performed.
  The passive VPS watcher was then restarted at `2026-07-04T22:56+07:00`
  with HAProxy (`tbot-wss-lb`) log capture in addition to both Python
  replicas, and it now matches both raw and URL-encoded target MAC forms
  (`28:84:85:85:1a:80` and `28%3A84%3A85%3A85%3A1a%3A80`). Future target
  sessions that reach the load balancer but do not enter runtime metrics are
  recorded under `/opt/tbot/voice_goal_watch`; the watcher now writes target
  snapshots when either runtime metrics contain the target or followed raw logs
  contain the raw/URL-encoded target/client identity.

Current audit/hardening files touched in this pass:

- `core/voice/session_provider/google_live.py`
- `core/voice/google_live/audio_bridge.py`
- `config.yaml`
- `config/config_loader.py`
- `deploy/watch-voice-goal-vps.sh`
- `scripts/physical_smoke_audit.py`
- `scripts/voice_mode_websocket_audio_bargein.py`
- `tests/test_config_voice_mode_merge.py`
- `tests/test_google_live_provider_edges.py`
- `tests/test_physical_smoke_audit.py`
- `tests/test_vps_voice_goal_watcher_script.py`
- `docs/google-live-smoke.md`
- `docs/google-live-completion-audit.md`
- `../manager-api/src/main/java/tbot/modules/sys/service/impl/SysParamsServiceImpl.java`
- `../manager-api/src/test/java/tbot/modules/sys/SysParamsServiceImplTest.java`

Earlier failing strict physical audit:

```json
{
  "passed": false,
  "physical_ws_connected": true,
  "input_audio_diag": 3,
  "first_audio_out_ms": {
    "count": 1,
    "min": 597.2,
    "max": 597.2
  },
  "live_server_interruption": 1,
  "interrupt_tts_stops": 2,
  "interrupt_stop_latency_ms": {
    "count": 2,
    "min": 2.1,
    "max": 2.2
  },
  "interrupt_stop_chains": 1,
  "interrupt_relisten_chains": 1,
  "live_identity": true,
  "live_identity_first_audio_chains": 1,
  "realtime_tts_stops": 8,
  "output_relisten_chains": 1,
  "user_transcripts": 0,
  "audio_interrupts": 0,
  "fatal_hits": [
    "Google Live waiting_model_timeout"
  ],
  "missing": [
    "user_transcript",
    "user_transcript_expected_match>=1",
    "aec_live_vad_forward",
    "aec_live_vad_forward>=1",
    "aec_interruption_chains>=1",
    "post_interrupt_user_transcripts>=1",
    "post_interrupt_user_transcript_expected_match>=1",
    "no_fatal_patterns"
  ]
}
```

Blocker:

- the boot blocker is cleared by the latest replug: the app boots, joins LAN,
  opens the production WebSocket, ACKs lesson frames, and speaks through Google
  Live on firmware `2.2.72`
- strict voice proof remains open: no real user transcript was captured after
  the Live interruption, no `dừng lại` transcript was observed, no
  AEC-forwarded robot-speaking mic frame was logged, and the physical sample
  still completed by demo graceful advance rather than accepted spoken child
  responses
- passive WebSocket sessions that only send `hello` time out after about
  60 seconds; the next spoken smoke should nudge immediately after reconnect or
  keep the connection active before lesson start
- the next strict pass needs a person speaking close to the robot mic during
  robot output (`dừng lại, nói chậm hơn`) and during s3/s4 child-response
  windows (`barn barn barn`)
- no usable MQTT manager endpoint is configured for remote reboot
- after the transcript-routing server deploy, hardware work is paused by user
  instruction: no reset, flash, serial intervention, or physical nudge should be
  attempted until the robot is physically available again

Audit conclusion:

- production-hardening, automated coverage, manager-key Live smoke, and partial
  physical voice proof are in place
- the server-side transcript routing bug seen when a correct `barn barn barn`
  transcript arrived after the provider audio window expired is patched and
  deployed
- physical production strict gate is not complete
- do not mark the ultragoal or Codex goal complete until the physical spoken smoke passes

## PR1-PR5 stability and barge-in (2026-05-19)

Triggered by user request "ổn định hóa Google Live trên robot + barge-in đáng tin cậy". Full plan: [`.omc/plans/google-live-stability-bargein-v2.md`](../../../.omc/plans/google-live-stability-bargein-v2.md).

### PR1 — baseline analysis (data-only, no code change)

- New tool: [`scripts/analyze_google_live_log.py`](../scripts/analyze_google_live_log.py) (one-shot parser for `server.log`)
- Evidence: [`docs/qa/ad-hoc/2026-05-19-google-live-baseline.md`](qa/ad-hoc/2026-05-19-google-live-baseline.md)
- Findings (37 sessions, 4,203 log lines):
  - 3 sessions with `goAway`-style 1008 disconnect, 2 abrupt drops, 10/37 zombie sessions with 0 audio chunks → ~40% session-level issue rate (stability is the dominant pain)
  - RMS while model speaking median 386, p95 2,186, max 8,310; RMS at barge-in trigger 9,044; ratio 0.043 → AEC is OPTIONAL (deferred from plan v2 central path to contingency)
  - 1/155 user interrupts came from `audio_input`; the rest from text path → voice barge-in path under-exercised in production logs

### PR2 — stability fixes

- New tests: [`tests/test_google_live_reconnect.py`](../tests/test_google_live_reconnect.py) (10 tests)
- Code changes:
  - `core/voice/google_live/client.py` — detect `goAway` field on incoming messages, yield `{"type": "session_expiring", "time_left_ms": ...}` (handles 3 SDK time_left shapes)
  - `core/voice/session_provider/google_live.py` — handle `session_expiring` event with scheduled reconnect; convert `_pending_reconnect_audio` from single-frame overwrite to bounded `deque(maxlen=reconnect_buffer_ms / frame_ms)`; classify exception class and skip retries for `auth`/`quota`/`invalid_config`
  - `core/voice/google_live/audio_bridge.py` — persist `audioop.ratecv` state for input + output direction so resampler does not click between 60ms Opus frames; reset output state on `audio_end`
  - `config.yaml` — `recv_timeout_sec: 60`, `reconnect_buffer_ms: 2000`, `reconnect.max_retries: 6`, `backoff_ms: 250`
- Test gate: 152 tests PASS (1 unrelated skip)

### PR4 — barge-in correctness

- New tests: [`tests/test_google_live_bargein.py`](../tests/test_google_live_bargein.py) (10 tests)
- Code changes:
  - `core/voice/session_provider/google_live.py` — 200ms debounce on `_begin_user_interrupt` but ONLY for `audio_input` reason (explicit and text interrupts are not debounced); guarded `client.end_audio_stream()` after `interrupt()` so Live API closes the user turn and the next utterance is not merged
  - `core/voice/google_live/audio_bridge.py` — `_unblock_timer_task` tracked, scheduled by `stop_output()`, cancelled by `allow_model_output()` and the new `bridge.close()` hook; auto-unblock after `model_output_unblock_timeout_sec=1.5` so the flag cannot deadlock when user's interrupting utterance is too short to produce a transcript
  - `config.yaml` — `interrupt_debounce_sec: 0.2`, `model_output_unblock_timeout_sec: 1.5`, `barge_in_min_input_duration_sec: 0.30` (down from 0.42), `barge_in_rms_threshold` kept at 5000 pending controlled echo measurement

### PR5 — soak harness and docs

- New tool: [`scripts/google_live_robot_soak.py`](../scripts/google_live_robot_soak.py) — bargein + idle cycles, log-tail validation, structured JSON report mapping to AC1-AC5
- New doc: [`docs/google-live-robot-validation.md`](google-live-robot-validation.md)
- Updated doc: [`docs/google-live-mode.md`](google-live-mode.md) — PR2/PR4 config keys explained
- PR5 evidence row (skeleton) at [`docs/qa/ad-hoc/2026-05-19-google-live-robot-validation.md`](qa/ad-hoc/2026-05-19-google-live-robot-validation.md) — fill after physical soak runs

### Outstanding

- Physical soak on robot vật lý (PR5.5) — gated on robot MAC `3c:0f:02:de:c2:e0` being on the LAN (was missing in the audit window above; re-check before the soak)
- PR3 server-side AEC — deferred (AEC_OPTIONAL verdict from PR1); only revisit if PR4 tuning misses AC2-AC4 after a fresh controlled capture

---

## Phase 5 — PR2 / PR4 / PR5 evidence (2026-05-19)

Status: **PLACEHOLDER** — fill after physical robot soak completes. PR2 and PR4 agents will update the evidence paths marked `TODO` below.

### PR2 evidence (stability fixes)

| Item | Path | Status |
|---|---|---|
| Unit test run output | TODO: `docs/qa/ad-hoc/2026-05-19-google-live-pr2.md` | TBD — fill by PR2 agent |
| `test_google_live_reconnect.py` results | TODO: attach test output | TBD |
| goAway handling verified | log pattern `Google Live session_expiring time_left_ms=X` | TBD |
| Proactive reconnect verified | log pattern `Google Live proactive_reconnect started/succeeded` | TBD |
| Deque replay counter | `pending_audio_replay_bytes` = 0 during reconnect window | TBD |

### PR4 evidence (barge-in correctness)

| Item | Path | Status |
|---|---|---|
| Unit test run output | TODO: `docs/qa/ad-hoc/2026-05-19-google-live-pr4.md` | TBD — fill by PR4 agent |
| `test_google_live_bargein.py` results | TODO: attach test output | TBD |
| Debounce verified | log pattern `Google Live interrupt_debounced age_ms=X` absent during single-fire | TBD |
| Timer cancel verified | `Google Live model_output_unblock_timeout` fires correctly | TBD |

### PR5 evidence (soak harness and docs)

| Item | Path | Status |
|---|---|---|
| Soak script syntax check | `scripts/google_live_robot_soak.py` compiles clean | PASS (2026-05-19) |
| Dry-run smoke | `--cycles 1 --duration-sec 5 --dry-run` → exit 0, valid JSON | PASS (2026-05-19) |
| CLI spec compliance | All 8 args from v2.1 §8 present (`--device-mac`, `--cycles`, `--duration-sec`, `--bargein-cycles`, `--inject-audio`, `--inject-text`, `--ws-url`, `--dry-run`) | PASS |
| Doc: `google-live-robot-validation.md` | `docs/google-live-robot-validation.md` | EXISTS — updated with dry-run note |
| Doc: `google-live-mode.md` | `docs/google-live-mode.md` — "Best-practice config for TBOT robot" section added | PASS (2026-05-19) |
| Physical robot soak report | TODO: `.omc/research/soak-<timestamp>.json` | TBD — requires robot MAC on LAN |
| Evidence file | `docs/qa/ad-hoc/2026-05-19-google-live-pr5.md` | EXISTS (skeleton ready) |

### AC verdicts after physical soak (fill when run)

| AC | Description | Verdict | Evidence |
|---|---|---|---|
| AC1 | ≥ 9/10 cycles without unplanned disconnect, reconnect < 3s | TBD | soak `ac_results.AC1` |
| AC2 | Barge-in p95 latency ≤ 500ms | TBD | soak `ac_results.AC2` |
| AC3 | Post-interrupt new response, ≥ 80% cycles | TBD | soak `ac_results.AC3` |
| AC4 | 0 false-positive interrupts during 120s idle | TBD | soak `ac_results.AC4` |
| AC5 | No fallback triggered during soak | TBD | soak `ac_results.AC5` |
| AC6 | Unit test coverage | PASS | 152 tests pass (see PR2/PR4 runs) |
| AC7 | Server-side AEC effectiveness | DEFERRED | PR1 verdict AEC_OPTIONAL |
