# Google Live End-to-End Production Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a behavior-preserving, layered release gate that proves TBOT-to-Google-Live conversation, barge-in, reconnect, lesson handoff, cleanup, latency, and soak reliability on the exact production candidate.

**Architecture:** Extend the existing Google Live smoke, WebSocket audio harness, log analyzer, robot soak, physical audit, benchmark, and resource-soak contracts instead of creating a parallel test framework. Add one focused shared reliability module for report schema, latency comparison, redaction, candidate identity, and resource verdicts; add one release-manifest verifier that aggregates already-produced evidence without deploying or controlling hardware. Runtime changes are permitted only when a RED regression exposes a behavior violation, and each such change must be surgical and retain the `b07038b8` behavior contract.

**Tech Stack:** Python 3.11+, asyncio, unittest/pytest, `google-genai`, `websockets`, Opus/WAV fixtures, psutil/resource metrics, JSON/SHA-256 evidence, existing TBOT Google Live provider and lesson runtime.

---

## Scope And Baseline

- Compatibility baseline: Git commit `b07038b8`.
- Approved design: `docs/superpowers/specs/2026-08-28-google-live-e2e-reliability-design.md`.
- Execution must begin in a dedicated worktree based on the branch that contains this plan.
- Do not alter prompts, model, voice, language, reconnect policy, timeout values, fallback policy, firmware protocol, or lesson ownership merely to make a gate pass.
- Real Google API, remote server, and physical robot commands are opt-in. Missing credentials or hardware produce `SKIPPED` and block a production release; they never produce `PASS`.
- The existing classic voice suite remains separate and mandatory.
- Commands below use `python3`. Before Task 1, verify that it is Python 3.11 or
  newer and can import `pytest`, `google.genai`, `websockets`, `numpy`, `psutil`,
  `yaml`, and `aiohttp`. The checked-in `.venv311/bin/python` may be a
  non-executable dereferenced symlink in copied worktrees; do not rely on it
  unless `test -x .venv311/bin/python` passes.

```bash
cd main/tbot-server
python3 --version
python3 -m pytest --version
python3 -c 'import google.genai, websockets, numpy, psutil, yaml, aiohttp; print("Google Live test runtime OK")'
```

## File Map

- Create `main/tbot-server/scripts/google_live_reliability.py`: shared report schema, percentiles, redaction, candidate identity, baseline comparison, resource sampling, and resource verdict adapter.
- Create `main/tbot-server/tests/test_google_live_reliability.py`: deterministic tests for the shared contract.
- Create `main/tbot-server/tests/test_google_live_lifecycle_e2e.py`: in-process lifecycle journey covering turn, interruption, timeout/reopen, replay, and cleanup.
- Modify `main/tbot-server/scripts/google_live_smoke.py`: add a real audio round trip, safe metrics, JSON report, and classified failure output.
- Modify `main/tbot-server/tests/test_google_live_smoke_script.py`: RED/GREEN tests for round-trip event handling and secret safety.
- Modify `main/tbot-server/tests/test_google_live_live_smoke.py`: opt-in real-API audio round trip.
- Modify `main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py`: complete the replacement response, measure audio gaps, and emit a journey record.
- Modify `main/tbot-server/tests/test_voice_mode_websocket_audio_bargein.py`: deterministic WebSocket journey tests.
- Modify `main/tbot-server/scripts/analyze_google_live_log.py`: add lifecycle invariants and production forbidden-marker verdicts.
- Modify `main/tbot-server/tests/test_analyze_google_live_log.py`: invariant ordering/balance tests.
- Modify `main/tbot-server/scripts/google_live_robot_soak.py`: add candidate composite mode, conversation/reconnect/lesson stages, resource sampling, baseline comparison, and exact identity.
- Create `main/tbot-server/tests/test_google_live_robot_soak.py`: focused tests for candidate-mode orchestration and verdicts.
- Modify `main/tbot-server/scripts/physical_smoke_audit.py`: enforce first-audio percentile, server-stop, output-gap, lifecycle-balance, and exact candidate identity.
- Modify `main/tbot-server/tests/test_physical_smoke_audit.py`: tests for the new production gates.
- Create `main/tbot-server/scripts/google_live_release_gate.py`: aggregate layer reports and fail closed on missing, skipped, mismatched, or failed evidence.
- Create `main/tbot-server/tests/test_google_live_release_gate.py`: release-manifest validation tests.
- Modify `main/tbot-server/docs/google-live-smoke.md`: exact software/API/WebSocket commands.
- Modify `main/tbot-server/docs/google-live-robot-validation.md`: physical and candidate-soak procedure, evidence layout, triage, and rollback boundary.

## Gate Budgets

```python
GOOGLE_LIVE_LIMITS = {
    "firstAudioP50Ms": 1200.0,
    "firstAudioP95Ms": 1800.0,
    "serverStopMaxMs": 250.0,
    "physicalBargeinP95Ms": 500.0,
    "serverOutputGapMaxMs": 250.0,
    "relativeLatencyRegressionPct": 15.0,
    "minimumSoakTurns": 30,
    "minimumSoakDurationSec": 1800.0,
    "minimumBargeins": 10,
    "minimumLatestIntentSuccessRate": 0.80,
    "rssDeltaBytes": 32 * 1024 * 1024,
    "fdDelta": 8,
    "asyncioTaskDelta": 4,
    "threadDelta": 4,
    "rssSlopeBytesPerSample": 1024 * 1024,
    "fdSlopePerSample": 0.25,
    "asyncioTaskSlopePerSample": 0.25,
    "threadSlopePerSample": 0.25,
}
```

