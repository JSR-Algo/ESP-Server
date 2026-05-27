# Google Live Voice Mode Design

## Goal

Add one new voice session mode, `google_live`, alongside existing `classic_pipeline` without rewriting current architecture, without deleting old code, and without changing default behavior.

Current default flow must remain:

`audio -> VAD -> ASR -> LLM -> TTS -> audio`

New flow must be additive:

- `classic_pipeline`
- `google_live`

The system must select mode per agent or device, with global fallback default from server config.

## Non-Goals

- No clean-architecture rewrite
- No protocol redesign
- No firmware rewrite
- No microservice split
- No removal of `selected_module`, `ASR`, `LLM`, `TTS`, `VAD`
- No behavior change for existing sessions unless `voice_mode.type == google_live`

## Current Architecture Summary

Observed code boundaries:

- WebSocket entrypoint: `core/websocket_server.py`
- Per-connection lifecycle and routing: `core/connection.py`
- Text message dispatch: `core/handle/textMessageProcessor.py`
- Audio send helpers: `core/handle/sendAudioHandle.py`
- Config load and merge: `config/config_loader.py`
- Per-device private config merge: `ConnectionHandler._initialize_private_config_async()`
- Firmware JSON handlers: `TBOT-Firmware/main/application.cc` for `tts`, `stt`, `llm`

Current server behavior is session-centric. `ConnectionHandler` already owns:

- websocket lifecycle
- bind/auth state
- private config merge
- timeout/reporting
- VAD/ASR/TTS/LLM instances
- binary audio routing
- text message routing

This makes `ConnectionHandler` correct insertion point for voice-mode provider selection.

## Design Overview

Introduce one new provider abstraction for per-session voice transport and orchestration:

```python
class VoiceSessionProvider:
    async def start_session(self) -> None: ...
    async def handle_text_message(self, message: str) -> bool: ...
    async def handle_audio_bytes(self, chunk: bytes) -> bool: ...
    async def interrupt(self) -> None: ...
    async def close(self) -> None: ...
```

Concrete implementations:

- `ClassicPipelineProvider`
- `GoogleLiveProvider`

Factory:

```python
def create_voice_session_provider(conn) -> VoiceSessionProvider:
    mode = conn.config.get("voice_mode", {}).get("type", "classic_pipeline")
    if mode == "google_live":
        return GoogleLiveProvider(conn)
    return ClassicPipelineProvider(conn)
```

`ConnectionHandler` remains owner of connection, auth, config, memory, prompt, reporting, and websocket. Provider only owns voice-session execution path.

## Voice Mode Selection Model

Selection precedence:

1. Agent or device private config from manager API
2. Global server config
3. Hard default: `classic_pipeline`

Recommended config shape:

```yaml
voice_mode:
  type: classic_pipeline
  fallback_to_classic_on_error: true

google_live:
  api_key: ${GOOGLE_API_KEY}
  model: gemini-2.5-flash-native-audio-preview-12-2025
  enable_audio_input: true
  enable_audio_output: true
  native_voice: true
  input_audio_format: pcm16
  input_sample_rate: 16000
  output_audio_format: pcm16
  output_sample_rate: 24000
  connect_timeout_sec: 10
  recv_timeout_sec: 30
  barge_in: true
  send_transcript_events: true
  send_llm_state_events: false
  reconnect:
    enabled: true
    max_retries: 2
    backoff_ms: 500
```

Private config may override:

```json
{
  "voice_mode": {
    "type": "google_live",
    "fallback_to_classic_on_error": true
  },
  "google_live": {
    "model": "gemini-2.5-flash-native-audio-preview-12-2025",
    "native_voice": true
  }
}
```

## Provider Responsibilities

### ClassicPipelineProvider

Purpose: preserve current behavior with minimal code movement.

Responsibilities:

- initialize and reuse current VAD/ASR/TTS lifecycle
- route binary audio into current ASR flow
- preserve current text-message behavior
- preserve current `stt`, `llm`, `tts` signaling
- preserve current abort and cleanup behavior

Implementation rule:

- move as little code as possible
- prefer delegation to existing `ConnectionHandler` helpers rather than duplication

### GoogleLiveProvider

Purpose: open direct streaming voice session with Google Live API while preserving server websocket lifecycle.

Responsibilities:

