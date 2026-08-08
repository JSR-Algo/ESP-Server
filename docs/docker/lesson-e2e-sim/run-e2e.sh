#!/usr/bin/env bash
# T5.3 — run ONE full simulated lesson end to end and verify it.
#
# `up.sh` brings the stack up. This script is the run itself, and it exists because
# every earlier T5.3 session re-derived the same four manual steps by hand (mint an
# admin session, clear the previous assignment, POST the assignment, capture BOTH log
# streams over the right window) and one of them — the log window — was silently wrong:
# `docker logs --since <RFC3339>` is interpreted in the daemon's local time, so a UTC
# timestamp captured the WRONG window and the verifier scored a run it had not seen.
# Unix-epoch `--since` is unambiguous; that is what this uses.
#
# Usage:
#   ./run-e2e.sh [--label NAME] [--out DIR] [--no-assign] [-- <extra sim_device.py args>]
#
# Exit code is the VERIFIER's, so this is usable as a gate.
set -euo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ESP_REPO="$(cd -- "${HERE}/../../.." && pwd)"
# Walking up finds the umbrella from the canonical checkout and from .worktrees/, but NOT
# from a throwaway worktree under /tmp — which is exactly where Ship-checklist step 4 says
# to re-test main from (F-T63-09). TBOT_ROOT is therefore overridable.
TBOT_ROOT="${TBOT_ROOT:-${ESP_REPO}}"
while [[ "${TBOT_ROOT}" != "/" && ! ( -d "${TBOT_ROOT}/tbot-backend" && -d "${TBOT_ROOT}/robot" ) ]]; do
  TBOT_ROOT="$(dirname "${TBOT_ROOT}")"
done
if [[ "${TBOT_ROOT}" == "/" ]]; then
  echo "[run] FATAL: cannot locate the TBOT root (a dir holding tbot-backend/ and robot/)." >&2
  echo "[run]        Running from a detached worktree? Pass it explicitly:" >&2
  echo "[run]          TBOT_ROOT=/Users/…/TBOT $0 …" >&2
  exit 1
fi

LABEL="sim"
OUT_DIR=""
DO_ASSIGN=1
SIM_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --label) LABEL="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --no-assign) DO_ASSIGN=0; shift ;;
    --) shift; SIM_ARGS=("$@"); break ;;
    *) echo "[run] unknown arg: $1" >&2; exit 2 ;;
  esac
done

DEVICE_MAC="${LESSON_SIM_DEVICE_ID:-14:c1:9f:d1:a8:48}"
DEVICE_UUID="${LESSON_SIM_DEVICE_UUID:-55555555-5555-4555-8555-555555555555}"
CHILD_UUID="${LESSON_SIM_CHILD_UUID:-44444444-4444-4444-8444-444444444444}"
ADMIN_UUID="${LESSON_SIM_ADMIN_UUID:-11111111-1111-4111-8111-111111111111}"
LESSON_KEY="${LESSON_SIM_LESSON_KEY:-w01-d01-barn-say-it}"
LESSON_VERSION="${LESSON_SIM_LESSON_VERSION:-1}"
BACKEND="${LESSON_SIM_BACKEND_BASE:-http://127.0.0.1:3100/v1}"
ESP_CONTAINER="${LESSON_SIM_ESP_CONTAINER:-tbot-ls-e2e-esp}"
PG_CONTAINER="${LESSON_SIM_PG_CONTAINER:-tbot-ls-e2e-pg}"
MYSQL_CONTAINER="${LESSON_SIM_MYSQL_CONTAINER:-tbot-ls-e2e-mysql}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${OUT_DIR:-${TBOT_ROOT}/robot/evidence/lesson-e2e-sim-${STAMP}-${LABEL}}"
mkdir -p "${OUT_DIR}"

psql_e2e() { docker exec -i "${PG_CONTAINER}" psql -U tbot -d tbot -v ON_ERROR_STOP=1 -qtA "$@"; }

# The manager-api secret rotates on every clean deploy; read it live, never pin it.
SECRET="$(docker exec "${MYSQL_CONTAINER}" sh -lc \
  'MYSQL_PWD=123456 mysql -u root tbot_esp32_server -N -e "select param_value from sys_params where param_code=\"server.secret\";"' \
  | tr -d '\r')"
[[ -z "${SECRET}" ]] && { echo "[run] FATAL: no manager-api server.secret" >&2; exit 1; }

