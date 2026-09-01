# Google Live Candidate Semantic Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hard-coded quiet and newest-intent candidate verdicts with authenticated, privacy-safe, server-issued semantic evidence while preserving the exact 33-execution workload and normal Google Live behavior.

**Architecture:** Extend candidate-lifecycle enrollment with two exact semantic proof shapes: a two-slot HMAC intent plan for `bargein`, and an immutable observation mode for `quiet`. The provider emits safe registry-bound observations, the bounded analyzer derives stage verdicts, and the producer consumes those verdicts through protected stdin without persisting transcript or audio content.

**Tech Stack:** Python 3.11+, asyncio, aiohttp, HMAC-SHA256, existing Google Live WebSocket/audio helpers, pytest, atomic filesystem writes.

---

## Repository And Safety

Work only in:

```text
/Users/manhhodinh/Documents/TBOT/robot/esp32-server/.worktrees/google-live-production-reliability
```

Start after design commit `39d94f41`. Never use live credentials, network,
deployment, firmware flashing, hardware reset, reconnect, or robot control while
executing this plan. Tests use local fixtures and fakes only.

## File Structure

- Modify `main/tbot-server/core/voice/google_live/evidence_enrollment.py`: semantic plan types, key ownership, ordered fail-closed matching, safe snapshots.
- Modify `main/tbot-server/core/api/google_live_evidence_handler.py`: strict semantic enrollment request shapes.
- Modify `main/tbot-server/core/voice/session_provider/google_live.py`: registry-bound intent observations, generation ownership, quiet scoped counters.
- Modify `main/tbot-server/core/handle/helloHandle.py`: safe semantic scope claims and canonical marker context.
- Modify `main/tbot-server/scripts/analyze_google_live_log.py`: full-line semantic marker parsing and stage-specific verdicts.
- Modify `main/tbot-server/scripts/google_live_robot_soak.py`: protected input, local fixtures, two quiet modes, analyzer-derived counters.
- Modify `main/tbot-server/scripts/google_live_command_runner.py` later only if needed to declare protected stdin; do not implement Task 7 provenance here.
- Modify focused tests corresponding to each production file.

### Task 1: Strict Semantic Enrollment And Registry Proof

**Files:**
- Modify: `main/tbot-server/core/voice/google_live/evidence_enrollment.py`
- Modify: `main/tbot-server/core/api/google_live_evidence_handler.py`
- Test: `main/tbot-server/tests/test_google_live_evidence_enrollment.py`
- Test: `main/tbot-server/tests/test_http_server.py`

- [ ] **Step 1: Write failing semantic enrollment tests**

Add an exact candidate barge-in request:

```python
body = {
    "clientId": "robot-client",
    "journeyId": "candidate-soak.20260902T010203Z.18",
    "ttlSec": 120,
    "journeyType": "bargein",
    "proofProfile": "candidate-lifecycle",
    "semanticProof": {
        "version": "google-live-candidate-intent-nfkc-casefold.v1",
        "hmacKeyBase64": base64.b64encode(key).decode("ascii"),
        "intentPlan": [
            {"slot": 1, "role": "initial", "expectedMac": initial_mac},
            {"slot": 2, "role": "newest", "expectedMac": newest_mac},
        ],
    },
}
```

Add exact quiet requests for `mode=silence` and `mode=robot_speaking`.
Reject semantic proof on non-bargein/non-quiet stages, wrong version, wrong field
sets, missing/extra slots, reversed roles, duplicate roles, malformed MAC/key,
mixed intent/quiet fields, and semantic proof supplied from hello.

- [ ] **Step 2: Run RED**

```bash
cd main/tbot-server
python3 -m pytest \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_http_server.py -q
```

Expected: semanticProof is rejected as an unknown field.

- [ ] **Step 3: Implement semantic types and exact validation**

Add immutable types:

```python
@dataclass(frozen=True, slots=True)
class CandidateIntentExpectation:
    slot: int
    role: Literal["initial", "newest"]
    expected_mac: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CandidateQuietProof:
    mode: Literal["silence", "robot_speaking"]
```

Store one of:

```python
semantic_kind: Literal["none", "bargein-intent", "quiet"]
intent_plan: tuple[CandidateIntentExpectation, ...]
quiet_mode: str | None
semantic_hmac_key: bytearray
```

The barge-in plan is exactly two slots/roles. Quiet has no key or plan.
Other lifecycle stages have semantic kind `none`. Every parsing or validation
failure zeroizes mutable key material, including malformed container/member
types and unexpected iterator failures.

- [ ] **Step 4: Implement safe snapshots and tombstones**

Expose only:

