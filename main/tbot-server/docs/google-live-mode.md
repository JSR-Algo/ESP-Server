# Google Live Mode

`google_live` is additive voice mode alongside existing `classic_pipeline`.

## Modes

- `classic_pipeline`
  - Existing flow stays unchanged:
    `audio -> VAD -> ASR -> LLM -> TTS -> audio`
- `google_live`
  - Server keeps websocket session + firmware protocol
  - Voice transport switches to Google Live session provider
  - Local ASR/TTS pipeline is bypassed for live audio turns

## Config

```yaml
voice_mode:
  type: classic_pipeline
  fallback_to_classic_on_error: true

google_live:
  api_key: ${GOOGLE_API_KEY}
  model: gemini-3.1-flash-live-preview
  voice_name: ""
  enable_audio_input: true
  enable_audio_output: true
  native_voice: true
  input_audio_format: pcm16
  input_sample_rate: 16000
  output_audio_format: pcm16
  output_sample_rate: 24000
  input_live_chunk_ms: 20
  response_modalities: [AUDIO]
  activity_handling: START_OF_ACTIVITY_INTERRUPTS
  connect_timeout_sec: 10
  recv_timeout_sec: 60               # PR2: raised from 30 to give native-audio model headroom
  interrupt_policy: wake_or_transcript
  raw_audio_barge_in_enabled: false
  input_flush_delay_sec: 1.0
  reconnect_buffer_ms: 2000          # current-turn mic packets preserved across reconnect
  interrupt_replay_buffer_ms: 900
  interrupt_on_input_while_speaking: false
  interrupt_rms_threshold: 5000      # legacy rollback knob; not active by default
  interrupt_min_input_duration_sec: 0.42
  interrupt_min_output_age_sec: 0.25
  interrupt_suppress_audio_sec: 0.25
  echo_tail_suppression_ms: 400
  interrupt_debounce_sec: 0.2
  model_output_unblock_timeout_sec: 1.5
  drop_input_while_speaking: false
  barge_in: false
  barge_in_rms_threshold: 4500       # legacy rollback knob; not active by default
  barge_in_min_input_duration_sec: 0.30
  barge_in_min_output_age_sec: 0.25
  suppress_robot_output_echo: true
  wake_audio_allow_window_sec: 5.0
  echo_bypass_interrupt_enabled: false
  music_auto_pause_on_user_speech: true
  send_transcript_events: true
  send_llm_state_events: false
  session_resumption_enabled: true
  context_window_compression_enabled: true
  context_window_trigger_tokens: 24000
  context_window_target_tokens: 12000
  tool_timeout_sec: 10.0
  reconnect:
    enabled: true
    max_retries: 6                   # PR2: 3 was too thin under flakey network
    backoff_ms: 250                  # PR2: 250ms base, multiplier 2 -> total budget ~16s
    backoff_multiplier: 2
```

### PR2 / PR4 config keys at a glance

| Key | Default | Purpose | Set by |
|---|---|---|---|
| `recv_timeout_sec` | 60 | soft timeout per Live message; raised for native-audio | PR2 |
| `activity_handling` | `START_OF_ACTIVITY_INTERRUPTS` | official Live interruption mode; `NO_INTERRUPTION` is forbidden for production robot barge-in | US-004 |
| `input_live_chunk_ms` | 20 | decoded PCM16 is sent upstream in 20 ms Live chunks, even when firmware sends 60 ms Opus frames | US-004 |
| `session_resumption_enabled` | true | receive and reuse Live resumable handles on reconnect | US-004 |
| `context_window_compression_enabled` | true | keep long sessions alive with sliding-window compression | US-004 |
| `tool_timeout_sec` | 10 | bound manual Live tool execution; late cancelled tool results are dropped | US-004 |
| `interrupt_policy` | `wake_or_transcript` | only wake/listen, user transcript, or deterministic music command can interrupt production output | US-004 |
| `raw_audio_barge_in_enabled` | false | raw/RMS barge-in is disabled unless an explicit tested rollout enables it | US-004 |
| `echo_tail_suppression_ms` | 400 | suppress mic frames briefly after robot output stops | US-004 |
| `reconnect_buffer_ms` | 2000 | how much current-turn mic audio to preserve across reconnect | US-004 |
| `interrupt_replay_buffer_ms` | 900 | short user-audio replay window after a valid interrupt gate | US-004 |
| `reconnect.max_retries` / `backoff_ms` | 6 / 250 | new reconnect budget; `auth`/`quota`/`invalid_config` skip retries and fall back | PR2 |
| `interrupt_debounce_sec` | 0.2 | minimum gap between successive `audio_input` interrupts (text / explicit interrupts are NOT debounced) | PR4 |
| `model_output_unblock_timeout_sec` | 1.5 | if no user transcript arrives after interrupt, unblock model output automatically | PR4 |
| `barge_in_min_input_duration_sec` | 0.30 | legacy raw-audio rollback knob; not active by default | US-004 |

## Admin UI

When `voiceMode = google_live`, admin panel exposes:

- API key
- model
- voice name
- enable audio input
- enable audio output
- native voice
- barge-in
- send LLM state events
- fallback to classic on error
- connect timeout
- receive timeout
- input flush delay
- input sample rate
- output sample rate
- transcript events
- reconnect enabled
- reconnect max retries
- reconnect backoff

`classic_pipeline` remains default.

## Runtime Flow

1. Connection starts with provider selected from `voice_mode.type`.
2. If private config later resolves to different mode, provider swaps without changing websocket protocol.
3. In `google_live` mode:
- inbound Opus audio is decoded to PCM16
- resampled to Google input sample rate
- streamed to Google Live as 20 ms PCM16 chunks
   - output PCM16 is resampled back to device sample rate
   - encoded to Opus
   - sent through existing audio send path

