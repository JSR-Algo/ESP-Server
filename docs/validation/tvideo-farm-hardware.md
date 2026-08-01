# TVideo Farm Attended Hardware Gate

Status: **PENDING_ATTENDED_HARDWARE**

Recorded: 2026-08-01 16:01:54 +0700 (Asia/Ho_Chi_Minh)

This document records software-only evidence available before the attended
ESP32-S3 N16R8 run. It does not claim hardware readiness, production rollout,
Google Live success with real credentials, or firmware performance on the
board. Status remains **PENDING_ATTENDED_HARDWARE** until attended hardware
evidence is captured.

## Source identities

| Repository | Commit observed before Task 13 commit |
|---|---|
| ESP/admin | `c2ef56b133152b7ed99076d6c7660829b9d8b3fc` |
| Backend | `f78f8eae312616d7d1a30bf350404e9d8028bab0` |
| Firmware | `03c74368d76e5278967a67cc6f0d682278e03745` |

Committed cross-repository fixture SHA-256:

- `main/tbot-server/tests/fixtures/tvideo_farm_manifest_v2.json`:
  `77f196f20c488aa215fc0051dcdbe490a154f651d8edb060c1b098fba7dc846a`
- `docs/validation/tvideo-farm-software.md`:
  `1e62638411a87e26161ab57ac82df85f9772694cf358701d8919bbf1dd6ec61f`

## Task 13 software observations

Admin preview used the committed local farm fixture and never called a
production API. The mounted manager page displayed an explicit preview-only
banner, kept publish disabled because backend derivative readiness was false,
and exposed all four live-preview Vietnamese tabs: `3 nguồn`, `Đường đi hành trình`,
`Hội thoại`, and `Robot đã hợp nhất`. The path tab loaded the committed farm
MP4 and the source pickers used committed object and WebM robot media.

Browser command family:

```text
python3 scripts/tvideo_farm_preview.py --host 127.0.0.1 --port 8093
playwright-cli open http://127.0.0.1:8093/
playwright-cli snapshot
playwright-cli click <path-tab>
playwright-cli click <conversation-tab>
playwright-cli click <flattened-tab>
```

Observed result: all four tab panels mounted and were selectable; the publish
button remained disabled. Fresh-page browser console evidence after removing
the duplicate legacy `data()` declarations showed zero Vue warnings/errors for
`addingStep`, `reordering`, `selectedStepDrafts`, `selectedContentDrafts`,
`selectedAssetDrafts`, `dirtyStepKeys`, `savingStepKeys`, or
`stepDraftRevisions`. There were no missing-media HTTP errors after the fixture
media pins were applied.

Credential-gated command:

```text
python3 scripts/google_live_robot_soak.py \
  --scenario tvideo-farm \
  --audio-source synthetic \
  --duration-sec 180
```

The recorded software-only command omitted
`--server-has-google-live-credentials` and observed
`SKIP_GOOGLE_LIVE_CREDENTIALS`, exit code `0`; no Google Live session was
opened. The explicit flag is required when the target server receives its key
from manager/private configuration instead of one of the four supported local
Live environment aliases.

```text
python3 scripts/google_live_robot_soak.py \
  --scenario tvideo-farm \
  --audio-source synthetic \
  --server-has-google-live-credentials \
  --duration-sec 180
```

The TVideo farm scenario is no longer a JSON-text detection smoke. When Google
Live credentials are present, the harness opens the robot websocket with the
standard `hello.audio_params` contract (`opus`, `24000` Hz, mono, `60` ms
frames) and sends bare binary Opus packets produced from committed privacy-safe
speech fixtures selected independently for each semantic turn. It validates
only authoritative robot-facing frames: `lesson_prepare`,
`lesson_cinematic_control`, and TTS state frames. Every cinematic prepare/start
pair is acknowledged with a real firmware-shaped `lesson_ack`. The expected
cue/effect progression is exact and bounded:

```text
barn-listen
barn-thinking
barn-correct
barn-retry-level-1
barn-correct
barn-to-hay-word-transition
hay-listen
hay-thinking
hay-correct
hay-celebrate
```

The authoritative Google Live tool audit proof is exact:

