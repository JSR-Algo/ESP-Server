# Google Live Mode

`google_live` is the production robot voice mode for normal conversation and
lesson narration. `classic_pipeline` remains a separate legacy mode, not a
fallback for production Google Live speech.

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
  type: google_live
  fallback_to_classic_on_error: false

google_live:
  api_key: ${GOOGLE_API_KEY}
  model: gemini-3.1-flash-live-preview
  voice_name: Kore
  language_code: vi-VN
  enable_audio_input: true
  enable_audio_output: true
  native_voice: true
  input_audio_format: pcm16
  input_sample_rate: 16000
  output_audio_format: pcm16
  output_sample_rate: 24000
  input_live_chunk_ms: 20
  response_modalities: [AUDIO]
  disable_server_side_interruptions: false
  activity_handling: START_OF_ACTIVITY_INTERRUPTS
  connect_timeout_sec: 10
  recv_timeout_sec: 60               # PR2: raised from 30 to give native-audio model headroom
  interrupt_policy: wake_or_transcript
  raw_audio_barge_in_enabled: false
  conversation_input_flush_delay_sec: 0.18
  conversation_input_speech_tail_ms: 180
  input_flush_delay_sec: 0.8
  input_speech_tail_ms: 600
  waiting_model_timeout_sec: 3.0     # runtime safety policy caps private configs at 4.0
  prewarm_live_on_connect: true
  prewarm_live_on_wake: true
  wake_greeting_enabled: true
  idle_timeout_sec: 180
  reconnect_buffer_ms: 2000          # current-turn mic packets preserved across reconnect
  interrupt_replay_buffer_ms: 900
  interrupt_on_input_while_speaking: false
  interrupt_rms_threshold: 5000      # legacy rollback knob; not active by default
  interrupt_min_input_duration_sec: 0.42
  interrupt_min_output_age_sec: 0.25
  interruption_min_output_age_sec: 0.0
  interrupt_suppress_audio_sec: 0.25
  mute_input_after_audio_start_sec: 0.28
  echo_tail_suppression_ms: 550
  echo_tail_extend_rms_threshold: 700
  echo_tail_extend_ms: 350
  echo_tail_max_total_ms: 1400
  echo_tail_audible_ms: 400
  interrupt_debounce_sec: 0.2
  model_output_unblock_timeout_sec: 1.5
  drop_input_while_speaking: false
  barge_in: false
  barge_in_rms_threshold: 4500       # legacy rollback knobs; not active by default
  barge_in_min_input_duration_sec: 0.30
  barge_in_min_output_age_sec: 0.25
  barge_in_transcript_min_output_age_sec: 0.0
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
| `conversation_input_flush_delay_sec` | 0.18 | normal conversation turn-close safety net; faster than lesson timing | US-004 |
| `conversation_input_speech_tail_ms` | 180 | normal conversation silence tail before finalising input | US-004 |
| `waiting_model_timeout_sec` | 3.0 (cap 4.0) | reopen listening if Live returns no model audio, so the robot does not stay stuck in waiting state | US-004 |
| `input_flush_delay_sec` / `input_speech_tail_ms` | 0.8 / 600 | lesson/child speech timing; longer than conversation to avoid cutting a paused child | US-006 |
| `interruption_min_output_age_sec` | 0.0 | honor Live interruption immediately when robot output has just started | US-004 |
| `barge_in_transcript_min_output_age_sec` | 0.0 | confirmed user transcript can stop output immediately instead of waiting for a minimum output age | US-004 |
| `raw_audio_barge_in_enabled` | false | raw/RMS barge-in is disabled unless an explicit tested rollout enables it | US-004 |
| `echo_tail_suppression_ms` | 550 | suppress mic frames after robot output stops (covers device playout drain) | US-004 |
| `echo_tail_extend_rms_threshold` / `echo_tail_extend_ms` | 700 / 350 | while residual mic energy stays high, extend the echo gate (capped) | echo-loop fix |
| `echo_tail_max_total_ms` | 1400 | hard cap on continuous adaptive echo suppression | echo-loop fix |
| `echo_tail_audible_ms` | 400 | keep output-active latch after stop so residual stays under echo gate | echo-loop fix |
| `mute_input_after_audio_start_sec` | 0.28 | drop mic right after model audio starts so AEC can converge | US-004 |
| `prewarm_live_on_connect` / `prewarm_live_on_wake` | true | open Live before first utterance to remove cold-connect hang | latency |
| `wake_greeting_enabled` | true | short spoken line after Hi ESP so cold start is not silent | latency |
| `idle_timeout_sec` | 180 | keep prewarmed Live hot longer after idle | latency |
| `reconnect_buffer_ms` | 2000 | how much current-turn mic audio to preserve across reconnect | US-004 |
| `interrupt_replay_buffer_ms` | 900 | short user-audio replay window after a valid interrupt gate | US-004 |
| `reconnect.max_retries` / `backoff_ms` | 6 / 250 | new reconnect budget; `auth`/`quota`/`invalid_config` skip retries and fall back | PR2 |
| `interrupt_debounce_sec` | 0.2 | minimum gap between successive `audio_input` interrupts (text / explicit interrupts are NOT debounced) | PR4 |
| `model_output_unblock_timeout_sec` | 1.5 | if no user transcript arrives after interrupt, unblock model output automatically | PR4 |
| `barge_in_min_input_duration_sec` | 0.30 | legacy raw-audio rollback knob; not active by default | US-004 |