if [[ "${DO_ASSIGN}" == "1" ]]; then
  # Admin auth is an OPAQUE session token matched by sha256 against admin_sessions —
  # not a JWT — so a session is minted directly rather than by logging in.
  ADMIN_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
  ADMIN_HASH="$(printf '%s' "${ADMIN_TOKEN}" | shasum -a 256 | cut -d' ' -f1)"
  psql_e2e -c "INSERT INTO admin_sessions (admin_user_id, token_hash, expires_at)
               VALUES ('${ADMIN_UUID}', '${ADMIN_HASH}', now() + interval '1 day');" >/dev/null

  # A previous run left in ASSIGNED/PRELOADING/READY/RUNNING/PAUSED blocks the single
  # active-assignment slot, and re-assigning onto it returns 201 with the STALE row,
  # which then cannot be driven to COMPLETED (F-T53-13). Clear it explicitly.
  CLEARED="$(psql_e2e -c "UPDATE lesson_assignments SET state='CANCELLED', updated_at=now()
                          WHERE device_id='${DEVICE_UUID}'
                            AND state IN ('ASSIGNED','PRELOADING','READY','RUNNING','PAUSED')
                          RETURNING id;" | wc -l | tr -d ' ')"
  echo "[run] cleared ${CLEARED} stale active assignment(s)"

  ASSIGN_BODY="$(curl -sS -X POST "${BACKEND}/admin/lesson-assignments" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" -H 'Content-Type: application/json' \
    -d "{\"deviceId\":\"${DEVICE_UUID}\",\"lessonId\":\"${LESSON_KEY}\",\"lessonVersion\":${LESSON_VERSION},\"childId\":\"${CHILD_UUID}\",\"profile\":\"espTft\"}")"
  echo "${ASSIGN_BODY}" > "${OUT_DIR}/assignment.json"
  ASSIGNMENT_ID="$(python3 -c "import json,sys;print((json.load(open(sys.argv[1]))['data']['assignment']['assignmentId']))" "${OUT_DIR}/assignment.json" 2>/dev/null || true)"
  [[ -z "${ASSIGNMENT_ID}" ]] && { echo "[run] FATAL: assignment failed: ${ASSIGN_BODY}" >&2; exit 1; }
  echo "[run] assigned ${LESSON_KEY} v${LESSON_VERSION} -> ${ASSIGNMENT_ID}"
fi

# Unix epoch, NOT RFC3339: docker reads a naive RFC3339 --since in the daemon's local
# timezone, so a UTC stamp silently captures a window hours away from the run.
SINCE="$(date +%s)"

echo "[run] driving simulated device ${DEVICE_MAC}"
set +e
LESSON_SIM_AUTH_KEY="${SECRET}" python3 "${HERE}/sim_device.py" \
  --device-id "${DEVICE_MAC}" \
  --serial-log "${OUT_DIR}/serial.log" \
  --timeline-log "${OUT_DIR}/timeline-serial.log" \
  --frame-dump "${OUT_DIR}/frames.jsonl" \
  ${SIM_ARGS[@]+"${SIM_ARGS[@]}"} > "${OUT_DIR}/sim-stdout.log" 2>&1
SIM_RC=$?
set -e
tail -1 "${OUT_DIR}/sim-stdout.log"

docker logs --since "${SINCE}" "${ESP_CONTAINER}" > "${OUT_DIR}/esp-server.log" 2>&1

# Interleave by event time. The verifier walks a MONOTONIC cursor, so two files passed
# separately are compared in file order, not event order, and the verdict changes with
# the order they are given (F-T53-15). Both streams stamp milliseconds, so the merge is
# decided by real time rather than by a tiebreak.
python3 "${HERE}/merge_timeline.py" \
  --server-log "${OUT_DIR}/esp-server.log" \
  --device-timeline "${OUT_DIR}/timeline-serial.log" \
  --out "${OUT_DIR}/timeline.log"

VERIFY_RC=0
python3 "${TBOT_ROOT}/robot/scripts/lesson_e2e_log_verify.py" \
  --device-id "${DEVICE_MAC}" \
  --device-alias "${DEVICE_UUID}" \
  --order-by-wire-sequence \
  --log-file "${OUT_DIR}/timeline.log" \
  > "${OUT_DIR}/lesson-e2e-report.json" 2>&1 || VERIFY_RC=$?

# Kept for comparison only: the pre-merge, file-order score. If these two ever converge
# it means the merge stopped mattering, which would itself be worth knowing.
python3 "${TBOT_ROOT}/robot/scripts/lesson_e2e_log_verify.py" \
  --device-id "${DEVICE_MAC}" \
  --device-alias "${DEVICE_UUID}" \
  --log-file "${OUT_DIR}/esp-server.log" \
  --log-file "${OUT_DIR}/serial.log" \
  > "${OUT_DIR}/lesson-e2e-report.concatenated.json" 2>&1 || true

python3 - "${OUT_DIR}/lesson-e2e-report.json" "${OUT_DIR}/lesson-e2e-report.concatenated.json" <<'PY'
import json, sys


def score(path):
    checks = json.load(open(path))["checks"]
    return checks, [c for c in checks if not c["ok"]]


checks, failed = score(sys.argv[1])
print(f"[run] verifier {len(checks) - len(failed)}/{len(checks)} (merged timeline)")
try:
    other, other_failed = score(sys.argv[2])
    print(f"[run]          {len(other) - len(other_failed)}/{len(other)} (concatenated, for comparison)")
except Exception:
    pass
for c in failed:
    print(f"       FAIL {c['name']}: {c['missing'][:90]}")
PY

echo "[run] evidence: ${OUT_DIR} (sim rc=${SIM_RC})"
exit "${VERIFY_RC}"
