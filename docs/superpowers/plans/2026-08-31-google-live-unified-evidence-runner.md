# Google Live Unified Evidence Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the six remaining evidence-production and provenance blockers so one production-safe runner can create an exact-candidate, privacy-safe Google Live release evidence set without changing the normal conversation flow.

**Architecture:** Add a short-lived, authenticated evidence enrollment registry shared by the HTTP OTA path and Google Live connection path; extend firmware to carry the optional enrolled journey ID into the existing hello; add canonical transcript, deterministic, soak, and command-provenance producers; then orchestrate them through one atomic evidence runner and bind every supporting artifact in the release gate. Existing Google Live clients, providers, correlation logic, physical audit, and release validators remain authoritative.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, websockets, pytest/unittest, XML/JUnit parsing, HMAC-SHA256, ESP-IDF C++/cJSON, firmware source-contract tests, atomic filesystem writes.

---

## Repository Boundaries

Server work is committed in:

```text
/Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-production-reliability
```

Firmware work is committed separately in an isolated worktree created from:

```text
/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware
```

Before Task 2, create a firmware worktree without modifying the current firmware checkout:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware
git worktree add .worktrees/google-live-evidence-journey -b feat/google-live-evidence-journey
```

Never flash, deploy, reset, or connect to hardware while executing this plan.

## File Structure

### Server repository

- Create `main/tbot-server/core/voice/google_live/evidence_enrollment.py`: bounded in-memory enrollment, transcript proof, expiry, and exactly-once finalization.
- Create `main/tbot-server/core/api/google_live_evidence_handler.py`: authenticated internal enrollment/status API using the existing `X-Mint-Secret` policy.
- Modify `main/tbot-server/core/api/ota_handler.py`: advertise only the enrolled journey ID for the exact device/client pair.
- Modify `main/tbot-server/app.py`: construct one registry and inject the same object into both servers.
- Modify `main/tbot-server/core/http_server.py`: pass the injected registry to the evidence handler and OTA handler.
- Modify `main/tbot-server/core/websocket_server.py`: pass the injected registry to every `ConnectionHandler`.
- Modify `main/tbot-server/core/handle/helloHandle.py`: require a matching enrollment for physical evidence mode while retaining the existing synthetic Task 4 scope.
- Modify `main/tbot-server/core/voice/session_provider/google_live.py`: record privacy-safe transcript matches from in-memory text.
- Modify `main/tbot-server/core/connection.py`: finalize transcript proof together with existing provider cleanup.
- Create `main/tbot-server/scripts/google_live_deterministic_evidence.py`: canonical node manifest, pytest execution, JUnit binding, and deterministic report.
- Create `main/tbot-server/scripts/google_live_deterministic_nodeid_plugin.py`: embed each exact pytest `item.nodeid` into its JUnit testcase properties.
- Modify `main/tbot-server/scripts/google_live_robot_soak.py`: expose the existing 33-execution candidate workload as a producer, not only replay.
- Create `main/tbot-server/scripts/google_live_command_runner.py`: structured execution plus redacted authoritative `commands.jsonl`.
- Create `main/tbot-server/scripts/google_live_physical_evidence.py`: protected-stdin enrollment, operator wait, bounded log production, and existing physical audit composition without hardware control.
- Create `main/tbot-server/scripts/google_live_evidence_runner.py`: layer state machine and unified orchestration.
- Modify `main/tbot-server/scripts/analyze_google_live_log.py`: persist the exact server-issued bounded log verdict.
- Modify `main/tbot-server/scripts/physical_smoke_audit.py`: consume transcript proof markers rather than raw transcript text.
- Modify `main/tbot-server/scripts/google_live_release_gate.py`: validate deterministic supporting artifacts and command provenance.
- Modify the two Google Live runbooks after the blockers are closed.

### Firmware repository

- Modify `main/ota.h` and `main/ota.cc`: parse a safe optional `evidence_journey_id` from the OTA WebSocket object without persisting or logging it.
- Modify `main/protocols/websocket_protocol.h` and `main/protocols/websocket_protocol.cc`: hold the transient journey ID and add it only to the next WebSocket hello.
- Modify `main/application.cc`: pass the transient OTA journey ID to `WebsocketProtocol`.
- Create `tests/test_google_live_evidence_journey_contract.py`: backward-compatibility, safety, and redaction contract tests.

---

### Task 1: Authenticated Evidence Enrollment And OTA Delivery

**Files:**
- Create: `main/tbot-server/core/voice/google_live/evidence_enrollment.py`
- Create: `main/tbot-server/core/api/google_live_evidence_handler.py`
- Modify: `main/tbot-server/core/api/ota_handler.py`
- Modify: `main/tbot-server/app.py`
- Modify: `main/tbot-server/core/http_server.py`
- Modify: `main/tbot-server/core/websocket_server.py`
- Test: `main/tbot-server/tests/test_google_live_evidence_enrollment.py`
- Test: `main/tbot-server/tests/test_ota_handler_edges.py`
- Test: `main/tbot-server/tests/test_http_server.py`
- Test: `main/tbot-server/tests/test_app_auth_key.py`

- [ ] **Step 1: Write failing registry tests**

Add tests proving exact device/client binding, TTL expiry, replacement rejection, no key/MAC exposure, and generic snapshots:

```python
def test_registry_binds_one_active_journey_to_exact_peer(fake_clock):
    registry = EvidenceEnrollmentRegistry(clock=fake_clock)
    registry.register(
        device_id="28:84:85:85:1a:80",
        client_id="robot-client",
        journey_id="physical.run-1",
        transcript_plan=(TranscriptExpectation(slot=1, phase="interrupt", expected_mac="a" * 64),),
        hmac_key=b"k" * 32,
        ttl_sec=120,
    )
    assert registry.ota_journey("28:84:85:85:1a:80", "robot-client") == "physical.run-1"
    assert registry.ota_journey("other", "robot-client") is None
    assert "hmac" not in json.dumps(registry.safe_snapshot("physical.run-1")).lower()
