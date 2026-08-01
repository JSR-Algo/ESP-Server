# TVideo Farm Attended Hardware Gate

Status: **PENDING_ATTENDED_HARDWARE**

Recorded: 2026-08-01 16:01:54 +0700 (Asia/Ho_Chi_Minh)

This document records software-only evidence available before the attended
ESP32-S3 N16R8 run. It does not claim hardware readiness, production rollout,
Google Live success, or firmware performance on the board.

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

Observed result: `SKIP_GOOGLE_LIVE_CREDENTIALS`, exit code `0`. No Google Live
session was opened. The validation report schema records only scenario labels,
timing/outcome metadata, and the selected `synthetic` or consenting-`adult`
provenance; it does not persist raw audio, transcripts, prompts, utterances, or
model prose.

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
