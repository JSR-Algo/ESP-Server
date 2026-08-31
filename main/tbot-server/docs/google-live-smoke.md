# Google Live production reliability smoke

This runbook preserves the current Google Live model, voice, language, reconnect,
fallback, lesson ownership, and firmware protocol. Run from `main/tbot-server`
with Python 3.11 or newer. Real Google API and WebSocket commands are opt-in;
missing credentials, server access, or hardware is `SKIPPED` and blocks release.

Never put a real key, OTA token, cookie, transcript, prompt, exception text, or
session-resumption handle in `commands.txt` or a report. Raw child audio is not
stored by default. Use only the checked-in synthetic/consenting-adult fixture.

## 1. Candidate identity and evidence root

Use one UTC run ID and one exact identity for every layer. `CONFIG_JSON` is the
effective non-secret Google Live configuration; do not add credentials or a
session-resumption handle.

```bash
cd main/tbot-server
set -euo pipefail
test -z "$(git status --porcelain=v1 --untracked-files=all)" || {
  echo "Refusing evidence collection from a dirty or untracked worktree" >&2
  exit 1
}
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_ROOT="task-artifacts/google-live/$RUN_ID"
CANDIDATE_SHA="$(git rev-parse HEAD)"
CANDIDATE_IMAGE_DIGEST="sha256:<64-lowercase-hex-image-digest>"
FIRMWARE_IDENTITY="<production-firmware-build-id>"
AUDIO_FIXTURE="tests/fixtures/tvideo_farm_audio/adult_speech_24k_mono.wav"
FIXTURE_SHA256="$(shasum -a 256 "$AUDIO_FIXTURE" | awk '{print $1}')"
CONFIG_JSON="$(AUDIO_FIXTURE="$AUDIO_FIXTURE" python3 -c 'import json,os; from pathlib import Path; from scripts.google_live_smoke import _build_env_config,_build_round_trip_config; config=_build_round_trip_config(_build_env_config("gemini-3.1-flash-live-preview","Kore"),Path(os.environ["AUDIO_FIXTURE"])); print(json.dumps(config,sort_keys=True,separators=(",",":")))')"
CONFIG_FINGERPRINT="$(CONFIG_JSON="$CONFIG_JSON" python3 -c 'import json,os; from scripts.google_live_reliability import build_candidate_identity; value=json.loads(os.environ["CONFIG_JSON"]); print(build_candidate_identity("identity-only","sha256:"+"0"*64,"identity-only",value,"0"*64)["configFingerprint"])')"
mkdir -p "$EVIDENCE_ROOT"/{deterministic,server-regression,real-api,websocket-e2e,physical,candidate-soak}
printf '%s\n' \
  '# Redacted operator command log' \
  '# Manual templates preserve intended arguments but keep secrets, identities, and protected paths as variable names or placeholders.' \
  > "$EVIDENCE_ROOT/commands.txt"
COMMAND_LOG="$EVIDENCE_ROOT/commands.txt"
record_command() {
  local entry
  entry="$(cat)"
  test -n "$entry"
  printf '%s\n\n' "$entry" >> "$COMMAND_LOG"
}
record_command <<'CMD'
cd main/tbot-server
set -euo pipefail
test -z "$(git status --porcelain=v1 --untracked-files=all)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_ROOT="task-artifacts/google-live/$RUN_ID"
CANDIDATE_SHA="$(git rev-parse HEAD)"
CANDIDATE_IMAGE_DIGEST="sha256:<64-lowercase-hex-image-digest>"
FIRMWARE_IDENTITY="<production-firmware-build-id>"
AUDIO_FIXTURE="tests/fixtures/tvideo_farm_audio/adult_speech_24k_mono.wav"
FIXTURE_SHA256="$(shasum -a 256 "$AUDIO_FIXTURE" | awk '{print $1}')"
CONFIG_JSON="$(AUDIO_FIXTURE="$AUDIO_FIXTURE" python3 -c 'import json,os; from pathlib import Path; from scripts.google_live_smoke import _build_env_config,_build_round_trip_config; config=_build_round_trip_config(_build_env_config("gemini-3.1-flash-live-preview","Kore"),Path(os.environ["AUDIO_FIXTURE"])); print(json.dumps(config,sort_keys=True,separators=(",",":")))')"
CONFIG_FINGERPRINT="$(CONFIG_JSON="$CONFIG_JSON" python3 -c 'import json,os; from scripts.google_live_reliability import build_candidate_identity; value=json.loads(os.environ["CONFIG_JSON"]); print(build_candidate_identity("identity-only","sha256:"+"0"*64,"identity-only",value,"0"*64)["configFingerprint"])')"
mkdir -p "$EVIDENCE_ROOT"/{deterministic,server-regression,real-api,websocket-e2e,physical,candidate-soak}
CMD
```