```

- [ ] **Step 2: Run the registry tests and verify RED**

```bash
cd main/tbot-server
python3 -m pytest tests/test_google_live_evidence_enrollment.py -q
```

Expected: import failure for `EvidenceEnrollmentRegistry`.

- [ ] **Step 3: Implement the bounded registry**

Use immutable enrollment identity and keep key material private:

```python
@dataclass(frozen=True, slots=True)
class TranscriptExpectation:
    slot: int
    phase: Literal["interrupt", "lesson", "post_lesson"]
    expected_mac: str

@dataclass(slots=True)
class EvidenceEnrollment:
    device_id: str
    client_id: str
    journey_id: str
    transcript_plan: tuple[TranscriptExpectation, ...]
    hmac_key: bytearray = field(repr=False)
    expires_at: float
    connected: bool = False
    finalized: bool = False

class EvidenceEnrollmentRegistry:
    def register(
        self,
        *,
        device_id: str,
        client_id: str,
        journey_id: str,
        transcript_plan: tuple[TranscriptExpectation, ...],
        hmac_key: bytes,
        ttl_sec: int,
    ) -> EvidenceEnrollment: raise NotImplementedError
    def ota_journey(self, device_id: str, client_id: str) -> str | None: raise NotImplementedError
    def claim(self, *, device_id: str, client_id: str, journey_id: str) -> EvidenceEnrollment | None: raise NotImplementedError
    def finalize(self, journey_id: str, *, status: Literal["PASS", "FAIL"]) -> dict[str, object]: raise NotImplementedError
    def safe_snapshot(self, journey_id: str) -> dict[str, object]: raise NotImplementedError
```

Cap active enrollments at 128 and terminal tombstones at 256. Every public operation first expires stale entries, overwrites every byte of each expired/finalized `hmac_key`, and retains only a safe tombstone (`journeyId`, status, timestamps, counts) so reuse remains rejected without retaining MACs or key material. `finalize()` is idempotent for the same terminal status and rejects contradictory second finalization.

- [ ] **Step 4: Write failing API and OTA tests**

Assert:

```python
assert response.status == 401  # missing/incorrect X-Mint-Secret
assert response_json["data"] == {"registered": True, "journeyId": "physical.run-1"}
assert ota_payload["websocket"]["evidence_journey_id"] == "physical.run-1"
assert "evidence_journey_id" not in unrelated_ota_payload["websocket"]
assert secret not in caplog.text
```

- [ ] **Step 5: Implement the internal endpoint and shared registry wiring**

Add:

```text
POST /internal/devices/{deviceId}/google-live-evidence
GET  /internal/devices/{deviceId}/google-live-evidence/{journeyId}
DELETE /internal/devices/{deviceId}/google-live-evidence/{journeyId}
```

Use `LessonNudgeHandler._authorize()` semantics with `X-Mint-Secret`. The POST body is exactly:

```json
{
  "clientId": "robot-client",
  "journeyId": "physical.run-1",
  "ttlSec": 120,
  "normalizationVersion": "google-live-transcript-nfkc-casefold.v1",
  "hmacKeyBase64": "<ephemeral-32-byte-key>",
  "transcriptPlan": [
    {"slot": 1, "phase": "interrupt", "expectedMac": "<64-lowercase-hex>"}
  ]
}
```

Reject non-JSON, unknown fields, unsafe IDs, non-canonical base64, keys other than exactly 32 bytes, TTL outside `30..3600`, duplicate/non-contiguous slots, unknown phases, invalid MACs, or plans outside `1..64` slots. GET returns only the safe snapshot. DELETE requires the same device/journey binding and changes an unclaimed or active enrollment to terminal `FAIL` with `failureCode=OPERATOR_CANCELLED`, zeroizing its key; it is idempotent for the same tombstone. The raw body, headers, key, and MACs must never enter logs or status responses. Construct `evidence_registry = EvidenceEnrollmentRegistry()` once in both `_build_servers()` and `_build_servers_async()` in `app.py`; pass it as `evidence_registry=evidence_registry` to both `WebSocketServer` and `SimpleHttpServer`. `SimpleHttpServer` gives it to `OTAHandler` and `GoogleLiveEvidenceHandler`; `WebSocketServer` gives it to each `ConnectionHandler`. Tests assert object identity across both server factories.

- [ ] **Step 6: Run focused GREEN and regressions**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_ota_handler_edges.py \
  tests/test_ota_websocket_url.py \
  tests/test_http_server.py -q
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add main/tbot-server/core/voice/google_live/evidence_enrollment.py \
  main/tbot-server/core/api/google_live_evidence_handler.py \
  main/tbot-server/core/api/ota_handler.py \
  main/tbot-server/app.py \
  main/tbot-server/core/http_server.py \
  main/tbot-server/core/websocket_server.py \
  main/tbot-server/tests/test_google_live_evidence_enrollment.py \
  main/tbot-server/tests/test_ota_handler_edges.py \
  main/tbot-server/tests/test_http_server.py \
  main/tbot-server/tests/test_app_auth_key.py
git commit -m "feat: enroll bounded Google Live evidence journeys"
```

### Task 2: Firmware Optional Journey Propagation

**Repository:** `/Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/google-live-evidence-journey`

**Files:**
- Modify: `main/ota.h`
- Modify: `main/ota.cc`
- Modify: `main/protocols/websocket_protocol.h`
- Modify: `main/protocols/websocket_protocol.cc`
- Modify: `main/application.cc`
- Create: `tests/test_google_live_evidence_journey_contract.py`

- [ ] **Step 1: Write failing firmware contract tests**

```python
def test_ota_accepts_only_safe_transient_evidence_journey():
    source = read("main/ota.cc")
    assert "IsSafeEvidenceJourneyId" in source
    assert '"evidence_journey_id"' in source
    assert "SetString(\"evidence_journey_id\"" not in source

def test_websocket_hello_adds_journey_only_when_transient_value_is_valid():
    source = read("main/protocols/websocket_protocol.cc")
    assert 'cJSON_AddStringToObject(root, "evidence_journey_id"' in source
    assert "evidence_journey_id_.empty()" in source
    assert "ESP_LOG" not in evidence_value_block(source)
```

