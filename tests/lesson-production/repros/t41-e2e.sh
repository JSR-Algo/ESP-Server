#!/usr/bin/env bash
# repo: $TBOT_REPRO_REPO_ROOT
#
# T4.1 follow-up — two lesson-studio defects the Playwright suite could not
# catch while it was unrunnable. Same shape as repros/t41.sh: the branch's
# lesson-studio check scripts are materialized into whichever checkout the gate
# is running (base or tip), so both phases execute byte-identical assertions
# against different src/. Node stdlib only, so a bare worktree suffices.
#
#   1. Add step / step-dialog Save / Delete step were PERMANENTLY disabled — no
#      step could be created in the studio at all. Their `:disabled` chains
#      ended in `deletingStepKey`, a string that idles at ''. `a || b || ''`
#      evaluates to '' and Vue coerces an empty string on a Boolean prop to
#      true, so the controls were dead whenever no delete was in flight, i.e.
#      always. The step navigator's "+ Add step" uses `!deletingStepKey`, so the
#      dialog still opened — the failure read as a UI hang, not a dead control.
#      Fixed by routing every such chain through the Boolean() computed
#      `stepMutationBlocked`.
#
#   2. Saving a step silently PATCHed the operator's PRE-EDIT prompt back.
#      `promptDraft` is not keyed by step, and the T4.1 concurrency guard added
#      an async listSteps hop before the PATCH; any step refetch landing in that
#      window ran resetPromptDraft and reverted the typing. Fixed at the root
#      (resetPromptDraft refuses to discard a dirty draft for the step still
#      being edited; only the post-save sync forces server truth) plus a
#      defensive prompt snapshot captured at click time.
#
# NOT gated here (no RED phase is possible, so it would be tautological):
#   - The e2e suite repairs themselves (helpers/admin-api.js, helpers/select.js,
#     session.js manager-bearer probe, and the five spec updates). They are the
#     harness, not the product; their proof is the suite going 0/5 -> 5/5
#     against the docker stack. See the T4.1 evidence file.
#   - The RobotEspTftProjectionPreview aria-label and LessonStepPromptEditor
#     data-testid: additive test affordances with no behavior to regress.
set -euo pipefail

REPO="$TBOT_REPRO_REPO_ROOT"

WORKTREE="$(pwd)"
cd "$WORKTREE/main/manager-web"

# Exercise this candidate's assertions without replacing them from another ref.
for script in check-lesson-builder-logic.cjs check-lesson-visual-selection.cjs check-lesson-step-editor-state.cjs; do
  if [ ! -s "scripts/$script" ]; then
    echo "FATAL: candidate check missing: $script" >&2
    exit 2
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
