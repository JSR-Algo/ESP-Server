# Google Live Evidence Profile Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the Task 4/Task 5 contract conflict by adding authenticated immutable evidence profiles and trusted journey types, then finish the exact 33-execution candidate-soak producer without fabricating transcripts or changing normal Google Live behavior.

**Architecture:** Extend the existing server-owned enrollment registry with two safe immutable claims: `journeyType` and `proofProfile`. Physical evidence retains the strict transcript-HMAC profile, while candidate soak uses lifecycle-only finalization backed by exact scope, candidate identity, provider cleanup, trusted server markers, and canonical bounded-log analysis. Complete the intermediate Task 5 producer through follow-up commits; do not amend or describe commit `806b32b3` as production-ready by itself.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, pytest, HMAC-SHA256, atomic filesystem writes, canonical server-log parsing.

---

## Repository And Safety

Work only in:

```text
/Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-production-reliability
```

The branch already contains the intermediate candidate producer commit
`806b32b3`. Preserve it and add remediation commits. Never deploy, flash, reset,
reconnect, or control hardware while executing this plan. Production-path tests
must use fakes/mocks and must not require credentials or a live robot.

## File Structure

- Modify `main/tbot-server/core/voice/google_live/evidence_enrollment.py`: immutable journey/profile claims, safe snapshots, profile-aware readiness.
- Modify `main/tbot-server/core/api/google_live_evidence_handler.py`: authenticated optional claim parsing and strict compatibility validation.
- Modify `main/tbot-server/core/handle/helloHandle.py`: emit trusted `journeys=` from the claimed registry enrollment.
- Modify `main/tbot-server/core/connection.py`: select transcript or lifecycle finalization without weakening bounded cleanup.
- Modify `main/tbot-server/scripts/google_live_robot_soak.py`: register exact candidate claims, use real existing helpers, produce monitored padding, and publish only closed manifests.
- Modify `main/tbot-server/tests/test_google_live_evidence_enrollment.py`: registry/profile/default/privacy contracts.
- Modify `main/tbot-server/tests/test_http_server.py`: control API validation and backward compatibility.
- Modify `main/tbot-server/tests/test_hello_audio_params.py`: trusted marker emission and hello spoof rejection.
- Modify `main/tbot-server/tests/test_connection_voice_provider_routing.py`: profile-aware finalization and cleanup lifecycle.
- Modify `main/tbot-server/tests/test_analyze_google_live_log.py`: trusted type parsing and rejection cases.
- Modify `main/tbot-server/tests/test_google_live_robot_soak.py`: real factory contracts, exact 33 executions, padding, audio barge-in, and atomicity.

### Task 1: Authenticated Immutable Evidence Claims

**Files:**
- Modify: `main/tbot-server/core/voice/google_live/evidence_enrollment.py`
- Modify: `main/tbot-server/core/api/google_live_evidence_handler.py`
- Test: `main/tbot-server/tests/test_google_live_evidence_enrollment.py`
- Test: `main/tbot-server/tests/test_http_server.py`

- [ ] **Step 1: Write failing registry claim tests**

Add tests that register an explicit candidate enrollment and a legacy physical
enrollment:

```python
def test_registry_stores_immutable_candidate_lifecycle_claims():
    registry = EvidenceEnrollmentRegistry()
    registry.register(
        device_id="28:84:85:85:1a:80",
        client_id="robot-client",
        journey_id="candidate-soak.20260901T010203Z.1",
        journey_type="conversation",
        proof_profile="candidate-lifecycle",
        transcript_plan=(),
        hmac_key=bytearray(),
        ttl_sec=120,
    )
    snapshot = registry.safe_snapshot("candidate-soak.20260901T010203Z.1")
    assert snapshot["journeyType"] == "conversation"
    assert snapshot["proofProfile"] == "candidate-lifecycle"
    assert snapshot["expectedCount"] == 0
    assert snapshot["readyToFinalize"] is True


def test_registry_defaults_legacy_enrollment_to_physical_transcript():
    enrollment = register_physical_enrollment_without_new_fields()
    assert enrollment.journey_type == "physical"
    assert enrollment.proof_profile == "physical-transcript"
```

Also assert:

- candidate profiles reject non-empty transcript plans and non-empty keys;
- physical profiles still require a 32-byte key and ordered transcript plan;
- claims survive safe tombstoning;
- claims cannot be changed through candidate identity binding, claim, finalize,
  or retry;
- safe serialization contains neither MACs nor key material.

- [ ] **Step 2: Run the registry tests and verify RED**

