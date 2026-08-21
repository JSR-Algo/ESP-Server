#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T4.2 — admin assignment console & monitoring (esp32-server side). Two defects:
#
#   1. manager-web `LessonMonitoring.vue` painted every settled response, with no
#      request token on either read. The console is refreshed while lessons are
#      live, so a slow response for an OLD filter set could land after a newer one
#      and repaint the table — and a timeline fetched for assignment A could land
#      in a dialog now showing assignment B. A stale repaint is indistinguishable
#      from truth to the operator: the exact "no stale state" failure this console
#      exists to prevent.
#
#   2. `LessonAssignmentConsoleHandler` published the websocket registry keys —
#      robot MACs — as the device picker's values, and PREFILLED one when a single
#      robot was connected. Every backend assignment route is
#      `/devices/{uuid}/...` behind a UUID param pipe, so a MAC there can only 400.
#      The picker must offer the resolved backend device UUID (mint cache) and must
#      never prefill a value it could not resolve.
#
# Both checks are pulled from the FIX COMMIT into whichever checkout the gate is
# running, so base and tip execute byte-identical assertions against different
# source — the repro tests the bugs, not the patch.
set -euo pipefail

REPO="$TBOT_REPRO_REPO_ROOT"
# Pinned to the T4.2 merge commit, NOT the branch: the Ship checklist deletes the
# branch after merging, and the every-5-merges integration re-gate still has to be
# able to materialize these checks later.
SOURCE_REV="2c2e75cd"
CHECK_REL="main/manager-web/scripts/check-lesson-assignment-ui-contracts.mjs"
TEST_REL="main/tbot-server/tests/test_lesson_assignment_console.py"

WORKTREE="$(pwd)"

# --- 1. manager-web monitoring console staleness contract ---------------------
# Pure node builtins (node:fs, node:vm) — no dependency install needed.
cd "$WORKTREE/main/manager-web"
git -C "$REPO" show "$SOURCE_REV:$CHECK_REL" > scripts/check-lesson-assignment-ui-contracts.mjs
node scripts/check-lesson-assignment-ui-contracts.mjs

# --- 2. ESP operator console device identity ---------------------------------
cd "$WORKTREE/main/tbot-server"
git -C "$REPO" show "$SOURCE_REV:$TEST_REL" > tests/test_lesson_assignment_console.py

exec python3 -m pytest -q --no-header -p no:cacheprovider \
  tests/test_lesson_assignment_console.py
