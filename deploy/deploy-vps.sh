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
REMOTE_ROOT_SET=0
DRY_RUN=0
BOOTSTRAP=0
RELEASE_ROOT="${PROJECT_DIR}/dist/deploy"
SMOKE_EXPECTED_WS_HOST=""
SMOKE_OTA_URL=""

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

url_host() {
  local url="$1"
  local rest="${url#*://}"
  printf '%s' "${rest%%/*}"
}

url_path() {
  local url="$1"
  local rest="${url#*://}"
  if [[ "${rest}" == */* ]]; then
    printf '/%s' "${rest#*/}"
  else
    printf '/'
  fi
}

is_quick_tunnel_host() {
  local host="$1"
  [[ "${host}" == "trycloudflare.com" || "${host}" == *.trycloudflare.com ]]
}

validate_public_endpoint_env() {
  [[ -n "${ENV_FILE}" ]] || return 0

  local env_remote_root websocket_url backend_url ws_host backend_path failed
  failed=0
  env_remote_root="$(env_value TBOT_REMOTE_ROOT "")"
  if [[ -n "${env_remote_root}" ]]; then
    if [[ "${REMOTE_ROOT_SET}" -eq 1 && "${env_remote_root}" != "${REMOTE_ROOT}" ]]; then
      die "TBOT_REMOTE_ROOT in env file conflicts with --remote-root"
    fi
    REMOTE_ROOT="${env_remote_root}"
  fi

  websocket_url="$(env_value TBOT_PUBLIC_WEBSOCKET_URL "")"
  backend_url="$(env_value TBOT_BACKEND_API_URL "")"

  if [[ -z "${websocket_url}" ]]; then
    printf 'error: TBOT_PUBLIC_WEBSOCKET_URL is required in env file\n' >&2
    failed=1
  fi
  if [[ -z "${backend_url}" ]]; then
    printf 'error: TBOT_BACKEND_API_URL is required in env file\n' >&2
    failed=1
  fi
  [[ "${failed}" -eq 0 ]] || exit 1

  if [[ "${websocket_url}" == *your-public-domain* ]]; then
    printf 'error: TBOT_PUBLIC_WEBSOCKET_URL still uses a placeholder value\n' >&2
    failed=1
  fi
  if [[ "${backend_url}" == *your-backend-api-domain* ]]; then
    printf 'error: TBOT_BACKEND_API_URL still uses a placeholder value\n' >&2
    failed=1
  fi
  [[ "${failed}" -eq 0 ]] || exit 1

  if [[ "${websocket_url}" != ws://* && "${websocket_url}" != wss://* ]]; then
    printf 'error: TBOT_PUBLIC_WEBSOCKET_URL must be a ws:// or wss:// URL\n' >&2
  fi
  if [[ "$(url_path "${websocket_url}")" != "/tbot/v1/" ]]; then
    printf 'error: TBOT_PUBLIC_WEBSOCKET_URL must use the /tbot/v1/ WebSocket path\n' >&2
  fi
  ws_host="$(url_host "${websocket_url}")"
  if is_quick_tunnel_host "${ws_host}"; then
    printf 'error: TBOT_PUBLIC_WEBSOCKET_URL must not use a trycloudflare quick tunnel\n' >&2
  fi

  if [[ "${backend_url}" != http://* && "${backend_url}" != https://* ]]; then
    printf 'error: TBOT_BACKEND_API_URL must be an http:// or https:// URL\n' >&2
  fi
  backend_path="$(url_path "${backend_url}")"
  if [[ "${backend_path}" != "/v1" && "${backend_path}" != "/v1/" && "${backend_path}" != /v1/* ]]; then
    printf 'error: TBOT_BACKEND_API_URL must include the /v1 route prefix\n' >&2
  fi

  if [[ "${websocket_url}" != ws://* && "${websocket_url}" != wss://* ]] || \
     [[ "$(url_path "${websocket_url}")" != "/tbot/v1/" ]] || \
     is_quick_tunnel_host "${ws_host}" || \
     [[ "${backend_url}" != http://* && "${backend_url}" != https://* ]] || \
     [[ "${backend_path}" != "/v1" && "${backend_path}" != "/v1/" && "${backend_path}" != /v1/* ]]; then
    exit 1
  fi

  SMOKE_EXPECTED_WS_HOST="${ws_host}"
  SMOKE_OTA_URL="https://${ws_host}/tbot/ota/"
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
      REMOTE_ROOT_SET=1
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

if [[ "${HOST}" == "160.187.240.56" && -z "${ENV_FILE}" ]]; then
  die "known Levcloud VPS deploy requires --env-file with TBOT_REMOTE_ROOT=/opt/tbot"
fi

validate_public_endpoint_env

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
  if [[ -n "${SMOKE_OTA_URL}" && -n "${SMOKE_EXPECTED_WS_HOST}" ]]; then
    "${SCRIPT_DIR}/smoke-vps.sh" --host "${HOST}" --port "${SMOKE_WS_PORT}" --http-port "${SMOKE_HTTP_PORT}" --ota-port "${SMOKE_OTA_PORT}" --ota-url "${SMOKE_OTA_URL}" --expected-ws-host "${SMOKE_EXPECTED_WS_HOST}"
  else
    "${SCRIPT_DIR}/smoke-vps.sh" --host "${HOST}" --port "${SMOKE_WS_PORT}" --http-port "${SMOKE_HTTP_PORT}" --ota-port "${SMOKE_OTA_PORT}"
  fi
else
  printf '[dry-run] skip smoke checks\n'
fi

printf 'Deployed release %s to %s@%s:%s\n' "${TAG}" "${USER_NAME}" "${HOST}" "${REMOTE_ROOT}"
