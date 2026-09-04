# Google Live unified evidence smoke

This runbook preserves the production Google Live model, voice, language,
prompt/configuration, fallback, reconnect, lesson, and conversation behavior.
The unified runner only coordinates evidence commands. It never deploys the
server, installs an image, flashes firmware, resets hardware, or controls a
robot outside the explicitly confirmed evidence journeys.

Run from `main/tbot-server` with the exact clean candidate checkout. The real
journey is opt-in and requires live Google/API, authenticated server, and
operator-controlled robot access. For offline verification, use the synthetic
command in Section 1.

## 1. Offline synthetic end-to-end check

This command uses checked-in fixtures and fake child processes. It uses no live
credentials, network endpoint, microphone, speaker, or hardware:

```bash
cd main/tbot-server
SYNTHETIC_ROOT="$(mktemp -d)/google-live-evidence"
python3 scripts/google_live_evidence_runner.py synthetic-dry-run "$SYNTHETIC_ROOT"
```

Success prints one JSON object with `"status": "PASS"`. The synthetic path
executes the same state machine, eight-command ordering, provenance, runtime
closure, checksum, timeline, and release-gate code as the real path.

## 2. Protected inputs and exact candidate identity

Use a Bash shell. Keep the evidence base outside the candidate repository and
on access-controlled storage. Set these values from the separately approved
deployment and operator environment; do not paste secrets into this document,
shell history, an operator config file, or an evidence artifact:

```bash
cd main/tbot-server
set -euo pipefail

: "${GOOGLE_LIVE_EVIDENCE_BASE:?set an existing protected absolute directory}"
: "${CANDIDATE_IMAGE_DIGEST:?set sha256:<64 lowercase hex>}"
: "${FIRMWARE_IDENTITY:?set the identity read from installed firmware}"
: "${AUDIO_FIXTURE:?set an approved consenting-adult WAV path}"
: "${BASELINE_REPORT:?set the same-environment baseline report path}"
: "${LESSON_MANIFEST:?set the exact lesson manifest path}"
: "${SERVER_LOG_SOURCE:?set the production server log path}"
: "${WEBSOCKET_URL:?set the authenticated WebSocket URL}"
: "${EVIDENCE_CONTROL_URL:?set the authenticated evidence-control URL}"
: "${BASE_URL:?set the authenticated server base URL}"
: "${DEVICE_ID:?set the robot device ID}"
: "${CLIENT_ID:?set the client ID}"

read -r -s -p "GOOGLE_API_KEY: " GOOGLE_API_KEY
echo
read -r -s -p "TBOT_DEVICE_MINT_SECRET: " TBOT_DEVICE_MINT_SECRET
echo
export GOOGLE_API_KEY TBOT_DEVICE_MINT_SECRET

EVIDENCE_BASE="$(cd "$GOOGLE_LIVE_EVIDENCE_BASE" && pwd -P)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_ROOT="$EVIDENCE_BASE/$RUN_ID"
SETUP_DIR="$EVIDENCE_BASE/.setup-$RUN_ID"
CANDIDATE_SHA="$(git rev-parse HEAD)"
test -z "$(git status --porcelain=v1 -z --untracked-files=all --no-renames)"
mkdir -m 700 "$SETUP_DIR"
export RUN_ID RUN_ROOT SETUP_DIR CANDIDATE_SHA CANDIDATE_IMAGE_DIGEST
export FIRMWARE_IDENTITY AUDIO_FIXTURE
```

Generate the effective configuration and identity with the same production
builders used by the smoke path. This retains model
`gemini-3.1-flash-live-preview` and voice `Kore`; do not edit the generated
JSON by hand:

```bash
python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

from scripts.google_live_reliability import build_candidate_identity
from scripts.google_live_smoke import _build_env_config, _build_round_trip_config

fixture = Path(os.environ["AUDIO_FIXTURE"]).resolve(strict=True)
setup = Path(os.environ["SETUP_DIR"])
config = _build_round_trip_config(
    _build_env_config("gemini-3.1-flash-live-preview", "Kore"), fixture
)
identity = build_candidate_identity(
    git_sha=os.environ["CANDIDATE_SHA"],
    image_digest=os.environ["CANDIDATE_IMAGE_DIGEST"],
    firmware_identity=os.environ["FIRMWARE_IDENTITY"],
    config=config,
    fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest(),
)
(setup / "effective-config.json").write_text(
    json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
(setup / "candidate-identity.json").write_text(
    json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

python3 scripts/google_live_evidence_runner.py init "$EVIDENCE_BASE" \
  --run-id "$RUN_ID" \
  --identity-json "$SETUP_DIR/candidate-identity.json" \
  --effective-config-json "$SETUP_DIR/effective-config.json" \
  --fixture "$AUDIO_FIXTURE"
```

