#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T2.1 — ESP lesson runtime state machine.
# Six defects, one repro. The test file is materialized from a REF that outlives
# the task branch (main, which carries it after the t21 merge), so both gate
# phases execute byte-identical assertions against different source — the repro
# tests the bugs, not the patch. Falls back to whatever the checkout already has
# if the ref lookup fails, so this never silently degrades into a no-op.
#
#   1. stop() emitted body.reason "STOPPED", outside the documented §4.6 enum;
#      firmware classifies it as a FAILURE (sad-face UI for a graceful stop).
#   2. on_lesson_error drove ANY inbound error to FAILED, ignoring the wire
#      `retryable` flag that firmware sets on transient conditions.
#   3. An unhandled exception inside the state machine left the runtime wedged in
#      RUNNING with no timer and no terminal event.
#   4. A pause outliving timeoutSec retired the step timer; resume never re-armed
#      it, leaving the step with no ack deadline.
#   5. The hand-built lesson_prepare{preloadResetOnly} frame put an RFC3339
#      STRING on `timestamp`, which the envelope defines as epoch milliseconds.
#   6. The S13 voice-latency breaker detached the runtime but left session_mode
#      pinned to LESSON, silencing the robot for the rest of the connection.
set -euo pipefail

REPO="$TBOT_REPRO_REPO_ROOT"
TEST_REL="main/tbot-server/tests/test_lesson_runtime_state_machine_t21.py"
TEST_NAME="test_lesson_runtime_state_machine_t21.py"

# gate.sh runs this with pwd = the throwaway worktree; the integration re-gate
# runs it with pwd = the repo checkout. Both have main/tbot-server beneath them.
cd "$(pwd)/main/tbot-server"

# Materialize the transition-table suite. `main` carries it after the t21 merge
# and, unlike the task branch, is never deleted. If the ref is unavailable the
# checkout's own copy is used; if there is none either, fail loudly rather than
# report a vacuous pass.
if ! git -C "$REPO" show "main:$TEST_REL" > "tests/$TEST_NAME" 2>/dev/null; then
  rm -f "tests/$TEST_NAME"
  git -C "$REPO" checkout main -- "$TEST_REL" 2>/dev/null || true
  if [ ! -s "tests/$TEST_NAME" ]; then
    echo "FATAL: cannot materialize tests/$TEST_NAME from main or the checkout" >&2
    exit 2
  fi
fi

exec python3 -m pytest -q --no-header -p no:cacheprovider "tests/$TEST_NAME"
