# Connection Activity Lease Design

Date: 2026-07-16
Status: Approved Approach A amendment
Scope: ESP server exact lesson-cache eviction, voice admission, and lesson mutation

## Decision

Replace the current uncommitted eviction booleans and transition counters with one
per-connection `ActivityLeaseCoordinator`. The coordinator provides a non-waiting
shared lease for voice operations and a non-waiting exclusive lease for exact
cache eviction. Lesson mutation remains serialized by `_lesson_pull_lock` and
must refuse while an exclusive lease exists.

The coordinator closes the TOCTOU window between `is_realtime_busy()` and the
firmware MCP call. Voice work that already owns a lease makes eviction refuse.
Once eviction owns the exclusive lease, new voice work fails immediately and is
consumed without queueing or replay.

## Non-goals

- Do not replace the voice interaction state machine or `is_realtime_busy()`.
- Do not queue voice work behind eviction.
- Do not retry an ambiguous destructive MCP result on the same connection.
- Do not broaden the exact-key deletion or internal authentication contract.
- Do not modify firmware, rewards, mobile, or manager-web behavior in this slice.

## Components

Create `main/tbot-server/core/activity_lease.py` with:

```python
class LeaseKind(str, Enum):
    VOICE = "voice"
    EVICTION_EXCLUSIVE = "eviction-exclusive"

class ExclusiveDisposition(str, Enum):
    DEFINITIVE = "definitive"
    AMBIGUOUS = "ambiguous"

class ActivityLeaseCoordinator:
    def try_acquire_voice(self, operation: str) -> ActivityLease | None: ...
    def try_acquire_eviction(
        self,
        operation: str,
        *,
        busy_probe: Callable[[], bool],
    ) -> ActivityLease | None: ...
    def has_voice_leases(self) -> bool: ...
    def has_exclusive_lease(self) -> bool: ...
    def close(self) -> None: ...

class ActivityLease:
    @property
    def owner_task(self) -> asyncio.Task: ...
    def release(self) -> None: ...
    def release_when_done(self, future: FutureLike) -> None: ...
    def complete_exclusive(self, disposition: ExclusiveDisposition) -> None: ...
```

`try_acquire_*` never waits. It returns `None` on conflict. The coordinator is
created by `ConnectionHandler` on the connection event loop and is closed during
connection teardown.

## Ownership And Reentrancy

- Acquisition requires `asyncio.current_task()` on the coordinator's event loop.
- Every record stores an opaque lease id, kind, operation name, owner task
  identity, nesting depth, and optional delegated future identity.
- Voice leases are shared across tasks.
- Nested voice acquisition by the same task is reentrant. It increments depth and
  returns a distinct one-shot handle; the record disappears only after every
  nested handle completes.
- Exclusive acquisition is not reentrant. A second exclusive attempt, including
  from the same task, is refused.
- A task holding a voice lease cannot acquire the exclusive lease. A task holding
  the exclusive lease cannot acquire a voice lease.
- `release()` is one-shot and valid only from the owning task. Wrong-task,
  duplicate, closed, or wrong-kind release raises an internal invariant error and
  does not change coordinator state.

These rules prevent unrelated callbacks from clearing another operation's guard
and make nested Google Live helpers safe without scattered counters.

## Future And Thread Lifetime

An async voice operation holds its lease through its complete `await` lifetime.
For work delegated to a future, the owner calls `release_when_done(future)` before
returning. Delegation is one-shot:

1. The lease records the exact future identity.
2. `asyncio.Future` callbacks run on the owner loop.
3. `concurrent.futures.Future` callbacks use
   `loop.call_soon_threadsafe(...)` to perform cleanup on the owner loop.
4. Cleanup validates both lease id and future identity before releasing.
5. Cancellation of the scheduling coroutine does not release a delegated lease;
   only completion or cancellation of the delegated future does.

`startToChat` must acquire before intent/LLM initiation, submit `conn.chat` while
the lease is held, delegate the lease to the returned executor future, and retain
it until that future is done. Submission failure releases from the owner task.

## Eviction Lifecycle

`evict_exact_cache_key` starts a connection-tracked operation task and awaits it
with `asyncio.shield`. The operation task, not the HTTP caller, owns the lesson
lock and exclusive lease:

1. Resolve and validate the connection and key.
2. Acquire `_lesson_pull_lock`.
3. Recompute voice busy, render busy, and every protected key.
4. Call `try_acquire_eviction(..., busy_probe=conn.is_realtime_busy)` without an
   intervening `await`.
