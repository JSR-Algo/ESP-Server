#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
repro_dir="$repo_root/tests/lesson-production/repros"
export TBOT_REPRO_REPO_ROOT="$repo_root"

if [[ -n "${TBOT_REPRO_PYTHON:-}" ]]; then
  export TBOT_REPRO_PYTHON
fi

passed=0
for repro in "$repro_dir"/*.sh; do
  case "$(basename "$repro")" in
    t42.sh)
      echo "lesson-production repro: t42.sh [SUPERSEDED_STALE_REPRO]"
      continue
      ;;
    t54-esp.sh)
      echo "lesson-production repro: t54-esp.sh [SKIP_REGATE preserved]"
      continue
      ;;
  esac
  echo "lesson-production repro: $(basename "$repro")"
  (cd "$repo_root" && bash "$repro")
  passed=$((passed + 1))
done

echo "lesson-production ESP repros passed: $passed; stale=1; skip-regate=1"
