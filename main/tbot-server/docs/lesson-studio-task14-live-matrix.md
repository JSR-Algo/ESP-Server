# Task 14 Lesson Studio Live Matrix

This matrix is fail-closed. Local parser or fixture success is not hardware
evidence. Keep every live row `NOT PASS` until its evidence fields are filled
from one bounded lab run using the exact published fixture version.

Fixture: `production-farm-english-358`, version `2026-07-11.1`; lessons
`pip-farm-3m`, `pip-farm-5m`, and `pip-farm-8m`.

This production farm fixture is the authoritative Task 14 hardware fixture.
The `tvideo-raw-code` source added for the admin demo and canonical software
round-trip is separate; do not substitute its demo version or identifiers in
the live commands unless this matrix is explicitly revised and re-reviewed.

The `T14-LIVE-*` identifiers in the evidence table are canonical. In
particular, `T14-LIVE-02` is cold preload and `T14-LIVE-03` is warm cache.
Verifier helper scenarios such as `manifest-pin-abort`, `preload-recovery`,
`no-assignment`, and `republish-eviction` are diagnostics and do not reuse Task
14 row numbers.

## One-time shell setup

Run from the ESP production Lesson Studio worktree. Replace every angle-bracket
placeholder before starting; do not put credentials or child transcripts in the
bundle.

```bash
export TBOT_ROOT=/Users/manhhodinh/Documents/TBOT
export ESP_WORKTREE=/Users/manhhodinh/Documents/TBOT/.worktrees/esp32-server-production-lesson-studio-continued
export BACKEND_WORKTREE=/Users/manhhodinh/Documents/TBOT/tbot-backend
export FIRMWARE_WORKTREE=/Users/manhhodinh/Documents/TBOT/.worktrees/tbot-firmware-production-lesson-studio-continued
export HIL_BUILD_MANIFEST="$FIRMWARE_WORKTREE/build-task14-hil/lesson-storage-hil-build.json"
export PRODUCTION_BUILD_MANIFEST="$FIRMWARE_WORKTREE/build-task14-production/lesson-storage-hil-build.json"
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export EVIDENCE_ROOT="$TBOT_ROOT/.codex_tmp/task14-live-$RUN_ID"
export RELEASE_LEDGER="$EVIDENCE_ROOT/release-ledger.json"
export HIL_MATRIX_REPORT="$EVIDENCE_ROOT/hil-matrix-report.json"
export PRODUCTION_REFLASH_RECEIPT="$EVIDENCE_ROOT/production-reflash.json"
export PRODUCTION_ATTESTATION="$EVIDENCE_ROOT/production-attestation.json"
export DEVICE_ID='28:84:85:85:1a:80' # exact live connection key used by the eviction route
export DEVICE_ALIAS='fce7bec8-8478-4ab4-817f-7b87c41c1f91'
export BACKEND_DEVICE_ID='14140000-0000-4000-8000-000000000004'
export CHILD_ID='14140000-0000-4000-8000-000000000003'
export COURSE_ID='production-farm-english-358'
export FIXTURE_VERSION='2026-07-11.1'
export LESSON_ID='pip-farm-3m'
export LESSON_VERSION=1
export MANIFEST_CHECKSUM='<64-lowercase-hex-from-the-published-manifest>'
export CACHE_KEY='<exact-cache-key-from-the-published-manifest>'
export CAPTURE_SCRIPT="$TBOT_ROOT/robot/scripts/lesson_e2e_live_capture.py"
export VERIFIER_SCRIPT="$TBOT_ROOT/robot/scripts/lesson_e2e_log_verify.py"
export BACKEND_URL='http://192.168.1.25:3100/v1'
export SERIAL_PORT='<serial-port>'
export SCENARIO='cold' # set to warm for the second fresh assignment
mkdir -p "$EVIDENCE_ROOT"

git -C "$BACKEND_WORKTREE" rev-parse HEAD
git -C "$ESP_WORKTREE" rev-parse HEAD
git -C "$FIRMWARE_WORKTREE" rev-parse HEAD
```

Every scenario directory must contain non-empty `serial.log`, `server.log`,
`command.txt`, and `result.json`. Screenshot paths in `result.json` must resolve
inside that scenario directory. Run the scenario validator only after the
bounded operator action has finished:

```bash
cd "$ESP_WORKTREE/main/tbot-server"
python3 scripts/lesson_studio_task14_fault_driver.py <scenario> \
  --evidence-dir "$EVIDENCE_ROOT/<scenario>" \
  --capture-script "$CAPTURE_SCRIPT" \
  --verifier-script "$VERIFIER_SCRIPT" \
  --output "$EVIDENCE_ROOT/<scenario>/evidence.json"
echo $? > "$EVIDENCE_ROOT/<scenario>/validator-exit-code.txt"
```

The validator must exit `0` and emit `status=PASS`. An operator-authored PASS,
successful local parser self-test, or success-looking log text is insufficient.