```python
{
    "semanticProofKind": "bargein-intent",
    "semanticExpectedCount": 2,
    "semanticObservedCount": 0,
    "semanticMatchCount": 0,
    "semanticMismatchCount": 0,
    "semanticOrderingValid": True,
    "semanticEligible": True,
    "latestIntentMatched": False,
}
```

Quiet snapshots expose only `semanticProofKind=quiet` and `quietMode`. Never
expose expected/observed MAC, key, text, audio paths, normalized text, or raw
exceptions. Preserve semantic kind/mode in tombstones but zeroize the key.

- [ ] **Step 5: Add matcher RED/GREEN tests**

Test versioned normalization and constant-time HMAC matching for initial/newest.
Missing, duplicate, reorder, mismatch, wrong role, extra observation, and
observation after failure are sticky fail-closed. Successful newest match alone
does not authorize finalization or `latestIntentSuccesses`; generation ownership
is added in Task 2.

- [ ] **Step 6: Run Task 1 GREEN**

```bash
python3 -m pytest \
  tests/test_google_live_evidence_enrollment.py \
  tests/test_http_server.py \
  tests/test_ota_handler_edges.py -q
```

Expected: all pass with existing physical transcript and lifecycle profiles
unchanged.

- [ ] **Step 7: Commit Task 1**

```bash
git add \
  main/tbot-server/core/voice/google_live/evidence_enrollment.py \
  main/tbot-server/core/api/google_live_evidence_handler.py \
  main/tbot-server/tests/test_google_live_evidence_enrollment.py \
  main/tbot-server/tests/test_http_server.py
git commit -m "feat: enroll privacy-safe candidate semantic proof"
```

### Task 2: Provider Semantic Ownership And Safe Markers

**Files:**
- Modify: `main/tbot-server/core/voice/session_provider/google_live.py`
- Modify: `main/tbot-server/core/handle/helloHandle.py`
- Modify: `main/tbot-server/core/connection.py`
- Test: `main/tbot-server/tests/test_google_live_provider_edges.py`
- Test: `main/tbot-server/tests/test_google_live_bargein.py`
- Test: `main/tbot-server/tests/test_hello_audio_params.py`
- Test: `main/tbot-server/tests/test_connection_voice_provider_routing.py`

- [ ] **Step 1: Write failing intent ownership tests**

Test this event sequence through provider callbacks:

```text
initial transcript match
old response audio_start generation=G1
newest transcript match while G1 active
interrupt old response G1
replacement audio_start generation=G2
replacement audio_end generation=G2
```

Assert registry semantic evidence is ready only when newest match owns the exact
interrupt/replacement chain. Add REDs for newest before old output, newest after
old output ends, stale G1 audio after replacement, G2 without newest match,
wrong response generation, missing replacement, and duplicate callback delivery.

- [ ] **Step 2: Run provider RED**

```bash
python3 -m pytest \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_bargein.py -q
```

Expected: no candidate semantic observation/ownership API exists.

- [ ] **Step 3: Observe semantic transcripts before dispatch**

Reuse the existing per-event transcript dedup token. For registry-bound
`bargein-intent`, call the semantic matcher exactly once before routing. Bind the
newest successful slot to the currently active old response generation. Do not
change callback return values, command/lesson routing, AEC behavior, or normal
turn generation.

- [ ] **Step 4: Bind replacement ownership**

On the existing interruption and response-start/end events, record safe
generation ownership in the registry. Require:

```python
old_response_generation == newest_observed_during_generation
replacement_generation != old_response_generation
replacement_started is True
replacement_completed is True
stale_old_audio_count == 0
```

Semantic failure is sticky. Cleanup/finalization remains governed by the common
candidate lifecycle contract and cannot PASS early from semantic proof alone.

- [ ] **Step 5: Emit canonical safe markers**

Add exact provider log records:

```text
Google Live evidence_candidate_intent_match journey_id=<id> slot=1 role=initial chars=<n> matched=true response_generation=<n>
Google Live evidence_candidate_intent_match journey_id=<id> slot=2 role=newest chars=<n> matched=true response_generation=<n>
Google Live evidence_candidate_intent_replacement journey_id=<id> old_generation=<n> new_generation=<n> old_stopped=true replacement_started=true replacement_completed=true stale_old_audio=0
```

Use fixed field order and values from registry/provider state only. Never log
text, audio, MAC, key, prompt, or raw exception.

- [ ] **Step 6: Add quiet scoped markers/counters**

At claimed scope start, bind the registry quiet mode. Emit safe counters at
finalization from measured provider state:

```text
Google Live evidence_candidate_quiet journey_id=<id> mode=silence duration_ms=<n> user_turns=0 response_starts=0 response_ends=0 interrupts=0 replacements=0 reconnects=0 fallbacks=0 stale_audio=0
```