```bash
cd main/tbot-server
python3 -m pytest tests/test_google_live_evidence_enrollment.py -q
```

Expected: failures because `register()` has no claim arguments and lifecycle
enrollments cannot omit transcript proof.

- [ ] **Step 3: Implement claim constants and compatibility validation**

In `evidence_enrollment.py`, add fixed constants and one validator:

```python
PHYSICAL_TRANSCRIPT_PROFILE = "physical-transcript"
CANDIDATE_LIFECYCLE_PROFILE = "candidate-lifecycle"

PHYSICAL_JOURNEY_TYPES = frozenset({"physical"})
CANDIDATE_JOURNEY_TYPES = frozenset(
    {
        "conversation",
        "bargein",
        "quiet",
        "quiet_padding",
        "reopen",
        "reconnect",
        "lesson",
        "conversation_after_lesson",
        "websocket",
    }
)


def validate_evidence_claims(journey_type: str, proof_profile: str) -> None:
    if proof_profile == PHYSICAL_TRANSCRIPT_PROFILE:
        if journey_type not in PHYSICAL_JOURNEY_TYPES:
            raise EnrollmentError("INVALID_EVIDENCE_CLAIMS")
        return
    if proof_profile == CANDIDATE_LIFECYCLE_PROFILE:
        if journey_type not in CANDIDATE_JOURNEY_TYPES:
            raise EnrollmentError("INVALID_EVIDENCE_CLAIMS")
        return
    raise EnrollmentError("INVALID_EVIDENCE_CLAIMS")
```

Extend `EvidenceEnrollment` and terminal tombstones with `journey_type` and
`proof_profile`. Default omitted arguments to the physical values. Require:

```python
if proof_profile == PHYSICAL_TRANSCRIPT_PROFILE:
    # Preserve all existing key, transcript ordering, and post_lesson rules.
else:
    if transcript_plan or hmac_key:
        raise EnrollmentError("INVALID_EVIDENCE_PROFILE_PAYLOAD")
```

For lifecycle profiles, the transcript safe report has zero counts,
`transcriptProofEligible=True`, and `readyToFinalize=True`; this readiness means
only that no transcript gate is required. Provider cleanup remains mandatory in
`ConnectionHandler`.

- [ ] **Step 4: Run registry GREEN**

```bash
python3 -m pytest tests/test_google_live_evidence_enrollment.py -q
```

Expected: pass, including all existing physical HMAC/causality/zeroization tests.

- [ ] **Step 5: Write failing control API tests**

Add POST tests for:

```python
candidate_body = {
    "clientId": "robot-client",
    "journeyId": "candidate-soak.20260901T010203Z.1",
    "ttlSec": 120,
    "journeyType": "conversation",
    "proofProfile": "candidate-lifecycle",
}
```

Assert explicit candidate enrollment returns the same stable response contract:

```python
assert response.status == 201
assert await response.json() == {
    "data": {
        "registered": True,
        "journeyId": "candidate-soak.20260901T010203Z.1",
    }
}
```

Assert the legacy exact physical body remains accepted. Reject only-one-claim,
unknown fields, unknown types/profiles, incompatible pairs, candidate bodies
containing transcript fields, and physical bodies omitting transcript fields.
Assert errors never echo the body, key, MAC, or secret.

- [ ] **Step 6: Run API tests and verify RED**

```bash
python3 -m pytest tests/test_http_server.py tests/test_google_live_evidence_enrollment.py -q
```

Expected: candidate body is rejected by the current exact field set.

- [ ] **Step 7: Implement strict dual-shape API parsing**

Replace one universal POST field set with two explicit shapes:

```python
_LEGACY_PHYSICAL_POST_FIELDS = {
    "clientId",
    "journeyId",
    "ttlSec",
    "normalizationVersion",
    "hmacKeyBase64",
    "transcriptPlan",
}
_EXPLICIT_PHYSICAL_POST_FIELDS = _LEGACY_PHYSICAL_POST_FIELDS | {
    "journeyType",
    "proofProfile",
}
_CANDIDATE_POST_FIELDS = {
    "clientId",
    "journeyId",
    "ttlSec",
    "journeyType",
    "proofProfile",
}
```

Parse each shape exactly. Do not accept arbitrary optional fields. Map the
legacy shape to the physical defaults. Candidate parsing supplies an empty tuple
and empty bytearray to the registry and never creates an HMAC key.

- [ ] **Step 8: Run Task 1 regression gate**

