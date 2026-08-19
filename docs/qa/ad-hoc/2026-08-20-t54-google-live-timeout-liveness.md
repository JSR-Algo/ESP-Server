# T5.4 Google Live receive-timeout liveness

## Scope

This lane owns only F-T54-65. It does not merge, deploy, flash, reset hardware,
create assignments, or change the classic voice pipeline.

## Root cause

`GoogleLiveClient.receive_events()` logged receive timeouts but converted them to
an internal `None` result. The provider therefore could not route the timeout to
an active renderer-v5 lesson. When the prompt had already been resent and no
child response window timer was armed, the lesson could remain `RUNNING` while
the separate robot WebSocket continued to exchange pings.

## Fix

- Surface a typed `receive_timeout` event from the Google Live client while
  preserving the pending receive operation.
- Consume that event inside `GoogleLiveProvider` only when Google Live owns an
  active lesson.
- Route renderer-v5 safe-speaking steps into the existing bounded no-answer
  policy. An already-armed timer is retained; a missing timer starts or advances
  the existing retry policy.
- Leave conversation mode and the classic pipeline unchanged.

## Deterministic regression

`lesson-prod/repros/t54-google-live-timeout-liveness.sh` installs a temporary
pytest probe into the selected checkout. Its event-controlled clock proves that
a post-resend receive timeout enters retry level 1, arms the response timer,
advances through the bounded retry budget, and cannot leave the original step
parked in the same `RUNNING` state indefinitely.

## Verification

- Focused client/provider/renderer-v5 regression: 3 passed.
- Conversation/runtime/provider campaign: 696 passed.
- Full ESP Python suite: 3,870 collected, exit 0.
- `py_compile`, `git diff --check`, dedicated RED-to-GREEN gate, and final
  commit review are recorded in the lane handoff.