For `robot_speaking`, require `response_starts=1` and `response_ends=1`; all
other listed counters remain zero. Add an explicit safe fallback marker in
authenticated scopes so the analyzer does not infer fallback absence solely
from client silence.

- [ ] **Step 7: Run Task 2 GREEN and regressions**

```bash
python3 -m pytest \
  tests/test_google_live_provider_edges.py \
  tests/test_google_live_bargein.py \
  tests/test_hello_audio_params.py \
  tests/test_connection_voice_provider_routing.py \
  tests/test_google_live_client.py \
  tests/test_google_live_audio_bridge_edges.py -q
```

Expected: all pass; physical transcript, normal turns, AEC and cleanup lifecycle
remain unchanged.

- [ ] **Step 8: Commit Task 2**

```bash
git add \
  main/tbot-server/core/voice/session_provider/google_live.py \
  main/tbot-server/core/handle/helloHandle.py \
  main/tbot-server/core/connection.py \
  main/tbot-server/tests/test_google_live_provider_edges.py \
  main/tbot-server/tests/test_google_live_bargein.py \
  main/tbot-server/tests/test_hello_audio_params.py \
  main/tbot-server/tests/test_connection_voice_provider_routing.py
git commit -m "feat: bind candidate semantic proof to Live responses"
```

### Task 3: Canonical Analyzer Semantic Verdicts

**Files:**
- Modify: `main/tbot-server/scripts/analyze_google_live_log.py`
- Test: `main/tbot-server/tests/test_analyze_google_live_log.py`

- [ ] **Step 1: Write failing canonical parser tests**

Add valid full file-log records and reject:

- warning/error prefix injection;
- suffix or extra fields;
- duplicate slot/role/mode records;
- foreign journey/window/provider module;
- malformed booleans/counts/generations;
- semantic markers outside the exact bounded scope.

- [ ] **Step 2: Run analyzer RED**

```bash
python3 -m pytest tests/test_analyze_google_live_log.py -q
```

Expected: new marker families are ignored or absent.

- [ ] **Step 3: Parse exact semantic marker families**

Use anchored/full-match regexes for canonical `tmp/server.log` INFO records.
Reject any malformed scoped semantic-family record rather than ignoring it.

- [ ] **Step 4: Derive barge-in verdict**

For `journeyType=bargein`, require exact slots `initial,newest`, both matched,
valid order, newest observed during active old generation, exact owned
replacement chain, no stale old audio, and the existing transport/lifecycle
evidence. Output:

```python
candidate_semantic = {
    "status": "PASS",
    "kind": "bargein-intent",
    "initialSlotMatched": True,
    "newestSlotMatched": True,
    "orderingValid": True,
    "latestIntentMatched": True,
    "replacementOwnedByNewestGeneration": True,
}
```

- [ ] **Step 5: Derive quiet verdicts**

For `quiet` require the authenticated mode and exact counters:

```python
silence = {
    "userTurns": 0,
    "responseStarts": 0,
    "responseEnds": 0,
    "interrupts": 0,
    "replacements": 0,
    "reconnects": 0,
    "fallbacks": 0,
    "staleAudio": 0,
}

robot_speaking = {**silence, "responseStarts": 1, "responseEnds": 1}
```

Require positive duration, exact scope/profile/candidate/lineage, and verified
cleanup. Any extra scoped response, interrupt, replacement, reconnect, fallback,
or stale audio fails.

- [ ] **Step 6: Run analyzer GREEN**

```bash
python3 -m pytest \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_reliability.py -q
```

- [ ] **Step 7: Commit Task 3**

```bash
git add \
  main/tbot-server/scripts/analyze_google_live_log.py \
  main/tbot-server/tests/test_analyze_google_live_log.py
git commit -m "feat: analyze candidate semantic evidence"
```

### Task 4: Protected-Input Producer Integration

**Files:**
- Modify: `main/tbot-server/scripts/google_live_robot_soak.py`
- Modify: `main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py`
- Test: `main/tbot-server/tests/test_google_live_robot_soak.py`
- Test: `main/tbot-server/tests/test_voice_mode_websocket_audio_bargein.py`

- [ ] **Step 1: Write failing protected-input tests**

Parse exactly one protected JSON document from stdin:

```python
{
    "bargein": {
        "initialAudioPath": initial_wav,
        "initialExpected": initial_text,
        "newestAudioPath": newest_wav,
        "newestExpected": newest_text,
    },
    "robotSpeaking": {"triggerAudioPath": trigger_wav},
}
```

Reject argv text/audio expectations, unknown fields, symlinks, non-regular
files, output aliases, unsupported WAV/Opus shape, duplicate fixture inode, and
missing stdin. Assert protected values never appear in args, logs, exceptions,
manifest, report, or test snapshots.

- [ ] **Step 2: Run producer RED**