5. Dispatch the fixed firmware MCP call while holding the exclusive lease.
6. Release only after a definitive result; otherwise make the exclusive lease
   sticky.

Caller cancellation cancels only the wait. It never cancels the operation task or
releases the exclusive lease. The task remains in the connection's tracked MCP
task set and drains the remote result.

A result is definitive when strict parsing proves a matching request/result key
and a coherent firmware success/refusal, or when a correlated MCP response proves
the tool was not executed. A local failure before dispatch is also definitive and
may release. Timeout, transport loss after dispatch, malformed payload, missing or
mismatched key, and any uncorrelated exception are ambiguous.

On ambiguity, call
`complete_exclusive(ExclusiveDisposition.AMBIGUOUS)`. The exclusive lease remains
sticky until `ActivityLeaseCoordinator.close()` during connection teardown. The
lesson lock may then be released; subsequent lesson mutation obtains the lock but
refuses because the sticky exclusive lease remains. No retry is allowed on that
connection.

## Lesson Mutation

`maybe_start_lesson_on_connect` and `start_sample_lesson` continue to use the same
`_lesson_pull_lock`. After acquiring it and before changing runtime, candidate,
activation, preload, current, or previous-known-good state, each checks
`has_exclusive_lease()` and returns stable `CACHE_EVICTION_RESERVED` status when
true.

All new lesson lifecycle entry points must follow the same order: acquire
`_lesson_pull_lock`, check the exclusive lease, then mutate. They must never take
a voice lease while holding the lesson lock.

## Voice Integration

Control-plane messages remain available during eviction: `hello`, `ping`, `mcp`,
`lesson_ack`, `lesson_progress`, `lesson_error`, and `abort`. `listen/stop` is a
safety control: it may clear local buffered input but must not finalize ASR or a
Live user stream while exclusive eviction is active. Voice-plane audio,
listen/start, wake/detect text, and conversational text require a voice lease.
Refused voice input is consumed immediately, logged with the stable operation and
reason only, and never placed in an ASR, provider, or replay queue.

Required integration points are:

- `ConnectionHandler._route_message` for inbound Google Live and classic voice.
- `handleAudioMessage` for classic audio already present in the cross-thread ASR
  queue when eviction begins.
- `startToChat` for classic intent, TTS, and the complete executor future.
- Google Live connect/open, reconnect, hard reconnect, text send, wake greeting,
  and delayed/background prewarm tasks.
- Conversation-mode entry and lesson-finish return-to-conversation before session
  mode changes.

Google helpers may acquire nested voice leases; task reentrancy collapses them to
one owner record. Before the final nested lease is released, the code must either
finish the operation or establish an existing canonical busy state such as
`WAITING_MODEL`, output-active, or reconnecting. This prevents an unguarded gap
between a send completing and the interaction state becoming busy.

## Failure And Logging Contract

- Lease conflicts are expected control flow, not exceptions to callers.
- Internal ownership violations fail closed and emit no child text or payload.
- Logs contain operation name, lease kind, stable reason, and lease id suffix only.
- Logs never contain transcripts, prompts, MCP arguments, asset URLs, tokens, or
  raw exception strings.
- Sticky ambiguity is visible in a sanitized coordinator snapshot for diagnostics.

## Deterministic Tests

Core tests use real asyncio tasks and controlled futures to prove shared voice,
exclusive eviction, task ownership, nested depth, wrong-task release refusal,
thread-safe done callbacks, delegated cancellation, sticky ambiguity, and teardown
cleanup.

Integration tests hold MCP, Google, ASR, or executor futures on events and prove:

- voice-first makes eviction refuse before MCP;
- eviction-first makes every voice path fail immediately with no queue/replay;
- classic `conn.chat` retains its lease until the executor future completes;
- Google connect/send/greeting and reconnect retain leases for their full tasks;
- caller cancellation leaves the remote eviction task and lease alive;
- definitive remote completion releases, while timeout/malformed/key mismatch is
  sticky until teardown;
- lesson pull/sample serialize on `_lesson_pull_lock`, recheck after waiting, and
  refuse a sticky exclusive lease;
- control-plane messages still work during eviction.

## Rollout And Stop Conditions

Land the coordinator core and its tests separately from integrations. Then land
eviction/lesson integration, followed by classic and Google Live integration.
Run focused tests after every slice and the complete server suite before Docker or
hardware work.

Stop on any lease leak after definitive completion, wrong-task cleanup, queued or
replayed refused voice, ambiguous result that releases, lesson mutation during an
exclusive lease, or transcript/payload data in logs. Keep the exact eviction route
disabled for live proof until all lease and existing voice non-regression tests
pass.
