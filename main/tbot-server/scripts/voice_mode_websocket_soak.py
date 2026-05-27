#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
import time

import websockets


DEFAULT_FIRST_TEXT = (
    "Hãy kể ngắn gọn bằng tiếng Việt về cách robot xử lý âm thanh sạch, "
    "ngắt ngang khi người dùng nói, và ưu tiên ý định mới nhất."
)
DEFAULT_INTERRUPT_TEXT = "Dừng câu cũ. Trả lời ngắn: hôm nay kiểm tra ngắt ngang thế nào?"


def _hello_message():
    return {
        "type": "hello",
        "version": 1,
        "transport": "websocket",
        "audio_params": {
            "format": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "frame_duration": 60,
        },
        "features": {},
    }


def _detect_message(text):
    return {"type": "listen", "state": "detect", "text": text}


def _is_tts_state(payload, state):
    return payload.get("type") == "tts" and payload.get("state") == state


async def _recv_until(websocket, predicate, timeout_sec):
    deadline = time.monotonic() + timeout_sec
    binary_chunks = 0
    json_messages = []
    while time.monotonic() < deadline:
        remaining = max(0.01, deadline - time.monotonic())
        try:
            message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if isinstance(message, bytes):
            binary_chunks += 1
            continue
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            continue
        json_messages.append(payload)
        if predicate(payload):
            return payload, binary_chunks, json_messages
    return None, binary_chunks, json_messages


async def run_soak(args):
    uri = args.websocket_url
    headers = {
        "device-id": args.device_id,
        "client-id": args.client_id,
    }
    summary = {
        "cycles": 0,
        "tts_starts": 0,
        "tts_stops": 0,
        "binary_chunks": 0,
    }
    async with websockets.connect(
        uri,
        additional_headers=headers,
        open_timeout=args.open_timeout_sec,
        max_size=None,
    ) as websocket:
        await websocket.send(json.dumps(_hello_message()))
        hello, binary_count, _messages = await _recv_until(
            websocket,
            lambda payload: payload.get("type") == "hello",
            args.event_timeout_sec,
        )
        summary["binary_chunks"] += binary_count
        if hello is None:
            raise RuntimeError("hello ack timeout")

        for index in range(args.cycles):
            await websocket.send(json.dumps(_detect_message(f"{args.first_text} Lần {index + 1}.")))
            start, binary_count, _messages = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "start"),
                args.event_timeout_sec,
            )
            summary["binary_chunks"] += binary_count
            if start is None:
                raise RuntimeError(f"cycle {index + 1}: first tts start timeout")
            summary["tts_starts"] += 1

            await asyncio.sleep(args.interrupt_delay_sec)
            await websocket.send(
                json.dumps(_detect_message(f"{args.interrupt_text} Lần {index + 1}."))
            )
            stop, binary_count, _messages = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.interrupt_timeout_sec,
            )
            summary["binary_chunks"] += binary_count
            if stop is None:
                raise RuntimeError(f"cycle {index + 1}: interrupt tts stop timeout")
            summary["tts_stops"] += 1

            latest_start, binary_count, _messages = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "start"),
                args.event_timeout_sec,
            )
            summary["binary_chunks"] += binary_count
            if latest_start is None:
                raise RuntimeError(f"cycle {index + 1}: latest tts start timeout")
            summary["tts_starts"] += 1

            latest_stop, binary_count, _messages = await _recv_until(
                websocket,
                lambda payload: _is_tts_state(payload, "stop"),
                args.settle_timeout_sec,
            )
            summary["binary_chunks"] += binary_count
            if latest_stop is None:
                raise RuntimeError(f"cycle {index + 1}: latest tts stop timeout")

            summary["cycles"] += 1
            print(
                "SOAK_CYCLE_OK "
                f"cycle={summary['cycles']} "
                f"tts_starts={summary['tts_starts']} "
                f"tts_stops={summary['tts_stops']} "
                f"binary_chunks={summary['binary_chunks']}"
            )

        await websocket.close()
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Websocket Google Live voice-mode text barge-in soak."
    )
    parser.add_argument("--websocket-url", default="ws://127.0.0.1:8000/tbot/v1/")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--first-text", default=DEFAULT_FIRST_TEXT)
    parser.add_argument("--interrupt-text", default=DEFAULT_INTERRUPT_TEXT)
    parser.add_argument("--interrupt-delay-sec", type=float, default=0.25)
    parser.add_argument("--open-timeout-sec", type=float, default=5)
    parser.add_argument("--event-timeout-sec", type=float, default=20)
    parser.add_argument("--interrupt-timeout-sec", type=float, default=3)
    parser.add_argument("--settle-timeout-sec", type=float, default=20)
    args = parser.parse_args()

    try:
        summary = asyncio.run(run_soak(args))
    except Exception as exc:
        print(f"SOAK_FAIL {exc}", file=sys.stderr)
        return 1
    print(
        "SOAK_OK "
        f"cycles={summary['cycles']} "
        f"tts_starts={summary['tts_starts']} "
        f"tts_stops={summary['tts_stops']} "
        f"binary_chunks={summary['binary_chunks']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