Resolve `CANDIDATE_IMAGE_DIGEST` from the immutable candidate image and
`FIRMWARE_IDENTITY` from the firmware actually installed on the robot. Do not
substitute a branch name, mutable tag, or intended firmware version. The
porcelain check rejects staged, unstaged, and untracked files before
`CANDIDATE_SHA` is captured; if an operator needs local artifacts, use a
separate clean immutable checkout rather than weakening this check. The
generated `CONFIG_JSON` matches the smoke script's effective round-trip config;
its API-key placeholder is redacted by the shared identity builder before the
fingerprint is calculated.

Run the whole evidence sequence in this same shell. The shown `record_command`
entries add selected sanitized invocation templates to partial operator notes;
they are examples and are not a complete command ledger. The heredoc delimiter
must be single-quoted so
`$GOOGLE_API_KEY`, device/client variables, and protected paths are recorded as
literal names rather than expanded values. Use `<robot-device-id>`-style
placeholders for operator identifiers; never pass a command containing a real
secret or protected path to `record_command`.

## 2. Deterministic gate

The mandatory focused gate is:

```bash
DETERMINISTIC_TEST_FILES=(
  tests/test_google_live_lifecycle_e2e.py
  tests/test_google_live_client.py
  tests/test_google_live_reconnect.py
  tests/test_google_live_provider_edges.py
  tests/test_google_live_audio_bridge_edges.py
  tests/test_google_live_bargein.py
  tests/test_google_live_event_mapping.py
  tests/test_google_live_tool_calls.py
  tests/test_google_live_lesson_conversation.py
  tests/test_connection_voice_provider_routing.py
  tests/test_connection_edges.py
  tests/test_audio_rate_controller_edges.py
  tests/test_receive_audio_handle.py
  tests/test_lesson_voice_output_discipline.py
)
record_command <<'CMD'
python3 -m pytest "${DETERMINISTIC_TEST_FILES[@]}" --collect-only -qq > "$EVIDENCE_ROOT/deterministic/collection-output.txt"
CMD
python3 -m pytest "${DETERMINISTIC_TEST_FILES[@]}" --collect-only -qq \
  > "$EVIDENCE_ROOT/deterministic/collection-output.txt"
sed -n '/^tests\/.*::/p' "$EVIDENCE_ROOT/deterministic/collection-output.txt" \
  > "$EVIDENCE_ROOT/deterministic/node-manifest.txt"
EXPECTED_TEST_FILES="$(printf '%s\n' "${DETERMINISTIC_TEST_FILES[@]}" | sort)"
COLLECTED_TEST_FILES="$(sed -n 's/^\(tests\/[^:[:space:]]*\.py\)::.*$/\1/p' "$EVIDENCE_ROOT/deterministic/node-manifest.txt" | sort -u)"
test "$COLLECTED_TEST_FILES" = "$EXPECTED_TEST_FILES"
for required_node in \
  'tests/test_connection_voice_provider_routing.py::ConnectionVoiceProviderRoutingTest::test_google_live_ping_routes_to_classic_heartbeat_handler' \
  'tests/test_connection_edges.py::ConnectionEdgeTest::test_lesson_start_handoff_uses_generation_token_and_rejects_stale_release' \
  'tests/test_audio_rate_controller_edges.py::test_controller_constructs_without_a_running_loop_and_rebinds_between_loops' \
  'tests/test_receive_audio_handle.py::StartToChatTest::test_google_live_start_to_chat_does_not_enter_classic_pipeline' \
  'tests/test_lesson_voice_output_discipline.py::ClassicLessonAudioOverlapInvariantTest::test_classic_audio_streams_when_no_lesson_owns_the_speaker'; do
  grep -Fqx "$required_node" "$EVIDENCE_ROOT/deterministic/node-manifest.txt"
done
record_command <<'CMD'
python3 -m pytest "${DETERMINISTIC_TEST_FILES[@]}" --junitxml "$EVIDENCE_ROOT/deterministic/pytest.xml" -q
CMD
python3 -m pytest "${DETERMINISTIC_TEST_FILES[@]}" \
  --junitxml "$EVIDENCE_ROOT/deterministic/pytest.xml" -q
```