## Exact live commands

### Cold eviction and capture order

For `cold`, start the bounded serial/server log capture before eviction. The
operator shell must already contain both `TBOT_DEVICE_MINT_SECRET` and the
parent-auth `TBOT_PARENT_JWT` from the approved secret store; do not echo them,
paste their values into a command, or copy them into the evidence directory.
Both must be exported, not merely assigned as local shell variables. Fail
closed before recording or executing either request if a child process cannot
read them. This check emits no secret value:

```bash
set -euo pipefail
mkdir -p "$EVIDENCE_ROOT/cold"
python3 - <<'PY'
import os

if not os.environ.get("TBOT_DEVICE_MINT_SECRET"):
    raise SystemExit("TBOT_DEVICE_MINT_SECRET must be exported and non-empty")
if not os.environ.get("TBOT_PARENT_JWT"):
    raise SystemExit("TBOT_PARENT_JWT must be exported and non-empty")
PY
command -v python3 >/dev/null
command -v docker >/dev/null
command -v jq >/dev/null
command -v shasum >/dev/null
command -v lsof >/dev/null
command -v pgrep >/dev/null
test -e "$SERIAL_PORT"
docker inspect tbot-esp32-server >/dev/null

cd "$TBOT_ROOT/robot"
python3 scripts/lesson_e2e_live_capture.py \
  --preflight \
  --device-id "$DEVICE_ID" \
  --device-alias "$DEVICE_ALIAS" \
  --serial-port "$SERIAL_PORT" \
  --expected-lesson-id "$LESSON_ID" \
  --expected-course-id "$COURSE_ID" \
  --expected-backend-url "$BACKEND_URL" \
  --expected-child-id "$CHILD_ID" \
  --expected-device-binding "$BACKEND_DEVICE_ID" \
  --require-assignment-version \
  --require-lesson-version \
  --require-story \
  --server-log-command "docker logs --since 0m --tail 0 -f tbot-esp32-server" \
  --out-dir "$EVIDENCE_ROOT/cold/preflight"

CAPTURE_PID=''
# BEGIN TASK14_CAPTURE_CLEANUP
cleanup_capture() {
  cleanup_status="$1"
  trap - EXIT INT TERM
  restore_capture_monitor
  if [ -n "${CAPTURE_PGID:-}" ] && kill -0 -- "-$CAPTURE_PGID" 2>/dev/null; then
    kill -TERM -- "-$CAPTURE_PGID" 2>/dev/null || true
    capture_attempt=0
    while [ "$capture_attempt" -lt 20 ] \
      && kill -0 -- "-$CAPTURE_PGID" 2>/dev/null; do
      sleep 0.1
      capture_attempt=$((capture_attempt + 1))
    done
    if kill -0 -- "-$CAPTURE_PGID" 2>/dev/null; then
      kill -KILL -- "-$CAPTURE_PGID" 2>/dev/null || true
    fi
  fi
  if [ -n "${CAPTURE_PID:-}" ]; then
    wait "$CAPTURE_PID" 2>/dev/null || true
  fi
  CAPTURE_PID=''
  CAPTURE_PGID=''
  exit "$cleanup_status"
}
# END TASK14_CAPTURE_CLEANUP

# BEGIN TASK14_COLD_CAPTURE_START
# BEGIN TASK14_CAPTURE_SESSION_HELPERS
restore_capture_monitor() {
  if [ "${CAPTURE_MONITOR_WAS_ON:-0}" -eq 1 ]; then
    set -m
  fi
  CAPTURE_MONITOR_WAS_ON=0
}

launch_capture_session() {
  capture_log="$1"
  shift
  CAPTURE_MONITOR_WAS_ON=0
  case $- in
    *m*) CAPTURE_MONITOR_WAS_ON=1 ;;
  esac
  if [ "$CAPTURE_MONITOR_WAS_ON" -eq 1 ]; then
    set +m
  fi
  python3 - "$@" > "$capture_log" 2>&1 <<'PY' &
import os
import sys

os.setsid()
os.execvp("python3", ["python3", *sys.argv[1:]])
PY
  CAPTURE_PID=$!
  CAPTURE_PGID=$CAPTURE_PID
  restore_capture_monitor
}
# END TASK14_CAPTURE_SESSION_HELPERS

date -u +%Y-%m-%dT%H:%M:%SZ > "$EVIDENCE_ROOT/cold/utc-start.txt"
trap 'cleanup_capture $?' EXIT
trap 'cleanup_capture 130' INT
trap 'cleanup_capture 143' TERM
launch_capture_session \
  "$EVIDENCE_ROOT/cold/capture-driver.log" \
  scripts/lesson_e2e_live_capture.py \
  --duration 240 \
  --device-id "$DEVICE_ID" \
  --device-alias "$DEVICE_ALIAS" \
  --serial-port "$SERIAL_PORT" \
  --expected-lesson-id "$LESSON_ID" \
  --expected-course-id "$COURSE_ID" \
  --expected-backend-url "$BACKEND_URL" \
  --expected-child-id "$CHILD_ID" \
  --expected-device-binding "$BACKEND_DEVICE_ID" \
  --require-assignment-version \
  --require-lesson-version \
  --require-story \
  --server-log-command "docker logs --since 0m --tail 0 -f tbot-esp32-server" \
  --out-dir "$EVIDENCE_ROOT/cold/capture"

for _ in $(seq 1 100); do
  if kill -0 "$CAPTURE_PID" 2>/dev/null \
    && test -f "$EVIDENCE_ROOT/cold/capture/esp-server.log" \
    && test -f "$EVIDENCE_ROOT/cold/capture/firmware-serial.log"; then
    break
  fi
  sleep 0.1
done
kill -0 "$CAPTURE_PID"
test "$(ps -o pgid= -p "$CAPTURE_PID" | tr -d ' ')" = "$CAPTURE_PGID"
test -f "$EVIDENCE_ROOT/cold/capture/esp-server.log"
test -f "$EVIDENCE_ROOT/cold/capture/firmware-serial.log"
pgrep -P "$CAPTURE_PID" -f 'docker logs.*tbot-esp32-server' >/dev/null
lsof "$SERIAL_PORT" >/dev/null
# END TASK14_COLD_CAPTURE_START

cat > "$EVIDENCE_ROOT/cold/command.txt" <<'EOF'
set -euo pipefail
curl --fail-with-body --silent --show-error \
  -X POST \
  -H "X-Mint-Secret: ${TBOT_DEVICE_MINT_SECRET}" \
  -H 'Content-Type: application/json' \
  --data "{\"cacheKey\":\"${CACHE_KEY}\"}" \
  "http://127.0.0.1:8003/internal/devices/${DEVICE_ID}/lesson-assets/evict-cache-key" \
  | tee "$EVIDENCE_ROOT/cold/eviction-response.json"
EOF
bash "$EVIDENCE_ROOT/cold/command.txt"
```

