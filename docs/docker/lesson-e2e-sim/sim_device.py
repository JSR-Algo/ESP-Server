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

import urllib.error
import urllib.request

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
    # No session line here: the real session id is only known after the hello ACK, and a
    # placeholder makes the verifier's session_consistent check see two distinct sessions.


def device_ack(frame: dict, seq: int) -> dict:
    """Correlate ``body.acks`` to the frame's S->F sequence and echo its identity.

    The runtime drops acks whose assignmentId/sessionId don't match the frame, so
    these are echoed verbatim rather than remembered from an earlier frame.
    """
    body = {"acks": frame.get("sequence"), "rendered": True, "degraded": False}

    # SD-pack delivery: the runtime will not leave PREPARE unless the DEVICE reports a
    # verified pack whose cacheKey matches the one it is expecting
    # (`_ack_reports_asset_pack_ready`, runtime.py:5968). Real firmware reports this after
    # materializing the pack on the SD card; the simulator echoes the cacheKey it was just
    # handed in lesson_prepare.
    #
    # FIDELITY BOUNDARY: the simulator does not write an SD card, so this attests that the
    # pack it was told about is the pack it "has". It cannot catch a firmware-side
    # materialization or checksum bug — that needs T5.4 on real hardware. It does exercise
    # the cacheKey agreement, so a server/device cacheKey mismatch WOULD fail here.
    pack = (frame.get("body") or {}).get("assetPack")
    if isinstance(pack, dict) and isinstance(pack.get("cacheKey"), str):
        body["assetPack"] = {"ready": True, "cacheKey": pack["cacheKey"]}

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
        "body": body,
    }


def render_step(serial: SerialLog, frame: dict) -> str:
    """Emit the three-layer render evidence for a lesson_step that really arrived."""
    assignment = frame.get("assignmentId")
    session = frame.get("sessionId")
    step = frame.get("stepId")
    scene = (frame.get("body") or {}).get("scene") or {}
    prefix = f"assignmentId={assignment} sessionId={session} stepId={step}"

    poster = (((scene.get("backgroundScene") or {}).get("poster")) or {}).get("src", "")
    serial.write("Lesson", f"{prefix} lesson_step poster fetched+drawn from URL url={poster}")

    # primaryWord sits directly on teachingObject; body.subject is the same word and is
    # the fallback when a step ships no teaching object.
    teaching = scene.get("teachingObject") or {}
    word = teaching.get("primaryWord") or (frame.get("body") or {}).get("subject") or ""
    serial.write("Lesson", f"{prefix} teachingObject rendered primaryWord={word}")

    overlay = scene.get("robotOverlay") or {}
    state = overlay.get("robotState") or "talking"
    serial.write("Lesson", f"{prefix} robotOverlay rendered robotState={state} pose=teach")
    serial.raw(f"serial Audio TTS played stepId={step} primaryWord={word}")
    return word


