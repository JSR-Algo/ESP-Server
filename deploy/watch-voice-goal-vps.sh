#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

HOST="${HOST:-}"
USER_NAME="${USER_NAME:-}"
PORT="${PORT:-22}"
KEY_FILE="${KEY_FILE:-}"
TARGET="${TARGET:-28:84:85:85:1a:80}"
CLIENT="${CLIENT:-c29ce67a-3288-4c39-8544-bba97dab332b}"
HEAD_TAG="${HEAD_TAG:-$(git -C "${PROJECT_DIR}/main/tbot-server" rev-parse --short=8 HEAD 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)}"
DURATION_SEC="${DURATION_SEC:-43200}"
WATCH_DIR="${WATCH_DIR:-/opt/tbot/voice_goal_watch}"
REMOTE_WORKER="${REMOTE_WORKER:-0}"

usage() {
  cat <<'USAGE'
Usage: watch-voice-goal-vps.sh --host <host> --user <user> [options]

Install and start a passive VPS watcher for the physical voice-flow goal.

Options:
  --host <host>            SSH host/IP.
  --user <user>            SSH user.
  --port <port>            SSH port (default: 22).
  --key <file>             SSH private key.
  --target <mac>           Target device MAC.
  --client <uuid>          Target client UUID.
  --head-tag <tag>         Source/deploy marker for filenames.
  --duration-sec <seconds> Watch duration (default: 43200).
  --watch-dir <dir>        Remote watch dir (default: /opt/tbot/voice_goal_watch).
  --remote-worker          Internal: run the watcher on the VPS.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

remote_quote() {
  printf '%q' "$1"
}

ssh_base_cmd() {
  local cmd=(ssh -p "${PORT}" -o StrictHostKeyChecking=accept-new)
  if [[ -n "${KEY_FILE}" ]]; then
    cmd+=(-i "${KEY_FILE}")
  fi
  printf '%s\0' "${cmd[@]}"
}

scp_base_cmd() {
  local cmd=(scp -P "${PORT}" -o StrictHostKeyChecking=accept-new)
  if [[ -n "${KEY_FILE}" ]]; then
    cmd+=(-i "${KEY_FILE}")
  fi
  printf '%s\0' "${cmd[@]}"
}

run_ssh() {
  local cmd=()
  while IFS= read -r -d '' part; do
    cmd+=("${part}")
  done < <(ssh_base_cmd)
  cmd+=("${USER_NAME}@${HOST}" "$@")
  "${cmd[@]}"
}

run_scp() {
  local src="$1"
  local dest="$2"
  local cmd=()
  while IFS= read -r -d '' part; do
    cmd+=("${part}")
  done < <(scp_base_cmd)
  cmd+=("${src}" "${USER_NAME}@${HOST}:${dest}")
  "${cmd[@]}"
}

redact_stream() {
  sed -E 's/(token|authorization|api[_-]?key|secret|password)[=:][^ ]+/REDACTED/Ig'
}

run_remote_worker() {
  mkdir -p "${WATCH_DIR}"

  local run_id summary raw pidfile end pattern
  run_id="watch-$(date -u +%Y%m%dT%H%M%SZ)-continuous-head-${HEAD_TAG}"
  summary="${WATCH_DIR}/${run_id}.summary.log"
  raw="${WATCH_DIR}/${run_id}.raw.log"
  pidfile="${WATCH_DIR}/watcher-continuous-head-${HEAD_TAG}.pid"
  printf '%s\n' "$$" > "${pidfile}"

  {
    printf 'run_id=%s\n' "${run_id}"
    printf 'target=%s client=%s head=%s duration_sec=%s started=%s\n' \
      "${TARGET}" "${CLIENT}" "${HEAD_TAG}" "${DURATION_SEC}" "$(date -Is)"
    grep -E '^TBOT_SERVER_IMAGE=' /opt/tbot/.env 2>/dev/null || true
    docker ps --filter name=current-tbot-esp32-server --format '{{.Names}} {{.Image}} {{.Status}}' 2>/dev/null || true
  } >> "${summary}"

  pattern="Headers:|Client disconnected|${TARGET}|${CLIENT}|Google Live|wake_transcript_only|wake_listening_feedback|Hi ESP|high speed|lesson_start_intent|user_audio_window_open|user_audio_window_expired|window_ms=15000|input_audio_diag|audio_decision|transcript source=user|input_finalized|tts_stop_sent|echo_suppressed|suppress_echo|Traceback|ERROR|WARNING|timeout"

  follow_container() {
    local container="$1"
    while true; do
      if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "${container}"; then
        printf '%s container_follow_start=%s\n' "$(date -Is)" "${container}" >> "${summary}"
        docker logs --since 30s --follow "${container}" 2>&1 \
          | redact_stream \
          | grep --line-buffered -E "${pattern}" \
          | while IFS= read -r line; do
              printf '%s container=%s %s\n' "$(date -Is)" "${container}" "${line}"
            done >> "${raw}" || true
        printf '%s container_follow_end=%s\n' "$(date -Is)" "${container}" >> "${summary}"
      fi
      sleep 5
    done
  }

  follow_container current-tbot-esp32-server-1 &
  local tail_pid_1=$!
  follow_container current-tbot-esp32-server-2 &
  local tail_pid_2=$!

  cleanup() {
    kill "${tail_pid_1}" "${tail_pid_2}" 2>/dev/null || true
  }
  trap cleanup INT TERM EXIT

  end=$(( $(date +%s) + DURATION_SEC ))
  while [[ "$(date +%s)" -lt "${end}" ]]; do
    local ts metrics snap
    ts="$(date -Is)"
    metrics="$(curl -sS --max-time 8 -H "device-id: ${TARGET}" http://127.0.0.1:8003/internal/lesson-runtime/metrics 2>/dev/null || true)"
    printf '%s metrics=%s\n' "${ts}" "${metrics}" >> "${summary}"
    if printf '%s' "${metrics}" | grep -q "${TARGET}"; then
      snap="${WATCH_DIR}/${run_id}-target-${ts//[:+]/_}.log"
      {
        printf 'snapshot=%s target=%s client=%s\n' "${ts}" "${TARGET}" "${CLIENT}"
        grep -E '^TBOT_SERVER_IMAGE=' /opt/tbot/.env 2>/dev/null || true
        printf 'metrics=%s\n' "${metrics}"
        docker ps --filter name=current-tbot-esp32-server --format '{{.Names}} {{.Image}} {{.Status}}' 2>/dev/null || true
        printf 'recent_raw_log=%s\n' "${raw}"
        tail -500 "${raw}" 2>/dev/null || true
      } > "${snap}"
    fi
    sleep 15
  done

  printf 'finished=%s raw=%s summary=%s\n' "$(date -Is)" "${raw}" "${summary}" >> "${summary}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="${2:-}"; shift 2 ;;
    --user) USER_NAME="${2:-}"; shift 2 ;;
    --port) PORT="${2:-}"; shift 2 ;;
    --key) KEY_FILE="${2:-}"; shift 2 ;;
    --target) TARGET="${2:-}"; shift 2 ;;
    --client) CLIENT="${2:-}"; shift 2 ;;
    --head-tag) HEAD_TAG="${2:-}"; shift 2 ;;
    --duration-sec) DURATION_SEC="${2:-}"; shift 2 ;;
    --watch-dir) WATCH_DIR="${2:-}"; shift 2 ;;
    --remote-worker) REMOTE_WORKER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