Only `evicted` or `not_found` may proceed to fresh assignment creation. A
`503 LESSON_CACHE_MAINTENANCE_REQUIRED` response with status and reason
`partial_evict_recovery_required` is truthful evidence that mutation started
but did not finish. Stop the cold run immediately: do not create an assignment,
retry or repair the exact cache key shown in `.data.cacheKey` while attended,
then rerun the exact eviction endpoint and retain only a fresh coherent
`evicted` or `not_found` response for the cold evidence bundle. Never rewrite
the partial `fileCount`, or treat the partial response as evicted, not-found, or
cold-cache evidence.

`DEVICE_ID` is the attended robot MAC exported above and therefore resolves
directly in the ESP server's live connection map. `command.txt` must contain
`${TBOT_DEVICE_MINT_SECRET}` literally and must
never contain its value. Immediately attest the reply against the exact target
key and retain a checksum of the downloaded response artifact:

```bash
jq -e --arg key "$CACHE_KEY" '
  .data.cacheKey == $key and
  ((.data.status == "evicted" and .data.evicted == true and
    .data.notFound == false and (.data.fileCount | type) == "number" and
    .data.fileCount >= 0 and .data.reason == "evicted") or
   (.data.status == "not_found" and .data.evicted == false and
    .data.notFound == true and .data.fileCount == 0 and
    .data.reason == "not_found"))
' "$EVIDENCE_ROOT/cold/eviction-response.json"
shasum -a 256 "$EVIDENCE_ROOT/cold/eviction-response.json" \
  > "$EVIDENCE_ROOT/cold/eviction-response.sha256"

record_strict_utc() {
  python3 - "$1" "$2" <<'PY'
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

output = Path(sys.argv[1])
previous = Path(sys.argv[2])
now = datetime.now(timezone.utc)
prior = datetime.fromisoformat(previous.read_text().strip().replace("Z", "+00:00"))
if now <= prior:
    now = prior + timedelta(microseconds=1)
output.write_text(now.isoformat(timespec="microseconds").replace("+00:00", "Z") + "\n")
PY
}

record_strict_utc \
  "$EVIDENCE_ROOT/cold/eviction-completed-utc.txt" \
  "$EVIDENCE_ROOT/cold/utc-start.txt"
record_strict_utc \
  "$EVIDENCE_ROOT/cold/cold-capture-started-utc.txt" \
  "$EVIDENCE_ROOT/cold/eviction-completed-utc.txt"

curl --fail-with-body --silent --show-error \
  -X POST \
  -H "Authorization: Bearer ${TBOT_PARENT_JWT}" \
  -H 'Content-Type: application/json' \
  --data "{\"childId\":\"${CHILD_ID}\",\"lessonId\":\"${LESSON_ID}\",\"lessonVersion\":${LESSON_VERSION},\"profile\":\"espTft\"}" \
  "$BACKEND_URL/devices/${BACKEND_DEVICE_ID}/assignments" \
  | tee "$EVIDENCE_ROOT/cold/assignment-create-response.json"
jq -e \
  --arg deviceId "$BACKEND_DEVICE_ID" \
  --arg childId "$CHILD_ID" \
  --arg lessonId "$LESSON_ID" \
  --argjson lessonVersion "$LESSON_VERSION" \
  --arg manifestChecksum "$MANIFEST_CHECKSUM" '
    .data.assignment.state == "ASSIGNED" and
    .data.assignment.profile == "espTft" and
    .data.assignment.deviceId == $deviceId and
    .data.assignment.childId == $childId and
    .data.assignment.lessonId == $lessonId and
    .data.assignment.lessonVersion == $lessonVersion and
    .data.assignment.manifestChecksum == $manifestChecksum and
    (.data.assignment.assignmentVersion | type) == "number" and
    .data.assignment.assignmentVersion > 0
  ' "$EVIDENCE_ROOT/cold/assignment-create-response.json"
ASSIGNMENT_ID="$(jq -er '.data.assignment.assignmentId' \
  "$EVIDENCE_ROOT/cold/assignment-create-response.json")"
ASSIGNMENT_VERSION="$(jq -er '.data.assignment.assignmentVersion' \
  "$EVIDENCE_ROOT/cold/assignment-create-response.json")"
ASSIGNMENT_CREATED_UTC="$(jq -er '.data.assignment.createdAt' \
  "$EVIDENCE_ROOT/cold/assignment-create-response.json")"
shasum -a 256 "$EVIDENCE_ROOT/cold/assignment-create-response.json" \
  > "$EVIDENCE_ROOT/cold/assignment-create-response.sha256"
```

