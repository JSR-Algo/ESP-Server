#!/bin/sh
set -eu

# Canonical inventory markers are kept here so audits can verify this committed
# entry point without trusting a workspace-level convenience wrapper:
# verify-course-mode-curriculum
# test_course_mode_curriculum_e2e.py
# test_course_mode_runtime_integration.py
# test_course_mode_physical_tft_preflight.py
# run_host_native_lesson_cinematic_renderer_test.sh
# test:e2e:course-mode

usage() {
  echo "usage: $0 --candidate PATH [--mode quick|full|live-db|physical-preflight] [--report PATH]" >&2
  exit 2
}

[ "$#" -ge 2 ] || usage
[ "$1" = "--candidate" ] || usage

SCRIPT_DIR=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && pwd -P)
REPOSITORY_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd -P)
PYTHON=/usr/bin/python3
[ -x "${PYTHON}" ] || PYTHON=/opt/homebrew/bin/python3
[ -x "${PYTHON}" ] || { echo "trusted python3 is unavailable" >&2; exit 1; }

exec /usr/bin/env -i \
  PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
  HOME=/nonexistent LANG=C LC_ALL=C \
  COURSE_MODE_V2_TEST_DATABASE_URL="${COURSE_MODE_V2_TEST_DATABASE_URL-}" \
  COURSE_MODE_TEST_DATABASE_URL="${COURSE_MODE_TEST_DATABASE_URL-}" \
  DATABASE_URL="${DATABASE_URL-}" \
  COURSE_MODE_ADMIN_E2E_READY="${COURSE_MODE_ADMIN_E2E_READY-}" \
  "${PYTHON}" "${REPOSITORY_ROOT}/main/tbot-server/scripts/course_mode_release_gate.py" "$@"
