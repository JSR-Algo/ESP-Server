# Google Live robot validation and release gate

Use this runbook with `docs/google-live-smoke.md`. It covers only the
operator-controlled candidate soak and physical Vietnamese journey. No command
here deploys the server, installs an image, flashes firmware, resets hardware,
or autonomously controls a robot.

The reviewed firmware evidence source is Git SHA
`351fd7f8737afd83dc572895b7cfe1d8248d0766`. Build and install it through the
normal separately reviewed operator process. Before collecting evidence, read
the identity from the firmware actually running on the robot and export that
exact value as `FIRMWARE_IDENTITY`. An intended version, branch, tag, local
build directory, or source SHA alone is not proof of installed firmware.

## 1. Operator preflight

Complete these checks before starting the unified runner:

```bash
set -euo pipefail
test -n "${RUN_ROOT:?run the smoke runbook setup first}"
test -n "${DEVICE_ID:?set the robot device ID}"
test -n "${CLIENT_ID:?set the client ID}"
test -n "${FIRMWARE_IDENTITY:?set the installed firmware identity}"
test -n "${TBOT_DEVICE_MINT_SECRET:?load the mint secret into the environment}"
curl -fsSI "$BASE_URL"
python3 scripts/voice_mode_preflight.py \
  --device-ip "$ROBOT_IP" \
  --max-loss-pct 0 \
  --max-avg-ms 1000 \
  --max-max-ms 1500 \
  --max-jitter-ms 500 \
  --max-duplicates 0
test -r "$SERVER_LOG_SOURCE"
```

Confirm the robot uses the production-equivalent microphone, speaker, AEC
posture, LAN, and installed firmware identity. Confirm the server image digest,
Git SHA, generated effective config, fixture checksum, baseline report, lesson
manifest, device/client IDs, and endpoints match the initialized run.

Do not continue if another session owns the robot, the server log is shared
with an unbounded capture, the network is unstable, or the candidate identity
cannot be read back.

## 2. Candidate soak protected input

The producer performs the fixed 33-execution lifecycle: 17 conversation, 10
barge-in, two quiet modes, reopen, reconnect, lesson, and post-lesson
conversation. It also records quiet padding until the 30-minute minimum,
resource samples, scoped server evidence, and exactly-once cleanup.

Prepare three distinct, regular, non-symlink consenting-adult WAV files at the
configured sample rate. When the smoke runbook invokes
`candidate-soak-produce`, paste this JSON directly into protected stdin and
send EOF:

```json
{"bargein":{"initialAudioPath":"/protected/adult-initial.wav","initialExpected":"<initial intent>","newestAudioPath":"/protected/adult-newest.wav","newestExpected":"<newest intent>"},"robotSpeaking":{"triggerAudioPath":"/protected/adult-trigger.wav"}}
```

The two expected intents must be distinct. Their normalized bytes exist only
in process memory and are zeroed after use. Do not redirect the object from a
file inside the evidence run, add it to `commands.txt`, or include the text in
reports. The producer publishes `candidate-soak/journey-evidence.json` only
after every execution and cleanup contract closes; the replay command then
creates `candidate-soak/report.json`.

## 3. Physical Vietnamese journey

The physical command first enrolls an authenticated evidence journey and
prints `READY journey_id=...`. Only then perform the following sequence near
the robot, speaking Vietnamese naturally through the real microphone:

1. Complete ten ordinary conversation turns and confirm each answer addresses
   the latest intent with prompt first audio.
2. Complete ten barge-in turns. Interrupt while robot audio is actively
   playing; confirm old audio stops, never resumes, and the newest request owns
   the response.
3. Hold one silence interval and one robot-speaking interval; confirm no false
   interruption or echo-driven request.
4. Reopen the same listening flow, then disconnect/reconnect once and complete
   the required follow-up turns.
5. Say the approved Vietnamese lesson-start intent, finish one interactive
   Google Live lesson step, exit or complete the bounded lesson, then make one
   final ordinary request.

The protected stdin plan must contain exactly 11 ordered slots: ten
`interrupt` phrases followed by one `post_lesson` phrase. Paste the following
shape directly into the terminal used by the `physical` command, replace only
the placeholder text and IDs, then send EOF:

```json
{"clientId":"<same client ID>","journeyId":"physical-<RUN_ID>","ttlSec":300,"transcriptPlan":[{"slot":1,"phase":"interrupt","text":"<phrase 01>"},{"slot":2,"phase":"interrupt","text":"<phrase 02>"},{"slot":3,"phase":"interrupt","text":"<phrase 03>"},{"slot":4,"phase":"interrupt","text":"<phrase 04>"},{"slot":5,"phase":"interrupt","text":"<phrase 05>"},{"slot":6,"phase":"interrupt","text":"<phrase 06>"},{"slot":7,"phase":"interrupt","text":"<phrase 07>"},{"slot":8,"phase":"interrupt","text":"<phrase 08>"},{"slot":9,"phase":"interrupt","text":"<phrase 09>"},{"slot":10,"phase":"interrupt","text":"<phrase 10>"},{"slot":11,"phase":"post_lesson","text":"<post-lesson phrase>"}]}
```

The command hashes normalized phrases with an ephemeral HMAC key before
enrollment. Reports retain only ordered match/count proof. Raw transcript text,
the HMAC key, and its input plan must not be persisted. The key is zeroed on
every exit path.

The operator must watch the full journey and abort on wrong firmware, wrong
device/client, stale audio, missing interruption, unexpected session ownership,
or unsafe robot behavior. Explicit `--operator-confirmed` acknowledges this
human responsibility; it is not an automation bypass.

## 4. Physical evidence outputs

The physical producer polls the authenticated evidence-control endpoint until
the 11 slots match in order and the server marks the journey ready. It then
finalizes and re-reads the authoritative terminal snapshot, selects only that
journey from the raw server log, and runs the existing production audit.

Success atomically produces:

```text
server-regression/report.json
physical/terminal-snapshot.json
physical/report.json
```

The bounded selected log is temporary. `server-regression/report.json` is
produced directly by the physical command and is bound to the same candidate,
journey, UTC window, terminal snapshot, and candidate-soak report. Foreign,
missing, duplicate, malformed, or out-of-window markers fail closed.

The physical audit enforces at least 11 first-audio samples, 10 interrupt-stop
samples, 10 physical-barge-in samples, 10 server-output-gap samples, balanced
receive-loop ownership, lesson/post-lesson completion, and the production
latency budgets already encoded by `physical_smoke_audit.py`.

## 5. Privacy and artifact handling

Allowed retained evidence is limited to candidate identity, safe aggregate
metrics, opaque journey/window/session identifiers, UTC bounds, result counts,
safe failure codes, checksums, and redacted command provenance.

Never retain raw/base64 audio, raw transcript or prompt text, credentials,
tokens, cookies, authorization headers, session-resumption handles, raw
exception text, or copied server-log lines in reports, `commands.jsonl`,
`commands.txt`, `timeline.log`, or the verdict. Do not put real protected paths
or endpoint query secrets in command notes.

The raw `server.log` is an ephemeral operator input. Restrict access while the
run is active, stop capture before finalization, and remove it after the final
gate according to the organization's secure retention policy. Never transform
or hand-edit it to make an analyzer pass.

## 6. Final release decision

Run `finalize` only after the exact eight commands complete in order. Release
requires all six layers to be present and `PASS` for one identical candidate:

```text
deterministic
server_regression
real_api
websocket_e2e
physical
candidate_soak
```

The gate revalidates the clean candidate Git object, approved interpreter and
dependency closure, every report schema and candidate identity, command input
and output bindings, the deterministic command projection, privacy-safe
timeline index, artifact aliases/hardlinks/symlinks, and external checksum
manifest. `release-verdict.json` must contain `"status":"PASS"`; any missing,
`PENDING`, `SKIPPED`, or failed layer blocks release.

This evidence result authorizes neither deployment nor hardware changes. The
actual release remains a separate operator-controlled process.

## 7. Triage, cancellation, and retry

On failure or cancellation, stop the bounded capture, preserve the failed run,
and classify only from safe codes and aggregate artifacts. Do not paste raw
logs, transcripts, keys, tokens, or exception bodies into tickets or reports.

Common classifications are `AUTH`, `CONFIG`, `QUOTA`, `PROTOCOL`, `TIMEOUT`,
`NETWORK`, `PROVIDER`, `CLEANUP`, `RESOURCE`, `IDENTITY`, `PRIVACY`,
`EVIDENCE_INTEGRITY`, and `CANCELLED`.

Never resume or repair a terminal run. Resolve the cause, confirm the exact
server and firmware candidates again, create a new UTC `RUN_ID`, and rerun all
eight commands. Preserve the first failed run for comparison. Do not weaken a
budget, remove a test, fabricate a marker, copy a report, or reuse evidence
from another candidate.
