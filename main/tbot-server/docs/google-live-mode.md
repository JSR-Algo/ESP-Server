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
  model: gemini-2.5-flash-native-audio-preview-12-2025
  voice_name: ""
  enable_audio_input: true
  enable_audio_output: true
  native_voice: true
  input_audio_format: pcm16
  input_sample_rate: 16000
  output_audio_format: pcm16
  output_sample_rate: 24000
  connect_timeout_sec: 10
  recv_timeout_sec: 60               # PR2: raised from 30 to give native-audio model headroom
  input_flush_delay_sec: 0.8
  reconnect_buffer_ms: 2000          # PR2: deque of raw Opus packets preserved across reconnect
  interrupt_on_input_while_speaking: true
  interrupt_rms_threshold: 5000
  interrupt_min_input_duration_sec: 0.30  # PR4: lowered from 0.42 for snappier response
  interrupt_min_output_age_sec: 0.25
  interrupt_suppress_audio_sec: 0.25
  interrupt_debounce_sec: 0.2        # PR4: minimum gap between two audio_input interrupts
  model_output_unblock_timeout_sec: 1.5  # PR4: auto-unblock after this if no user transcript
  drop_input_while_speaking: false
  barge_in: true
  barge_in_rms_threshold: 5000       # keep until controlled echo measurement justifies a change
  barge_in_min_input_duration_sec: 0.30
  barge_in_min_output_age_sec: 0.25
  send_transcript_events: true
  send_llm_state_events: false
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
| `reconnect_buffer_ms` | 2000 | how much mic audio to preserve across reconnect | PR2 |
| `reconnect.max_retries` / `backoff_ms` | 6 / 250 | new reconnect budget; `auth`/`quota`/`invalid_config` skip retries and fall back | PR2 |
| `interrupt_debounce_sec` | 0.2 | minimum gap between successive `audio_input` interrupts (text / explicit interrupts are NOT debounced) | PR4 |
| `model_output_unblock_timeout_sec` | 1.5 | if no user transcript arrives after interrupt, unblock model output automatically | PR4 |
| `barge_in_min_input_duration_sec` | 0.30 | sustained loud-input window required before barge-in fires | PR4 |

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
   - streamed to Google Live
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
- inbound audio can trigger live barge-in when `barge_in = true`

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

Recommended `config.yaml` block for the `Freenove_ESP32S3_DISPLAY_2.8_LCD` board after PR2 + PR4 land. Apply via manager-web > role config or directly in the agent's private config.

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
    disable_server_side_interruptions: true  # keep RMS-VAD as sole trigger (avoids race)
    barge_in_rms_threshold: 4500             # PR4: tuned down from 5000 after baseline data
    barge_in_min_input_duration_sec: 0.30    # PR4: lowered from 0.42 for snappier response
    interrupt_debounce_sec: 0.2              # PR4: prevents double-fire on audio_input
    model_output_unblock_timeout_sec: 1.5   # PR4: auto-unblock if user transcript never arrives
```

### Config key → symptom table

| Config key | Symptom it addresses | Set by |
|---|---|---|
| `reconnect.max_retries: 6` | Session dropped after 1-3 goAway / network blips; robot goes silent | PR2 |
| `reconnect.backoff_ms: 250` / `backoff_multiplier: 2` | Rapid re-connect storm under flakey network | PR2 |
| `recv_timeout_sec: 60` | Zombie sessions: server never fires timeout reset, session drifts | PR2 |
| `disable_server_side_interruptions: true` | Race between Live VAD and RMS VAD causing double-interrupt | PR4 (kept) |
| `barge_in_rms_threshold: 4500` | Barge-in never fires (RMS too high) vs. false-positive during echo | PR4 |
| `barge_in_min_input_duration_sec: 0.30` | Too-slow reaction to user interruption; now matches 0.30s window | PR4 |
| `interrupt_debounce_sec: 0.2` | Double-fire on short audio burst; two interrupts sent in quick succession | PR4 |
| `model_output_unblock_timeout_sec: 1.5` | Model output stuck after interrupt when user utterance is too short to produce transcript | PR4 |

### Re-tuning barge-in thresholds

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
  barge_in_rms_threshold: 5000
  barge_in_min_input_duration_sec: 0.42
```
