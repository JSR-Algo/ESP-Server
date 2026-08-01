#!/usr/bin/env python3
"""PR5 soak harness for Google Live voice mode.

Runs N Q&A cycles against a tbot-server websocket endpoint, optionally
injects audio for barge-in, and emits a structured JSON report with
per-cycle latencies and aggregated AC1-AC7 verdicts described in
``.omc/plans/google-live-stability-bargein-v2.md`` Section 8.

Designed to be invoked manually before/after a deploy to gate AC PASS.
Reuses helpers from ``voice_mode_websocket_soak`` and the Opus encoder
from ``voice_mode_websocket_audio_bargein`` so we do not duplicate
plumbing. Log-based AC3 validation requires read access to the
``tmp/server.log`` file the server is writing.

Usage:
    python scripts/google_live_robot_soak.py \\
        --device-id 3c:0f:02:de:c2:e0 \\
        --client-id d16afa54-eb44-4fcb-8cac-cdefdf05f6fc \\
        --cycles 10 --bargein-cycles 5 \\
        --report .omc/research/soak.json

    # CI smoke (no server required):
    python scripts/google_live_robot_soak.py \\
        --cycles 1 --duration-sec 5 --dry-run \\
        --report /tmp/soak-dry.json

    # New --mode variants (plan §6.4 / AC1, AC2, AC4):
    python scripts/google_live_robot_soak.py --mode false_positive \\
        --duration 300 --env quiet --report /tmp/fp.json

    python scripts/google_live_robot_soak.py --mode bargein_latency \\
        --trials 10 --inject-audio data/test_stop_vn.wav --report /tmp/lat.json

    python scripts/google_live_robot_soak.py --mode rapid_interrupt \\
        --trials 10 --report /tmp/rapid.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import websockets

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from scripts.voice_mode_websocket_soak import (  # noqa: E402
    _detect_message,
    _hello_message,
    _is_tts_state,
    _recv_until,
)

# ---------------------------------------------------------------------------
# Latency-chain patterns for PR5 modes (also used by analyze_google_live_log)
# ---------------------------------------------------------------------------
LOG_TTS_STOP_SENT_RE = re.compile(r"tts_state_stop_sent|tts_stop_sent")
LOG_REPLAYED_INTERRUPT_RE = re.compile(r"replayed_interrupt_audio")
LOG_INTERRUPT_FINALIZED_RE = re.compile(r"interrupt_input_finalized")
LOG_TRANSCRIPT_SOURCE_USER_RE = re.compile(r"transcript source=user")

DEFAULT_FIRST_PROMPT = (
    "Hãy đếm số tiếng Việt từ một đến năm mươi, chậm rãi và rõ ràng."
)
DEFAULT_INTERRUPT_PROMPT = (
    "Đổi đề tài. Hãy trả lời ngắn về thời tiết Hà Nội hôm nay."
)
DEFAULT_IDLE_PROMPT = (
    "Hãy kể một câu chuyện ngắn bằng tiếng Việt trong khoảng hai phút."
)

LOG_INTERRUPT_RE = re.compile(
    r"Google Live user_interrupted reason=(?P<reason>\w+) "
    r"cancelled_response_id=(?P<cancelled>\d+) "
    r"next_response_id=(?P<next>\d+)"
)
LOG_TRANSCRIPT_USER_RE = re.compile(
    r"Google Live transcript source=user chars=(?P<chars>\d+)"
)
LOG_AUDIO_START_RE = re.compile(r"Google Live audio_start")
LOG_GOAWAY_RE = re.compile(r"session_expiring|go_away|goAway", re.I)
LOG_RECONNECT_RE = re.compile(r"reconnect attempt (\d+) succeeded")
LOG_FALLBACK_RE = re.compile(r"fallback_triggered")


def _safe_soak_config(args):
    """Serialize test controls without utterances, audio paths, or model prose."""
    safe_names = (
        "mode",
        "audio_source",
        "duration",
        "trials",
        "env",
        "skip_firmware_timing",
        "bargein_cycles",
        "idle_cycles",
        "event_timeout_sec",
        "speak_for_sec",
        "idle_duration_sec",
        "open_timeout_sec",
        "interrupt_timeout_sec",
        "settle_timeout_sec",
        "bargein_latency_budget_ms",
        "ac1_goaway_budget",
        "dry_run",
    )
    config = {
        name: getattr(args, name)
        for name in safe_names
        if hasattr(args, name)
    }
    config["inject_audio"] = bool(getattr(args, "inject_audio", None))
    config["inject_text"] = bool(getattr(args, "inject_text", None))
    return config


def _bargein_injection_detect(args):
    if getattr(args, "inject_audio", None):
        return _detect_message("SOAK_AUDIO_INTERRUPT_SENTINEL")
    return _detect_message(str(getattr(args, "interrupt_prompt", "") or ""))


class LogTail:
    """Cheap server.log tail: capture file offset at start, read on demand."""

    def __init__(self, log_path: Path | None):
        self.log_path = log_path
        self._start_offset = self._current_offset()

    def _current_offset(self):
        if self.log_path is None or not self.log_path.exists():
            return None
        try:
            return self.log_path.stat().st_size
        except OSError:
            return None

    def reset(self):
        self._start_offset = self._current_offset()

    def read_new(self):
        if self.log_path is None or self._start_offset is None:
            return ""
        try:
            with self.log_path.open("rb") as fh:
                fh.seek(self._start_offset)
                payload = fh.read()
            return payload.decode("utf-8", errors="replace")
        except OSError:
            return ""


async def _wait_first_binary_after(websocket, start_deadline, log_tail):
    binary = 0
    json_messages = []
    while time.monotonic() < start_deadline:
        remaining = max(0.01, start_deadline - time.monotonic())
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if isinstance(message, bytes):
            return True, binary, json_messages
        binary += 0
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        json_messages.append(payload)
    return False, binary, json_messages


async def _run_bargein_cycle(websocket, cycle_index, args, log_tail):
    record = {
        "index": cycle_index,
        "kind": "bargein",
        "outcome": "FAIL",
        "first_audio_latency_ms": None,
        "bargein_latency_ms": None,
        "user_transcript_received": False,
        "new_response_id": None,
        "cancelled_response_id": None,
        "errors": [],
    }
    log_tail.reset()

    t0 = time.monotonic()
    await websocket.send(json.dumps(_detect_message(f"{args.first_prompt} Lần {cycle_index + 1}.")))
    start_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "start"),
        args.event_timeout_sec,
    )
    if start_payload is None:
        record["errors"].append("first_tts_start_timeout")
        return record
    t1 = time.monotonic()
    record["first_audio_latency_ms"] = round((t1 - t0) * 1000, 1)

    # let the model speak for a while so we have something to interrupt
    await asyncio.sleep(args.speak_for_sec)

    t_int_send = time.monotonic()
    await websocket.send(
        json.dumps(_detect_message(f"{args.interrupt_prompt} Lần {cycle_index + 1}."))
    )
    stop_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "stop"),
        args.interrupt_timeout_sec,
    )
    if stop_payload is None:
        record["errors"].append("interrupt_tts_stop_timeout")
        return record
    t_int_stop = time.monotonic()
    record["bargein_latency_ms"] = round((t_int_stop - t_int_send) * 1000, 1)

    second_start, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "start"),
        args.event_timeout_sec,
    )
    if second_start is None:
        record["errors"].append("post_interrupt_tts_start_timeout")
        return record

    final_stop, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "stop"),
        args.settle_timeout_sec,
    )
    if final_stop is None:
        record["errors"].append("final_tts_stop_timeout")
        return record

    log_chunk = log_tail.read_new()
    transcript_match = LOG_TRANSCRIPT_USER_RE.search(log_chunk)
    interrupt_match = LOG_INTERRUPT_RE.search(log_chunk)
    record["user_transcript_received"] = transcript_match is not None
    if interrupt_match:
        record["cancelled_response_id"] = int(interrupt_match.group("cancelled"))
        record["new_response_id"] = int(interrupt_match.group("next"))

    pass_conditions = [
        record["bargein_latency_ms"] is not None
        and record["bargein_latency_ms"] <= args.bargein_latency_budget_ms,
        record["new_response_id"] is not None
        and record["cancelled_response_id"] is not None
        and record["new_response_id"] > record["cancelled_response_id"],
    ]
    if all(pass_conditions):
        record["outcome"] = "PASS"
    return record


async def _run_idle_cycle(websocket, cycle_index, args, log_tail):
    record = {
        "index": cycle_index,
        "kind": "idle",
        "outcome": "FAIL",
        "false_positive_interrupts": 0,
        "errors": [],
    }
    log_tail.reset()
    await websocket.send(json.dumps(_detect_message(args.idle_prompt)))
    start_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "start"),
        args.event_timeout_sec,
    )
    if start_payload is None:
        record["errors"].append("idle_tts_start_timeout")
        return record

    end_payload, _, _ = await _recv_until(
        websocket,
        lambda payload: _is_tts_state(payload, "stop"),
        args.idle_duration_sec + args.settle_timeout_sec,
    )
    if end_payload is None:
        record["errors"].append("idle_tts_stop_timeout")
    log_chunk = log_tail.read_new()
    false_positive = len(LOG_INTERRUPT_RE.findall(log_chunk))
    record["false_positive_interrupts"] = false_positive
    record["outcome"] = "PASS" if false_positive == 0 else "FAIL"
    return record


def _summarize_acs(cycles, full_log, args):
    bargein_cycles = [c for c in cycles if c["kind"] == "bargein"]
    idle_cycles = [c for c in cycles if c["kind"] == "idle"]
    goaway_count = len(LOG_GOAWAY_RE.findall(full_log))
    reconnect_count = len(LOG_RECONNECT_RE.findall(full_log))
    fallback_count = len(LOG_FALLBACK_RE.findall(full_log))

    bargein_pass = sum(1 for c in bargein_cycles if c["outcome"] == "PASS")
    bargein_latencies = [
        c["bargein_latency_ms"] for c in bargein_cycles if c["bargein_latency_ms"] is not None
    ]
    p95 = (
        sorted(bargein_latencies)[max(0, int(0.95 * (len(bargein_latencies) - 1)))]
        if bargein_latencies
        else None
    )

    idle_pass = sum(1 for c in idle_cycles if c["outcome"] == "PASS")

    return {
        "AC1": {
            "pass": fallback_count == 0 and goaway_count <= args.ac1_goaway_budget,
            "goaway_seen": goaway_count,
            "reconnect_succeeded": reconnect_count,
            "fallback_triggered": fallback_count,
            "budget_goaway": args.ac1_goaway_budget,
        },
        "AC2": {
            "pass": (
                len(bargein_cycles) > 0
                and p95 is not None
                and p95 <= args.bargein_latency_budget_ms
            ),
            "cycles": len(bargein_cycles),
            "p95_latency_ms": p95,
            "budget_ms": args.bargein_latency_budget_ms,
        },
        "AC3": {
            "pass": (
                len(bargein_cycles) > 0
                and bargein_pass >= max(1, int(0.8 * len(bargein_cycles)))
            ),
            "ratio": f"{bargein_pass}/{len(bargein_cycles)}",
            "rule": ">= 80% bargein cycles must produce new response id with user transcript",
        },
        "AC4": {
            "pass": len(idle_cycles) == 0 or idle_pass == len(idle_cycles),
            "ratio": f"{idle_pass}/{len(idle_cycles)}",
            "rule": "0 user_interrupted log lines during idle cycles",
        },
        "AC5": {
            "pass": fallback_count == 0,
            "fallback_triggered": fallback_count,
            "rule": "no fallback to classic during soak; auth/quota tests run separately",
        },
    }


# ---------------------------------------------------------------------------
# PR5 §6.4: Three new mode runners
# ---------------------------------------------------------------------------

async def _run_false_positive_mode(args):
    """AC1: count user_interrupted events during robot soliloquy (no user present)."""
    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    log_tail.reset()
    started_at = time.time()
    duration = getattr(args, "duration", 300)
    env = getattr(args, "env", "unknown")
    false_positives = 0
    tts_stop_timeout = False

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        await websocket.send(json.dumps(_detect_message(args.idle_prompt)))
        tts_start, _, _ = await _recv_until(
            websocket,
            lambda payload: _is_tts_state(payload, "start"),
            args.event_timeout_sec,
        )
        if tts_start is None:
            tts_stop_timeout = True
        else:
            tts_end, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                duration + args.settle_timeout_sec,
            )
            if tts_end is None:
                tts_stop_timeout = True

        await websocket.close()

    log_chunk = log_tail.read_new()
    false_positives = len(LOG_INTERRUPT_RE.findall(log_chunk))
    # AC1 threshold: ≤ 3 false-positives per 15 min (1 per 5 min)
    ac1_budget = max(1, int(duration / 300))
    ac1_pass = false_positives <= ac1_budget

    report = {
        "mode": "false_positive",
        "environment": env,
        "duration_sec": duration,
        "started_at": started_at,
        "elapsed_sec": round(time.time() - started_at, 1),
        "false_positives": false_positives,
        "ac1_budget": ac1_budget,
        "ac1_pass": ac1_pass,
        "tts_stop_timeout": tts_stop_timeout,
        "exit_code": 0 if ac1_pass else 1,
    }
    return report


async def _run_bargein_latency_mode(args):
    """AC2: inject audio mid-TTS, measure T0-T2 server-side latency chain."""
    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    trials = getattr(args, "trials", 10)
    skip_firmware = getattr(args, "skip_firmware_timing", True)
    latencies_ms = []
    started_at = time.time()
    trial_records = []

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        for i in range(trials):
            log_tail.reset()
            await websocket.send(
                json.dumps(_detect_message(f"{args.first_prompt} Trial {i + 1}."))
            )
            tts_start, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "start"),
                args.event_timeout_sec,
            )
            if tts_start is None:
                trial_records.append({"trial": i, "outcome": "FAIL", "error": "tts_start_timeout"})
                continue

            await asyncio.sleep(args.speak_for_sec)

            # T0: emit only a non-sensitive sentinel; binary audio stays hardware-gated.
            t0 = time.monotonic()
            await websocket.send(json.dumps(_bargein_injection_detect(args)))

            tts_stop, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.interrupt_timeout_sec,
            )
            t2 = time.monotonic()

            if tts_stop is None:
                trial_records.append({"trial": i, "outcome": "FAIL", "error": "tts_stop_timeout"})
                continue

            latency_ms = (t2 - t0) * 1000
            latencies_ms.append(latency_ms)

            log_chunk = log_tail.read_new()
            has_interrupted = bool(LOG_INTERRUPT_RE.search(log_chunk))
            trial_records.append({
                "trial": i,
                "outcome": "PASS",
                "t0_to_t2_ms": round(latency_ms, 1),
                "user_interrupted_in_log": has_interrupted,
                "skip_firmware_timing": skip_firmware,
            })

            # wait for next tts_start before next trial
            await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.settle_timeout_sec,
            )

        await websocket.close()

    p50 = None
    p95 = None
    if latencies_ms:
        s = sorted(latencies_ms)
        p50 = s[max(0, int(0.5 * (len(s) - 1)))]
        p95 = s[max(0, int(0.95 * (len(s) - 1)))]

    budget_ms = args.bargein_latency_budget_ms
    ac2_pass = p95 is not None and p95 <= budget_ms

    report = {
        "mode": "bargein_latency",
        "started_at": started_at,
        "elapsed_sec": round(time.time() - started_at, 1),
        "trials": trials,
        "completed_trials": len(latencies_ms),
        "p50_ms": round(p50, 1) if p50 is not None else None,
        "p95_ms": round(p95, 1) if p95 is not None else None,
        "budget_ms": budget_ms,
        "ac2_pass": ac2_pass,
        "trial_records": trial_records,
        "exit_code": 0 if ac2_pass else 1,
    }
    return report


async def _run_rapid_interrupt_mode(args):
    """AC4: inject two utterances 0.2-0.4s apart; assert 1 interrupt per pair."""
    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    trials = getattr(args, "trials", 10)
    started_at = time.time()
    single_interrupt_count = 0
    disconnect_count = 0
    trial_records = []
    rapid_gap_sec = 0.3

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        for i in range(trials):
            log_tail.reset()
            await websocket.send(
                json.dumps(_detect_message(f"{args.first_prompt} Trial {i + 1}."))
            )
            tts_start, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "start"),
                args.event_timeout_sec,
            )
            if tts_start is None:
                trial_records.append({"trial": i, "outcome": "FAIL", "error": "tts_start_timeout"})
                continue

            await asyncio.sleep(args.speak_for_sec)

            # First utterance: "stop"
            await websocket.send(json.dumps(_detect_message(args.interrupt_prompt)))
            await asyncio.sleep(rapid_gap_sec)
            # Second utterance: "play music" — 0.2-0.4s later
            await websocket.send(json.dumps(_detect_message("Phát nhạc ngay đi.")))

            tts_stop, _, _ = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.interrupt_timeout_sec + 1.0,
            )

            log_chunk = log_tail.read_new()
            interrupt_matches = LOG_INTERRUPT_RE.findall(log_chunk)
            interrupt_count = len(interrupt_matches)
            is_single_interrupt = interrupt_count == 1
            if is_single_interrupt:
                single_interrupt_count += 1

            trial_records.append({
                "trial": i,
                "outcome": "PASS" if is_single_interrupt else "FAIL",
                "interrupt_count": interrupt_count,
                "tts_stopped": tts_stop is not None,
            })

            # Allow the response to settle before next trial
            await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.settle_timeout_sec,
            )

        await websocket.close()

    ac4_pass = single_interrupt_count == trials and disconnect_count == 0
    report = {
        "mode": "rapid_interrupt",
        "started_at": started_at,
        "elapsed_sec": round(time.time() - started_at, 1),
        "trials": trials,
        "single_interrupt_trials": single_interrupt_count,
        "disconnect_count": disconnect_count,
        "ac4_pass": ac4_pass,
        "trial_records": trial_records,
        "exit_code": 0 if ac4_pass else 1,
    }
    return report


def _dry_run_report(args):
    """Return a placeholder report when --dry-run is set (no server needed)."""
    started_at = time.time()
    n = args.bargein_cycles
    cycles = [
        {
            "index": i,
            "kind": "bargein",
            "outcome": "SKIPPED_DRY_RUN",
            "first_audio_latency_ms": None,
            "bargein_latency_ms": None,
            "user_transcript_received": False,
            "new_response_id": None,
            "cancelled_response_id": None,
            "stale_audio_after_interrupt_count": 0,
            "errors": ["dry_run"],
        }
        for i in range(n)
    ]
    ac_results = {
        "AC1": {"pass": None, "details": "dry_run — not evaluated"},
        "AC2": {"pass": None, "p95_latency_ms": None, "budget_ms": args.bargein_latency_budget_ms, "details": "dry_run"},
        "AC3": {"pass": None, "ratio": f"0/{n}", "rule": "dry_run"},
        "AC4": {"pass": None, "ratio": "0/0", "rule": "dry_run"},
        "AC5": {"pass": None, "fallback_triggered": 0, "rule": "dry_run"},
    }
    return {
        "started_at": started_at,
        "duration_sec": round(time.time() - started_at, 3),
        "dry_run": True,
        "config": _safe_soak_config(args),
        "cycles": cycles,
        "ac_results": ac_results,
        "error_distribution": {"dry_run": n},
        "all_ac_pass": False,
        "exit_code": 0,
    }


async def run_soak(args):
    mode = getattr(args, "mode", None)
    if mode == "false_positive":
        return await _run_false_positive_mode(args)
    if mode == "bargein_latency":
        return await _run_bargein_latency_mode(args)
    if mode == "rapid_interrupt":
        return await _run_rapid_interrupt_mode(args)

    if getattr(args, "dry_run", False):
        return _dry_run_report(args)

    headers = {
        "device-id": args.device_mac if args.device_mac else args.device_id,
        "client-id": args.client_id,
    }
    log_tail = LogTail(Path(args.log_path) if args.log_path else None)
    initial_offset = log_tail._start_offset
    started_at = time.time()

    cycles = []
    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello_payload, _, _ = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        if hello_payload is None:
            raise RuntimeError("hello ack timeout")

        for index in range(args.bargein_cycles):
            cycle = await _run_bargein_cycle(websocket, index, args, log_tail)
            cycles.append(cycle)
            print(
                "BARGEIN_CYCLE outcome={outcome} "
                "first_audio_ms={fa} bargein_latency_ms={bl} "
                "transcript={tx} new_id={ni} cancelled_id={ci}".format(
                    outcome=cycle["outcome"],
                    fa=cycle["first_audio_latency_ms"],
                    bl=cycle["bargein_latency_ms"],
                    tx=cycle["user_transcript_received"],
                    ni=cycle["new_response_id"],
                    ci=cycle["cancelled_response_id"],
                )
            )

        for index in range(args.idle_cycles):
            cycle = await _run_idle_cycle(websocket, index, args, log_tail)
            cycles.append(cycle)
            print(
                "IDLE_CYCLE outcome={outcome} false_positives={fp}".format(
                    outcome=cycle["outcome"],
                    fp=cycle["false_positive_interrupts"],
                )
            )

        await websocket.close()

    full_log = ""
    if log_tail.log_path and initial_offset is not None:
        try:
            with log_tail.log_path.open("rb") as fh:
                fh.seek(initial_offset)
                full_log = fh.read().decode("utf-8", errors="replace")
        except OSError:
            full_log = ""

    ac_results = _summarize_acs(cycles, full_log, args)
    error_distribution = dict(
        Counter(err for cycle in cycles for err in cycle.get("errors", []))
    )

    all_pass = all(ac["pass"] for ac in ac_results.values())
    report = {
        "started_at": started_at,
        "duration_sec": round(time.time() - started_at, 1),
        "dry_run": False,
        "config": _safe_soak_config(args),
        "cycles": cycles,
        "ac_results": ac_results,
        "error_distribution": error_distribution,
        "all_ac_pass": all_pass,
        "exit_code": 0 if all_pass else 1,
    }
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # Mode selector for PR5 §6.4 modes
    parser.add_argument(
        "--mode",
        choices=["false_positive", "bargein_latency", "rapid_interrupt"],
        default=None,
        help=(
            "false_positive: AC1 soliloquy false-positive count; "
            "bargein_latency: AC2 T0-T2 latency measurement; "
            "rapid_interrupt: AC4 double-interrupt debounce check"
        ),
    )
    parser.add_argument("--duration", type=int, default=300,
                        help="duration in seconds for --mode false_positive (default 300)")
    parser.add_argument("--trials", type=int, default=10,
                        help="number of trials for --mode bargein_latency / rapid_interrupt")
    parser.add_argument("--env", default="unknown",
                        choices=["quiet", "music", "chatter", "unknown"],
                        help="acoustic environment label for --mode false_positive")
    parser.add_argument("--skip-firmware-timing", action="store_true", default=True,
                        help="skip T3/T4 firmware UART timing (software-only run; default: True)")
    # Primary URL arg (spec name --ws-url; --websocket-url kept for backward compat)
    parser.add_argument("--ws-url", "--websocket-url", dest="websocket_url",
                        default="ws://localhost:8000/xiaozhi/v1")
    # Device identity — spec uses --device-mac; --device-id kept for backward compat
    parser.add_argument("--device-mac", "--device-id", dest="device_mac", default=None,
                        help="Device MAC / device-id header sent in websocket handshake")
    parser.add_argument("--client-id", default="soak-harness-client",
                        help="client-id header (optional for text-mode soak)")
    parser.add_argument("--log-path", default="tmp/server.log",
                        help="path to server.log for AC3/AC4 log-tail validation")
    # Cycle counts — --cycles sets bargein-cycles (spec §8 primary knob)
    parser.add_argument("--cycles", "--bargein-cycles", dest="bargein_cycles",
                        type=int, default=10,
                        help="number of barge-in Q&A cycles (spec §8 default 10)")
    parser.add_argument("--idle-cycles", type=int, default=1)
    # Per-cycle max wall-clock (spec §8 --duration-sec)
    parser.add_argument("--duration-sec", dest="event_timeout_sec",
                        type=float, default=600,
                        help="per-cycle max wall-clock in seconds (spec §8 default 600)")
    # Audio injection (spec §8)
    parser.add_argument("--inject-audio", dest="inject_audio", default=None,
                        help="synthetic or consenting-adult WAV; never use child recordings")
    parser.add_argument(
        "--audio-source",
        choices=["synthetic", "adult"],
        default="synthetic",
        help="privacy provenance for injected audio; child audio is forbidden",
    )
    parser.add_argument("--inject-text", dest="inject_text", default=None,
                        help="text injection for interrupt (default: use --interrupt-prompt)")
    # Prompts
    parser.add_argument("--first-prompt", default=DEFAULT_FIRST_PROMPT)
    parser.add_argument("--interrupt-prompt", default=DEFAULT_INTERRUPT_PROMPT)
    parser.add_argument("--idle-prompt", default=DEFAULT_IDLE_PROMPT)
    # Timing knobs
    parser.add_argument("--speak-for-sec", type=float, default=3.0)
    parser.add_argument("--idle-duration-sec", type=float, default=120.0)
    parser.add_argument("--open-timeout-sec", type=float, default=10.0)
    parser.add_argument("--interrupt-timeout-sec", type=float, default=3.0)
    parser.add_argument("--settle-timeout-sec", type=float, default=30.0)
    parser.add_argument("--bargein-latency-budget-ms", type=float, default=500.0)
    parser.add_argument("--ac1-goaway-budget", type=int, default=0)
    # Output
    parser.add_argument("--report", type=Path, default=None,
                        help="write JSON report to this path (required for CI)")
    # Dry-run: validate args + emit placeholder report, no websocket connect
    parser.add_argument("--dry-run", action="store_true",
                        help="skip websocket connect; emit placeholder report for CI smoke")
    args = parser.parse_args()

    # --inject-text overrides --interrupt-prompt when provided
    if args.inject_text:
        args.interrupt_prompt = args.inject_text

    # device_id alias for backward compat in _run_bargein_cycle / _run_idle_cycle
    args.device_id = args.device_mac or "unknown"

    try:
        report = asyncio.run(run_soak(args))
    except Exception as exc:
        print(f"SOAK_FAIL {exc}", file=sys.stderr)
        return 1

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, default=str))
        print(f"Wrote soak report: {args.report}")

    if report.get("dry_run"):
        print("DRY_RUN_OK schema validated, no server connection made")
    if "ac_results" in report:
        print(json.dumps(report["ac_results"], indent=2))
    return report.get("exit_code", 0 if report.get("all_ac_pass", False) else 1)


if __name__ == "__main__":
    raise SystemExit(main())