Only after pytest exits zero, convert its result to the release-layer contract:

```bash
record_command <<'CMD'
EVIDENCE_ROOT="$EVIDENCE_ROOT" CANDIDATE_SHA="$CANDIDATE_SHA" CANDIDATE_IMAGE_DIGEST="$CANDIDATE_IMAGE_DIGEST" FIRMWARE_IDENTITY="$FIRMWARE_IDENTITY" CONFIG_FINGERPRINT="$CONFIG_FINGERPRINT" FIXTURE_SHA256="$FIXTURE_SHA256" python3 - <<'PY'
import hashlib, json, os, xml.etree.ElementTree as ET
from pathlib import Path
root = Path(os.environ["EVIDENCE_ROOT"])
xml_root = ET.parse(root / "deterministic/pytest.xml").getroot()
cases = list(xml_root.iter("testcase"))
total = len(cases)
failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
skipped = sum(case.find("skipped") is not None for case in cases)
collection_path = root / "deterministic/node-manifest.txt"
collection = collection_path.read_text().splitlines()
identity = {"gitSha": os.environ["CANDIDATE_SHA"], "imageDigest": os.environ["CANDIDATE_IMAGE_DIGEST"], "firmwareIdentity": os.environ["FIRMWARE_IDENTITY"], "configFingerprint": os.environ["CONFIG_FINGERPRINT"], "fixtureSha256": os.environ["FIXTURE_SHA256"]}
collection_sha256 = hashlib.sha256(collection_path.read_bytes()).hexdigest()
ok = total == len(collection) > 0 and failed == skipped == 0
report = {"schemaVersion": "google-live-reliability.v1", "name": "deterministic", "status": "PASS" if ok else "FAIL", "candidateIdentity": identity, "coverageProof": {"collectionSha256": collection_sha256, "collectedNodeCount": len(collection)}, "testVerdict": {"status": "PASS" if ok else "FAIL", "total": total, "failed": failed, "skipped": skipped, "failures": [] if failed == 0 else [{"code": "PYTEST_FAILED"}]}, "failures": [] if ok else [{"code": "DETERMINISTIC_GATE_FAILED"}]}
(root / "deterministic/report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
CMD
EVIDENCE_ROOT="$EVIDENCE_ROOT" CANDIDATE_SHA="$CANDIDATE_SHA" \
CANDIDATE_IMAGE_DIGEST="$CANDIDATE_IMAGE_DIGEST" \
FIRMWARE_IDENTITY="$FIRMWARE_IDENTITY" CONFIG_FINGERPRINT="$CONFIG_FINGERPRINT" \
FIXTURE_SHA256="$FIXTURE_SHA256" python3 - <<'PY'
import hashlib, json, os, xml.etree.ElementTree as ET
from pathlib import Path

root = Path(os.environ["EVIDENCE_ROOT"])
xml_root = ET.parse(root / "deterministic/pytest.xml").getroot()
cases = list(xml_root.iter("testcase"))
total = len(cases)
failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
skipped = sum(case.find("skipped") is not None for case in cases)
collection_path = root / "deterministic/node-manifest.txt"
collection = collection_path.read_text().splitlines()
identity = {
    "gitSha": os.environ["CANDIDATE_SHA"],
    "imageDigest": os.environ["CANDIDATE_IMAGE_DIGEST"],
    "firmwareIdentity": os.environ["FIRMWARE_IDENTITY"],
    "configFingerprint": os.environ["CONFIG_FINGERPRINT"],
    "fixtureSha256": os.environ["FIXTURE_SHA256"],
}
collection_sha256 = hashlib.sha256(collection_path.read_bytes()).hexdigest()
ok = total == len(collection) > 0 and failed == skipped == 0
report = {
    "schemaVersion": "google-live-reliability.v1",
    "name": "deterministic",
    "status": "PASS" if ok else "FAIL",
    "candidateIdentity": identity,
    "coverageProof": {"collectionSha256": collection_sha256, "collectedNodeCount": len(collection)},
    "testVerdict": {"status": "PASS" if ok else "FAIL", "total": total, "failed": failed, "skipped": skipped, "failures": [] if failed == 0 else [{"code": "PYTEST_FAILED"}]},
    "failures": [] if ok else [{"code": "DETERMINISTIC_GATE_FAILED"}],
}
(root / "deterministic/report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
```