| Field | Required value |
|---|---|
| Audit frame type | `google_live_validation_tool_audit` |
| Audit feature | `googleLiveValidationToolAuditV1` |
| Protocol | `teebot-lesson-renderer.v4` |
| Authoritative tools | `lesson_child_response`, `lesson_pronunciation_outcome`, `lesson_context_turn`, `lesson_visual_reaction`, `lesson_continue` |

The passing harness must observe 18 accepted audit records: 10
`lesson_visual_reaction`, 3 `lesson_pronunciation_outcome`, 2
`lesson_child_response`, 2 `lesson_continue`, and 1 `lesson_context_turn`.
Every audit identity must match the current lesson session and step, and each
refreshed identity must advance monotonically. Rejected audits, wrong audit
features, unknown tool names, stale sessions, cross-step identities, cue/effect
mismatches, or missing identity fields are hard failures.

The validator rejects missing frames, wrong cue/effect pairs, wrong cinematic
frame types, missing robot-facing binary audio, non-increasing
`commandSequenceId` values, envelope/command sequence mismatches, lesson-session
identity drift, and stale barn output after the barn-to-hay transition. The
retry/coaching turn intentionally leaves an active TTS response with binary
Opus; correction audio is sent while that output is still active. The harness
then requires exactly one `tts stop` interruption fence and a new accepted
response. Any binary output arriving after that stop but before the new
`tts start` is a hard failure. The observed conversation step identity must
change exactly once from `barn` to `hay` while assignment/session/lesson
identity remains stable.

The cinematic duplicate checks are strict. For every `lesson_prepare` and
`lesson_cinematic_control` frame, the envelope/body and `body.cinematicPhase`
copies of `cueId`, `effect`, `stepKey`, `playbackMode`, `command`, and
`commandSequenceId` must exist, match each other, and match the expected
cue/effect/step. Missing duplicates, duplicate mismatches, wrong playback mode,
wrong frame type, wrong protocol version, or wrong `stepId` fail the run.

Reports contain fixture IDs, fixture hashes, packet/transition/interruption
counts, validation codes, and latency metadata. They do not persist raw audio,
transcripts, prompts, utterances, filesystem paths, model prose, cue IDs, step
keys, or unhashed session identities.

Validation audit identities are ephemeral websocket-only proof. The server sends
them only on the robot websocket and only after an admitted runtime decision; it
does not log the audit frame, session sentinel, attempt sentinel, raw tool args,
or child response class. The JSON report records aggregate audit counts and
validation codes, not lesson session IDs, attempt IDs, turn identities, raw
transcripts, prompts, or model prose.

Committed speech fixture identities:

| Audio source | Fixture ID | SHA-256 | Format |
|---|---|---|---|
| `synthetic` | `tvideo-farm-synthetic-speech-v1` plus 10 pinned per-turn IDs | Per-file SHA-256 pinned in the runner | WAV PCM signed 16-bit little-endian, mono, 24000 Hz |
| `adult` | `tvideo-farm-adult-speech-v1` plus 10 pinned per-turn IDs | Per-file SHA-256 pinned in the runner | WAV PCM signed 16-bit little-endian, mono, 24000 Hz |

Both categories are generated by macOS system TTS voice `Linh`, then normalized
with FFmpeg. `synthetic` uses rate 180 and the slower adult-category set uses
rate 150. Neither set contains a human or child recording. Auditable provenance
is stored beside the fixtures in `tests/fixtures/tvideo_farm_audio/README.md`.

Explicit fake smoke command:

```text
python3 scripts/google_live_robot_soak.py \
  --scenario tvideo-farm \
  --audio-source synthetic \
  --duration-sec 180 \
  --dry-run
```

`--dry-run` is an in-process fake transcript check for local development only.
It may return `FAKE_PASS` without credentials, but it does not open Google Live,
does not contact hardware, and does not satisfy the attended hardware gate.

The audit emitter fails closed outside the attended local soak shape. It is
disabled unless all conditions are true: `validation_tool_audit_enabled: true`,
`validation_tool_audit_mode: local_soak`, websocket hello feature
`googleLiveValidationToolAuditV1: true`, exactly one
`validation_tool_audit_client_ids` entry matching the websocket `client_id`, and
exactly one `validation_tool_audit_device_ids` entry matching the websocket
`device_id`. The connection must also be in `LESSON` mode with an active lesson
runtime, and the audit must carry a canonical admission receipt produced by the
real lesson-conversation tool path. Generic handler mappings, missing runtime
context, refreshed-identity mismatches, and canonical decisions whose
`accepted` value is not exactly `true` do not emit an audit frame. Rejected tool
responses still return to Google Live normally; only validation audit emission
is suppressed.