### Task 1: Shared Reliability Report And Candidate Identity

**Files:**
- Create: `main/tbot-server/scripts/google_live_reliability.py`
- Create: `main/tbot-server/tests/test_google_live_reliability.py`

- [ ] **Step 1: Write failing report-contract tests**

Create tests with these exact behaviors:

```python
from scripts.google_live_reliability import (
    GOOGLE_LIVE_LIMITS,
    build_candidate_identity,
    compare_latency_baseline,
    redact_mapping,
    reliability_verdict,
)


def test_redaction_removes_all_secret_values():
    payload = redact_mapping(
        {
            "authorization": "Bearer secret",
            "api_key": "AIza-secret",
            "session_resumption_handle": "handle-secret",
            "nested": {"token": "jwt-secret", "model": "gemini-live"},
        }
    )
    assert payload == {
        "authorization": "<redacted>",
        "api_key": "<redacted>",
        "session_resumption_handle": "<redacted>",
        "nested": {"token": "<redacted>", "model": "gemini-live"},
    }


def test_candidate_identity_is_stable_and_private():
    identity = build_candidate_identity(
        git_sha="b07038b8",
        image_digest="sha256:image",
        firmware_identity="fw-2.2.72",
        config={"model": "gemini-live", "voice_name": "Kore", "api_key": "secret"},
        fixture_sha256="a" * 64,
    )
    assert identity["gitSha"] == "b07038b8"
    assert identity["configFingerprint"].startswith("sha256:")
    assert "secret" not in str(identity)


def test_relative_latency_regression_fails_above_fifteen_percent():
    result = compare_latency_baseline(
        candidate={"firstAudioP50Ms": 1160.0, "firstAudioP95Ms": 1700.0},
        baseline={"firstAudioP50Ms": 1000.0, "firstAudioP95Ms": 1500.0},
    )
    assert result["checks"]["firstAudioP50Regression"] is False
    assert result["checks"]["firstAudioP95Regression"] is True


def test_reliability_verdict_fails_skipped_or_wrong_candidate_layer():
    verdict = reliability_verdict(
        expected_identity={"gitSha": "candidate"},
        layers=[
            {"name": "deterministic", "status": "PASS", "candidateIdentity": {"gitSha": "candidate"}},
            {"name": "physical", "status": "SKIPPED", "candidateIdentity": {"gitSha": "other"}},
        ],
    )
    assert verdict["status"] == "FAIL"
    assert {failure["code"] for failure in verdict["failures"]} == {
        "LAYER_SKIPPED",
        "CANDIDATE_IDENTITY_MISMATCH",
    }
```

- [ ] **Step 2: Run RED tests**

Run:

```bash
cd main/tbot-server
python3 -m pytest tests/test_google_live_reliability.py -q
```

Expected: collection fails because `scripts.google_live_reliability` does not exist.

- [ ] **Step 3: Implement the shared contract**

Implement these public surfaces:

```python
SCHEMA_VERSION = "google-live-reliability.v1"
SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "session_resumption_handle",
        "handle",
    }
)


def redact_mapping(value):
    if isinstance(value, dict):
        return {
            str(key): "<redacted>" if str(key).lower() in SECRET_KEYS else redact_mapping(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    return value


def percentile(values, percentile_value):
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = min(len(ordered) - 1, int(round((percentile_value / 100) * (len(ordered) - 1))))
    return round(ordered[index], 3)


def build_candidate_identity(*, git_sha, image_digest, firmware_identity, config, fixture_sha256):
    safe_config = redact_mapping(config or {})
    encoded = json.dumps(safe_config, sort_keys=True, separators=(",", ":")).encode()
    return {
        "gitSha": str(git_sha),
        "imageDigest": str(image_digest),
        "firmwareIdentity": str(firmware_identity),
        "configFingerprint": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "fixtureSha256": str(fixture_sha256),
    }


def compare_latency_baseline(*, candidate, baseline):
    checks = {}
    deltas = {}
    for key in ("firstAudioP50Ms", "firstAudioP95Ms", "bargeinP95Ms", "reconnectRecoveryP95Ms"):
        if key not in candidate or key not in baseline or not baseline[key]:
            continue
        regression = ((float(candidate[key]) - float(baseline[key])) / float(baseline[key])) * 100
        check_name = key.removesuffix("Ms") + "Regression"
        deltas[key] = round(regression, 3)
        checks[check_name] = regression <= GOOGLE_LIVE_LIMITS["relativeLatencyRegressionPct"]
    return {"checks": checks, "regressionPct": deltas, "pass": all(checks.values())}


def reliability_verdict(*, expected_identity, layers):
    failures = []
    for layer in layers:
        if layer.get("status") != "PASS":
            failures.append({"code": f"LAYER_{layer.get('status', 'MISSING')}", "layer": layer.get("name")})
        if layer.get("candidateIdentity") != expected_identity:
            failures.append({"code": "CANDIDATE_IDENTITY_MISMATCH", "layer": layer.get("name")})
    return {"schemaVersion": SCHEMA_VERSION, "status": "PASS" if not failures else "FAIL", "failures": failures}
```

Also expose `sample_process_resources()` and `resource_verdict(samples)` by reusing the exact limits and slope function from `scripts/course_mode_resource_soak.py`; add tests for RSS/task leak failures.

- [ ] **Step 4: Run GREEN tests**

