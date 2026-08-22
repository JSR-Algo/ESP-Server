#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T2.1 — ESP lesson runtime state machine.
# Six defects, one repro. The promoted state-machine suite is tracked in this
# repository, so detached and shallow CI checkouts need no local branch refs.
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

TEST_NAME="test_lesson_runtime_state_machine_t21.py"

cd main/tbot-server

exec python3 -m pytest -q --no-header -p no:cacheprovider "tests/$TEST_NAME"