```bash
python3 -m pytest \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_http_server.py \
  tests/test_ota_handler_edges.py \
  tests/test_app_auth_key.py -q
```

Expected: all pass.

- [ ] **Step 9: Commit Task 1**

```bash
git add \
  main/tbot-server/core/voice/google_live/evidence_enrollment.py \
  main/tbot-server/core/api/google_live_evidence_handler.py \
  main/tbot-server/tests/test_google_live_evidence_enrollment.py \
  main/tbot-server/tests/test_http_server.py
git commit -m "feat: add authenticated Google Live evidence profiles"
```

### Task 2: Profile-Aware Finalization And Trusted Journey Marker

**Files:**
- Modify: `main/tbot-server/core/handle/helloHandle.py`
- Modify: `main/tbot-server/core/connection.py`
- Modify: `main/tbot-server/scripts/analyze_google_live_log.py`
- Test: `main/tbot-server/tests/test_hello_audio_params.py`
- Test: `main/tbot-server/tests/test_connection_voice_provider_routing.py`
- Test: `main/tbot-server/tests/test_analyze_google_live_log.py`

- [ ] **Step 1: Write failing hello marker tests**

Add a claimed candidate enrollment and assert the exact post-ACK marker contains:

```python
assert "journeys=conversation " in start_marker
```

Add a hello payload containing spoof fields:

```python
hello["journeyType"] = "bargein"
hello["proofProfile"] = "physical-transcript"
```

Assert the server still emits `journeys=conversation` from the registry. Add
tests for unknown/missing registry claims failing before a production marker is
emitted.

- [ ] **Step 2: Run hello tests and verify RED**

```bash
python3 -m pytest tests/test_hello_audio_params.py -q
```

Expected: the current marker contains no `journeys=` field.

- [ ] **Step 3: Emit the trusted registry journey type**

When claim succeeds, store the safe immutable claims on the connection:

```python
conn.google_live_evidence_journey_type = enrollment.journey_type
conn.google_live_evidence_proof_profile = enrollment.proof_profile
```

Emit the start marker as:

```python
"Google Live reliability_window_start window_id={} journey_id={} journeys={} "
```

Use only the claimed registry value. Do not inspect similarly named hello
fields. Keep emission post-ACK and preserve every existing scope/candidate
identity field.

- [ ] **Step 4: Run hello GREEN**

```bash
python3 -m pytest tests/test_hello_audio_params.py tests/test_analyze_google_live_log.py -q
```

Expected: claimed production windows parse to exactly one `journeyType`.

- [ ] **Step 5: Write failing lifecycle finalization tests**

Add candidate lifecycle tests proving:

```python
snapshot = registry.safe_snapshot(journey_id)
assert snapshot["proofProfile"] == "candidate-lifecycle"
assert snapshot["readyToFinalize"] is True
result = await handler.finalize_google_live_evidence(scope)
assert result["status"] == "PASS"
```

The PASS fixture must return provider cleanup status PASS, `pendingTasks=0`, and
a valid transition result. Add negative tests for provider FAIL, exception,
timeout, cancellation, pending tasks, malformed transition result, stale scope,
claim/profile mismatch, and contradictory cached finalization. Assert every
negative case leaves the reliability boundary open and emits no registry
tombstone until verified cleanup.

Re-run existing physical invalid/not-ready/fail-closed tests unchanged.

- [ ] **Step 6: Run finalization tests and verify RED**

```bash
python3 -m pytest \
  tests/test_connection_voice_provider_routing.py \
  tests/test_google_live_evidence_enrollment.py -q
```

Expected: lifecycle profile is unsupported by the current transcript-only gate.

- [ ] **Step 7: Implement profile-aware proof gating**

In `_finalize_google_live_evidence_once`, branch only the proof readiness check:

```python
proof_profile = proof_snapshot.get("proofProfile")
if proof_profile == "physical-transcript":
    # Preserve current eligibility and readyToFinalize checks exactly.
elif proof_profile == "candidate-lifecycle":
    if proof_snapshot.get("journeyType") != scope.get("journeyType"):
        return retryable_scope_failure()
else:
    return retryable_scope_failure()
```

Do not branch or weaken provider cleanup validation. Both profiles still require
the same bounded finalize task, PASS status, zero pending tasks, valid transition
ledger, marker emission ordering, registry finalization, and cached terminal
contract. Include `journeyType` and `proofProfile` in the server-issued evidence
scope so exact scope equality binds retries to the claims.

- [ ] **Step 8: Add analyzer rejection tests**