def inject_child_response(args, device_id: str, text: str) -> bool:
    """Answer an interactive step through the ESP's internal child-response endpoint.

    A real child answers by SPEAKING, and the utterance reaches the runtime through the
    voice provider. The simulator has no voice pipeline (no Gemini key, no audio), so a
    `listen/detect` text frame is never routed to the lesson runtime and the step just
    times out. `POST /internal/devices/{id}/lesson-child-response` is the supported
    injection point for exactly this (it is what the backend nudge path uses), so the
    simulator drives interactive steps through it.

    FIDELITY BOUNDARY: this exercises the runtime's child-response handling and step
    advancement, NOT ASR. Whether the robot actually hears and recognises the word is a
    T5.4 hardware question.
    """
    url = f"{args.esp_http_base.rstrip('/')}/internal/devices/{device_id}/lesson-child-response"
    payload = json.dumps({"text": text}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "X-Mint-Secret": args.mint_secret},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read() or b"{}")
            return bool((data.get("data") or {}).get("handled"))
    except urllib.error.HTTPError as exc:
        print(f"[warn] child-response {exc.code}: {exc.read()[:200]!r}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] child-response failed: {type(exc).__name__}: {exc}", flush=True)
    return False


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

                if args.frame_dump:
                    with open(args.frame_dump, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(frame, ensure_ascii=False) + "\n")

                serial.raw(
                    f"serial RX {ftype} assignmentId={frame.get('assignmentId')} "
                    f"sessionId={frame.get('sessionId')} seq={frame.get('sequence')}"
                )

                if ftype == "lesson_step":
                    word = render_step(serial, frame)
                    steps_rendered += 1
                    interactive_word = None
                    story_beat = (frame.get("body") or {}).get("storyBeat")
                    if isinstance(story_beat, dict) and story_beat.get("waitForChild") is True:
                        interactive_word = word
                elif ftype == "lesson_prepare":
                    saw["prepare"] = True
                elif ftype == "lesson_start":
                    saw["start"] = True
                elif ftype == "lesson_stop":
                    saw["stop"] = True

                seq += 1
                ack = device_ack(frame, seq)
                await client.send(json.dumps(ack))
                # The server logs only a truncated '{"type":"lesson_ack"}', so the device
                # serial is the sole record of which frame was acked and in what state.
                robot_state = (
                    ((frame.get("body") or {}).get("scene") or {}).get("robotOverlay") or {}
                ).get("robotState") or "talking"
                serial.raw(
                    f"serial TX lesson_ack {ftype} assignmentId={frame.get('assignmentId')} "
                    f"sessionId={frame.get('sessionId')} stepId={frame.get('stepId') or ''} "
                    f"acks={frame.get('sequence')} seq={seq} rendered=true degraded=false "
                    f"robotState={robot_state}"
                )

                # An interactive step blocks until the child answers. The runtime opens the
                # response window when it processes our render ack, so the utterance has to
                # follow the ack, not precede it.
                if ftype == "lesson_step" and interactive_word:
                    serial.raw(
                        f"LessonRuntime child response window opened stepId="
                        f"{frame.get('stepId')} listening=true"
                    )
                    await asyncio.sleep(args.child_response_delay)
                    handled = await asyncio.get_running_loop().run_in_executor(
                        None, inject_child_response, args, args.device_id, interactive_word
                    )
                    if handled:
                        serial.raw(
                            f"serial interactive child response accepted stepId="
                            f"{frame.get('stepId')} recognizedText={interactive_word}"
                        )
                    else:
                        serial.raw(
                            f"serial interactive child response REJECTED stepId="
                            f"{frame.get('stepId')} recognizedText={interactive_word}"
                        )

                if ftype == "lesson_stop":
                    # Do NOT drop the socket here. The runtime finishes the session and
                    # forwards lesson_progress / lesson_completed to the backend AFTER the
                    # stop ack; closing immediately leaves the assignment PAUSED and loses
                    # every backend-side checkpoint.
                    serial.raw(
                        f"serial lesson_stop acked; holding socket {args.post_stop_linger}s "
                        f"for completion + backend progress"
                    )
                    await asyncio.sleep(args.post_stop_linger)
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
    parser.add_argument(
        "--post-stop-linger",
        type=float,
        default=10.0,
        help="Seconds to hold the socket open after acking lesson_stop so the runtime "
        "can complete and forward progress to the backend.",
    )
    parser.add_argument("--esp-http-base", default="http://127.0.0.1:8013")
    parser.add_argument(
        "--mint-secret",
        default=os.environ.get("TBOT_DEVICE_MINT_SECRET", "lab-mint-58b6712d872ccec8"),
        help="X-Mint-Secret for the internal child-response endpoint.",
    )
    parser.add_argument("--serial-log", default="sim-firmware-serial.log")
    parser.add_argument(
        "--child-response-delay",
        type=float,
        default=0.4,
        help="Pause before the simulated child answers an interactive step. Keep it "
        "non-zero: an instant reply can beat the runtime's response window open.",
    )
    parser.add_argument(
        "--frame-dump",
        help="Append every lesson_* frame received, one JSON object per line. "
        "Useful for inspecting the exact wire shape when a step stalls.",
    )
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=15.0)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
