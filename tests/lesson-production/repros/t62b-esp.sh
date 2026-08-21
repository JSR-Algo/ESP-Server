#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
# T6.2 follow-up repro (ESP) — F-T62-02.
#
# T6.2 correlated the lesson runtime surface but left 44 lesson log lines in the
# Google Live voice provider and 5 in the audio bridge carrying neither
# assignment_id nor session_id. Those are the lines a live-run investigation
# reads first (F-T54-02 was diagnosed from exactly this family). The grep audit
# fails at base naming the offending lines.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="main/tbot-server"
TEST_REL="tests/test_lesson_observability_t62b_repro.py"

[ -d "$SERVER_DIR" ] || { echo "FATAL: run from an esp32-server worktree root"; exit 2; }
cp "$HERE/t62/test_lesson_observability_t62.py" "$SERVER_DIR/$TEST_REL"
CLEANUP="$(pwd)/$SERVER_DIR/$TEST_REL"
trap 'rm -f "$CLEANUP"' EXIT

cd "$SERVER_DIR"
python3 -m pytest -q -p no:cacheprovider --tb=short "$TEST_REL"

echo "REPRO PASS: T6.2 follow-up ESP — Google Live lesson log lines carry assignment/session correlation."
