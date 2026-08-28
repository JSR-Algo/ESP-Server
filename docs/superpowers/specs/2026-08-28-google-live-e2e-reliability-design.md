# Google Live End-to-End Reliability Test Design

## Status

Approved interactively on 2026-08-28. This design adds verification around the
existing Google Live conversation and lesson flows. It does not change runtime
behavior, production configuration, prompt content, session ownership, fallback
policy, or firmware protocol.

## Goal

Prove that a real TBOT conversation can travel from robot or synthetic device
audio through the TBOT WebSocket server and Google Live, then return usable
audio and control events without a stuck session, duplicated response, stale
audio, unrecovered timeout, or cross-flow regression.

The release gate covers both ordinary conversation and the transition into and
out of lesson mode. A passing run must demonstrate healthy behavior in steady
state and under interruption, silence, transient network loss, Google Live
session reopen, and device reconnect.

## Constraints

- Preserve the current product flow and runtime implementation.
- Reuse the existing Google Live client, provider, WebSocket harnesses, log
  analyzer, physical audit, soak runner, and lesson E2E infrastructure.
- Keep Google Live and `classic_pipeline` verification separate. Google Live
  failures must not be hidden by classic fallback, and Google Live test work
  must not alter classic behavior.
- Never print or persist Google API keys, manager private configuration, bearer
  tokens, or session-resumption handles in test artifacts.
- Make real-API and physical-hardware tests explicit opt-in gates. Deterministic
  tests remain runnable without network access, credentials, or a robot.
- Do not deploy, flash, reset, or mutate physical hardware as part of the
  automated software test suite.

## System Boundary

The E2E boundary is:

```text
robot microphone or approved audio fixture
  -> firmware Opus/WebSocket protocol
  -> TBOT connection and GoogleLiveProvider
  -> Google Live bidirectional session
  -> model transcript/audio/tool events
  -> TBOT interruption, lesson, and audio bridge logic
  -> firmware TTS events and binary audio output
  -> correlated logs and evidence report
```

The suite validates communication across this boundary. Unit-level DSP quality,
model answer quality, lesson content pedagogy, and renderer pixel correctness
remain covered by their existing dedicated suites.

## Test Architecture

### Layer 1: Deterministic protocol and lifecycle regression

Run the existing fake-client and fake-session suites as the fast mandatory
gate. These tests cover setup configuration, event mapping, audio forwarding,
tool calls, barge-in, reconnect buffering, receive-loop termination, timeout
recovery, lesson conversation, lesson handoff, and provider fallback edges.

Add a single E2E-style lifecycle scenario using the existing in-process
connection/test doubles. The scenario exercises the same public event sequence
used by the real path:

1. WebSocket hello establishes input and output audio parameters.
2. The provider opens or prewarms one Google Live session.
3. User audio opens a clean turn and is finalized.
4. Model audio starts and binary output reaches the device side.
5. A second user turn interrupts the first response.
6. A simulated receive timeout or transport close triggers the current recovery
   path.
7. Buffered input is replayed exactly once after reopen.
8. The new response completes and the connection closes cleanly.

Assertions focus on invariants rather than private timing details:

- at most one active receive loop and one active Live session per connection;
- response identifiers advance monotonically across interruption;
- interrupted response audio cannot resume after the new response starts;
- buffered audio is replayed once, in order, and then cleared;
- timeout/reopen returns the provider to an interactive state;
- close cancels background tasks and leaves no pending receive or flush task;
- expected failures produce a bounded fallback or clean close, never a hung
  `WAITING_MODEL` or `USER_STREAMING` state.

### Layer 2: Real Google Live API smoke

Use `scripts/google_live_smoke.py` and the opt-in live smoke test with either a
resolved `GOOGLE_API_KEY` or manager-backed private configuration. The smoke
must validate more than connect/close by adding an opt-in round trip that:

1. connects using the production model, voice, and language configuration;
2. sends a short approved Vietnamese audio fixture as realtime input;
3. finalizes the user turn;
4. observes server content containing output audio or a valid terminal response;
5. records time to connection, first server event, and first audio;
6. closes the input and session without an exception or orphan receive loop.