- create outbound Google Live session
- stream upstream audio from device to Google
- receive downstream live events
- translate live events into existing firmware-compatible JSON/audio outputs
- support interrupt or barge-in where API supports it
- reconnect briefly when configured
- fallback to classic provider when configured and initialization or live session fails

Out of scope for first version:

- full tool-call interleaving inside Google live turn
- memory/tool/plugin parity with classic pipeline inside same live turn

Phase 1 goal is speech-session transport parity, not full reasoning feature parity.

## ConnectionHandler Integration

Minimal invasive changes:

1. Add `self.voice_provider`
2. Create provider after private config merge
3. Delegate audio and text routing to provider first
4. Fall back to current path for classic mode

Proposed shape:

```python
async def handle_connection(self, ws):
    ...
    await self._initialize_private_config_async()
    self.voice_provider = create_voice_session_provider(self)
    await self.voice_provider.start_session()
    async for message in self.websocket:
        await self._route_message(message)
```

```python
async def _route_message(self, message):
    ...
    if isinstance(message, str):
        handled = await self.voice_provider.handle_text_message(message)
        if not handled:
            await handleTextMessage(self, message)
    elif isinstance(message, bytes):
        handled = await self.voice_provider.handle_audio_bytes(message)
        if not handled:
            self.asr_audio_queue.put(message)
```

This keeps current websocket/session ownership intact.

## Websocket and Firmware Compatibility

Compatibility requirement: firmware must keep working without rewrite.

Current firmware already supports:

- binary audio receive
- `tts` state messages
- `stt` text messages
- `llm` metadata messages

Therefore Google Live mode should map to same surface:

- live transcript partial or final -> `type: "stt"`
- live model metadata or emotion if used -> `type: "llm"`
- live audio start -> `type: "tts", state: "start"`
- live audio end -> `type: "tts", state: "stop"`
- live audio bytes -> same binary playback path already used for TTS audio

No protocol break required.

Optional additive message types allowed but not required:

- `voice_mode`
- `provider_state`

These must be ignored safely by old firmware if ever added.

## Audio Bridging

Important constraint:

- firmware transport uses Opus audio packets
- Google Live is expected to use PCM16 or API-defined live audio payloads

Server must bridge formats:

- upstream: Opus from device -> PCM16 for Google
- downstream: PCM16 from Google -> Opus for firmware playback

Needed components:

- Opus decoder for upstream live audio
- resampler as needed
- PCM16 to Opus encoder for downstream playback

Existing audio helper and opus utilities should be reused where possible.

## Google Live Event Mapping

Representative mapping:

- session open -> log `provider initialized`
- transcript event -> optionally emit `stt`
- response audio start -> emit `tts start`
- response audio chunk -> send binary Opus packets
- response audio end -> emit `tts stop`
- interruption acknowledged -> clear pending downstream stream state
- reconnect attempt -> log and retry if configured

## Fallback Behavior

Fallback must be non-crashing.

Config:

```yaml
voice_mode:
  type: google_live
  fallback_to_classic_on_error: true
```

Fallback conditions:

- Google session init failure
- authentication failure to Google
- unsupported live mode response shape
- reconnect budget exhausted mid-session

Fallback policy:

1. Log failure with cause
2. If fallback enabled, instantiate `ClassicPipelineProvider`
3. Start classic provider
4. Continue session if safe
5. Do not crash server process

If fallback disabled:

- surface soft error to client
- close only current session cleanly

## Logging and Metrics

Required log fields:

- `voice_mode`
- `provider`
- `session_id`
- `google_live_model`
- `connect_latency_ms`
- `first_audio_out_latency_ms`
- `streaming_state`
- `reconnect_attempt`
- `fallback_triggered`

Logs must be present for:

- provider selection
- provider initialization
- Google connect success and failure
- upstream stream start
- downstream first audio
- interruption handling
- reconnect
- provider close

## Manager API and Admin UI

### Storage Model

Recommended manager-side schema:

- add `voice_mode` column on agent
- add `google_live_config_json` column on agent

Reason:

- minimal additive DB change
- keeps future provider-specific settings grouped
- avoids many nullable columns

### API Changes

Expose in:

- `AgentEntity`
- `AgentUpdateDTO`
- `AgentDTO`
- config builder returned to Python server

Manager config builder must append:

- `voice_mode`
- `google_live`

while preserving existing `selected_module` contract.

### UI Changes

