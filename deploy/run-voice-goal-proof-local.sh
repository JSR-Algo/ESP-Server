#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${HOST:-}"
USER_NAME="${USER_NAME:-root}"
PORT="${PORT:-22}"
KEY_FILE="${KEY_FILE:-}"
TARGET="${TARGET:-28:84:85:85:1a:80}"
CLIENT="${CLIENT:-c29ce67a-3288-4c39-8544-bba97dab332b}"
DURATION_SEC="${DURATION_SEC:-1800}"
POLL_SEC="${POLL_SEC:-5}"
OUT_DIR="${OUT_DIR:-/tmp}"
INTERRUPT_PROMPT_VOICE="${INTERRUPT_PROMPT_VOICE:-Linh}"
INTERRUPT_PROMPT_TEXT="${INTERRUPT_PROMPT_TEXT:-kẹo}"
CHILD_PROMPT_VOICE="${CHILD_PROMPT_VOICE:-Samantha}"
CHILD_PROMPT_TEXT="${CHILD_PROMPT_TEXT:-darn darn darn}"
CHILD_RESPONSE_TEXT="${CHILD_RESPONSE_TEXT:-barn}"
POST_LESSON_PROMPT_VOICE="${POST_LESSON_PROMPT_VOICE:-Linh}"
POST_LESSON_PROMPT_TEXT="${POST_LESSON_PROMPT_TEXT:-bạn nghe thấy con không}"
EXPECTED_TRANSCRIPT="${EXPECTED_TRANSCRIPT:-${INTERRUPT_PROMPT_TEXT}}"

