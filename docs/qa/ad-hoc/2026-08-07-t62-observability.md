# T6.2 — Observability: lesson log correlation + reconnect-storm counters (ESP)

**Date:** 2026-08-07
**Branch:** `lesson-prod/t62-observability`
**Repos:** `robot/esp32-server` (this file) + `tbot-backend`
([backend evidence](../../../../../tbot-backend/docs/qa/ad-hoc/2026-08-07-t62-observability.md))
**Gate:** `t62-esp` VERIFIED (RED@base rc=1, GREEN@tip rc=0) — [GATE_LOG.md](../../../../../lesson-prod/GATE_LOG.md)

## Repro (RED @ base `9d447434`)

`bash lesson-prod/repros/t62-esp.sh` — 6 failed / 1 passed / 11 errors. The failures are
real assertions naming the offending lines, e.g.:

```
AssertionError: connection.py lesson log lines without correlation:
  [(885,  'self.logger.bind(tag=TAG).warning( "lesson Live context deactivation failed error={}"…'),
   (907,  '…warning(f"send lesson tts stop failed: {e}")'),
   (939,  '…warning(f"send lesson emotion failed: {e}")'),
   (1413, '…info("lesson_tts_initialized")'),
   (1416, '…warning(f"lesson_tts_initialize_failed: {type(exc).__name__}")'),
   (2344, '…error("LESSON_RUNTIME_ENABLED auto-disabled (voice-latency-during-preload alarm)")'),
   (2499, '…warning(f"lesson pull-on-connect failed: {type(exc).__name__}: {exc}")'),
   (2975, '…warning("lesson_peer_silent device_id={} session_id={} …")')]

FAILED test_runtime_and_forwarder_delegate_to_the_shared_helper
FAILED test_lesson_log_lines_carry_correlation[core/lesson/runtime.py]
FAILED test_lesson_log_lines_carry_correlation[core/handle/textHandler/lessonMessageHandler.py]
FAILED test_connection_lesson_lifecycle_logs_carry_correlation
FAILED test_supersede_and_peer_silence_paths_increment_their_counters
FAILED test_esp_metrics_endpoint_exposes_the_counters
```

`test_lesson_log_lines_carry_correlation[core/lesson/forwarder.py]` is the one that passed
at base — the forwarder already had its own private helper.

### Grep audit

Across `core/`, **89 direct lesson log statements carried neither `assignment_id` nor
`session_id`**; exactly one (`lesson_peer_silent`) carried a session id. The correlated
lines all went through one of **three separate implementations of the same suffix**, each
reading ids from a different shape:

- `LessonRuntime._with_log_context` (attributes on the runtime)
- the module-level `_log` inside `maybe_start_lesson_on_connect` (a dict + `conn`)
- `forwarder._with_lesson_log_context` (a wire batch dict)

Triplicated correlation logic is how the ids drift apart; nothing outside those three got
any correlation at all.

## Fix

| File | Change |
| --- | --- |
| `core/lesson/log_context.py` | **New.** The single implementation. Resolves ids from a wire batch dict *or* a `conn`/runtime object, and follows `conn → conn.lesson_runtime` for the assignment the connection itself does not hold. Never raises (a log helper must not be able to break a lesson). Skips any id the message already spells out. |
| `core/lesson/runtime.py` | Both local helpers delegate to it; the previously bare SD-GC fallback line now carries context. |
| `core/lesson/forwarder.py` | `_with_lesson_log_context` delegates to it. |
| `core/handle/textHandler/lessonMessageHandler.py` | Both log lines correlated. |
| `core/connection.py` | 11 lesson lifecycle log lines correlated. |
| `core/lesson/runtime_counters.py` | **New.** Process-level monotonic counters. |
| `core/websocket_server.py` | Count a superseded device connection. |
| `core/connection.py` | Count a peer-silence socket close, on the `peer_silent` branch. |
| `core/http_server.py` | Surface both through the existing `GET /internal/lesson-runtime/metrics`, reported even at zero so an absent key and a quiet counter do not look the same. |

This closes the T2.4 finding routed to T6.2: a superseded connection and a peer-silence
close — the two signals a reconnect storm shows up in — were log lines only, so a flapping
robot had nothing countable to alert on.

## Scope decisions

Two classes of lesson log line are **deliberately not** given session correlation, because
they have no session by construction:

- SD-pack fanout / GC / retry workers (`sd_pack_fanout.py`, `sd_pack_pending_store.py`,
  `sd_pack_retry_worker.py`) are device + cache-key scoped background jobs;
