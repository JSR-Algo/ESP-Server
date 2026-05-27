# Google Live PR5 — soak harness & docs evidence

**Task:** `adhoc-2026-05-19-google-live-pr5-soak-docs`
**Owning repo:** `esp32-server/main/tbot-server` (ad-hoc worktree scope)
**Plan:** [`.omc/plans/google-live-fix-execution-2026-05-19.md §6`](../../../../.omc/plans/google-live-fix-execution-2026-05-19.md)
**Spec:** [`.omc/plans/google-live-stability-bargein-v2.md §8`](../../../../.omc/plans/google-live-stability-bargein-v2.md)
**Date:** 2026-05-19

---

## 1. Verification-matrix rows (agent-executable items)

| # | Item | Command | Exit | Key output | Status |
|---|---|---|---|---|---|
| 1 | Syntax check | `./.venv311/bin/python -m py_compile scripts/google_live_robot_soak.py` | 0 | _(no output = pass)_ | **PASS** |
| 2 | CLI help prints all 8 spec args | `./.venv311/bin/python scripts/google_live_robot_soak.py --help` | 0 | `--device-mac`, `--cycles`, `--duration-sec`, `--inject-audio`, `--inject-text`, `--ws-url`, `--dry-run`, `--bargein-cycles` all listed | **PASS** |
| 3 | Dry-run smoke (no server) | `./.venv311/bin/python scripts/google_live_robot_soak.py --cycles 1 --duration-sec 5 --dry-run --report /tmp/soak-dry.json` | 0 | `DRY_RUN_OK schema validated, no server connection made` | **PASS** |
| 4 | Dry-run JSON valid | `/usr/bin/python3 -m json.tool /tmp/soak-dry.json` | 0 | All required fields: `started_at`, `config`, `cycles[]`, `ac_results`, `exit_code`, `dry_run: true` | **PASS** |
| 5 | Dry-run exit_code field = 0 | `jq .exit_code /tmp/soak-dry.json` | — | `0` | **PASS** |
| 6 | `docs/google-live-mode.md` has best-practice section | `grep -c "Best-practice config for TBOT robot" docs/google-live-mode.md` | — | `1` | **PASS** |
| 7 | `docs/google-live-completion-audit.md` has Phase 5 section | `grep -c "Phase 5" docs/google-live-completion-audit.md` | — | `≥1` | **PASS** |
| 8 | `docs/google-live-robot-validation.md` exists and has AC table | `grep -c "AC1" docs/google-live-robot-validation.md` | — | `≥1` | **PASS** |

---

## 2. Files created or modified

| File | LOC delta | Status |
|---|---|---|
| `scripts/google_live_robot_soak.py` | +89 lines (dry-run, CLI aliases, exit_code in report) | MODIFIED |
| `docs/google-live-mode.md` | +63 lines (Best-practice config section) | MODIFIED |
| `docs/google-live-completion-audit.md` | +52 lines (Phase 5 placeholder) | MODIFIED |
| `docs/google-live-robot-validation.md` | 0 (already substantive; no changes needed) | UNCHANGED |
| `docs/qa/ad-hoc/2026-05-19-google-live-pr5.md` | NEW | THIS FILE |

---

## 3. CLI spec compliance (v2.1 §8)

| Spec arg | Present in script | Notes |
|---|---|---|
| `--device-mac MAC` | Yes (`--device-mac / --device-id`) | backward-compat alias kept |
| `--cycles N` | Yes (`--cycles / --bargein-cycles`) | maps to `bargein_cycles` |
| `--duration-sec S` | Yes (`--duration-sec`) | maps to `event_timeout_sec` |
| `--bargein-cycles M` | Yes (also original primary form) | |
| `--report PATH` | Yes | |
| `--ws-url URL` | Yes (`--ws-url / --websocket-url`) | |
| `--inject-audio PATH` | Yes | WAV path for future Opus injection |
| `--inject-text TEXT` | Yes | overrides `--interrupt-prompt` when set |
| `--dry-run` (added) | Yes | skips websocket, emits placeholder JSON |

---