Test production windows with missing, empty, duplicate, comma-separated,
unknown, and type-conflicting `journeys=` values. Assert deterministic failures
and `journeyType=None` for invalid claims. Confirm every supported candidate type
parses exactly and existing physical/synthetic compatibility behavior remains
unchanged where explicitly allowed.

- [ ] **Step 9: Run Task 2 regression gate**

```bash
python3 -m pytest \
  tests/test_hello_audio_params.py \
  tests/test_connection_voice_provider_routing.py \
  tests/test_connection_edges.py \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_bargein.py -q
```

Expected: all pass with no change to physical transcript, AEC, barge-in, normal
turn, or bounded teardown behavior.

- [ ] **Step 10: Commit Task 2**

```bash
git add \
  main/tbot-server/core/handle/helloHandle.py \
  main/tbot-server/core/connection.py \
  main/tbot-server/scripts/analyze_google_live_log.py \
  main/tbot-server/tests/test_hello_audio_params.py \
  main/tbot-server/tests/test_connection_voice_provider_routing.py \
  main/tbot-server/tests/test_analyze_google_live_log.py
git commit -m "feat: bind Google Live lifecycle evidence to trusted stages"
```

### Task 3: Complete The Exact Candidate Producer

**Files:**
- Modify: `main/tbot-server/scripts/google_live_robot_soak.py`
- Modify: `main/tbot-server/tests/test_google_live_robot_soak.py`

- [ ] **Step 1: Replace mocked enrollment assumptions with failing production-factory tests**

For every execution factory call, capture the authenticated POST body and assert:

```python
assert body == {
    "clientId": "robot-client",
    "journeyId": expected_journey_id,
    "ttlSec": 3600,
    "journeyType": expected_stage,
    "proofProfile": "candidate-lifecycle",
}
```

Assert no `normalizationVersion`, `hmacKeyBase64`, `transcriptPlan`, transcript,
MAC, or key exists recursively in producer state or output.

Add a full fake control-plane/analyzer integration test where all 33 executions
receive a server log proof whose `journeyType` equals the stage. Assert the
ordered multiplicity is exactly:

```python
[
    ("conversation", 17),
    ("bargein", 10),
    ("quiet", 2),
    ("reopen", 1),
    ("reconnect", 1),
    ("lesson", 1),
    ("conversation_after_lesson", 1),
]
```

- [ ] **Step 2: Run producer tests and verify RED**

```bash
python3 -m pytest tests/test_google_live_robot_soak.py -q
```

Expected: intermediate producer still constructs transcript plans and invalid
profile bodies.

- [ ] **Step 3: Remove candidate transcript fabrication**

Delete `_candidate_transcript_enrollment()` and all candidate key allocation,
HMAC generation, and zeroization paths. Register the exact lifecycle body using
a fixed stage mapping:

```python
stage_type = "conversation_after_lesson" if name == "conversation_after_lesson" else name
```

Quiet padding uses `quiet_padding`. Candidate control requests continue reading
the mint secret only from the named environment source and never include the
expanded value in reports, command arguments, exceptions, or provenance.

- [ ] **Step 4: Add failing existing-helper tests for barge-in and monitor**

Instrument the candidate driver/helper boundary and assert:

- `bargein` invokes the existing WebSocket audio barge-in helper and returns raw
  transport `SKIPPED/PENDING` plus bounded log correlation;
- it does not send a text substitute;
- `monitor` enrolls and finalizes a unique `quiet_padding` lifecycle journey;
- requested padding duration is monitored instead of rejected;
- monitor invokes the analyzer and returns the canonical quiet-padding schema.

- [ ] **Step 5: Run helper tests and verify RED**

```bash
python3 -m pytest \
  tests/test_google_live_robot_soak.py -k "bargein or monitor or padding" -q
```

Expected: intermediate default paths use text barge-in and lack an enrolled
monitored padding window.

- [ ] **Step 6: Implement production helper composition**

Route `bargein` through the existing audio helper already used by
`voice_mode_websocket_audio_bargein.py`. Preserve its standalone
`SKIPPED/PENDING_BOUNDED_SERVER_LOG_VERIFICATION` result and call
`correlate_websocket_bargein_evidence()` only after the exact bounded analyzer
report exists.

Make `monitor` use the same enrollment/bind/finalize/analyze lifecycle as other
executions, with `journeyType=quiet_padding`. It must monitor for the requested
duration, retain the final trusted connection lineage, and fail on gaps greater
than `--evidence-gap-budget-sec`.

- [ ] **Step 7: Strengthen atomic manifest tests**