Run the Task 1 test command. Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/scripts/google_live_reliability.py \
  main/tbot-server/tests/test_google_live_reliability.py
git commit -m "test: add Google Live reliability report contract"
```

### Task 2: Deterministic Full Lifecycle Regression

**Files:**
- Create: `main/tbot-server/tests/test_google_live_lifecycle_e2e.py`
- Modify only if RED exposes a defect: `main/tbot-server/core/voice/google_live/client.py`
- Modify only if RED exposes a defect: `main/tbot-server/core/voice/session_provider/google_live.py`
- Modify only if RED exposes a defect: `main/tbot-server/core/voice/google_live/audio_bridge.py`
- Modify only if RED exposes a defect: `main/tbot-server/core/connection.py`

- [ ] **Step 1: Add an in-process lifecycle journey**

Build a fake Live session whose first receive iterator emits model audio, then whose replacement iterator times out once and emits replacement audio. Use the existing `GoogleLiveClient` and provider seams; do not call the network. Assert:

```python
async def test_turn_interrupt_timeout_reopen_replay_and_close_is_single_owned():
    journey = await run_in_process_google_live_journey(
        first_events=[
            {"type": "audio_start"},
            {"type": "audio", "data": b"old-audio", "mime_type": "audio/pcm;rate=24000"},
        ],
        interrupt_audio=[b"frame-1", b"frame-2"],
        reopened_events=[
            {"type": "transcript", "source": "user", "text": "dung lai"},
            {"type": "audio_start"},
            {"type": "audio", "data": b"new-audio", "mime_type": "audio/pcm;rate=24000"},
            {"type": "audio_end"},
        ],
    )

    assert journey.receive_loop_max_active == 1
    assert journey.live_session_max_active == 1
    assert journey.replayed_audio == [b"frame-1", b"frame-2"]
    assert journey.replay_count == 1
    assert journey.device_audio_after_replacement == [b"new-audio"]
    assert journey.response_ids == sorted(set(journey.response_ids))
    assert journey.final_state == "LISTENING"
    assert journey.pending_owned_tasks == []
```

Define the helper and dataclass entirely inside the test file. The fake connection must implement the same minimal attributes already used by `tests/test_google_live_reconnect.py`, plus an output sink and `voice_metrics` collector.

- [ ] **Step 2: Lock the historical regression node ids**

Add a collection test that verifies the named production regressions still
exist and remain part of the release suite:

```python
HISTORICAL_REGRESSION_NODE_IDS = (
    "tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_model_audio_start_cancels_pending_idle_input_flush",
    "tests/test_connection_voice_provider_routing.py::ConnectionVoiceProviderRoutingTest::test_google_live_ping_routes_to_classic_heartbeat_handler",
    "tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_start_asr_fallback_live_close_breaks_fragment_chain",
    "tests/test_connection_edges.py::ConnectionEdgeTest::test_lesson_start_handoff_uses_generation_token_and_rejects_stale_release",
    "tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_matching_device_drain_ack_releases_prompt_wait_and_stale_ack_does_not",
    "tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_quota_error_logs_no_retry_and_returns_false",
    "tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_invalid_config_error_logs_no_retry_and_returns_false",
    "tests/test_audio_rate_controller_edges.py::test_controller_constructs_without_a_running_loop_and_rebinds_between_loops",
)


def test_historical_regression_node_ids_are_unique_and_named():
    assert len(HISTORICAL_REGRESSION_NODE_IDS) == len(set(HISTORICAL_REGRESSION_NODE_IDS))
    assert all(node_id.startswith("tests/test_") and "::test_" in node_id for node_id in HISTORICAL_REGRESSION_NODE_IDS)
```

After adding the tuple, run `pytest --collect-only -q` with every node id in the
tuple. A renamed or removed regression must update both the test and the approved
compatibility matrix in the same commit; silently dropping a node is forbidden.

```bash
cd main/tbot-server
python3 -m pytest --collect-only -q \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_model_audio_start_cancels_pending_idle_input_flush \
  tests/test_connection_voice_provider_routing.py::ConnectionVoiceProviderRoutingTest::test_google_live_ping_routes_to_classic_heartbeat_handler \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_start_asr_fallback_live_close_breaks_fragment_chain \
  tests/test_connection_edges.py::ConnectionEdgeTest::test_lesson_start_handoff_uses_generation_token_and_rejects_stale_release \
  tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_matching_device_drain_ack_releases_prompt_wait_and_stale_ack_does_not \
  tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_quota_error_logs_no_retry_and_returns_false \
  tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_invalid_config_error_logs_no_retry_and_returns_false \
  tests/test_audio_rate_controller_edges.py::test_controller_constructs_without_a_running_loop_and_rebinds_between_loops
```

Expected: eight tests collected.

- [ ] **Step 3: Run RED/compatibility tests**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_lifecycle_e2e.py \
  tests/test_google_live_client.py \
  tests/test_google_live_reconnect.py \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_audio_bridge_edges.py \
  tests/test_google_live_bargein.py \
  tests/test_google_live_event_mapping.py \
  tests/test_google_live_tool_calls.py \
  tests/test_google_live_lesson_conversation.py \
  tests/test_connection_voice_provider_routing.py \
  tests/test_audio_rate_controller_cleanup.py -q
```

Expected: new tests either pass against the baseline behavior or fail with one observable invariant. Existing tests must remain green.

- [ ] **Step 4: If any new test fails, make the smallest behavior-preserving fix**

