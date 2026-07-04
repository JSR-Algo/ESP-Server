#!/usr/bin/env python3
"""Run the clean local sample demo path only when preflight says it is safe."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def build_server_command(*, lan_ip: str, ws_port: int, http_port: int) -> list[str]:
    return [
        sys.executable,
        str(SCRIPT_DIR / "local_sample_demo_server.py"),
        "--lan-ip",
        lan_ip,
        "--ws-port",
        str(ws_port),
        "--http-port",
        str(http_port),
    ]


def _run_json_command(command: list[str]) -> dict:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {
            "status": "COMMAND_OUTPUT_INVALID",
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    payload["exitCode"] = completed.returncode
    return payload


def start_server(*, lan_ip: str, ws_port: int, http_port: int):
    return subprocess.Popen(
        build_server_command(lan_ip=lan_ip, ws_port=ws_port, http_port=http_port),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_preflight(*, device_id: str, http_port: int) -> dict:
    return _run_json_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "local_sample_demo_preflight.py"),
            "--device-id",
            device_id,
            "--local-base-url",
            f"http://127.0.0.1:{http_port}",
        ]
    )


def run_nudge(*, device_id: str, base_url: str) -> dict:
    return _run_json_command(
        [
            sys.executable,
            str(SCRIPT_DIR / "local_sample_demo_nudge.py"),
            device_id,
            "--base-url",
            base_url,
        ]
    )


def _signal_server(server, sig: signal.Signals) -> None:
    pid = getattr(server, "pid", None)
    if pid:
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            return
        except Exception:
            pass
    if sig == signal.SIGTERM:
        server.terminate()
    else:
        server.kill()


def _stop_server(server) -> None:
    if server is None:
        return
    _signal_server(server, signal.SIGTERM)
    try:
        server.wait(timeout=5)
    except Exception:
        with contextlib.suppress(Exception):
            _signal_server(server, signal.SIGKILL)
        with contextlib.suppress(Exception):
            server.wait(timeout=2)


def run_guarded(
    *,
    device_id: str,
    lan_ip: str,
    ws_port: int,
    http_port: int,
    wait_seconds: int,
    poll_seconds: int,
    start_server=start_server,
    run_preflight=run_preflight,
    run_nudge=run_nudge,
    sleep=time.sleep,
) -> dict:
    server = start_server(lan_ip=lan_ip, ws_port=ws_port, http_port=http_port)
    last = None
    try:
        poll_interval = max(1, poll_seconds)
        attempts = max(1, (max(0, wait_seconds) + poll_interval - 1) // poll_interval)
        for _ in range(attempts):
            last = run_preflight(device_id=device_id, http_port=http_port)
            if last.get("canNudgeLocal") is True:
                nudge = run_nudge(
                    device_id=device_id,
                    base_url=f"http://127.0.0.1:{http_port}",
                )
                return {"status": "NUDGED", "preflight": last, "nudge": nudge}
            sleep(poll_interval)
        return {"status": "NOT_READY", "lastPreflight": last}
    finally:
        _stop_server(server)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start local sample server and nudge only if local preflight is ready."
    )
    parser.add_argument("--device-id", default="28:84:85:85:1a:80")
    parser.add_argument("--lan-ip", default="192.168.0.104")
    parser.add_argument("--ws-port", type=int, default=8000)
    parser.add_argument("--http-port", type=int, default=8003)
    parser.add_argument("--wait-seconds", type=int, default=60)
    parser.add_argument("--poll-seconds", type=int, default=2)
    args = parser.parse_args(argv)

    result = run_guarded(
        device_id=args.device_id,
        lan_ip=args.lan_ip,
        ws_port=args.ws_port,
        http_port=args.http_port,
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "NUDGED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