This producer now binds its own report to the collected node count and
collection hash. However, the current `_deterministic_valid()` release-gate
validator checks only the generic `testVerdict`; it does not validate
`coverageProof` or the supporting JUnit/collection artifacts. Therefore this
deterministic output remains diagnostic, not release-eligible, until the
checked-in release gate validates the exact matrix proof. Do not remove
`coverageProof` or treat the extra checksum entries as a substitute for that
software validation.

## 3. Guarded real Google API round trip

Source the key from the operator's protected secret store into the environment;
never paste it into `commands.txt`. The report contains metrics and classified
failures only, never the key, raw audio, transcript, prompt, or exception text.

```bash
record_command <<'CMD'
RUN_GOOGLE_LIVE_SMOKE=1 GOOGLE_API_KEY="$GOOGLE_API_KEY" python3 scripts/google_live_smoke.py --round-trip --audio-file "$AUDIO_FIXTURE" --candidate-git-sha "$CANDIDATE_SHA" --candidate-image-digest "$CANDIDATE_IMAGE_DIGEST" --firmware-identity "$FIRMWARE_IDENTITY" --config-fingerprint "$CONFIG_FINGERPRINT" --fixture-sha256 "$FIXTURE_SHA256" --report "$EVIDENCE_ROOT/real-api/report.json"
CMD
RUN_GOOGLE_LIVE_SMOKE=1 GOOGLE_API_KEY="$GOOGLE_API_KEY" \
python3 scripts/google_live_smoke.py \
  --round-trip \
  --audio-file "$AUDIO_FIXTURE" \
  --candidate-git-sha "$CANDIDATE_SHA" \
  --candidate-image-digest "$CANDIDATE_IMAGE_DIGEST" \
  --firmware-identity "$FIRMWARE_IDENTITY" \
  --config-fingerprint "$CONFIG_FINGERPRINT" \
  --fixture-sha256 "$FIXTURE_SHA256" \
  --report "$EVIDENCE_ROOT/real-api/report.json"
```

Manager-backed private configuration is also supported for a guarded
connect/close diagnostic and remains in memory:

```bash
record_command <<'CMD'
RUN_GOOGLE_LIVE_SMOKE=1 python3 scripts/google_live_smoke.py --manager-device-id "<robot-device-id>" --manager-client-id "<robot-client-id>"
CMD
RUN_GOOGLE_LIVE_SMOKE=1 python3 scripts/google_live_smoke.py \
  --manager-device-id "<robot-device-id>" \
  --manager-client-id "<robot-client-id>"
```

This diagnostic does not create the release report. If manager-backed config is
the production candidate, start a fresh `RUN_ID`, derive `CONFIG_JSON` and its
fingerprint from that exact redacted manager config, then rerun every layer;
never mix the environment-config identity with manager-config evidence.

## 4. Authenticated WebSocket barge-in journey

Use the supported OTA mint path so the bearer exists only in process memory and
never appears in the process argv, shell history, environment, or evidence
bundle. The OTA endpoint must be the production authenticated endpoint for the
same device/client identity.

