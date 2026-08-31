# Google Live robot validation and release gate

Run this after the deterministic and real-API gates in `google-live-smoke.md`
pass. Physical execution is operator-controlled: this runbook does not deploy,
flash, reset, or control a robot automatically.

Use the exact `RUN_ID`, `EVIDENCE_ROOT`, candidate identity, configuration
fingerprint, and fixture checksum exported by the smoke runbook.

Production release is currently blocked by four software gaps documented below:
there is no standalone bounded log-report producer, production firmware does
not establish a candidate-bound physical journey scope, there is no operator
producer for the 33-execution soak manifest, and metadata-only transcript logs
cannot provide the physical expected-match proof. The preflight and physical
sequence remain useful for diagnosis, but they are not a complete release gate
until all four gaps are fixed.

## 1. Target evidence layout and privacy

`RUN_ID` must be UTC, for example `20260830T140500Z`:

This is the required post-remediation release layout, not a claim that every
artifact can be produced today.

```text
task-artifacts/google-live/$RUN_ID/
  deterministic/report.json
  server-regression/report.json       # unavailable until producer remediation
  real-api/report.json
  websocket-e2e/report.json           # unavailable until producer remediation
  physical/report.json                # unavailable until producer remediation
  candidate-soak/report.json          # unavailable until producer remediation
  timeline.log
  commands.txt
  checksums.sha256
  release-verdict.json
```

Currently capturable intermediates include `websocket-e2e/timeline.log`,
`websocket-e2e/transport.json`, `websocket-e2e/correlated.json`,
`physical/timeline.log`, `pytest.xml`, and operator notes. The standalone
`websocket-e2e/log-report.json`, physical candidate audit, soak report, and
release reports remain unavailable until the documented producers exist. After
remediation, each analyzer input must contain exactly one reliability start/end
anchor. The top-level `timeline.log` must be a privacy-safe index of the
separate window paths, window IDs, and UTC bounds; it must never concatenate raw
journey logs.
`commands.txt` records commands with `$GOOGLE_API_KEY`, `$OTA_TOKEN`, device IDs,
and protected paths left as redacted variable names, never expanded values.

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

The WebSocket window is captured separately by `google-live-smoke.md`. Start a
new physical-only capture immediately before the physical journey:

```bash
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$EVIDENCE_ROOT/physical/server-start-utc.txt"
tail -n 0 -F tmp/server.log > "$EVIDENCE_ROOT/physical/timeline.log" &
PHYSICAL_LOG_PID=$!
```

After the final bounded journey:

```bash
kill "$PHYSICAL_LOG_PID"
wait "$PHYSICAL_LOG_PID" 2>/dev/null || true
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$EVIDENCE_ROOT/physical/server-end-utc.txt"
```

This physical file is currently diagnostic only. Production firmware does not
send the optional `evidence_journey_id` in its hello message, so the server does
not establish `google_live_evidence_journey_id` or emit candidate-bound scoped
reliability markers. Separately, normal raw server logs do not emit the
reliability-window start/end anchors required by the standalone analyzers. Do
not insert scope or anchors manually or reuse the synthetic WebSocket hello:
none would prove the production firmware journey. After a production producer
is implemented, the physical analyzer input must contain exactly one produced
start anchor and one matching end anchor. Never merge it with the WebSocket
file or any candidate-soak execution window.

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

## 4. Supported WebSocket correlation and unavailable lifecycle reports

The command in `google-live-smoke.md` can currently produce and validate
`websocket-e2e/correlated.json`. It selects the raw server lines using the
transport report's UTC window, adds anchors only in a temporary file, and
correlates that temporary log verdict with the exact transport evidence.

It does not persist the temporary verdict as
`websocket-e2e/log-report.json`. Directly running
`analyze_reliability_window()` on either raw timeline is unsupported because
normal server logs lack the required start/end anchors. Consequently:

- `server-regression/report.json` cannot currently be produced from
  `physical/timeline.log`;
- `websocket-e2e/report.json` cannot currently be built with the separate
  `logEvidence` object required by `google_live_release_gate.py`;
- the passing correlated WebSocket artifact is diagnostic evidence, not a
  substitute for either missing release report.

A checked-in producer must expose the validated standalone bounded verdict
without fabricating anchors or accepting foreign log lines. The physical path
also requires the production firmware journey scope described in Section 2.

