#!/usr/bin/env bash
# repo: robot/esp32-server
set -euo pipefail

cd main/tbot-server
python3 - <<'PY'
import asyncio

from core.lesson import sd_pack_sync


async def main():
    checksum = "d" * 64
    cache_key = f"lesson-b/v2-{checksum}"
    messages = []

    class BoundLogger:
        def warning(self, message):
            messages.append(message)

        def info(self, message):
            messages.append(message)

    class Logger:
        def bind(self, **_kwargs):
            return BoundLogger()

    class Client:
        async def is_ready(self):
            return True

    class Conn:
        config = {"lesson": {"asset_delivery_mode": "sd_pack"}}
        mcp_client = Client()
        logger = Logger()

    pack = {
        "cacheKey": cache_key,
        "manifestChecksum": checksum,
        "assets": [{"key": "poster"}],
    }
    original_packs = sd_pack_sync.cached_asset_packs
    original_call = sd_pack_sync._call_sd_pack_sync_with_voice_guard
    sd_pack_sync.cached_asset_packs = lambda _config: iter([pack])

    async def rejected(*_args, **_kwargs):
        return {
            "ready": False,
            "downloadedCount": 1,
            "reusedCount": 0,
            "skippedCount": 0,
            "failedCount": 2,
            "criticalFailedCount": 1,
            "errorCode": " SD failed/token=secret ",
        }

    sd_pack_sync._call_sd_pack_sync_with_voice_guard = rejected
    try:
        await sd_pack_sync.sync_cached_lesson_assets_to_sd(Conn())
    finally:
        sd_pack_sync.cached_asset_packs = original_packs
        sd_pack_sync._call_sd_pack_sync_with_voice_guard = original_call

    assert messages[0] == (
        "cached SD pack sync rejected "
        f"cache_key={cache_key} downloaded=1 reused=0 skipped=0 failed=2 "
        "critical_failed=1 error_code=sd_failed_token_secret"
    )
    assert "token=secret" not in messages[0]


asyncio.run(main())
PY