```bash
WEBSOCKET_LOG_PID=""
cleanup_websocket_capture() {
  if test -n "${WEBSOCKET_LOG_PID:-}"; then
    kill "$WEBSOCKET_LOG_PID" 2>/dev/null || true
    wait "$WEBSOCKET_LOG_PID" 2>/dev/null || true
  fi
  WEBSOCKET_LOG_PID=""
}
trap cleanup_websocket_capture EXIT INT TERM
record_command <<'CMD'
tail -n 0 -F tmp/server.log > "$EVIDENCE_ROOT/websocket-e2e/timeline.log" &
CMD
tail -n 0 -F tmp/server.log > "$EVIDENCE_ROOT/websocket-e2e/timeline.log" &
WEBSOCKET_LOG_PID=$!
JOURNEY_ID="$RUN_ID-bargein-01"
record_command <<'CMD'
python3 scripts/voice_mode_websocket_audio_bargein.py --websocket-url "wss://<server>/tbot/v1/" --ota-url "https://<server>/tbot/ota/" --device-id "<robot-device-id>" --client-id "<robot-client-id>" --audio-file "$AUDIO_FIXTURE" --journey-id "$JOURNEY_ID" --candidate-git-sha "$CANDIDATE_SHA" --candidate-image-digest "$CANDIDATE_IMAGE_DIGEST" --firmware-identity "$FIRMWARE_IDENTITY" --config-json "$CONFIG_JSON" --fixture-sha256 "$FIXTURE_SHA256" --report "$EVIDENCE_ROOT/websocket-e2e/transport.json"
CMD
if python3 scripts/voice_mode_websocket_audio_bargein.py \
    --websocket-url "wss://<server>/tbot/v1/" \
    --ota-url "https://<server>/tbot/ota/" \
    --device-id "<robot-device-id>" \
    --client-id "<robot-client-id>" \
    --audio-file "$AUDIO_FIXTURE" \
    --journey-id "$JOURNEY_ID" \
    --candidate-git-sha "$CANDIDATE_SHA" \
    --candidate-image-digest "$CANDIDATE_IMAGE_DIGEST" \
    --firmware-identity "$FIRMWARE_IDENTITY" \
    --config-json "$CONFIG_JSON" \
    --fixture-sha256 "$FIXTURE_SHA256" \
    --report "$EVIDENCE_ROOT/websocket-e2e/transport.json"; then
  echo "Transport unexpectedly returned standalone success" >&2
  exit 1
fi
record_command <<'CMD'
python3 - "$EVIDENCE_ROOT/websocket-e2e/transport.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report.get("status") == "SKIPPED"
assert report.get("pendingCode") == "PENDING_BOUNDED_SERVER_LOG_VERIFICATION"
PY
CMD
python3 - "$EVIDENCE_ROOT/websocket-e2e/transport.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report.get("status") == "SKIPPED"
assert report.get("pendingCode") == "PENDING_BOUNDED_SERVER_LOG_VERIFICATION"
PY
cleanup_websocket_capture
trap - EXIT INT TERM
```

The transport command intentionally exits non-zero with `SKIPPED` and
`PENDING_BOUNDED_SERVER_LOG_VERIFICATION`. That is correct: raw Task 4 transport
evidence can never be a standalone PASS. Only Task 5 correlation with the exact
bounded server log can upgrade the composite WebSocket layer.

Capture only this WebSocket journey into its own raw log file. Never append a
physical or soak journey: the correlation path selects the transport UTC window
and rejects foreign or malformed scoped reliability markers inside it.

Then correlate the exact WebSocket window:

```bash
record_command <<'CMD'
EVIDENCE_ROOT="$EVIDENCE_ROOT" CANDIDATE_SHA="$CANDIDATE_SHA" CANDIDATE_IMAGE_DIGEST="$CANDIDATE_IMAGE_DIGEST" FIRMWARE_IDENTITY="$FIRMWARE_IDENTITY" CONFIG_FINGERPRINT="$CONFIG_FINGERPRINT" FIXTURE_SHA256="$FIXTURE_SHA256" python3 - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ["EVIDENCE_ROOT"])
identity = {"gitSha": os.environ["CANDIDATE_SHA"], "imageDigest": os.environ["CANDIDATE_IMAGE_DIGEST"], "firmwareIdentity": os.environ["FIRMWARE_IDENTITY"], "configFingerprint": os.environ["CONFIG_FINGERPRINT"], "fixtureSha256": os.environ["FIXTURE_SHA256"]}
(root / "candidate.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
PY
CMD
EVIDENCE_ROOT="$EVIDENCE_ROOT" CANDIDATE_SHA="$CANDIDATE_SHA" \
CANDIDATE_IMAGE_DIGEST="$CANDIDATE_IMAGE_DIGEST" FIRMWARE_IDENTITY="$FIRMWARE_IDENTITY" \
CONFIG_FINGERPRINT="$CONFIG_FINGERPRINT" FIXTURE_SHA256="$FIXTURE_SHA256" python3 - <<'PY'
import json, os
from pathlib import Path
root = Path(os.environ["EVIDENCE_ROOT"])
identity = {"gitSha": os.environ["CANDIDATE_SHA"], "imageDigest": os.environ["CANDIDATE_IMAGE_DIGEST"], "firmwareIdentity": os.environ["FIRMWARE_IDENTITY"], "configFingerprint": os.environ["CONFIG_FINGERPRINT"], "fixtureSha256": os.environ["FIXTURE_SHA256"]}
(root / "candidate.json").write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n")
PY
record_command <<'CMD'
PYTHONPATH=. python3 scripts/analyze_google_live_log.py --log "$EVIDENCE_ROOT/websocket-e2e/timeline.log" --correlate-transport "$EVIDENCE_ROOT/websocket-e2e/transport.json" --expected-candidate-json "$EVIDENCE_ROOT/candidate.json" --out-json "$EVIDENCE_ROOT/websocket-e2e/correlated.json"
CMD
PYTHONPATH=. python3 scripts/analyze_google_live_log.py \
  --log "$EVIDENCE_ROOT/websocket-e2e/timeline.log" \
  --correlate-transport "$EVIDENCE_ROOT/websocket-e2e/transport.json" \
  --expected-candidate-json "$EVIDENCE_ROOT/candidate.json" \
  --out-json "$EVIDENCE_ROOT/websocket-e2e/correlated.json"
```

This correlation command is the only currently supported path for the raw
WebSocket capture. It uses the transport report's UTC `logWindow`, selects those
server lines, synthesizes the required start/end anchors in a temporary file,
validates that temporary bounded window, and emits the composite
`correlated.json`. The temporary standalone log verdict is not persisted or
exposed by the CLI.

Do not call `analyze_reliability_window()` directly on `timeline.log`: normal
server output does not contain `reliability_window_start` and
`reliability_window_end`, so the raw file is not a standalone Task 5 report.
Until a checked-in producer exposes the validated bounded verdict,
`websocket-e2e/log-report.json` and the release wrapper that requires its
`logEvidence` field are unavailable. A passing `correlated.json` proves the
supported WebSocket correlation path, but it does not by itself satisfy the
six-layer release aggregator.

The analyzer still requires the exact journey, connection, peer hash, Live
connection transition ledger, candidate identity, and UTC window. Foreign or
unscoped log markers cannot satisfy the journey.

The `record_command` helper records selected sanitized operator templates, but
it does not execute commands, capture every invocation, or prove that a template
equals process argv. Until a checked-in structured execute-and-record wrapper
exists, `commands.txt` is partial diagnostic context only and command provenance
remains a software release blocker. A checksum upgrades neither completeness
nor manual transcription into provenance.

## 5. Historical compatibility matrix

These contracts remain mandatory. The deterministic command above executes
every test file cited below; the generated JUnit and deterministic report cover
the whole executable matrix rather than treating node collection as a pass.