- [ ] **Step 2: Run RED**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/google-live-evidence-journey
python3 -m pytest tests/test_google_live_evidence_journey_contract.py -q
```

Expected: assertions fail because the optional field is absent.

- [ ] **Step 3: Implement safe transient propagation**

Add an ASCII validator matching the server contract (`[A-Za-z0-9._:-]{1,64}`), `Ota::GetTransientEvidenceJourneyId()`, and a third `SetTransientConfig` argument:

```cpp
void SetTransientConfig(std::string url,
                        std::string token,
                        std::string evidence_journey_id);

if (!evidence_journey_id_.empty()) {
    cJSON_AddStringToObject(root, "evidence_journey_id",
                            evidence_journey_id_.c_str());
}
```

In `Ota::CheckVersion()`, handle `evidence_journey_id` before the generic loop that persists WebSocket strings: validate it into `transient_evidence_journey_id_`, `continue`, and never call `Settings::SetString` for that key. Unknown WebSocket keys still retain their existing behavior. In both production and local-endpoint branches of `Application::InitializeProtocol()`, pass the transient value to `WebsocketProtocol` without changing URL/token selection. `GetHelloMessage()` adds the field only when non-empty, then securely clears the member after copying it into the JSON object. For normal OTA responses keep it empty; never persist or print it, and clear it on rejected/cleared OTA configuration.

- [ ] **Step 4: Run firmware regressions**

```bash
python3 -m pytest \
  tests/test_google_live_evidence_journey_contract.py \
  tests/test_tbot_connect_config.py \
  tests/test_ws_ota_token_redaction.py \
  tests/test_unclaimed_public_websocket_security_contract.py \
  tests/test_realtime_voice_state.py -q
```

Expected: pass without building or flashing firmware.

- [ ] **Step 5: Commit in the firmware worktree**

```bash
git add main/ota.h main/ota.cc \
  main/protocols/websocket_protocol.h \
  main/protocols/websocket_protocol.cc \
  main/application.cc \
  tests/test_google_live_evidence_journey_contract.py
git commit -m "feat: propagate Google Live evidence journey in hello"
```

Record the firmware commit SHA for the final cross-repository handoff.

### Task 3: Claimed Physical Scope And Persisted Bounded Log Verdict

**Files:**
- Modify: `main/tbot-server/core/api/google_live_evidence_handler.py`
- Modify: `main/tbot-server/core/handle/helloHandle.py`
- Modify: `main/tbot-server/core/connection.py`
- Modify: `main/tbot-server/scripts/analyze_google_live_log.py`
- Test: `main/tbot-server/tests/test_hello_audio_params.py`
- Test: `main/tbot-server/tests/test_connection_voice_provider_routing.py`
- Test: `main/tbot-server/tests/test_http_server.py`
- Test: `main/tbot-server/tests/test_analyze_google_live_log.py`

- [ ] **Step 1: Write failing enrollment-claim tests**

Add tests proving a production `WebSocketServer` connection claims the exact injected registry entry and fails closed for an unregistered, expired, wrong-peer, reused, or already-finalized journey. Preserve direct unit-test compatibility only when a `ConnectionHandler` was constructed without a registry; every server built by `app.py`, including the synthetic Task 4 runner, has a registry and must enroll first.

```python
assert ack["evidenceScope"]["journeyId"] == journey_id
assert registry.safe_snapshot(journey_id)["connected"] is True
assert bad_ack["evidenceScope"]["failureCode"] == "EVIDENCE_ENROLLMENT_INVALID"
assert normal_ack.get("evidenceScope") is None
```

- [ ] **Step 2: Run RED**

```bash
cd main/tbot-server
python3 -m pytest tests/test_hello_audio_params.py \
  tests/test_connection_voice_provider_routing.py \
  tests/test_http_server.py -q
```

Expected: physical enrollment assertions fail.

- [ ] **Step 3: Bind hello/finalize to the registry**

Store the injected registry on `ConnectionHandler`. `handleHelloMessage()` calls `claim(device_id=conn.device_id, client_id=single_header(conn.headers, "client-id"), journey_id=evidence_journey_id)` before producing scope. If a registry exists, a failed claim returns `evidenceScope.status=FAIL`, `failureCode=EVIDENCE_ENROLLMENT_INVALID`, clears the journey ID, and never emits a start marker. If no registry was injected, retain the existing direct-unit-test/synthetic compatibility behavior.

Extract the current `evidence_finalize` branch into `async finalize_google_live_evidence(expected_scope) -> dict[str, object]`; both the WebSocket message and authenticated HTTP control path call this same idempotent method. Add `POST /internal/devices/{deviceId}/google-live-evidence/{journeyId}/finalize`: find the current connection from the injected `ConnectionRegistry`, reserve its exact session, require its active scope to match the registry claim, and invoke finalization. This is an operator evidence-boundary action only; it sends no robot frame and performs no deploy, flash, or reset. Evidence finalization calls `registry.finalize(journey_id, status=terminal_status)` exactly once only after provider, receive-loop, WebSocket-owned task, and connection cleanup evidence is available; a failed cleanup finalizes as `FAIL` and never permits reuse.

- [ ] **Step 4: Write failing persisted analyzer tests**

```python
def test_cli_persists_exact_single_server_window(tmp_path):
    exit_code = main([str(log), "--reliability-window", "--out-json", str(out)])
    report = json.loads(out.read_text())
    assert exit_code == 0
    assert report["logWindow"] == {"windowId": "physical.run-1", "start": START, "end": END}
    assert report["evidenceScope"]["journeyId"] == "physical.run-1"
```

Also reject duplicate anchors, temporary synthetic-only anchors, and output aliases.

- [ ] **Step 5: Implement the checked-in bounded report producer**

Add `--reliability-window --journey-id JOURNEY_ID --out-json PATH`. This mode selects exactly one server-emitted start/end pair for that journey from the raw log, rejects zero/multiple pairs and foreign in-window markers, and writes the existing `google_live_log_reliability` schema with the shared atomic helper. It must never synthesize anchors. Keep `_correlate_transport_cli()` temporary-anchor compatibility limited to raw Task 4 correlation.

- [ ] **Step 6: Run GREEN and broad correlation tests**

```bash
python3 -m pytest \
  tests/test_hello_audio_params.py \
  tests/test_connection_voice_provider_routing.py \
  tests/test_voice_mode_websocket_audio_bargein.py \
  tests/test_analyze_google_live_log.py -q
