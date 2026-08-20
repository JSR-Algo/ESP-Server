#!/usr/bin/env python3
"""Behavioral F-T54-62 gate: expired lesson-event JWT remints once."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from unittest import mock

import httpx


def _expired_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://backend.test/v1/devices/dev1/lesson-events")
    response = httpx.Response(
        401,
        request=request,
        json={"code": "AUTH_TOKEN_EXPIRED", "error": {"code": "AUTH_TOKEN_EXPIRED"}},
    )
    return httpx.HTTPStatusError("expired", request=request, response=response)


async def _run(source_root: Path) -> None:
    sys.path.insert(0, str(source_root))

    from config import device_token_client
    from core.lesson.forwarder import LessonEventForwarder

    clock = {"now": 0.0}
    token_expiry: dict[str, float] = {}
    minted: list[str] = []
    attempts: list[tuple[str | None, dict]] = []
    persisted: list[dict] = []

    class MintResponse:
        def __init__(self, token: str) -> None:
            self.token = token

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"deviceUuid": "dev1", "token": self.token}}

    class MintClient:
        async def post(self, _url: str, **_kwargs) -> MintResponse:
            token = f"jwt-{len(minted) + 1}"
            minted.append(token)
            token_expiry[token] = clock["now"] + 900.0
            return MintResponse(token)

    mint_client = MintClient()
    device_token_client._cache.clear()

    async def refresh(_client, _rejected_token):
        return await device_token_client.resolve_device_identity(
            mint_client,
            "http://backend.test/v1",
            "AA:BB:CC:DD:EE:FF",
            force_refresh=True,
        )

    async def post(_client, _base_url, _device_id, batch, *, token=None):
        attempts.append((token, batch))
        if clock["now"] > token_expiry[token]:
            raise _expired_error()
        persisted.append(batch)
        return {"accepted": len(batch["events"])}

    terminal = {
        "assignmentId": "a1",
        "sessionId": "s1",
        "events": [{"type": "lesson_completed"}],
    }
    with mock.patch.dict(os.environ, {"TBOT_DEVICE_MINT_SECRET": "gate-secret"}), mock.patch.object(
        device_token_client.time, "monotonic", side_effect=lambda: clock["now"]
    ):
        device_id, token = await device_token_client.resolve_device_identity(
            mint_client, "http://backend.test/v1", "AA:BB:CC:DD:EE:FF"
        )
        forwarder = LessonEventForwarder(
            device_id=device_id,
            base_url="http://backend.test/v1",
            token=token,
            post_fn=post,
            token_refresh_fn=refresh,
            max_reenqueue_attempts=0,
        )
        clock["now"] = 901.0
        forwarder.enqueue(terminal)
        await forwarder.drain()

    assert [attempt[0] for attempt in attempts] == ["jwt-1", "jwt-2"], attempts
    assert attempts[0][1] is terminal and attempts[1][1] is terminal
    assert persisted == [terminal], persisted
    assert forwarder.dead_letters == [], forwarder.dead_letters
    assert forwarder.pending_terminal_batch is None
    await forwarder.aclose()
    print("F-T54-62 event token refresh repro: PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="main/tbot-server source tree to test",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.source_root.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
