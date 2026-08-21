#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T4.1 — Admin lesson builder / studio. Four defects, one repro.
# The three lesson-studio check scripts are pulled from the FIX BRANCH into
# whichever checkout the gate is running (base or tip), so both phases execute
# byte-identical assertions against different src/ — the repro tests the bugs,
# not the patch. All three are Node-stdlib only, so a bare worktree suffices.
#
#   1. Deleting a bundle asset that a step still binds was unguarded: the
#      popconfirm asked nothing about references, so the step body kept a src
#      pointing at a row the server no longer had and the manifest shipped an
#      un-cached, un-attested URL instead of failing validation.
#      (collectAssetReferences already existed — it was only wired to the
#      shared-visual clone path, never to delete.)
#   2. PATCH /lessons/:id/steps/:stepKey carries no version token and the client
#      sent none, so two operators on one lesson were silent last-write-wins:
#      the second save overwrote the first with no signal to either. The local
#      `savedRevision` only ever compared the editor's own draft revisions.
#   3. Step drafts (authoringDrafts / contentDrafts / assetDrafts) live in
#      component state alone — no router leave guard and no beforeunload, so a
#      navigation or reload dropped unsaved authoring silently.
#   4. teachingWord used HTML maxlength="12", i.e. 12 UTF-16 code units, while
#      the backend budgets 12 *visible characters* (Intl.Segmenter graphemes).
#      A Vietnamese word carrying combining diacritics measures up to 3x its
#      visible length, so the input truncated valid input mid-word.
#
# NOT gated here (no RED phase is possible, so it would be tautological):
#   - main/tbot-server/tests/test_lesson_studio_e2e_compose.py and
#     test_manager_web_lesson_derivatives_runtime.py were stale *tests* against
#     an unchanged docs/docker/nginx.conf; the branch versions pass on base too.
#     Verified by direct pytest runs — see the T4.1 evidence file.
#   - scripts/check-tvideo-journey-browser.mjs browser-preference fix: needs a
#     built harness + Chrome, not a bare worktree.
set -euo pipefail

REPO="$TBOT_REPRO_REPO_ROOT"

WORKTREE="$(pwd)"
cd "$WORKTREE/main/manager-web"

# Materialize the lesson-studio checks. `main` carries them after the merge and,
# unlike a task branch, is never deleted — and unlike a pinned SHA it never drifts
# behind a later fix that deliberately changes the asserted source shape. If the
# ref is unavailable, fall back to the checkout's own copy; if there is none
# either, fail loudly rather than report a vacuous pass.
for rel in \
  main/manager-web/scripts/check-lesson-builder-logic.cjs \
  main/manager-web/scripts/check-lesson-visual-selection.cjs \
  main/manager-web/scripts/check-lesson-step-editor-state.cjs
do
  dest="scripts/$(basename "$rel")"
  if ! git -C "$REPO" show "main:$rel" > "$dest" 2>/dev/null; then
    rm -f "$dest"
    git -C "$REPO" checkout main -- "$rel" 2>/dev/null || true
    if [ ! -s "$dest" ]; then
      echo "FATAL: cannot materialize $dest from main or the checkout" >&2
      exit 2
    fi
  fi
done

status=0
for script in \
  check-lesson-builder-logic.cjs \
  check-lesson-visual-selection.cjs \
  check-lesson-step-editor-state.cjs
do
  echo "--- $script ---"
  if node "scripts/$script"; then
    echo "PASS $script"
  else
    echo "FAIL $script"
    status=1
  fi
done
exit "$status"
