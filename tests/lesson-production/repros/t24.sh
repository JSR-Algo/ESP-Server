#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
# T2.4 repro — ESP WebSocket lifecycle & reconnection.
#
# Three defects, all asserted behaviourally so RED@base is a failed assertion
# about observable behaviour, not a missing symbol:
#
#   1. Stale socket on reconnect. A device that reconnects registers a second
#      ConnectionHandler; before the fix the first one stayed alive with its own
#      lesson runtime + event forwarder and its socket stayed OPEN. The repro
#      connects twice with one device-id over a real websockets acceptor and
#      requires the first client to observe a close (1001).
#   2. Silent peer death during a lesson. `timeout_seconds` is floored at 61
#      minutes, so a half-open socket left a RUNNING lesson parked in LISTENING
#      for the rest of the hour. The repro drives the real `_check_timeout` loop
#      with a running lesson runtime and 120 s of inbound silence and requires
#      the socket to be closed.
#   3. Out-of-order resume. A hello arriving on the socket the device already
#      abandoned was processed by the superseded handler (conn.features got set
#      on it). Nothing classifies the T2.5 liveness-lease epoch on the receive
#      path, so closing the socket is what enforces this.
#
# The test file is carried here rather than read from the worktree so the repro
# still runs after the task worktree is removed (T7.5 promotes repros into CI).
set -euo pipefail

SERVER_DIR="main/tbot-server"
TEST_REL="tests/test_ws_reconnect_lifecycle.py"

[ -d "$SERVER_DIR" ] || { echo "FATAL: run from an esp32-server worktree root"; exit 2; }

cd "$SERVER_DIR"
python3 -m pytest -q -p no:cacheprovider --tb=short "$TEST_REL" \
  -k "test_reconnect_closes_the_old_socket_and_silences_its_sends \
      or test_running_lesson_with_a_silent_peer_closes_inside_the_budget \
      or test_a_late_hello_on_the_old_socket_is_never_processed"