Before editing production code, record the failing test and root cause in the commit message body. Acceptable fixes are cancellation/cleanup, missing guard, event routing, or observability needed to prove the existing contract. Do not tune model/config values. Run the exact failed test after each minimal change, then rerun the full Task 2 command.

- [ ] **Step 5: Commit**

```bash
git add main/tbot-server/tests/test_google_live_lifecycle_e2e.py
git add main/tbot-server/core/voice/google_live/client.py \
  main/tbot-server/core/voice/session_provider/google_live.py \
  main/tbot-server/core/voice/google_live/audio_bridge.py \
  main/tbot-server/core/connection.py
git commit -m "test: lock Google Live lifecycle compatibility"
```

### Task 3: Real Google API Audio Round Trip

**Files:**
- Modify: `main/tbot-server/scripts/google_live_smoke.py`
- Modify: `main/tbot-server/tests/test_google_live_smoke_script.py`
- Modify: `main/tbot-server/tests/test_google_live_live_smoke.py`

- [ ] **Step 1: Write failing unit tests for round-trip collection**

Add a fake client that yields transcript, audio start, audio, and audio end. Assert the helper returns safe metrics and always closes:

```python
async def test_run_audio_round_trip_collects_terminal_audio_and_closes(tmp_path):
    client = _FakeClient(
        events=[
            {"type": "transcript", "source": "user", "text": "xin chao"},
            {"type": "audio_start"},
            {"type": "audio", "data": b"pcm", "mime_type": "audio/pcm;rate=24000"},
            {"type": "audio_end"},
        ]
    )
    result = await smoke._run_audio_round_trip(
        client,
        pcm_chunks=[b"\x00\x00" * 320],
        event_timeout_sec=2,
        clock=_Clock([0.0, 0.2, 0.4]),
    )
    assert result["status"] == "PASS"
    assert result["audioChunks"] == 1
    assert result["firstServerEventMs"] == 200.0
    assert result["firstAudioMs"] == 400.0
    assert client.end_calls == 1
    assert client.close_calls == 1
    assert "pcm" not in json.dumps(result)
```

Add failure tests for timeout, auth, quota, invalid model/config, and transient service errors. Assert reports contain a class and safe message, never key/token/handle values.

- [ ] **Step 2: Run RED tests**

```bash
cd main/tbot-server
python3 -m pytest tests/test_google_live_smoke_script.py -q
```

Expected: fail because `_run_audio_round_trip`, `--round-trip`, `--audio-file`, and `--report` do not exist.

- [ ] **Step 3: Implement WAV-to-PCM streaming and event collection**

Add:

```python
async def _run_audio_round_trip(client, *, pcm_chunks, event_timeout_sec, clock=time.monotonic):
    started = clock()
    first_event_ms = None
    first_audio_ms = None
    audio_chunks = 0
    terminal = False
    try:
        await client.connect()
        for chunk in pcm_chunks:
            await client.send_audio(chunk)
        await client.end_audio_stream()
        async with asyncio.timeout(event_timeout_sec):
            async for event in client.receive_events():
                now_ms = round((clock() - started) * 1000, 1)
                if first_event_ms is None:
                    first_event_ms = now_ms
                if event.get("type") == "audio":
                    audio_chunks += 1
                    if first_audio_ms is None:
                        first_audio_ms = now_ms
                if event.get("type") in {"audio_end", "turn_complete"}:
                    terminal = True
                    break
        if not terminal or audio_chunks == 0:
            raise RuntimeError("Google Live round trip ended without terminal audio")
        return {
            "status": "PASS",
            "firstServerEventMs": first_event_ms,
            "firstAudioMs": first_audio_ms,
            "audioChunks": audio_chunks,
        }
    finally:
        await client.close()
```

Use Python `wave` to require mono 16-bit PCM WAV and yield 20 ms chunks at the fixture sample rate. Default fixture:

```text
tests/fixtures/tvideo_farm_audio/adult_speech_24k_mono.wav
```

The CLI writes a redacted `google-live-reliability.v1` layer report. Retry exactly once only when classification is `network_or_transport` or `google_service_unavailable`.

- [ ] **Step 4: Extend the opt-in live test**

Add:

```python
async def test_real_audio_round_trip(self):
    report = await smoke._run_audio_round_trip(
        self.client,
        pcm_chunks=smoke._read_pcm_chunks(self.fixture, chunk_ms=20),
        event_timeout_sec=20,
    )
    self.assertEqual(report["status"], "PASS")
    self.assertGreater(report["audioChunks"], 0)
    self.assertLessEqual(report["firstAudioMs"], 1800.0)
```

Keep the existing `RUN_GOOGLE_LIVE_SMOKE=1` and `GOOGLE_API_KEY` skip conditions.

- [ ] **Step 5: Verify and commit**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_smoke_script.py \
  tests/test_google_live_live_smoke.py -q
git add scripts/google_live_smoke.py \
  tests/test_google_live_smoke_script.py \
  tests/test_google_live_live_smoke.py
