#!/usr/bin/env python3
import argparse
import asyncio
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.voice.google_live.client import GoogleLiveClient


class _ConsoleLogger:
    def bind(self, **kwargs):
        return self

    def info(self, message, *args, **kwargs):
        if args:
            message = message.format(*args)
        print(f"[info] {message}")

    def warning(self, message, *args, **kwargs):
        if args:
            message = message.format(*args)
        print(f"[warn] {message}")

    def error(self, message, *args, **kwargs):
        if args:
            message = message.format(*args)
        print(f"[error] {message}")


def _build_env_config(model, voice_name):
    return {
        "api_key": "${GOOGLE_API_KEY}",
        "model": model,
        "enable_audio_input": True,
        "enable_audio_output": True,
        "native_voice": bool(voice_name),
        "voice_name": voice_name,
        "connect_timeout_sec": 15,
        "recv_timeout_sec": 5,
    }

async def _load_manager_google_live_config(device_id, client_id):
    from config.config_loader import load_config_async, get_private_config_from_api
    from config.manage_api_client import ManageApiClient

    config = await load_config_async()
    ManageApiClient(config)
    try:
        private_config = await get_private_config_from_api(config, device_id, client_id)
    finally:
        ManageApiClient.safe_close()
        await asyncio.sleep(0)

    google_live_config = private_config.get("google_live") or {}
    if not google_live_config:
        raise RuntimeError("manager private config has no google_live section")
    if private_config.get("voice_mode", {}).get("type") != "google_live":
        raise RuntimeError("manager private config voice_mode is not google_live")
    return dict(google_live_config)

async def _run_smoke(config):
    client = GoogleLiveClient(config, _ConsoleLogger())
    await client.connect()
    print("SMOKE_CONNECT_OK")
    await client.close()
    print("SMOKE_CLOSE_OK")


def main():
    parser = argparse.ArgumentParser(
        description="Connect to Google Live API and immediately close."
    )
    parser.add_argument(
        "--model",
        default=os.environ.get(
            "GOOGLE_LIVE_MODEL",
            "gemini-2.5-flash-native-audio-preview-12-2025",
        ),
    )
    parser.add_argument(
        "--voice-name",
        default=os.environ.get("GOOGLE_LIVE_VOICE_NAME", ""),
    )
    parser.add_argument(
        "--manager-device-id",
        default=os.environ.get("GOOGLE_LIVE_MANAGER_DEVICE_ID", ""),
        help="Load google_live config from manager API private config for this device.",
    )
    parser.add_argument(
        "--manager-client-id",
        default=os.environ.get("GOOGLE_LIVE_MANAGER_CLIENT_ID", ""),
        help="Client/agent id to use with --manager-device-id.",
    )
    args = parser.parse_args()

    if args.manager_device_id:
        if not args.manager_client_id:
            print("--manager-client-id is required with --manager-device-id", file=sys.stderr)
            return 1
        config = asyncio.run(
            _load_manager_google_live_config(args.manager_device_id, args.manager_client_id)
        )
    else:
        config = _build_env_config(args.model, args.voice_name)

    if not config.get("api_key"):
        print("GOOGLE_API_KEY is required", file=sys.stderr)
        return 1

    asyncio.run(_run_smoke(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
