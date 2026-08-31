# Google Live robot validation and release gate

Run this after the deterministic and real-API gates in `google-live-smoke.md`
pass. Physical execution is operator-controlled: this runbook does not deploy,
flash, reset, or control a robot automatically.

Use the exact `RUN_ID`, `EVIDENCE_ROOT`, candidate identity, configuration
fingerprint, and fixture checksum exported by the smoke runbook.

## 1. Evidence layout and privacy

`RUN_ID` must be UTC, for example `20260830T140500Z`:

```text
task-artifacts/google-live/$RUN_ID/
  deterministic/report.json
  server-regression/report.json
  real-api/report.json
  websocket-e2e/report.json
  physical/report.json
  candidate-soak/report.json
  timeline.log
  commands.txt
  checksums.sha256
  release-verdict.json
```

Additional bounded intermediates such as `transport.json`, `correlated.json`,
`audit.json`, `pytest.xml`, and operator notes may live below the same root.
`timeline.log` must cover only the recorded UTC test window. `commands.txt`
records commands with `$GOOGLE_API_KEY`, `$OTA_TOKEN`, device IDs, and protected
paths left as redacted variable names, never expanded secret values.

Raw child audio is not stored by default. Use synthetic or consenting-adult
fixtures only. Reports and retained logs must contain no raw/base64 audio, raw
transcripts or prompts, cookies, credentials, tokens, keys, session-resumption
handles, or raw exception text. Session-resumption handles may exist in runtime
memory but their values must never enter artifacts.

## 2. Preflight and bounded log capture

All checks must pass before judging audio behavior:

```bash
curl -fsSI "http://<server-ip>:8000"
python3 scripts/voice_mode_preflight.py \
  --device-ip "<robot-ip>" \
  --max-loss-pct 0 --max-avg-ms 1000 --max-max-ms 1500 \
  --max-jitter-ms 500 --max-duplicates 0
test -s tmp/server.log
```

Start the bounded capture immediately before the first WebSocket/physical
journey. Record the UTC boundaries without copying environment secrets:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$EVIDENCE_ROOT/server-start-utc.txt"
tail -n 0 -F tmp/server.log > "$EVIDENCE_ROOT/timeline.log" &
LOG_CAPTURE_PID=$!
```

After the final bounded journey:

```bash
kill "$LOG_CAPTURE_PID"
wait "$LOG_CAPTURE_PID" 2>/dev/null || true
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$EVIDENCE_ROOT/server-end-utc.txt"
```

The journey-generated reliability anchors inside `timeline.log`, not unrelated
lines outside the window, are authoritative for Task 5 correlation.

## 3. Physical Vietnamese journey

Use the real production-equivalent firmware microphone, speaker, AEC posture,
and LAN. During every opened listening window, stand near the robot and speak
Vietnamese naturally. Do not inject a text substitute for the physical gate.

1. Complete ten ordinary Vietnamese conversation turns.
2. For each turn, wait for the listening window, speak close to the robot, and
   confirm the response starts promptly and answers the latest intent.
3. Complete ten barge-in turns. While robot audio is actively playing, interrupt mid-output
   with a new Vietnamese request near the microphone.
4. Confirm old audio stops, no stale audio resumes, and the new request is served.
5. Hold one quiet interval and one robot-speaking interval; there must be no
   false interruption or echo-driven request.
6. Disconnect and reconnect the same robot, then complete two more turns.
7. Say the approved Vietnamese lesson-start intent, finish one interactive Live
   lesson step, exit/complete the bounded lesson, then make one final ordinary
   Vietnamese request.

Record only verdicts, timings, response/session IDs already safe for logs, and
operator timestamps. Do not transcribe the child's or operator's speech into
the evidence bundle.

## 4. Server lifecycle report and WebSocket composite

Create the exact Task 5 report from the bounded timeline. This calls the same
validator used by tests and writes only its redacted contract:

```bash
EVIDENCE_ROOT="$EVIDENCE_ROOT" PYTHONPATH=. python3 - <<'PY'
import json, os
from pathlib import Path
from scripts.analyze_google_live_log import analyze_reliability_window

