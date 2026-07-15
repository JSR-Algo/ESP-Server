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
export ESP_WORKTREE=/Users/manhhodinh/.config/superpowers/worktrees/esp32-server/production-lesson-studio
export BACKEND_WORKTREE=/Users/manhhodinh/.config/superpowers/worktrees/tbot-backend/production-lesson-studio
export FIRMWARE_WORKTREE=/Users/manhhodinh/.config/superpowers/worktrees/TBOT-Firmware/production-lesson-studio
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
export EVIDENCE_ROOT="$TBOT_ROOT/.codex_tmp/task14-live-$RUN_ID"
export DEVICE_ID='28:84:85:85:1a:80'
export DEVICE_ALIAS='fce7bec8-8478-4ab4-817f-7b87c41c1f91'
export BACKEND_DEVICE_ID='14140000-0000-4000-8000-000000000004'
export CHILD_ID='14140000-0000-4000-8000-000000000003'
export COURSE_ID='production-farm-english-358'
export FIXTURE_VERSION='2026-07-11.1'
export LESSON_ID='pip-farm-3m'
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

The capture command below is the green-path source for preview, cold preload,
and lesson runtime evidence. For `cold`, create a fresh non-terminal assignment
and clear only that lesson's exact target cache key on the attended robot. After
the cold assignment completes, create a second fresh assignment/session for
`warm` that pins the same `lessonId`, `lessonVersion`, `manifestChecksum`, and
cache key; do not clear or alter the verified SD pack. Assignment/session IDs are
expected to differ because terminal assignments cannot be restarted. Copy each
run's generated `esp-server.log` to `server.log` and `firmware-serial.log` to
`serial.log` in the corresponding scenario directory before validation.

```bash
cd "$TBOT_ROOT/robot"
python3 scripts/lesson_e2e_live_capture.py \
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
  --out-dir "$EVIDENCE_ROOT/$SCENARIO/capture"
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

After at least 100 real step transitions, run both fail-closed audits:

```bash
cd "$ESP_WORKTREE/main/tbot-server"
python3 scripts/lesson_studio_task14_soak.py \
  "$EVIDENCE_ROOT/soak/serial.log" "$EVIDENCE_ROOT/soak/server.log" \
  --timeline-log "$EVIDENCE_ROOT/soak/timeline.log" \
  --fixture-version "$FIXTURE_VERSION" \
  --course-id "$COURSE_ID" \
  --lesson-id "$LESSON_ID" \
  --capture-script "$CAPTURE_SCRIPT" \
  --verifier-script "$VERIFIER_SCRIPT" \
  --output "$EVIDENCE_ROOT/soak/report.json"
python3 scripts/lesson_studio_task14_log_audit.py \
  "$EVIDENCE_ROOT/soak/serial.log" "$EVIDENCE_ROOT/soak/server.log" \
  --timeline-log "$EVIDENCE_ROOT/soak/timeline.log" \
  --fixture-version "$FIXTURE_VERSION" \
  --course-id "$COURSE_ID" \
  --lesson-id "$LESSON_ID" \
  --capture-script "$CAPTURE_SCRIPT" \
  --verifier-script "$VERIFIER_SCRIPT" \
  --output "$EVIDENCE_ROOT/soak/audit.json"
```

The soak command requires at least 100 strictly increasing transition
identities (monotonic within each bound assignment/session), at least three
PSRAM samples, no monotonic loss over 64 KiB, no reset marker, and both active
phase and firmware-lifetime internal-SRAM minima at or above the provisional
20 KiB gate. Production approval still requires the hardware-derived threshold
to be recorded and agreed; the parser default alone does not establish it.
When step IDs repeat across lesson sessions, `timeline.log` is mandatory so
each server transition is bound inside its matching `lesson_prepare` session
boundary instead of being correlated by file order alone.

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

Add the decisive fields below for each scenario. The canonical marker strings
must appear both in `logMarkers` and verbatim in `serial.log` or `server.log`.

| ID / scenario | Required decisive fields and canonical markers | Status |
|---|---|---|
| T14-LIVE-01 `preview-parity` | `previewLayerRects == hardwareLayerRects` (non-empty), `previewWordText == hardwareWordText`, `previewPathOutcome == hardwarePathOutcome`, `previewMotionTimeline == hardwareMotionTimeline`; exactly two 480x320 screenshots with roles `preview` and `hardware`; markers `lesson_step_started`, `motion_preset` | NOT PASS - live run required |
| T14-LIVE-02 `cold` | `bytesDownloaded > 0`, `elapsedMs > 0`, `ready=true`, `checksumVerified=true`, `manifestChecksum == packChecksum`; markers `lesson_preload_ready`, `checksum_verified` | NOT PASS - live run required |
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