The real-API gate may retry once only for a classified transient transport or
service-availability error. Authentication, configuration, unsupported model,
quota exhaustion, malformed request, and repeated timeout failures fail
immediately with their error class preserved in the report.

### Layer 3: Real server WebSocket E2E

Run against a locally or remotely deployed TBOT server configured for
`google_live`. Reuse the existing OTA token and WebSocket helpers so the path
includes production authentication, hello negotiation, connection routing,
manager configuration, Google Live, TTS control messages, and binary audio.

The mandatory journeys are:

1. **Conversation round trip:** send Vietnamese speech audio, observe a user
   transcript, one TTS start, binary audio, and one terminal TTS stop.
2. **Voice barge-in:** interrupt active model output with approved Opus speech,
   observe stop for the old response and output for the newest intent.
3. **Silence after response:** remain connected without speech and prove no
   false interruption, reconnect storm, or repeated response.
4. **Receive-timeout recovery:** inject or select the existing bounded timeout
   reproduction, then prove Live reopens or safely releases the turn and handles
   the next utterance.
5. **Device reconnect:** close the device WebSocket during an idle or completed
   turn, reconnect with the same identity, and prove exactly one usable session
   remains.
6. **Lesson entry and exit:** speak the approved lesson-start intent, verify the
   existing handoff suppresses conversational audio during startup, complete at
   least one interactive lesson turn through Google Live, then return to normal
   conversation without stale lesson audio or tool ownership.

The harness emits a machine-readable JSON report and a bounded correlated log
slice. It must include timestamps, device/client identity hashes, journey name,
response/session correlation values already safe for logs, observed latency,
reconnect count, timeout count, audio chunk count, and verdict. It must exclude
credentials and raw child audio by default.

### Layer 4: Physical robot release gate

Run the existing physical Vietnamese smoke and robot soak only after Layers 1-3
pass. Use a real robot, production-equivalent firmware audio settings, the real
microphone/speaker path, and a stable LAN preflight.

The physical sequence is:

1. Verify server reachability, zero packet loss, active Google Live
   configuration, log capture, and firmware AEC posture.
2. Complete ten ordinary Vietnamese conversation turns.
3. Complete ten mid-speech Vietnamese barge-in turns.
4. Hold one quiet interval and one robot-speaking interval to detect false
   interruption or self-echo loops.
5. Disconnect and reconnect the robot, then complete two additional turns.
6. Start a lesson by voice, complete one interactive Google Live lesson step,
   exit or complete the bounded test lesson, and complete one final ordinary
   conversation turn.
7. Audit the captured logs and generate the final JSON evidence report.

Physical execution remains operator-controlled. The test plan may provide exact
commands and evidence paths but must not autonomously flash or deploy firmware.

## Failure Injection

Deterministic and server E2E layers cover these failures without changing the
normal flow:

- Google Live connect timeout;
- receive timeout while waiting for the model;
- transport close or GOAWAY during idle and during a user turn;
- delayed event from an interrupted response;
- duplicate session-resumption update;
- audio arriving while a reopen is in progress;
- device WebSocket close followed by same-device reconnect;
- malformed or unrelated tool event during lesson handoff;
- lesson startup refusal or timeout followed by conversation restoration.

Faults are injected at test seams or through existing reproduction scripts. No
production-only chaos switch is introduced.

## Acceptance Criteria

### Functional correctness

- Every successful user turn produces a transcript or explicit accepted input
  signal, a bounded model response, binary audio when audio output is enabled,
  and one terminal response state.
- Barge-in stops the old output and serves the newest request; no old binary
  audio appears after the replacement response starts.
- Lesson handoff prevents conversation output from leaking into lesson startup,
  and lesson exit restores an ordinary Google Live conversation.
- Tool calls remain allowlisted and associated with the active flow; stale or
  unrelated calls do not mutate state.

### Liveness and cleanup

- No journey ends in a stuck `WAITING_MODEL`, `USER_STREAMING`, or model-speaking
  state.
- No connection owns more than one active receive loop or usable Google Live
  session.
- Reconnect and timeout recovery are bounded and do not form a reopen storm.
- Connection close leaves no pending Google Live receive, audio flush, timeout,
  idle-close, or replay task owned by that connection.

### Reliability budgets