root = Path(os.environ["EVIDENCE_ROOT"])
report = analyze_reliability_window(root / "timeline.log")
(root / "server-regression/report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
raise SystemExit(0 if report.get("status") == "PASS" else 1)
PY
```

The standalone WebSocket transport remains `SKIPPED/PENDING`. Build the release
layer only from raw transport plus the correlated Task 5 PASS:

```bash
EVIDENCE_ROOT="$EVIDENCE_ROOT" python3 - <<'PY'
import json, os
from pathlib import Path

root = Path(os.environ["EVIDENCE_ROOT"])
transport = json.loads((root / "websocket-e2e/transport.json").read_text())
log_evidence = json.loads((root / "server-regression/report.json").read_text())
correlated = json.loads((root / "websocket-e2e/correlated.json").read_text())
ok = correlated.get("status") == "PASS" and correlated.get("aggregateReleaseEligible") is True
report = {
    "schemaVersion": "google-live-reliability.v1", "name": "websocket_e2e",
    "status": "PASS" if ok else "FAIL", "candidateIdentity": transport.get("candidateIdentity"),
    "transportEvidence": transport, "logEvidence": log_evidence,
    "correlatedEvidence": correlated,
    "failures": [] if ok else [{"code": "WEBSOCKET_CORRELATION_FAILED"}],
}
(root / "websocket-e2e/report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
```

## 5. Production-candidate soak

Candidate mode consumes the exact 33 recorded executions in
`candidate-soak/journey-evidence.json`: 17 ordinary turns, 10 audio barge-ins,
two controlled quiet windows, one Live reopen, one same-device reconnect, one
lesson entry/interactive/exit, and one ordinary turn after lesson. The summed
monitored UTC windows must be at least 1800 seconds; gaps do not count.

```bash
python3 scripts/google_live_robot_soak.py \
  --mode candidate \
  --ws-url "wss://<server>/tbot/v1/" \
  --device-mac "<robot-device-id>" \
  --client-id "<robot-client-id>" \
  --cycles 10 \
  --inject-audio "$AUDIO_FIXTURE" \
  --audio-source adult \
  --server-has-google-live-credentials \
  --candidate-git-sha "$CANDIDATE_SHA" \
  --candidate-image-digest "$CANDIDATE_IMAGE_DIGEST" \
  --firmware-identity "$FIRMWARE_IDENTITY" \
  --fixture-sha256 "$FIXTURE_SHA256" \
  --config-json "$CONFIG_JSON" \
  --baseline-report "<b07038b8-same-environment-baseline.json>" \
  --real-api-report "$EVIDENCE_ROOT/real-api/report.json" \
  --transport-report "$EVIDENCE_ROOT/websocket-e2e/transport.json" \
  --correlated-transport-report "$EVIDENCE_ROOT/websocket-e2e/correlated.json" \
  --log-reliability-report "$EVIDENCE_ROOT/server-regression/report.json" \
  --journey-evidence "$EVIDENCE_ROOT/candidate-soak/journey-evidence.json" \
  --lesson-manifest "<exact-lesson-manifest.json>" \
  --minimum-turns 30 \
  --minimum-duration-sec 1800 \
  --report "$EVIDENCE_ROOT/candidate-soak/report.json"
```

The soak fails on latency regression above 15%, insufficient turns/duration,
fewer than ten barge-ins, newest-intent success below 80%, any false interrupt,
fallback, stale response, lifecycle imbalance, resource leak, or incomplete
exactly-once cleanup. Synthetic waiting cannot be counted as a turn or latency.

## 6. Physical production-candidate audit

Audit the captured physical window after the Task 5 and candidate-soak reports
exist. The production profile automatically requires strict voice and lesson
markers plus the exact latency, lifecycle, cleanup, and candidate identity.

```bash
python3 scripts/physical_smoke_audit.py "$EVIDENCE_ROOT/timeline.log" \
  --device-id "<robot-device-id>" \
  --client-id "<robot-client-id>" \
  --server-ip "<server-ip>" \
  --min-interrupts 10 \
  --expected-user-transcript "<approved-Vietnamese-test-phrase>" \
  --expected-post-lesson-transcript "<approved-Vietnamese-post-lesson-phrase>" \
  --production-google-live-candidate \
  --candidate-git-sha "$CANDIDATE_SHA" \
  --candidate-image-digest "$CANDIDATE_IMAGE_DIGEST" \
  --firmware-identity "$FIRMWARE_IDENTITY" \
  --config-fingerprint "$CONFIG_FINGERPRINT" \
  --fixture-sha256 "$FIXTURE_SHA256" \
  --google-live-reliability-report "$EVIDENCE_ROOT/server-regression/report.json" \
  --candidate-soak-report "$EVIDENCE_ROOT/candidate-soak/report.json" \
  --lesson-manifest "<exact-lesson-manifest.json>" \
  > "$EVIDENCE_ROOT/physical/audit.json"
```

The expected phrases are runtime assertions only. Before retaining artifacts,
confirm `physical/audit.json` contains counts/hashes but no transcript text.

Wrap the raw audit with the exact upstream evidence required by the release gate:

```bash
EVIDENCE_ROOT="$EVIDENCE_ROOT" python3 - <<'PY'
import json, os
from pathlib import Path

root = Path(os.environ["EVIDENCE_ROOT"])
audit = json.loads((root / "physical/audit.json").read_text())
log_evidence = json.loads((root / "server-regression/report.json").read_text())
soak = json.loads((root / "candidate-soak/report.json").read_text())
report = {
    "schemaVersion": "google-live-reliability.v1", "name": "physical",
    "status": "PASS" if audit.get("passed") is True else "FAIL",
    "candidateIdentity": audit.get("candidateIdentity"), "auditReport": audit,
    "productionProfile": {
        "strictMarkersValidated": True, "lessonValidated": True,
        "postLessonValidated": True, "receiveLoopBalanceRequired": True,
        "sampleCounts": {"firstAudio": 10, "interruptStop": 10, "physicalBargein": 10, "serverOutputGap": 10},
        "budgetsMs": {"firstAudioP50": 1200.0, "firstAudioP95": 1800.0, "interruptStopMax": 250.0, "physicalBargeinP95": 500.0, "serverOutputGapMax": 250.0},
    },
    "logEvidence": log_evidence, "candidateSoakEvidence": soak,
    "failures": [] if audit.get("passed") is True else [{"code": "PHYSICAL_AUDIT_FAILED"}],
}
(root / "physical/report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
```

## 7. External checksums and release aggregation

All six layers are mandatory and must be `PASS` for the exact same candidate.
No layer may be missing, `SKIPPED`, or `PENDING`. In particular, the WebSocket
layer passes only as the Task 4 + Task 5 correlated composite.

Create `checksums.sha256` from outside the reports. A checksum embedded inside a
report is not trusted. Each exact required report path must appear once, relative
to `EVIDENCE_ROOT`; unrelated bounded artifacts may also be listed. Generate the
manifest only after reports are final, and never regenerate it to bless a
modified report.

```bash
(
  cd "$EVIDENCE_ROOT"
  test -s commands.txt
  shasum -a 256 \
    deterministic/report.json \
    server-regression/report.json \
    real-api/report.json \
    websocket-e2e/report.json \
    physical/report.json \
    candidate-soak/report.json \
    timeline.log commands.txt \
    > checksums.sha256
)
```

Run the read-only aggregator. It does not start a server, call Google, open a
WebSocket, deploy, flash, reset, or operate hardware:

```bash
python3 scripts/google_live_release_gate.py \
  --expected-git-sha "$CANDIDATE_SHA" \
  --expected-image-digest "$CANDIDATE_IMAGE_DIGEST" \
  --expected-firmware-identity "$FIRMWARE_IDENTITY" \
  --expected-config-fingerprint "$CONFIG_FINGERPRINT" \
  --expected-fixture-sha256 "$FIXTURE_SHA256" \
  --layer "deterministic=$EVIDENCE_ROOT/deterministic/report.json" \
  --layer "server_regression=$EVIDENCE_ROOT/server-regression/report.json" \
  --layer "real_api=$EVIDENCE_ROOT/real-api/report.json" \
  --layer "websocket_e2e=$EVIDENCE_ROOT/websocket-e2e/report.json" \
  --layer "physical=$EVIDENCE_ROOT/physical/report.json" \
  --layer "candidate_soak=$EVIDENCE_ROOT/candidate-soak/report.json" \
  --checksums-file "$EVIDENCE_ROOT/checksums.sha256" \
  --out "$EVIDENCE_ROOT/release-verdict.json"
```

Release only when `release-verdict.json` is `PASS`, contains all six layers,
reports no failures, and every checksum is verified.

## 8. Triage and rollback boundary

| Failure | First evidence to inspect | Required action |
|---|---|---|
| Deterministic or historical contract | `deterministic/pytest.xml` and compatibility matrix | Stop; fix code/tests before any physical rerun |
| Real API auth/quota/config | `real-api/report.json` classified failure | Fix credential/service/config; do not retry as transport recovery |
| Raw WebSocket `SKIPPED/PENDING` | `websocket-e2e/transport.json` and Task 5 correlation | Expected until exact bounded correlation; never waive it |
| Lifecycle/replay/ownership failure | `server-regression/report.json` and matching `timeline.log` scope | Stop; preserve the window and fix the invariant |
| Physical latency/self-interrupt/stale audio | `physical/audit.json` plus operator timestamps | Check LAN/AEC/firmware posture, then reproduce on the same candidate |
| Soak duration/resource/cleanup failure | `candidate-soak/report.json` | Stop; do not pad duration, drop samples, or reuse another candidate's evidence |
| Identity/checksum mismatch | `checksums.sha256` and each `candidateIdentity` | Rebuild the evidence set; never edit identity or regenerate checksums to force PASS |

Rollback or hold the release at the first failed layer. A network or hardware
availability issue remains blocking `SKIPPED`, not product PASS. Do not change
the model, prompt, voice, language, timeout, reconnect, fallback, lesson
ownership, or firmware protocol merely to make the gate pass.
