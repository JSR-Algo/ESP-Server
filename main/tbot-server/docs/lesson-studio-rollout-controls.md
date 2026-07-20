# Lesson Studio Rollout Controls

This is the ESP Server-owned companion to the operator runbook at
`robot/docs/runbooks/lesson-production-runbook.md`. It documents implemented
software controls only; it does not authorize deploy, flash, child pilot, or
production approval.

## Current status

- Motion presets and playful interactions default to disabled.
- `LESSON_ROLLOUT_DEVICE_ALLOWLIST` optionally limits both controls to one or
  more normalized device IDs/MACs.
- `LESSON_ASSET_DELIVERY_MODE=sd_pack` remains an independent delivery control.
- Firmware requires `lesson_prepare.body.runtimeControls.motionPresetsEnabled`
  before it dispatches a named preset; absent/false is a non-fatal no-op.
- Visual and voice lesson behavior continues when motion is independently disabled.
- Live internal-admin, one-robot soak, child-pilot, and wider-rollout gates are pending.

## First internal robot

```bash
LESSON_RUNTIME_ENABLED=true
LESSON_ASSET_DELIVERY_MODE=sd_pack
LESSON_MOTION_PRESETS_ENABLED=true
LESSON_PLAYFUL_INTERACTIONS_ENABLED=true
LESSON_ROLLOUT_DEVICE_ALLOWLIST=<one-device-id-or-mac>
```

Do not add a second device until the first-device soak evidence is accepted.
Never record secrets or child transcripts in logs or evidence.

## Independent rollback

Set `LESSON_MOTION_PRESETS_ENABLED=false` to stop servant motion while retaining
lesson visual/voice behavior. Set `LESSON_RUNTIME_ENABLED=false` only when the
whole lesson runtime must be disabled. Assignment rollback must reactivate the
previous immutable version and exact checksum while retaining its READY pack.

## Evidence source of truth

The authoritative Task 14 live matrix is the monorepo document
`robot/docs/TEST_MATRIX.md`. The local probe command reference is
`main/tbot-server/docs/lesson-studio-task14-probes.md`. A rollout decision must
cite both the matrix row and its raw artifacts: device ID, backend/ESP/firmware
SHAs, assignment and lesson versions, manifest checksum, READY pack metadata,
SRAM/PSRAM minima, render timings, screenshots, ESP logs, and firmware serial logs.

If the matrix lacks live evidence, the gate remains pending even when local tests
and builds pass.