| Contract | Automated proof | Production failure prevented |
|---|---|---|
| Single Live session and receive-loop ownership | `tests/test_google_live_lifecycle_e2e.py::test_google_live_full_lifecycle_recovers_without_duplicate_owners_or_audio`; `tests/test_google_live_reconnect.py::LiveOpenReceiveTaskRaceTest::test_open_live_session_cancels_stale_receive_task_before_new_loop` | Duplicate sessions, doubled audio, two receive loops consuming one connection |
| Replay once, ordered, current turn only | `tests/test_google_live_lifecycle_e2e.py::test_google_live_full_lifecycle_recovers_without_duplicate_owners_or_audio`; `tests/test_google_live_reconnect.py::MidStreamBlipReconnectTest::test_mid_stream_audio_replays_from_deque_on_successful_reconnect`; `tests/test_google_live_bargein.py::InterruptTurnControllerTest::test_replay_idempotent_when_called_twice` | Duplicate words, replay storms, old-turn speech contaminating the replacement turn |
| Stale flush cancellation | `tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_model_audio_start_cancels_pending_idle_input_flush` | Late flush moving an already-speaking turn back to `WAITING_MODEL` |
| Heartbeat ping routing | `tests/test_connection_voice_provider_routing.py::ConnectionVoiceProviderRoutingTest::test_google_live_ping_routes_to_classic_heartbeat_handler` | Healthy robot disconnected because Google Live swallowed firmware ping |
| Trigger-fragment cleanup | `tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_lesson_start_asr_fallback_live_close_breaks_fragment_chain` and adjacent/exact fragment tests in the same file | Split lesson trigger leaking into a later turn or false lesson start |
| Barge-in idempotency | `tests/test_google_live_bargein.py::InterruptTurnControllerTest::test_replay_idempotent_when_called_twice`; `tests/test_google_live_event_mapping.py::GoogleLiveEventMappingTest::test_interruption_clears_queue_before_tts_stop_send_finishes` | Repeated destructive stops, duplicated replacement input, old output surviving interruption |
| Stale event rejection | `tests/test_google_live_lifecycle_e2e.py::test_synthetic_stream_end_keeps_stale_generation_out_of_provider_state`; `tests/test_google_live_bargein.py::TurnIsolationBarrierTest::test_stale_model_events_are_dropped_after_interrupted_audio_end` | Delayed audio/transcript/tool/completion mutating the active turn |
| Receive-timeout recovery | `tests/test_google_live_lifecycle_e2e.py::test_real_receive_timeout_routes_to_bounded_recovery_and_replacement_output`; Task 5 `unrecoveredTimeouts == []` | Silent hang in `WAITING_MODEL`, unbounded reopen loop, dead air |
| Non-retriable error classification | `tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_auth_error_logs_no_retry_and_returns_false`; `tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_quota_error_logs_no_retry_and_returns_false`; `tests/test_google_live_reconnect.py::ClassifyErrorRoutingTest::test_invalid_config_error_logs_no_retry_and_returns_false` | Auth/quota/config reconnect storm and misleading Live success |
| Lesson handoff generations | `tests/test_connection_edges.py::ConnectionEdgeTest::test_lesson_start_handoff_uses_generation_token_and_rejects_stale_release` | Older task releasing a newer lesson lease or duplicate lesson runtime |
| Device audio-drain ordering | `tests/test_google_live_provider_edges.py::GoogleLiveProviderEdgeTest::test_matching_device_drain_ack_releases_prompt_wait_and_stale_ack_does_not` | Prompt overlap, premature lesson progression, stale drain acknowledgement |
| Semantic tool fencing | `tests/test_google_live_lesson_conversation.py::LessonConversationProviderTest::test_old_response_generation_cannot_dispatch_lesson_mutation`; `tests/test_google_live_lesson_conversation.py::LessonConversationProviderTest::test_lesson_mode_admits_semantic_tool_and_preserves_structured_decision` | Stale/unapproved tool advancing the wrong lesson step |
| Event-loop cleanup | `tests/test_audio_rate_controller_edges.py::test_controller_constructs_without_a_running_loop_and_rebinds_between_loops`; lifecycle E2E pending-task assertions | Cross-loop pacing errors and leaked receive/flush/background tasks |
| Classic-pipeline isolation | `tests/test_receive_audio_handle.py::StartToChatTest::test_google_live_start_to_chat_does_not_enter_classic_pipeline`; `tests/test_lesson_voice_output_discipline.py::ClassicLessonAudioOverlapInvariantTest::test_classic_audio_streams_when_no_lesson_owns_the_speaker` | Classic fallback masking a broken Live turn or Live changes mutating classic behavior |

If a node ID changes, update this table only after `pytest --collect-only` proves
the replacement. The classic voice suite remains separate and mandatory; a
Google Live PASS cannot replace it. Do not weaken a contract to make
documentation match.