Only after reply/key attestation succeeds, record `coldCaptureStartedUtc` as
shown, then use the existing authenticated parent assignment endpoint above.
The response artifact is authoritative: copy `ASSIGNMENT_ID` and
`ASSIGNMENT_VERSION` and `ASSIGNMENT_CREATED_UTC` verbatim into `assignmentId`,
`assignmentVersion`, and `assignmentCreatedUtc`; never derive these values from
operator wall-clock time. Set cold-only `assignmentBackendDeviceId` to
`BACKEND_DEVICE_ID`, `assignmentChildId` to `CHILD_ID`, and `assignmentProfile`
to `espTft`; the validator binds them back to the authenticated create response.
The response and its checksum contain no bearer token and are retained with the
cold evidence. The validator accepts only the backend's documented
`data.assignment` envelope and typed assignment fields; it rejects nested
`authorization`, `token`, `accessToken`, or `refreshToken` keys plus Bearer or
JWT-shaped string values before the artifact can be reported as valid evidence.
Raw response text is scanned before JSON parsing; malformed, non-object, or
schema-invalid assignment responses and their checksum files are omitted from
the evidence report rather than hashed as valid artifacts.
The strict order is bounded log capture,
exact eviction, response/key attestation, cold capture start, fresh assignment,
lesson execution, and validation. It must satisfy
`utcStart <= evictionCompletedUtc < coldCaptureStartedUtc < assignmentCreatedUtc < utcEnd`.
Map `utc-start.txt`, `eviction-completed-utc.txt`, and
`cold-capture-started-utc.txt` verbatim to `utcStart`, `evictionCompletedUtc`,
and `coldCaptureStartedUtc` in `result.json`; preserve the backend assignment's
strict UTC `createdAt` as `assignmentCreatedUtc`.
Do not use `rm`, raw SD/filesystem deletion, a generic MCP route, or a completed
assignment for this proof.

The capture command below is the green-path source for preview, cold preload,
and lesson runtime evidence. For `cold`, create a fresh non-terminal assignment
and clear only that lesson's exact target cache key on the attended robot. After
the cold assignment completes, create a second fresh assignment/session for
`warm` that pins the same `lessonId`, `lessonVersion`, `manifestChecksum`, and
cache key; do not clear or alter the verified SD pack. Assignment/session IDs are
expected to differ because terminal assignments cannot be restarted. Copy each
run's generated logs only after the bounded capture is explicitly stopped. The
capture already started before eviction, so its server stream contains the
eviction marker and must never be replaced by a later-only log.

```bash
# Run this only after the fresh assignment has executed inside the active capture window.
wait "$CAPTURE_PID" # explicit wait/stop before validation
CAPTURE_STATUS=$?
CAPTURE_PID=''
CAPTURE_PGID=''
trap - EXIT INT TERM
test "$CAPTURE_STATUS" -eq 0
test -s "$EVIDENCE_ROOT/cold/capture/esp-server.log"
test -s "$EVIDENCE_ROOT/cold/capture/firmware-serial.log"
cp "$EVIDENCE_ROOT/cold/capture/esp-server.log" "$EVIDENCE_ROOT/cold/server.log"
cp "$EVIDENCE_ROOT/cold/capture/firmware-serial.log" "$EVIDENCE_ROOT/cold/serial.log"

cd "$ESP_WORKTREE/main/tbot-server"
python3 scripts/lesson_studio_task14_fault_driver.py cold \
  --evidence-dir "$EVIDENCE_ROOT/cold" \
  --capture-script "$CAPTURE_SCRIPT" \
  --verifier-script "$VERIFIER_SCRIPT" \
  --output "$EVIDENCE_ROOT/cold/evidence.json"
```