```bash
python3 -m pytest \
  tests/test_google_live_robot_soak.py \
  tests/test_voice_mode_websocket_audio_bargein.py -q
```

- [ ] **Step 3: Implement protected input and enrollment MACs**

Read stdin once, validate files through descriptor-chain `openat/O_NOFOLLOW`,
and retain stable evidence-relative labels only. For every barge-in execution,
create a fresh key and compute expected MACs in memory. Post the semantic plan,
then overwrite the key and drop expected text references after enrollment.

- [ ] **Step 4: Replace hard-coded barge-in success**

Send the initial fixture, wait for active output, authorize audio with
`listen/start`, then send the newest fixture through the existing audio helper.
Set:

```python
latest_intent_successes = 1 if (
    log_evidence["candidateSemanticEvidence"]["status"] == "PASS"
    and log_evidence["candidateSemanticEvidence"]["latestIntentMatched"] is True
) else 0
```

Do not accept a client constant, transcript text, or raw transport result.

- [ ] **Step 5: Implement two exact quiet executions**

Map the first quiet execution to `silence` and the second to
`robot_speaking`. Silence sends no prompt/audio for the requested duration.
Robot-speaking sends the approved trigger fixture through the normal path and
observes AEC/server-side VAD behavior until terminal response. Derive
`falseInterrupts` only from bound analyzer semantic evidence.

- [ ] **Step 6: Add semantic failure/atomicity tests**

Inject stale newest intent, unmatched newest transcript, missing replacement,
quiet response during silence, AEC false interrupt, fallback, reconnect,
malformed semantic report, foreign scope/identity/window, timeout, cancellation,
and cleanup failure. Assert no partial manifest and exactly-once bounded cleanup.

- [ ] **Step 7: Verify exact workload/replay**

Assert exact 33 multiplicity remains unchanged and the two quiet entries contain
distinct safe modes. Replay must require exact semantic evidence equality and
reject mutations without overwriting a report.

- [ ] **Step 8: Run Task 4 GREEN**

```bash
python3 -m pytest \
  tests/test_google_live_robot_soak.py \
  tests/test_voice_mode_websocket_audio_bargein.py \
  tests/test_analyze_google_live_log.py \
  tests/test_google_live_reliability.py -q
```

- [ ] **Step 9: Commit Task 4**

```bash
git add \
  main/tbot-server/scripts/google_live_robot_soak.py \
  main/tbot-server/scripts/voice_mode_websocket_audio_bargein.py \
  main/tbot-server/tests/test_google_live_robot_soak.py \
  main/tbot-server/tests/test_voice_mode_websocket_audio_bargein.py
git commit -m "fix: derive candidate semantic verdicts from server proof"
```

### Task 5: Cross-Profile Verification And Reviews

**Files:**
- Test only; production edits require a demonstrated failing regression.

- [ ] **Step 1: Run the complete semantic/cross-profile gate**

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

Expected: all pass with only the known third-party deprecation warning.

- [ ] **Step 2: Run static/privacy/diff checks**

```bash
python3 -m py_compile \
  core/voice/google_live/evidence_enrollment.py \
  core/api/google_live_evidence_handler.py \
  core/voice/session_provider/google_live.py \
  core/handle/helloHandle.py \
  core/connection.py \
  scripts/analyze_google_live_log.py \
  scripts/google_live_robot_soak.py \
  scripts/voice_mode_websocket_audio_bargein.py

python3 -m ruff check \
  core/voice/google_live/evidence_enrollment.py \
  core/api/google_live_evidence_handler.py \
  core/voice/session_provider/google_live.py \
  core/handle/helloHandle.py \
  core/connection.py \
  scripts/analyze_google_live_log.py \
  scripts/google_live_robot_soak.py \
  scripts/voice_mode_websocket_audio_bargein.py \
  --select E9,F63,F7,F82

git diff --check 39d94f41..HEAD
git status --short
```

- [ ] **Step 3: Independent spec review**

Require explicit confirmation that quiet and newest-intent verdicts are derived
only from exact authenticated server evidence, no transcript/audio/key is
persisted, physical transcript remains strict, and exact 33/replay/cleanup
contracts remain intact. Same implementer fixes findings test-first; re-review
until `Spec Approved`.

- [ ] **Step 4: Independent quality/security review**

Review HMAC/key ownership, semantic generation races, callback dedup, marker
spoofing, quiet absence-proof completeness, AEC behavior, protected input path
safety, filesystem races, cancellation, cleanup, privacy scans, and false-PASS
tests. Same implementer fixes findings test-first; re-review until
`Approved — No findings`.

- [ ] **Step 5: Record final Task 5 SHA**

After fresh controller verification and both reviews pass, record the final
server SHA for the unified evidence runner handoff. Do not execute live API or
physical evidence during this plan.