Initialization fails closed unless the Git HEAD is exact, the worktree is
clean (including untracked files and rename ambiguity), the evidence base is a
canonical absolute directory, and the effective config plus fixture reproduce
the supplied identity.

Install only the non-secret inputs into the new run and create the validated
operator configuration:

```bash
install -m 600 "$AUDIO_FIXTURE" "$RUN_ROOT/fixture.wav"
install -m 600 "$SETUP_DIR/candidate-identity.json" "$RUN_ROOT/candidate-identity.json"
mkdir -m 700 "$RUN_ROOT/baseline"
install -m 600 "$BASELINE_REPORT" "$RUN_ROOT/baseline/report.json"
install -m 600 "$LESSON_MANIFEST" "$RUN_ROOT/lesson-manifest.json"
: > "$RUN_ROOT/server.log"
chmod 600 "$RUN_ROOT/server.log"
export WEBSOCKET_URL EVIDENCE_CONTROL_URL BASE_URL DEVICE_ID CLIENT_ID

python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["RUN_ROOT"]).resolve(strict=True)
operator = {
    "fixture": str(root / "fixture.wav"),
    "websocket_url": os.environ["WEBSOCKET_URL"],
    "device_id": os.environ["DEVICE_ID"],
    "client_id": os.environ["CLIENT_ID"],
    "journey_id": "websocket-" + os.environ["RUN_ID"],
    "server_log": str(root / "server.log"),
    "expected_candidate_json": str(root / "candidate-identity.json"),
    "evidence_control_url": os.environ["EVIDENCE_CONTROL_URL"],
    "baseline_report": str(root / "baseline/report.json"),
    "lesson_manifest": str(root / "lesson-manifest.json"),
    "base_url": os.environ["BASE_URL"],
}
(root / "operator-config.json").write_text(
    json.dumps(operator, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
```

`operator-config.json` contains protected endpoint and device metadata, but no
credential or transcript text. Keep it mode `0600`. The API key and mint
secret are read only from their named environment variables and provenance
records only `<env:GOOGLE_API_KEY>` or `<env:TBOT_DEVICE_MINT_SECRET>`.

## 3. Execute the exact eight-command sequence

Define a bounded server-log capture in the current shell. Stop it before either
standalone WebSocket analyzer reads the file; restart it for the soak and
physical journeys, which need live log growth:

```bash
SERVER_LOG_CAPTURE_PID=""
start_server_log_capture() {
  test -z "$SERVER_LOG_CAPTURE_PID"
  tail -n 0 -F "$SERVER_LOG_SOURCE" >> "$RUN_ROOT/server.log" &
  SERVER_LOG_CAPTURE_PID=$!
}
stop_server_log_capture() {
  if test -n "$SERVER_LOG_CAPTURE_PID"; then
    kill "$SERVER_LOG_CAPTURE_PID" 2>/dev/null || true
    wait "$SERVER_LOG_CAPTURE_PID" 2>/dev/null || true
    SERVER_LOG_CAPTURE_PID=""
  fi
}
trap stop_server_log_capture EXIT INT TERM
```

Run the commands exactly once and in this order:

```bash
python3 scripts/google_live_evidence_runner.py deterministic "$RUN_ROOT" \
  --operator-config "$RUN_ROOT/operator-config.json"
python3 scripts/google_live_evidence_runner.py real-api "$RUN_ROOT" \
  --operator-config "$RUN_ROOT/operator-config.json"
start_server_log_capture
python3 scripts/google_live_evidence_runner.py websocket-transport "$RUN_ROOT" \
  --operator-config "$RUN_ROOT/operator-config.json"
stop_server_log_capture
python3 scripts/google_live_evidence_runner.py websocket-log-analysis "$RUN_ROOT" \
  --operator-config "$RUN_ROOT/operator-config.json"
python3 scripts/google_live_evidence_runner.py websocket-correlation "$RUN_ROOT" \
  --operator-config "$RUN_ROOT/operator-config.json"
```

