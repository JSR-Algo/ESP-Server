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
  `lesson_* queued via tts` markers, even if a Live-text marker is also present
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
  `audio_decision decision=suppress_echo reason=robot_speaking`, `Google Live
  echo_bypass`, `Google Live echo_suppressed reason=robot_speaking`, `Google
  Live AEC import failed`, `Google Live AEC initialised ... bypassed=True`,
  `Google Live AEC process_mic failed while output active`,
  `live_identity_mismatch`, `Google Live
  interruption suppressed_for_age`, `interrupt_started reason=loud_input`,
  `Google Live user_interrupted reason=loud_input`,
  `Google Live transcript_barge_in suppressed_for_age`, `Google Live
  transcript_barge_in suppressed_as_model_echo`, and `Google Live server
  interruption ignored by config`, Google Live receive/waiting-model timeouts,
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
- runtime provider policy also clamps direct slow timing overrides:
  `waiting_model_timeout_sec<=2.0`,
  `interruption_min_output_age_sec=0.0`, and
  `barge_in_transcript_min_output_age_sec=0.0`
- runtime provider policy also forces `echo_bypass_interrupt_enabled=false`, so
  direct runtime config cannot re-enable local raw/RMS loud-input interruption
  around the config normalizer
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
- exceptions from parsed Google Live text commands are also consumed after
  runtime-failure handling, so a failed local stop-word/command branch cannot
  fall through to the classic text handler
- Google Live audio decode/forward/send exceptions are consumed after
  runtime-failure handling, so a bad Live audio frame or send failure cannot
  fall through to the connection's classic ASR queue for the same mic audio
- runtime safety policy forces `waiting_model_timeout_sec=2.0` after
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
  `167 passed`; barge-in/fallback/connection-routing suite `135 passed`
- focused test evidence from 2026-07-02: Google Live/config/provider suite
  `332 passed`; lesson slice `34 passed, 1 warning`; physical audit tests
  `44 passed`; analyzer tests `14 passed`

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
| Physical robot connects as non-Python client | `scripts/physical_smoke_audit.py` requires non-local/non-Python headers and rejects the server IP when `--server-ip` is provided | missing |
| Physical microphone audio reaches Google Live | `scripts/physical_smoke_audit.py` requires `input_audio_diag` | missing |
| Physical speech produces expected user transcript | `scripts/physical_smoke_audit.py --expected-user-transcript ...` requires the expected phrase inside a user transcript | missing |
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
- latest LAN sweep with `fping` found `192.168.0.2`, `192.168.0.15`, `192.168.0.100`, `192.168.0.107`, `192.168.0.108`, `192.168.0.112`, host `192.168.0.114`, and `192.168.0.254`; none map to target MAC `3c:0f:02:de:c2:e0`
- mDNS browse found router services only; no `_esp32._tcp`, `_xiaozhi._tcp`, or `_tbot._tcp` advertisement
- read-only router/extender status probe shows one associated station but does not provide target client MAC evidence; raw captures were deleted because the endpoint also returns sensitive Wi-Fi fields
- `server.mqtt_gateway` and `server.mqtt_manager_api` remain `null`
- no remote robot reboot path found in manager/device/MQTT code paths
- physical audit re-run after proxy smoke still fails with no real-device evidence: `physical_ws_connected=false`, `input_audio_diag=0`, `user_transcripts=0`, `audio_interrupts=0`
- USB recheck found only `/dev/cu.OGVN5574` and `/dev/cu.debug-console`; no ESP/JTAG/USB serial device visible via `system_profiler`

Current audit/hardening files touched in this pass:

- `core/voice/google_live/audio_bridge.py`
- `scripts/physical_smoke_audit.py`
- `scripts/voice_mode_websocket_audio_bargein.py`
- `tests/test_physical_smoke_audit.py`
- `docs/google-live-smoke.md`
- `docs/google-live-completion-audit.md`
- `../manager-api/src/main/java/tbot/modules/sys/service/impl/SysParamsServiceImpl.java`
- `../manager-api/src/test/java/tbot/modules/sys/SysParamsServiceImplTest.java`

Latest failing physical audit:

```json
{
  "passed": false,
  "physical_ws_connected": false,
  "input_audio_diag": 0,
  "user_transcripts": 0,
  "audio_interrupts": 0,
  "missing": [
    "physical_ws_connected",
    "input_audio_diag",
    "user_transcript",
    "audio_interrupts>=10"
  ]
}
```

Blocker:

- DB row still has `app_version=2.2.6`; `last_connected_at=2026-05-18 22:28:23` is not proof of physical presence because OTA emulation updates the same row
- target MAC `3c:0f:02:de:c2:e0` is absent from ARP
- visible serial endpoints `/dev/cu.OGVN5574` and `/dev/cu.debug-console` are generic `IOSerialFamily` clients; passive reads return 0 bytes and ESP32-S3 `chip_id` returns `No serial data received`
- no usable MQTT manager endpoint is configured for remote reboot

Audit conclusion:

- production-hardening and automated coverage are in place
- physical production gate is not complete
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