Use the existing strict runtime verifier on every combined bounded timeline:

```bash
cd "$TBOT_ROOT/robot"
python3 scripts/lesson_e2e_log_verify.py \
  --scenario lesson \
  --device-id "$DEVICE_ID" \
  --device-alias "$DEVICE_ALIAS" \
  --log-file "$EVIDENCE_ROOT/<scenario>/server.log" \
  --log-file "$EVIDENCE_ROOT/<scenario>/serial.log" \
  --expected-lesson-id "$LESSON_ID" \
  --expected-course-id "$COURSE_ID" \
  --expected-backend-url "$BACKEND_URL" \
  --expected-child-id "$CHILD_ID" \
  --expected-device-binding "$BACKEND_DEVICE_ID" \
  --require-assignment-version \
  --require-lesson-version \
  --require-story
```

The normal `lesson` scenario is intentionally success-only. Validate a clean
assignment-vs-manifest pin rejection separately; it must surface the start error
and emit no prepare/start/step frames:

```bash
cd "$TBOT_ROOT/robot"
python3 scripts/lesson_e2e_log_verify.py \
  --scenario manifest-pin-abort \
  --device-id "$DEVICE_ID" \
  --log-file "$EVIDENCE_ROOT/<scenario>/server.log" \
  --log-file "$EVIDENCE_ROOT/<scenario>/serial.log"
```

For interruption/power-loss recovery, also run the specialized recovery gate:

```bash
cd "$TBOT_ROOT/robot"
python3 scripts/lesson_e2e_log_verify.py \
  --scenario preload-recovery \
  --device-id "$DEVICE_ID" \
  --log-file "$EVIDENCE_ROOT/<scenario>/server.log" \
  --log-file "$EVIDENCE_ROOT/<scenario>/serial.log"
```

For rollback, compare the bounded before/after logs with the republish/eviction
gate in addition to the Task 14 scenario validator:

```bash
cd "$TBOT_ROOT/robot"
python3 scripts/lesson_e2e_log_verify.py \
  --scenario republish-eviction \
  --device-id "$DEVICE_ID" \
  --before-log-file "$EVIDENCE_ROOT/rollback/before-rollback.log" \
  --after-log-file "$EVIDENCE_ROOT/rollback/after-rollback.log"
```

The attended release order is immutable:
`hil-matrix-pass -> production-reflash -> production-attest -> production-soak`.
Complete the HIL fault matrix first, reflash the clean production image built
from the exact paired source commit, verify that the selected manifest has
profile `production` and HIL disabled, and only then collect the production
soak. Never count transitions captured from the HIL image toward production.

Record each completed gate immediately after its evidence exists. Every call
appends exactly one timestamped receipt and hashes its prerequisite artifact;
skips, rewrites, non-increasing timestamps, foreign builds, and changed evidence
are rejected. Run the first command after flashing HIL, the second after the HIL
matrix report is PASS, the third after production reflash, and the fourth after
production attestation:

```bash
cd "$ESP_WORKTREE/main/tbot-server"
python3 scripts/lesson_studio_task14_build_identity.py release \
  --ledger "$RELEASE_LEDGER" \
  --hil-manifest "$HIL_BUILD_MANIFEST" \
  --production-manifest "$PRODUCTION_BUILD_MANIFEST" \
  --event hil-flash \
  --evidence "$HIL_BUILD_MANIFEST" \
  --completed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 scripts/lesson_studio_task14_build_identity.py release \
  --ledger "$RELEASE_LEDGER" \
  --hil-manifest "$HIL_BUILD_MANIFEST" \
  --production-manifest "$PRODUCTION_BUILD_MANIFEST" \
  --event hil-matrix-pass \
  --evidence "$HIL_MATRIX_REPORT" \
  --completed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 scripts/lesson_studio_task14_build_identity.py release \
  --ledger "$RELEASE_LEDGER" \
  --hil-manifest "$HIL_BUILD_MANIFEST" \
  --production-manifest "$PRODUCTION_BUILD_MANIFEST" \
  --event production-reflash \
  --evidence "$PRODUCTION_REFLASH_RECEIPT" \
  --completed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 scripts/lesson_studio_task14_build_identity.py release \
  --ledger "$RELEASE_LEDGER" \
  --hil-manifest "$HIL_BUILD_MANIFEST" \
  --production-manifest "$PRODUCTION_BUILD_MANIFEST" \
  --event production-attest \
  --evidence "$PRODUCTION_ATTESTATION" \
  --completed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

Capture the raw production soak timeline without applying the single-lesson
identity, marker, or verifier gates. Raw mode still requires non-empty serial
and server streams for the whole bounded capture and fails on either source
exiting early:

```bash
cd "$TBOT_ROOT/robot"
python3 scripts/lesson_e2e_live_capture.py \
  --capture-only \
  --duration 900 \
  --device-id "$DEVICE_ID" \
  --device-alias "$DEVICE_ALIAS" \
  --serial-port "$SERIAL_PORT" \
  --server-log-command "docker logs --since 0m --tail 0 -f tbot-esp32-server" \
  --out-dir "$EVIDENCE_ROOT/soak/capture"