## 5. Production-candidate soak: current release blocker

The checked-in CLI cannot currently produce
`candidate-soak/journey-evidence.json`. In `--mode candidate`,
`google_live_robot_soak.py` requires `--journey-evidence`, reads the existing
file, and replays/validates it. It does not open the WebSocket to record the 33
executions. The only producer path is the injected `candidate_journeys` mapping
used by in-process tests; it is not exposed through argparse or an operator CLI.

A valid producer must record, rather than synthesize:

- exactly 33 uniquely scoped execution reports in the fixed 17 conversation,
  10 barge-in, two quiet, reopen, reconnect, lesson, and post-lesson order;
- one UTC log window and server-issued evidence scope per execution, with no
  duplicate anchors and at most the configured 10-second inter-window gap;
- monitored quiet-padding windows when needed to reach 1800 seconds;
- resource samples before work, after every execution, after every padding
  window, and after cleanup, each with a unique `sampleId`;
- exactly one candidate-bound cleanup record proving closed WebSocket/provider,
  zero owned tasks, sessions, and receive loops.

Until a checked-in operator producer emits that contract, the candidate-soak
layer is `SKIPPED`/missing and production release is blocked. Do not hand-author,
copy from tests, or transform logs into a manifest. The replay command is a
validator, not evidence that the 30-minute workload ran. It also consumes the
currently unavailable standalone WebSocket log report, so both producers must
exist before this future command is runnable.

After both the journey-manifest and standalone log-report producers exist,
validate the manifest with the current replay CLI:

```bash
python3 scripts/google_live_robot_soak.py \
  --mode candidate --cycles 10 \
  --candidate-git-sha "$CANDIDATE_SHA" \
  --candidate-image-digest "$CANDIDATE_IMAGE_DIGEST" \
  --firmware-identity "$FIRMWARE_IDENTITY" \
  --fixture-sha256 "$FIXTURE_SHA256" \
  --config-json "$CONFIG_JSON" \
  --baseline-report "<b07038b8-same-environment-baseline.json>" \
  --real-api-report "$EVIDENCE_ROOT/real-api/report.json" \
  --transport-report "$EVIDENCE_ROOT/websocket-e2e/transport.json" \
  --correlated-transport-report "$EVIDENCE_ROOT/websocket-e2e/correlated.json" \
  --log-reliability-report "$EVIDENCE_ROOT/websocket-e2e/log-report.json" \
  --journey-evidence "$EVIDENCE_ROOT/candidate-soak/journey-evidence.json" \
  --lesson-manifest "<exact-lesson-manifest.json>" \
  --minimum-turns 30 --minimum-duration-sec 1800 \
  --report "$EVIDENCE_ROOT/candidate-soak/report.json"
```

## 6. Physical production-candidate audit: two current release blockers

The first blocker is candidate scope. Production firmware does not send
`evidence_journey_id`, so the physical connection lacks the journey-bound scope
and start/end anchors required to tie its log evidence to the exact candidate.
Test fixtures that construct marker lines or synthetic WebSocket clients that
send the field are not production evidence. A production-safe firmware/runtime
producer must create this scope before `server-regression/report.json` or a
candidate physical audit can be generated.

The second blocker is transcript-match proof. The physical CLI currently
requires the production report to contain exactly
10 `expected_user_transcripts`, 10 expected matches, and 10 post-interrupt
expected matches. `--expected-user-transcript` is repeatable and each occurrence
adds one expected phrase. However, production Google Live logs intentionally
emit only `transcript source=user chars=N`; `_expected_user_transcript_match_count`
can match only legacy log lines containing raw `text=...`.

Therefore repeating the flag ten times is not a valid workaround: privacy-safe
production logs provide no text for any of the ten matches. Enabling raw
transcript logging, adding transcript text to `physical/timeline.log`,
fabricating legacy lines, or copying phrases into retained artifacts is
forbidden. A future privacy-safe producer must emit candidate-bound match/count
proof without raw speech, and the audit must validate that proof.

Until both physical remediations, the standalone log-report producer, and the
candidate-soak producer exist, do not claim the following production profile is
runnable or release-eligible. The interface below is retained only to show the
remaining validator inputs after remediation:

```bash
python3 scripts/physical_smoke_audit.py "$EVIDENCE_ROOT/physical/timeline.log" \
  --device-id "<robot-device-id>" \
  --client-id "<robot-client-id>" \
  --server-ip "<server-ip>" \
  --min-interrupts 10 \
  --expected-user-transcript "<expected-utterance-01>" \
  --expected-user-transcript "<expected-utterance-02>" \
  --expected-user-transcript "<expected-utterance-03>" \
  --expected-user-transcript "<expected-utterance-04>" \
  --expected-user-transcript "<expected-utterance-05>" \
  --expected-user-transcript "<expected-utterance-06>" \
  --expected-user-transcript "<expected-utterance-07>" \
  --expected-user-transcript "<expected-utterance-08>" \
  --expected-user-transcript "<expected-utterance-09>" \
  --expected-user-transcript "<expected-utterance-10>" \
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

Do not execute this candidate command against metadata-only production logs and
then weaken its failure. The expected phrases may be runtime-only inputs after a
privacy-safe proof path exists; they must still be redacted from `commands.txt`
and absent from retained reports.

Only after all required producers exist and the raw audit genuinely passes,
wrap it with the exact upstream evidence required by the release gate. This is
a future, non-runnable interface while any blocker remains:

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

After all four blockers are implemented and all reports exist, create the
top-level timeline as an index of bounded windows. The following commands in
this section are future release steps and are not currently runnable. The index
contains references and UTC metadata only; no raw log lines are concatenated:

```bash
EVIDENCE_ROOT="$EVIDENCE_ROOT" python3 - <<'PY'
import json, os
from pathlib import Path

root = Path(os.environ["EVIDENCE_ROOT"])
websocket = json.loads((root / "websocket-e2e/log-report.json").read_text())
physical = json.loads((root / "server-regression/report.json").read_text())
soak = json.loads((root / "candidate-soak/report.json").read_text())
rows = [
    {"layer": "websocket_e2e", "artifact": "websocket-e2e/timeline.log", "logWindow": websocket["logWindow"]},
    {"layer": "physical", "artifact": "physical/timeline.log", "logWindow": physical["logWindow"]},
]
rows.extend(
    {"layer": "candidate_soak", "journeyId": item["journeyId"], "logWindow": item["logWindow"]}
    for item in soak["evidenceExecutions"]
)
rows.extend(
    {"layer": "candidate_soak_padding", "journeyId": item["journeyId"], "logWindow": item["logWindow"]}
    for item in soak["quietPadding"]
)
(root / "timeline.log").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
PY
```

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
    websocket-e2e/timeline.log websocket-e2e/log-report.json \
    physical/timeline.log timeline.log commands.txt \
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
| Standalone bounded log report unavailable | Raw timelines lack persisted start/end anchors; correlation keeps its synthesized verdict temporary | Software release blocker; expose the validated bounded verdict through a checked-in producer, never hand-insert anchors |
| Physical candidate scope unavailable | Production firmware hello omits `evidence_journey_id` | Software release blocker; add a production-safe journey scope and anchor producer, never substitute the synthetic WebSocket client |
| Lifecycle/replay/ownership failure | The layer report and its matching single-window log | Stop; preserve that window; never concatenate another anchored journey |
| Physical latency/self-interrupt/stale audio | `physical/audit.json` plus operator timestamps | Check LAN/AEC/firmware posture, then reproduce on the same candidate |
| Physical expected-match proof unavailable | Metadata-only transcript logs and repeated flag semantics | Software release blocker; implement privacy-safe proof, never enable/store raw transcripts |
| Candidate manifest producer unavailable | `--journey-evidence` replay-only CLI path | Software release blocker; implement a trusted operator producer, never hand-author evidence |
| Soak duration/resource/cleanup failure | `candidate-soak/report.json` | Stop; do not synthesize duration, drop samples, or reuse another candidate's evidence |
| Identity/checksum mismatch | `checksums.sha256` and each `candidateIdentity` | Rebuild the evidence set; never edit identity or regenerate checksums to force PASS |

Rollback or hold the release at the first failed layer. A network or hardware
availability issue remains blocking `SKIPPED`, not product PASS. Do not change
the model, prompt, voice, language, timeout, reconnect, fallback, lesson
ownership, or firmware protocol merely to make the gate pass.
