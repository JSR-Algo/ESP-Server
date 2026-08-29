#!/bin/sh
set -eu

usage() {
  echo "usage: $0 --candidate PATH [--mode quick|full|live-db|physical-preflight] [--report PATH] | --list-lanes" >&2
  exit 2
}

SCRIPT_DIR=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && pwd -P)
REPOSITORY_ROOT=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd -P)
GIT=/usr/bin/git
[ -x "${GIT}" ] || GIT=/Library/Developer/CommandLineTools/usr/bin/git
[ -x "${GIT}" ] || { echo "trusted git is unavailable" >&2; exit 1; }

trusted_git() {
  /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C LC_ALL=C \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
    GIT_OPTIONAL_LOCKS=0 PAGER=cat \
    "${GIT}" --no-optional-locks -c core.fsmonitor=false -c core.hooksPath=/dev/null \
    -c credential.helper= -c diff.external= -c core.pager=cat "$@"
}

# A root-owned immutable launcher remains blocked to Task9; this only detects
# bootstrap drift relative to the repository's current HEAD.
[ "$(trusted_git -C "${REPOSITORY_ROOT}" rev-parse --show-toplevel)" = "${REPOSITORY_ROOT}" ] || {
  echo "canonical gate is not inside its repository root" >&2
  exit 1
}
HEAD_SHA=$(trusted_git -C "${REPOSITORY_ROOT}" rev-parse --verify "HEAD^{commit}")
for SOURCE in \
  scripts/course_robot_e2e_gates.sh \
  main/tbot-server/scripts/course_mode_release_gate.py \
  main/tbot-server/scripts/course_mode_candidate_manifest.py
do
  [ -f "${REPOSITORY_ROOT}/${SOURCE}" ] && [ ! -L "${REPOSITORY_ROOT}/${SOURCE}" ] || {
    echo "bootstrap source is not a regular file: ${SOURCE}" >&2
    exit 1
  }
  COMMITTED_BLOB=$(trusted_git -C "${REPOSITORY_ROOT}" rev-parse "${HEAD_SHA}:${SOURCE}")
  WORKING_BLOB=$(trusted_git -C "${REPOSITORY_ROOT}" hash-object -- "${SOURCE}")
  [ "${COMMITTED_BLOB}" = "${WORKING_BLOB}" ] || {
    echo "bootstrap source does not match HEAD: ${SOURCE}" >&2
    exit 1
  }
  trusted_git -C "${REPOSITORY_ROOT}" diff --quiet "${HEAD_SHA}" -- "${SOURCE}" || {
    echo "bootstrap source metadata does not match HEAD: ${SOURCE}" >&2
    exit 1
  }
done
[ "$(trusted_git -C "${REPOSITORY_ROOT}" rev-parse --verify "HEAD^{commit}")" = "${HEAD_SHA}" ] || {
  echo "repository HEAD changed during bootstrap verification" >&2
  exit 1
}

if [ "$#" -eq 1 ] && [ "$1" = "--list-lanes" ]; then
  :
else
  [ "$#" -ge 2 ] || usage
  [ "$1" = "--candidate" ] || usage
fi

PYTHON=/opt/homebrew/bin/python3
[ -x "${PYTHON}" ] || PYTHON=/usr/local/bin/python3
[ -x "${PYTHON}" ] || PYTHON=/usr/bin/python3
[ -x "${PYTHON}" ] || { echo "trusted python3 is unavailable" >&2; exit 1; }

exec /usr/bin/env -i \
  PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin \
  HOME=/nonexistent LANG=C LC_ALL=C \
  COURSE_MODE_V2_TEST_DATABASE_URL="${COURSE_MODE_V2_TEST_DATABASE_URL-}" \
  COURSE_MODE_TEST_DATABASE_URL="${COURSE_MODE_TEST_DATABASE_URL-}" \
  DATABASE_URL="${DATABASE_URL-}" \
  COURSE_MODE_ADMIN_E2E_READY="${COURSE_MODE_ADMIN_E2E_READY-}" \
  "${PYTHON}" "${REPOSITORY_ROOT}/main/tbot-server/scripts/course_mode_release_gate.py" "$@"
