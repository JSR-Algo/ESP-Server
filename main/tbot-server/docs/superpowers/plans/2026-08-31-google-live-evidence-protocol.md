# Google Live Evidence Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind Task 4 transport evidence to server-issued connection/UTC cleanup anchors and prove model-output ownership without requiring stale-drop events.

**Architecture:** Add an evidence-only hello/finalize protocol at the connection boundary, scoped provider cleanup, per-chunk ownership markers in the audio bridge, and exact scope/window validation in the analyzer/correlator. Existing non-evidence traffic remains unchanged.

**Tech Stack:** Python, asyncio, websockets, unittest/pytest, structured Loguru markers.

---

### Task 1: Public fail-closed correlation and optional stale evidence

**Files:**
- Modify: `scripts/analyze_google_live_log.py`
- Test: `tests/test_analyze_google_live_log.py`

- [ ] Add direct tests passing list, null, string, and number values to `correlate_websocket_bargein_evidence()` and assert a redacted `FAIL` verdict.
- [ ] Add a complete scoped lifecycle without `evidence_stale_model_drop` and assert analyzer/correlation `PASS`.
- [ ] Remove stale-drop from the mandatory phase transition while retaining stale-forward failure detection.
- [ ] Run `python3 -m pytest tests/test_analyze_google_live_log.py -q`.

### Task 2: Observable response ownership

**Files:**
- Modify: `core/voice/google_live/audio_bridge.py`
- Modify: `core/voice/session_provider/google_live.py`
- Test: `tests/test_google_live_audio_bridge_edges.py`
- Test: `tests/test_google_live_provider_edges.py`
- Test: `tests/test_analyze_google_live_log.py`

- [ ] Add a bridge test proving every forwarded evidence chunk logs its response owner, while non-evidence mode keeps first-chunk/no scoped logging behavior.
- [ ] Add a provider test proving the stale guard emits `evidence_stale_model_drop` only when an evidence journey is active.
- [ ] Add an analyzer test where a late cancelled-response chunk follows the first replacement chunk and assert `STALE_AUDIO_AFTER_REPLACEMENT`.
- [ ] Implement the minimum gated marker changes and run the three focused suites.

### Task 3: Server-issued hello evidence scope

**Files:**
- Modify: `core/handle/helloHandle.py`
- Modify: `core/connection.py`
- Test: `tests/test_hello_audio_params.py`
- Test: `tests/test_voice_mode_websocket_audio_bargein.py`

- [ ] Add hello tests for opaque server connection ID, safe peer hash, UTC start anchor, and absence of raw device/client values.
- [ ] Add Task 4 tests rejecting missing/malformed scope and storing only server-issued values.
- [ ] Implement evidence-only ack fields and exact validation.
- [ ] Run both focused suites.

### Task 4: Evidence finalization cleanup ack

**Files:**
- Modify: `core/connection.py`
- Modify: `core/voice/session_provider/google_live.py`
- Modify: `scripts/voice_mode_websocket_audio_bargein.py`
- Test: `tests/test_google_live_provider_edges.py`
- Test: `tests/test_voice_mode_websocket_audio_bargein.py`

- [ ] Add provider tests for idempotent cleanup, scoped close marker, and zero pending resources.
- [ ] Add Task 4 tests for success, timeout, failed ack, changed scope, ambiguous Live identity, clock skew, and cleanup-bounded UTC end.
- [ ] Route `evidence_finalize` before normal handlers, await provider cleanup with a bounded timeout, and send success/failure ack while the socket remains open.
- [ ] Update Task 4 to send finalize and fail closed unless the exact success ack arrives.
- [ ] Run both focused suites.

### Task 5: Exact scope correlation and release gates

**Files:**
- Modify: `scripts/analyze_google_live_log.py`
- Modify: `tests/test_analyze_google_live_log.py`

- [ ] Add cross-connection same-journey, wrong Live ID, peer-hash mismatch, timezone, skew, and cleanup-boundary tests.
- [ ] Require exact scope in the transport contract, log window, and matching correlation record.
- [ ] Parse timezone-aware UTC anchors and reject naive/mismatched windows.
- [ ] Run the analyzer suite.

### Task 6: Production verification

**Files:**
- Verify only; no Task 6 product work, hardware, deployment, or reset.

- [ ] Run the broad provider/bridge/connection/Task 4/analyzer/Task 1 test matrix.
- [ ] Run Ruff E/F checks, compileall, and `git diff --check`.
- [ ] Request independent code review, fix all findings, and rerun the gates.
- [ ] Commit the completed Task 5 reliability work.
