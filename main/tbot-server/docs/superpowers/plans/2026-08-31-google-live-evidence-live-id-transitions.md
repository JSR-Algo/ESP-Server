# Google Live Evidence Live-ID Transitions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow validated Google Live reconnects to advance the evidence-owned Live connection ID without weakening journey, socket, peer, ordering, or cleanup ownership.

**Architecture:** The provider owns an append-only transition ledger. A reconnect starts from the current evidence Live ID, records a pending attempt, and accepts exactly one `from -> to` transition only after the reopened session is ready. Hello exposes immutable identity plus `initialLiveConnectionId`; finalization exposes `finalLiveConnectionId` and the exact ordered ledger. The analyzer replays the same state machine so historical markers remain valid under their phase and later markers must use the accepted current ID.

**Tech Stack:** Python 3.14, asyncio, unittest/pytest, JSON evidence artifacts, regex log analyzer.

---

### Task 1: Provider Transition Ledger

**Files:**
- Modify: `core/voice/session_provider/google_live.py`
- Test: `tests/test_google_live_provider_edges.py`

- [x] Add failing tests for one successful `old -> new` reconnect, two sequential transitions, failure before ready retaining the old ID, wrong/duplicate attempt rejection, and finalization returning the exact ledger.
- [x] Add provider state: immutable initial ID, current ID, ordered transitions, and pending reconnect attempts keyed by attempt.
- [x] Emit reconnect markers with exact `from_live_connection_id` and `to_live_connection_id`; accept a transition once after reopen-ready and before replay/success.
- [x] Bind subsequent scoped runtime markers and cleanup to the current accepted ID.
- [x] Run `python3 -m pytest tests/test_google_live_provider_edges.py tests/test_google_live_reconnect.py -q`.

### Task 2: Hello and Finalize Contract

**Files:**
- Modify: `core/handle/helloHandle.py`
- Modify: `core/connection.py`
- Test: `tests/test_hello_audio_params.py`
- Test: `tests/test_connection_voice_provider_routing.py`

- [x] Change hello scope to include `initialLiveConnectionId` while retaining immutable journey, connection, peer hash, and server start UTC.
- [x] Add failing finalize tests for a valid transition ledger, current-ID change without a ledger, fabricated/extra/reordered transitions, and immutable identity drift.
- [x] Validate provider finalization against the hello initial ID and exact ordered transition chain; return `finalLiveConnectionId` and `liveConnectionTransitions` in `evidence_finalized`.
- [x] Run `python3 -m pytest tests/test_hello_audio_params.py tests/test_connection_voice_provider_routing.py -q`.

### Task 3: Analyzer State Machine

**Files:**
- Modify: `scripts/analyze_google_live_log.py`
- Test: `tests/test_analyze_google_live_log.py`

- [x] Add failing log tests for real reconnect PASS, arbitrary ID drift, wrong from/to/attempt, reordered/duplicate transitions, multiple sequential reconnects, failure before ready, replay before ready, and cleanup on a non-final ID.
- [x] Replace fixed Live-ID scope matching with immutable journey/connection matching plus a current-ID transition state machine.
- [x] Validate historical markers against the ID current at their log position; accept the new ID only at a valid reopen-ready transition.
- [x] Include `initialLiveConnectionId`, `finalLiveConnectionId`, and ordered `liveConnectionTransitions` in the reliability report.
- [x] Run `python3 -m pytest tests/test_analyze_google_live_log.py -q`.

### Task 4: Task4 Artifact and Correlator

**Files:**
- Modify: `scripts/voice_mode_websocket_audio_bargein.py`
- Modify: `scripts/analyze_google_live_log.py`
- Test: `tests/test_voice_mode_websocket_audio_bargein.py`
- Test: `tests/test_analyze_google_live_log.py`

- [x] Add failing tests proving Task4 preserves the initial scope and records the finalized final ID/ledger.
- [x] Validate the finalized immutable identity and ordered transition ledger instead of exact equality with the hello scope.
- [x] Require exact Task4/report agreement for initial ID, final ID, and every transition.
- [x] Run `python3 -m pytest tests/test_voice_mode_websocket_audio_bargein.py tests/test_analyze_google_live_log.py -q`.

### Task 5: Documentation and Release Gates

**Files:**
- Modify: `docs/superpowers/specs/2026-08-31-google-live-evidence-protocol-design.md`

- [x] Document immutable fields, initial/current/final IDs, transition ordering, reconnect failure semantics, cleanup ownership, and Task4/report correlation.
- [x] Run focused, broad, Ruff, compileall, and `git diff --check` gates.
- [x] Request independent adversarial review and fix all P1/P2 findings.
- [x] Commit the completed change without deploying or starting Task6.
