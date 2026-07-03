#!/usr/bin/env bash
set -euo pipefail

HOST=""
ADMIN_URL=""
TCP_PORT="8000"
HTTP_PORT="8002"
OTA_PORT="8003"
TIMEOUT="5"
SCHEME="http"
PUBLIC_OTA_URL=""
EXPECTED_WS_HOST=""

usage() {
  cat <<'USAGE'
Usage: smoke-vps.sh --host <host> [options]
       smoke-vps.sh --admin-url <url> --ota-url <url> --expected-ws-host <host>

Check TBOT VPS public endpoints:
  HTTP http://host:8002/
  HTTP http://host:8003/tbot/ota/
  TCP  host:8000
  HTTPS admin/OTA domains when --admin-url and --ota-url are provided

Options:
  --host <host>            VPS host/IP for direct port checks.
  --admin-url <url>        Full admin URL for reverse-proxy checks.
  --port <port>            TCP WebSocket port (default: 8000).
  --http-port <port>       Admin HTTP port (default: 8002).
  --ota-port <port>        OTA HTTP port (default: 8003).
  --timeout <seconds>      Per-check timeout (default: 5).
  --scheme <http|https>    HTTP scheme (default: http).
  --ota-url <url>          Public OTA URL to fetch and validate.
  --expected-ws-host <h>   Expected host in the OTA websocket.url payload.
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

url_host() {
  local url="$1"
  local rest="${url#*://}"
  printf '%s' "${rest%%/*}"
}

is_quick_tunnel_host() {
  local host="$1"
  [[ "${host}" == "trycloudflare.com" || "${host}" == *.trycloudflare.com ]]
}

check_public_ota_payload() {
  local output
  output="$(mktemp)"
  trap 'rm -f "${output}"' RETURN

  local ota_host
  ota_host="$(url_host "${PUBLIC_OTA_URL}")"
  if is_quick_tunnel_host "${ota_host}" || is_quick_tunnel_host "${EXPECTED_WS_HOST}"; then
    die "public OTA/WebSocket endpoint must not use a trycloudflare quick tunnel"
  fi

  printf 'Checking public OTA payload %s\n' "${PUBLIC_OTA_URL}"
  curl --fail --silent --show-error --location --max-time "${TIMEOUT}" --output "${output}" "${PUBLIC_OTA_URL}"
  python3 - "${output}" "${EXPECTED_WS_HOST}" <<'PY'
import json
import re
import sys
from urllib.parse import urlparse

path, expected_host = sys.argv[1], sys.argv[2]
with open(path, 'r', encoding='utf-8') as fh:
    raw = fh.read()

ws_url = None
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    match = re.search(r'wss?://[^\s"<>]+', raw)
    if match:
        ws_url = match.group(0)
else:
    api_url = payload.get('api_url')
    if not isinstance(api_url, str) or not api_url.strip():
        print('OTA response missing api_url', file=sys.stderr)
        sys.exit(1)

    websocket = payload.get('websocket') or {}
    ws_url = websocket.get('url')

if not isinstance(ws_url, str) or not ws_url.strip():
    print('OTA response missing websocket URL', file=sys.stderr)
    sys.exit(1)

parsed = urlparse(ws_url)
if parsed.hostname != expected_host:
    print(f'OTA websocket host {parsed.hostname or ""} does not match expected host {expected_host}', file=sys.stderr)
    sys.exit(1)
if parsed.path != '/tbot/v1/':
    print('OTA websocket.url must use /tbot/v1/', file=sys.stderr)
    sys.exit(1)
PY
}

while (($#)); do
  case "$1" in
    --host)
      [[ $# -ge 2 ]] || die "--host requires a value"
      HOST="$2"
      shift 2
      ;;
    --admin-url)
      [[ $# -ge 2 ]] || die "--admin-url requires a value"
      ADMIN_URL="$2"
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
    --ota-url)
      [[ $# -ge 2 ]] || die "--ota-url requires a value"
      PUBLIC_OTA_URL="$2"
      shift 2
      ;;
    --expected-ws-host)
      [[ $# -ge 2 ]] || die "--expected-ws-host requires a value"
      EXPECTED_WS_HOST="$2"
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

[[ -n "${HOST}" || -n "${ADMIN_URL}" ]] || die "--host or --admin-url is required"
need_cmd curl
if [[ -n "${PUBLIC_OTA_URL}" || -n "${EXPECTED_WS_HOST}" ]]; then
  [[ -n "${PUBLIC_OTA_URL}" ]] || die "--ota-url is required when --expected-ws-host is set"
  [[ -n "${EXPECTED_WS_HOST}" ]] || die "--expected-ws-host is required when --ota-url is set"
  need_cmd python3
fi

if [[ -n "${ADMIN_URL}" ]]; then
  check_http "${ADMIN_URL}"
else
  check_http "${SCHEME}://${HOST}:${HTTP_PORT}/"
fi

if [[ -n "${HOST}" ]]; then
  check_http "${SCHEME}://${HOST}:${OTA_PORT}/tbot/ota/"
  check_tcp "${HOST}" "${TCP_PORT}"
fi

if [[ -n "${PUBLIC_OTA_URL}" ]]; then
  check_public_ota_payload
fi

printf 'Smoke checks passed\n'