```

After at least 104 real step transitions, run the soak gate against the ledger
ending at `production-attest`:

```bash
cd "$ESP_WORKTREE/main/tbot-server"
python3 scripts/lesson_studio_task14_soak.py \
  "$EVIDENCE_ROOT/soak/capture/firmware-serial.log" \
  "$EVIDENCE_ROOT/soak/capture/esp-server.log" \
  --timeline-log "$EVIDENCE_ROOT/soak/capture/timeline.log" \
  --fixture-version "$FIXTURE_VERSION" \
  --course-id "$COURSE_ID" \
  --lesson-id "$LESSON_ID" \
  --capture-script "$CAPTURE_SCRIPT" \
  --verifier-script "$VERIFIER_SCRIPT" \
  --minimum-transitions 104 \
  --build-manifest "$PRODUCTION_BUILD_MANIFEST" \
  --release-ledger "$RELEASE_LEDGER" \
  --output "$EVIDENCE_ROOT/soak/report.json"
```

Only after that report is PASS may the final receipt be appended. The command
validates the report's transition gate, production build identity, and embedded
prerequisite ledger before recording `production-soak`. Then run the final log
audit against the completed ledger:

```bash
python3 scripts/lesson_studio_task14_build_identity.py release \
  --ledger "$RELEASE_LEDGER" \
  --hil-manifest "$HIL_BUILD_MANIFEST" \
  --production-manifest "$PRODUCTION_BUILD_MANIFEST" \
  --event production-soak \
  --evidence "$EVIDENCE_ROOT/soak/report.json" \
  --completed-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python3 scripts/lesson_studio_task14_log_audit.py \
  "$EVIDENCE_ROOT/soak/capture/firmware-serial.log" \
  "$EVIDENCE_ROOT/soak/capture/esp-server.log" \
  --timeline-log "$EVIDENCE_ROOT/soak/capture/timeline.log" \
  --fixture-version "$FIXTURE_VERSION" \
  --course-id "$COURSE_ID" \
  --lesson-id "$LESSON_ID" \
  --capture-script "$CAPTURE_SCRIPT" \
  --verifier-script "$VERIFIER_SCRIPT" \
  --minimum-transitions 104 \
  --build-manifest "$PRODUCTION_BUILD_MANIFEST" \
  --release-ledger "$RELEASE_LEDGER" \
  --output "$EVIDENCE_ROOT/soak/audit.json"