- Real-API connect succeeds within the configured connect timeout.
- First returned audio is at or below the existing physical gate of 1800 ms for
  accepted steady-state turns; cold-start measurements are reported separately.
- Physical barge-in stop latency p95 is at or below the existing 500 ms budget.
- Successful barge-in cycles serve the newest intent in at least 80 percent of
  physical cycles, matching the current soak contract.
- False-positive interruptions are zero during each controlled idle cycle.
- Unexpected fallback, duplicate active session, stale-response audio,
  unhandled exception, and fatal Google Live marker counts are all zero.

### Regression safety

- Focused Google Live suites pass.
- Existing lesson voice, lesson handoff, WebSocket reconnect, and classic voice
  regression suites pass.
- A skipped real-API or physical gate is reported as `SKIPPED`, never `PASS`.

## Error Classification

Reports classify failures into one primary category:

- `credential_or_auth`;
- `model_or_config`;
- `quota_or_rate_limit`;
- `network_or_transport`;
- `google_service_unavailable`;
- `protocol_or_event_order`;
- `audio_input_or_codec`;
- `server_state_or_cleanup`;
- `lesson_handoff_or_ownership`;
- `device_or_firmware`;
- `acceptance_timeout`.

The harness retains the first relevant safe error message, stage, and correlated
log markers so a failure can be triaged without rerunning blindly.

## Evidence and Observability

Each non-unit run writes into a timestamped evidence directory containing:

- `report.json` with environment metadata, journeys, metrics, and verdicts;
- `timeline.log` containing only the bounded test window;
- `commands.txt` with redacted commands and configuration sources;
- `checksums.sha256` for generated artifacts;
- optional physical-run notes and operator timestamps.

Required log evidence includes session identity, connection/receive-loop start
and stop, input audio diagnostics, user transcript or accepted-input marker,
first-audio or turn latency, interruption response IDs, timeout/reopen outcome,
session-resumption update, lesson handoff/ownership markers, TTS terminal state,
and clean disconnect. The verifier rejects missing, contradictory, duplicated,
or out-of-order markers.

## Execution Order and Gate Policy

The gate is strictly layered:

1. deterministic focused suites;
2. full relevant server regression suite;
3. real Google Live API round trip;
4. real TBOT WebSocket journeys;
5. physical robot soak and lesson journey.

A lower-layer failure blocks higher-layer execution because hardware evidence
would otherwise be ambiguous. A higher-layer infrastructure failure is reported
separately from a product assertion failure. Release is allowed only when every
required layer is `PASS`; required but unavailable layers remain blocking.

## File Responsibilities for the Implementation Plan

The implementation plan should prefer extending these existing surfaces:

- `main/tbot-server/tests/test_google_live_live_smoke.py`: opt-in real API round
  trip coverage.
- `main/tbot-server/scripts/google_live_smoke.py`: reusable audio round-trip
  operation and safe metrics.
- `main/tbot-server/scripts/voice_mode_websocket_soak.py`: authenticated
  conversation journey primitives.
- `main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py`: real Opus
  input and interruption journey.
- `main/tbot-server/scripts/google_live_robot_soak.py`: multi-journey reporting,
  physical reliability budgets, and lesson coverage.
- `main/tbot-server/scripts/analyze_google_live_log.py`: lifecycle ordering and
  forbidden-marker verification.
- `main/tbot-server/scripts/physical_smoke_audit.py`: physical gate aggregation.
- `main/tbot-server/tests/test_google_live_reconnect.py` and focused provider,
  client, event, barge-in, tool, lesson, and WebSocket tests: deterministic
  failure injection and cleanup invariants.
- `main/tbot-server/docs/google-live-smoke.md` and
  `main/tbot-server/docs/google-live-robot-validation.md`: operator commands,
  evidence contract, and triage guidance.

New files are justified only for a narrow shared E2E journey runner or report
schema that cannot be kept focused inside the existing scripts.

## Non-goals

- No Google Live provider rewrite or behavior change.
- No prompt, voice, language, model, timeout, retry, or fallback tuning based
  solely on this test-design work.
- No replacement of the existing lesson E2E, curriculum, renderer, or physical
  evidence gates.
- No subjective scoring of model intelligence, teaching style, or voice quality.
- No storage of raw production conversations.
- No CI requirement for credentials, network access, or physical hardware.