Production normalization reapplies the Live safety policy after manager/private
config merge, so old agent configs cannot change `voice_name` away from the
single robot voice (`Kore` by default), raise `waiting_model_timeout_sec`,
`interruption_min_output_age_sec`, or
`barge_in_transcript_min_output_age_sec` back to slow values.
Connection setup also normalizes after applying private config, so tests or
fallback callers that bypass the API normalizer cannot reopen those values before
the Google Live provider starts.

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
- no classic/local TTS fallback for AI speech
- connect timeout
- receive timeout
- input flush delay
- input sample rate
- output sample rate
- transcript events
- reconnect enabled
- reconnect max retries
- reconnect backoff

The same `google_live` block is used for external conversation and
`LessonRuntime` prompt handoff through `GoogleLiveProvider.speak_lesson_step_prompt`,
so one configured Live voice speaks both surfaces.
Lesson prompt text is sent as verbatim Live text: no translation, additions,
omissions, or shortening before the child-response window can open.
Production audit derives lesson spoken text from manifest `prompt`,
`retryPrompt`, and `successPrompt` fields.

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
   - text input sent with `turn_complete=true` is treated as a complete user
     turn: the provider opens the normal no-response watchdog and reopens
     listening if Live returns no model audio
   - consumed text/control messages, inbound user audio, and Live model events
     refresh Live idle activity; idle close is based on real inactivity, not
     session age

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
- while robot speech is active and AEC is available, AEC-cleaned mic frames are
  forwarded to Google Live so official Live VAD can catch a real user
  interruption; local raw/RMS interrupt gates stay off
- music/no-AEC frames and the 400 ms post-output echo tail are suppressed before
  they reach Google Live
- when Live sends `serverContent.interrupted`, the server stops playback and
  clears queued audio immediately; `NO_INTERRUPTION` must not be configured
- robot `listen:stop` is handled by the Live provider and calls
  `end_audio_stream()` immediately; it must not fall back to idle flush delay
- firmware `tts:stop` waits only a bounded playback-drain window before
  relistening; explicit abort/wake interruption clears queued playback first

## Failure Behavior

If Google Live init or runtime path fails:

- reconnect is attempted if enabled
- `google_live` does not switch to `classic_pipeline`
- local/classic TTS is not queued for AI speech or lesson narration
- failures are logged as `fallback_disabled`; websocket session should stay
  alive where possible
- required AEC failures hard-fail instead of degrading, because echo isolation is
  part of the production safety boundary

### TVideo lesson reconnect and curated fallback

Validated renderer-v4 TVideo conversations use a narrower failure policy than
general chat:

- a Live timeout or transport interruption asks the authoritative lesson FSM for
  the `thinking` cue; firmware visual state changes only after the existing
  cinematic command is accepted and ACKed
- the provider makes at most one reconnect attempt for that interruption window
- a curated prompt is released only after the exact thinking cue, attempt, and
  cinematic command sequence receive an ACK; wrong, stale, or timed-out ACKs
  fail closed
