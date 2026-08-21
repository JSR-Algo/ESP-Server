#!/usr/bin/env bash
# T5.4 — renderer-v5 must publish exactly one ordered step_started for every
# semantic step, including the first step entered by the unscoped start frame.
set -euo pipefail

ROOT="${T54_REPO_ROOT:-$(pwd)}"
[ -d "$ROOT/main/tbot-server" ] || { echo "FATAL: no main/tbot-server under $ROOT"; exit 2; }

cd "$ROOT"
python3 - <<'PYEOF'
from pathlib import Path

source = Path("main/tbot-server/core/lesson/runtime.py").read_text()
assert "self._started_step_ids" in source, "renderer-v5 step_started events are not deduplicated"
assert "telemetry_step = self._steps[0]" in source, "initial renderer-v5 start does not resolve the first step"
print("renderer-v5 first-step progress source guards OK")
PYEOF

python3 -m pytest -q main/tbot-server/tests/test_lesson_cinematic_phase_routing.py \
  -k 'initial_lesson_start_ack_forwards_first_step_started or accepted_lesson_start_ack_forwards_step_started_once'