```

- [ ] **Step 7: Commit**

```bash
git add main/tbot-server/core/handle/helloHandle.py \
  main/tbot-server/core/connection.py \
  main/tbot-server/core/api/google_live_evidence_handler.py \
  main/tbot-server/scripts/analyze_google_live_log.py \
  main/tbot-server/tests/test_hello_audio_params.py \
  main/tbot-server/tests/test_connection_voice_provider_routing.py \
  main/tbot-server/tests/test_http_server.py \
  main/tbot-server/tests/test_analyze_google_live_log.py
git commit -m "feat: persist bounded Google Live evidence windows"
```

### Task 4: Privacy-Safe Transcript Match Proof

**Files:**
- Modify: `main/tbot-server/core/voice/google_live/evidence_enrollment.py`
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`
- Modify: `main/tbot-server/core/connection.py`
- Modify: `main/tbot-server/scripts/physical_smoke_audit.py`
- Modify: `main/tbot-server/scripts/google_live_release_gate.py`
- Modify: `main/tbot-server/tests/test_google_live_release_gate.py`
- Test: `main/tbot-server/tests/test_google_live_evidence_enrollment.py`
- Test: `main/tbot-server/tests/test_google_live_provider_edges.py`
- Test: `main/tbot-server/tests/test_physical_smoke_audit.py`

- [ ] **Step 1: Write failing normalization and HMAC tests**

```python
def test_matcher_records_boolean_proof_without_text_or_digest():
    proof = matcher.observe("  BẮT đầu bài học!  ", phase="interrupt", observed_at=NOW)
    assert proof == {"slot": 1, "phase": "interrupt", "chars": 17, "matched": True, "observedAt": NOW}
    encoded = json.dumps(matcher.safe_report())
    assert "bắt đầu" not in encoded.lower()
    assert "expectedMac" not in encoded
    assert key.hex() not in encoded
```

Add missing, duplicate, reordered, wrong-phase, Unicode-equivalent, mismatch, cancellation, and key-zeroization cases.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_google_live_evidence_enrollment.py -q
```

- [ ] **Step 3: Implement the matcher**

Use versioned normalization and constant-time comparison:

```python
def normalize_transcript(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in normalized).split())

observed_mac = hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
matched = hmac.compare_digest(observed_mac, expected_mac)
```

The safe report contains counts, phases, slots, character counts, timestamps, booleans, and `readyToFinalize` only. `readyToFinalize` becomes true only after every required match is present in order and the provider has emitted terminal turn/output completion for the final post-lesson response; a transcript match alone cannot authorize early cleanup.

Use the schema constant `google-live-transcript-nfkc-casefold.v1`. The runner applies this exact function to protected stdin expectations, generates `secrets.token_bytes(32)`, computes each `expectedMac`, posts only MACs plus the ephemeral key to the authenticated enrollment endpoint, and overwrites its local key buffer after the enrollment is finalized. The input text, normalized text, MACs, and key are excluded from argv, provenance, timeline, reports, and status output.

- [ ] **Step 4: Hook transcript observations into the provider**

Call the registry matcher before command/lesson dispatch in both `_on_user_transcript()` and `_on_user_transcript_barge_in()`. Determine phase from existing state: `interrupt` when model output is active, `lesson` during the interactive lesson window, and `post_lesson` for the registered final slot after durable lesson release. Matching must not change routing or return values. On the existing final response-complete/output-idle event, call `registry.mark_output_idle(journey_id, response_generation=...)`; reject stale generations and set `readyToFinalize` only for the generation caused by the final matched slot.

- [ ] **Step 5: Replace raw-text physical matching with proof markers**

Add parser support for safe lines such as:

```text
Google Live evidence_transcript_match journey_id=physical.run-1 slot=1 phase=interrupt chars=17 matched=true
```

Production-candidate mode requires exactly ten matched interrupt slots plus one matched post-lesson slot. Legacy `text='...'` parsing remains only for non-production compatibility tests and cannot satisfy the production candidate profile.

- [ ] **Step 6: Run GREEN and privacy regressions**

```bash
python3 -m pytest \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_bargein.py \
  tests/test_google_live_lesson_conversation.py \
  tests/test_physical_smoke_audit.py \
  tests/test_google_live_reliability.py -q
```

- [ ] **Step 7: Commit**

```bash
git add main/tbot-server/core/voice/google_live/evidence_enrollment.py \
  main/tbot-server/core/voice/session_provider/google_live.py \
  main/tbot-server/core/connection.py \
  main/tbot-server/scripts/physical_smoke_audit.py \
  main/tbot-server/tests/test_google_live_evidence_enrollment.py \
  main/tbot-server/tests/test_google_live_provider_edges.py \
  main/tbot-server/tests/test_physical_smoke_audit.py
git commit -m "feat: prove Google Live transcript matches without text"
```

### Task 5: Production 33-Execution Candidate Soak Producer

**Files:**
- Modify: `main/tbot-server/scripts/google_live_robot_soak.py`
- Test: `main/tbot-server/tests/test_google_live_robot_soak.py`

- [ ] **Step 1: Write failing producer tests**

Expose a production journey factory and assert the already-approved Task 6 stage multiplicity. The 17 conversation executions carry the ten ordinary/latest-intent checks plus the remaining conversation checks; the ten barge-in executions each prove stop and newest-intent replacement. Do not invent an additional ten-execution stage:

```python
assert [(stage["name"], stage["executions"]) for stage in report["stages"]] == [
    ("conversation", 17),
    ("bargein", 10),
    ("quiet", 2),
    ("reopen", 1),
    ("reconnect", 1),
    ("lesson", 1),
    ("conversation_after_lesson", 1),
]
assert len(manifest["executions"]) == 33
assert manifest["cleanup"]["status"] == "PASS"
assert len(manifest["resourceSamples"]) == 1 + 33 + len(manifest["quietPadding"]) + 1
```

Add interruption failure, cancellation, replay equivalence, >10-second gap, reused window, resource under-sampling, cleanup twice, and partial-manifest atomicity tests.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_google_live_robot_soak.py -q
```