The candidate-soak producer accepts its audio paths and expected intents only
through protected stdin. Paste one JSON object directly into the terminal,
then send EOF. Do not save this object in the run directory or command log:

```bash
start_server_log_capture
python3 scripts/google_live_evidence_runner.py candidate-soak-produce "$RUN_ROOT" \
  --operator-config "$RUN_ROOT/operator-config.json" < /dev/tty
```

The protected object has this schema (values shown are placeholders):

```json
{"bargein":{"initialAudioPath":"/protected/adult-initial.wav","initialExpected":"<initial intent>","newestAudioPath":"/protected/adult-newest.wav","newestExpected":"<newest intent>"},"robotSpeaking":{"triggerAudioPath":"/protected/adult-trigger.wav"}}
```

After the producer closes all 33 executions and cleanup evidence, replay the
immutable manifest:

```bash
python3 scripts/google_live_evidence_runner.py candidate-soak-replay "$RUN_ROOT" \
  --operator-config "$RUN_ROOT/operator-config.json"
```

Run the physical step only after completing the operator checks in
`docs/google-live-robot-validation.md`. It requires both explicit flags and a
protected transcript plan on stdin:

```bash
python3 scripts/google_live_evidence_runner.py physical "$RUN_ROOT" \
  --operator-confirmed \
  --transcript-plan-stdin \
  --operator-config "$RUN_ROOT/operator-config.json" < /dev/tty
```

Stop and reap the log capture before finalization so every evidence input is
stable:

```bash
stop_server_log_capture
trap - EXIT INT TERM
python3 scripts/google_live_evidence_runner.py finalize "$RUN_ROOT"
python3 scripts/google_live_evidence_runner.py status "$RUN_ROOT"
unset GOOGLE_API_KEY TBOT_DEVICE_MINT_SECRET
```

`finalize` reopens every artifact, verifies digests and provenance, runs the
real release gate in-process, and writes `release-verdict.json`. It succeeds
only when all six layers and all support artifacts are `PASS`.

## 4. Evidence layout and checksum semantics

The completed run contains:

```text
<RUN_ROOT>/
  run-state.json
  operator-config.json              # transient, not release evidence
  server.log                        # transient raw input, never publish
  baseline/report.json              # bound input
  lesson-manifest.json              # bound input
  fixture.wav                       # bound input
  candidate-identity.json
  runtime-closure.json
  deterministic/report.json
  deterministic/node-manifest.txt
  deterministic/pytest.xml
  real-api/report.json
  websocket-e2e/transport.json
  websocket-e2e/server-report.json
  websocket-e2e/report.json
  candidate-soak/journey-evidence.json
  candidate-soak/report.json
  server-regression/report.json
  physical/terminal-snapshot.json
  physical/report.json
  commands.jsonl
  commands.txt
  timeline.log
  checksums.sha256
  release-verdict.json
```

`commands.jsonl` is the canonical structured execution provenance;
`commands.txt` is its deterministic redacted projection. `timeline.log` is a
JSONL index of artifact labels, journey/window IDs, and UTC bounds only. It
must never contain copied raw log lines.

`checksums.sha256` is an external manifest: it hashes the six reports plus the
deterministic manifest/JUnit, runtime closure, command provenance/projection,
and timeline index. It deliberately does not hash itself or the later
`release-verdict.json`. The verdict binds the exact checksums and candidate
identity after independently reopening all inputs.

`server.log`, protected stdin, credentials, raw/base64 audio, transcripts,
prompts, cookies, tokens, exception text, and session-resumption handles are
not release artifacts. Retain the raw log only as long as required to finish
the bounded run, then remove it under the organization's secure evidence
retention policy.

## 5. Failure and retry policy

Any `FAIL`, terminal `SKIPPED`, timeout, cancellation, cleanup failure,
identity drift, repository change, or checksum mismatch permanently closes the
run. Preserve its first safe failure code and artifacts for triage. Do not edit
the run, rerun a command in place, use `--resume`, or replace a report.

Correct the classified issue, verify the exact candidate again, generate a new
UTC `RUN_ID`, and execute all eight commands from the beginning. A missing
credential, endpoint, server log, firmware, or operator confirmation is a
blocked release, never permission to weaken the gate.

## 6. Historical compatibility matrix

Before production release, also run the full server, classic voice, adjacent
regression, firmware contract, documentation, and synthetic verification
matrix from the implementation plan. Guarded live/hardware tests retain their
existing skip behavior; a skip does not count as real evidence for this
release run.