git commit -m "test: exercise real Google Live audio round trip"
```

Expected without credentials: unit tests pass and the live test is skipped. With explicit credentials: live round trip passes.

### Task 4: Full WebSocket Audio Conversation And Barge-In Journey

**Files:**
- Modify: `main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py`
- Modify: `main/tbot-server/tests/test_voice_mode_websocket_audio_bargein.py`
- Modify: `main/tbot-server/tests/test_voice_mode_websocket_soak.py`

- [ ] **Step 1: Write failing full-journey tests**

Update the fake WebSocket sequence to include binary response data and a replacement `tts start/stop`. Assert:

```python
assert record["status"] == "PASS"
assert record["oldResponseStopped"] is True
assert record["replacementResponseStarted"] is True
assert record["replacementBinaryChunks"] == 2
assert record["maxServerOutputGapMs"] == 60.0
assert record["bargeinStopMs"] <= 500.0
assert record["correlationSource"] == "server_log"
```

Add a RED case in which replacement start occurs but no binary audio or terminal
stop follows; expected status is `FAIL` with
`REPLACEMENT_RESPONSE_INCOMPLETE`. Binary WebSocket frames do not carry a
response id, so stale-response ownership is determined by Task 5's correlated
server log, never guessed by this transport harness.

- [ ] **Step 2: Run RED tests**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_voice_mode_websocket_audio_bargein.py \
  tests/test_voice_mode_websocket_soak.py -q
```

- [ ] **Step 3: Implement event timestamps and terminal journey**

Replace the early return after interrupt stop with a collector that waits for replacement start, binary audio, and terminal stop:

```python
async def _collect_replacement_response(websocket, *, timeout_sec, clock=time.monotonic):
    deadline = clock() + timeout_sec
    started = False
    binary_times = []
    messages = []
    while clock() < deadline:
        message = await asyncio.wait_for(websocket.recv(), timeout=max(0.01, deadline - clock()))
        now = clock()
        if isinstance(message, bytes):
            if started:
                binary_times.append(now)
            continue
        payload = json.loads(message)
        messages.append(payload)
        if _is_tts_state(payload, "start"):
            started = True
        elif started and _is_tts_state(payload, "stop"):
            gaps = [(right - left) * 1000 for left, right in zip(binary_times, binary_times[1:])]
            return {
                "replacementResponseStarted": True,
                "replacementBinaryChunks": len(binary_times),
                "maxServerOutputGapMs": round(max(gaps, default=0.0), 1),
                "messages": messages,
            }
    raise RuntimeError("replacement response did not reach terminal stop")
```

Add optional correlation parsing for response ids already present in TTS/log events. If the transport does not expose response ids, report `correlationSource: "server_log"` and let the bounded log verifier decide stale ownership; do not invent ids.

- [ ] **Step 4: Verify and commit**

Run Task 4 tests, then:

```bash
git add main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py \
  main/tbot-server/tests/test_voice_mode_websocket_audio_bargein.py \
  main/tbot-server/tests/test_voice_mode_websocket_soak.py
git commit -m "test: complete Google Live websocket barge-in journey"
```

### Task 5: Log Lifecycle And Forbidden-Marker Verifier

**Files:**
- Modify: `main/tbot-server/scripts/analyze_google_live_log.py`
- Modify: `main/tbot-server/tests/test_analyze_google_live_log.py`

- [ ] **Step 1: Write failing lifecycle-verdict tests**

Add logs proving balanced and unbalanced lifecycles:

```python
verdict = analyze_reliability_window(log_path)
assert verdict["status"] == "PASS"
assert verdict["receiveLoopBalance"] == 0
assert verdict["maxReceiveLoopsActive"] == 1
assert verdict["duplicateResponseIds"] == []
assert verdict["staleAudioAfterReplacement"] == 0
assert verdict["fatalHits"] == []
```

Failure fixtures must cover:

```text
two receive loop starts without a stop
replayed_buffered_audio twice for one reopen
waiting_model_timeout without recovery/terminal outcome
old response audio after next response id
auth/quota/config error followed by reconnect attempt
lesson handoff acquire without transfer or release
connection close with pending flush/timeout/replay task marker
firmware pings continuing while the Live lesson step makes no progress
```

- [ ] **Step 2: Run RED tests**

```bash
cd main/tbot-server
python3 -m pytest tests/test_analyze_google_live_log.py -q
```

- [ ] **Step 3: Implement `analyze_reliability_window()`**

Add marker patterns and a stateful single-pass verifier. Return:

```python
{
    "status": "PASS" | "FAIL",
    "receiveLoopBalance": int,
    "maxReceiveLoopsActive": int,
    "replayCountsByReopen": dict,
    "duplicateResponseIds": list,
    "staleAudioAfterReplacement": int,
    "unrecoveredTimeouts": list,
    "unreleasedLessonHandoffs": list,
    "fatalHits": list,
    "failures": [{"code": str, "line": int, "detail": str}],
}
```

Treat the existing physical fatal patterns as fatal. Allow a receive timeout only when followed within the same connection window by reopen success, bounded retry/fallback, explicit step failure, or clean close. Keep parsing linear in log size.

- [ ] **Step 4: Expose CLI output and commit**

Add `--check-reliability` to write the result into the existing JSON output and exit non-zero on failure.

```bash
cd main/tbot-server
python3 -m pytest tests/test_analyze_google_live_log.py -q
git add scripts/analyze_google_live_log.py tests/test_analyze_google_live_log.py
git commit -m "test: verify Google Live lifecycle invariants from logs"
```

### Task 6: Production Candidate Composite Soak

**Files:**
- Modify: `main/tbot-server/scripts/google_live_robot_soak.py`
- Create: `main/tbot-server/tests/test_google_live_robot_soak.py`
- Use: `main/tbot-server/scripts/google_live_reliability.py`

- [ ] **Step 1: Write failing orchestration tests**

