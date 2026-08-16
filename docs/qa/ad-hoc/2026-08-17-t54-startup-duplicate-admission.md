# T5.4 Spoken Startup Duplicate Admission — 2026-08-17

## Physical Reproduction

Failed capture:

```text
/Users/manhhodinh/Documents/TBOT/.codex_tmp/lesson-live-20260816T180509Z-t54-final-main
assignmentId=bfa76e9b-ba11-4059-933c-b7fe7494fe62
lesson=w02-feelings v7
manifestChecksum=418dfd660ef4254418229d86f8e77890d29573793b1a8b7a211d31dcfdf0ef27
```

The first normal-distance trigger was recognized at `01:12:07` and started the
assignment. It fetched the manifest and bound session
`59030846-11d2-4c3b-8e43-048380840b28`. A repeated recognized trigger arrived at
`01:12:21`, after the fixed 12-second duplicate window but before the foreground
15-second SD admission deadline:

```text
01:12:21 start_lesson: scheduled lesson start on live connection
01:12:21 start_lesson: lesson pull task cancelled
01:12:22 lesson session bound sessionId=fdd38f51-6127-4d93-9f0d-b2538325dab1
01:12:24 robot SD sync realtime busy timeout timeoutSec=15.000 state=INTERRUPTING
01:12:25 backend post lesson_failed persisted=true
```

The replacement pull used the same pack cache key and joined the coordinator
operation already created by the first pull. That operation retained the first
response-generation admission token and deadline. The repeated intent had already
advanced Google Live to a new generation, so the old operation could no longer use
its scoped admission and expired. This is the concrete F-T54-49 failure sequence.

The finalized capture correctly fails its verifier and is retained as RED evidence.

## Automated RED

Two tests were written before production code:

```text
test_lesson_start_intent_suppresses_duplicate_while_spoken_start_is_pending
  FAIL: transition_to_lesson_start awaited 1 time

test_coalesces_duplicate_tool_call_while_spoken_start_is_pending
  FAIL: replacement task is not the first active task; first task cancelling
```

The T0.4 repro independently fails on base `603bbd52` with:

```text
AssertionError: duplicate replaced the active spoken startup
```

Final branch review then reproduced a narrower preceding race: a loud repeated
utterance could enter the audio barge-in path before its transcript was classified.
That path advanced `_response_generation`, invalidating the active admission token
before the transcript-level duplicate guard ran. The added audio-to-transcript
regression failed on the reviewed tip with `AssertionError: 1 != 0`.

## Fix

Commits:

```text
98ba87b0 docs(lesson): design duplicate startup admission
04a3e2c0 fix(lesson): coalesce duplicate spoken startup
```

`start_lesson` now marks the task it schedules as `spoken_start`. A repeated direct
tool call reuses that unfinished task instead of cancelling it. An unmarked
connect-time/background pull remains replaceable by an explicit spoken start.

Google Live checks the same pending spoken-start ownership immediately after intent
classification. Normal microphone audio still follows the existing barge-in path, so
stop commands and unrelated speech are not muted during a long preload. When that
audio is classified as a repeated lesson start, the provider suppresses the second
tool dispatch and returns the interaction controller to `LISTENING`; the stale
generation no longer leaves the foreground SD busy guard wedged in `INTERRUPTING`.

No SD coordinator, busy-state, timeout, attestation, assignment-state, or renderer
fallback contract changed.

## Passing Verification

```text
RED tests after fix plus background-pull replacement: 4 passed
audio-to-transcript review regressions: initial broad-drop fix REJECTED; scoped
  duplicate recovery PASS
focused start/provider/runtime suites before review fix: 435 passed
post-review provider/tool/voice-guard suite: 183 passed
post-review full ESP suite: 3852 passed, 8 skipped, 12 warnings
python py_compile touched files: PASS
git diff --check: PASS
T0.4 gate: VERIFIED
  base=603bbd52a6f37ca41505e30f254a4e3287b3409e
  tip=72de6a149464e080246fb614049d3e6a445b4ac1
  RED rc=1, GREEN rc=0
```

## Release Gate

The code branch is ready for final review, merge, VPS deployment, and a fresh no-PIN
physical assignment. T5.4 remains `IN_PROGRESS` until the deployed robot passes the
mid-lesson power cycle, completes all nine steps, and produces final CP-7/CP-8 and
parent Progress evidence.
