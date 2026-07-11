# Task 14 live probes

Run only in a bounded lab. The driver does not inject faults; it validates and
hashes evidence captured by the operator and fails closed when any evidence is
missing. Supported subcommands are `preview-parity`, `cold`, `warm`, `offline`, `checksum`,
`interrupted`, `power-loss`, `missing-optional`, `sd-full`,
`slave-unavailable`, and `rollback`.

```sh
python3 scripts/lesson_studio_task14_fault_driver.py cold --evidence-dir artifacts/cold --output artifacts/cold/evidence.json
python3 scripts/lesson_studio_task14_fault_driver.py preview-parity --evidence-dir artifacts/preview-parity --output artifacts/preview-parity/evidence.json
python3 scripts/lesson_studio_task14_soak.py artifacts/soak/serial.log artifacts/soak/server.log --output artifacts/soak/report.json
python3 scripts/lesson_studio_task14_log_audit.py artifacts/soak/serial.log artifacts/soak/server.log --output artifacts/soak/audit.json
```

Each fault directory must contain non-empty `serial.log`, `server.log`,
`command.txt`, and `result.json`. The result must explicitly name the scenario
and contain `"status":"PASS"`; success-looking log text is never sufficient.

Common metadata in every `result.json` is mandatory: UTC start/end, backend,
ESP-server, and firmware commits, firmware version, device/assignment/lesson
identities and versions, manifest/pack SHA-256 values, internal SRAM minimum,
first/last PSRAM samples, operator, command exit code, declared raw-log
markers, and one or more non-empty screenshot paths. The driver validates the
schema, confirms declared markers occur in `serial.log` or `server.log`, and
records SHA-256 hashes for logs, commands, results, and screenshots. It does
not convert synthetic or operator-authored fields into hardware proof.

Screenshot entries use `{"role": "preview", "path": "preview.png"}` and
`{"role": "hardware", "path": "hardware.png"}` objects. Paths must resolve
inside the evidence directory without symlinks, must be real PNG/JPEG files,
and are capped at 10 MiB. `preview-parity` requires exactly those two roles,
both images at 480x320, and different file content. Relative paths are resolved
from the scenario evidence directory.

`logMarkers` must include the canonical markers enforced for its scenario:
`lesson_step_started` + `motion_preset` (preview), `lesson_preload_ready` +
`checksum_verified` (cold), `asset_cache_hit` (warm), `offline_replay` +
`sd://` (offline), `checksum_mismatch` + `partial_cleaned` (checksum),
`download_interrupted` or `power_loss_recovery` plus `partial_cleaned`,
`optional_asset_missing` + `render_degraded`, `sd_full_refused` +
`previous_pack_retained`, `motion_degraded`, or `rollback_activated` +
`old_files_reattested`. Every declared marker must occur in a raw log.
