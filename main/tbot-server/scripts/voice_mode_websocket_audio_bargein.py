#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import websockets

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.utils.opus_encoder_utils import OpusEncoderUtils  # noqa: E402
from core.utils.util import audio_to_data_stream  # noqa: E402
from scripts.google_live_reliability import GOOGLE_LIVE_LIMITS  # noqa: E402
from scripts.voice_mode_websocket_soak import (  # noqa: E402
    _build_headers,
    _hello_message,
    _is_tts_state,
    _recv_until,
)

DEFAULT_TEXT = (
    "Hãy trả lời bằng tiếng Việt trong khoảng hai câu về kiểm thử ngắt ngang "
    "khi robot đang nói."
)
REPLACEMENT_RESPONSE_INCOMPLETE = "REPLACEMENT_RESPONSE_INCOMPLETE"


def _detect_message(text):
    return {"type": "listen", "state": "detect", "text": text}


def _tone_pcm(sample_rate, duration_sec, rms):
    sample_count = int(sample_rate * duration_sec)
    peak = min(30000, max(0, int(rms * math.sqrt(2))))
    timeline = np.arange(sample_count, dtype=np.float64) / sample_rate
    samples = np.sin(2 * math.pi * 440 * timeline) * peak
    return samples.astype(np.int16).tobytes()


def _opus_packets(sample_rate, frame_duration_ms, duration_sec, rms):
    encoder = OpusEncoderUtils(sample_rate, 1, frame_duration_ms)
    packets = []
    encoder.encode_pcm_to_opus_stream(
        _tone_pcm(sample_rate, duration_sec, rms),
        end_of_stream=True,
        callback=packets.append,
    )
    encoder.close()
    return packets


def _opus_packets_from_audio_file(audio_file, sample_rate, frame_duration_ms):
    encoder = OpusEncoderUtils(sample_rate, 1, frame_duration_ms)
    packets = []
    try:
        audio_to_data_stream(
            audio_file,
            is_opus=True,
            callback=packets.append,
            sample_rate=sample_rate,
            opus_encoder=encoder,
        )
    finally:
        encoder.close()
    return packets


async def _collect_replacement_response(websocket, *, timeout_sec, clock=time.monotonic):
    deadline = clock() + timeout_sec
    started = False
    terminal_stopped = False
    binary_times = []
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            break
        try:
            message = await asyncio.wait_for(
                websocket.recv(),
                timeout=max(0.01, remaining),
            )
        except asyncio.TimeoutError:
            break
        now = clock()
        if isinstance(message, bytes):
            if started:
                binary_times.append(now)
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        if _is_tts_state(payload, "start"):
            started = True
        elif started and _is_tts_state(payload, "stop"):
            terminal_stopped = True
            break

    gaps = [
        (right - left) * 1000
        for left, right in zip(binary_times, binary_times[1:], strict=False)
    ]
    return {
        "replacementResponseStarted": started,
        "replacementResponseStopped": terminal_stopped,
        "replacementBinaryChunks": len(binary_times),
        "maxServerOutputGapMs": round(max(gaps, default=0.0), 1),
    }


