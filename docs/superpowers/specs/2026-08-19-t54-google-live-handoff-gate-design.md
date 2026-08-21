# T5.4 Google Live Lesson Handoff Gate

## Context

The strict T5.4 physical run can reach the spoken or protected `start_lesson`
path while Google Live still owns the child interaction. The current transition
interrupts the active turn and sends the lesson-specific firmware TTS stop, but
then returns the provider to `LISTENING` before assignment pull, SD sync, and
runtime preload finish. During that awaited interval, late microphone frames,
model events, or unrelated tool calls can reopen realtime activity. Firmware then
reports `lesson asset sync busy or worker unavailable` until the existing
15-second SD-sync deadline expires.

Entering `SessionMode.LESSON` before preload is not acceptable: a refused or
failed preload would require rolling back durable lesson ownership and risks a
latched lesson mode. The handoff therefore needs a provisional, bounded owner
that suppresses Google Live without claiming that a lesson runtime has started.

## Goals

- Keep Google Live quiescent from the accepted lesson-start intent through
  assignment pull and SD attestation.
- Use the same handoff behavior for the normal spoken `start_lesson` tool and the
  protected HTTP nudge.
- Transfer ownership to `SessionMode.LESSON` only after preload succeeds.
- Restore ordinary conversation safely when startup fails, times out, is
  cancelled, or the connection closes.
- Preserve the existing 15-second transient SD-sync retry and fail-closed rules.
- Leave classic/non-Google voice behavior unchanged.

## Non-goals

- Do not broaden which firmware SD-sync errors are retryable.
- Do not extend or replace the existing SD-sync timeout.
- Do not synthesize lesson progress or alter renderer event semantics.
- Do not change assignment selection, authorization, or nudge identity rules.
- Do not make the provisional gate a new externally visible session mode.

## Design

### Connection-owned provisional gate

Add a connection-scoped lesson-start handoff gate with explicit acquire, query,
and release operations. The gate is independent of `SessionMode`: while held,
the connection remains in its prior conversation state but Google Live no longer
owns mic input, model output, or general tools.

The gate is idempotent and coalesces concurrent starts on the existing
`_lesson_pull_lock`. Acquisition records the current ownership generation so a
late release from an older/cancelled attempt cannot release a newer attempt.
Release must be safe to call from success, failure, timeout, cancellation, and
disconnect cleanup paths.

The gate lifetime is bounded by the existing lesson-start operation. It begins
before the first awaited assignment pull/preload work and ends in exactly one of
two ways:

1. Successful preload: `enter_lesson_mode()` establishes durable lesson
   ownership, then the provisional gate is released without reopening Live.
2. Any unsuccessful exit: the provisional gate is released and the provider is
   restored to a safe conversation/listening state if the connection is still
   active.

### Google Live transition

`transition_to_lesson_start()` performs the hard interruption and acquires the
handoff gate. The lesson interruption uses the existing lesson-specific bridge
stop so firmware receives:

```json
{
  "type": "tts",
  "state": "stop",
  "reason": "interrupt",
  "continue_listening": false,
  "listen_mode": "manual"
}
```

The transition may settle internal transient state for admission, but it must not
reopen the firmware microphone or return practical interaction ownership to
Google Live while the gate is held.

For the protected nudge, the gate remains held across the awaited
`maybe_start_lesson_on_connect()` call. For the spoken tool path, it remains held
across tool dispatch and the scheduled/pulled startup. Duplicate `start_lesson`
requests are allowed to coalesce with the in-flight startup; they must not open a
second gate or create another runtime.

### Input, output, and tool guards

All three realtime entry paths treat the provisional gate as lesson-owned for
suppression purposes:

- `ConnectionHandler._route_audio_message_impl()` consumes inbound mic frames
  while the gate is held and does not forward them to Google Live or classic ASR.
- `GoogleLiveAudioBridge._should_drop_lesson_model_output()` drops transcript and
  audio events while the gate is held. Cancellation/control cleanup events remain
  admissible where needed to settle the interrupted response.
- `GoogleLiveProvider._handle_tool_call_event()` rejects unrelated tools such as
  `change_volume` with the existing lesson-ownership error shape while the gate is
  held. `start_lesson` remains admissible only for coalescing the same startup.

No gate check changes the normal classic/non-Google path unless that connection
has explicitly acquired the gate.

### Success and failure cleanup

The runtime startup orchestration owns final gate cleanup because it knows whether
preload transferred to lesson mode. Cleanup is structured with `try/finally` (or
an equivalent single-owner mechanism) so exceptions and task cancellation cannot
leak the gate.

On successful preload, ordering is:

1. Verify SD attestation and all existing pre-start guards.
2. Enter `SessionMode.LESSON`.
3. Activate/swap the candidate runtime and start the protocol using existing
   ordering guarantees.
4. Release the provisional gate without reopening conversation.

On failure before lesson ownership, cleanup clears provisional state, cancels any
remaining stopped-response output, sets the interaction controller to a safe
listening state, clears `client_abort`, and reopens the normal user audio window
only when the connection is still usable. Disconnect cleanup clears the gate but
does not attempt to reopen a dead provider.

### Timeout and retry compatibility

The handoff gate introduces no new SLA or SD-sync deadline. The existing
15-second foreground deadline remains authoritative. Runtime continues to retry
only the exact transient firmware condition
`lesson asset sync busy or worker unavailable`; unrelated MCP errors are attempted
once and fail closed.

## Observability

Emit structured logs for gate acquire, coalesced acquire, transfer, failure
release, cancellation/timeout release, stale-generation release rejection, and
blocked audio/model/tool events. Include the available assignment/session context
without logging secrets. These logs must distinguish provisional handoff from
durable `SessionMode.LESSON` ownership.

## Test Strategy

Implementation follows RED-GREEN-REFACTOR. RED coverage must prove:

- Protected nudge holds the gate across awaited assignment pull/preload.
- Spoken `start_lesson` holds the same gate across scheduled startup.
- Firmware stop uses `continue_listening=false` and `listen_mode=manual`.
- Late inbound audio is consumed without reopening `USER_STREAMING`.
- Late model transcript/audio output is dropped while the gate is held.
- Late `change_volume` is rejected while `start_lesson` remains coalescible.
- Successful preload enters lesson mode before releasing provisional ownership.
- Refused/failed preload releases the gate and safely restores conversation.
- Timeout, cancellation, and disconnect release the gate.
- Existing exact transient retry and unrelated-error fail-closed tests stay green.
- Non-Google/classic behavior remains unchanged.

Targeted suites include lesson nudge, Google Live provider/tool/output edges,
connection audio routing, start-lesson tool scheduling, and lesson runtime startup.
The full relevant server suite must pass before an isolated ESP deployment.

## Physical Verification

After isolated deployment, run one fresh strict assignment only after Parent Today
has remained continuously foreground for more than 15 minutes. Success still
requires the complete H1 evidence set: assignment/session IDs, automatic s1-s9
PNG/XML captures, exact nine-row percentages, canonical single `step_started`
events and raw observed latencies, audio/three-layer/MCP-motion evidence,
persisted completion and `COMPLETED` read-back, return to `CONVERSATION` with the
normal face, renderer verifier 101/101, and production probe exit 0.

Any failed acceptance item freezes evidence, routes a finding, and leaves T5.4
`IN_PROGRESS`; it does not authorize another assignment in the same pass or C1
Ship closeout.
