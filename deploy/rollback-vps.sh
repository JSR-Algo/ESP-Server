#!/usr/bin/env bash
set -euo pipefail

HOST=""
USER_NAME=""
TAG=""
PORT="22"
KEY_FILE=""
ENV_FILE=""
REMOTE_ROOT="/opt/tbot"
REMOTE_ROOT_SET=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
Usage: rollback-vps.sh --host <host> --user <user> --tag <tag> [options]

Switch /opt/tbot/current to an existing release and restart compose.

Options:
  --host <host>            Required SSH host/IP.
  --user <user>            Required SSH user.
  --tag <tag>              Required release tag to restore.
  --port <port>            SSH port (default: 22).
  --key <file>             SSH private key.
  --env-file <file>        Read TBOT_REMOTE_ROOT from the same env file compose uses.
  --remote-root <dir>      Remote app root (default: /opt/tbot).
  --dry-run                Print actions only.
  -h, --help               Show help.

Password auth: set SSH_PASSWORD. If sshpass is installed, it will be used.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

run_with_password() {
  command -v expect >/dev/null 2>&1 || return 127
  expect -f - -- "$@" <<'EXPECT'
    set timeout -1
    set password $env(SSH_PASSWORD)
    set cmd [lrange $argv 0 end]
    spawn {*}$cmd
    expect {
      -re "(?i)are you sure you want to continue connecting" {
        send "yes\r"
        exp_continue
      }
      -re "(?i)password:" {
        send "$password\r"
        exp_continue
      }
      eof {
        catch wait result
        exit [lindex $result 3]
      }
    }
EXPECT
}

run_ssh() {
  local target="${USER_NAME}@${HOST}"
  local cmd=(ssh -p "${PORT}" -o StrictHostKeyChecking=accept-new)
  if [[ -n "${KEY_FILE}" ]]; then
    cmd+=(-i "${KEY_FILE}")
  fi
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '[dry-run] ssh %s %q\n' "${target}" "$*"
    return 0
  fi
  if [[ -n "${SSH_PASSWORD:-}" && -z "${KEY_FILE}" && "$(command -v sshpass || true)" ]]; then
    sshpass -e "${cmd[@]}" "${target}" "$@"
  elif [[ -n "${SSH_PASSWORD:-}" && -z "${KEY_FILE}" ]] && command -v expect >/dev/null 2>&1; then
    run_with_password "${cmd[@]}" "${target}" "$@"
  else
    "${cmd[@]}" "${target}" "$@"
  fi
}

remote_quote() {
  printf '%q' "$1"
}

env_value() {
  local key="$1"
  local fallback="$2"
  local value=""
  if [[ -n "${ENV_FILE}" && -r "${ENV_FILE}" ]]; then
    value="$(awk -F= -v key="${key}" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "${ENV_FILE}")"
    value="${value%\"}"
    value="${value#\"}"
    value="${value%\'}"
    value="${value#\'}"
  fi
  printf '%s' "${value:-${fallback}}"
}

apply_env_remote_root() {
  [[ -n "${ENV_FILE}" ]] || return 0
  local env_remote_root
  env_remote_root="$(env_value TBOT_REMOTE_ROOT "")"
  [[ -n "${env_remote_root}" ]] || return 0
  if [[ "${REMOTE_ROOT_SET}" -eq 1 && "${env_remote_root}" != "${REMOTE_ROOT}" ]]; then
    die "TBOT_REMOTE_ROOT in env file conflicts with --remote-root"
  fi
  REMOTE_ROOT="${env_remote_root}"
}

while (($#)); do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || die "--host requires a value"
      HOST="$2"
      shift 2
      ;;
    --user)
      [[ $# -ge 2 ]] || die "--user requires a value"
      USER_NAME="$2"
      shift 2
      ;;
    --tag)
      [[ $# -ge 2 ]] || die "--tag requires a value"
      TAG="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || die "--port requires a value"
      PORT="$2"
      shift 2
      ;;
    --key)
      [[ $# -ge 2 ]] || die "--key requires a value"
      KEY_FILE="$2"
      shift 2
      ;;
    --env-file)
      [[ $# -ge 2 ]] || die "--env-file requires a value"
      ENV_FILE="$2"
      shift 2
      ;;
    --remote-root)
      [[ $# -ge 2 ]] || die "--remote-root requires a value"
      REMOTE_ROOT="$2"
      REMOTE_ROOT_SET=1
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ -n "${HOST}" ]] || die "--host is required"
[[ -n "${USER_NAME}" ]] || die "--user is required"
[[ -n "${TAG}" ]] || die "--tag is required"
[[ -z "${KEY_FILE}" || -r "${KEY_FILE}" ]] || die "cannot read key file: ${KEY_FILE}"
[[ -z "${ENV_FILE}" || -r "${ENV_FILE}" ]] || die "cannot read env file: ${ENV_FILE}"
need_cmd ssh

apply_env_remote_root

if [[ -n "${SSH_PASSWORD:-}" && -z "${KEY_FILE}" ]] && ! command -v sshpass >/dev/null 2>&1 && ! command -v expect >/dev/null 2>&1; then
  printf 'warning: SSH_PASSWORD set but neither sshpass nor expect was found; falling back to interactive SSH auth\n' >&2
fi

REMOTE_RELEASE="${REMOTE_ROOT}/releases/${TAG}"
REMOTE_Q="$(remote_quote "${REMOTE_ROOT}")"
REMOTE_RELEASE_Q="$(remote_quote "${REMOTE_RELEASE}")"

run_ssh "test -d ${REMOTE_RELEASE_Q} && test -f ${REMOTE_RELEASE_Q}/docker-compose.prod.yml && ln -sfn ${REMOTE_RELEASE_Q} ${REMOTE_Q}/current && if docker compose version >/dev/null 2>&1; then docker compose --env-file ${REMOTE_Q}/.env -f ${REMOTE_Q}/current/docker-compose.prod.yml up -d; else docker-compose --env-file ${REMOTE_Q}/.env -f ${REMOTE_Q}/current/docker-compose.prod.yml up -d; fi"

printf 'Rolled back %s to release %s\n' "${HOST}" "${TAG}"
