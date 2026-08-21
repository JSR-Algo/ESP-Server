#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

probe="$(mktemp "${TMPDIR:-/tmp}/t54-trigger-fragment-recovery.XXXXXX.py")"
trap 'rm -f "$probe"' EXIT

cat >"$probe" <<'PY'
from core.voice.session_provider.google_live import GoogleLiveProvider


provider = object.__new__(GoogleLiveProvider)
provider._start_lesson_asr_fragment = ""
provider._start_lesson_asr_fragment_parts = 0
provider._start_lesson_asr_fragment_at = 0.0
provider._start_lesson_asr_fragment_generation = -1

combine = getattr(provider, "_combine_start_lesson_asr_fragment", None)
assert callable(combine), "fragmented lesson trigger has no bounded recovery path"

for generation, fragment in ((2, "bắt"), (4, "đầu"), (6, "bài")):
    assert combine(fragment, generation) is None
assert combine("học", 8) == "bat dau bai hoc", (
    "exact marker fragments did not recover the lesson trigger"
)

provider._clear_start_lesson_asr_fragment()
for generation, fragment in ((10, "bắt đầu"), (12, "bài hát"), (14, "bài học")):
    assert combine(fragment, generation) is None

print("T54 fragmented lesson trigger recovery: PASS")
PY

if [ -n "${TBOT_GATE_PYTHON:-}" ]; then
  python_bin="$TBOT_GATE_PYTHON"
else
  python_bin="${TBOT_REPRO_PYTHON:-python3}"
  if [ ! -x "$python_bin" ]; then
    common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
    if [ -n "$common_dir" ]; then
      python_bin="$(dirname "$common_dir")/main/tbot-server/.venv311/bin/python"
    fi
  fi
  if [ ! -x "$python_bin" ]; then
    python_bin="$(command -v python3 || true)"
  fi
fi

if [ -z "$python_bin" ] || [ ! -x "$python_bin" ]; then
  echo "FATAL: no executable Python interpreter found for the T54 trigger probe" >&2
  exit 2
fi

PYTHONPATH="$PWD/main/tbot-server" "$python_bin" "$probe"
