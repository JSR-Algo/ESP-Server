#!/usr/bin/env bash
set -euo pipefail

HOST=""
TCP_PORT="8000"
HTTP_PORT="8002"
OTA_PORT="8003"
TIMEOUT="5"
SCHEME="http"

usage() {
  cat <<'USAGE'
Usage: smoke-vps.sh --host <host> [options]

Check TBOT VPS public endpoints:
  HTTP http://host:8002/
  HTTP http://host:8003/tbot/ota/
  TCP  host:8000

Options:
  --host <host>            Required VPS host/IP.
  --port <port>            TCP WebSocket port (default: 8000).
  --http-port <port>       Admin HTTP port (default: 8002).
  --ota-port <port>        OTA HTTP port (default: 8003).
  --timeout <seconds>      Per-check timeout (default: 5).
  --scheme <http|https>    HTTP scheme (default: http).
  -h, --help               Show help.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

check_http() {
  local url="$1"
  printf 'Checking %s\n' "${url}"
  curl --fail --silent --show-error --location --max-time "${TIMEOUT}" --output /dev/null "${url}"
}

check_tcp() {
  local host="$1"
  local port="$2"
  printf 'Checking tcp://%s:%s\n' "${host}" "${port}"
  if command -v nc >/dev/null 2>&1; then
    nc -z -w "${TIMEOUT}" "${host}" "${port}"
  else
    bash -c ":</dev/tcp/${host}/${port}" >/dev/null 2>&1
  fi
}

while (($#)); do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || die "--host requires a value"
      HOST="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || die "--port requires a value"
      TCP_PORT="$2"
      shift 2
      ;;
    --http-port)
      [[ $# -ge 2 ]] || die "--http-port requires a value"
      HTTP_PORT="$2"
      shift 2
      ;;
    --ota-port)
      [[ $# -ge 2 ]] || die "--ota-port requires a value"
      OTA_PORT="$2"
      shift 2
      ;;
    --timeout)
      [[ $# -ge 2 ]] || die "--timeout requires a value"
      TIMEOUT="$2"
      shift 2
      ;;
    --scheme)
      [[ $# -ge 2 ]] || die "--scheme requires a value"
      SCHEME="$2"
      shift 2
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
need_cmd curl

check_http "${SCHEME}://${HOST}:${HTTP_PORT}/"
check_http "${SCHEME}://${HOST}:${OTA_PORT}/tbot/ota/"
check_tcp "${HOST}" "${TCP_PORT}"

echo "Checking health endpoints..."

# Java actuator health
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${SCHEME}://${HOST}:${HTTP_PORT}/tbot/actuator/health" 2>/dev/null || echo "000")
if [ "$HTTP_STATUS" = "200" ]; then
    echo "✓ Java actuator health: OK"
else
    echo "⚠ Java actuator health: HTTP $HTTP_STATUS (may not be configured yet)"
fi

# Python health
PYTHON_HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${SCHEME}://${HOST}:${OTA_PORT}/health" 2>/dev/null || echo "000")
if [ "$PYTHON_HEALTH_STATUS" = "200" ]; then
    echo "✓ Python health endpoint: OK"
else
    echo "⚠ Python health endpoint: HTTP $PYTHON_HEALTH_STATUS (may not be configured yet)"
fi

printf 'Smoke checks passed for %s\n' "${HOST}"
