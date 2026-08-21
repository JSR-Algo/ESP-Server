#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
# T6.3 close-out, robot half — the mid-assignment rollback policy.
#
# The T6.3 evidence claims the robot side of "rollback mid-assignment" is pinned by tests.
# It was written, but never committed: the session assumed robot/ was untracked (F-T53-03),
# which holds for the robot/ PARENT but not for robot/esp32-server, which is its own repo.
# verify-on-main.sh exposed it — 253 tests on main against 255 in the working tree.
#
# Policy under test: the backend rollout gate runs only on assignment CREATION and never
# mutates, and the robot reads the assignment exactly ONCE, at start. So a lesson already
# RUNNING when a rollout flag flips runs to completion, and the rollback lands on the NEXT
# start, where a terminalized assignment is declined with ASSIGNMENT_TERMINAL.
#
# Expected: RED at base (both tests absent), GREEN at tip.
set -euo pipefail

SERVER="main/tbot-server"
[ -d "$SERVER" ] || { echo "FATAL: run from the esp32-server repo root"; exit 2; }
cd "$SERVER"

python3 -m pytest -q \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_assignment_is_read_once_per_start_so_a_running_lesson_is_not_revoked \
  tests/test_lesson_runtime.py::RepublishOnConnectTest::test_rollback_cancelled_assignment_is_refused_cleanly