```

The soak command requires at least 104 strictly increasing transition
identities (monotonic within each bound assignment/session), at least three
PSRAM samples, no monotonic loss over 64 KiB, no reset marker, and both active
phase and firmware-lifetime internal-SRAM minima at or above the provisional
20 KiB gate. Production approval still requires the hardware-derived threshold
to be recorded and agreed; the parser default alone does not establish it.
When step IDs repeat across lesson sessions, `timeline.log` is mandatory so
each server transition is bound inside its matching `lesson_prepare` session
boundary instead of being correlated by file order alone.
Both JSON reports must contain `minimumTransitionsRequired=104` and the exact
flattened `buildIdentity` loaded from `PRODUCTION_BUILD_MANIFEST`; a HIL,
unverified, stale, or foreign-profile manifest is a hard failure.
The soak report must contain `releaseLedgerEvidence` through
`production-attest`; the final audit must contain the same ledger with the
`production-soak` receipt appended and bound to the actual PASS soak report.

## Evidence schema

Every `result.json` requires these common fields. Checksums are exactly 64
lowercase hex characters; commits are 7-40 lowercase hex characters; times are
UTC ISO-8601 with `utcEnd > utcStart`; all versions and heap values are positive
integers. `screenshots` must contain real PNG/JPEG files no larger than 10 MiB.
`fixtureVersion` and `courseId` are exact constants, while `lessonId` must be one
of `pip-farm-3m`, `pip-farm-5m`, or `pip-farm-8m`. The validator computes the
two helper hashes from the files selected by `--capture-script` and
`--verifier-script`; operator-entered hashes alone are never trusted.

```json
{
  "scenario": "<scenario>",
  "status": "PASS",
  "utcStart": "2026-07-13T00:00:00Z",
  "utcEnd": "2026-07-13T00:10:00Z",
  "backendCommit": "<git-rev-parse-head>",
  "espServerCommit": "<git-rev-parse-head>",
  "firmwareCommit": "<git-rev-parse-head>",
  "firmwareVersion": "<firmware-version>",
  "deviceId": "<device-id-or-mac>",
  "assignmentId": "<assignment-id>",
  "sessionId": "<session-id>",
  "assignmentVersion": 1,
  "fixtureVersion": "2026-07-11.1",
  "courseId": "production-farm-english-358",
  "lessonId": "pip-farm-3m",
  "lessonVersion": 1,
  "manifestChecksum": "<64-lowercase-hex>",
  "packChecksum": "<64-lowercase-hex>",
  "cacheKey": "<lesson-id>/v<lesson-version>-<manifest-checksum>",
  "captureScriptSha256": "<sha256-of---capture-script>",
  "verifierScriptSha256": "<sha256-of---verifier-script>",
  "internalSramMin": 32768,
  "psramFirst": 8000000,
  "psramLast": 7999000,
  "screenshots": [{"role": "hardware", "path": "hardware.png"}],
  "operator": "<operator-name>",
  "commandExitCode": 0,
  "logMarkers": ["<canonical-marker-present-in-raw-log>"]
}
```

The `cold` scenario additionally requires these cold-only fields (a coherent
`not_found` result with `fileCount: 0` is also accepted):

```json
{
  "evictionRequestedCacheKey": "<same-exact-cache-key-as-cacheKey>",
  "evictionResult": {
    "cacheKey": "<same-exact-cache-key-as-cacheKey>",
    "status": "evicted",
    "evicted": true,
    "notFound": false,
    "fileCount": 4,
    "reason": "evicted"
  },
  "evictionCompletedUtc": "2026-07-13T00:01:00Z",
  "coldCaptureStartedUtc": "2026-07-13T00:01:01Z",
  "assignmentCreatedUtc": "2026-07-13T00:01:02Z",
  "assignmentBackendDeviceId": "14140000-0000-4000-8000-000000000004",
  "assignmentChildId": "14140000-0000-4000-8000-000000000003",
  "assignmentProfile": "espTft"
}
```

Its artifact directory must also contain non-empty, non-symlink
`eviction-response.json`, `eviction-response.sha256`, `utc-start.txt`,
`eviction-completed-utc.txt`, `cold-capture-started-utc.txt`,
`assignment-create-response.json`, and `assignment-create-response.sha256`.
The validator parses both response files, path-binds and verifies both checksum
files, derives the three timestamps from their files, and requires the backend
assignment response's `assignmentId` and `createdAt` to equal the corresponding
`result.json` values exactly.

Add the decisive fields below for each scenario. The canonical marker strings
must appear both in `logMarkers` and verbatim in `serial.log` or `server.log`.

| ID / scenario | Required decisive fields and canonical markers | Status |
|---|---|---|
| T14-LIVE-01 `preview-parity` | `previewLayerRects == hardwareLayerRects` (non-empty), `previewWordText == hardwareWordText`, `previewPathOutcome == hardwarePathOutcome`, `previewMotionTimeline == hardwareMotionTimeline`; exactly two 480x320 screenshots with roles `preview` and `hardware`; markers `lesson_step_started`, `motion_preset` | NOT PASS - live run required |
| T14-LIVE-02 `cold` | exact eviction response and parent-auth assignment response artifacts with path-bound SHA-256 files; assignment response `assignmentId`/`createdAt` exactly match result; `evictionRequestedCacheKey == evictionResult.cacheKey == cacheKey`; coherent `evicted` or `not_found`; strict ordered artifact-derived UTC fields; matching sanitized `lesson_cache_evict cache_key=... code=... file_count=...` marker; `bytesDownloaded > 0`, `elapsedMs > 0`, `ready=true`, `checksumVerified=true`, `manifestChecksum == packChecksum`; markers `lesson_cache_evict`, `lesson_preload_ready`, `checksum_verified` | NOT PASS - live run required |
| T14-LIVE-03 `warm` | `cacheHit=true`, `bytesDownloaded=0`, `elapsedMs > 0`, `ready=true`, `manifestChecksum == packChecksum`; marker `asset_cache_hit` | NOT PASS - live run required |
| T14-LIVE-04 `offline` | `networkAvailable=false`, `completed=true`, `source="sd"`; markers `offline_replay`, `sd://` | NOT PASS - live run required |
| T14-LIVE-05 `checksum` | `mismatchDetected=true`, `partialCleaned=true`, `ready=false`; markers `checksum_mismatch`, `partial_cleaned`; both lines bind the same `cacheKey`, `manifestChecksum`, `assignment_id`, and `session_id` | NOT PASS - live run required |
| T14-LIVE-06 `interrupted` | `recovered=true`, `partialCleaned=true`, `readyBeforeVerify=false`, `readyAfterRecovery=true`; markers `download_interrupted`, `partial_cleaned` | NOT PASS - live run required |
| T14-LIVE-07 `power-loss` | same recovery fields as interrupted; markers `power_loss_recovery`, `partial_cleaned` | NOT PASS - live run required |
| T14-LIVE-08 `missing-optional` | `optionalAssetMissing=true`, `degraded=true`, `advanced=true`; markers `optional_asset_missing`, `render_degraded` | NOT PASS - live run required |
| T14-LIVE-09 `sd-full` | `0 <= freeRatio < 0.05`, `refused=true`, `activePackRetained=true`, `previousPackRetained=true`; markers `sd_full_refused`, `previous_pack_retained` | NOT PASS - live run required |
| T14-LIVE-10 `slave-unavailable` | `motionDegraded=true`, `completed=true`; marker `motion_degraded` | NOT PASS - live run required |
| T14-LIVE-11 `rollback` | `activeVersion == previousVersion`, `activeChecksum == previousChecksum` (non-empty), `oldFilesReattested=true`, `ready=true`; markers `rollback_activated`, `old_files_reattested` | NOT PASS - live run required |
| T14-LIVE-12 soak | `report.json.status=PASS`; transition/PSRAM/SRAM/reset checks all true; `report.json.fixtureVersion`, `courseId`, and `lessonId` match the approved fixture; helper hashes are computed from the explicit script paths | NOT PASS - live run required |
| T14-LIVE-13 log audit | `audit.json.status=PASS`; all six failure-marker counts are zero and `duplicateProgress=[]`; `audit.json.fixtureVersion`, `courseId`, and `lessonId` match the approved fixture; helper hashes are computed from the explicit script paths | NOT PASS - live run required |