if [[ "${REMOTE_WORKER}" -eq 1 ]]; then
  run_remote_worker
  exit 0
fi

[[ -n "${HOST}" ]] || die "--host is required"
[[ -n "${USER_NAME}" ]] || die "--user is required"

remote_script="${WATCH_DIR}/watch-voice-goal-vps-${HEAD_TAG}.sh"
remote_pidfile="${WATCH_DIR}/watcher-continuous-head-${HEAD_TAG}.pid"

run_ssh "mkdir -p $(remote_quote "${WATCH_DIR}")"
run_scp "$0" "${remote_script}"
run_ssh "chmod +x $(remote_quote "${remote_script}")"
run_ssh "if [ -s $(remote_quote "${remote_pidfile}") ] && ps -p \$(cat $(remote_quote "${remote_pidfile}")) >/dev/null 2>&1; then kill \$(cat $(remote_quote "${remote_pidfile}")) 2>/dev/null || true; fi"
run_ssh "TARGET=$(remote_quote "${TARGET}") CLIENT=$(remote_quote "${CLIENT}") HEAD_TAG=$(remote_quote "${HEAD_TAG}") DURATION_SEC=$(remote_quote "${DURATION_SEC}") WATCH_DIR=$(remote_quote "${WATCH_DIR}") nohup env TARGET=$(remote_quote "${TARGET}") CLIENT=$(remote_quote "${CLIENT}") HEAD_TAG=$(remote_quote "${HEAD_TAG}") DURATION_SEC=$(remote_quote "${DURATION_SEC}") WATCH_DIR=$(remote_quote "${WATCH_DIR}") $(remote_quote "${remote_script}") --remote-worker >> $(remote_quote "${WATCH_DIR}/launcher-${HEAD_TAG}.log") 2>&1 &"
run_ssh "for _ in 1 2 3 4 5; do [ -s $(remote_quote "${remote_pidfile}") ] && break; sleep 1; done; pid=\$(cat $(remote_quote "${remote_pidfile}") 2>/dev/null || true); [ -n \"\$pid\" ]; ps -p \"\$pid\" -o pid=,etime=,cmd=; ls -t $(remote_quote "${WATCH_DIR}")/watch-*continuous-head-${HEAD_TAG}.*.log | head -5"
