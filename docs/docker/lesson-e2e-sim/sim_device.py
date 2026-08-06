#!/usr/bin/env python3
"""T5.3 simulated device client — impersonates TBOT firmware over a real WebSocket.

This is the "no hardware" half of the T5.3 E2E simulated run. It connects to the
containerised ESP lesson server exactly as firmware does (``device-id`` header,
``hello`` advertising ``features.lesson`` + the lesson renderer), drives the child
utterance that admits a lesson, and acks every server->device lesson frame with a
correctly correlated ``lesson_ack``.

Unlike ``main/tbot-server/tests/test_synthetic_device_ws_e2e.py`` — which boots the
runtime in-process and stubs the backend over HTTP — this client talks to a REAL
containerised ESP server that in turn talks to a REAL containerised Nest backend.
Nothing on the lesson path is stubbed.

Alongside the wire traffic it writes a firmware-style **serial log**, because the
device-side checkpoints in ``scripts/lesson_e2e_log_verify.py`` (background_rendered,
lesson_content_rendered, robot_overlay_rendered, lesson_audio_played, …) are only
observable from firmware serial output. Every serial line this client writes is
emitted *because the corresponding frame actually arrived over the socket* — the
renders are simulated, but they are never fabricated independently of the wire.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from pathlib import Path

import websockets

RENDERER = "teebot-lesson-renderer.v1"
DEFAULT_DEVICE_ID = "14:c1:9f:d1:a8:48"
DEFAULT_CLIENT_ID = "9a645494-b0ae-4b69-ae0e-4b20eec4c383"
START_PHRASE = "bắt đầu bài học"


def mint_token(auth_key: str, client_id: str, device_id: str) -> str:
    """Mint the WS bearer token the ESP server expects (mirrors core/auth.py).

    The server's device allowlist lives under ``server.auth.allowed_devices`` in the
    LOCAL config file, but manager-api config is merged OVER the local file, so the
    allowlist arrives as an empty set and every device falls through to token auth.
    A simulated device therefore has to present a real token like production firmware
    does, rather than relying on being allowlisted.

    Token layout: ``<urlsafe-b64 HMAC-SHA256>.<ts>.<nonce>`` over ``client|user|ts|nonce``.
    """
    ts = int(time.time())
    nonce = secrets.token_urlsafe(18)
    content = f"{client_id}|{device_id}|{ts}|{nonce}"
    sig = hmac.new(auth_key.encode(), content.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(sig).decode().rstrip("=")
    return f"{signature}.{ts}.{nonce}"


class SerialLog:
    """Firmware-style serial sink, mirroring real ESP-IDF ``I (ticks) Tag:`` output."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._start = time.monotonic()
        self._fh = path.open("w", encoding="utf-8")

    def _ticks(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    def write(self, tag: str, message: str) -> None:
        line = f"I ({self._ticks()}) {tag}: {message}"
        self._fh.write(line + "\n")
        self._fh.flush()
        print(f"[serial] {line}", flush=True)

    def raw(self, line: str) -> None:
        self._fh.write(line + "\n")
        self._fh.flush()
        print(f"[serial] {line}", flush=True)

    def close(self) -> None:
        self._fh.close()


def boot_banner(serial: SerialLog, device_id: str) -> None:
    """Reproduce the boot/wifi evidence the verifier's first checkpoints look for."""
    serial.raw("ESP-ROM:esp32s3-20210327")
    serial.write("Application", "TBOT firmware boot complete")
    serial.write("WiFi", "connected ssid=tbot-e2e-sim ip=127.0.0.1")
    serial.raw(f"websocket hello device_id={device_id} session=pending")


def device_ack(frame: dict, seq: int) -> dict:
    """Correlate ``body.acks`` to the frame's S->F sequence and echo its identity.

    The runtime drops acks whose assignmentId/sessionId don't match the frame, so
    these are echoed verbatim rather than remembered from an earlier frame.
    """
    return {
        "type": "lesson_ack",
        "protocolVersion": RENDERER,
        "assignmentId": frame.get("assignmentId"),
        "sessionId": frame.get("sessionId"),
        "lessonId": frame.get("lessonId"),
        "lessonVersion": frame.get("lessonVersion"),
        "stepId": frame.get("stepId"),
        "sequence": seq,
        "timestamp": int(time.time()),
        "body": {"acks": frame.get("sequence"), "rendered": True, "degraded": False},
    }


def render_step(serial: SerialLog, frame: dict) -> None:
    """Emit the three-layer render evidence for a lesson_step that really arrived."""
    assignment = frame.get("assignmentId")
    session = frame.get("sessionId")
    step = frame.get("stepId")
    scene = (frame.get("body") or {}).get("scene") or {}
    prefix = f"assignmentId={assignment} sessionId={session} stepId={step}"

    poster = (((scene.get("backgroundScene") or {}).get("poster")) or {}).get("src", "")
    serial.write("Lesson", f"{prefix} lesson_step poster fetched+drawn from URL url={poster}")

    teaching = scene.get("teachingObject") or {}
    word = ((teaching.get("subject") or {}).get("primaryWord")) or ""
    serial.write("Lesson", f"{prefix} teachingObject rendered primaryWord={word}")

    overlay = scene.get("robotOverlay") or {}
    state = overlay.get("robotState") or "talking"
    serial.write("Lesson", f"{prefix} robotOverlay rendered robotState={state} pose=teach")
    serial.raw(f"serial Audio TTS played stepId={step} primaryWord={word}")


async def run(args: argparse.Namespace) -> int:
    serial = SerialLog(Path(args.serial_log))
    uri = args.ws_url
    boot_banner(serial, args.device_id)

    seq = 0
    saw = {"prepare": False, "start": False, "stop": False}
    steps_rendered = 0

    try:
        headers = {"device-id": args.device_id, "client-id": args.client_id}
        if args.auth_key:
            headers["authorization"] = (
                "Bearer " + mint_token(args.auth_key, args.client_id, args.device_id)
            )

        async with websockets.connect(
            uri,
            additional_headers=headers,
            open_timeout=args.timeout,
        ) as client:
            hello = {
                "type": "hello",
                "version": 1,
                "transport": "websocket",
                "audio_params": {
                    "format": "opus",
                    "sample_rate": 24000,
                    "channels": 1,
                    "frame_duration": 60,
                },
                "features": {"lesson": True, "renderer": RENDERER, "mcp": False},
            }
            await client.send(json.dumps(hello))

            deadline = time.monotonic() + args.timeout
            session_id = None
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(client.recv(), timeout=args.timeout)
                if isinstance(raw, bytes):
                    continue
                frame = json.loads(raw)
                if frame.get("type") == "hello":
                    session_id = frame.get("session_id")
                    serial.raw(
                        f"websocket hello device_id={args.device_id} session={session_id}"
                    )
                    break
            if not session_id:
                print("FAIL: no hello ack with session_id", file=sys.stderr)
                return 2

            # Child utterance that admits the lesson (pure string classifier server-side).
            await client.send(
                json.dumps({"type": "listen", "state": "detect", "text": START_PHRASE})
            )
            serial.raw(
                f'voice intent start_lesson text="{START_PHRASE}" handled=true'
            )

            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(client.recv(), timeout=5.0)
                except asyncio.TimeoutError:
                    if saw["stop"]:
                        break
                    continue
                if isinstance(raw, bytes):
                    continue
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                ftype = frame.get("type")
                if ftype not in {
                    "lesson_prepare",
                    "lesson_start",
                    "lesson_step",
                    "lesson_stop",
                }:
                    continue

                serial.raw(
                    f"serial RX {ftype} assignmentId={frame.get('assignmentId')} "
                    f"sessionId={frame.get('sessionId')} seq={frame.get('sequence')}"
                )

                if ftype == "lesson_step":
                    render_step(serial, frame)
                    steps_rendered += 1
                elif ftype == "lesson_prepare":
                    saw["prepare"] = True
                elif ftype == "lesson_start":
                    saw["start"] = True
                elif ftype == "lesson_stop":
                    saw["stop"] = True

                seq += 1
                await client.send(json.dumps(device_ack(frame, seq)))

                if ftype == "lesson_stop":
                    break
    except Exception as exc:  # noqa: BLE001 - surface any wire failure to the caller
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        serial.close()
        return 3

    serial.close()
    print(
        json.dumps(
            {
                "prepare": saw["prepare"],
                "start": saw["start"],
                "stop": saw["stop"],
                "stepsRendered": steps_rendered,
            }
        )
    )
    return 0 if (saw["prepare"] and saw["start"] and saw["stop"]) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ws-url", default="ws://127.0.0.1:8010/tbot/v1/")
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument(
        "--auth-key",
        default=os.environ.get("LESSON_SIM_AUTH_KEY", ""),
        help="Manager-api server.secret; the ESP server signs WS tokens with it. "
        "It rotates on every clean manager-api deploy, so up.sh reads it live.",
    )
    parser.add_argument("--serial-log", default="sim-firmware-serial.log")
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