usage() {
  cat <<'USAGE'
Usage: run-voice-goal-proof-local.sh --host <host> [options]

Owner-gated physical Google Live proof runner. It waits for the exact target
device/client on one Python replica, nudges that replica by direct container IP,
plays local voice prompts, captures owner logs, and runs strict audit.

Options:
  --host <host>              SSH host/IP.
  --user <user>              SSH user (default: root).
  --port <port>              SSH port (default: 22).
  --key <file>               SSH private key.
  --target <mac>             Target device MAC.
  --client <uuid>            Target client UUID.
  --duration-sec <seconds>   Wait window (default: 1800).
  --poll-sec <seconds>       Poll interval (default: 5).
  --out-dir <dir>            Local artifact directory (default: /tmp).
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

ssh_cmd() {
  local cmd=(ssh -p "${PORT}" -o StrictHostKeyChecking=accept-new)
  if [[ -n "${KEY_FILE}" ]]; then
    cmd+=(-i "${KEY_FILE}")
  fi
  cmd+=("${USER_NAME}@${HOST}" "$@")
  "${cmd[@]}"
}

owner_probe() {
  ssh_cmd python3 - "${TARGET}" "${CLIENT}" <<'PY'
import json
import subprocess
import sys
import urllib.request

target = sys.argv[1].lower()
client = sys.argv[2]

for container in ("current-tbot-esp32-server-1", "current-tbot-esp32-server-2"):
    owner_ip = subprocess.check_output(
        [
            "docker",
            "inspect",
            "-f",
            "{{range.NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container,
        ],
        text=True,
    ).strip()
    try:
        request = urllib.request.Request(
            f"http://{owner_ip}:8003/internal/lesson-runtime/metrics",
            headers={"device-id": target},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            metrics = json.loads(response.read().decode())
    except Exception:
        continue

    found = False
    for device in metrics.get("devices") or []:
        if not isinstance(device, dict):
            continue
        if (
            str(device.get("deviceId", "")).strip().lower() == target
            and str(device.get("clientId", "")).strip() == client
        ):
            found = True
            break
    if not found:
        continue

    print(f"{container} {owner_ip}")
    raise SystemExit(0)

raise SystemExit(1)
PY
}

remote_nudge() {
  local owner_ip="$1"
  ssh_cmd python3 - "${owner_ip}" "${TARGET}" <<'PY'
from pathlib import Path
import sys
import urllib.request

owner_ip, target = sys.argv[1], sys.argv[2]
secret = ""
for raw in Path("/opt/tbot/.env").read_text().splitlines():
    if raw.startswith("TBOT_DEVICE_MINT_SECRET="):
        secret = raw.split("=", 1)[1].strip().strip('"').strip("'")
        break
if not secret:
    print('{"error":"missing-secret"}')
    raise SystemExit(2)

request = urllib.request.Request(
    f"http://{owner_ip}:8003/internal/devices/{target}/lesson-nudge",
    method="POST",
    headers={"X-Mint-Secret": secret},
)
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.read().decode())
PY
}

remote_child_response() {
  local owner_ip="$1" text="$2"
  ssh_cmd python3 - "${owner_ip}" "${TARGET}" "${text}" <<'PY'
from pathlib import Path
import json
import sys
import urllib.request

owner_ip, target, text = sys.argv[1], sys.argv[2], sys.argv[3]
secret = ""
for raw in Path("/opt/tbot/.env").read_text().splitlines():
    if raw.startswith("TBOT_DEVICE_MINT_SECRET="):
        secret = raw.split("=", 1)[1].strip().strip('"').strip("'")
        break
if not secret:
    print('{"error":"missing-secret"}')
    raise SystemExit(2)

body = json.dumps({"text": text}).encode()
request = urllib.request.Request(
    f"http://{owner_ip}:8003/internal/devices/{target}/lesson-child-response",
    data=body,
    method="POST",
    headers={"X-Mint-Secret": secret, "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=10) as response:
    print(response.read().decode())
PY
}

first_audio_seen() {
  local owner="$1" since="$2"
  ssh_cmd python3 - "${owner}" "${since}" <<'PY'
import re
import subprocess
import sys

owner, since = sys.argv[1], sys.argv[2]
text = subprocess.run(
    ["docker", "logs", "--since", since, "--tail", "2000", owner],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    errors="replace",
).stdout
pattern = re.compile(
    r"Google Live (?:first_audio_out_latency_ms=[\d.]+|turn_latency_ms=[\d.]+ phase=first_audio_out)"
)
raise SystemExit(0 if pattern.search(text) else 1)
PY
}

child_window_seen() {
  local owner="$1" since="$2"
  ssh_cmd python3 - "${owner}" "${since}" <<'PY'
import subprocess
import sys

owner, since = sys.argv[1], sys.argv[2]
text = subprocess.run(
    ["docker", "logs", "--since", since, "--tail", "4000", owner],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    errors="replace",
).stdout
markers = (
    "Google Live lesson_child_response_window_open",
)
raise SystemExit(0 if any(marker in text for marker in markers) else 1)
PY
}

child_window_seen_count() {
  local owner="$1" since="$2"
  ssh_cmd python3 - "${owner}" "${since}" <<'PY'
import subprocess
import sys

owner, since = sys.argv[1], sys.argv[2]
text = subprocess.run(
    ["docker", "logs", "--since", since, "--tail", "4000", owner],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    errors="replace",
).stdout
markers = (
    "Google Live lesson_child_response_window_open",
)
print(sum(text.count(marker) for marker in markers))
PY
}

lesson_completed_seen() {
  local owner="$1" since="$2"
  ssh_cmd python3 - "${owner}" "${since}" <<'PY'
import re
import subprocess
import sys

owner, since = sys.argv[1], sys.argv[2]
text = subprocess.run(
    ["docker", "logs", "--since", since, "--tail", "5000", owner],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    errors="replace",
).stdout
raise SystemExit(0 if re.search(r"lesson_completed|stepsCompleted=\d+", text) else 1)
PY
}

capture_logs() {
  local owner="$1" since="$2"
  ssh_cmd python3 - "${owner}" "${since}" "${TARGET}" "${CLIENT}" <<'PY'
import subprocess
import sys

owner, since, target, client = sys.argv[1:5]
recent = subprocess.run(
    ["docker", "logs", "--since", "90m", "--tail", "20000", owner],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    errors="replace",
).stdout.splitlines()
headers = [
    line for line in recent
    if "Headers:" in line and target.lower() in line.lower() and client in line
]
if headers:
    print(headers[-1])

run = subprocess.run(
    ["docker", "logs", "--since", since, "--tail", "8000", owner],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    errors="replace",
)
print(run.stdout, end="")
PY
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --user) USER_NAME="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --key) KEY_FILE="${2:-}"; shift 2 ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --client) CLIENT="${2:-}"; shift 2 ;;
    --duration-sec) DURATION_SEC="${2:-}"; shift 2 ;;
    --poll-sec) POLL_SEC="${2:-}"; shift 2 ;;
    --out-dir) OUT_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

[[ -n "${HOST}" ]] || die "--host is required"
[[ -z "${KEY_FILE}" || -r "${KEY_FILE}" ]] || die "cannot read key file: ${KEY_FILE}"
need_cmd ssh
need_cmd python3
need_cmd say

mkdir -p "${OUT_DIR}"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-voice-proof"
log_path="${OUT_DIR}/tbot_prod_voice_${run_id}.log"
audit_path="${OUT_DIR}/tbot_physical_audit_${run_id}.txt"
printf 'run_id=%s\nlog=%s\naudit=%s\n' "${run_id}" "${log_path}" "${audit_path}"