Inject async journey callables so tests do not open a network. Assert exact stage order:

```python
report = await run_candidate_soak(
    args,
    journeys={
        "conversation": fake_conversation,
        "bargein": fake_bargein,
        "quiet": fake_quiet,
        "reopen": fake_reopen,
        "reconnect": fake_reconnect,
        "lesson": fake_lesson,
    },
    sample_resources=fake_samples,
    clock=fake_clock,
)
assert [stage["name"] for stage in report["stages"]] == [
    "conversation",
    "bargein",
    "quiet",
    "reopen",
    "reconnect",
    "lesson",
    "conversation_after_lesson",
]
assert report["totals"]["successfulTurns"] == 30
assert report["totals"]["bargeins"] == 10
assert report["resourceVerdict"]["status"] == "PASS"
assert report["status"] == "PASS"
```

Add failures for duration below 1800 seconds, 29 turns, nine barge-ins, 79 percent newest-intent success, false interrupt, unexpected fallback, resource leak, candidate mismatch, and latency regression above 15 percent.

- [ ] **Step 2: Run RED tests**

```bash
cd main/tbot-server
python3 -m pytest tests/test_google_live_robot_soak.py -q
```

- [ ] **Step 3: Add `--mode candidate` and exact arguments**

Add CLI arguments:

```text
--mode candidate
--candidate-git-sha
--candidate-image-digest
--firmware-identity
--baseline-report
--fixture-sha256
--minimum-turns 30
--minimum-duration-sec 1800
--bargein-cycles 10
--lesson-manifest
--report
```

`--report` and all identity arguments are required in candidate mode. The mode reuses existing WebSocket helpers and audio fixtures, samples resources before/after each stage, and calls the log reliability verifier on the bounded log slice.

- [ ] **Step 4: Implement the staged workload**

Implement `run_candidate_soak()` with this fixed production sequence:

```python
for _ in range(17):
    await run_conversation_turn()
for _ in range(10):
    await run_audio_bargein_turn()
await run_quiet_window()
await run_quiet_window(robot_speaking=True)
await run_live_reopen_turn()
await run_same_device_reconnect_turn()
await run_lesson_entry_interactive_exit()
await run_conversation_turn(label="conversation_after_lesson")
```

If the total duration is below 1800 seconds after work completes, remain connected in a monitored quiet window until the minimum duration is met. Do not pad latency measurements or mark synthetic waiting as a turn.

- [ ] **Step 5: Verify and commit**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_robot_soak.py \
  tests/test_google_live_reliability.py \
  tests/test_analyze_google_live_log.py \
  tests/test_voice_mode_websocket_soak.py \
  tests/test_voice_mode_websocket_audio_bargein.py -q
git add scripts/google_live_robot_soak.py tests/test_google_live_robot_soak.py
git commit -m "test: add production candidate Google Live soak"
```

### Task 7: Physical Audit Production Budgets

**Files:**
- Modify: `main/tbot-server/scripts/physical_smoke_audit.py`
- Modify: `main/tbot-server/tests/test_physical_smoke_audit.py`

- [ ] **Step 1: Add failing aggregate budget tests**

Add focused test functions near the existing latency tests:

```python
assert report["firstAudioLatencyMs"]["p50"] <= 1200.0
assert report["firstAudioLatencyMs"]["p95"] <= 1800.0
assert report["interruptStopLatencyMs"]["max"] <= 250.0
assert report["physicalBargeinLatencyMs"]["p95"] <= 500.0
assert report["serverOutputGapMs"]["max"] <= 250.0
assert report["receiveLoopBalance"] == 0
assert report["maxReceiveLoopsActive"] == 1
assert report["candidateIdentity"] == expected_identity
```

Add one failure test for each bound and one test proving an intentional interrupt boundary excludes the corresponding output gap.

- [ ] **Step 2: Run focused RED tests**

```bash
cd main/tbot-server
python3 -m pytest tests/test_physical_smoke_audit.py -q
```

- [ ] **Step 3: Add production-candidate CLI arguments**

```text
--production-google-live-candidate
--candidate-git-sha
--candidate-image-digest
--firmware-identity
--config-fingerprint
--fixture-sha256
--max-first-audio-p50-ms 1200
--max-first-audio-p95-ms 1800
--max-server-stop-ms 250
--max-physical-bargein-p95-ms 500
--max-server-output-gap-ms 250
```

When `--production-google-live-candidate` is set, require all identity arguments and every existing strict voice/lesson marker. Remove the legacy allowance that treats `Google Live waiting_model_timeout` as acceptable in the production-candidate profile; a timeout is permitted only when the reliability analyzer proves bounded recovery.

- [ ] **Step 4: Verify and commit**

```bash
cd main/tbot-server
python3 -m pytest tests/test_physical_smoke_audit.py -q
git add scripts/physical_smoke_audit.py tests/test_physical_smoke_audit.py
git commit -m "test: enforce Google Live physical production budgets"
```

### Task 8: Exact-Candidate Release Manifest Gate

**Files:**
- Create: `main/tbot-server/scripts/google_live_release_gate.py`
- Create: `main/tbot-server/tests/test_google_live_release_gate.py`

- [ ] **Step 1: Write failing manifest tests**

Use temporary JSON reports for the six required layers:

```python
REQUIRED_LAYERS = (
    "deterministic",
    "server_regression",
    "real_api",
    "websocket_e2e",
    "physical",
    "candidate_soak",
)
```

Assert PASS only when every report has `status: PASS`, the same full candidate identity, a checksum, and the expected schema. Add failures for missing file, `SKIPPED`, wrong SHA/image/config/firmware/fixture, corrupt JSON, checksum mismatch, failed latency comparison, and failed resource verdict.

- [ ] **Step 2: Run RED tests**

```bash
cd main/tbot-server
python3 -m pytest tests/test_google_live_release_gate.py -q
```

- [ ] **Step 3: Implement read-only aggregation**

The CLI is shown below. Populate the variables from the candidate build and
generated report checksums before running it:

```bash
RUN_ID=20260830T140500Z
EVIDENCE_ROOT="task-artifacts/google-live/$RUN_ID"
python3 scripts/google_live_release_gate.py \
  --expected-git-sha "$CANDIDATE_SHA" \
  --expected-image-digest "$CANDIDATE_IMAGE_DIGEST" \
  --expected-firmware-identity "$FIRMWARE_IDENTITY" \
  --expected-config-fingerprint "$CONFIG_FINGERPRINT" \
  --expected-fixture-sha256 "$FIXTURE_SHA256" \
  --layer "deterministic=$EVIDENCE_ROOT/deterministic/report.json" \
  --layer "server_regression=$EVIDENCE_ROOT/server-regression/report.json" \
  --layer "real_api=$EVIDENCE_ROOT/real-api/report.json" \
  --layer "websocket_e2e=$EVIDENCE_ROOT/websocket-e2e/report.json" \
  --layer "physical=$EVIDENCE_ROOT/physical/report.json" \
  --layer "candidate_soak=$EVIDENCE_ROOT/candidate-soak/report.json" \
  --out "$EVIDENCE_ROOT/release-verdict.json"
