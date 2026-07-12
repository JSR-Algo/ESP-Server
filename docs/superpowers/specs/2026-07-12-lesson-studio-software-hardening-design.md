# Lesson Studio Software Hardening Design

## Scope

This addendum closes four software-only gaps found while executing the Production Lesson Studio plan. Hardware proof remains explicitly out of scope and all Task 14 live rows stay `NOT PASS`.

## Rollout Capabilities

The NestJS backend remains the sole authority for `sharedVisualAuthoring` and `exactEspTftPreview`. An authenticated super-admin endpoint returns capabilities evaluated from the existing literal-`true` environment flags, real admin session, and admin allowlist. The manager initializes both values to false and stays false on timeout, malformed data, or request failure.

The manager hides the visual-library navigation and guards direct visual-library routes when shared authoring is unavailable. `LessonEditor` hides shared-asset controls independently from exact preview controls. Backend guards remain mandatory, so client gating is UX rather than security.

## Live Prewarm Ownership

The prewarm task owns its nested Live-open attempt even though `asyncio.wait_for` runs that attempt in a child task. A narrowly propagated `preserve_live_prewarm` flag tells budget-degrade cleanup not to cancel or clear the owning parent. Every external close, timeout, reconnect, and foreground cleanup keeps the existing cancellation behavior.

The fix must not merely catch `CancelledError`: fallback activation must complete and leave no orphan child task.

## Audio Rate Controller Loop Ownership

Queue contents and empty/data state remain loop-neutral. Async events are created lazily inside the running loop and may be rebound only when no sender task is alive. Cross-loop use while an active sender belongs to another loop fails explicitly. Production wait sites use an async `wait_until_empty()` API instead of awaiting a constructor-created event.

## Python Runtime Guard

Python 3.10 is the required minimum because CI, production images, and `mcp==1.22.0` use it. A small preflight test and documentation make unsupported Python 3.9 fail clearly instead of producing misleading partial-suite import errors. Dependencies are not downgraded.

## Verification

- Backend capability controller/config/guard tests.
- Mounted manager route, navigation, editor, and failure-default tests.
- Prewarm budget-degrade, external-close, timeout, and subsequent-prewarm tests.
- Audio controller synchronous construction, loop rebind, active-loop rejection, reset, cancellation, and existing timing tests.
- Python version guard tests plus existing CI/Docker contract tests.
- Full Lesson Studio gates, bounded broad ESP suite, manager production build, backend lint/typecheck/build, and clean-worktree checks.

