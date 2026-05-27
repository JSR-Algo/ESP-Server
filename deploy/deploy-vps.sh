#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
HOST=""
USER_NAME=""
TAG=""
PORT="22"
KEY_FILE=""
ENV_FILE=""
REMOTE_ROOT="/opt/tbot"
DRY_RUN=0
BOOTSTRAP=0
RELEASE_ROOT="${PROJECT_DIR}/dist/deploy"

usage() {
  cat <<'USAGE'
Usage: deploy-vps.sh --host <host> --user <user> --tag <tag> [options]

Upload a packaged release and start it on a VPS. No builds run remotely.

Options:
  --host <host>            Required SSH host/IP.
  --user <user>            Required SSH user.
  --tag <tag>              Required release tag.
  --port <port>            SSH port (default: 22).
  --key <file>             SSH private key.
  --env-file <file>        Upload env file to /opt/tbot/.env.
  --release-root <dir>     Local release root (default: esp32-server/dist/deploy).
  --remote-root <dir>      Remote app root (default: /opt/tbot).
  --bootstrap              Create remote dirs and verify Docker/Compose.
  --dry-run                Print actions only; do not mutate remote host.
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

run_scp() {
  local src="$1"
  local dest="$2"
  local cmd=(scp -P "${PORT}" -o StrictHostKeyChecking=accept-new -r)
  if [[ -n "${KEY_FILE}" ]]; then
    cmd+=(-i "${KEY_FILE}")
  fi
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    printf '[dry-run] scp %s %s\n' "${src}" "${dest}"
    return 0
  fi
  if [[ -n "${SSH_PASSWORD:-}" && -z "${KEY_FILE}" && "$(command -v sshpass || true)" ]]; then
    sshpass -e "${cmd[@]}" "${src}" "${dest}"
  elif [[ -n "${SSH_PASSWORD:-}" && -z "${KEY_FILE}" ]] && command -v expect >/dev/null 2>&1; then
    run_with_password "${cmd[@]}" "${src}" "${dest}"
  else
    "${cmd[@]}" "${src}" "${dest}"
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
    --release-root)
      [[ $# -ge 2 ]] || die "--release-root requires a value"
      RELEASE_ROOT="$2"
      shift 2
      ;;
    --remote-root)
      [[ $# -ge 2 ]] || die "--remote-root requires a value"
      REMOTE_ROOT="$2"
      shift 2
      ;;
    --bootstrap)
      BOOTSTRAP=1
      shift
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
need_cmd scp

if [[ -n "${SSH_PASSWORD:-}" && -z "${KEY_FILE}" ]] && ! command -v sshpass >/dev/null 2>&1 && ! command -v expect >/dev/null 2>&1; then
  printf 'warning: SSH_PASSWORD set but neither sshpass nor expect was found; falling back to interactive SSH auth\n' >&2
fi

RELEASE_DIR="${RELEASE_ROOT}/${TAG}"
[[ -d "${RELEASE_DIR}" ]] || die "missing release dir: ${RELEASE_DIR}"
[[ -f "${RELEASE_DIR}/release.json" ]] || die "missing release.json in ${RELEASE_DIR}"
[[ -f "${RELEASE_DIR}/checksums.sha256" ]] || die "missing checksums.sha256 in ${RELEASE_DIR}"

REMOTE_RELEASES="${REMOTE_ROOT}/releases"
REMOTE_RELEASE="${REMOTE_RELEASES}/${TAG}"
REMOTE_Q="$(remote_quote "${REMOTE_ROOT}")"
REMOTE_RELEASES_Q="$(remote_quote "${REMOTE_RELEASES}")"
REMOTE_RELEASE_Q="$(remote_quote "${REMOTE_RELEASE}")"
SMOKE_WS_PORT="$(env_value TBOT_WS_PORT 8000)"
SMOKE_HTTP_PORT="$(env_value TBOT_ADMIN_PORT 8002)"
SMOKE_OTA_PORT="$(env_value TBOT_HTTP_PORT 8003)"

if [[ "${BOOTSTRAP}" -eq 1 ]]; then
  run_ssh "mkdir -p ${REMOTE_Q}/releases ${REMOTE_Q}/data ${REMOTE_Q}/models/SenseVoiceSmall ${REMOTE_Q}/mysql/data ${REMOTE_Q}/redis/data ${REMOTE_Q}/uploadfile && docker --version >/dev/null && (docker compose version >/dev/null 2>&1 || docker-compose version >/dev/null)"
fi

run_ssh "mkdir -p ${REMOTE_RELEASES_Q}"
run_ssh "rm -rf ${REMOTE_RELEASE_Q} && mkdir -p ${REMOTE_RELEASE_Q}"
run_scp "${RELEASE_DIR}/." "${USER_NAME}@${HOST}:${REMOTE_RELEASE}/"

if [[ -n "${ENV_FILE}" ]]; then
  run_scp "${ENV_FILE}" "${USER_NAME}@${HOST}:${REMOTE_ROOT}/.env"
else
  run_ssh "test -f ${REMOTE_Q}/.env || cp ${REMOTE_RELEASE_Q}/.env.example ${REMOTE_Q}/.env"
fi

run_ssh "cd ${REMOTE_RELEASE_Q} && sha256sum -c checksums.sha256 && for f in *.tar.gz; do gunzip -c \"\$f\" | docker load; done && ln -sfn ${REMOTE_RELEASE_Q} ${REMOTE_Q}/current && if docker compose version >/dev/null 2>&1; then docker compose --env-file ${REMOTE_Q}/.env -f ${REMOTE_Q}/current/docker-compose.prod.yml up -d && docker compose --env-file ${REMOTE_Q}/.env -f ${REMOTE_Q}/current/docker-compose.prod.yml ps; else docker-compose --env-file ${REMOTE_Q}/.env -f ${REMOTE_Q}/current/docker-compose.prod.yml up -d && docker-compose --env-file ${REMOTE_Q}/.env -f ${REMOTE_Q}/current/docker-compose.prod.yml ps; fi"

if [[ "${DRY_RUN}" -eq 0 ]]; then
  "${SCRIPT_DIR}/smoke-vps.sh" --host "${HOST}" --port "${SMOKE_WS_PORT}" --http-port "${SMOKE_HTTP_PORT}" --ota-port "${SMOKE_OTA_PORT}"
else
  printf '[dry-run] skip smoke checks\n'
fi

printf 'Deployed release %s to %s@%s:%s\n' "${TAG}" "${USER_NAME}" "${HOST}" "${REMOTE_ROOT}"
