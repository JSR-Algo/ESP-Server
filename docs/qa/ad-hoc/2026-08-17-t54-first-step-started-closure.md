# T5.4 Renderer-v5 First-Step Progress Closure — 2026-08-17

## Reproduction

The first production run after the original step-progress deployment emitted no
`step_started` for `s1`. The initial renderer-v5 `lesson_start` frame is intentionally
unscoped (`stepId=null`); its accepted ACK enters the first semantic step, but the
telemetry helper only accepted start frames that already carried a step id.

The same run also emitted repeated `step_started` events for interactive steps when
the renderer accepted later `listen` and `thinking` phase starts. The contract needs
one ordered progress event per semantic step, not one per cinematic effect.

The two regressions failed before the fix with:

```text
test_v5_initial_lesson_start_ack_forwards_first_step_started: assert [] == [{...s1...}]
test_v5_accepted_lesson_start_ack_forwards_step_started_once: left contains one more step_started
```

Physical repro: assignment `e9979849-b341-4364-bb96-59239058bbf1`, session
`ec2b6412-3441-4d67-b638-4ffddb48a499`, captured from 11:01 ICT. Logs show `s1`
completed with no preceding `step_started`, while `s2` emitted four starts.

## Fix

For the initial accepted renderer-v5 start only, the telemetry boundary resolves the
pending semantic identity from `steps[0]`. Step-scoped starts keep using their frame
identity. A runtime-local set suppresses later cinematic starts for a semantic step,
while a fresh runtime/session can publish the step again during recovery.

The ACK remains behind the existing identity, cinematic-payload, and inbound-sequence
validation. No backend, firmware, legacy renderer, or voice-pipeline contract changes.

## Passing Verification

```text
step progress regressions: 2 passed
cinematic phase routing: 22 passed
cinematic + runtime + forwarder: 319 passed
```

Production deployment and the final physical s1-s9 Parent Progress capture are
recorded in the T5.4 closeout evidence after the gate and merge complete.