## Bounded operator actions

The validator deliberately does not inject faults. For `offline`, disable the
lab network only after READY and restore it after local completion. For
`checksum`, corrupt only a disposable lab pack. For `interrupted`, terminate the
lab download; for `power-loss`, remove power only from the attended lab robot.
For `missing-optional`, publish a disposable fixture variant. For `sd-full`, use
a bounded SD quota/image. For `slave-unavailable`, disconnect only the lab slave.
For `rollback`, follow `docs/runbooks/lesson-production-runbook.md` and preserve
both before/after logs. Never inject these faults into production.

## Recording and commit gate

After all validators pass, add a dated evidence subsection to
`docs/TEST_MATRIX.md` containing every exact command, its exit code, the three
commits/firmware version, identities/versions/checksums, SRAM/PSRAM metrics,
artifact paths plus SHA-256 values, operator, and row-by-row PASS/FAIL. Commit
only sanitized evidence in each owning repository; large/raw/private artifacts
remain in the approved evidence store and are referenced by immutable hash.

```bash
git -C "$BACKEND_WORKTREE" status --short
git -C "$ESP_WORKTREE" status --short
git -C "$FIRMWARE_WORKTREE" status --short
git -C "$ESP_WORKTREE" diff --check
```

Task 14 and production readiness remain **NOT PASS** until every live row above
has real-device evidence and the hardware SRAM release threshold is explicitly
accepted.

## 2026-07-15 software/demo checkpoint

- Backend commit `2238295` replaces the synthetic canonical payload with the
  real `tvideo-raw-code` MP4, source PNGs, deterministic ESP derivatives, and a
  storage-verified importer. Focused fixture/import tests passed `9/9` and the
  Nest build passed.
- ESP/admin commit `17e0b6d6` serves the demo assets read-only in the local
  Compose stack and renders the MP4 only in the admin source panel. The full
  real-Google-Chrome lesson-studio suite passed `5/5`, including actual video
  playback, all response paths, publish immutability, real response assets, and
  the shared-visual replacement workflow.
- Firmware commit `6c14545` built as version `2.2.80`; host-native lesson tests
  passed `1,147` checks with `1320/1320` covered lines, Python tests passed
  `744` with `1` skip, and the app image retained `15%` partition headroom.
- Fresh hardware preflight still returned `TARGET_USB_ABSENT`; macOS exposed no
  `/dev/cu.usb*` or `/dev/tty.usb*`, and public lesson-runtime metrics returned
  `connections=0`, `devices=[]`. No flash, reset, nudge, or fault injection was
  attempted. All 13 live rows above remain `NOT PASS - live run required`.
- Fresh readiness verification passed all 11 fault-driver scenarios (22 valid
  and invalid cases per self-test invocation), the focused ESP Task 14 slice
  (`120/120`), and the capture/verifier/checklist slice (`563/563`). The live
  capture preflight reported every local dependency ready and failed only the
  missing `/dev/cu.usbmodem101` serial-port check.
- The attended flash candidate is firmware commit
  `6c145457b6e0c700d34be3dcf9292d686f95b836`, version `2.2.80`. Its current
  `xiaozhi.bin` is 3,513,808 bytes with SHA-256
  `7dab7aa1b6e16aa33d3b2d574df548fcbc3809032391092f54c4af08623b163f`;
  do not flash it until a current USB identity is bound to the target MAC.
