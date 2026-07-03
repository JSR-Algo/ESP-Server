#!/usr/bin/env python3
"""Run a clean local ESP server for the built-in sample lesson demo.

This intentionally bypasses app.py/load_config_async so private data/.config.yaml
and data/.auth_key are not read. It starts only the local WebSocket + HTTP surfaces;
it does not nudge the robot.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import sys
from pathlib import Path


def ensure_project_root_on_path() -> None:
    root = str(Path(__file__).resolve().parents[1])
    if sys.path[0:1] != [root]:
        sys.path.insert(0, root)


def build_config(*, lan_ip: str, ws_port: int, http_port: int) -> dict:
    return {
        "read_config_from_api": True,
        "selected_module": {},
        "server": {
            "ip": "0.0.0.0",
            "port": ws_port,
            "http_port": http_port,
            "auth_key": "local-demo-only-key",
            "auth": {"enabled": False},
            "websocket": f"ws://{lan_ip}:{ws_port}/tbot/v1/",
            "api_url": "",
            "audio_admission": {"enabled": False},
        },
        "voice_mode": {"type": "google_live", "fallback_to_classic_on_error": False},
        "tbot": {
            "type": "hello",
            "version": 1,
            "transport": "websocket",
            "audio_params": {
                "format": "opus",
                "sample_rate": 24000,
                "channels": 1,
                "frame_duration": 60,
            },
        },
        "lesson": {
            "runtime_enabled": False,
            "sample_lesson": True,
            "sample_mode": "interactive",
            "supported_profiles": ["espTft"],
        },
        "firmware_cache_ttl": 30,
        "close_connection_no_voice_time": 120,
        "enable_websocket_ping": True,
    }


def status_lines(*, lan_ip: str, ws_port: int, http_port: int) -> list[str]:
    return [
        f"local_http_loopback=http://127.0.0.1:{http_port}",
        f"local_http_lan=http://{lan_ip}:{http_port}",
        f"local_ws_advertised=ws://{lan_ip}:{ws_port}/tbot/v1/",
        (
            "local_nudge_url="
            f"http://127.0.0.1:{http_port}/internal/devices/{{deviceId}}/lesson-nudge"
        ),
        "local_nudge_headers=X-TBOT-Local-Sample-Demo: 1",
        "local_nudge_env=TBOT_LOCAL_SAMPLE_DEMO_BYPASS=1",
    ]


async def serve(*, lan_ip: str, ws_port: int, http_port: int) -> None:
    os.environ["TBOT_LOCAL_SAMPLE_DEMO_BYPASS"] = "1"
    ensure_project_root_on_path()

    from core.http_server import SimpleHttpServer
    from core.websocket_server import WebSocketServer

    config = build_config(lan_ip=lan_ip, ws_port=ws_port, http_port=http_port)
    ws = WebSocketServer(config)
    http = SimpleHttpServer(config, ws.lesson_connections)
    tasks = [asyncio.create_task(ws.start()), asyncio.create_task(http.start())]
    try:
        for line in status_lines(lan_ip=lan_ip, ws_port=ws_port, http_port=http_port):
            print(line, flush=True)
        await asyncio.Future()
    finally:
        await ws.drain(timeout=1.0)
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await task


def _default_lan_ip() -> str:
    ensure_project_root_on_path()

    from core.utils.util import get_local_ip

    return get_local_ip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a no-secret local ESP server for the sample lesson demo."
    )
    parser.add_argument("--lan-ip", default=None, help="Mac LAN IP reachable by the robot")
    parser.add_argument("--ws-port", type=int, default=8000)
    parser.add_argument("--http-port", type=int, default=8003)
    args = parser.parse_args(argv)

    lan_ip = args.lan_ip or _default_lan_ip()
    try:
        asyncio.run(serve(lan_ip=lan_ip, ws_port=args.ws_port, http_port=args.http_port))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