Expected: CLI producer flag and production factory are missing.

- [ ] **Step 3: Implement the production factory**

Add `build_candidate_journeys(args) -> dict[str, Callable[..., Awaitable[Mapping[str, object]]]]` returning exactly `conversation`, `bargein`, `quiet`, `reopen`, `reconnect`, `lesson`, `monitor`, and `cleanup`, the keys already consumed by `run_candidate_soak()`. Each callable registers a unique `candidate-soak.<RUN_ID>.<sequence>` journey through the authenticated control endpoint before hello, runs the existing WebSocket/audio helper, finalizes the enrolled scope, invokes `analyze_google_live_log.py --reliability-window --journey-id <id> --out-json <per-execution-path>`, and returns the existing Task 6 execution schema. Only `reconnect` may change server connection ID, and only through the existing trusted same-device transition.

- [ ] **Step 4: Add producer CLI arguments**

```text
--produce-candidate-evidence PATH
--evidence-control-url URL
--evidence-mint-secret-env TBOT_DEVICE_MINT_SECRET
--server-log PATH
```

Producer mode and replay `--journey-evidence` are mutually exclusive. Secret values are read from the named environment variable and never included in reports or argv-derived provenance.

- [ ] **Step 5: Write the manifest atomically and keep replay independent**

The producer keeps the incomplete manifest in memory and writes `journey-evidence.json` only after exactly-once cleanup. It exits zero only after reopening and structurally validating that closed file, but does not write the layer report. The unified runner then launches a second structured command using the existing replay mode, which requires equality for ordered executions, padding, cleanup, resources, duration, and identity before atomically writing `candidate-soak/report.json`. Cancellation or any failed execution writes only terminal layer state/provenance; it must not leave a partial `journey-evidence.json`.

- [ ] **Step 6: Run GREEN and Task 6 contracts**

```bash
python3 -m pytest \
  tests/test_google_live_robot_soak.py \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_reliability.py -q
```

- [ ] **Step 7: Commit**

```bash
git add main/tbot-server/scripts/google_live_robot_soak.py \
  main/tbot-server/tests/test_google_live_robot_soak.py
git commit -m "feat: produce Google Live candidate soak evidence"
```

### Task 6: Canonical Deterministic Manifest And JUnit Binding

**Files:**
- Create: `main/tbot-server/scripts/google_live_deterministic_evidence.py`
- Create: `main/tbot-server/scripts/google_live_deterministic_nodeid_plugin.py`
- Create: `main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt`
- Create: `main/tbot-server/tests/test_google_live_deterministic_evidence.py`
- Modify: `main/tbot-server/scripts/google_live_release_gate.py`
- Modify: `main/tbot-server/tests/test_google_live_release_gate.py`

- [ ] **Step 1: Check in the canonical node manifest**

Generate it once from the approved 14 files at the Task 6 candidate tip and review every exact node ID. The current pre-remediation baseline collects 690 nodes, but Tasks 1-5 intentionally add tests inside this matrix, so 690 must not be hard-coded as the final count:

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
  tests/test_connection_edges.py \
  tests/test_audio_rate_controller_edges.py \
  tests/test_receive_audio_handle.py \
  tests/test_lesson_voice_output_discipline.py \
  --collect-only -qq > /tmp/google-live-collection.txt
sed -n '/^tests\/.*::/p' /tmp/google-live-collection.txt \
  > tests/fixtures/google_live_deterministic_nodes.txt
test "$(wc -l < tests/fixtures/google_live_deterministic_nodes.txt | tr -d ' ')" -gt 690
```

Preserve pytest's canonical collection order in the file; do not sort away ordering drift. The producer later refuses any ordered collection mismatch and never regenerates the release-authoritative manifest automatically.

- [ ] **Step 2: Write failing producer tests**

```python
assert report["coverageProof"] == {
    "manifestSchema": "google-live-deterministic-nodes.v1",
    "manifestSha256": sha256(manifest),
    "manifestNodeCount": len(manifest_nodes),
    "executedNodeCount": len(manifest_nodes),
    "junitSha256": sha256(junit),
}
assert report["testVerdict"] == {"status": "PASS", "total": len(manifest_nodes), "failed": 0, "skipped": 0, "errors": 0, "failures": []}
```

Add missing node, duplicate node, extra node, JUnit name mismatch, count mismatch, skip, failure, error, tampered manifest, tampered JUnit, and dirty-worktree cases.

- [ ] **Step 3: Run RED**

```bash
python3 -m pytest tests/test_google_live_deterministic_evidence.py -q
```

- [ ] **Step 4: Implement the producer**

`google_live_deterministic_nodeid_plugin.py` appends `("google_live_nodeid", item.nodeid)` to every collected item's JUnit properties. The producer invokes pytest with `-p scripts.google_live_deterministic_nodeid_plugin`; both the producer and release gate require exactly one property per testcase and compare its ordered value with the canonical manifest.

The producer:

1. rejects staged or tracked modifications and rejects untracked files except the current runner-owned evidence root created after `init`;
2. collects the approved test files;
3. compares the exact ordered collected node sequence with the checked-in manifest;
4. runs pytest with JUnit output;
5. parses every testcase's `google_live_nodeid` property and verifies exact ordered node equality;
6. writes the report atomically with both supporting SHA-256 values.

Expose `--manifest`, `--junit-out`, `--report`, `--candidate-git-sha`, `--candidate-image-digest`, `--firmware-identity`, `--config-fingerprint`, and `--fixture-sha256`.

- [ ] **Step 5: Bind supporting files in the release gate**

Extend deterministic layer syntax with supporting artifacts:

```text
--support deterministic_manifest=$EVIDENCE_ROOT/deterministic/node-manifest.txt
--support deterministic_junit=$EVIDENCE_ROOT/deterministic/pytest.xml
```

Require both support files in `checksums.sha256`, recompute their hashes, parse them independently, and match `coverageProof`. Any support file aliasing `--out` is rejected.

- [ ] **Step 6: Run GREEN**

```bash
python3 -m pytest \
  tests/test_google_live_deterministic_evidence.py \
  tests/test_google_live_release_gate.py \
  tests/test_google_live_reliability.py -q