## 4. JSON schema fields (per v2.1 §8)

Confirmed present in dry-run output `/tmp/soak-dry.json`:

- `started_at` (ISO8601 / float)
- `config` (all CLI args)
- `cycles[]` with per-cycle: `index`, `outcome`, `first_audio_latency_ms`, `bargein_latency_ms`, `user_transcript_received`, `new_response_id`, `cancelled_response_id`, `stale_audio_after_interrupt_count`, `errors`
- `ac_results` with `AC1`–`AC5` keys, each containing `pass`, metric fields
- `exit_code` (0 in dry-run; live run: 0 if ≥8/10 cycles PASS + AC thresholds met, else 1)
- `dry_run` boolean flag

---

## 5. Physical robot soak (requires hardware)

Physical soak (PR5.5 from plan) cannot be completed by agent — requires:

- Robot MAC `3c:0f:02:de:c2:e0` visible on LAN (`arp -a | grep 3c:0f:02:de:c2:e0`)
- Valid `GOOGLE_API_KEY` set in environment
- Server running on LAN IP with robot's agent config set to `google_live`

Command to run when hardware is available:

```bash
cd /Users/manhhodinh/Documents/TBOT/robot/esp32-server/main/tbot-server
./.venv311/bin/python scripts/google_live_robot_soak.py \
    --ws-url ws://<server-ip>:8000/tbot/v1/ \
    --device-mac 3c:0f:02:de:c2:e0 \
    --client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc \
    --cycles 10 \
    --bargein-cycles 5 \
    --idle-cycles 1 \
    --duration-sec 60 \
    --idle-duration-sec 120 \
    --log-path tmp/server.log \
    --report .omc/research/soak-$(date +%Y%m%d-%H%M%S).json
```

After the run, paste `ac_results` block into `docs/qa/ad-hoc/2026-05-19-google-live-robot-validation.md` section 3.

---

## 6. Critique-before-close

1. **Root cause vs symptom**: Soak script probes the actual symptoms — `bargein_latency_ms` measures AC2 (response smoothness), log-tail for `transcript source=user` + `user_interrupted` measures AC3 (missing replies), idle-cycle false-positive count measures AC4. Not a superficial check.
2. **Code vs docs**: Script implements v2.1 §8 spec (all 8 CLI args, 7-step per-cycle flow described in spec §8, JSON schema with all required fields). One deliberate deviation: `--cycles` maps to `bargein_cycles` (not a separate total) because the spec's "N full Q&A cycles" equates to barge-in cycles in this harness; idle cycles are additive. This is noted in arg help text.
3. **Test quality**: Dry-run exercises JSON schema validation — all required fields are present and parseable. It does NOT exercise websocket logic (that requires a live server), which is correct: dry-run is a CI smoke, not a functional test.
4. **Drift**: Docs updated to reflect PR2/PR4 config values. `google-live-mode.md` best-practice section references actual PR2/PR4 config keys from the deployed `config.yaml`. `completion-audit.md` Phase 5 section uses TODO markers for evidence not yet available (physical soak not run by agent).
5. **Cold review**: An SRE reviewing this diff would see: (a) dry-run prevents CI failures when server is unavailable, (b) `exit_code` is now in the JSON report (was missing before — report only had `all_ac_pass` boolean), (c) all spec CLI args are present. One concern: `--inject-audio` is wired up in the CLI but the actual WAV → Opus injection code is not yet implemented in `_run_bargein_cycle` (the script still uses text-message path). This is **documented** in `docs/google-live-robot-validation.md §6 Known limitations` — it is a known gap, not a silent omission.
6. **Reproducibility**: Any developer can run `python scripts/google_live_robot_soak.py --cycles 1 --duration-sec 5 --dry-run --report /tmp/soak-dry.json` and get identical output structure (values vary only by timestamp and wall-clock duration, which is expected).

**Status: PARTIAL** — agent-executable items are DONE and PASS. Physical robot soak (PR5.5) is NOT done — blocked on hardware availability (MAC `3c:0f:02:de:c2:e0` absent from LAN per audit history). This is a documented blocker, not a gap in the implementation.