## Firmware Compatibility

No firmware rewrite required.

Existing surfaces remain:

- `stt`
- `llm`
- `tts`

Notes:

- user transcripts stay on `stt`
- optional model transcript events can be emitted on `llm` when `send_llm_state_events = true`
- output audio still arrives through existing binary audio path

## Interrupt / Barge-In

- text `abort` delegates to active voice provider interrupt
- `listen:start` can interrupt active provider before reset
- inbound raw/RMS audio does not trigger production barge-in by default
- Gemini Live automatic VAD remains active with `START_OF_ACTIVITY_INTERRUPTS`
- allowed interrupt gates are wake/listen, confirmed user transcript, deterministic Vietnamese music command, or explicitly tested loud-speech bypass
- while robot speech/music is active, mic frames are suppressed before they reach Google Live, with a 400 ms echo tail after output stop
- when Live sends `serverContent.interrupted`, the server stops playback and
  clears queued audio immediately; `NO_INTERRUPTION` must not be configured
- robot `listen:stop` is handled by the Live provider and calls
  `end_audio_stream()` immediately; it must not fall back to idle flush delay
- firmware `tts:stop` waits only a bounded playback-drain window before
  relistening; explicit abort/wake interruption clears queued playback first

## Fallback

If Google Live init or runtime path fails:

- reconnect is attempted if enabled
- provider can fall back to `classic_pipeline`
- websocket session should stay alive

## Rollback

Fast rollback:

```yaml
voice_mode:
  type: classic_pipeline
```

Or disable fallback if you want hard failure visibility during staged testing:

```yaml
voice_mode:
  type: google_live
  fallback_to_classic_on_error: false
```

## Verification

Server focused verification:

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

Admin/backend focused verification:

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

Notes:

- `manager-api` project target remains Java 21.
- Focused voice-mode backend tests are verified on JDK 21.
- Focused voice-mode backend tests also pass on current local default JDK 25 with current test/build harness.
- Optional live smoke still needs real Google credentials.

Optional live smoke:

- `docs/google-live-smoke.md`
- `scripts/google_live_smoke.py`

## Best-practice config for TBOT robot

Recommended `config.yaml` block for production TBOT Live mode. Apply via manager-web > role config or directly in the agent's private config.

```yaml
voice_mode:
  type: google_live
  fallback_to_classic_on_error: true
  google_live:
    reconnect:
      enabled: true
      max_retries: 6          # PR2: six attempts covers ~16s total backoff budget
      backoff_ms: 250         # PR2: 250ms base, doubles each retry
      backoff_multiplier: 2
    recv_timeout_sec: 60      # PR2: raised from 30; native-audio model needs headroom
    interrupt_policy: wake_or_transcript
    raw_audio_barge_in_enabled: false
    disable_server_side_interruptions: false
    activity_handling: START_OF_ACTIVITY_INTERRUPTS
    barge_in: false
    interrupt_on_input_while_speaking: false
    echo_tail_suppression_ms: 400
    interrupt_replay_buffer_ms: 900
    interrupt_debounce_sec: 0.2              # PR4: prevents double-fire on audio_input
    model_output_unblock_timeout_sec: 1.5   # PR4: auto-unblock if user transcript never arrives
```

### Config key → symptom table

| Config key | Symptom it addresses | Set by |
|---|---|---|
| `reconnect.max_retries: 6` | Session dropped after 1-3 goAway / network blips; robot goes silent | PR2 |
| `reconnect.backoff_ms: 250` / `backoff_multiplier: 2` | Rapid re-connect storm under flakey network | PR2 |
| `recv_timeout_sec: 60` | Zombie sessions: server never fires timeout reset, session drifts | PR2 |
| `activity_handling: START_OF_ACTIVITY_INTERRUPTS` | User speech can interrupt model output through official Live VAD; queue clear handles stale playback | US-004 |
| `barge_in: false` / `interrupt_on_input_while_speaking: false` | raw mic frames must not interrupt robot output | US-004 |
| `echo_tail_suppression_ms: 400` | stale echo immediately after `tts stop` leaking upstream | US-004 |
| `interrupt_debounce_sec: 0.2` | Double-fire on short audio burst; two interrupts sent in quick succession | PR4 |
| `model_output_unblock_timeout_sec: 1.5` | Model output stuck after interrupt when user utterance is too short to produce transcript | PR4 |

### Legacy raw/RMS barge-in tuning

The old production guidance that set `barge_in: true` is now legacy. Do not use
it as a production default. Only enable raw/RMS interruption for a controlled
rollout that also sets an explicit bypass flag and has physical false-positive
tests.

If the board is in a noisy environment (> 50 dBA background) and AC4 false-positives appear:

1. Capture 2 min of silent log with `log_audio_diagnostics: true`.
2. Grep `input_audio_diag rms=` lines, compute P95 noise floor.
3. Set `barge_in_rms_threshold = P95_noise_floor * 1.5` (adds ~3.5 dB headroom).
4. Run `scripts/google_live_robot_soak.py --idle-cycles 1 --idle-duration-sec 120` to confirm AC4.

### Rollback

Config-only rollback (no redeploy needed):

```yaml
voice_mode:
  type: classic_pipeline     # bypass Live entirely
```

Or to keep Live but revert barge-in tuning:

```yaml
google_live:
  # Legacy/experimental only. Not a production default.
  barge_in_rms_threshold: 5000
  barge_in_min_input_duration_sec: 0.42
```
