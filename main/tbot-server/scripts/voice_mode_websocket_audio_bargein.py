#!/usr/bin/env python3
import argparse
import asyncio
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
import websockets

SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from core.utils.opus_encoder_utils import OpusEncoderUtils
from scripts.voice_mode_websocket_soak import _hello_message, _is_tts_state, _recv_until

DEFAULT_TEXT = (
    "Hãy trả lời bằng tiếng Việt trong khoảng hai câu về kiểm thử ngắt ngang "
    "khi robot đang nói."
)


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


async def run_smoke(args):
    headers = {
        "device-id": args.device_id,
        "client-id": args.client_id,
    }
    packets = _opus_packets(
        args.sample_rate,
        args.frame_duration_ms,
        args.audio_duration_sec,
        args.rms,
    )
    if not packets:
        raise RuntimeError("no opus packets generated")

    summary = {
        "opus_packets": len(packets),
        "tts_starts": 0,
        "tts_stops": 0,
        "binary_chunks": 0,
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
        for packet in packets:
            await websocket.send(packet)
            await asyncio.sleep(args.frame_duration_ms / 1000)

        stop, binary_count, _messages = await _recv_until(
            websocket,
            lambda payload: _is_tts_state(payload, "stop"),
            args.interrupt_timeout_sec,
        )
        summary["binary_chunks"] += binary_count
        if stop is None:
            raise RuntimeError("audio interrupt tts stop timeout")
        summary["tts_stops"] += 1

        await websocket.close()
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Websocket Google Live voice-mode synthetic Opus audio barge-in smoke."
    )
    parser.add_argument("--websocket-url", default="ws://127.0.0.1:8000/tbot/v1/")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--sample-rate", type=int, default=24000)
    parser.add_argument("--frame-duration-ms", type=int, default=60)
    parser.add_argument("--audio-duration-sec", type=float, default=0.6)
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
    print(
        "AUDIO_BARGE_IN_OK "
        f"opus_packets={summary['opus_packets']} "
        f"tts_starts={summary['tts_starts']} "
        f"tts_stops={summary['tts_stops']} "
        f"binary_chunks={summary['binary_chunks']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