owner_line=""
deadline=$(( $(date +%s) + DURATION_SEC ))
poll=0
while [[ "$(date +%s)" -lt "${deadline}" ]]; do
  poll=$((poll + 1))
  if owner_line="$(owner_probe 2>/dev/null)"; then
    break
  fi
  if (( poll == 1 || poll % 6 == 0 )); then
    printf 'poll=%s target_offline_or_identity_gate_closed\n' "${poll}"
  fi
  sleep "${POLL_SEC}"
done

if [[ -z "${owner_line}" ]]; then
  printf 'NO_TARGET_OWNER_WITH_EXPECTED_CLIENT\n' >&2
  exit 20
fi

read -r owner owner_ip <<<"${owner_line}"
printf 'target_owner owner=%s owner_ip=%s\n' "${owner}" "${owner_ip}"
sleep 5
since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
nudge_body="$(remote_nudge "${owner_ip}")"
printf 'remote_nudge_body=%s\n' "${nudge_body}"
python3 - "${nudge_body}" <<'PY'
import json
import sys

body = json.loads(sys.argv[1])
if not body.get("data", {}).get("nudged"):
    raise SystemExit(1)
PY

(
  for _ in $(seq 1 80); do
    if first_audio_seen "${owner}" "${since}" >/dev/null 2>&1; then
      printf 'first_audio_seen_local_prompt=interrupt\n'
      say -v "${INTERRUPT_PROMPT_VOICE}" "${INTERRUPT_PROMPT_TEXT}"
      sleep 0.25
      say -v "${INTERRUPT_PROMPT_VOICE}" "${INTERRUPT_PROMPT_TEXT}"
      exit 0
    fi
    sleep 0.25
  done
  printf 'first_audio_watch_timeout\n'
) &
watch_pid=$!

(
  sleep 1.2
  say -v "${INTERRUPT_PROMPT_VOICE}" "${INTERRUPT_PROMPT_TEXT}"
  sleep 0.3
  say -v "${INTERRUPT_PROMPT_VOICE}" "${INTERRUPT_PROMPT_TEXT}"
) &
early_pid=$!

(
  last_child_window_count=0
  child_window_prompted=0
  for _ in $(seq 1 320); do
    if lesson_completed_seen "${owner}" "${since}" >/dev/null 2>&1; then
      printf 'child_window_watch_complete count=%s\n' "${last_child_window_count}"
      exit 0
    fi
    child_count="$(child_window_seen_count "${owner}" "${since}" 2>/dev/null || printf '0')"
    if [[ "${child_count}" =~ ^[0-9]+$ ]] && (( child_count > last_child_window_count )); then
      last_child_window_count="${child_count}"
      child_window_prompted=1
      printf 'child_window_seen_local_prompt=child count=%s\n' "${child_count}"
      remote_child_response "${owner_ip}" "${CHILD_RESPONSE_TEXT}"
    fi
    sleep 0.25
  done
  if (( child_window_prompted == 0 )); then
    printf 'child_window_watch_timeout\n'
  else
    printf 'child_window_watch_complete count=%s\n' "${last_child_window_count}"
  fi
) &
child_pid=$!

(
  for _ in $(seq 1 360); do
    if lesson_completed_seen "${owner}" "${since}" >/dev/null 2>&1; then
      printf 'lesson_completed_seen_local_prompt=post_lesson\n'
      sleep 6
      say -v "${POST_LESSON_PROMPT_VOICE}" "${POST_LESSON_PROMPT_TEXT}"
      sleep 0.5
      say -v "${POST_LESSON_PROMPT_VOICE}" "${POST_LESSON_PROMPT_TEXT}"
      exit 0
    fi
    sleep 0.25
  done
  printf 'lesson_completed_watch_timeout\n'
) &
post_lesson_pid=$!

sleep 100
wait "${watch_pid}" 2>/dev/null || true
wait "${early_pid}" 2>/dev/null || true
wait "${child_pid}" 2>/dev/null || true
wait "${post_lesson_pid}" 2>/dev/null || true

capture_logs "${owner}" "${since}" > "${log_path}"
printf 'captured_log=%s bytes=%s\n' "${log_path}" "$(wc -c < "${log_path}")"
python3 "${PROJECT_DIR}/main/tbot-server/scripts/physical_smoke_audit.py" "${log_path}" \
  --device-id "${TARGET}" \
  --client-id "${CLIENT}" \
  --production-output-safe-strict \
  --min-interrupts 1 \
  | tee "${audit_path}"
printf 'audit=%s\n' "${audit_path}"