```

- [ ] **Step 7: Commit**

```bash
git add main/tbot-server/scripts/google_live_deterministic_evidence.py \
  main/tbot-server/scripts/google_live_deterministic_nodeid_plugin.py \
  main/tbot-server/tests/fixtures/google_live_deterministic_nodes.txt \
  main/tbot-server/tests/test_google_live_deterministic_evidence.py \
  main/tbot-server/scripts/google_live_release_gate.py \
  main/tbot-server/tests/test_google_live_release_gate.py
git commit -m "feat: bind deterministic Google Live release coverage"
```

### Task 7: Structured Command Execution And Provenance

**Files:**
- Create: `main/tbot-server/scripts/google_live_command_runner.py`
- Create: `main/tbot-server/tests/test_google_live_command_runner.py`
- Modify: `main/tbot-server/scripts/google_live_release_gate.py`
- Modify: `main/tbot-server/tests/test_google_live_release_gate.py`

- [ ] **Step 1: Write failing command-spec tests**

```python
spec = CommandSpec(
    command_id="real_api.round_trip",
    argv=(sys.executable, "scripts/google_live_smoke.py", "--round-trip"),
    secret_env=("GOOGLE_API_KEY",),
    inputs=(fixture,),
    outputs=(report,),
    expected_exit_codes=(0,),
)
result = execute_and_record(spec, env={"GOOGLE_API_KEY": "secret"}, provenance=out)
assert result.exit_code == 0
entry = json.loads(out.read_text().splitlines()[0])
assert entry["secretSources"] == ["<env:GOOGLE_API_KEY>"]
assert "secret" not in json.dumps(entry)
```

Add shell-string rejection, protected absolute paths, non-allowed env, nonzero exit policy, timeout, cancellation, output checksum, direct/symlink/hardlink alias, partial-write, duplicate command ID, and privacy-field tests.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_google_live_command_runner.py -q
```

- [ ] **Step 3: Implement structured execution**

Execute without a shell and with a dedicated process group:

```python
process = subprocess.Popen(
    list(spec.argv),
    shell=False,
    cwd=spec.cwd,
    env=child_env,
    stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    start_new_session=True,
)
stdout, stderr = process.communicate(input=stdin_bytes, timeout=spec.timeout_sec)
```

On timeout, cancellation, or `KeyboardInterrupt`, send `SIGTERM` to `os.getpgid(process.pid)`, wait the configured cleanup grace, then `SIGKILL` only that process group if it remains alive; await pipes before recording the terminal classification. Build the provenance entry from the same immutable `CommandSpec` object used for execution. Store evidence-relative input/output labels, candidate identity, UTC start/end, exit code, command-spec SHA-256, and observed artifact SHA-256 values. Under an exclusive lock, read and validate the existing JSONL, add exactly one entry, write the complete next version to a sibling temporary file, flush and `fsync`, then `os.replace` and `fsync` the parent directory. Generate `commands.txt` from that committed JSONL with the same atomic-write helper; never append in place. `stdin_source="protected_transcript_plan"` records only `<stdin:protected_transcript_plan>` and passes bytes directly to the child without storing them. Captured stdout/stderr are reduced to exit classification and never copied verbatim into evidence.

- [ ] **Step 4: Bind provenance in the release gate**

Require:

```text
--support command_provenance=$EVIDENCE_ROOT/commands.jsonl
```

Validate the exact ordered command IDs required for produced layers, unique command-spec digests, candidate identity, output/report checksum bindings, terminal exit policies, and the shared recursive privacy policy.

The required order is:

```text
deterministic.produce
real_api.round_trip
websocket.transport
websocket.log_analysis
websocket.correlation
candidate_soak.produce
candidate_soak.replay
physical.capture_and_audit
```

Enrollment occurs inside `websocket.transport`, `candidate_soak.produce`, and `physical.capture_and_audit`; their specs declare the mint-secret source, and the physical command also declares protected stdin, so there is no unrecorded standalone enrollment command. The physical command runs last because the existing production validator requires the exact candidate-soak report. It outputs both the authoritative `server-regression/report.json` and the composed `physical/report.json`, so no fabricated extra layer command is needed. `finalize` is the in-process aggregator, not an evidence-producing subprocess: excluding it avoids a self-referential `commands.jsonl`/`checksums.sha256` digest cycle. Optional diagnostic commands use a `diagnostic.` prefix and cannot satisfy a required ID.

- [ ] **Step 5: Run GREEN**

```bash
python3 -m pytest \
  tests/test_google_live_command_runner.py \
  tests/test_google_live_release_gate.py \
  tests/test_google_live_reliability.py -q
```

- [ ] **Step 6: Commit**

```bash
git add main/tbot-server/scripts/google_live_command_runner.py \
  main/tbot-server/tests/test_google_live_command_runner.py \
  main/tbot-server/scripts/google_live_release_gate.py \
  main/tbot-server/tests/test_google_live_release_gate.py
git commit -m "feat: record authoritative Google Live command provenance"
```

### Task 8: Unified Evidence Runner And Layer State Machine

**Files:**
- Create: `main/tbot-server/scripts/google_live_physical_evidence.py`
- Create: `main/tbot-server/scripts/google_live_evidence_runner.py`
- Create: `main/tbot-server/tests/test_google_live_physical_evidence.py`
- Create: `main/tbot-server/tests/test_google_live_evidence_runner.py`
- Modify: `main/tbot-server/scripts/google_live_smoke.py`
- Modify: `main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py`
- Modify: `main/tbot-server/scripts/physical_smoke_audit.py`

- [ ] **Step 1: Write failing state-machine tests**

