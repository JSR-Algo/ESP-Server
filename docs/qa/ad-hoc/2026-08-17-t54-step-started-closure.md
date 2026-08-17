# T5.4 Renderer-v5 Step Progress Closure — 2026-08-17

## Reproduction

The physical renderer-v5 run rendered all nine steps but emitted zero
`step_started` events. `_forward_lesson_step_ack_telemetry()` returned unless the
accepted frame type was legacy `lesson_step`; renderer-v5 uses step-scoped
`lesson_prepare` / `lesson_start` pairs.

The new regression test first failed with:

```text
test_v5_accepted_lesson_start_ack_forwards_step_started_once FAILED
assert [] == [{"type": "step_started", ...}]
```

Command:

```bash
python3 -m pytest main/tbot-server/tests/test_lesson_cinematic_phase_routing.py -q \
  -k 'v5_accepted_lesson_start_ack_forwards_step_started_once'
```

## Fix

The existing post-validation ACK telemetry boundary now accepts a renderer-v5
`lesson_start` only when it is step-scoped and carries an authored
`cinematicPhase.command=start`. The event includes the current safe `stepType` and
the existing bounded renderer telemetry. Stale, mismatched, rejected, and duplicate
ACKs remain behind `_cinematic_ack_matches()` and `_accept_inbound()`.

## Passing Verification

```text
test_lesson_cinematic_phase_routing.py: 20 passed
test_lesson_runtime.py: 274 passed, 1 pre-existing deprecation warning
```

Commands:

```bash
python3 -m pytest main/tbot-server/tests/test_lesson_cinematic_phase_routing.py -q
python3 -m pytest main/tbot-server/tests/test_lesson_runtime.py -q
```

Production deployment and physical Parent Progress evidence are recorded in the
T5.4 final closeout report after both cross-repo fixes merge.
