# T5.4 Renderer-v5 Phase-less Step Progress Closure

## Reproduction

Renderer-v5 semantic steps normally publish `step_started` from an accepted,
step-scoped cinematic `lesson_start` ACK. Later semantic steps without an
authored cinematic phase continue directly from `_emit_step()`, so they have no
renderer start ACK and previously published no `step_started` event.

The regression models a completed first step followed by a phase-less second
step and requires one event with semantic sequence 2:

```text
test_v5_phase_less_later_step_forwards_ordered_step_started_once FAILED
assert [] == [{"type": "step_started", "sequence": 2, ...}]
```

Command:

```bash
python -m pytest -q \
  main/tbot-server/tests/test_lesson_cinematic_phase_routing.py \
  -k phase_less_later_step
```

## Fix

At renderer-v5 semantic activation, `_emit_step()` now publishes
`step_started` directly only when the selected step has no layered cinematic
phase. The event uses the runtime semantic sequence and current step identity.
The existing runtime-local started-step set preserves one event per step.

Steps with an authored phase remain on the existing ACK-backed telemetry path,
so accepted `lesson_start` frames retain renderer telemetry and do not receive a
second activation event.

## Passing Verification

```text
phase-less regression: 1 passed, 22 deselected
cinematic phase routing: 23 passed
cinematic + conversation + runtime + forwarder: 384 passed
py_compile: PASS
git diff --check: PASS
```

Commands:

```bash
python -m pytest -q \
  main/tbot-server/tests/test_lesson_cinematic_phase_routing.py \
  -k phase_less_later_step
python -m pytest -q main/tbot-server/tests/test_lesson_cinematic_phase_routing.py
python -m pytest -q \
  main/tbot-server/tests/test_lesson_cinematic_phase_routing.py \
  main/tbot-server/tests/test_lesson_conversation_integration.py \
  main/tbot-server/tests/test_lesson_runtime.py \
  main/tbot-server/tests/test_lesson_forwarder.py
python -m py_compile \
  main/tbot-server/core/lesson/runtime.py \
  main/tbot-server/tests/test_lesson_cinematic_phase_routing.py
git diff --check
```

Deployment, physical verification, merge, and push remain in the parent T5.4
Ship checklist and were not performed in this isolated implementation lane.
