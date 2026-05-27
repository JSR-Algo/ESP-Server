# Google Live PR2 + PR4 physical validation — evidence skeleton

**Task:** `adhoc-2026-05-19-google-live-stability-aec`
**Owning repo:** `esp32-server/main/tbot-server`
**Plan:** [`.omc/plans/google-live-stability-bargein-v2.md`](../../../../.omc/plans/google-live-stability-bargein-v2.md)
**Playbook:** [`docs/google-live-robot-validation.md`](../../google-live-robot-validation.md)

> Status: **SKELETON** — fill once the soak runs against the physical robot.
> Until then this file simply records the harness is ready and what
> commands to run. Do NOT mark the task DONE while values below are TBD.

---

## 1. Verification-matrix row

| AC | Description | PASS / FAIL / PARTIAL | Evidence |
|---|---|---|---|
| AC1 | 10-min stability | TBD | soak report `ac_results.AC1` |
| AC2 | Barge-in latency p95 ≤ 500ms | TBD | soak report `ac_results.AC2` |
| AC3 | Post-interrupt: new response id > cancelled id, ≥ 80% cycles | TBD | soak report `ac_results.AC3` |
| AC4 | No false-positive interrupts during idle | TBD | soak report `ac_results.AC4` |
| AC5 | No fallback during soak | TBD | soak report `ac_results.AC5` |
| AC6 | Unit test coverage | **PASS** | `./.venv311/bin/python -m unittest discover -s tests` → 152 PASS / 1 skipped |
| AC7 | Server-side AEC effectiveness | **DEFERRED** | PR1 verdict `AEC_OPTIONAL` (ratio 0.043) |

---

## 2. Commands run

```bash
# 1. Network preflight
./.venv311/bin/python scripts/voice_mode_preflight.py \
    --device-ip <robot-ip> --max-loss-pct 0

# 2. Smoke API key
GOOGLE_API_KEY=$KEY ./.venv311/bin/python scripts/google_live_smoke.py

# 3. Soak harness
./.venv311/bin/python scripts/google_live_robot_soak.py \
    --websocket-url ws://<server-ip>:8000/tbot/v1/ \
    --device-id 3c:0f:02:de:c2:e0 \
    --client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc \
    --bargein-cycles 5 \
    --idle-cycles 1 \
    --idle-duration-sec 120 \
    --log-path tmp/server.log \
    --report .omc/research/soak-<timestamp>.json
```

## 3. Soak report excerpt

Paste the `ac_results` block from the JSON report here.

```json
{
  "AC1": {"pass": "TBD"},
  "AC2": {"pass": "TBD", "p95_latency_ms": null},
  "AC3": {"pass": "TBD", "ratio": "0/0"},
  "AC4": {"pass": "TBD", "ratio": "0/0"},
  "AC5": {"pass": "TBD", "fallback_triggered": 0}
}
```

## 4. Critique-before-close

1. **Root cause vs symptom**: ___
2. **Code vs docs**: ___
3. **Test quality**: PR2 + PR4 unit tests cover goAway, deque replay, classification, debounce, end_audio_stream guard, timer cancel, auto-unblock (20 new tests)
4. **Drift status**: PR2 + PR4 + PR5 docs all updated in this change. Plan v2 Section 17 reflects PR1 data-informed pivot.
5. **Principal-engineer cold review**: ___
6. **Reproducibility**: soak script is deterministic for given prompts; non-determinism from LLM response content is bypassed by validating transcript signals not response content (AC3)

## 5. Open follow-ups (after the physical run)

- If AC2 fails: lower `barge_in_min_input_duration_sec` further OR run controlled mic capture
- If AC4 fails: confirm echo RMS in fresh controlled capture, then either raise `barge_in_rms_threshold` OR start PR3 (server-side AEC)
- If AC1 fails with `goaway_seen > budget`: verify `recv_timeout_sec=60` and proactive reconnect actually fired (look for `Google Live session_expiring` log lines)
- If AC5 fails: dig into `fallback_triggered reason=...` — classification may be over-aggressive
