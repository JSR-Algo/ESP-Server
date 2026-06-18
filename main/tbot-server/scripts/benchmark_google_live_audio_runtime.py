"""Benchmark Google Live bridge audio CPU cost and loop latency.

This is a local load harness: it does not open Gemini Live or firmware sockets.
It drives N concurrent bridges with real Opus decode, resampling, AEC-reference
push, and Opus encode, then records process CPU cost per active stream and
per-connection event-loop wake delay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import statistics
import time
from pathlib import Path
from types import SimpleNamespace

import psutil

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.utils.opus_encoder_utils import OpusEncoderUtils
from core.voice.google_live.audio_bridge import GoogleLiveAudioBridge

FRAME_MS = 60
DEFAULT_HEADROOM_FRACTION = 0.70


class _Logger:
    def bind(self, **kwargs):
        return self

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _Client:
    def __init__(self, config):
        self.config = config
        self.sent_audio_bytes = 0
        self.sent_audio_frames = 0

    async def send_audio(self, audio_bytes):
        self.sent_audio_bytes += len(audio_bytes)
        self.sent_audio_frames += 1


def _make_conn(sample_rate=24000, input_sample_rate=16000, aec_enabled=False):
    return SimpleNamespace(
        config={
            "google_live": {
                "input_sample_rate": input_sample_rate,
                "input_live_chunk_ms": 20,
                "aec_enabled": aec_enabled,
                "log_audio_diagnostics": False,
            }
        },
        sample_rate=sample_rate,
        logger=_Logger(),
        websocket=None,
        session_id="benchmark-session",
        google_live_session_started_at=None,
        google_live_turn_started_at=None,
        google_live_audio_out_started_at=None,
    )


def _make_opus_frame(sample_rate=24000, frame_ms=FRAME_MS):
    encoder = OpusEncoderUtils(sample_rate=sample_rate, channels=1, frame_size_ms=frame_ms)
    packets = []
    pcm = b"\x00\x00" * int(sample_rate * frame_ms / 1000)
    encoder.encode_pcm_to_opus_stream(pcm, end_of_stream=False, callback=packets.append)
    encoder.close()
    if not packets:
        raise RuntimeError("failed to generate benchmark opus frame")
    return packets[0]


async def _heartbeat(stop_event, interval_sec, samples):
    next_tick = time.perf_counter() + interval_sec
    while not stop_event.is_set():
        await asyncio.sleep(max(0.0, next_tick - time.perf_counter()))
        now = time.perf_counter()
        samples.append(max(0.0, now - next_tick) * 1000)
        next_tick += interval_sec


async def _speaker(
    bridge,
    opus_frame,
    output_pcm,
    frames,
    latencies_ms,
    connection_loop_latencies_ms,
    device_packet_counts,
    pace_realtime,
    offload_audio,
):
    frame_interval_sec = FRAME_MS / 1000
    next_frame_at = time.perf_counter()
    for index in range(frames):
        now = time.perf_counter()
        if index > 0 and pace_realtime:
            connection_loop_latencies_ms.append(max(0.0, now - next_frame_at) * 1000)
        start = time.perf_counter()
        if offload_audio:
            await bridge.forward_input_audio(opus_frame)
            packets = await bridge._run_audio_cpu(
                bridge._encode_output_audio,
                output_pcm,
                "audio/pcm;rate=24000",
            )
        else:
            await bridge.forward_decoded_input_audio(bridge._decode_input_audio(opus_frame))
            packets = bridge._encode_output_audio(output_pcm, "audio/pcm;rate=24000")
        device_packet_counts.append(len(packets))
        latencies_ms.append((time.perf_counter() - start) * 1000)
        if pace_realtime and index < frames - 1:
            next_frame_at += frame_interval_sec
            await asyncio.sleep(max(0.0, next_frame_at - time.perf_counter()))


def _percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round((percentile / 100) * (len(ordered) - 1))))
    return ordered[index]


async def run_case(
    speakers,
    frames_per_speaker,
    aec_enabled,
    pace_realtime=True,
    offload_audio=True,
):
    opus_frame = _make_opus_frame()
    output_pcm = b"\x00\x00" * int(24000 * FRAME_MS / 1000)
    bridges = []
    clients = []
    for _ in range(speakers):
        conn = _make_conn(aec_enabled=aec_enabled)
        client = _Client(conn.config["google_live"])
        clients.append(client)
        bridges.append(GoogleLiveAudioBridge(conn, client, _Logger()))

    heartbeat_samples = []
    frame_latencies = []
    connection_loop_latencies = []
    device_packet_counts = []
    stop_event = asyncio.Event()
    heartbeat_task = asyncio.create_task(_heartbeat(stop_event, 0.01, heartbeat_samples))
    process = psutil.Process()
    cpu_start = sum(process.cpu_times()[:2])
    wall_start = time.perf_counter()
    try:
        await asyncio.gather(
            *[
                _speaker(
                    bridge,
                    opus_frame,
                    output_pcm,
                    frames_per_speaker,
                    frame_latencies,
                    connection_loop_latencies,
                    device_packet_counts,
                    pace_realtime,
                    offload_audio,
                )
                for bridge in bridges
            ]
        )
    finally:
        wall_elapsed = time.perf_counter() - wall_start
        cpu_end = sum(process.cpu_times()[:2])
        stop_event.set()
        await heartbeat_task
        await asyncio.gather(*[bridge.close() for bridge in bridges])

    cpu_elapsed = max(0.0, cpu_end - cpu_start)
    stream_audio_sec = frames_per_speaker * FRAME_MS / 1000
    cost_per_stream = cpu_elapsed / max(speakers * stream_audio_sec, 0.001)
    return {
        "speakers": speakers,
        "audio_execution": "worker" if offload_audio else "inline",
        "frames_per_speaker": frames_per_speaker,
        "stream_audio_ms": int(stream_audio_sec * 1000),
        "wall_ms": round(wall_elapsed * 1000, 2),
        "process_cpu_ms": round(cpu_elapsed * 1000, 2),
        "cost_per_active_stream_core_fraction": round(cost_per_stream, 4),
        "recommended_accept_cap_1_core_70_headroom": max(
            1, int(DEFAULT_HEADROOM_FRACTION / max(cost_per_stream, 0.0001))
        ),
        "frame_latency_avg_ms": round(statistics.fmean(frame_latencies), 3),
        "frame_latency_p95_ms": round(_percentile(frame_latencies, 95), 3),
        "connection_loop_latency_p95_ms": round(
            _percentile(connection_loop_latencies, 95), 3
        ),
        "loop_heartbeat_p95_ms": round(_percentile(heartbeat_samples, 95), 3),
        "sent_live_frames": sum(client.sent_audio_frames for client in clients),
        "sent_device_packets": sum(device_packet_counts),
    }


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--speakers", default="1,2,4")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--aec", action="store_true")
    parser.add_argument(
        "--no-pace",
        action="store_true",
        help="Disable 60ms frame pacing and run a burst stress benchmark.",
    )
    parser.add_argument(
        "--inline-audio",
        action="store_true",
        help="Run the historical inline event-loop audio path as a comparison baseline.",
    )
    args = parser.parse_args()
    speaker_counts = [int(value) for value in args.speakers.split(",") if value.strip()]
    rows = []
    for speakers in speaker_counts:
        rows.append(
            await run_case(
                speakers,
                args.frames,
                args.aec,
                pace_realtime=not args.no_pace,
                offload_audio=not args.inline_audio,
            )
        )
    print(json.dumps({"results": rows}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
