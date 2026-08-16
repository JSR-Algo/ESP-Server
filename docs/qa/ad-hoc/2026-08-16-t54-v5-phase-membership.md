# T5.4 renderer-v5 phase asset membership

Status: implementation verified locally; physical re-test and Ship checklist remain pending.

## Reproduction

The live `w02-feelings` v6 manifest contained the nine canonical renderer-v5
phase assets plus four bundle-era pose images. The runtime projected all thirteen
into `self.lesson_assets.sync_to_sd`. Under the live conversation heap pressure,
the four unused images failed HTTP allocation and the firmware correctly returned
`ready=false`, `failedCount=4`, and `checksumMatch=false`.

The focused regression adds the same four legacy keys to a valid seven-effect v5
manifest. Before the fix it failed with thirteen projected assets:

```text
FAILED test_runtime_manifest_projection_replaces_generic_v5_assets_with_phase_attestations
Left contains 4 more items
```

## Fix

Renderer-v5 `cinematicPhases` now define asset membership as well as metadata.
`_manifest_asset_cache_inputs()` returns only the unique background, teaching
object, and Robot video identities referenced by those phases. It does not weaken
firmware download, checksum, or READY validation.

## Passing re-run

```text
focused regression: 1 passed
layered cinematic + MCP payload: 73 passed
lesson-prod repro: T54 renderer-v5 phase membership: PASS
```

Physical lesson, power-cycle recovery, main verification, and deployment evidence
will be appended after the Ship gates complete.