For each failure point below, assert `journey-evidence.json` does not exist:

```text
execution 1
execution 33
quiet padding
resource sample
scope finalize
log analyzer
cleanup
caller cancellation
```

Assert cleanup runs once when execution started, never twice, and caller
cancellation propagates after bounded cleanup. On success, assert the manifest
is closed, reopened, structurally validated, and contains:

```python
assert len(manifest["executions"]) == 33
assert manifest["cleanup"]["status"] == "PASS"
assert len(manifest["resourceSamples"]) == (
    1 + 33 + len(manifest["quietPadding"]) + 1
)
```

- [ ] **Step 8: Verify replay independence and equality**

Produce a manifest with the fake production factory, then launch replay as a
separate call. Mutate one field at a time across executions, padding, cleanup,
resources, duration, identity, journey type, window, and connection lineage.
Assert replay fails without overwriting an existing report. Assert the unmutated
manifest atomically produces `candidate-soak/report.json`.

- [ ] **Step 9: Run Task 3 GREEN**

```bash
python3 -m pytest \
  tests/test_google_live_robot_soak.py \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_reliability.py \
  tests/test_voice_mode_websocket_audio_bargein.py -q
```

Expected: all pass without network, credentials, or hardware.

- [ ] **Step 10: Commit Task 3**

```bash
git add \
  main/tbot-server/scripts/google_live_robot_soak.py \
  main/tbot-server/tests/test_google_live_robot_soak.py
git commit -m "fix: complete production Google Live candidate soak producer"
```

### Task 4: Cross-Profile Verification And Review Gate

**Files:**
- Test only; modify production files only to fix a demonstrated failing test.

- [ ] **Step 1: Run the complete remediation controller suite**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_http_server.py \
  tests/test_hello_audio_params.py \
  tests/test_connection_voice_provider_routing.py \
  tests/test_connection_edges.py \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_bargein.py \
  tests/test_google_live_lesson_conversation.py \
  tests/test_google_live_client.py \
  tests/test_google_live_audio_bridge_edges.py \
  tests/test_physical_smoke_audit.py \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_robot_soak.py \
  tests/test_google_live_reliability.py \
  tests/test_voice_mode_websocket_audio_bargein.py \
  tests/test_google_live_release_gate.py -q
```

Expected: all pass. The only tolerated output is the known dependency
deprecation warning; no task leaks, unclosed resources, or new warnings.

- [ ] **Step 2: Run privacy and static checks**

```bash
python3 -m py_compile \
  core/voice/google_live/evidence_enrollment.py \
  core/api/google_live_evidence_handler.py \
  core/handle/helloHandle.py \
  core/connection.py \
  scripts/analyze_google_live_log.py \
  scripts/google_live_robot_soak.py

python3 -m ruff check \
  core/voice/google_live/evidence_enrollment.py \
  core/api/google_live_evidence_handler.py \
  core/handle/helloHandle.py \
  core/connection.py \
  scripts/analyze_google_live_log.py \
  scripts/google_live_robot_soak.py \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_http_server.py \
  tests/test_hello_audio_params.py \
  tests/test_connection_voice_provider_routing.py \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_robot_soak.py \
  --select E9,F63,F7,F82

git diff --check a82b2d25..HEAD
git status --short
```

Expected: compile, fatal Ruff checks, and diff check pass; status is clean.
Legacy broad-style violations must not be mass-formatted as part of this task.

- [ ] **Step 3: Perform independent spec review**

Provide the reviewer the approved remediation spec and commits after
`8a8335d8`. Require explicit verification that:

- physical transcript proof is not weakened;
- candidate lifecycle cannot bypass provider cleanup or analyzer validation;
- journey type is authenticated and never client-controlled;
- the exact 33-execution workload and existing audio helper are used;
- no transcript, key, MAC, secret, synthetic anchor, or partial manifest is
  produced.

If findings exist, the same implementer fixes them test-first and the same
reviewer re-reviews until `Spec Approved`.

- [ ] **Step 4: Perform independent quality/security review**

Review the full remediation diff for races, cancellation, timeout, task
ownership, registry capacity, cache/profile confusion, parser ambiguity,
privacy leaks, output aliasing, atomicity, event-loop blocking, and test gaps.
If findings exist, the same implementer fixes them test-first and the same
reviewer re-reviews until `Approved — No findings`.

- [ ] **Step 5: Record the Task 5 completion SHA**

After both reviews and fresh controller verification pass, record the final
server SHA in the main unified runner handoff. Do not merge, deploy, or execute
physical evidence during this plan.