```python
runner.start_layer("websocket_e2e")
runner.finish_layer("websocket_e2e", "FAIL", failure={"code": "LOG_WINDOW_INVALID"})
with pytest.raises(TerminalLayerStateError):
    runner.finish_layer("websocket_e2e", "PASS")
assert runner.state("physical") == "PENDING"
assert not runner.can_start("physical")
```

Add first-failure preservation, new-RUN_ID retry, cancellation cleanup, resume refusal, atomic state, evidence-root alias, and no-hardware-without-confirmation cases.

- [ ] **Step 2: Run RED**

```bash
python3 -m pytest tests/test_google_live_evidence_runner.py -q
```

- [ ] **Step 3: Implement runner initialization and identity**

`init` requires a clean worktree, immutable image digest, firmware identity, effective config JSON, fixture checksum, and a new UTC `RUN_ID` matching `^[0-9]{8}T[0-9]{6}Z$`. It captures `git rev-parse HEAD` before creating files, refuses an existing run directory, creates the exact evidence tree, and atomically writes `run-state.json` with `deterministic`, `server_regression`, `real_api`, `websocket_e2e`, `physical`, and `candidate_soak` all `PENDING`. Later source-clean checks exclude only that exact run directory; another untracked path still fails.

Each layer record is exactly:

```json
{
  "state": "PENDING",
  "startedAt": null,
  "endedAt": null,
  "firstFailure": null,
  "artifactSha256": {}
}
```

On failure, retain only a safe code from `AUTH`, `CONFIG`, `QUOTA`, `PROTOCOL`, `TIMEOUT`, `NETWORK`, `PROVIDER`, `CLEANUP`, `RESOURCE`, `IDENTITY`, `PRIVACY`, `EVIDENCE_INTEGRITY`, or `CANCELLED`, plus bounded timestamps and checksums already available. Never store exception messages. `RUNNING -> PASS|FAIL|SKIPPED` is the only terminal transition, and every state update uses temp-file, flush, `fsync`, `os.replace`, and parent-directory `fsync`.

- [ ] **Step 4: Implement layer subcommands through CommandSpec**

```text
init
deterministic
real-api
websocket
physical --operator-confirmed
candidate-soak
finalize
status
synthetic-dry-run
```

Every evidence-producing subcommand constructs immutable `CommandSpec` objects and executes them through `google_live_command_runner.py`; no layer constructs a shell string. Enforce the order `deterministic -> real_api -> websocket_e2e -> candidate_soak -> physical/server_regression`; a failed or skipped predecessor blocks dependent execution. This preserves `physical_smoke_audit.py`'s existing requirement that the exact candidate-soak report already exists.

`physical` requires `--operator-confirmed --transcript-plan-stdin` and launches `google_live_physical_evidence.py` as `physical.capture_and_audit`. That script reads the protected JSON expectations from stdin, creates the ephemeral HMAC enrollment, prints only a safe `READY journey_id=<id>` status, and polls the authenticated safe-status endpoint. Once the exact slot count, ordering, ten interrupt matches, post-lesson match, and `readyToFinalize=true` are present, it calls the authenticated finalize endpoint, which closes evidence-owned server resources without sending a robot frame. It then waits for terminal safe status or fails at the bounded timeout. After finalization it selects the exact raw server-log window into `server-regression/report.json`, invokes the existing production physical validator with that report plus the already-produced candidate-soak report, and atomically writes `physical/report.json`. It always zeroizes the local key and calls authenticated DELETE on timeout/cancellation. It never deploys, flashes, resets, reconnects, or otherwise controls the robot.

`synthetic-dry-run` uses checked-in fixtures and fake subprocesses, never credentials/network/hardware, but exercises the same state, provenance, checksum, and release-gate code.

- [ ] **Step 5: Preserve raw Task 4 semantics**

The WebSocket command writes raw transport as `SKIPPED/PENDING`, persists the exact Task 5 bounded log report, then creates the composite report only through `correlate_websocket_bargein_evidence()`. A missing correlation keeps the layer terminal `FAIL` or `SKIPPED`, never standalone `PASS`.

- [ ] **Step 6: Finalize checksums and release verdict**

`finalize` requires every layer state and every report to be `PASS`. First atomically generate `timeline.log` as a JSONL index containing only layer name, evidence-relative artifact label, journey/window IDs, and UTC bounds; never copy raw log lines. Regenerate the closed `commands.txt` projection, then write external `checksums.sha256` for the six reports, `deterministic/node-manifest.txt`, `deterministic/pytest.xml`, `commands.jsonl`, `commands.txt`, and `timeline.log`. The manifest never hashes itself or the later `release-verdict.json`.

Extend the release gate to require `command_projection` and `timeline_index` support paths, verify their checksums, reject aliases/symlinks/hardlinks, parse the timeline index, bind every row to a known bounded report, and run the shared privacy scanner over both text files plus checksum labels. After reopening and verifying every digest, call the release gate in-process and atomically write its deterministic verdict. Reject any terminal failed/skipped run and instruct the operator to use a new `RUN_ID`.

- [ ] **Step 7: Run synthetic end-to-end GREEN**

```bash
python3 -m pytest \
  tests/test_google_live_physical_evidence.py \
  tests/test_google_live_evidence_runner.py \
  tests/test_google_live_command_runner.py \
  tests/test_google_live_deterministic_evidence.py \
  tests/test_google_live_release_gate.py -q
```

The synthetic test creates six valid layer reports and obtains release `PASS`; mutating every supporting artifact in turn obtains `FAIL`.

- [ ] **Step 8: Commit**

```bash
git add main/tbot-server/scripts/google_live_evidence_runner.py \
  main/tbot-server/scripts/google_live_physical_evidence.py \
  main/tbot-server/tests/test_google_live_physical_evidence.py \
  main/tbot-server/tests/test_google_live_evidence_runner.py \
  main/tbot-server/scripts/google_live_smoke.py \
  main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py \
  main/tbot-server/scripts/physical_smoke_audit.py \
  main/tbot-server/scripts/google_live_release_gate.py \
  main/tbot-server/tests/test_google_live_release_gate.py
git commit -m "feat: orchestrate exact-candidate Google Live evidence"
```