- `lesson_nudge_handler` identity resolution runs before an assignment exists.

`lesson_log_context(device_id=…)` gives these a `device_id` fallback, which never displaces
a known session. The literal checklist wording ("every ESP lesson log line carries
assignmentId+sessionId") is therefore not satisfiable as stated; the standard applied is
**every lesson log line emitted while a lesson session exists carries both**.

The **Google Live voice provider is untouched** (44 lesson-adjacent lines in
`core/voice/session_provider/google_live.py`, 6 in `audio_bridge.py`). Per ground rule 1 it
is a separate flow from `classic_pipeline`, and its lines are voice-interaction diagnostics
rather than lesson lifecycle. Routed to the findings log instead of edited here.

## Passing re-run (GREEN @ tip)

```
$ python3 -m pytest tests/test_lesson_observability_t62.py -q
18 passed in 0.02s

$ python3 -m pytest tests/ -q
13 failed, 3731 passed, 9 skipped, 6 warnings in 115.82s
```

The 13 failures are the **pre-existing baseline** — the failure set is byte-identical to
`main` (`diff` of the sorted `FAILED` lines is empty; main reports
`13 failed, 3715 passed`). This branch adds 16 passing tests and no new failures.

```
$ bash lesson-prod/repros/t62-esp.sh
18 passed
REPRO PASS
```

## Deep-dive checklist (ESP-owned box)

| Box | Verdict |
| --- | --- |
| Every ESP lesson log line carries assignmentId+sessionId | **PARTIAL — PASS on the lesson runtime surface** (`core/lesson/`, the lesson message handler, and the lesson lifecycle lines in `core/connection.py`), enforced by a grep-audit regression test. Session-less background workers carry `device_id`; the Google Live voice path is out of scope by ground rule 1. |


---

## Follow-up pass — F-T62-02 closed (gate `t62b-esp` VERIFIED, main `8f3f7eaa`)

The first pass deliberately left the Google Live voice path alone under ground rule 1. The
follow-up closed it: those are the lines a live-run investigation reads first — **F-T54-02 was
diagnosed from exactly this family** — so joining them to a session is worth more than the
isolation was buying.

- **44** lesson log statements in `core/voice/session_provider/google_live.py` and **5** in
  `core/voice/google_live/audio_bridge.py` had their MESSAGE argument wrapped in
  `with_lesson_log_context(..., self.conn)`.
- Applied via an **AST pass**, not a regex: only `Call` nodes whose own source mentions a lesson
  and whose first argument is a string literal are touched, so loguru's brace-formatting
  arguments are never disturbed (the helper appends plain `k=v` text containing no braces).
- **Message text only** — no voice behaviour, ordering or control flow changed.

### What the change caught

Two Google Live tests pinned a log message by equality
(`test_lesson_child_response_audio_forward_logs_diagnostic`,
`test_blocked_output_lesson_child_audio_logs_aec_marker`) and went to zero matches once the
correlation suffix was appended — i.e. the correlation was working. They now match the stable
prefix instead of the whole message.

### The audit itself was wrong

Adding the two files to `CORRELATED_FILES` surfaced four false positives in `audio_bridge.py`
(`"Google Live tool_call dropped (no handler)"` and friends). The audit read a fixed **8-line
window** after each logger call, which spills into the *following* code — so it judged a call by
its neighbourhood rather than its own arguments, and would have pushed correlation onto log lines
that have nothing to do with a lesson. It now reads each statement to its **closing paren**.

### Re-run

```
$ python3 -m pytest tests/test_lesson_observability_t62.py -q
20 passed

$ python3 -m pytest tests/test_google_live_tool_calls.py -q
76 passed

$ python3 -m pytest tests/ -q
14 failed, 3734 passed, 7 skipped
```

The 14 failures are pre-existing: verified identical at `adaac386`, the merge's pre-merge parent
(`test_nginx_generation_cache_runtime.py::test_generation_cache_collapses_cloudflared_burst_and_preserves_http_semantics`
fails 3/3 in isolation there, and this merge touches no nginx or cache file).

### Deep-dive box — now closed

| Box | Verdict |
| --- | --- |
| Every ESP lesson log line carries assignmentId+sessionId | **PASS** across the lesson runtime surface **and** the Google Live voice path, enforced by the grep audit. Session-less background workers (SD-pack fanout/GC/retry, nudge identity resolution) carry `device_id` — no session exists there by construction. |
