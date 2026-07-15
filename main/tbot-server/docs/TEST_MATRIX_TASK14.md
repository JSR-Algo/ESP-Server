# Task 14 evidence matrix

All live rows remain **NOT PASS** until a real-device artifact directory passes
the scenario-specific driver and contains serial/server logs, exact command,
device and build versions, manifest/pack checksums, screenshots, heap metrics,
and operator timestamps.

| Scenario | Probe | Status |
|---|---|---|
| preview-parity | `scripts/lesson_studio_task14_fault_driver.py preview-parity --evidence-dir <dir>` | NOT PASS - live evidence required |
| cold | authenticated exact-key eviction plus fresh `ASSIGNED`/`espTft` assignment response bound to assignment/device/child/lesson/version/checksum/profile metadata and `createdAt`; strict UTC order, response checksums, positive download, and checksum proof through `scripts/lesson_studio_task14_fault_driver.py cold --evidence-dir <dir>` | NOT PASS - live evidence required |
| warm, offline | `scripts/lesson_studio_task14_fault_driver.py <scenario> --evidence-dir <dir>` | NOT PASS - live evidence required |
| checksum, interrupted, power-loss | same driver; scenario-specific recovery fields required | NOT PASS - live evidence required |
| missing-optional, sd-full, slave-unavailable, rollback | same driver; fail-closed invariants required | NOT PASS - live evidence required |
| 100+ transitions, PSRAM/SRAM/reset | `scripts/lesson_studio_task14_soak.py <logs>` | NOT PASS - live evidence required |
| allocation/watchdog/decode/audio/sequence/duplicate progress | `scripts/lesson_studio_task14_log_audit.py <logs>` | NOT PASS - live evidence required |