## Rollout remains dark

The checked-in ESP configuration remains:

```yaml
lesson:
  runtime_enabled: false
  motion_presets_enabled: false
  playful_interactions_enabled: false
  renderer_v4_enabled: false
  rollout_device_allowlist: []
  storage_hil_device_allowlist: []
```

`main/tbot-server/config.yaml` SHA-256 at this checkpoint is
`081b4fb0ba803e7f31bc112e175f9efb0c45a2e4c43ca4f102cdde3b34e5205e`.
Task 13 does not edit this file, environment defaults, device allowlists, or
production deployment settings.

Runtime rollout and storage HIL gates are also singleton allowlists. Renderer v4
admission requires `renderer_v4_enabled: true`, renderer-v4 negotiation and
capability, and exactly one `rollout_device_allowlist` entry matching the
normalized connected `device_id`. Storage HIL admission requires exactly one
normalized MAC in `storage_hil_device_allowlist`; malformed or multiple entries
fail closed before hardware evidence can be claimed.

## Required attended ESP32-S3 N16R8 checklist

The gate stays pending until an operator records all items below against a
physically identified ESP32-S3 N16R8 robot and the exact flashed firmware
binary SHA-256.

- [ ] Board identity, MAC/device ID, firmware version, ELF/bin SHA-256, SD card
      model/filesystem, power source, display model, and ambient network noted.
- [ ] Renderer-v4 enabled only for the single attended device; before/after
      configuration and rollback-to-dark proof captured.
- [ ] Cold SD run and warm-cache run each materialize the exact 19 cue files,
      verify checksums, and play only one `480x320`, 10 FPS, no-audio MJPEG MP4
      stream at a time.
- [ ] Every `once` cue stops on its final frame and every `loop` cue repeats
      without reopen, decoder reallocation, black frame, stale frame, or visible
      seam across at least 100 cue transitions.
- [ ] Per-run decode/TFT metrics record requested FPS, achieved FPS, decoded and
      displayed frame counts, dropped/late frames, maximum decode time, maximum
      TFT transfer time, and cue start/stop/loop counts.
- [ ] Internal heap and PSRAM record boot baseline, pre-lesson, minimum, maximum,
      post-lesson, and post-reconnect values; repeated cycles show no monotonic
      growth and no allocation failure.
- [ ] Watchdog, panic, brownout, reset reason, decoder error, SD error, queue
      overflow, stuck-cue, stale-ACK, and duplicate-progress counters stay zero.
- [ ] Guided target, Vietnamese meaning bridge, related-concept bridge, silence,
      uncertain answer, all three gentle coaching levels, accepted pronunciation,
      third-attempt review-needed, word transition, and completion are observed.
- [ ] Invalid, duplicate, reordered, stale-session, stale-attempt, stale-step, and
      future-retry tool calls cannot advance lesson progress or skip cue levels.
- [ ] Synthetic or consenting-adult barge-in is measured end to end; record p50,
      p95, maximum stop latency, sample count, and the agreed acceptance budget.
- [ ] Google Live reconnect/GoAway and network interruption resume the same
      authoritative lesson session without repeated mastery, skipped step, stuck
      listen/talk state, or classic-provider fallback.
- [ ] Privacy inspection confirms no raw child audio or transcript is stored in
      reports, application logs, SD evidence, screenshots, or uploaded bundles.
- [ ] Rollback disables renderer-v4, empties the device allowlist, reconnects the
      robot, and proves renderer-v3/template-v1 behavior remains available.

## TEST_MATRIX ownership note

`/Users/manhhodinh/Documents/TBOT/robot/docs/TEST_MATRIX.md` is outside the
ESP/admin feature worktree, and `/Users/manhhodinh/Documents/TBOT/robot` is not
the owning Git worktree. It was intentionally not edited. Once this Task 13
commit is integrated into the owning docs checkout, append a pending row that
states only the software evidence above and keeps hardware as
`PENDING_ATTENDED_HARDWARE`; do not mark the platform or live columns as pass.