```

The script reads and validates evidence only. It must not start a server, deploy, flash, reset, call Google, open a WebSocket, or operate hardware.

- [ ] **Step 4: Verify and commit**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_release_gate.py \
  tests/test_google_live_reliability.py -q
git add scripts/google_live_release_gate.py tests/test_google_live_release_gate.py
git commit -m "test: aggregate exact-candidate Google Live release evidence"
```

### Task 9: Operator Documentation And Evidence Layout

**Files:**
- Modify: `main/tbot-server/docs/google-live-smoke.md`
- Modify: `main/tbot-server/docs/google-live-robot-validation.md`

- [ ] **Step 1: Document deterministic and real-API gates**

Add exact commands:

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_lifecycle_e2e.py \
  tests/test_google_live_client.py \
  tests/test_google_live_reconnect.py \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_audio_bridge_edges.py \
  tests/test_google_live_bargein.py \
  tests/test_google_live_event_mapping.py \
  tests/test_google_live_tool_calls.py \
  tests/test_google_live_lesson_conversation.py -q

RUN_ID=20260830T140500Z
RUN_GOOGLE_LIVE_SMOKE=1 GOOGLE_API_KEY="$GOOGLE_API_KEY" \
python3 scripts/google_live_smoke.py \
  --round-trip \
  --audio-file tests/fixtures/tvideo_farm_audio/adult_speech_24k_mono.wav \
  --report "task-artifacts/google-live/$RUN_ID/real-api/report.json"
```

- [ ] **Step 2: Document WebSocket and physical commands**

Include real device/client placeholders, OTA token sourcing, bounded log capture, physical voice instructions, candidate identity arguments, and exact report locations. State explicitly that the operator speaks Vietnamese close to the robot during opened listening windows and interrupts while robot audio is active.

Add a historical compatibility matrix with one row per contract section from the
approved design. Each row names the exact automated test(s), E2E journey or log
assertion, and the production failure it prevents. The matrix must include
single session/receive-loop ownership, replay-once, stale flush cancellation,
heartbeat ping routing, trigger-fragment cleanup, barge-in idempotency, stale
event rejection, timeout recovery, non-retriable error classification, lesson
handoff generations, device audio-drain ordering, semantic tool fencing, event
loop cleanup, and classic-pipeline isolation.

- [ ] **Step 3: Document candidate soak and release aggregation**

Use this evidence tree, where `$RUN_ID` is a UTC value such as
`20260830T140500Z`:

```text
task-artifacts/google-live/$RUN_ID/
  deterministic/report.json
  server-regression/report.json
  real-api/report.json
  websocket-e2e/report.json
  physical/report.json
  candidate-soak/report.json
  timeline.log
  commands.txt
  checksums.sha256
  release-verdict.json
```

Document that raw child audio is not stored by default; command files redact tokens and keys; session-resumption handle values never enter artifacts.

- [ ] **Step 4: Verify docs and commit**

```bash
git diff --check -- main/tbot-server/docs/google-live-smoke.md \
  main/tbot-server/docs/google-live-robot-validation.md
git add main/tbot-server/docs/google-live-smoke.md \
  main/tbot-server/docs/google-live-robot-validation.md
git commit -m "docs: add Google Live production reliability runbook"
```

### Task 10: Full Verification And Release-Gate Dry Run

**Files:**
- Verify all files from Tasks 1-9.

- [ ] **Step 1: Run formatting and syntax checks**

```bash
cd main/tbot-server
python3 -m ruff check \
  scripts/google_live_reliability.py \
  scripts/google_live_smoke.py \
  scripts/voice_mode_websocket_audio_bargein.py \
  scripts/analyze_google_live_log.py \
  scripts/google_live_robot_soak.py \
  scripts/physical_smoke_audit.py \
  scripts/google_live_release_gate.py \
  tests/test_google_live_reliability.py \
  tests/test_google_live_lifecycle_e2e.py \
  tests/test_google_live_robot_soak.py \
  tests/test_google_live_release_gate.py
