#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
# T6.2 repro (ESP) — lesson log correlation + reconnect-storm counters.
#
# Two defects:
#
#   1. Lesson log correlation was implemented three separate times, over three
#      different id shapes (LessonRuntime._with_log_context, the module-level
#      _log in maybe_start_lesson_on_connect, forwarder._with_lesson_log_context)
#      — and EVERY lesson log line outside those three helpers carried neither
#      assignment_id nor session_id. A grep audit at base finds bare lesson log
#      lines in core/lesson/runtime.py, core/handle/textHandler/
#      lessonMessageHandler.py and core/connection.py, so a lesson session cannot
#      be reconstructed from the ESP log or joined to the backend's
#      progress_events.
#   2. A superseded device connection and a peer-silence socket close — the two
#      signals a reconnect storm shows up in — had no counter at all, only log
#      lines (the T2.4 finding routed to T6.2).
#
# The test file is carried here rather than read from the worktree so the repro
# still runs after the task worktree is removed (T7.5 promotes repros into CI).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="main/tbot-server"
TEST_REL="tests/test_lesson_observability_t62_repro.py"

[ -d "$SERVER_DIR" ] || { echo "FATAL: run from an esp32-server worktree root"; exit 2; }
cp "$HERE/t62/test_lesson_observability_t62.py" "$SERVER_DIR/$TEST_REL"
# Absolute path: the trap fires after the `cd` below, so a relative one would
# resolve against the wrong directory and leave the copy behind.
CLEANUP="$(pwd)/$SERVER_DIR/$TEST_REL"
trap 'rm -f "$CLEANUP"' EXIT

cd "$SERVER_DIR"
python3 -m pytest -q -p no:cacheprovider --tb=short "$TEST_REL"

echo "REPRO PASS: T6.2 ESP — lesson log lines carry assignment/session correlation and reconnect-storm counters are exposed."