### Task 9: Runbook Closure And Full Verification

**Files:**
- Modify: `main/tbot-server/docs/google-live-smoke.md`
- Modify: `main/tbot-server/docs/google-live-robot-validation.md`
- Verify all server and firmware files from Tasks 1-8.

- [ ] **Step 1: Replace the six blocker sections with runnable commands**

Document the unified runner commands, firmware evidence build SHA, protected environment inputs, physical operator confirmation, artifact tree, retry/new-RUN_ID policy, and external checksum semantics. Retain the statement that no automated step deploys, flashes, or resets hardware.

- [ ] **Step 2: Run server static checks**

```bash
cd main/tbot-server
python3 -m ruff check --select E9,F63,F7,F82,I \
  app.py core/websocket_server.py \
  core/voice/google_live/evidence_enrollment.py \
  core/api/google_live_evidence_handler.py \
  core/api/ota_handler.py core/http_server.py core/handle/helloHandle.py \
  core/connection.py core/voice/session_provider/google_live.py \
  scripts/google_live_deterministic_evidence.py \
  scripts/google_live_deterministic_nodeid_plugin.py \
  scripts/google_live_command_runner.py scripts/google_live_evidence_runner.py \
  scripts/google_live_robot_soak.py scripts/analyze_google_live_log.py \
  scripts/physical_smoke_audit.py scripts/google_live_release_gate.py
python3 -m py_compile \
  app.py core/websocket_server.py \
  core/voice/google_live/evidence_enrollment.py \
  core/api/google_live_evidence_handler.py \
  scripts/google_live_deterministic_evidence.py \
  scripts/google_live_deterministic_nodeid_plugin.py \
  scripts/google_live_command_runner.py scripts/google_live_evidence_runner.py
```

Expected: exit 0.

- [ ] **Step 3: Run the complete focused server matrix**

```bash
python3 -m pytest \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_ota_handler_edges.py tests/test_http_server.py tests/test_app_auth_key.py \
  tests/test_hello_audio_params.py tests/test_connection_voice_provider_routing.py \
  tests/test_google_live_provider_edges.py tests/test_google_live_bargein.py \
  tests/test_google_live_lesson_conversation.py \
  tests/test_analyze_google_live_log.py tests/test_physical_smoke_audit.py \
  tests/test_google_live_robot_soak.py \
  tests/test_google_live_deterministic_evidence.py \
  tests/test_google_live_command_runner.py \
  tests/test_google_live_evidence_runner.py \
  tests/test_google_live_release_gate.py \
  tests/test_google_live_reliability.py -q
```

Expected: all deterministic tests pass.

- [ ] **Step 4: Run classic and adjacent regressions**

```bash
python3 -m pytest \
  tests/test_connection_voice_provider_routing.py \
  tests/test_connection_edges.py \
  tests/test_lesson_voice_nonregression.py \
  tests/test_lesson_voice_output_discipline.py \
  tests/test_audio_rate_controller_cleanup.py \
  tests/test_audio_rate_controller_edges.py \
  tests/test_receive_audio_handle.py \
  tests/test_send_audio_tts_stop.py -q
```

- [ ] **Step 5: Run firmware tests without flashing**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/TBOT-Firmware/.worktrees/google-live-evidence-journey
python3 -m pytest \
  tests/test_google_live_evidence_journey_contract.py \
  tests/test_tbot_connect_config.py \
  tests/test_ws_ota_token_redaction.py \
  tests/test_unclaimed_public_websocket_security_contract.py \
  tests/test_realtime_voice_state.py -q
git diff --check
```

- [ ] **Step 6: Run full server suite and synthetic release dry run**

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-production-reliability/main/tbot-server
python3 -m pytest tests -q
python3 scripts/google_live_evidence_runner.py synthetic-dry-run \
  --out-root "$(mktemp -d)/google-live-evidence"
```

Expected: deterministic suite passes; guarded live/hardware tests keep their existing skip behavior; synthetic verdict is `PASS`. Tamper one support file and verify the release verdict becomes `FAIL`.

- [ ] **Step 7: Run documentation and diff hygiene**

```bash
git diff --check
git status --short
```

Parse every documented Bash block with `bash -n`, compile Python heredocs, and verify every referenced CLI with `--help`.

- [ ] **Step 8: Request final implementation review**

Review the complete server range from `892714f8` to final HEAD and the firmware feature branch range. Resolve every correctness, security, privacy, lifecycle, or missing-test finding, then rerun Steps 2-7.

- [ ] **Step 9: Commit final runbook or verification adjustments**

```bash
git add main/tbot-server/docs/google-live-smoke.md \
  main/tbot-server/docs/google-live-robot-validation.md
git commit -m "test: close Google Live evidence production blockers"
```

Do not create an empty commit. If final review changes implementation files, commit those fixes in the owning task with explicit paths before this documentation-only commit; never stage whole source directories.

---

## Review Workflow For Every Task

1. The implementer demonstrates RED before production changes.
2. The implementer reaches GREEN, runs focused regressions, self-reviews, and commits.
3. An independent spec reviewer checks the exact task and approved design.
4. The same implementer fixes every spec finding test-first.
5. An independent quality reviewer checks correctness, security, privacy, maintainability, and tests.
6. The same implementer fixes every quality finding test-first.
7. The controller independently reruns focused tests, static gates, compile checks, diff hygiene, and worktree status.
8. No task advances with an open finding.

## Real And Physical Execution Boundary

After Task 9 software verification and only with explicit operator readiness:

1. Build or install the separately reviewed firmware candidate using the normal operator-controlled release process.
2. Confirm its exact firmware identity; do not flash from the evidence runner.
3. Start a new UTC `RUN_ID` and execute the real API, authenticated WebSocket, physical Vietnamese, and 30-minute soak layers.
4. Preserve the first failure and start a new `RUN_ID` only after classification and remediation.
5. Release only when all six exact-candidate layers and every supporting checksum/provenance artifact pass the final gate.
