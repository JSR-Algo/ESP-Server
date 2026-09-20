# Lesson Studio E2E state preparation

Both Playwright global setup and the real login helper call
`main/manager-web/scripts/reset-lesson-studio-e2e-state.cjs`.

- `LESSON_STUDIO_E2E_STATE_MODE=preserve` checks existing services, selected image
  IDs, loopback ports and media mounts, then returns without running reset
  commands. It does not require seed services or clear Redis throttles or
  PostgreSQL login attempts. Repeated logins and captcha requests remain subject
  to real authentication limits; a rate-limited journey is not a passing run.
- `reset` (also the default when the variable is absent) additionally requires
  seed services and runs the existing three throttle-reset commands. The Redis
  `rl:*` deletion is namespace-wide, not scoped to fixture accounts. Use it only
  in an independently verified, owned disposable test namespace.
- Other values fail before service subprocesses run.

Preflight checks do not establish database ownership, authorization to reset, or
that a historical stack matches current source. Verify those separately before
running either suite. Preserve mode makes preparation read-only; the journeys
themselves still create and modify data and require authorized fixture ownership.
Do not switch a recovered/shared stack to reset mode to bypass a login failure.

This boundary implements the recovery requirement in the campaign's
`T08/runs/run-03-20260913-recovery73/runtime/recovery-sequence.md`, section 4:
preserve mode does not manufacture missing login inputs or relax throttling.
The previous implementation intentionally cleared throttles in both modes;
preserve no longer performs that exception. Disposable reset behavior is unchanged.
