# Google Live robot validation (PR5)

Step-by-step playbook for validating PR2 + PR4 stability and barge-in
changes on a real TBOT robot. Output is a JSON report that maps onto the
acceptance criteria defined in [.omc/plans/google-live-stability-bargein-v2.md](../../../.omc/plans/google-live-stability-bargein-v2.md).

---

## 1. Pre-flight checklist (must all be PASS before soak)

| Check | Command | Pass criteria |
|---|---|---|
| Server reachable | `curl -sI http://<server-ip>:8000` | HTTP 1xx/2xx |
| Robot ARP present | `arp -a \| grep <robot-mac>` | One match |
| Network preflight | `python scripts/voice_mode_preflight.py --device-ip <robot-ip> --max-loss-pct 0` | 0 packet loss |
| Google Live key reachable | `GOOGLE_API_KEY=... python scripts/google_live_smoke.py` | `SMOKE_CONNECT_OK` + `SMOKE_CLOSE_OK` |
| Firmware audio mode | `cat TBOT-Firmware/sdkconfig.defaults.local \| grep AEC` | confirm what AEC mode is compiled |
| Voice mode config | manager-web > role config > Voice Mode | `google_live` selected |
| `tmp/server.log` writable & rotating | `ls -lah tmp/server.log` | non-zero size, last-mod within minutes |

If any pre-flight fails, fix before running soak — failures here are
network/config bias, not server defect.

---

## 2. Run the soak harness

```bash
cd esp32-server/main/tbot-server
./.venv311/bin/python scripts/google_live_robot_soak.py \
    --websocket-url ws://<server-ip>:8000/tbot/v1/ \
    --device-id <robot-mac> \
    --client-id <robot-client-uuid> \
    --bargein-cycles 5 \
    --idle-cycles 1 \
    --idle-duration-sec 120 \
    --log-path tmp/server.log \
    --report .omc/research/soak-$(date +%Y%m%d-%H%M%S).json
```

The script reuses helpers from [`voice_mode_websocket_soak.py`](../scripts/voice_mode_websocket_soak.py)
(hello, detect, recv predicates) and tails [`server.log`](../tmp/server.log)
for deterministic AC3/AC4 signals.

### Per-cycle output

For each barge-in cycle you should see one line:
```
BARGEIN_CYCLE outcome=PASS first_audio_ms=632.4 bargein_latency_ms=312.0 \
    transcript=True new_id=17 cancelled_id=16
```

For the idle cycle:
```
IDLE_CYCLE outcome=PASS false_positives=0
```

Then the script prints the AC summary and writes the JSON report.

---

## 3. AC interpretation guide

| AC | Definition | Where measured | Pass rule |
|---|---|---|---|
| AC1 | Stability over the soak window | `LOG_GOAWAY_RE`, `LOG_RECONNECT_RE`, `LOG_FALLBACK_RE` | `goaway_seen <= --ac1-goaway-budget` AND `fallback_triggered == 0` |
| AC2 | Barge-in latency (p95) | wall clock from interrupt-send to `tts.state=stop` | `p95 <= --bargein-latency-budget-ms` (default 500) |
| AC3 | Post-interrupt: model serves the NEW request | Log: `transcript source=user` + `user_interrupted` with `next_response_id > cancelled_response_id` | `>= 80%` bargein cycles match |
| AC4 | No false-positive interrupts during long monologue | Log: `user_interrupted` count between `tts.state=start` and `tts.state=stop` of the idle cycle | `false_positive_interrupts == 0` for every idle cycle |
| AC5 | No regression: no fallback during soak | Log: `fallback_triggered` count | `== 0` |

AC6 and AC7 are separate workstreams:
- **AC6 (test coverage)** is gated by `python -m unittest discover -s tests`
  in CI — not part of the soak.
- **AC7 (AEC-forward evidence)** is gated in physical robot logs by
  `scripts/physical_smoke_audit.py --require-aec-live-vad-forward`, which
  requires `Google Live aec_live_vad_forward reason=robot_speaking`. Quantitative
  AEC effectiveness remains covered by `scripts/aec_loopback_eval.py`.
- **First-audio response speed** is gated by
  `scripts/physical_smoke_audit.py --max-first-audio-ms 1800`, using
  `Google Live first_audio_out_latency_ms=...` log markers.
- **Expected user speech recognition** is gated by
  `scripts/physical_smoke_audit.py --expected-user-transcript "bắt đầu bài học"`,
  which requires the expected phrase to appear in a user transcript after
  case/punctuation/whitespace normalization. The flag is repeatable.
- **Lesson Live voice path** is gated by
  `scripts/physical_smoke_audit.py --require-lesson --require-lesson-live-text`,
  which requires each expected lesson prompt to be queued through Live text.
  Add `--lesson-manifest <lesson-manifest.json>` so the audit derives expected
  step count, interactive step count, prompt char lower bound, and per-prompt
  SHA-256 hashes from the selected lesson to catch truncated or changed
  payloads without logging content.

---

## 4. Common failure modes

| Symptom in report | Likely root cause | Action |
|---|---|---|
| `first_tts_start_timeout` on every cycle | Server not running or wrong voice_mode | check Docker `docker ps`, agent config |
| `bargein_latency_ms > 500` but `transcript=True` | network jitter or model still on cold start | rerun, retain only steady-state cycles |
| `transcript=False` but `bargein_latency_ms` good | Live interruption reached the server, but the captured user turn was too short or got suppressed before turn close | check `activity_handling=START_OF_ACTIVITY_INTERRUPTS`, `input_live_chunk_ms=20`, `input_flush_delay_sec=1.0`, and `model_output_unblock_timeout_sec` |
| `goaway_seen > 0` and PR2 deployed | confirm `recv_timeout_sec=60`, `reconnect_buffer_ms=2000` are active in container, restart server | |
| Repeated `IDLE_CYCLE false_positives > 0` | echo (no AEC) is exceeding `barge_in_rms_threshold`. Pause and run controlled measurement: silent room vs talking-robot, compare RMS in `tmp/server.log` `input_audio_diag` lines | |
| `fallback_triggered > 0` | non-retriable error class — open `server.log`, find `reason=...`. Most often `auth` (bad key) or `quota` (429) |

---

## 5. Evidence file

After every run, capture:
1. Soak JSON report → `.omc/research/soak-<timestamp>.json`
2. Tail of `tmp/server.log` covering the soak window
3. Verification-matrix row in
   [`docs/qa/ad-hoc/2026-05-19-google-live-robot-validation.md`](qa/ad-hoc/2026-05-19-google-live-robot-validation.md)

Two passes (before-deploy / after-deploy) lets reviewers compare deltas.

---

## 6. Known limitations of this harness

- **Audio injection is NOT yet wired here** — barge-in is triggered via
  the existing text-message path (`type=listen state=detect`). For true
  voice barge-in injection see
  [`scripts/voice_mode_websocket_audio_bargein.py`](../scripts/voice_mode_websocket_audio_bargein.py).
  Extending this script with Opus injection is a follow-up; the current
  text path exercises the same server-side `_begin_user_interrupt`
  pipeline so AC1/AC3/AC5 are still meaningful.
- The AC3 transcript signal is `transcript source=user`, which the Live
  API emits when its input transcription detects user speech. With
  text-message bargein this comes from `handle_text_message` instead;
  the log pattern still fires.
- Soak does NOT spoof firmware AEC capability — pre-flight should
  confirm the firmware's AEC mode separately.
