# T5.4 renderer-v5 disk-pack layer recovery

- Date: 2026-08-16
- Status: **IN_PROGRESS** — code verification is green; gate, merge, deployment, and physical re-test remain.
- Branch: `lesson-prod/t54-v5-disk-pack-layer`
- Verification cwd: `main/tbot-server`

## Production reproduction

Physical capture:
`/Users/manhhodinh/Documents/TBOT/.codex_tmp/lesson-live-20260816T162800Z-v7-5859733d`

- Robot booted firmware 2.2.89, mounted the 15 GiB SD card, joined Wi-Fi, opened the production WebSocket, and completed MCP discovery.
- Connect-time aggregate sync processed 26 cached packs but repeatedly reported `w02-feelings/v7-418dfd...` as `FirmwareSyncPackError`; the aggregate ended `packs=26 synced=25 failed=1`.
- Inside the production container, `_ready_rich_asset_pack()` successfully reconstructed all nine files and verified their sizes and SHA-256 values.
- Passing that reconstructed pack to `build_firmware_sync_pack()` failed in `validate_layered_cinematic_runtime_asset()` with `CINEMATIC_METADATA_MISMATCH: layered cinematic runtime layer is invalid`.
- Per-asset inspection showed all nine reconstructed assets lacked `layer`, including the background JPEG, teaching-object PNG, and seven Robot MJPEG/MP4 effects.

Root cause: `validate_layered_cinematic_generation_asset()` already proved that every asset used exactly one valid `visualRefs.slot`, but its normalized return omitted the canonical runtime `layer`. Disk reconstruction merged that incomplete normalized identity into the cached asset. The firmware payload validator then correctly rejected the incomplete runtime asset before MCP transfer.

## RED and fix

| Commit | Evidence |
| --- | --- |
| `54d29905` | Extended the existing disk-reconstruction regression through `build_firmware_sync_pack()` and required canonical layers for background, teaching object, and Robot media. The test failed with `FirmwareSyncPackError` caused by the missing layer. |
| `beba02de` | Derived the runtime layer from the already-validated single slot using the existing `LAYER_SLOTS` mapping and included it in the normalized generation identity. No caller-controlled layer, fallback, or validation relaxation was added. |

The campaign repro `lesson-prod/repros/t54-v5-disk-pack-layer.sh` is self-contained. It returned `1` on main `5859733d` with `actual=None expected='background'` and returned `0` on `beba02de` with `T54 renderer-v5 disk-pack layer recovery: PASS`.

## Verification

| Command | Result | Status |
| --- | --- | --- |
| `python -m pytest tests/test_lesson_sd_pack_sync.py::test_cached_asset_packs_preserve_renderer_v5_mixed_media_identity -q` before fix | 1 failed; `FirmwareSyncPackError` from missing runtime layer | RED |
| Same focused command after fix | 1 passed | PASS |
| `python -m pytest tests/test_layered_cinematic_contract.py tests/test_lesson_sd_pack_sync.py tests/test_lesson_sd_pack_mcp_payload.py tests/test_lesson_sd_pack_materializer.py -q` | 177 passed | PASS |
| `python -m pytest -q` | 3,853 passed, 3 skipped, 3 warnings in 160.11 s | PASS |
| Independent spec review | APPROVE, zero findings | PASS |
| Independent code-quality review | APPROVE, zero findings | PASS |
| `git diff --check 54d29905..beba02de` | Exit 0 | PASS |

## Preserved invariants

- Generation assets must still have non-empty refs that all use exactly one slot.
- Slot/media-type pairing remains strict: background JPEG, teaching-object PNG, Robot MJPEG/MP4.
- The runtime layer is derived from validated slot identity; it is never trusted from external input.
- Invalid renderer-v5 metadata remains fail-closed.
- Existing renderer-v3/v4, TRGB, materialization, SD-sync, and firmware-payload suites remain green.
- No backend, mobile, firmware, database, wire protocol, or content mutation is included.

## Deployment and physical verification

**PENDING.** After the official RED-to-GREEN gate and merge, deploy the server image, confirm the same W2 v7 disk pack syncs `ready=true` with all nine canonical assets, then repeat the physical lesson, CP-7 panel confirmation, power-cycle recovery, and Android Progress checks. T5.4 remains **IN_PROGRESS** until those gates pass.
