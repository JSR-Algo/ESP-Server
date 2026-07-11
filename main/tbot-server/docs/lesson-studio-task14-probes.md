# Task 14 live probes

Run only in a bounded lab. The driver does not inject faults; it validates and
hashes evidence captured by the operator and fails closed when any evidence is
missing. Supported subcommands are `cold`, `warm`, `offline`, `checksum`,
`interrupted`, `power-loss`, `missing-optional`, `sd-full`,
`slave-unavailable`, and `rollback`.

```sh
python3 scripts/lesson_studio_task14_fault_driver.py cold --evidence-dir artifacts/cold --output artifacts/cold/evidence.json
python3 scripts/lesson_studio_task14_soak.py artifacts/soak/serial.log artifacts/soak/server.log --output artifacts/soak/report.json
python3 scripts/lesson_studio_task14_log_audit.py artifacts/soak/serial.log artifacts/soak/server.log --output artifacts/soak/audit.json
```

Each fault directory must contain non-empty `serial.log`, `server.log`,
`command.txt`, and `result.json`. The result must explicitly name the scenario
and contain `"status":"PASS"`; success-looking log text is never sufficient.