- a successful reconnect closes the window, republishes the current lesson
  identity, and permits one attempt in a later independent window
- a failed window remains bounded for the same authoritative turn; after the
  child starts a genuinely new turn, that new turn owns a fresh reconnect window
- a failed or repeated reconnect uses one short deterministic prompt composed
  only from the validated `targetWord` guidance
- if reconnect fails and no verified prompt channel remains, the lesson fallback
  returns unhandled so the existing transport failure path can reconnect or fail
  terminally instead of pretending recovery succeeded
- fallback never calls pronunciation mastery and never advances progress; only
  an accepted pronunciation outcome can produce `mastered` evidence
- diagnostics contain typed codes, reason classes, and attempt counts only; they
  do not contain child speech, transcripts, or generated model prose

Conversation progress stores exactly one structured evidence object per
completed step:

```json
{
  "outcome": "mastered",
  "attempt_count": 1,
  "final_coaching_level": 0,
  "elapsed_ms": 4321,
  "step_key": "barn",
  "lesson_version": 4
}
```

The only outcomes are `mastered`, `attempted`, and explicitly enabled
`comprehended`. Raw audio bytes, transcript text, utterance text, and model prose
must never be attached to evidence, progress, telemetry, soak reports, or logs.

## Rollback

Fast rollback:

```yaml
voice_mode:
  type: classic_pipeline
```

Production Google Live mode should fail fast instead of switching to a different
voice stack:

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

Robot soak audio must be synthetic or recorded from a consenting adult. The
report stores only the provenance label and whether injection was enabled, not
the audio path, injected text, prompt text, transcript, or audio bytes:

```bash
python scripts/google_live_robot_soak.py \
  --mode bargein_latency \
  --audio-source synthetic \
  --inject-audio data/synthetic_stop_vn.wav \
  --report /tmp/google-live-soak.json
```

The current text-mode soak never embeds the `--inject-audio` path or configured
prompt into websocket detect messages. It sends a fixed
`SOAK_AUDIO_INTERRUPT_SENTINEL`; actual binary audio injection remains an
attended hardware-smoke path.

Physical interrupt/course audit for the production robot should use one strict
preset so latency, transcript accuracy, AEC-forwarding, and full lesson-prompt
hash gates cannot be accidentally omitted:

```bash
python scripts/physical_smoke_audit.py tmp/server.log \
  --device-id <robot-mac> \
  --client-id <robot-client-id> \
  --server-ip <server-ip> \
  --expected-user-transcript "bắt đầu bài học" \
  --min-interrupts 10 \
  --production-strict \
  --lesson-manifest <lesson-manifest.json>
```

Pass criteria: first-audio latency under budget, `aec_live_vad_forward` count
matching `--min-interrupts`, numeric `live_server_interruption` count matching
`--min-interrupts`, expected user speech matched in transcript, lesson prompts
sent via Live text with enough total text and matching SHA-256 hashes from the
manifest, no lesson prompt queued through local TTS, required audio interrupts present, and no
fatal/self-interrupt/fallback patterns.

## Best-practice config for TBOT robot

Recommended `config.yaml` block for production TBOT Live mode. Apply via manager-web > role config or directly in the agent's private config.

```yaml
voice_mode:
  type: google_live
  fallback_to_classic_on_error: false
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
    mute_input_after_audio_start_sec: 0.28
    echo_tail_suppression_ms: 550
    echo_tail_extend_rms_threshold: 700
    echo_tail_extend_ms: 350
    echo_tail_max_total_ms: 1400
    echo_tail_audible_ms: 400
    interrupt_replay_buffer_ms: 900
    barge_in_transcript_min_output_age_sec: 0.0
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
| `aec_enabled: true` | lets AEC-cleaned mic audio reach Live VAD while robot speech is active, without enabling local raw/RMS barge-in | US-004 |
| `barge_in: false` / `interrupt_on_input_while_speaking: false` | raw mic frames must not interrupt robot output | US-004 |
| `echo_tail_suppression_ms: 550` + residual extend | stale echo after `tts stop` leaking upstream and self-answer loops | US-004 |
| `barge_in_transcript_min_output_age_sec: 0.0` | User transcript arrives while robot has just started speaking but output is not stopped | US-004 |
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