Add one select in role or agent config page:

- Classic Pipeline
- Google Live API

Behavior:

- when `classic_pipeline`, existing ASR/LLM/TTS config remains visible and behavior unchanged
- when `google_live`, show small config block for Google Live mode
- no redesign
- no removal of old controls

Recommended Google Live UI fields for phase 1:

- mode select
- model
- native voice on or off
- fallback to classic on error

Secret API key should prefer global server config or backend-managed secret, not browser-entered plaintext unless project already allows same pattern for other providers.

## File-Level Change Plan

Python server:

- `core/connection.py`
  - add provider field
  - delegate routing
  - keep old helpers for classic path reuse
- `core/handle/textMessageProcessor.py`
  - allow provider interception for mode-specific control handling
- `core/handle/sendAudioHandle.py`
  - add small reusable helpers for live playback state signaling
- `config/config_loader.py`
  - normalize `voice_mode`
  - merge `google_live`
- `config.yaml`
  - add default `voice_mode` and `google_live` sections
- `core/voice/session_provider/base.py`
  - new interface
- `core/voice/session_provider/classic_pipeline.py`
  - wrapper around current flow
- `core/voice/session_provider/google_live.py`
  - live provider
- `core/voice/session_provider/factory.py`
  - provider selection
- `core/voice/google_live/client.py`
  - Google live transport client
- `core/voice/google_live/audio_bridge.py`
  - codec and resample bridge

Manager API:

- `AgentEntity`
- `AgentUpdateDTO`
- `AgentDTO`
- `AgentServiceImpl`
- `ConfigServiceImpl`
- mapper XML for agent
- DB changelog SQL

Manager Web:

- `src/views/roleConfig.vue`
- related API payload assembly
- i18n labels

Tests:

- config merge tests
- provider factory tests
- classic provider regression tests
- google live fallback tests
- manager config serialization tests

## Migration Strategy

1. Add nullable DB fields and backend serialization support
2. Add server config defaults with `classic_pipeline`
3. Deploy code with no agents switched
4. Enable one canary agent with `google_live`
5. Observe logs and latency
6. Expand rollout gradually

Safety property:

- if no one sets `voice_mode`, old behavior remains unchanged

## Rollback Strategy

Fast rollback:

- set affected agents back to `classic_pipeline`

Code rollback:

- revert deployment while leaving additive DB fields in place

Runtime rollback:

- use `fallback_to_classic_on_error`

No destructive cleanup needed.

## Risks

1. Audio format mismatch
   - Live API may require PCM16 while firmware path uses Opus.
   - Mitigation: explicit audio bridge and resampling tests.

2. Latency regression
   - Extra decode or encode steps can add delay.
   - Mitigation: first-audio metrics and careful buffering.

3. Transcript event spam
   - Too many partial transcripts may flood UI.
   - Mitigation: config gate for transcript emission and debounce if needed.

4. Mid-turn reconnect complexity
   - Retrying live sessions may not preserve exact turn state.
   - Mitigation: small reconnect budget, then fallback.

5. Feature mismatch with classic mode
   - Tooling, memory, or intent behavior may differ.
   - Mitigation: explicitly scope phase 1 to speech-session transport and keep classic as default.

6. Secret handling risk
   - Google API key must not leak into browser unnecessarily.
   - Mitigation: prefer backend or server-level secret storage.

## Compatibility Notes

- Existing endpoints remain unchanged
- Existing `selected_module` config remains unchanged
- Existing websocket binary and JSON behavior remains valid
- Existing firmware handlers remain valid
- Existing classic pipeline remains default
- Existing ASR, LLM, TTS, VAD providers remain untouched unless classic provider wraps them

## Recommended Delivery Phases

### Phase 1

- provider abstraction
- classic wrapper
- google live speech session
- fallback
- admin mode select
- basic Google config

### Phase 2

- richer Google live event mapping
- optional transcript tuning
- optional feature parity work for tools or memory

## Decision Summary

Chosen strategy is additive integration:

- keep `ConnectionHandler` as session owner
- add per-session provider router
- preserve old flow under `ClassicPipelineProvider`
- implement `GoogleLiveProvider` as opt-in mode
- keep websocket and firmware compatibility by mapping live events onto current JSON and binary surfaces

This approach satisfies backward compatibility and minimizes code churn while creating clear extension point for future voice session modes.