python3 -m py_compile \
  scripts/google_live_reliability.py \
  scripts/google_live_smoke.py \
  scripts/voice_mode_websocket_audio_bargein.py \
  scripts/analyze_google_live_log.py \
  scripts/google_live_robot_soak.py \
  scripts/physical_smoke_audit.py \
  scripts/google_live_release_gate.py
```

Expected: exit 0.

- [ ] **Step 2: Run all focused Google Live and harness tests**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_reliability.py \
  tests/test_google_live_lifecycle_e2e.py \
  tests/test_google_live_smoke_script.py \
  tests/test_google_live_live_smoke.py \
  tests/test_google_live_client.py \
  tests/test_google_live_reconnect.py \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_provider_fallback.py \
  tests/test_google_live_audio_bridge_edges.py \
  tests/test_google_live_bargein.py \
  tests/test_google_live_event_mapping.py \
  tests/test_google_live_tool_calls.py \
  tests/test_google_live_lesson_conversation.py \
  tests/test_google_live_course_mode.py \
  tests/test_live_admission_edges.py \
  tests/test_voice_mode_websocket_soak.py \
  tests/test_voice_mode_websocket_audio_bargein.py \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_robot_soak.py \
  tests/test_physical_smoke_audit.py \
  tests/test_google_live_release_gate.py \
  tests/test_course_mode_resource_soak.py \
  tests/test_benchmark_google_live_audio_runtime.py -q
```

Expected: all deterministic tests pass; the explicitly guarded live smoke may skip without credentials.

- [ ] **Step 3: Run classic and adjacent regression suites**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_connection_voice_provider_routing.py \
  tests/test_lesson_voice_nonregression.py \
  tests/test_lesson_voice_output_discipline.py \
  tests/test_lesson_runtime.py \
  tests/test_lesson_nudge_handler.py \
  tests/test_start_lesson_tool.py \
  tests/test_audio_rate_controller_cleanup.py \
  tests/test_audio_rate_controller_edges.py \
  tests/test_send_audio_tts_stop.py -q
```

Expected: pass with no Google Live/classic cross-regression.

- [ ] **Step 4: Run the complete server test suite**

```bash
cd main/tbot-server
python3 -m pytest tests -q
```

Expected: all deterministic tests pass; tests explicitly guarded by missing
credentials, remote services, or hardware report their existing skip result.
Any non-guarded failure blocks progression.

- [ ] **Step 5: Run local performance benchmark**

```bash
cd main/tbot-server
RUN_ID=20260830T140500Z
python3 scripts/benchmark_google_live_audio_runtime.py \
  --speakers 1,2,4 \
  --frames 120 \
  --aec > "task-artifacts/google-live/$RUN_ID/server-regression/audio-benchmark.json"
```

Expected: JSON parses, `audio_execution` is `worker`, recommended admission cap is at least 1, and loop latency has no regression greater than 15 percent against the same-host baseline.

- [ ] **Step 6: Dry-run the release aggregator**

Generate six synthetic PASS reports with the same candidate identity in a temporary directory, run `google_live_release_gate.py`, and assert `release-verdict.json` is `PASS`. Change one layer to `SKIPPED` and assert exit non-zero and `LAYER_SKIPPED`.

- [ ] **Step 7: Run diff hygiene**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended files are modified.

- [ ] **Step 8: Request code review before real/physical execution**

Use the repository's code-review workflow on the completed implementation. Resolve correctness findings, rerun affected tests, then rerun Steps 1-7.

- [ ] **Step 9: Commit final verification-only adjustments**

```bash
git add main/tbot-server/scripts main/tbot-server/tests \
  main/tbot-server/docs/google-live-smoke.md \
  main/tbot-server/docs/google-live-robot-validation.md
git commit -m "test: finalize Google Live production reliability gate"
```

Do not create an empty commit if no adjustments remain.

## Real And Physical Execution Checklist

These actions occur only after Task 10 software verification and explicit operator readiness:

1. Produce the same-environment `b07038b8` baseline evidence with the approved model/voice/language and fixture.
2. Run real API round trip on the candidate.
3. Run authenticated server WebSocket conversation, audio barge-in, timeout recovery, and reconnect journeys.
4. Confirm stable LAN, real robot identity, firmware AEC posture, and candidate image identity.
5. Run ten ordinary Vietnamese turns and ten physical mid-speech barge-ins.
6. Run quiet/self-echo windows, reconnect, lesson entry/interactive turn/exit, and final conversation.
7. Run the 30-turn/30-minute candidate soak.
8. Run physical audit and log reliability verifier over the bounded candidate window.
9. Run the release manifest gate; release only on `PASS` with no missing/skipped layer.

## Production Failure Policy

- Preserve evidence immediately on the first failed invariant.
- Classify the failure as credential/auth, model/config, quota/rate, network/transport, Google service, protocol/order, audio/codec, server cleanup, lesson ownership, device/firmware, or acceptance timeout.
- Do not rerun until the failure has a concrete classification and the suspected environmental condition is recorded.
- A flaky rerun does not erase the original failure. Fix or explicitly prove an external transient before accepting a new run.
- Any runtime fix returns to the Task 2 RED/GREEN loop and requires the full Task 10 regression set plus new real/physical evidence on a new candidate identity.