async def run_smoke(args, *, clock=time.monotonic):
    headers = _build_headers(args)
    if getattr(args, "audio_file", ""):
        packets = _opus_packets_from_audio_file(
            args.audio_file,
            args.sample_rate,
            args.frame_duration_ms,
        )
    else:
        packets = _opus_packets(
            args.sample_rate,
            args.frame_duration_ms,
            args.audio_duration_sec,
            args.rms,
        )
    if not packets:
        raise RuntimeError("no opus packets generated")

    summary = {
        "status": "FAIL",
        "opus_packets": len(packets),
        "tts_starts": 0,
        "tts_stops": 0,
        "binary_chunks": 0,
        "oldResponseStopped": False,
        "replacementResponseStarted": False,
        "replacementResponseStopped": False,
        "replacementBinaryChunks": 0,
        "maxServerOutputGapMs": 0.0,
        "bargeinStopMs": None,
        "correlationSource": "server_log",
    }

    async with websockets.connect(
        args.websocket_url,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        hello = _hello_message()
        hello["audio_params"]["sample_rate"] = args.sample_rate
        hello["audio_params"]["frame_duration"] = args.frame_duration_ms
        await websocket.send(json.dumps(hello))
        ack, binary_count, _messages = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        summary["binary_chunks"] += binary_count
        if ack is None:
            raise RuntimeError("hello ack timeout")

        await websocket.send(json.dumps(_detect_message(args.text)))
        start, binary_count, _messages = await _recv_until(
            websocket,
            lambda payload: _is_tts_state(payload, "start"),
            args.event_timeout_sec,
        )
        summary["binary_chunks"] += binary_count
        if start is None:
            raise RuntimeError("tts start timeout")
        summary["tts_starts"] += 1

        await asyncio.sleep(args.interrupt_delay_sec)
        interrupt_sent_at = clock()
        for packet in packets:
            await websocket.send(packet)
            await asyncio.sleep(args.frame_duration_ms / 1000)

        stop, binary_count, _messages = await _recv_until(
            websocket,
            lambda payload: _is_tts_state(payload, "stop"),
            args.interrupt_timeout_sec,
        )
        stop_observed_at = clock()
        summary["binary_chunks"] += binary_count
        if stop is None:
            raise RuntimeError("audio interrupt tts stop timeout")
        summary["tts_stops"] += 1
        summary["oldResponseStopped"] = True
        summary["bargeinStopMs"] = round(
            (stop_observed_at - interrupt_sent_at) * 1000,
            1,
        )

        replacement = await _collect_replacement_response(
            websocket,
            timeout_sec=args.event_timeout_sec,
            clock=clock,
        )
        summary.update(replacement)
        summary["binary_chunks"] += replacement["replacementBinaryChunks"]
        if replacement["replacementResponseStarted"]:
            summary["tts_starts"] += 1
        if replacement["replacementResponseStopped"]:
            summary["tts_stops"] += 1

        if (
            not replacement["replacementResponseStarted"]
            or replacement["replacementBinaryChunks"] < 1
            or not replacement["replacementResponseStopped"]
        ):
            summary["failureCode"] = REPLACEMENT_RESPONSE_INCOMPLETE
            return summary
        if summary["bargeinStopMs"] > GOOGLE_LIVE_LIMITS["physicalBargeinP95Ms"]:
            summary["failureCode"] = "BARGEIN_STOP_LATENCY_EXCEEDED"
            return summary
        if (
            replacement["maxServerOutputGapMs"]
            > GOOGLE_LIVE_LIMITS["serverOutputGapMaxMs"]
        ):
            summary["failureCode"] = "SERVER_OUTPUT_GAP_EXCEEDED"
            return summary

        summary["status"] = "PASS"
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Websocket Google Live voice-mode synthetic Opus audio barge-in smoke."
    )
    parser.add_argument("--websocket-url", default="ws://127.0.0.1:8000/tbot/v1/")
    parser.add_argument("--ota-url", default="")
    parser.add_argument("--authorization-token", default="")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--frame-duration-ms", type=int, default=60)
    parser.add_argument("--audio-duration-sec", type=float, default=0.6)
    parser.add_argument("--audio-file", default="")
    parser.add_argument("--rms", type=int, default=9000)
    parser.add_argument("--interrupt-delay-sec", type=float, default=0.3)
    parser.add_argument("--open-timeout-sec", type=float, default=5)
    parser.add_argument("--event-timeout-sec", type=float, default=20)
    parser.add_argument("--interrupt-timeout-sec", type=float, default=5)
    args = parser.parse_args()

    try:
        summary = asyncio.run(run_smoke(args))
    except Exception as exc:
        print(f"AUDIO_BARGE_IN_FAIL {exc}", file=sys.stderr)
        return 1
    if summary["status"] != "PASS":
        print(
            "AUDIO_BARGE_IN_FAIL "
            f"failure_code={summary.get('failureCode', 'UNKNOWN')}",
            file=sys.stderr,
        )
        return 1
    print(
        "AUDIO_BARGE_IN_OK "
        f"opus_packets={summary['opus_packets']} "
        f"tts_starts={summary['tts_starts']} "
        f"tts_stops={summary['tts_stops']} "
        f"binary_chunks={summary['binary_chunks']} "
        f"replacement_binary_chunks={summary['replacementBinaryChunks']} "
        f"bargein_stop_ms={summary['bargeinStopMs']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
